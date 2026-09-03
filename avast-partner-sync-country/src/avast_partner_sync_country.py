"""Sync Avast country-detail CSV snapshots from Gmail to Google Sheets."""
import argparse
import csv
import email
import imaplib
import io
import json
import os
import re
import sys
from datetime import datetime, timedelta
from email.header import decode_header
from email.utils import parseaddr
from zoneinfo import ZoneInfo


SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量分国家"
PRICE_SHEET_NAME = "合作方价格"
HEADERS = ("日期", "合作方", "国家代码", "运营位", "新增", "血量")
PRICE_HEADERS = ("合作方", "国家Code", "运营位", "价格")
PARTNER = "Avast"
MAILBOX = "54lingbai@gmail.com"
SENDER = "online.acquisition@avast.com"
SUBJECT_PREFIX = "Daily Performance Snapshot from Avast | WPS |"
SURFACES = {
    "mmm_wps_ppi_008_595_b": "avast气泡",
    "mmm_wps_ppi_008_595_a": "avast换量弹窗",
    "mmm_wps_ppi_008_595_e": "avast文档雷达",
    "mmm_wps_ppi_008_595_c": "avast卸载后弹出H5",
}
PRICE_OPERATIONS = {
    "avast气泡": "气泡",
    "avast换量弹窗": "换量弹窗",
    "avast文档雷达": "文档雷达",
    "avast卸载后弹出H5": "卸载后弹出H5",
}
ALIASES = {
    "date": {"date", "day", "reportdate"},
    "campaign": {"campaign", "campaignid", "campaignname", "subcampaign", "subcampaignid", "placement", "placementid", "offer", "offerid", "source", "channel"},
    "country": {"country", "countrycode", "countryiso", "countrycodeiso", "geo"},
    "new": {"newusers", "newuser", "installs", "installcount", "installations", "downloads"},
    "blood": {"revenue", "totalrevenue", "netrevenue", "estimatedrevenue", "earnings", "estimatedearnings", "payout", "totalpayout", "commission", "cost", "totalcost", "spend", "totalspend", "ppi", "amount", "totalamount", "amountusd", "revenueusd", "payoutusd", "costusd"},
}


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    raise ValueError(f"unsupported date: {value!r}")


def number(value):
    text = str(value).replace("\u00a0", "").replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise ValueError(f"invalid numeric value: {value!r}")
    parsed = float(text)
    return int(parsed) if parsed.is_integer() else parsed


def norm(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def resolve_fields(row, require_campaign=True, require_blood=True):
    available = {norm(header): header for header in row if header not in (None, "")}
    fields = {key: next((available[alias] for alias in aliases if alias in available), None) for key, aliases in ALIASES.items()}
    missing = [key for key, value in fields.items() if value is None and not ((key == "campaign" and not require_campaign) or (key == "blood" and not require_blood))]
    if missing:
        raise RuntimeError("Avast CSV missing required columns: " + ", ".join(missing) + "; available columns: " + ", ".join(str(header) for header in row if header))
    return fields


def csv_rows(raw):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            rows = list(csv.DictReader(io.StringIO(raw.decode(encoding))))
            if not rows:
                raise RuntimeError("Avast CSV has no data rows")
            return rows
        except UnicodeDecodeError:
            pass
    raise RuntimeError("could not decode Avast CSV attachment")


def campaign_column(rows, fields):
    if fields["campaign"] is not None:
        return fields["campaign"]
    candidates = [header for header in rows[0] if any(str(row.get(header) or "").strip().lower() in SURFACES for row in rows)]
    if len(candidates) != 1:
        raise RuntimeError("Avast CSV could not uniquely locate the campaign column; available columns: " + ", ".join(str(header) for header in rows[0] if header))
    return candidates[0]


def parse_report(raw):
    rows = csv_rows(raw)
    fields = resolve_fields(rows[0], require_campaign=False, require_blood=False)
    fields["campaign"] = campaign_column(rows, fields)
    output = {}
    for row in rows:
        campaign = str(row[fields["campaign"]] or "").strip().lower()
        operation = SURFACES.get(campaign)
        if operation is None:
            continue
        day = parse_day(row[fields["date"]])
        country = str(row[fields["country"]] or "").strip().upper()
        if not country:
            continue
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise RuntimeError(f"invalid Avast country code: {country!r}")
        metrics = output.setdefault((day, country, operation), {"new": 0})
        metrics["new"] += number(row[fields["new"]])
    return output


def latest_three_days(source, end=None):
    eligible = [day for day, _, _ in source if end is None or day <= end]
    if not eligible:
        raise RuntimeError("Avast CSV has no mapped data on or before the requested end date")
    anchor = max(eligible)
    start = anchor - timedelta(days=2)
    return {(day, country, operation): metrics for (day, country, operation), metrics in source.items() if start <= day <= anchor}, start, anchor


def price_index(api):
    rows = api.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{PRICE_SHEET_NAME}'!A1:H10000",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute().get("values", [])
    if not rows or len(rows[0]) != len(set(rows[0])) or any(header not in rows[0] for header in PRICE_HEADERS):
        raise RuntimeError("partner-price headers are missing or duplicated")
    positions = {header: rows[0].index(header) for header in PRICE_HEADERS}
    prices = {}
    for row in rows[1:]:
        if str(value(row, positions["合作方"])).strip() != PARTNER:
            continue
        country = str(value(row, positions["国家Code"])).strip().upper()
        operation = str(value(row, positions["运营位"])).strip()
        raw_price = value(row, positions["价格"])
        if not country or not operation or raw_price in ("", None):
            continue
        key = (country, operation)
        if key in prices:
            raise RuntimeError(f"duplicate Avast partner price: {country}/{operation}")
        prices[key] = number(raw_price)
    return prices


def apply_prices(source, prices):
    result, missing = {}, []
    for (day, country, operation), metrics in source.items():
        price_operation = PRICE_OPERATIONS[operation]
        price = prices.get((country, price_operation))
        if price is None:
            missing.append(f"{day} {country}/{price_operation}")
            continue
        result[(day, country, operation)] = {"new": metrics["new"], "blood": metrics["new"] * price}
    if missing:
        raise RuntimeError("missing Avast partner price: " + "; ".join(sorted(missing)))
    return result

def decoded(value):
    return "".join(
        piece.decode(charset or "utf-8", "replace") if isinstance(piece, bytes) else piece
        for piece, charset in decode_header(value or "")
    )


def gmail(username, password):
    if username.strip().lower() != MAILBOX:
        raise RuntimeError(f"GMAIL_IMAP_USERNAME must be {MAILBOX}")
    client = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        client.login(username.strip(), password.replace(" ", "").strip())
    except imaplib.IMAP4.error as exc:
        raise RuntimeError("Gmail IMAP login failed") from exc
    return client


def reports(client):
    status, boxes = client.list()
    if status != "OK":
        raise RuntimeError("Gmail mailbox listing failed")
    all_mail = next((box.decode("utf-8", "replace").rsplit('"', 2)[-2] for box in boxes if b"\\All" in box), None)
    if client.select(all_mail or "INBOX", readonly=True)[0] != "OK":
        raise RuntimeError("Gmail mailbox select failed")
    status, data = client.uid("search", None, "FROM", SENDER, "SUBJECT", f'"{SUBJECT_PREFIX}"')
    if status != "OK":
        raise RuntimeError("Gmail Avast country-report search failed")
    for uid in reversed(data[0].split()):
        status, payload = client.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            continue
        message = email.message_from_bytes(payload[0][1])
        if parseaddr(message.get("From", ""))[1].lower() != SENDER or not decoded(message.get("Subject", "")).startswith(SUBJECT_PREFIX):
            continue
        for part in message.walk():
            raw, filename = part.get_payload(decode=True), decoded(part.get_filename() or "")
            if raw and filename.lower().endswith(".csv"):
                yield raw


def service(raw):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_service_account_info(json.loads(raw), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def value(row, index):
    return row[index] if index < len(row) else ""


def col(index):
    output = ""
    while True:
        index, remainder = divmod(index, 26)
        output = chr(65 + remainder) + output
        if index == 0:
            return output
        index -= 1


def targets(api):
    rows = api.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A1:F10000", valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="SERIAL_NUMBER").execute().get("values", [])
    if not rows or len(rows[0]) != len(set(rows[0])) or any(header not in rows[0] for header in HEADERS):
        raise RuntimeError("country-detail target headers are missing or duplicated")
    headers, pos, found = rows[0], {header: rows[0].index(header) for header in HEADERS}, {}
    for row_no, row in enumerate(rows[1:], 2):
        if row and value(row, pos["日期"]):
            key = (parse_day(value(row, pos["日期"])), str(value(row, pos["合作方"])).strip(), str(value(row, pos["国家代码"])).strip().upper(), str(value(row, pos["运营位"])).strip())
            if key in found:
                raise RuntimeError(f"duplicate country-detail record: {key}")
            found[key] = (row_no, row)
    return headers, found


def plan(headers, found, source):
    pos = {header: headers.index(header) for header in HEADERS}
    updates, appends, overwrites = [], [], []
    for (day, country, operation), metrics in sorted(source.items()):
        target = found.get((day, PARTNER, country, operation))
        if target is None:
            appends.append({"日期": day, "合作方": PARTNER, "国家代码": country, "运营位": operation, "新增": metrics["new"], "血量": metrics["blood"]})
            continue
        row_no, row = target
        for header, metric in (("新增", "new"), ("血量", "blood")):
            old, wanted = value(row, pos[header]), metrics[metric]
            if old in ("", None) or not values_match(old, wanted):
                updates.append({"range": f"'{SHEET_NAME}'!{col(pos[header])}{row_no}", "values": [[wanted]]})
                if old not in ("", None):
                    overwrites.append(f"{day} Avast/{country}/{operation}/{header}")
    return updates, appends, overwrites


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def append(api, headers, records):
    if not records:
        return
    pos, output = {header: headers.index(header) for header in HEADERS}, []
    for record in records:
        row = [""] * len(headers)
        for header in HEADERS:
            row[pos[header]] = record[header].isoformat() if header == "日期" else record[header]
        output.append(row)
    api.spreadsheets().values().append(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A1:{col(len(headers) - 1)}", valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"majorDimension": "ROWS", "values": output}).execute()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    args = parser.parse_args()
    username, password, raw = os.environ.get("GMAIL_IMAP_USERNAME", ""), os.environ.get("GMAIL_APP_PASSWORD", ""), os.environ.get("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON", "")
    if not username or not password or not raw:
        raise RuntimeError("missing required GitHub Actions secret")

    source = {}
    client = gmail(username, password)
    try:
        for raw_csv in reports(client):
            for key, metrics in parse_report(raw_csv).items():
                source[key] = metrics
    finally:
        client.logout()
    if not source:
        raise RuntimeError("no verified Avast country-report CSV rows were found")
    source, start, end = latest_three_days(source, parse_day(args.end_date) if args.end_date else None)
    api = service(raw)
    headers, found = targets(api)
    updates, appends, overwrites = plan(headers, found, source)
    if updates:
        api.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute()
    append(api, headers, appends)
    print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "records": len(source), "updated_cells": len(updates), "appended_rows": len(appends), "overwrites": overwrites}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
