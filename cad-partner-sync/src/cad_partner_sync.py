"""Incrementally sync CAD DWG new-user data into the partner long table."""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SOURCE_SHEET_ID = "1zhFz3d996b1oiD4p4Oc786dPWlriu7gg6g2S6TN83b8"
SOURCE_SHEET_NAME = "DWG新增"
TARGET_SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
TARGET_SHEET_NAME = "合作方新增血量"
PARTNER = "CAD"
SOURCE_HEADERS = ("日期", "运营位", "DWG安装量")
TARGET_HEADERS = ("日期", "合作方", "运营位", "新增", "血量")


def parse_day(value, reference_day=None):
    """Parse Sheets serials, full dates, and Chinese month/day text.

    The CAD source omits the year (for example, 7月15日). Pick the occurrence
    closest to the requested end date so December/January rollover stays safe.
    """
    text = str(value).strip().split(",", 1)[0]
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", text)
    if match and reference_day:
        month, day = map(int, match.groups())
        candidates = []
        for year in (reference_day.year - 1, reference_day.year, reference_day.year + 1):
            try:
                candidates.append(date(year, month, day))
            except ValueError:
                pass
        if candidates:
            return min(candidates, key=lambda candidate: abs((candidate - reference_day).days))
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


def normalized_header(value):
    return "".join(str(value).strip().lower().split())


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


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def source_layout(values):
    if not values:
        raise RuntimeError("CAD source sheet is empty")
    headers = [normalized_header(value) for value in values[0]]
    expected = [normalized_header(value) for value in SOURCE_HEADERS]
    if any(headers.count(header) != 1 for header in expected):
        raise RuntimeError("CAD source headers are missing or duplicated; expected 日期, 运营位, DWG安装量")
    return {header: headers.index(normalized_header(header)) for header in SOURCE_HEADERS}


def source_records(values, start, end):
    """Return one source record for each date and operating position."""
    positions = source_layout(values)
    records = {}
    for row in values[1:]:
        if not row or not value_at(row, positions["日期"]):
            continue
        try:
            day = parse_day(value_at(row, positions["日期"]), end)
        except ValueError:
            continue  # Ignore the 汇总 row and other non-daily notes.
        if not start <= day <= end:
            continue
        operation = str(value_at(row, positions["运营位"])).strip()
        if not operation:
            continue
        new_users = number(value_at(row, positions["DWG安装量"]))
        if new_users is None:
            continue
        key = (day, PARTNER, operation)
        if key in records:
            raise RuntimeError(f"duplicate CAD source record: {key}")
        records[key] = {"new_users": new_users}
    return records


def first_source_day(values, reference_day):
    positions = source_layout(values)
    days = []
    for row in values[1:]:
        if not row or not value_at(row, positions["日期"]):
            continue
        operation = str(value_at(row, positions["运营位"])).strip()
        if not operation:
            continue
        try:
            day = parse_day(value_at(row, positions["日期"]), reference_day)
            metric = number(value_at(row, positions["DWG安装量"]))
        except ValueError:
            continue
        if metric is not None and day <= reference_day:
            days.append(day)
    if not days:
        raise RuntimeError("CAD source has no dated operation records through the requested end date")
    return min(days)


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


def required_source_records(headers, existing, source, source_first_day, end, explicit_start=None, lookback_days=14):
    """Backfill all data initially, then recheck a small recent window."""
    new_column = headers.index("新增")
    if explicit_start:
        floor = max(explicit_start, source_first_day)
        return {key: metrics for key, metrics in source.items() if key[0] >= floor}
    recent_start = max(source_first_day, end - timedelta(days=lookback_days - 1))
    by_operation = {}
    for key, metrics in source.items():
        by_operation.setdefault(key[2], []).append((key, metrics))
    required = {}
    for operation, records in by_operation.items():
        populated_days = [
            key[0] for key, record in existing.items()
            if key[1] == PARTNER and key[2] == operation and value_at(record, new_column) not in ("", None)
        ]
        latest = max(populated_days) if populated_days else None
        # A brand-new operation needs its complete available history. Once an
        # operation exists in the target, keep normal runs bounded while still
        # repairing recent gaps.
        floor = source_first_day if latest is None else min(latest + timedelta(days=1), recent_start)
        required.update({key: metrics for key, metrics in records if key[0] >= floor})
    return required


def plan_writes(headers, existing, source, allow_overwrite=False):
    new_column = headers.index("新增")
    updates, appends, overwrites, skipped_conflicts = [], [], [], []
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
                skipped_conflicts.append(detail)
    return updates, appends, overwrites, skipped_conflicts


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
    credentials = Credentials.from_service_account_info(
        json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
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
    requested_start = parse_day(args.start_date) if args.start_date else None
    if requested_start and requested_start > end:
        raise RuntimeError("start date is after end date")
    service = sheets_service(service_json)
    target_values = service.spreadsheets().values().get(
        spreadsheetId=TARGET_SHEET_ID,
        range=f"'{TARGET_SHEET_NAME}'!A:E",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="SERIAL_NUMBER",
    ).execute().get("values", [])
    headers, existing = target_records(target_values)
    source_values = service.spreadsheets().values().get(
        spreadsheetId=SOURCE_SHEET_ID,
        range=f"'{SOURCE_SHEET_NAME}'!A:C",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="SERIAL_NUMBER",
    ).execute().get("values", [])
    source_first_day = first_source_day(source_values, end)
    source = source_records(source_values, source_first_day, end)
    required = required_source_records(headers, existing, source, source_first_day, end, requested_start)
    if not required:
        print(json.dumps({"status": "already_complete", "updated_cells": 0, "appended_rows": 0}, ensure_ascii=False))
        return
    source_start = min(key[0] for key in required)
    updates, appends, overwrites, skipped_conflicts = plan_writes(headers, existing, required, args.allow_overwrite)
    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=TARGET_SHEET_ID,
            body={"valueInputOption": "USER_ENTERED", "data": updates},
        ).execute()
    append_rows(service, headers, appends)
    print(json.dumps({
        "start": (requested_start or source_start).isoformat(),
        "end": end.isoformat(),
        "updated_cells": len(updates),
        "appended_rows": len(appends),
        "overwrites": overwrites,
        "skipped_conflicts": skipped_conflicts,
        "source_operations": sorted({key[2] for key in required}),
    }, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
