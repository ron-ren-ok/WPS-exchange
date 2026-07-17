#!/usr/bin/env python3
"""Prepare a dynamic WPS partner-progress report from Google Sheets without AI."""

from __future__ import annotations

import argparse
import calendar
import json
import math
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import google.auth.transport.requests
from google.oauth2 import service_account


SPREADSHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SOURCE_SHEET = "合作方返回数据"
TARGET_SHEET = "目标完成度"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=303958504"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
UNIT_DIVISOR = 10_000
TARGET_BLOCKS = {"合作方预算目标": "revenue", "合作方新增目标": "new"}


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def request_rows(session: google.auth.transport.requests.AuthorizedSession, sheet_range: str) -> list[list[dict]]:
    """Use one API request per range; Google may group ranges from one tab."""
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
    # ZZ keeps the column discovery open for newly-added partners.
    return (
        request_rows(session, f"{SOURCE_SHEET}!A1:ZZ1000"),
        request_rows(session, f"{TARGET_SHEET}!A1:ZZ20"),
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
    return date(1899, 12, 30) + timedelta(days=int(value)) if value is not None else None


def cell_text(row: list[dict], index: int) -> str:
    return row[index].get("formattedValue", "").strip() if len(row) > index else ""


def latest_actual_date(source_rows: list[list[dict]]) -> date:
    """Ignore future dated rows whose partner cells are entirely blank."""
    latest: date | None = None
    for row in source_rows[1:]:
        row_date = sheet_date(row[0] if row else None)
        if row_date and any(number(cell) is not None for cell in row[1:]):
            latest = max(latest, row_date) if latest else row_date
    if latest is None:
        raise RuntimeError("合作方返回数据 does not contain actual partner metrics.")
    return latest


def target_config(target_rows: list[list[dict]], report_month: int) -> dict[str, dict]:
    """Discover partner names and monthly targets from the two target blocks."""
    if len(target_rows) < 3:
        raise RuntimeError("目标完成度 does not contain target blocks and monthly rows.")
    block_row, header_row = target_rows[0], target_rows[1]
    month_row = next((row for row in target_rows[2:] if cell_text(row, 0) == f"{report_month}月"), None)
    if month_row is None:
        raise RuntimeError(f"目标完成度 does not contain a {report_month}月 target row.")

    starts = [(index, TARGET_BLOCKS[cell_text(block_row, index)]) for index in range(len(block_row)) if cell_text(block_row, index) in TARGET_BLOCKS]
    if not starts:
        raise RuntimeError("目标完成度 does not contain 合作方预算目标 or 合作方新增目标 blocks.")

    configs: dict[str, dict] = {}
    for block_index, (start, metric) in enumerate(starts):
        end = starts[block_index + 1][0] if block_index + 1 < len(starts) else len(header_row)
        for column in range(start, end):
            name = cell_text(header_row, column)
            target = number(month_row[column]) if len(month_row) > column else None
            if not name or target is None:
                continue
            # Budget target takes precedence if a name accidentally appears in both blocks.
            if name not in configs or metric == "revenue":
                configs[name] = {"name": name, "target_metric": metric, "target": target}
    return configs


def source_columns(source_header: list[dict], partner_name: str) -> dict[str, tuple[int, ...]]:
    """Group all columns such as Avast换量弹窗新增 / Avast气泡血量 by suffix."""
    columns: dict[str, list[int]] = {"new": [], "revenue": []}
    prefix = partner_name.casefold()
    for index in range(1, len(source_header)):
        header = cell_text(source_header, index)
        if not header.casefold().startswith(prefix):
            continue
        if header.endswith("新增"):
            columns["new"].append(index)
        elif header.endswith("血量"):
            columns["revenue"].append(index)
    return {metric: tuple(indices) for metric, indices in columns.items()}


def build_partners(source_rows: list[list[dict]], target_rows: list[list[dict]], report_month: int) -> list[dict]:
    targets = target_config(target_rows, report_month)
    if not source_rows:
        raise RuntimeError("合作方返回数据 is empty.")
    partners: list[dict] = []
    for config in targets.values():
        columns = source_columns(source_rows[0], config["name"])
        if columns[config["target_metric"]]:
            partners.append({**config, **columns})
    return partners


def aggregate(row: list[dict], columns: tuple[int, ...]) -> float | None:
    """A blank component is not treated as zero; numeric zero remains valid."""
    values = [number(row[column]) if len(row) > column else None for column in columns]
    if not values or any(value is None for value in values):
        return None
    return sum(values) / UNIT_DIVISOR


def make_series(source_rows: list[list[dict]], partners: list[dict]) -> dict[str, dict[str, dict[date, float]]]:
    series = {partner["name"]: {"new": {}, "revenue": {}} for partner in partners}
    for row in source_rows[1:]:
        row_date = sheet_date(row[0] if row else None)
        if not row_date:
            continue
        for partner in partners:
            for metric in ("new", "revenue"):
                value = aggregate(row, partner[metric])
                if value is not None:
                    series[partner["name"]][metric][row_date] = value
    return series


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


def forecast_line(label: str, series: dict[date, float], latest: date, target: float) -> str:
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
        reach_day = latest + timedelta(days=math.ceil((target - completed) / daily_average))
        reach = f"预计 {reach_day.month}.{reach_day.day} 达成目标"
    unit = "万" if label == "新增目标预测" else "万美元"
    return (
        f"🔴**{label}：当月目标 {target:.2f}{unit}；截至当日，完成 {completed:.2f}{unit}；"
        f"预计本月可完成 {projected:.2f}{unit}；{reach}**"
    )


def report_text(source_rows: list[list[dict]], target_rows: list[list[dict]]) -> str:
    report_date = latest_actual_date(source_rows)
    partners = build_partners(source_rows, target_rows, report_date.month)
    series = make_series(source_rows, partners)
    blocks: list[str] = []
    for partner in partners:
        name, metric = partner["name"], partner["target_metric"]
        latest = latest_metric_date(series[name][metric], report_date)
        if latest is None:
            continue
        lines = [f"➡️**{name}：{latest.month}.{latest.day}**"]
        if latest in series[name]["new"]:
            lines.append(metric_line("新增", series[name]["new"], latest))
        elif metric == "revenue":
            lines.append("🔴**新增：当日未回传**")
        if metric == "revenue":
            lines.append(metric_line("血量", series[name]["revenue"], latest))
            lines.append(forecast_line("血量目标预测", series[name]["revenue"], latest, partner["target"]))
        else:
            lines.append(forecast_line("新增目标预测", series[name]["new"], latest, partner["target"]))
        blocks.append("\n\n".join(lines))
    if not blocks:
        raise RuntimeError("No configured partner metrics were available for the latest reporting month.")
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
