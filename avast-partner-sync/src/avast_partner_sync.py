"""Fetch Avast PBI PDF attachments from Gmail and sync verified daily metrics."""
import argparse
import base64
import io
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pdfplumber

SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方返回数据"
ORIGINAL_SENDER = "no-reply-powerbi@microsoft.com"
FORWARDER = "partner@wps.com"
SURFACES = {
    "popup": {"subject": "Avast AV - WPS - Daily PBI report", "headers": ("Avast换量弹窗新增", "Avast换量弹窗血量")},
    "bubble": {"subject": "Avast AV - WPS - Toast - Daily PBI report", "headers": ("Avast气泡新增", "Avast气泡血量")},
}


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
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


def parse_avast_page(page_text):
    """Return daily records from the first PBI page, with strict Total semantics."""
    # PBI exports repeat this header for the new-user and revenue tables.
    # The first table and its immediately following Total pair are authoritative.
    header_lines = re.findall(r"(?m)^Country Code\s+(.+)$", page_text)
    if not header_lines:
        raise ValueError("Avast page-one Country Code header was not found")
    days = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", header_lines[0])
    if not days or len(days) != len(set(days)):
        raise ValueError("Avast first-table date headers are missing or duplicated")
    totals = re.findall(r"(?m)^Total\s+(.+)$", page_text)
    new_line = next((line for line in totals if "$" not in line), None)
    if new_line is None:
        raise ValueError("Avast first non-$ Total was not found")
    position = totals.index(new_line)
    blood_line = totals[position + 1] if position + 1 < len(totals) else None
    if not blood_line or "$" not in blood_line:
        raise ValueError("Avast $ Total must immediately follow the non-$ Total")
    new_values = re.findall(r"\d[\d,]*", new_line)
    blood_values = re.findall(r"\$[\d,]+(?:\.\d+)?", blood_line)
    if len(new_values) != len(days) + 1 or len(blood_values) != len(days) + 1:
        raise ValueError("Avast Total values do not align exactly with dates")
    return {parse_day(day): {"new_users": number(new), "blood_volume": number(blood)}
            for day, new, blood in zip(days, new_values[:len(days)], blood_values[:len(days)])}


def pdf_rows(raw_pdf):
    with pdfplumber.open(io.BytesIO(raw_pdf)) as pdf:
        if not pdf.pages:
            raise ValueError("Avast attachment is empty")
        return parse_avast_page(pdf.pages[0].extract_text() or "")


def oauth_client_config(value):
    data = json.loads(value)
    config = data.get("installed") or data.get("web")
    if not config or not config.get("client_id") or not config.get("client_secret"):
        raise RuntimeError("GMAIL_OAUTH_CLIENT_JSON must be an OAuth client JSON")
    return config


def gmail_service(client_json, refresh_token):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    config = oauth_client_config(client_json)
    creds = Credentials(token=None, refresh_token=refresh_token.strip(), token_uri=config.get("token_uri", "https://oauth2.googleapis.com/token"), client_id=config["client_id"], client_secret=config["client_secret"], scopes=["https://www.googleapis.com/auth/gmail.readonly"])
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def sheets_service(service_json):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_info(json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def b64url(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def headers(message):
    return {item["name"].lower(): item["value"] for item in message.get("payload", {}).get("headers", [])}


def parts(node):
    yield node
    for child in node.get("parts", []) or []:
        yield from parts(child)


def body_text(message):
    values = []
    for part in parts(message.get("payload", {})):
        if part.get("mimeType", "").startswith("text/") and part.get("body", {}).get("data"):
            values.append(b64url(part["body"]["data"]).decode("utf-8", "replace"))
    return "\n".join(values)


def verified_sender(message):
    sent_by = headers(message).get("from", "").lower()
    if ORIGINAL_SENDER in sent_by:
        return True
    return FORWARDER in sent_by and ORIGINAL_SENDER in body_text(message).lower()


def attachments(service, message):
    for part in parts(message.get("payload", {})):
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")
        if attachment_id and (part.get("mimeType") == "application/pdf" or part.get("filename", "").lower().endswith(".pdf")):
            payload = service.users().messages().attachments().get(userId="me", messageId=message["id"], id=attachment_id).execute()
            yield b64url(payload["data"])


def source_rows(service, surface, start, end):
    spec = SURFACES[surface]
    query = f'in:anywhere has:attachment -in:spam -in:trash subject:"{spec["subject"]}"'
    listing = service.users().messages().list(userId="me", q=query, maxResults=100).execute().get("messages", [])
    resolved = {}
    rejected = 0
    for item in listing:  # Gmail returns newest first; first report wins for each day.
        message = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
        if not verified_sender(message):
            rejected += 1
            continue
        for raw_pdf in attachments(service, message):
            for day, metrics in pdf_rows(raw_pdf).items():
                if start <= day <= end and day not in resolved:
                    resolved[day] = metrics
    if not resolved:
        raise RuntimeError(f"no verified {surface} Avast PDF rows in the requested date range")
    unavailable = [start + timedelta(days=i) for i in range((end - start).days + 1) if start + timedelta(days=i) not in resolved]
    print(json.dumps({"surface": surface, "available_days": len(resolved), "unavailable_days": [d.isoformat() for d in unavailable], "rejected_messages": rejected}, ensure_ascii=False))
    return resolved


def col_name(index):
    result = ""
    while True:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        if index == 0:
            return result
        index -= 1


def get_sheet(service):
    values = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A1:Z1000", valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    if not values:
        raise RuntimeError("target sheet is empty")
    required = ["日期", *(h for spec in SURFACES.values() for h in spec["headers"])]
    if len(values[0]) != len(set(values[0])) or any(h not in values[0] for h in required):
        raise RuntimeError("target sheet headers are missing or duplicated")
    rows = {}
    for row_number, row in enumerate(values[1:], start=2):
        if row and row[0]:
            day = parse_day(row[0])
            if day in rows:
                raise RuntimeError(f"duplicate date row: {day}")
            rows[day] = {"row": row_number, "values": row}
    return values[0], rows


def value_at(row, column):
    return row["values"][column] if column < len(row["values"]) else ""


def first_missing(headers, rows, cutoff):
    days = [day for day, row in rows.items() if day <= cutoff and any(value_at(row, headers.index(h)) in ("", None) for spec in SURFACES.values() for h in spec["headers"])]
    return min(days) if days else cutoff


def updates_for(headers, rows, surface, source):
    updates = []
    for day, metrics in sorted(source.items()):
        row = rows.get(day)
        if not row:
            raise RuntimeError(f"missing target date row: {day}")
        for header, key in zip(SURFACES[surface]["headers"], ("new_users", "blood_volume")):
            column, wanted = headers.index(header), metrics[key]
            current = value_at(row, column)
            if current in ("", None) or str(current).replace(",", "") != str(wanted):
                updates.append({"range": f"'{SHEET_NAME}'!{col_name(column)}{row['row']}", "values": [[wanted]]})
    return updates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()
    secrets = {name: os.environ.get(name) for name in ("GMAIL_OAUTH_CLIENT_JSON", "GMAIL_REFRESH_TOKEN", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
    if not all(secrets.values()):
        raise RuntimeError("missing required GitHub Actions secret")
    end = parse_day(args.end_date) if args.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    sheets = sheets_service(secrets["GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"])
    headers_row, target_rows = get_sheet(sheets)
    start = parse_day(args.start_date) if args.start_date else first_missing(headers_row, target_rows, end)
    if start > end:
        raise RuntimeError("start date is after end date")
    gmail = gmail_service(secrets["GMAIL_OAUTH_CLIENT_JSON"], secrets["GMAIL_REFRESH_TOKEN"])
    writes = []
    for surface in SURFACES:
        writes.extend(updates_for(headers_row, target_rows, surface, source_rows(gmail, surface, start, end)))
    if writes:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": writes}).execute()
    print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "updated_cells": len(writes), "overwrite": args.allow_overwrite}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)