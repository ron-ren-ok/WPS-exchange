"""Sync the verified Winriser bubble metrics from Tracker / EntireTrack."""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup



SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
LOGIN_URL = "https://trk.entiretrack.com/trackingassistant/"
REPORT_URL = "https://trk.entiretrack.com/trackingassistant/viewdailyinstallinfo.aspx"
HEADERS = ("日期", "合作方", "运营位", "新增", "血量")
PARTNER = "Winriser"
SOURCE_TO_OPERATION = {
    "wnrwpsofc": "气泡",
    "wnrwpsofc_exchange": "换量弹窗",
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
    text = str(value).replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise ValueError(f"invalid numeric value: {value!r}")
    parsed = float(text)
    return int(parsed) if parsed.is_integer() else parsed


def form_data(soup):
    return {
        element["name"]: element.get("value", "")
        for element in soup.select("input[type=hidden][name]")
    }


def login(session, secret):
    response = session.get(LOGIN_URL, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    password = soup.select_one("input[type=password][name]")
    username = next((field for field in soup.select("input[type=text][name]") if field.get("name")), None)
    submit = next((field for field in soup.select("input[type=submit][name]") if "login" in field.get("value", "").lower()), None)
    if not password or not username or not submit:
        raise RuntimeError("Tracker login form changed")
    data = form_data(soup)
    data.update({username["name"]: "WPS", password["name"]: secret, submit["name"]: submit.get("value", "Login")})
    response = session.post(response.url, data=data, timeout=30)
    response.raise_for_status()
    if "dashboard.aspx" not in response.url.lower() and "logout" not in response.text.lower():
        raise RuntimeError("Tracker login was not accepted")


def source_option_value(select, source_name):
    normalized = source_name.strip().lower()
    for option in select.select("option"):
        label = option.get_text(" ", strip=True).lower()
        if label == normalized or label.startswith(normalized + " - "):
            return option.get("value")
    raise RuntimeError(f"Tracker source selector does not contain {source_name}")


def fetch_report(session, source_name):
    response = session.get(REPORT_URL, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    source = soup.select_one("select[name='ctl00$ContentPlaceHolder1$ddSource']")
    report_date = soup.select_one("select[name='ctl00$ContentPlaceHolder1$dddate']")
    submit = soup.select_one("input[name='ctl00$ContentPlaceHolder1$btnview']")
    if not source or not report_date or not submit:
        raise RuntimeError("Tracker report controls changed")
    data = form_data(soup)
    data.update({
        source["name"]: source_option_value(source, source_name),
        report_date["name"]: "3",
        submit["name"]: submit.get("value", "View Report"),
    })
    response = session.post(REPORT_URL, data=data, timeout=30)
    response.raise_for_status()
    return response.text


def parse_report(html, cutoff):
    soup = BeautifulSoup(html, "html.parser")
    expected = ("Date", "Source", "Install Count", "Spend-PPI($)")
    table = next((table for table in soup.find_all("table") if expected == tuple(cell.get_text(" ", strip=True) for cell in table.find_all("th")[-4:])), None)
    if table is None:
        raise RuntimeError("Tracker report table headers changed")
    rows = {}
    for tr in table.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
        if len(cells) < 5 or cells[-4] == "Date":
            continue
        day_text, source, installs, spend = cells[-4:]
        source_key = source.strip().lower().split(" - ", 1)[0]
        operation = SOURCE_TO_OPERATION.get(source_key)
        if operation is None:  # Ignore WPS aggregate and unrelated child sources.
            continue
        day = parse_day(day_text)
        if day > cutoff:
            continue
        key = (day, operation)
        if key in rows:
            raise RuntimeError(f"Tracker has duplicate {source} rows for {day}")
        rows[key] = {"new_users": number(installs), "blood_volume": number(spend)}
    return rows


def col_name(index):
    result = ""
    while True:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        if index == 0:
            return result
        index -= 1


def sheets_service(service_json):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    credentials = Credentials.from_service_account_info(json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


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


def value_at(row, column):
    values = row["values"] if isinstance(row, dict) else row
    return values[column] if column < len(values) else ""


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def plan_writes(headers, target_rows, source_rows, allow_overwrite):
    positions = {header: headers.index(header) for header in HEADERS}
    updates, appends, conflicts, overwrites = [], [], [], []
    for (day, operation), metrics in sorted(source_rows.items()):
        row = target_rows.get((day, PARTNER, operation))
        if row is None:
            appends.append({"日期": day, "合作方": PARTNER, "运营位": operation, "新增": metrics["new_users"], "血量": metrics["blood_volume"]})
            continue
        for header, key in (("新增", "new_users"), ("血量", "blood_volume")):
            current, wanted = value_at(row, positions[header]), metrics[key]
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
    parser.add_argument("--end-date")
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()
    secret = os.environ.get("WINRISER_LOGIN_SECRET", "").strip().strip('"').strip("'")
    service_json = os.environ.get("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")
    if not secret or not service_json:
        raise RuntimeError("missing required GitHub Actions secret")
    cutoff = parse_day(args.end_date) if args.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    with requests.Session() as session:
        session.headers["User-Agent"] = "WPS partner data sync/1.0"
        login(session, secret)
        source = {}
        unavailable_sources = []
        for source_name in SOURCE_TO_OPERATION:
            child_rows = parse_report(fetch_report(session, source_name), cutoff)
            if not child_rows:
                unavailable_sources.append(source_name)
            source.update(child_rows)
    if not source:
        raise RuntimeError("Tracker returned no verified Winriser rows for: " + ", ".join(SOURCE_TO_OPERATION))
    service = sheets_service(service_json)
    headers, target_rows = get_sheet(service)
    updates, appends, overwrites = plan_writes(headers, target_rows, source, args.allow_overwrite)
    if updates:
        service.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute()
    append_rows(service, headers, appends)
    print(json.dumps({"source_records": [{"date": day.isoformat(), "operation": operation} for day, operation in sorted(source)], "updated_cells": len(updates), "appended_rows": len(appends), "overwrites": overwrites, "unavailable_sources": unavailable_sources}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, requests.RequestException, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)