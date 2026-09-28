"""Sync the verified Winriser bubble metrics from Tracker / EntireTrack."""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup



SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
LOGIN_URL = "https://innovana.in/trackingassistant/"
REPORT_URL = "https://innovana.in/trackingassistant/viewdailyinstallinfo.aspx"
EXPAND_URL = "https://innovana.in/trackingassistant/ajax/fetchDailyInstallinfo.aspx"
HEADERS = ("日期", "合作方", "运营位", "新增", "血量")
PARTNER = "Winriser"
SOURCE_TO_OPERATION = {
    "wnrwpsofc": "气泡",
    "wnrwpsofc_exchange": "换量弹窗",
    "wnrwps_radar": "文档雷达",
}
RETRIES = 3
RETRY_DELAY = 5


def retryable(exc):
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None) or getattr(getattr(exc, "resp", None), "status", None)
    return status in (408, 429, 500, 502, 503, 504) or isinstance(
        exc, (requests.Timeout, requests.ConnectionError, TimeoutError, ConnectionError)
    )


def retry_call(call):
    for attempt in range(RETRIES + 1):
        try:
            return call()
        except Exception as exc:
            if not retryable(exc) or attempt == RETRIES:
                raise
            print(f"Transient request failure; retry {attempt + 1}/{RETRIES} in {RETRY_DELAY}s", file=sys.stderr)
            time.sleep(RETRY_DELAY)


def tracker_request(session, method, url, **kwargs):
    def call():
        response = getattr(session, method)(url, timeout=30, **kwargs)
        response.raise_for_status()
        return response
    return retry_call(call)


def sheets_execute(request):
    return retry_call(request.execute)


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y"):
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
    response = tracker_request(session, "get", LOGIN_URL)
    soup = BeautifulSoup(response.text, "html.parser")
    password = soup.select_one("input[type=password][name]")
    username = next((field for field in soup.select("input[type=text][name]") if field.get("name")), None)
    submit = next((field for field in soup.select("input[type=submit][name]") if "login" in field.get("value", "").lower()), None)
    if not password or not username or not submit:
        raise RuntimeError("Tracker login form changed")
    data = form_data(soup)
    data.update({username["name"]: "WPS", password["name"]: secret, submit["name"]: submit.get("value", "Login")})
    response = tracker_request(session, "post", response.url, data=data)
    if "dashboard.aspx" not in response.url.lower() and "logout" not in response.text.lower():
        raise RuntimeError("Tracker login was not accepted")


def report_controls(session):
    response = tracker_request(session, "get", REPORT_URL)
    soup = BeautifulSoup(response.text, "html.parser")
    source = soup.select_one("select[name='ctl00$ContentPlaceHolder1$ddSource']")
    report_date = soup.select_one("select[name='ctl00$ContentPlaceHolder1$dddate']")
    submit = soup.select_one("input[name='ctl00$ContentPlaceHolder1$btnview']")
    if not source or not report_date or not submit:
        raise RuntimeError("Tracker report controls changed")
    return soup, source, report_date, submit


def fetch_parent_reports(session, include_history=False):
    soup, source, report_date, submit = report_controls(session)
    options = tuple(dict.fromkeys(option.get("value") for option in report_date.select("option") if option.get("value"))) if include_history else ("3",)
    if not options:
        raise RuntimeError("Tracker has no selectable historical date options")
    for index, option in enumerate(options):
        if index:
            soup, source, report_date, submit = report_controls(session)
        data = form_data(soup)
        data.update({source["name"]: "0", report_date["name"]: option, submit["name"]: submit.get("value", "View Report")})
        yield tracker_request(session, "post", REPORT_URL, data=data).text


def fetch_parent_report(session):
    return next(fetch_parent_reports(session))


def fetch_child_sources(session, partner_id, day):
    response = tracker_request(
        session, "post", EXPAND_URL,
        data={
            "PartnerId": str(partner_id), "SourceId": "0",
            "dateFrom": day.isoformat(), "dateTo": day.isoformat(),
            "ExpandPartner": "1", "ExpandSrc": "0", "ExpandCamp": "0", "ExpandPub": "0",
        },
    )
    return response.text


def parse_parent_rows(html, cutoff, start=None):
    soup = BeautifulSoup(html, "html.parser")
    expected = ("Date", "Source", "Install Count", "Spend-PPI($)")
    tables = soup.find_all("table")
    table = next((table for table in tables if expected == tuple(cell.get_text(" ", strip=True) for cell in table.find_all("th")[-4:])), None)
    if tables and table is None:
        raise RuntimeError("Tracker report table headers changed")
    report_rows = table.find_all("tr") if table else soup.find_all("tr")
    if not report_rows:
        raise RuntimeError("Tracker child-source response has no rows")
    rows = {}
    for tr in report_rows:
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
        if len(cells) < 5:
            continue
        day_text, source, _, _ = cells[-4:]
        if source != "WPS":
            continue
        day = parse_day(day_text)
        if day > cutoff or (start is not None and day < start):
            continue
        key = tr.select_one("input[name$='$key']")
        if key is None or not key.get("value"):
            raise RuntimeError(f"Tracker WPS parent key is missing for {day}")
        if day in rows:
            raise RuntimeError(f"Tracker has duplicate WPS parent rows for {day}")
        rows[day] = key["value"]
    if not rows:
        raise RuntimeError("Tracker returned no WPS parent rows")
    return rows


def parse_report(html, cutoff, start=None, diagnostics=None):
    diagnostics = diagnostics if diagnostics is not None else {}
    soup = BeautifulSoup(html, "html.parser")
    # The Tracker expand endpoint returns bare <tr> fragments rather than a table.
    rows = {}
    report_rows = soup.find_all("tr")
    if not report_rows:
        raise RuntimeError("Tracker child-source response has no rows")
    for tr in report_rows:
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all("td")]
        if not cells or (len(cells) >= 4 and cells[-4] == "Date"):
            continue
        if len(cells) < 5:
            diagnostics["malformed_rows"] = diagnostics.get("malformed_rows", 0) + 1
            continue
        day_text, source, installs, spend = cells[-4:]
        source_key = source.strip().lower().split(" - ", 1)[0]
        operation = SOURCE_TO_OPERATION.get(source_key)
        if operation is None:  # Ignore WPS aggregate and unrelated child sources.
            bucket = "aggregate_rows" if source_key == "wps" else "unmapped_sources"
            if bucket == "aggregate_rows":
                diagnostics[bucket] = diagnostics.get(bucket, 0) + 1
            else:
                counts = diagnostics.setdefault(bucket, {})
                counts[source_key] = counts.get(source_key, 0) + 1
            continue
        day = parse_day(day_text)
        if day > cutoff or (start is not None and day < start):
            diagnostics["outside_range_rows"] = diagnostics.get("outside_range_rows", 0) + 1
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
    values = sheets_execute(service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{SHEET_NAME}'!A:E",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="SERIAL_NUMBER",
    )).get("values", [])
    if not values or len(values[0]) != len(set(values[0])) or any(header not in values[0] for header in HEADERS):
        raise RuntimeError("long-format target headers are missing or duplicated")
    headers = values[0]
    positions = {header: headers.index(header) for header in HEADERS}
    rows = {}
    for row_number, row in enumerate(values[1:], start=2):
        if str(value_at(row, positions["合作方"])).strip() != PARTNER:
            continue
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
    # Append is not idempotent: a lost response can still mean the write succeeded.
    pending = records
    for attempt in range(RETRIES + 1):
        try:
            append_once(service, headers, pending)
            return
        except Exception as exc:
            if not retryable(exc):
                raise
            print("Append response uncertain; checking target before retry", file=sys.stderr)
            time.sleep(RETRY_DELAY)
            fresh_headers, targets = get_sheet(service)
            if fresh_headers != headers:
                raise RuntimeError("target headers changed during append recovery") from exc
            pending = []
            positions = {header: headers.index(header) for header in HEADERS}
            for record in records:
                found = targets.get((record["日期"], PARTNER, record["运营位"]))
                if found is None:
                    pending.append(record)
                elif any(not values_match(value_at(found, positions[h]), record[h]) for h in ("新增", "血量")):
                    raise RuntimeError("append recovery found conflicting metrics") from exc
            if not pending:
                return
            if attempt == RETRIES:
                raise


def append_once(service, headers, records):
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


def verify_source(service, source):
    headers, targets = get_sheet(service)
    positions = {header: headers.index(header) for header in HEADERS}
    errors = []
    for (day, operation), metrics in sorted(source.items()):
        row = targets.get((day, PARTNER, operation))
        if row is None:
            errors.append(f"missing: {day}/{operation}")
        else:
            for header, metric in (("新增", "new_users"), ("血量", "blood_volume")):
                if not values_match(value_at(row, positions[header]), metrics[metric]):
                    errors.append(f"mismatch: {day}/{operation}/{header}")
    if errors:
        raise RuntimeError("write verification failed: " + "; ".join(errors))
    return {"verified_records": len(source), "missing": 0, "mismatches": 0}


def collect_source(session, cutoff, start, diagnostics):
    parents = {}
    for html in fetch_parent_reports(session, include_history=start is not None):
        diagnostics["reports_fetched"] = diagnostics.get("reports_fetched", 0) + 1
        try:
            parsed = parse_parent_rows(html, cutoff, start)
        except RuntimeError as exc:
            if str(exc) not in ("Tracker returned no WPS parent rows", "Tracker child-source response has no rows", "Tracker report table headers changed"):
                raise
            diagnostics.setdefault("skipped_reports", []).append(str(exc))
            continue
        for day, partner_id in parsed.items():
            if day in parents and parents[day] != partner_id:
                raise RuntimeError(f"conflicting WPS parent IDs for {day}")
            parents[day] = partner_id
    if not parents:
        raise RuntimeError("Tracker returned no WPS parent rows in the requested range")
    source = {}
    for day, partner_id in sorted(parents.items()):
        parsed = parse_report(fetch_child_sources(session, partner_id, day), cutoff, start, diagnostics)
        if any(parsed_day != day for parsed_day, _ in parsed):
            raise RuntimeError(f"Tracker child response date differs from requested day {day}")
        source.update(parsed)
    if not source:
        raise RuntimeError("Tracker returned no verified Winriser child-source rows")
    return source, parents


def coverage_summary(source, parents, cutoff, start, diagnostics):
    days = sorted({day for day, _ in source})
    requested_start = start if start is not None else min(parents)
    expected_days = [requested_start + timedelta(days=i) for i in range((cutoff - requested_start).days + 1)]
    missing_days = [day.isoformat() for day in expected_days if day not in days]
    missing_records = [{"date": day.isoformat(), "operation": operation} for day in expected_days for operation in SOURCE_TO_OPERATION.values() if (day, operation) not in source]
    latest = {operation: max((day for day, op in source if op == operation), default=None) for operation in SOURCE_TO_OPERATION.values()}
    return {
        "status": "partial" if missing_records or diagnostics.get("malformed_rows") or diagnostics.get("skipped_reports") else "complete",
        "requested_range": {"start": start.isoformat() if start else None, "end": cutoff.isoformat(), "mode": "explicit" if start else "tracker_default"},
        "actual_range": {"start": days[0].isoformat(), "end": days[-1].isoformat(), "dates": [day.isoformat() for day in days]},
        "latest_by_operation": {op: day.isoformat() if day else None for op, day in latest.items()},
        "missing_dates": missing_days, "missing_records": missing_records,
        "zero_records": [{"date": day.isoformat(), "operation": op} for (day, op), m in sorted(source.items()) if m["new_users"] == 0 and m["blood_volume"] == 0],
        "diagnostics": diagnostics,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()
    secret = os.environ.get("WINRISER_LOGIN_SECRET", "").strip().strip('"').strip("'")
    service_json = os.environ.get("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")
    if not secret or not service_json:
        raise RuntimeError("missing required GitHub Actions secret")
    cutoff = parse_day(args.end_date) if args.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    start = parse_day(args.start_date) if args.start_date else None
    if start is not None and start > cutoff:
        raise RuntimeError("start date is after end date")
    diagnostics = {}
    with requests.Session() as session:
        session.headers["User-Agent"] = "WPS partner data sync/1.0"
        login(session, secret)
        source, parents = collect_source(session, cutoff, start, diagnostics)
    summary = coverage_summary(source, parents, cutoff, start, diagnostics)
    service = sheets_service(service_json)
    headers, target_rows = get_sheet(service)
    updates, appends, overwrites = plan_writes(headers, target_rows, source, args.allow_overwrite)
    if updates:
        sheets_execute(service.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}))
    append_rows(service, headers, appends)
    summary.update({"source_records": [{"date": day.isoformat(), "operation": operation} for day, operation in sorted(source)], "updated_cells": len(updates), "appended_rows": len(appends), "overwrites": overwrites, "verification": verify_source(service, source)})
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, requests.RequestException, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
