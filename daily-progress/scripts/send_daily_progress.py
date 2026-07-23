#!/usr/bin/env python3
"""Prepare WPS daily-progress cards from long-format partner data without AI."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import google.auth.transport.requests
from google.oauth2 import service_account


SPREADSHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SOURCE_SHEET_NAME = "合作方新增血量"
TARGET_SHEET_NAME = "目标完成度"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=63683153"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
RAW_UNIT_DIVISOR = 10_000
REQUIRED_SOURCE_HEADERS = ("日期", "合作方", "运营位", "新增", "血量")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def cell_text(cell: dict) -> str:
    return cell.get("formattedValue", "").strip()


def cell_number(cell: dict) -> float | None:
    effective = cell.get("effectiveValue", {})
    if effective.get("numberValue") is not None:
        return float(effective["numberValue"])
    text = cell_text(cell).replace(",", "").replace("$", "")
    if not text or text.endswith("%"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def cell_date(cell: dict) -> date | None:
    serial = cell.get("effectiveValue", {}).get("numberValue")
    if serial is not None:
        return date(1899, 12, 30) + timedelta(days=int(float(serial)))
    text = cell_text(cell)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def request_rows(session: google.auth.transport.requests.AuthorizedSession, ranges: list[str]) -> list[list[list[dict]]]:
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}",
        params={
            "ranges": ranges,
            "includeGridData": "true",
            "fields": "sheets(data(rowData(values(formattedValue,effectiveValue))))",
        },
        timeout=30,
    )
    response.raise_for_status()
    sheets = response.json().get("sheets", [])
    grids = [grid for sheet in sheets for grid in sheet.get("data", [])]
    if len(grids) != len(ranges):
        raise RuntimeError(f"Google Sheets returned {len(grids)} ranges, expected {len(ranges)}.")
    return [[row.get("values", []) for row in grid.get("rowData", [])] for grid in grids]


def request_values() -> tuple[list[list[dict]], list[list[dict]]]:
    info = json.loads(required("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"))
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(credentials)
    # The source has five fields. Reading it by header name keeps partner and
    # operation additions data-only changes, not code changes.
    source, targets = request_rows(session, [
        f"{SOURCE_SHEET_NAME}!A1:E10001",
        f"{TARGET_SHEET_NAME}!A1:W30",
    ])
    return source, targets


def long_records(rows: list[list[dict]]) -> list[dict]:
    if not rows:
        raise RuntimeError("合作方新增血量 is empty.")
    header_index = {cell_text(cell): index for index, cell in enumerate(rows[0]) if cell_text(cell)}
    missing = [header for header in REQUIRED_SOURCE_HEADERS if header not in header_index]
    if missing or len(header_index) != len(rows[0]):
        raise RuntimeError(f"合作方新增血量 headers are missing or duplicated: {', '.join(missing)}")
    records = []
    for row in rows[1:]:
        def value(header: str) -> dict:
            index = header_index[header]
            return row[index] if index < len(row) else {}

        day = cell_date(value("日期"))
        partner, operation = cell_text(value("合作方")), cell_text(value("运营位"))
        new_users, revenue = cell_number(value("新增")), cell_number(value("血量"))
        if not day or not partner or not operation or (new_users is None and revenue is None):
            continue
        records.append({"date": day, "partner": partner, "operation": operation, "新增": new_users, "血量": revenue})
    if not records:
        raise RuntimeError("合作方新增血量 has no usable records.")
    return records


def monthly_targets(rows: list[list[dict]], month: int) -> dict[str, float]:
    """Read goal values, not actuals, from the independent target table."""
    headers = None
    for row_index, row in enumerate(rows):
        names = [cell_text(cell) for cell in row]
        if all(name in names for name in ("月份", "当月目标", "我方新增目标")):
            headers = {name: names.index(name) for name in ("月份", "当月目标", "我方新增目标")}
            for candidate in rows[row_index + 1:]:
                month_name = cell_text(candidate[headers["月份"]]) if len(candidate) > headers["月份"] else ""
                if month_name != f"{month}月":
                    continue
                revenue = cell_number(candidate[headers["当月目标"]]) if len(candidate) > headers["当月目标"] else None
                users = cell_number(candidate[headers["我方新增目标"]]) if len(candidate) > headers["我方新增目标"] else None
                if revenue is None or users is None:
                    raise RuntimeError(f"目标完成度 {month_name} target values are missing.")
                return {"血量": revenue, "360新增": users}
    raise RuntimeError("目标完成度 lacks the required target headers or current-month row.")


def series_by_key(records: list[dict], metric: str, predicate=lambda record: True) -> dict[tuple[str, str], dict[int, float]]:
    series: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    for record in records:
        if not predicate(record) or record[metric] is None:
            continue
        key = (record["partner"], record["operation"])
        day = record["date"].day
        if day in series[key]:
            raise RuntimeError(f"duplicate long-table metric: {record['date']} {key} {metric}")
        series[key][day] = record[metric] / RAW_UNIT_DIVISOR
    return dict(series)


def actual_metric_summary(records: list[dict], metric: str, cutoff: date, predicate=lambda record: True) -> dict:
    """Sum only values actually returned by partners for the daily card."""
    matching = [record for record in records if predicate(record) and record[metric] is not None]
    if not matching:
        raise RuntimeError(f"No {metric} data is available for the report month.")
    return {
        "cumulative": sum(record[metric] / RAW_UNIT_DIVISOR for record in matching),
        "partners": sorted({record["partner"] for record in matching}),
        "operations": sorted({record["operation"] for record in matching}),
    }


def partner_daily_series(records: list[dict], metric: str, predicate=lambda record: True) -> dict[str, dict[int, float]]:
    """Aggregate all positions into one daily series for each partner."""
    series: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for record in records:
        if predicate(record) and record[metric] is not None:
            series[record["partner"]][record["date"].day] += record[metric] / RAW_UNIT_DIVISOR
    return {partner: dict(values) for partner, values in series.items()}


def projected_partner_series(series: dict[int, float], cutoff: date) -> tuple[float, float]:
    """Fill after each partner's last actual day with its preceding 14-day mean."""
    last_actual = max(series)
    recent_days = [day for day in sorted(series) if day <= last_actual][-14:]
    average = sum(series[day] for day in recent_days) / len(recent_days)
    actual_total = sum(series.values())
    measured_cumulative = actual_total + average * max(0, cutoff.day - last_actual)
    days_in_month = calendar.monthrange(cutoff.year, cutoff.month)[1]
    projected = actual_total + average * max(0, days_in_month - last_actual)
    return measured_cumulative, projected


def forecast_metric_summary(records: list[dict], metric: str, cutoff: date, predicate=lambda record: True) -> dict:
    series = partner_daily_series(records, metric, predicate)
    if not series:
        raise RuntimeError(f"No {metric} data is available for the report month.")
    cumulative = projected = 0.0
    for values in series.values():
        current, month_total = projected_partner_series(values, cutoff)
        cumulative += current
        projected += month_total
    return {"cumulative": cumulative, "projected": projected}


def progress_line(label: str, summary: dict, target: float, cutoff: date) -> str:
    completion = summary["cumulative"] / target * 100
    time_progress = cutoff.day / calendar.monthrange(cutoff.year, cutoff.month)[1] * 100
    gap = completion - time_progress
    pace = "领先" if gap >= 0 else "落后"
    remaining_days = calendar.monthrange(cutoff.year, cutoff.month)[1] - cutoff.day
    remaining = max(target - summary["cumulative"], 0)
    daily_needed = remaining / remaining_days if remaining_days else 0
    return (
        f"🔴**{label}累计完成 {summary['cumulative']:.2f}　目标 {target:.2f}　完成率 {completion:.1f}%　"
        f"时间进度 {time_progress:.1f}%　·　{pace}时间进度 {abs(gap):.1f}%　剩余目标 {remaining:.2f}　"
        f"后续日均需完成 {daily_needed:.2f}**"
    )


def forecast_line(label: str, summary: dict, target: float, cutoff: date) -> str:
    completion = summary["cumulative"] / target * 100
    projected_rate = summary["projected"] / target * 100
    time_progress = cutoff.day / calendar.monthrange(cutoff.year, cutoff.month)[1] * 100
    gap = completion - time_progress
    pace = "领先" if gap >= 0 else "落后"
    return (
        f"🔴**{label}测算累计完成 {summary['cumulative']:.2f}　目标 {target:.2f}　完成率 {completion:.1f}%　"
        f"时间进度 {time_progress:.1f}%　·　{pace}时间进度 {abs(gap):.1f}%　剩余目标 {max(target - summary['cumulative'], 0):.2f}**\n\n"
        f"🔴**预计本月目标可达成 {summary['projected']:.2f}　完成率 {projected_rate:.1f}%**"
    )


def source_status(records: list[dict], cutoff: date) -> str:
    """One returned position means the partner is not marked incomplete."""
    partners = sorted({record["partner"] for record in records})
    returned = {record["partner"] for record in records if record["date"] == cutoff}
    incomplete = [partner for partner in partners if partner not in returned]
    if not incomplete:
        return "\u2705 \u6570\u636e\u5b8c\u6574"
    return "\u26a0\ufe0f " + "\uff1b".join(f"{partner}\u6570\u636e\u4e0d\u5168" for partner in incomplete)


def report(records: list[dict], targets: dict[str, float], cutoff: date) -> tuple[str, str]:
    monthly = [record for record in records if (record["date"].year, record["date"].month) == (cutoff.year, cutoff.month) and record["date"] <= cutoff]
    if not monthly:
        raise RuntimeError(f"\u5408\u4f5c\u65b9\u65b0\u589e\u8840\u91cf has no records for {cutoff:%Y-%m}.")
    revenue = actual_metric_summary(monthly, "\u8840\u91cf", cutoff)
    users = actual_metric_summary(monthly, "\u65b0\u589e", cutoff, lambda record: record["partner"] == "360")
    forecast_revenue = forecast_metric_summary(monthly, "\u8840\u91cf", cutoff)
    forecast_users = forecast_metric_summary(monthly, "\u65b0\u589e", cutoff, lambda record: record["partner"] == "360")
    status = source_status(monthly, cutoff)
    daily = (
        "\u27a1\ufe0f**\u8840\u91cf\uff08\u4e07\u7f8e\u5143\uff09**\\n\\n"
        f"{progress_line('', revenue, targets['\u8840\u91cf'], cutoff)}\\n\\n"
        "\u27a1\ufe0f**\u65b0\u589e\uff08\u4e07\uff09**\\n\\n"
        f"{progress_line('360 ', users, targets['360\u65b0\u589e'], cutoff)}\\n\\n"
        f"\u27a1\ufe0f**\u6570\u636e\u72b6\u6001\uff1a** {status}\\n\\n[\u67e5\u770b\u5408\u4f5c\u65b9\u65b0\u589e\u8840\u91cf]({SHEET_URL})"
    )
    forecast = (
        "\u27a1\ufe0f**\u8840\u91cf\uff08\u4e07\u7f8e\u5143\uff09**\\n\\n"
        f"{forecast_line('', forecast_revenue, targets['\u8840\u91cf'], cutoff)}\\n\\n"
        "\u27a1\ufe0f**\u65b0\u589e\uff08\u4e07\uff09**\\n\\n"
        f"{forecast_line('360 ', forecast_users, targets['360\u65b0\u589e'], cutoff)}\\n\\n"
        f"[\u67e5\u770b\u5408\u4f5c\u65b9\u65b0\u589e\u8840\u91cf]({SHEET_URL})"
    )
    return daily, forecast


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare independent WPS daily-progress cards from long-format partner data.")
    parser.add_argument("--output", required=True, help="UTF-8 file path for the daily-progress card body")
    parser.add_argument("--forecast-output", required=True, help="UTF-8 file path for the forecast card body")
    parser.add_argument("--end-date", help="Optional report cutoff date in YYYY-MM-DD; defaults to yesterday Beijing time")
    args = parser.parse_args()
    cutoff = parse_day(args.end_date) if args.end_date else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    source_rows, target_rows = request_values()
    daily, forecast = report(long_records(source_rows), monthly_targets(target_rows, cutoff.month), cutoff)
    Path(args.output).write_text(daily, encoding="utf-8")
    Path(args.forecast_output).write_text(forecast, encoding="utf-8")
    print("Independent WPS daily-progress and forecast content prepared.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Daily progress job failed: {exc}", file=sys.stderr)
        raise