"""Sync the newest three CCleaner Toast daily-install figures from Gmail."""
import argparse
import email
import imaplib
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta

import pdfplumber


SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
ORIGINAL_SENDER = "no-reply-powerbi@microsoft.com"
FORWARDER = "partner@wps.com"
SUBJECT = "CCleaner - WPS - B - Daily Report PBI"
PARTNER = "CCleaner"
OPERATION = "气泡"
HEADERS = ("日期", "合作方", "运营位", "新增", "血量")


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y"):
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


def parse_ccleaner_page(page_text):
    """Read page-one daily installation and cost Total rows by their own dates."""
    total_pattern = r"(?im)^[^\w$\d\r\n]*(?:Grand[ \t]+)?Total[ \t]+(.+)$"
    date_token = (
        r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}"
        r"|\d{1,2}[./]\d{1,2}[./]\d{4}"
        r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
        r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
    )
    installations = None
    costs = None
    for match in re.finditer(total_pattern, page_text):
        total_text = match.group(1)
        currency = "$" in total_text
        values = re.findall(r"\$?-?\d[\d,]*(?:\.\d+)?", total_text)
        if len(values) < 2:
            continue
        found_days = re.findall(date_token, page_text[:match.start()], flags=re.IGNORECASE)
        expected_days = len(values) - 1  # The final column is the all-time Total.
        if len(found_days) < expected_days:
            continue
        days = [parse_day(day) for day in found_days[-expected_days:]]
        if len(days) != len(set(days)):
            raise ValueError("CCleaner date headers are duplicated")
        parsed = {day: number(value) for day, value in zip(days, values[:-1])}
        if currency and costs is None:
            costs = parsed
        elif not currency and installations is None:
            installations = parsed
    if installations is None:
        raise ValueError("CCleaner daily-install Total row or date headers were not found")
    records = {day: {"new_users": value} for day, value in installations.items()}
    for day, value in (costs or {}).items():
        if day in records:
            records[day]["blood_volume"] = value
    return records


def pdf_rows(raw_pdf):
    with pdfplumber.open(io.BytesIO(raw_pdf)) as pdf:
        if not pdf.pages:
            raise ValueError("CCleaner attachment is empty")
        return parse_ccleaner_page(pdf.pages[0].extract_text() or "")


def latest_three_days(records, start=None, end=None):
    if start is not None and end is not None and start > end:
        raise RuntimeError("start date is after end date")
    eligible = [day for day in records if end is None or day <= end]
    if not eligible:
        raise RuntimeError("CCleaner report has no dated install data in range")
    anchor = max(eligible)
    window_start = start or anchor - timedelta(days=2)
    selected = {day: records[day] for day in records if window_start <= day <= anchor}
    if not selected:
        raise RuntimeError("CCleaner report has no dated install data in the requested range")
    return selected, window_start, anchor


def gmail_imap_client(username, app_password):
    try:
        client = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        client.login(username.strip(), app_password.replace(" ", "").strip())
        return client
    except imaplib.IMAP4.error as exc:
        raise RuntimeError("Gmail IMAP login failed; check GMAIL_IMAP_USERNAME and GMAIL_APP_PASSWORD") from exc


def body_text(message):
    return "\n".join(
        part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in message.walk()
        if part.get_content_maintype() == "text" and part.get_payload(decode=True)
    )


def verified_sender(message):
    sent_by = message.get("From", "").lower()
    return ORIGINAL_SENDER in sent_by or (FORWARDER in sent_by and ORIGINAL_SENDER in body_text(message).lower())


def attachments(message):
    for part in message.walk():
        filename = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                yield payload


def newest_report(client):
    status, mailboxes = client.list()
    if status != "OK":
        raise RuntimeError("Gmail IMAP mailbox listing failed")
    all_mail = next((item.decode("utf-8", "replace").rsplit('"', 2)[-2] for item in mailboxes if b"\\All" in item), None)
    if client.select(all_mail or "INBOX", readonly=True)[0] != "OK":
        raise RuntimeError("Gmail IMAP could not open mailbox")
    status, data = client.uid("search", None, "SUBJECT", f'"{SUBJECT}"')
    if status != "OK":
        raise RuntimeError("Gmail IMAP search failed")
    for uid in reversed(data[0].split()):
        status, payload = client.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            continue
        message = email.message_from_bytes(payload[0][1])
        if verified_sender(message):
            return message
    raise RuntimeError("no verified CCleaner report email was found")


def sheets_service(service_json):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_service_account_info(json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def value_at(row, column):
    values = row["values"] if isinstance(row, dict) else row
    return values[column] if column < len(values) else ""


def col_name(index):
    output = ""
    while True:
        index, remainder = divmod(index, 26)
        output = chr(65 + remainder) + output
        if index == 0:
            return output
        index -= 1


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def get_sheet(service):
    values = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A1:E10000", valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="SERIAL_NUMBER").execute().get("values", [])
    if not values or len(values[0]) != len(set(values[0])) or any(header not in values[0] for header in HEADERS):
        raise RuntimeError("long-format target headers are missing or duplicated")
    headers, positions, records = values[0], {header: values[0].index(header) for header in HEADERS}, {}
    for row_number, row in enumerate(values[1:], start=2):
        if not row or not value_at(row, positions["日期"]):
            continue
        key = (parse_day(value_at(row, positions["日期"])), str(value_at(row, positions["合作方"])).strip(), str(value_at(row, positions["运营位"])).strip())
        if key in records:
            raise RuntimeError(f"duplicate long-format record: {key}")
        records[key] = {"row": row_number, "values": row}
    return headers, records


def plan_writes(headers, existing_rows, source):
    positions = {header: headers.index(header) for header in HEADERS}
    updates, appends, overwrites = [], [], []
    for day, metrics in sorted(source.items()):
        key = (day, PARTNER, OPERATION)
        row = existing_rows.get(key)
        if row is None:
            record = {"日期": day, "合作方": PARTNER, "运营位": OPERATION, "新增": metrics["new_users"]}
            if "blood_volume" in metrics:
                record["血量"] = metrics["blood_volume"]
            appends.append(record)
            continue
        for header, metric in (("新增", "new_users"), ("血量", "blood_volume")):
            if metric not in metrics:
                continue
            current, wanted = value_at(row, positions[header]), metrics[metric]
            if current in ("", None) or not values_match(current, wanted):
                updates.append({"range": f"'{SHEET_NAME}'!{col_name(positions[header])}{row['row']}", "values": [[wanted]]})
                if current not in ("", None):
                    overwrites.append(f"{day} {PARTNER}/{OPERATION}/{header}: sheet={current}, source={wanted}")
    return updates, appends, overwrites


def append_rows(service, headers, records):
    if not records:
        return
    positions = {header: headers.index(header) for header in HEADERS}
    rows = []
    for record in records:
        row = [""] * len(headers)
        for header in ("日期", "合作方", "运营位", "新增"):
            row[positions[header]] = record[header].isoformat() if header == "日期" else record[header]
        if "血量" in record:
            row[positions["血量"]] = record["血量"]
        rows.append(row)
    service.spreadsheets().values().append(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A1:{col_name(len(headers) - 1)}", valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"majorDimension": "ROWS", "values": rows}).execute()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", help="First report date to write (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="Ignore report dates later than YYYY-MM-DD")
    args = parser.parse_args()
    secrets = {name: os.environ.get(name) for name in ("GMAIL_IMAP_USERNAME", "GMAIL_APP_PASSWORD", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
    if not all(secrets.values()):
        raise RuntimeError("missing required GitHub Actions secret")
    gmail = gmail_imap_client(secrets["GMAIL_IMAP_USERNAME"], secrets["GMAIL_APP_PASSWORD"])
    try:
        report = newest_report(gmail)
        rows = [pdf_rows(raw_pdf) for raw_pdf in attachments(report)]
    finally:
        gmail.logout()
    if not rows:
        raise RuntimeError("verified CCleaner report has no PDF attachment")
    requested_start = parse_day(args.start_date) if args.start_date else None
    requested_end = parse_day(args.end_date) if args.end_date else None
    source, start, end = latest_three_days(rows[0], start=requested_start, end=requested_end)
    sheets = sheets_service(secrets["GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"])
    headers, existing = get_sheet(sheets)
    updates, appends, overwrites = plan_writes(headers, existing, source)
    if updates:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute()
    append_rows(sheets, headers, appends)
    print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "updated_cells": len(updates), "appended_rows": len(appends), "overwrites": overwrites}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
