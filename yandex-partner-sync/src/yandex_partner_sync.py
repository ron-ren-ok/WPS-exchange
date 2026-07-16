"""Sync verified Yandex daily metrics to the partner Google Sheet."""
import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://distribution.yandex.net/api/v2/constructor_statistics/api_table/?lang=en"
SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方返回数据"
FIELDS = (
    "default_field_dt", "default_field_country", "default_field_pack_id",
    "msetupstatistics_setups", "default_fixed_partner_reward_metric",
)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_day(value):
    text = str(value).strip().split(",", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported date: {value!r}")


def scalar(value):
    text = str(value).replace("\u00a0", "").replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise ValueError(f"non-numeric metric: {value!r}")
    number = float(text)
    return int(number) if number.is_integer() else number


def load_profile(name):
    profile = load_json(ROOT / "config" / "profiles" / f"{name}.json")
    required = {"profile_id", "status", "surface", "template", "country_id", "country_name", "pack_ids", "target_headers", "filter_fingerprint", "template_sha256"}
    if profile.get("status") != "active" or required - set(profile):
        raise RuntimeError(f"invalid or inactive {name} profile")
    contract = {key: profile[key] for key in ("profile_id", "version", "template", "country_id", "country_name", "pack_ids")}
    if digest(contract) != profile["filter_fingerprint"]:
        raise RuntimeError(f"{name} profile fingerprint mismatch")
    template = ROOT / "config" / "yandex-api-blocks-template.json"
    if digest(load_json(template)) != profile["template_sha256"]:
        raise RuntimeError("Yandex API template fingerprint mismatch")
    return profile


def walk(node):
    if isinstance(node, list):
        for value in node:
            yield from walk(value)
    elif isinstance(node, dict):
        if "id" in node:
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from walk(value)


def build_blocks(profile, start, end):
    blocks = load_json(ROOT / "config" / "yandex-api-blocks-template.json")
    blocks = [block for block in blocks if block.get("id") in {"filters", "measures", "conditional_filters", "currency_converter"}]
    for block in blocks:
        if block.get("id") == "filters":
            block["fields"] = [field for field in block.get("fields", []) if field.get("id") in {"templates", "period", "detalization"}]
    values = {
        "templates": profile["template"], "period": f"{start:%Y.%m.%d}-{end:%Y.%m.%d}", "detalization": "1",
        "default_constructor_field_country_group": True, "default_constructor_field_country_operation": "1",
        "default_constructor_field_country": [profile["country_id"]], "default_constructor_field_pack_id_group": True,
        "default_constructor_field_pack_id_operation": "1", "default_constructor_field_pack_id": ",".join(map(str, profile["pack_ids"])),
    }
    wanted = {"msetupstatistics_setups", "default_fixed_partner_reward_metric"}
    for field in walk(blocks):
        field_id = field.get("id")
        if field_id in values:
            field["value"] = values[field_id]
        elif field_id in {"msetupstatistics_setups", "promocode_statistics_paid_search", "default_fixed_partner_reward_metric"}:
            field["value"] = field_id in wanted
    return blocks


def fetch_daily(profile, start, end, token):
    body = json.dumps({"blocks": build_blocks(profile, start, end)}, ensure_ascii=False).encode()
    request = Request(ENDPOINT, data=body, method="POST", headers={"Authorization": f"OAuth {token}", "Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Yandex API HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(f"Yandex API connection error: {exc.reason}") from None
    rows = payload.get("data", {}).get("rows", payload.get("rows"))
    if not isinstance(rows, list):
        raise RuntimeError("Yandex response has no rows")
    totals = defaultdict(lambda: [0, 0])
    for row in rows:
        if isinstance(row, list) and len(row) == 1:
            row = row[0]
        if not isinstance(row, dict) or any(field not in row for field in FIELDS):
            raise RuntimeError("Yandex response fields changed")
        if str(row["default_field_country"]).strip().lower() != profile["country_name"].lower():
            raise RuntimeError("Yandex response has an unexpected country")
        if int(row["default_field_pack_id"]) not in set(profile["pack_ids"]):
            raise RuntimeError("Yandex response has an unexpected pack ID")
        day = parse_day(row["default_field_dt"])
        totals[day][0] += scalar(row["msetupstatistics_setups"])
        totals[day][1] += scalar(row["default_fixed_partner_reward_metric"])
    expected = {start + timedelta(days=index) for index in range((end - start).days + 1)}
    if set(totals) != expected:
        raise RuntimeError(f"Yandex source coverage gap: {sorted(expected - set(totals))}")
    return {day: {"new_users": values[0], "blood_volume": values[1]} for day, values in totals.items()}


def column_name(index):
    name = ""
    while True:
        index, remainder = divmod(index, 26)
        name = chr(65 + remainder) + name
        if index == 0:
            return name
        index -= 1


def sheet_rows(service):
    values = service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A1:Z1000", valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    if not values:
        raise RuntimeError("target sheet is empty")
    headers = values[0]
    required = ["日期", "Yandex换量弹窗新增", "Yandex换量弹窗血量", "Yandex气泡新增", "Yandex气泡血量"]
    if any(header not in headers for header in required) or len(headers) != len(set(headers)):
        raise RuntimeError("target sheet headers are missing or duplicated")
    result = {}
    for row_number, row in enumerate(values[1:], start=2):
        if not row or not row[0]:
            continue
        day = parse_day(row[0])
        if day in result:
            raise RuntimeError(f"duplicate date row in target sheet: {day}")
        result[day] = {"row": row_number, "values": row}
    return headers, result


def existing_value(row, column):
    values = row["values"]
    return values[column] if column < len(values) else ""


def first_missing_day(headers, rows, profiles, cutoff):
    missing = []
    for day, row in rows.items():
        if day > cutoff:
            continue
        for profile in profiles:
            for header in profile["target_headers"]:
                if existing_value(row, headers.index(header)) in ("", None):
                    missing.append(day)
    return min(missing) if missing else cutoff

def planned_updates(headers, rows, profile_rows, profile, allow_overwrite):
    updates, conflicts = [], []
    for day, metrics in sorted(profile_rows.items()):
        row = rows.get(day)
        if row is None:
            conflicts.append(f"missing date row: {day}")
            continue
        for header, key in zip(profile["target_headers"], ("new_users", "blood_volume")):
            column = headers.index(header)
            current, value = existing_value(row, column), metrics[key]
            if current not in ("", None) and str(current).replace(",", "") != str(value):
                if not allow_overwrite:
                    conflicts.append(f"{day} {header}: sheet={current}, source={value}")
                    continue
            if current in ("", None) or str(current).replace(",", "") != str(value):
                updates.append({"range": f"'{SHEET_NAME}'!{column_name(column)}{row['row']}", "values": [[value]]})
    if conflicts:
        raise RuntimeError("refusing to write conflicts: " + "; ".join(conflicts))
    return updates


def google_service(service_json):
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
    token = os.environ.get("YANDEX_DISTRIBUTION_TOKEN")
    service_json = os.environ.get("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")
    if not token or not service_json:
        raise RuntimeError("missing required GitHub Actions secret")
    end = parse_day(args.end_date) if args.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    service = google_service(service_json)
    headers, rows = sheet_rows(service)
    profiles = [load_profile(name) for name in ("popup", "bubble")]
    start = parse_day(args.start_date) if args.start_date else first_missing_day(headers, rows, profiles, end)
    if start > end:
        raise RuntimeError("start date is after end date")
    updates = []
    for profile in profiles:
        source_rows = fetch_daily(profile, start, end, token)
        updates.extend(planned_updates(headers, rows, source_rows, profile, args.allow_overwrite))
    if updates:
        service.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute()
    print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "updated_cells": len(updates), "overwrite": args.allow_overwrite}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
