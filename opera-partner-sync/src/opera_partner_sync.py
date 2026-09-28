"""Fetch Opera dashboard PDF attachments from Gmail and sync daily metrics."""
import argparse
import email
import imaplib
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from time import perf_counter

import pdfplumber

logging.getLogger("pdfminer").setLevel(logging.ERROR)

SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
SENDER = "noreply@lookermail.com"
SUBJECT = "Opera for Computers distribution partner dashboard"
GX_SUBJECT = "OperaGX for Computers distribution partner dashboard"
GX_PARTNER = "Opera GX"
GX_SURFACES = {
    "bubble": {"utm_content": "toast", "operation": "气泡"},
    "popup": {"utm_content": "bundle", "operation": "换量弹窗"},
    "recall": {"utm_content": "recall", "operation": "卸载引导"},
}
HEADERS = ("日期", "合作方", "运营位", "新增", "血量")
PARTNER = "Opera"
SURFACES = {
    "popup": {"campaign": "wpstest2/opera.exe", "operation": "换量弹窗"},
    "bubble": {"campaign": "wpstest", "operation": "气泡"},
}


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y"):
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


def parse_dashboard_text(text, gx=False):
    """Parse all operating positions from a single text extraction."""
    label = "OperaGX" if gx else "Opera"
    dimension = "Utm Content" if gx else "Campaign"
    if "Summary table" not in text or f"Day {dimension} New Users Revenue" not in text:
        raise ValueError(f"{label} Summary table headers were not found")
    specs = GX_SURFACES if gx else SURFACES
    field = "utm_content" if gx else "campaign"
    by_content = {spec[field]: surface for surface, spec in specs.items()}
    contents = "|".join(re.escape(content) for content in by_content)
    pattern = re.compile(
        rf"(?m)^\d+\s+(\d{{4}}-\d{{2}}-\d{{2}})\s+({contents})\s+([\d,]+)\s+\$([\d,]+(?:\.\d+)?)\s*$"
    )
    sources = {surface: {} for surface in specs}
    for day_text, content, new_users, revenue in pattern.findall(text):
        day = parse_day(day_text)
        rows = sources[by_content[content]]
        if day in rows:
            raise ValueError(f"{label} duplicate {content} row for {day}")
        rows[day] = {"new_users": number(new_users), "blood_volume": number(revenue)}
    return sources


def extract_pdf_text(raw_pdf):
    # Current Looker dashboards are single-page PDFs. Read every page if the
    # layout changes, so a Summary table continued on another page is retained.
    with pdfplumber.open(io.BytesIO(raw_pdf)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def parse_opera_text(text, campaign):
    sources = parse_dashboard_text(text)
    surface = next(key for key, spec in SURFACES.items() if spec["campaign"] == campaign)
    if not sources[surface]:
        raise ValueError(f"Opera Summary table has no {campaign} rows")
    return sources[surface]


def parse_opera_pdf(raw_pdf, campaign):
    return parse_opera_text(extract_pdf_text(raw_pdf), campaign)


def gmail_imap_client(username, app_password):
    """Authenticate with a Gmail app password instead of OAuth refresh tokens."""
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


def imap_date(day):
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{day.day:02d}-{months[day.month - 1]}-{day.year}"


def imap_messages(client, since=None, subject=SUBJECT, include_history=True):
    select_all_mail(client)
    criteria = ["FROM", SENDER, "SUBJECT", f'"{subject}"']
    if since is not None:
        criteria.extend(["SINCE", imap_date(since)])
    status, data = client.uid("search", None, *criteria)
    if status != "OK":
        raise RuntimeError("Gmail IMAP subject search failed")
    uids = data[0].split()
    if not uids:
        return
    selected = reversed(uids) if include_history else (uids[-1],)
    for uid in selected:
        status, payload = client.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            continue
        yield email.message_from_bytes(payload[0][1])


def gx_messages(client, since=None, include_history=False):
    return imap_messages(client, since=since, subject=GX_SUBJECT, include_history=include_history)


def dashboard_source_rows(client, start, end, gx=False, since=None, include_history=True):
    """Download each message once and extract each PDF once for all surfaces."""
    started = perf_counter()
    specs = GX_SURFACES if gx else SURFACES
    sources = {surface: {} for surface in specs}
    expected = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    compatible = fetched = parsed = skipped = 0
    extract_seconds = 0.0
    messages = gx_messages(client, since=since, include_history=include_history) if gx else imap_messages(client, since=since, include_history=include_history)
    for message in messages:
        fetched += 1
        if SENDER not in message.get("From", "").lower():
            continue
        for raw_pdf in attachments(message):
            extraction_started = perf_counter()
            text = extract_pdf_text(raw_pdf)
            extract_seconds += perf_counter() - extraction_started
            parsed += 1
            try:
                report = parse_dashboard_text(text, gx=gx)
            except ValueError as exc:
                if "Summary table headers were not found" not in str(exc):
                    raise
                skipped += 1
                continue
            compatible += 1
            for surface, rows in report.items():
                for day, metrics in rows.items():
                    if start <= day <= end:
                        sources[surface].setdefault(day, metrics)
        # Read every attachment in this message before deciding to stop.
        if all(expected <= set(rows) for rows in sources.values()):
            break
    if not compatible:
        raise RuntimeError(f"no compatible {'OperaGX' if gx else 'Opera'} PDF attachments found")
    if not gx:
        for surface, rows in sources.items():
            if not rows:
                raise RuntimeError(f"no verified {surface} Opera PDF rows in the requested date range")
    print(json.dumps({
        "partner": GX_PARTNER if gx else PARTNER,
        "fetched_messages": fetched,
        "parsed_pdfs": parsed,
        "skipped_incompatible_attachments": skipped,
        "source_seconds": round(perf_counter() - started, 3),
        "pdf_extract_seconds": round(extract_seconds, 3),
        "surfaces": {surface: {"available_days": len(rows), "unavailable_day_count": len(expected - set(rows))} for surface, rows in sources.items()},
    }, ensure_ascii=False))
    return sources


def source_rows(client, surface, start, end):
    return dashboard_source_rows(client, start, end)[surface]


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


def first_missing(rows, cutoff):
    candidates = []
    for spec in SURFACES.values():
        days = sorted(day for day, partner, operation in rows if partner == PARTNER and operation == spec["operation"] and day <= cutoff)
        if not days:
            continue
        expected = {days[0] + timedelta(days=index) for index in range((cutoff - days[0]).days + 1)}
        missing = expected - set(days)
        candidates.append(min(missing) if missing else cutoff)
    return min(candidates) if candidates else cutoff


def plan_writes(headers, existing_rows, sources, allow_overwrite):
    positions = {header: headers.index(header) for header in HEADERS}
    updates, appends, conflicts, overwrites = [], [], [], []
    for surface, source in sources.items():
        operation = SURFACES[surface]["operation"]
        for day, metrics in sorted(source.items()):
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


def parse_opera_gx_text(text, utm_content):
    sources = parse_dashboard_text(text, gx=True)
    surface = next(key for key, spec in GX_SURFACES.items() if spec["utm_content"] == utm_content)
    if not sources[surface]:
        raise ValueError(f"OperaGX Summary table has no {utm_content} rows")
    return sources[surface]


def parse_opera_gx_pdf(raw_pdf, utm_content):
    return parse_opera_gx_text(extract_pdf_text(raw_pdf), utm_content)


def gx_source_rows(client, surface, start, end):
    return dashboard_source_rows(client, start, end, gx=True, include_history=False)[surface]


def gx_first_missing(rows, cutoff):
    candidates = []
    for spec in GX_SURFACES.values():
        days = sorted(day for day, partner, operation in rows if partner == GX_PARTNER and operation == spec["operation"] and day <= cutoff)
        if days:
            expected = {days[0] + timedelta(days=i) for i in range((cutoff - days[0]).days + 1)}
            missing = expected - set(days)
            candidates.append(min(missing) if missing else cutoff)
    return min(candidates) if candidates else cutoff


def gx_plan_writes(headers, existing_rows, sources, allow_overwrite):
    positions = {header: headers.index(header) for header in HEADERS}
    updates, appends, conflicts, overwrites = [], [], [], []
    for surface, source in sources.items():
        operation = GX_SURFACES[surface]["operation"]
        for day, metrics in sorted(source.items()):
            row = existing_rows.get((day, GX_PARTNER, operation))
            if row is None:
                appends.append({"日期": day, "合作方": GX_PARTNER, "运营位": operation, "新增": metrics["new_users"], "血量": metrics["blood_volume"]})
                continue
            for header, metric in (("新增", "new_users"), ("血量", "blood_volume")):
                current, wanted = value_at(row, positions[header]), metrics[metric]
                if current in ("", None):
                    updates.append({"range": f"'{SHEET_NAME}'!{col_name(positions[header])}{row['row']}", "values": [[wanted]]})
                elif not values_match(current, wanted):
                    detail = f"{day} {GX_PARTNER}/{operation}/{header}: sheet={current}, source={wanted}"
                    if allow_overwrite:
                        updates.append({"range": f"'{SHEET_NAME}'!{col_name(positions[header])}{row['row']}", "values": [[wanted]]})
                        overwrites.append(detail)
                    else:
                        conflicts.append(detail)
    if conflicts:
        raise RuntimeError("refusing to overwrite conflicts: " + "; ".join(conflicts))
    return updates, appends, overwrites


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
    end = parse_day(args.end_date) if args.end_date else today - timedelta(days=1)
    sheets = sheets_service(secrets["GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"])
    headers, target_rows = get_sheet(sheets)
    start = parse_day(args.start_date) if args.start_date else end - timedelta(days=2)
    if start > end:
        raise RuntimeError("start date is after end date")
    explicit_range = bool(args.start_date or args.end_date)
    # Reports received after the requested end may still contain its data.
    mail_since = start if explicit_range else today - timedelta(days=6)
    gmail = gmail_imap_client(secrets["GMAIL_IMAP_USERNAME"], secrets["GMAIL_APP_PASSWORD"])
    try:
        opera_sources = dashboard_source_rows(gmail, start, end, since=mail_since)
        gx_sources = dashboard_source_rows(gmail, start, end, gx=True, since=mail_since, include_history=explicit_range)
    finally:
        gmail.logout()
    updates, appends, overwrites = plan_writes(headers, target_rows, opera_sources, args.allow_overwrite)
    gx_updates, gx_appends, gx_overwrites = gx_plan_writes(headers, target_rows, gx_sources, args.allow_overwrite)
    updates.extend(gx_updates); appends.extend(gx_appends); overwrites.extend(gx_overwrites)
    if updates:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute()
    append_rows(sheets, headers, appends)
    print(json.dumps({"ranges": [{"partner": PARTNER, "start": start.isoformat(), "end": end.isoformat()}, {"partner": GX_PARTNER, "start": start.isoformat(), "end": end.isoformat()}], "updated_cells": len(updates), "appended_rows": len(appends), "overwrites": overwrites}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
