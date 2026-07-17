#!/usr/bin/env python3
"""Prepare a WPS partner-progress report from Google Sheets without AI."""

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
SOURCE_SHEET = "合作方返回数据"
TARGET_SHEET = "目标完成度"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=303958504"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
BJ_TZ = ZoneInfo("Asia/Shanghai")
UNIT_DIVISOR = 10_000

# Cell offsets are zero-based and follow 合作方返回数据!A:Q.
PARTNERS = (
    {"name": "360", "new": (1, 2), "revenue": (), "target_name": "360", "target_metric": "new"},
    {"name": "Avast", "new": (3, 5), "revenue": (4, 6), "target_name": "Avast", "target_metric": "revenue"},
    {"name": "Opera", "new": (7, 9), "revenue": (8, 10), "target_name": "Opera", "target_metric": "revenue"},
    {"name": "Yandex", "new": (11, 13), "revenue": (12, 14), "target_name": "Yandex", "target_metric": "revenue"},
    {"name": "Winriser", "new": (15,), "revenue": (16,), "target_name": "Winriser", "target_metric": "revenue"},
)


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def request_rows(session: google.auth.transport.requests.AuthorizedSession, sheet_range: str) -> list[list[dict]]:
    """Use one Sheets API request per range; ranges from one tab may be grouped."""
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}",
        params={
            "ranges": [sheet_range],
            "includeGridData": "true",
            "fields": "sheets(data(rowData(values(formattedValue,effectiveValue))))",
        },
        timeout=30,
    )
    response.raise_for_status()
    grids = [grid for sheet in response.json().get("sheets", []) for grid in sheet.get("data", [])]
    if len(grids) != 1:
        raise RuntimeError(f"Google Sheets returned {len(grids)} ranges, expected 1.")
    return [row.get("values", []) for row in grids[0].get("rowData", [])]


def read_data() -> tuple[list[list[dict]], list[list[dict]]]:
    info = json.loads(required("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"))
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(credentials)
    return (
        request_rows(session, f"{SOURCE_SHEET}!A1:Q1000"),
        request_rows(session, f"{TARGET_SHEET}!A1:Q20"),
    )


def number(cell: dict | None) -> float | None:
    if not cell:
        return None
    value = cell.get("effectiveValue", {}).get("numberValue")
    if value is not None:
        return float(value)
    text = cell.get("formattedValue", "").replace(",", "").strip()
    if not text or text.endswith("%"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def sheet_date(cell: dict | None) -> date | None:
    value = number(cell)
    if value is None:
        return None
    return date(1899, 12, 30) + timedelta(days=int(value))


def cell_text(row: list[dict], index: int) -> str:
    return row[index].get("formattedValue", "").strip() if len(row) > index else ""


def aggregate(row: list[dict], columns: tuple[int, ...]) -> float | None:
    """Return a complete partner daily metric in units of ten thousand.

    A blank component means that partner's daily metric has not returned yet;
    numeric zero is retained as a valid reported value.
    """
    values = [number(row[column]) if len(row) > column else None for column in columns]
    if not values or any(value is None for value in values):
        return None
    return sum(values) / UNIT_DIVISOR


def make_series(rows: list[list[dict]]) -> tuple[dict[str, dict[str, dict[date, float]]], date]:
    series: dict[str, dict[str, dict[date, float]]] = {
        partner["name"]: {"new": {}, "revenue": {}} for partner in PARTNERS
    }
    latest_any: date | None = None
    for row in rows[1:]:
        row_date = sheet_date(row[0] if row else None)
        if not row_date:
            continue
        latest_any = max(latest_any, row_date) if latest_any else row_date
        for partner in PARTNERS:
            new_value = aggregate(row, partner["new"])
            if new_value is not None:
                series[partner["name"]]["new"][row_date] = new_value
            if partner["revenue"]:
                revenue_value = aggregate(row, partner["revenue"])
                if revenue_value is not None:
                    series[partner["name"]]["revenue"][row_date] = revenue_value
    if latest_any is None:
        raise RuntimeError("合作方返回数据 does not contain valid dates.")
    return series, latest_any


def month_targets(rows: list[list[dict]], report_month: int) -> dict[str, float]:
    if len(rows) < 3:
        raise RuntimeError("目标完成度 does not contain a header and monthly targets.")
    headers = {cell_text(rows[1], index): index for index in range(len(rows[1])) if cell_text(rows[1], index)}
    targets: dict[str, float] = {}
    for row in rows[2:]:
        if cell_text(row, 0) != f"{report_month}月":
            continue
        for partner in PARTNERS:
            column = headers.get(partner["target_name"])
            value = number(row[column]) if column is not None and len(row) > column else None
            if value is not None:
                targets[partner["name"]] = value
        return targets
    raise RuntimeError(f"目标完成度 does not contain a {report_month}月 target row.")


def latest_metric_date(series: dict[date, float], report_month: date) -> date | None:
    candidates = [day for day in series if (day.year, day.month) == (report_month.year, report_month.month)]
    return max(candidates) if candidates else None


def average(series: dict[date, float], end: date, days: int) -> float | None:
    values = [series[day] for offset in range(days) if (day := end - timedelta(days=offset)) in series]
    return sum(values) / len(values) if values else None


def percent_change(current: float | None, previous: float | None) -> str:
    if current is None or previous is None or previous == 0:
        return "--"
    return f"{(current / previous - 1) * 100:+.1f}%"


def metric_line(label: str, series: dict[date, float], latest: date) -> str:
    current = series[latest]
    previous_day = series.get(latest - timedelta(days=7))
    seven = average(series, latest, 7)
    seven_previous = average(series, latest - timedelta(days=7), 7)
    twenty_eight = average(series, latest, 28)
    twenty_eight_previous = average(series, latest - timedelta(days=28), 28)
    return (
        f"🔴**{label}：{current:.2f}万，环比 {percent_change(current, previous_day)}；"
        f"7日均 {seven:.2f}万，环比 {percent_change(seven, seven_previous)}；"
        f"28日均 {twenty_eight:.2f}万，环比 {percent_change(twenty_eight, twenty_eight_previous)}**"
    )


def forecast_line(label: str, series: dict[date, float], latest: date, target: float | None) -> str:
    if target is None or target <= 0:
        return f"🔴**{label}：未配置当月目标**"
    month_start = latest.replace(day=1)
    completed = sum(value for day, value in series.items() if month_start <= day <= latest)
    daily_average = average(series, latest, 14)
    days_in_month = calendar.monthrange(latest.year, latest.month)[1]
    projected = completed + (daily_average or 0) * (days_in_month - latest.day)
    if completed >= target:
        reach = f"已于 {latest.month}.{latest.day} 达成目标"
    elif not daily_average or projected < target:
        reach = "预计本月无法达成目标"
    else:
        days_needed = int(__import__("math").ceil((target - completed) / daily_average))
        reach_day = latest + timedelta(days=days_needed)
        reach = f"预计 {reach_day.month}.{reach_day.day} 达成目标"
    unit = "万" if label == "新增目标预测" else "万美元"
    return (
        f"🔴**{label}：当月目标 {target:.2f}{unit}；截至当日，完成 {completed:.2f}{unit}；"
        f"预计本月可完成 {projected:.2f}{unit}；{reach}**"
    )


def report_text(source_rows: list[list[dict]], target_rows: list[list[dict]]) -> str:
    series, latest_source_date = make_series(source_rows)
    targets = month_targets(target_rows, latest_source_date.month)
    blocks: list[str] = []
    for partner in PARTNERS:
        name = partner["name"]
        metric_name = partner["target_metric"]
        latest = latest_metric_date(series[name][metric_name], latest_source_date)
        if latest is None:
            continue
        lines = [f"➡️**{name}：{latest.month}.{latest.day}**", metric_line("新增", series[name]["new"], latest)]
        if partner["revenue"]:
            lines.append(metric_line("血量", series[name]["revenue"], latest))
            lines.append(forecast_line("血量目标预测", series[name]["revenue"], latest, targets.get(name)))
        else:
            lines.append(forecast_line("新增目标预测", series[name]["new"], latest, targets.get(name)))
        blocks.append("\n\n".join(lines))
    if not blocks:
        raise RuntimeError("No partner metrics were available for the latest reporting month.")
    return "\n\n".join(blocks) + f"\n\n[查看合作方返回数据]({SHEET_URL})"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a WPS partner-progress report.")
    parser.add_argument("--output", required=True, help="UTF-8 output file for the WPS card body")
    args = parser.parse_args()
    source_rows, target_rows = read_data()
    Path(args.output).write_text(report_text(source_rows, target_rows), encoding="utf-8")
    print("WPS partner progress content prepared.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Partner progress job failed: {exc}", file=sys.stderr)
        raise
