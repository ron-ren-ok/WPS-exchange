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
SHEET_NAME = "合作方返回数据"
LOGIN_URL = "https://trk.entiretrack.com/trackingassistant/"
REPORT_URL = "https://trk.entiretrack.com/trackingassistant/viewdailyinstallinfo.aspx"
TARGET_HEADERS = ("Winriser气泡新增", "Winriser气泡血量")


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
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


def fetch_report(session):
    response = session.get(REPORT_URL, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    source = soup.select_one("select[name='ctl00$ContentPlaceHolder1$ddSource']")
    report_date = soup.select_one("select[name='ctl00$ContentPlaceHolder1$dddate']")
    submit = soup.select_one("input[name='ctl00$ContentPlaceHolder1$btnview']")
    if not source or not report_date or not submit:
        raise RuntimeError("Tracker report controls changed")
    data = form_data(soup)
    data.update({source["name"]: "0", report_date["name"]: "3", submit["name"]: submit.get("value", "View Report")})
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
        if source != "WPS":
            continue
        day = parse_day(day_text)
        if day > cutoff:
            continue
        if day in rows:
            raise RuntimeError(f"Tracker has duplicate WPS rows for {day}")
        rows[day] = {"new_users": number(installs), "blood_volume": number(spend)}
    if not rows:
        raise RuntimeError("Tracker returned no verified WPS rows")
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
    values = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A1:Z1000", valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    if not values or "日期" not in values[0] or any(header not in values[0] for header in TARGET_HEADERS) or len(values[0]) != len(set(values[0])):
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


def updates_for(headers, target_rows, source_rows):
    updates, conflicts = [], []
    for day, metrics in sorted(source_rows.items()):
        row = target_rows.get(day)
        if not row:
            conflicts.append(f"missing target date row: {day}")
            continue
        for header, key in zip(TARGET_HEADERS, ("new_users", "blood_volume")):
            column, wanted = headers.index(header), metrics[key]
            current = value_at(row, column)
            if current not in ("", None) and str(current).replace(",", "") != str(wanted):
                conflicts.append(f"{day} {header}: sheet={current}, source={wanted}")
            elif current in ("", None):
                updates.append({"range": f"'{SHEET_NAME}'!{col_name(column)}{row['row']}", "values": [[wanted]]})
    if conflicts:
        raise RuntimeError("refusing to overwrite conflicts: " + "; ".join(conflicts))
    return updates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--end-date")
    args = parser.parse_args()
    secret = os.environ.get("WINRISER_LOGIN_SECRET", "").strip().strip('"').strip("'")
    service_json = os.environ.get("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")
    if not secret or not service_json:
        raise RuntimeError("missing required GitHub Actions secret")
    cutoff = parse_day(args.end_date) if args.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    with requests.Session() as session:
        session.headers["User-Agent"] = "WPS partner data sync/1.0"
        login(session, secret)
        source = parse_report(fetch_report(session), cutoff)
    service = sheets_service(service_json)
    headers, target_rows = get_sheet(service)
    updates = updates_for(headers, target_rows, source)
    if updates:
        service.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute()
    print(json.dumps({"source_days": sorted(day.isoformat() for day in source), "updated_cells": len(updates)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, requests.RequestException, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
