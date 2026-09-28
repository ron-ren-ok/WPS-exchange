"""Sync verified Yandex daily metrics to the partner Google Sheet."""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://distribution.yandex.net/api/v2/constructor_statistics/api_table/?lang=en"
SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
HEADERS = ("日期", "合作方", "运营位", "新增", "血量")
PARTNER = "Yandex"
FIELDS = (
    "default_field_dt", "default_field_country", "default_field_pack_id",
    "msetupstatistics_setups", "default_fixed_partner_reward_metric",
)
MAX_ATTEMPTS = 4


def http_status(exc):
    return getattr(exc, "code", None) or getattr(getattr(exc, "resp", None), "status", None)


def retryable(exc):
    status = http_status(exc)
    if status is not None:
        return status == 429 or 500 <= status < 600
    if isinstance(exc, (URLError, TimeoutError, ConnectionError, OSError)):
        return True
    try:
        from httplib2 import ServerNotFoundError
    except ImportError:
        return False
    return isinstance(exc, ServerNotFoundError)


def retry_delay(exc, attempt):
    headers = getattr(exc, "headers", None) or getattr(exc, "resp", {})
    try:
        return min(30, max(2 ** attempt, float(headers.get("retry-after", 0))))
    except (TypeError, ValueError):
        return 2 ** attempt


def with_retry(action, label):
    for attempt in range(MAX_ATTEMPTS):
        try:
            return action()
        except Exception as exc:
            if not retryable(exc) or attempt == MAX_ATTEMPTS - 1:
                raise
            print(f"{label}: transient failure; retry {attempt + 1}/{MAX_ATTEMPTS - 1}")
            time.sleep(retry_delay(exc, attempt))


def requested_dates(start_date=None, end_date=None):
    end = parse_day(end_date) if end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    start = parse_day(start_date) if start_date else end - timedelta(days=6)
    if start > end:
        raise RuntimeError("start date is after end date")
    return start, end


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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


def scalar(value):
    text = str(value).replace("\u00a0", "").replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise ValueError(f"non-numeric metric: {value!r}")
    number = float(text)
    return int(number) if number.is_integer() else number


def load_profile(name):
    profile = load_json(ROOT / "config" / "profiles" / f"{name}.json")
    required = {"profile_id", "status", "surface", "template", "country_id", "country_name", "pack_ids", "filter_fingerprint", "template_sha256"}
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
            # `templates` belongs to the dashboard UI; api_table now rejects it.
            # The profile country/pack filters below define the actual source.
            block["fields"] = [field for field in block.get("fields", []) if field.get("id") in {"period", "detalization"}]
    values = {
        "period": f"{start:%Y.%m.%d}-{end:%Y.%m.%d}", "detalization": "1",
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


def normalize_oauth_token(token):
    """Accept either a bare OAuth token or a copied Authorization header."""
    token = token.strip().strip('"').strip("'")
    if token.lower().startswith("oauth"):
        token = token[5:].lstrip(" :=\t\r\n")
    # OAuth access tokens never contain whitespace. Removing it makes a
    # multi-line GitHub secret copied from a browser safe for HTTP headers.
    return "".join(token.split())

def fetch_daily(profile, start, end, token):
    token = normalize_oauth_token(token)
    if not token:
        raise RuntimeError("Yandex token is empty")
    body = json.dumps({"blocks": build_blocks(profile, start, end)}, ensure_ascii=False).encode()
    request = Request(ENDPOINT, data=body, method="POST", headers={"Authorization": f"OAuth {token}", "Accept": "application/json", "Content-Type": "application/json"})
    def read_response():
        with urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8")
    try:
        payload = json.loads(with_retry(read_response, "Yandex API"))
    except HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace").replace("\r", " ").replace("\n", " ").strip()
        raise RuntimeError(f"Yandex API HTTP {exc.code}: {detail[:1000]}") from None
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
        if not start <= day <= end:
            raise RuntimeError(f"Yandex response date outside requested range: {day}")
        totals[day][0] += scalar(row["msetupstatistics_setups"])
        totals[day][1] += scalar(row["default_fixed_partner_reward_metric"])
    reported_days = set(totals)
    source_start = validate_source_coverage(totals, start, end)
    if source_start > start:
        print(f"Yandex source has no historical data before {source_start}; skipping that leading interval.")
    return {day: {"new_users": values[0], "blood_volume": values[1], "inferred_zero": day not in reported_days} for day, values in totals.items()}


def validate_source_coverage(totals, start, end):
    """Infer only internal zero days; leave leading and trailing missing data pending."""
    if not totals:
        raise RuntimeError("Yandex source returned no data")
    source_start = min(totals)
    source_end = max(totals)
    if source_start < start or source_end > end:
        raise RuntimeError("Yandex source dates outside requested range")
    expected = {source_start + timedelta(days=index) for index in range((source_end - source_start).days + 1)}
    missing = sorted(expected - set(totals))
    # The Yandex daily table omits dates on which every requested metric is 0.
    # Preserve those calendar dates as explicit zero values for Sheets.
    if missing:
        print(f"Yandex API omitted internal dates: {','.join(day.isoformat() for day in missing)}; inferring 0 / 0 subject to existing-value protection.")
        for day in missing:
            totals[day] = [0, 0]
    if source_end < end:
        print(f"Yandex source pending after {source_end}; not filling trailing missing dates with zero.")
    return source_start


def column_name(index):
    name = ""
    while True:
        index, remainder = divmod(index, 26)
        name = chr(65 + remainder) + name
        if index == 0:
            return name
        index -= 1


def sheet_rows(service):
    values = with_retry(lambda: service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{SHEET_NAME}'!A:E",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="SERIAL_NUMBER",
    ).execute(), "Sheets read").get("values", [])
    if not values or len(values[0]) != len(set(values[0])) or any(header not in values[0] for header in HEADERS):
        raise RuntimeError("long-format target headers are missing or duplicated")
    headers = values[0]
    positions = {header: headers.index(header) for header in HEADERS}
    result = {}
    for row_number, row in enumerate(values[1:], start=2):
        if not row or not existing_value(row, positions["日期"]):
            continue
        key = (
            parse_day(existing_value(row, positions["日期"])),
            str(existing_value(row, positions["合作方"])).strip(),
            str(existing_value(row, positions["运营位"])).strip(),
        )
        if key in result:
            raise RuntimeError(f"duplicate long-format record in target sheet: {key}")
        result[key] = {"row": row_number, "values": row}
    return headers, result


def existing_value(row, column):
    values = row["values"] if isinstance(row, dict) else row
    return values[column] if column < len(values) else ""


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def has_nonzero_metrics(headers, row):
    for header in ("新增", "血量"):
        value = existing_value(row, headers.index(header))
        if value not in ("", None) and not values_match(value, 0):
            return True
    return False


def protect_missing_source(headers, rows, source, profile, start, end):
    reported = {day for day, metrics in source.items() if not metrics.get("inferred_zero", False)}
    for (day, partner, surface), row in rows.items():
        if partner != PARTNER or surface != profile["surface"] or not start <= day <= end or day in reported:
            continue
        if has_nonzero_metrics(headers, row):
            raise RuntimeError(f"Yandex source missing {day} {surface}, but sheet has nonzero/invalid metrics; refusing all writes")


def planned_writes(headers, rows, profile_rows, profile, allow_overwrite):
    positions = {header: headers.index(header) for header in HEADERS}
    updates, appends, conflicts, overwrites = [], [], [], []
    operation = profile["surface"]
    for day, metrics in sorted(profile_rows.items()):
        row = rows.get((day, PARTNER, operation))
        if row is not None and metrics.get("inferred_zero") and has_nonzero_metrics(headers, row):
            raise RuntimeError(f"refusing inferred zero over nonzero metrics: {day} {operation}")
        if row is None:
            appends.append({"日期": day, "合作方": PARTNER, "运营位": operation, "新增": metrics["new_users"], "血量": metrics["blood_volume"]})
            continue
        for header, key in (("新增", "new_users"), ("血量", "blood_volume")):
            current, value = existing_value(row, positions[header]), metrics[key]
            if current in ("", None):
                updates.append({"range": f"'{SHEET_NAME}'!{column_name(positions[header])}{row['row']}", "values": [[value]]})
            elif not values_match(current, value):
                detail = f"{day} {PARTNER}/{operation}/{header}: sheet={current}, source={value}"
                if allow_overwrite:
                    updates.append({"range": f"'{SHEET_NAME}'!{column_name(positions[header])}{row['row']}", "values": [[value]]})
                    overwrites.append(detail)
                else:
                    conflicts.append(detail)
    if conflicts:
        raise RuntimeError("refusing to write conflicts: " + "; ".join(conflicts))
    return updates, appends, overwrites


def append_rows(service, headers, records):
    if not records:
        return
    pending = list(records)
    for attempt in range(MAX_ATTEMPTS):
        try:
            append_once(service, headers, pending)
            return
        except Exception as exc:
            if not retryable(exc):
                raise
            # A timeout/5xx may have committed. Never blindly resend an append.
            current_headers, current_rows = sheet_rows(service)
            if current_headers != headers:
                raise RuntimeError("target headers changed during append recovery") from exc
            remaining = []
            for record in pending:
                key = (record["日期"], record["合作方"], record["运营位"])
                row = current_rows.get(key)
                if row is None:
                    remaining.append(record)
                elif any(not values_match(existing_value(row, headers.index(header)), record[header]) for header in ("新增", "血量")):
                    raise RuntimeError(f"append recovery found conflicting record: {key}") from exc
            if not remaining:
                print("Append recovered: all records confirmed by readback.")
                return
            if http_status(exc) != 429:
                raise RuntimeError("append outcome uncertain and readback incomplete; refusing resend to prevent duplicates; rerun after checking the sheet") from exc
            if attempt == MAX_ATTEMPTS - 1:
                raise
            pending = remaining
            print(f"Sheets append: rate limited; retry {attempt + 1}/{MAX_ATTEMPTS - 1} after readback")
            time.sleep(retry_delay(exc, attempt))


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
        range=f"'{SHEET_NAME}'!A1:{column_name(len(headers) - 1)}",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"majorDimension": "ROWS", "values": values},
    ).execute()


def verify_writes(service, headers, profiles, source_by_profile):
    current_headers, rows = sheet_rows(service)
    if current_headers != headers:
        raise RuntimeError("target headers changed during sync")
    verified = 0
    for profile in profiles:
        for day, metrics in source_by_profile[profile["profile_id"]].items():
            key = (day, PARTNER, profile["surface"])
            row = rows.get(key)
            if row is None or any(not values_match(existing_value(row, headers.index(header)), metrics[metric]) for header, metric in (("新增", "new_users"), ("血量", "blood_volume"))):
                raise RuntimeError(f"write readback mismatch: {key}")
            verified += 1
    return verified


def google_service(service_json):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    credentials = Credentials.from_service_account_info(json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--allow-overwrite", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    token = os.environ.get("YANDEX_DISTRIBUTION_TOKEN")
    service_json = os.environ.get("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")
    if not token or not service_json:
        raise RuntimeError("missing required GitHub Actions secret")
    start, end = requested_dates(args.start_date, args.end_date)
    service = google_service(service_json)
    headers, rows = sheet_rows(service)
    profiles = [load_profile(name) for name in ("popup", "bubble")]
    source_by_profile = {profile["profile_id"]: fetch_daily(profile, start, end, token) for profile in profiles}
    updates, appends, overwrites = [], [], []
    for profile in profiles:
        protect_missing_source(headers, rows, source_by_profile[profile["profile_id"]], profile, start, end)
        next_updates, next_appends, next_overwrites = planned_writes(headers, rows, source_by_profile[profile["profile_id"]], profile, args.allow_overwrite)
        updates.extend(next_updates)
        appends.extend(next_appends)
        overwrites.extend(next_overwrites)
    if updates:
        with_retry(lambda: service.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}).execute(), "Sheets update")
    append_rows(service, headers, appends)
    verified = verify_writes(service, headers, profiles, source_by_profile)
    print(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "allow_overwrite": args.allow_overwrite, "updated_cells": len(updates), "appended_rows": len(appends), "verified_rows": verified, "overwrites": overwrites}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
