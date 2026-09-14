"""Fetch Avast PBI PDF attachments from Gmail and sync verified daily metrics."""
import argparse
import email
import imaplib
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pdfplumber


SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
ORIGINAL_SENDER = "no-reply-powerbi@microsoft.com"
FORWARDER = "partner@wps.com"
HEADERS = ("日期", "合作方", "运营位", "新增", "血量")
PARTNER = "Avast"
SURFACES = {
    "popup": {"subject": "Avast AV - WPS - Daily PBI report", "operation": "换量弹窗", "optional": False},
    "bubble": {"subject": "Avast AV - WPS - Toast - Daily PBI report", "operation": "气泡", "optional": False},
    "uninstall_h5": {"subject": "Avast One - WPS - C - Daily Report PBI", "operation": "卸载后引导H5", "optional": True},
    "document_radar": {"subject": "Avast AV - WPS - E - Daily PBI report", "operation": "\u6587\u6863\u96f7\u8fbe", "optional": True},
}


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported date: {value!r}")


def number(value):
    text = str(value).replace("\u00a0", "").replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise ValueError(f"invalid numeric value: {value!r}")
    parsed = float(text)
    return int(parsed) if parsed.is_integer() else parsed


def parse_avast_page(page_text):
    """Return available daily metrics from the first PBI page.

    Power BI can refresh the installs and costs tables at different times.
    Each table is therefore validated against its own date header: a valid
    table is retained even when the corresponding metric is absent or lags.
    """
    total_pattern = r"(?im)^[^\w$\d\r\n]*(?:Grand[ \t]+)?Total[ \t]+(.+)$"
    totals = list(re.finditer(total_pattern, page_text))
    new_total = next((match for match in totals if "$" not in match.group(1)), None)
    blood_total = next(
        (match for match in totals if "$" in match.group(1) and (new_total is None or match.start() > new_total.start())),
        None,
    )
    date_token = (
        r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}"
        r"|\d{1,2}[./]\d{1,2}[./]\d{4}"
        r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
        r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
    )

    def parse_metric(total, header_region, pattern):
        if total is None:
            return {}
        values = re.findall(pattern, total.group(1))
        expected_days = len(values) - 1  # final value is the grand total
        if expected_days < 1:
            return {}
        found_days = re.findall(date_token, header_region, flags=re.IGNORECASE)
        if len(found_days) < expected_days:
            return {}
        parsed_days = [parse_day(day) for day in found_days[-expected_days:]]
        if len(parsed_days) != len(set(parsed_days)):
            return {}
        return dict(zip(parsed_days, map(number, values[:-1])))

    new_metrics = parse_metric(
        new_total,
        page_text[:new_total.start()] if new_total else "",
        r"\d[\d,]*",
    )
    blood_header_region = ""
    if blood_total:
        blood_header_region = (
            page_text[new_total.end():blood_total.start()]
            if new_total
            else page_text[:blood_total.start()]
        )
    blood_metrics = parse_metric(
        blood_total,
        blood_header_region,
        r"\$[\d,]+(?:\.\d+)?",
    )
    # Older exports omit the repeated header before the costs Total. In that
    # case the costs columns share the installs header rather than being bad.
    if not blood_metrics and new_total and blood_total:
        blood_metrics = parse_metric(
            blood_total,
            page_text[:new_total.start()],
            r"\$[\d,]+(?:\.\d+)?",
        )
    # A date is usable only when the report contains both metrics for that
    # date. Do not append a partially refreshed day and backfill it later.
    return {
        day: {"new_users": new_metrics[day], "blood_volume": blood_metrics[day]}
        for day in new_metrics.keys() & blood_metrics.keys()
    }

def pdf_rows(raw_pdf):
    with pdfplumber.open(io.BytesIO(raw_pdf)) as pdf:
        if not pdf.pages:
            raise ValueError("Avast attachment is empty")
        return parse_avast_page(pdf.pages[0].extract_text() or "")


def gmail_imap_client(username, app_password):
    """Authenticate with an app password; OAuth refresh tokens are not used."""
    try:
        client = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        client.login(username.strip(), app_password.replace(" ", "").strip())
        return client
    except imaplib.IMAP4.error as exc:
        raise RuntimeError("Gmail IMAP login failed; check GMAIL_IMAP_USERNAME and GMAIL_APP_PASSWORD") from exc

def sheets_service(service_json):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_info(json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def body_text(message):
    return "\n".join(
        part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in message.walk()
        if part.get_content_maintype() == "text" and part.get_payload(decode=True)
    )


def verified_sender(message):
    sent_by = message.get("From", "").lower()
    if ORIGINAL_SENDER in sent_by:
        return True
    return FORWARDER in sent_by and ORIGINAL_SENDER in body_text(message).lower()


def attachments(message):
    for part in message.walk():
        filename = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                yield payload


def select_all_mail(client):
    status, mailboxes = client.list()
    if status != "OK":
        raise RuntimeError("Gmail IMAP mailbox listing failed")
    all_mail = next((item.decode("utf-8", "replace").rsplit('"', 2)[-2]
                     for item in mailboxes if b"\\All" in item), None)
    mailbox = all_mail or "INBOX"
    status, _ = client.select(mailbox, readonly=True)
    if status != "OK":
        raise RuntimeError(f"Gmail IMAP could not open mailbox: {mailbox}")


def imap_messages(client, subject, sent_on):
    select_all_mail(client)
    # Restrict the server-side search to the report date. This prevents old
    # messages with the same subject from being fetched or parsed.
    status, data = client.uid(
        "search",
        None,
        "SENTON",
        sent_on.strftime("%d-%b-%Y"),
        "SUBJECT",
        f'"{subject}"',
    )
    if status != "OK":
        raise RuntimeError("Gmail IMAP search failed")
    uids = data[0].split()
    if not uids:
        return
    # Gmail UIDs are monotonically increasing. Fetch only the newest matching
    # report instead of traversing older messages from the same day.
    status, payload = client.uid("fetch", uids[-1], "(RFC822)")
    if status == "OK" and payload and isinstance(payload[0], tuple):
        yield email.message_from_bytes(payload[0][1])

def source_rows(client, surface, start, end, email_date):
    spec = SURFACES[surface]
    resolved = {}
    rejected = 0
    for message in imap_messages(client, spec["subject"], email_date):
        if not verified_sender(message):
            rejected += 1
            continue
        for raw_pdf in attachments(message):
            for day, metrics in pdf_rows(raw_pdf).items():
                if (start is None or start <= day) and day <= end and day not in resolved:
                    resolved[day] = metrics
    status_start = start or (min(resolved) if resolved else end)
    unavailable = [status_start + timedelta(days=i) for i in range((end - status_start).days + 1) if status_start + timedelta(days=i) not in resolved]
    print(json.dumps({"surface": surface, "status": "available" if resolved else "unavailable", "available_days": len(resolved), "unavailable_days": [d.isoformat() for d in unavailable], "rejected_messages": rejected}, ensure_ascii=False))
    return resolved


def col_name(index):
    result = ""
    while True:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        if index == 0:
            return result
        index -= 1


def value_at(row, column):
    values = row["values"] if isinstance(row, dict) else row
    return values[column] if column < len(values) else ""


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def get_sheet(service):
    values = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{SHEET_NAME}'!A1:E10000",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="SERIAL_NUMBER",
    ).execute().get("values", [])
    if not values or len(values[0]) != len(set(values[0])) or any(header not in values[0] for header in HEADERS):
        raise RuntimeError("long-format target headers are missing or duplicated")
    headers = values[0]
    positions = {header: headers.index(header) for header in HEADERS}
    rows = {}
    for row_number, row in enumerate(values[1:], start=2):
        if not row or not value_at(row, positions["日期"]):
            continue
        key = (
            parse_day(value_at(row, positions["日期"])),
            str(value_at(row, positions["合作方"])).strip(),
            str(value_at(row, positions["运营位"])).strip(),
        )
        if key in rows:
            raise RuntimeError(f"duplicate long-format record: {key}")
        rows[key] = {"row": row_number, "values": row}
    return headers, rows




def plan_writes(headers, existing_rows, sources, allow_overwrite):
    positions = {header: headers.index(header) for header in HEADERS}
    updates, appends, conflicts, overwrites = [], [], [], []
    for surface, source in sources.items():
        operation = SURFACES[surface]["operation"]
        for day, metrics in sorted(source.items()):
            if set(metrics) != {"new_users", "blood_volume"}:
                continue
            row = existing_rows.get((day, PARTNER, operation))
            if row is None:
                appends.append({"日期": day, "合作方": PARTNER, "运营位": operation, "新增": metrics["new_users"], "血量": metrics["blood_volume"]})
                continue
            for header, metric in (("新增", "new_users"), ("血量", "blood_volume")):
                current, wanted = value_at(row, positions[header]), metrics[metric]
                if current in ("", None):
                    updates.append({"range": f"'{SHEET_NAME}'!{col_name(positions[header])}{row['row']}", "values": [[wanted]]})
                elif not values_match(current, wanted):
                    detail = f"{day} {PARTNER}/{operation}/{header}: sheet={current}, source={wanted}"
                    if allow_overwrite:
                        updates.append({"range": f"'{SHEET_NAME}'!{col_name(positions[header])}{row['row']}", "values": [[wanted]]})
                        overwrites.append(detail)
                    else:
                        conflicts.append(detail)
    if conflicts:
        raise RuntimeError("refusing to overwrite conflicts: " + "; ".join(conflicts))
    return updates, appends, overwrites


def append_rows(service, headers, records):
    if not records:
        return
    positions = {header: headers.index(header) for header in HEADERS}
    values = []
    for record in records:
        row = [""] * len(headers)
        row[positions["日期"]] = record["日期"].isoformat()
        row[positions["合作方"]] = record["合作方"]
        row[positions["运营位"]] = record["运营位"]
        row[positions["新增"]] = record["新增"]
        row[positions["血量"]] = record["血量"]
        values.append(row)
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"'{SHEET_NAME}'!A1:{col_name(len(headers) - 1)}",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"majorDimension": "ROWS", "values": values},
    ).execute()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()
    secrets = {name: os.environ.get(name) for name in ("GMAIL_IMAP_USERNAME", "GMAIL_APP_PASSWORD", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
    if not all(secrets.values()):
        raise RuntimeError("missing required GitHub Actions secret")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    # Reports arriving after midnight Beijing time contain data through the
    # previous day, so their email date and data end date are different.
    end = parse_day(args.end_date) if args.end_date else today - timedelta(days=1)
    email_date = today
    sheets = sheets_service(secrets["GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"])
    headers, existing_rows = get_sheet(sheets)
    explicit_start = parse_day(args.start_date) if args.start_date else None
    if explicit_start is not None and explicit_start > end:
        raise RuntimeError("start date is after end date")
    gmail = gmail_imap_client(secrets["GMAIL_IMAP_USERNAME"], secrets["GMAIL_APP_PASSWORD"])
    try:
        sources = {surface: source_rows(gmail, surface, explicit_start, end, email_date) for surface in SURFACES}
    finally:
        gmail.logout()
    start_candidates = [explicit_start] if explicit_start is not None else []
    start_candidates.extend(day for source in sources.values() for day in source)
    start = min(start_candidates) if start_candidates else end
    updates, appends, overwrites = plan_writes(headers, existing_rows, sources, args.allow_overwrite)
    if updates:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute()
    append_rows(sheets, headers, appends)
    print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "updated_cells": len(updates), "appended_rows": len(appends), "overwrites": overwrites}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
