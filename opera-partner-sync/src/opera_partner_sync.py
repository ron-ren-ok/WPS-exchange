"""Fetch Opera dashboard PDF attachments from Gmail and sync daily metrics."""
import argparse
import base64
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pdfplumber

SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
SENDER = "noreply@lookermail.com"
SUBJECT = "Opera for Computers distribution partner dashboard"
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


def parse_opera_text(text, campaign):
    """Extract exactly one Summary table row per requested campaign/date."""
    if "Summary table" not in text or "Day Campaign New Users Revenue" not in text:
        raise ValueError("Opera Summary table headers were not found")
    pattern = re.compile(
        r"(?m)^\d+\s+(\d{4}-\d{2}-\d{2})\s+(wpstest2/opera\.exe|wpstest)\s+([\d,]+)\s+\$([\d,]+(?:\.\d+)?)\s*$"
    )
    rows = {}
    for day_text, observed_campaign, new_users, revenue in pattern.findall(text):
        if observed_campaign != campaign:
            continue
        day = parse_day(day_text)
        if day in rows:
            raise ValueError(f"Opera duplicate {campaign} row for {day}")
        rows[day] = {"new_users": number(new_users), "blood_volume": number(revenue)}
    if not rows:
        raise ValueError(f"Opera Summary table has no {campaign} rows")
    return rows


def parse_opera_pdf(raw_pdf, campaign):
    with pdfplumber.open(io.BytesIO(raw_pdf)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return parse_opera_text(text, campaign)


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


def imap_messages(client):
    select_all_mail(client)
    status, data = client.uid("search", None, "SUBJECT", f'"{SUBJECT}"')
    if status != "OK":
        raise RuntimeError("Gmail IMAP subject search failed")
    for uid in reversed(data[0].split()):
        status, payload = client.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            continue
        yield email.message_from_bytes(payload[0][1])


def source_rows(client, surface, start, end):
    spec = SURFACES[surface]
    resolved = {}
    for message in imap_messages(client):
        if SENDER not in message.get("From", "").lower():
            continue
        for raw_pdf in attachments(message):
            for day, metrics in parse_opera_pdf(raw_pdf, spec["campaign"]).items():
                if start <= day <= end and day not in resolved:
                    resolved[day] = metrics
    if not resolved:
        raise RuntimeError(f"no verified {surface} Opera PDF rows in the requested date range")
    unavailable = [start + timedelta(days=i) for i in range((end - start).days + 1) if start + timedelta(days=i) not in resolved]
    print(json.dumps({"surface": surface, "available_days": len(resolved), "unavailable_days": [d.isoformat() for d in unavailable]}, ensure_ascii=False))
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()
    secrets = {name: os.environ.get(name) for name in ("GMAIL_IMAP_USERNAME", "GMAIL_APP_PASSWORD", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
    if not all(secrets.values()):
        raise RuntimeError("missing required GitHub Actions secret")
    end = parse_day(args.end_date) if args.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    sheets = sheets_service(secrets["GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"])
    headers, target_rows = get_sheet(sheets)
    start = parse_day(args.start_date) if args.start_date else first_missing(target_rows, end)
    if start > end:
        raise RuntimeError("start date is after end date")
    gmail = gmail_imap_client(secrets["GMAIL_IMAP_USERNAME"], secrets["GMAIL_APP_PASSWORD"])
    try:
        sources = {surface: source_rows(gmail, surface, start, end) for surface in SURFACES}
    finally:
        gmail.logout()
    updates, appends, overwrites = plan_writes(headers, target_rows, sources, args.allow_overwrite)
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