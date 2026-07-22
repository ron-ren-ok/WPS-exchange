"""Sync 360 daily new-user data from its source Google Sheet to the long partner table."""
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SOURCE_SHEET_ID = "1fHVgG5EnrSR-BXOsQxmNbIM_fk88qwTHe8u-gkxsFvw"
SOURCE_SHEET_NAME = "每日"
TARGET_SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
TARGET_SHEET_NAME = "合作方新增血量"
PARTNER = "360"
TARGET_HEADERS = ("日期", "合作方", "运营位", "新增", "血量")
SURFACES = {
    "360-1": "换量弹窗",
    "360-2": "气泡",
    "360-3": "卸载后引导H5",
}


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported date: {value!r}")


def number(value):
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ValueError(f"invalid numeric value: {value!r}") from exc
    return int(parsed) if parsed.is_integer() else parsed


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def value_at(row, column):
    values = row["values"] if isinstance(row, dict) else row
    return values[column] if column < len(values) else ""


def col_name(index):
    result = ""
    while True:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        if index == 0:
            return result
        index -= 1


def source_records(values, start, end):
    if not values:
        raise RuntimeError("360 source sheet is empty")
    headers = values[0]
    indexes = {header: headers.index(header) for header in SURFACES if header in headers}
    missing = [header for header in SURFACES if header not in indexes]
    if missing:
        raise RuntimeError("360 source headers are missing: " + ", ".join(missing))
    records = {}
    for row in values[1:]:
        if not row or not row[0]:
            continue
        try:
            day = parse_day(row[0])
        except ValueError:
            # The source has a summary row directly beneath its header.
            continue
        if not start <= day <= end:
            continue
        for header, operation in SURFACES.items():
            raw = row[indexes[header]] if len(row) > indexes[header] else ""
            new_users = number(raw)
            if new_users is not None:  # Blank means not reported; zero is a valid report.
                key = (day, PARTNER, operation)
                if key in records:
                    raise RuntimeError(f"duplicate 360 source record: {key}")
                records[key] = {"new_users": new_users}
    if not records:
        raise RuntimeError("360 source has no daily records in the requested range")
    return records


def target_records(values):
    if not values or len(values[0]) != len(set(values[0])) or any(header not in values[0] for header in TARGET_HEADERS):
        raise RuntimeError("long-format target headers are missing or duplicated")
    headers = values[0]
    positions = {header: headers.index(header) for header in TARGET_HEADERS}
    records = {}
    for row_number, row in enumerate(values[1:], start=2):
        if not row or not value_at(row, positions["日期"]):
            continue
        key = (
            parse_day(value_at(row, positions["日期"])),
            str(value_at(row, positions["合作方"])).strip(),
            str(value_at(row, positions["运营位"])).strip(),
        )
        if key in records:
            raise RuntimeError(f"duplicate long-format target record: {key}")
        records[key] = {"row": row_number, "values": row}
    return headers, records


def plan_writes(headers, existing, source, allow_overwrite):
    new_column = headers.index("新增")
    updates, appends, conflicts, overwrites = [], [], [], []
    for key, metrics in sorted(source.items()):
        row = existing.get(key)
        if row is None:
            day, partner, operation = key
            appends.append({"日期": day, "合作方": partner, "运营位": operation, "新增": metrics["new_users"]})
            continue
        current, wanted = value_at(row, new_column), metrics["new_users"]
        if current in ("", None):
            updates.append({"range": f"'{TARGET_SHEET_NAME}'!{col_name(new_column)}{row['row']}", "values": [[wanted]]})
        elif not values_match(current, wanted):
            detail = f"{key[0]} {key[1]}/{key[2]}/新增: sheet={current}, source={wanted}"
            if allow_overwrite:
                updates.append({"range": f"'{TARGET_SHEET_NAME}'!{col_name(new_column)}{row['row']}", "values": [[wanted]]})
                overwrites.append(detail)
            else:
                conflicts.append(detail)
    if conflicts:
        raise RuntimeError("refusing to overwrite conflicts: " + "; ".join(conflicts))
    return updates, appends, overwrites


def append_rows(service, headers, records):
    if not records:
        return
    positions = {header: headers.index(header) for header in TARGET_HEADERS}
    values = []
    for record in records:
        row = [""] * len(headers)
        row[positions["日期"]] = record["日期"].isoformat()
        row[positions["合作方"]] = record["合作方"]
        row[positions["运营位"]] = record["运营位"]
        row[positions["新增"]] = record["新增"]
        values.append(row)
    service.spreadsheets().values().append(
        spreadsheetId=TARGET_SHEET_ID,
        range=f"'{TARGET_SHEET_NAME}'!A1:{col_name(len(headers) - 1)}",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"majorDimension": "ROWS", "values": values},
    ).execute()


def sheets_service(service_json):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_service_account_info(json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--allow-overwrite", action="store_true")
    args = parser.parse_args()
    service_json = os.environ.get("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")
    if not service_json:
        raise RuntimeError("missing GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")
    end = parse_day(args.end_date) if args.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    start = parse_day(args.start_date) if args.start_date else date(2023, 1, 1)
    if start > end:
        raise RuntimeError("start date is after end date")
    service = sheets_service(service_json)
    source_values = service.spreadsheets().values().get(
        spreadsheetId=SOURCE_SHEET_ID, range=f"'{SOURCE_SHEET_NAME}'!A:D", valueRenderOption="UNFORMATTED_VALUE"
    ).execute().get("values", [])
    target_values = service.spreadsheets().values().get(
        spreadsheetId=TARGET_SHEET_ID, range=f"'{TARGET_SHEET_NAME}'!A:E", valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="SERIAL_NUMBER"
    ).execute().get("values", [])
    source = source_records(source_values, start, end)
    headers, existing = target_records(target_values)
    updates, appends, overwrites = plan_writes(headers, existing, source, args.allow_overwrite)
    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=TARGET_SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}
        ).execute()
    append_rows(service, headers, appends)
    print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "updated_cells": len(updates), "appended_rows": len(appends), "overwrites": overwrites}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)