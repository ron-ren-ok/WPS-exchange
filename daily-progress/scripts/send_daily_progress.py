#!/usr/bin/env python3
"""Send the current daily-progress values from Google Sheets to a WPS group bot.

This job only reads the spreadsheet's existing formula results.  It does not
call an AI service or alter the spreadsheet.
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import google.auth.transport.requests
from google.oauth2 import service_account


SPREADSHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "日进度追踪"
SHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    f"{SPREADSHEET_ID}/edit#gid=1377957533"
)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
BJ_TZ = ZoneInfo("Asia/Shanghai")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def row_value(rows: list[list[dict]], row: int, column: int) -> str:
    try:
        return rows[row][column].get("formattedValue", "").strip()
    except IndexError as exc:
        raise RuntimeError("The 日进度追踪 summary range is incomplete.") from exc


def last_status(rows: list[list[dict]], date_column: int, status_column: int) -> str:
    for row in reversed(rows):
        if len(row) > status_column:
            date = row[date_column].get("formattedValue", "").strip()
            status = row[status_column].get("formattedValue", "").strip()
            if date and status:
                return status
    return "状态待核对"


def display_status(status: str) -> str:
    if status == "完整":
        return "✅ 数据完整"
    return f"⚠️ {status}" if status else "⚠️ 状态待核对"


def request_values() -> list[list[list[dict]]]:
    info = json.loads(required("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"))
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(credentials)
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}",
        params={
            "ranges": [f"{SHEET_NAME}!A1:P6", f"{SHEET_NAME}!A10:I40", f"{SHEET_NAME}!J10:R40"],
            "includeGridData": "true",
            "fields": "sheets(data(rowData(values(formattedValue,effectiveValue))))",
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json().get("sheets", [{}])[0].get("data", [])
    if len(data) != 3:
        raise RuntimeError("Google Sheets returned an unexpected number of ranges.")
    return [[row.get("values", []) for row in grid.get("rowData", [])] for grid in data]

def card_text(summary: list[list[dict]], revenue_rows: list[list[dict]], users_rows: list[list[dict]]) -> str:
    # 状态表按天落数，日报发送时只使用其中最新的一条有效状态（通常为昨天）。
    revenue_status = last_status(revenue_rows, 0, 8)
    users_status = last_status(users_rows, 0, 8)

    def metric(row: int, label: str = "") -> str:
        prefix = f"{label} " if label else ""
        return (
            f"🔴**{prefix}累计完成 {row_value(summary, row, 3)}　目标 {row_value(summary, row, 1)}　"
            f"完成率 {row_value(summary, row, 5)}　时间进度 {row_value(summary, row, 13)}　·　"
            f"{row_value(summary, row, 15)}　剩余目标 {row_value(summary, row, 7)}　·　"
            f"后续日均需完成 {row_value(summary, row, 11)}**"
        )

    incomplete = []
    for status in (revenue_status, users_status):
        detail = status.removeprefix("数据不全：").strip()
        if detail and detail not in ("完整", "状态待核对") and detail not in incomplete:
            incomplete.append(detail)
    incomplete_note = f"\n\n➡️**部分数据不全：** {"；".join(incomplete)}" if incomplete else ""

    return (
        "➡️**血量（万美元）**\n\n"
        f"{metric(3)}\n\n"
        "➡️**新增（万）**\n\n"
        f"{metric(5, '360')}"
        f"{incomplete_note}\n\n[查看日进度追踪]({SHEET_URL})"
    )
def cell_number(cell: dict) -> float | None:
    effective = cell.get("effectiveValue", {})
    if effective.get("numberValue") is not None:
        return float(effective["numberValue"])
    text = cell.get("formattedValue", "").strip().replace(",", "").replace("$", "")
    if not text or text.endswith("%"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def formatted_percent(cell: dict) -> float:
    text = cell.get("formattedValue", "").strip()
    if text.endswith("%"):
        return float(text[:-1].replace(",", ""))
    value = cell_number(cell)
    if value is None:
        raise RuntimeError("The 日进度追踪 time-progress cell is not numeric.")
    return value * 100 if 0 <= value <= 1 else value


def sheet_date(cell: dict, fallback_year: int) -> date | None:
    serial = cell.get("effectiveValue", {}).get("numberValue")
    if serial is not None:
        return date(1899, 12, 30) + timedelta(days=int(float(serial)))
    parts = [int(part) for part in re.findall(r"\d+", cell.get("formattedValue", ""))]
    if len(parts) >= 3:
        if parts[0] >= 1900:
            return date(parts[0], parts[1], parts[2])
        if parts[2] >= 1900:
            return date(parts[2], parts[0], parts[1])
    if len(parts) == 2:
        return date(fallback_year, parts[0], parts[1])
    return None


def daily_series(rows: list[list[dict]], report_date: date) -> list[dict[int, float]]:
    columns = [dict() for _ in range(7)]
    for row in rows[1:]:
        if not row:
            continue
        row_date = sheet_date(row[0], report_date.year)
        if not row_date or (row_date.year, row_date.month) != (report_date.year, report_date.month):
            continue
        for output_index, column in enumerate(range(1, 8)):
            if len(row) > column:
                value = cell_number(row[column])
                if value is not None:
                    columns[output_index][row_date.day] = value
    return columns


def column_projection(series: dict[int, float], report_date: date) -> tuple[float, float]:
    """Use the latest at-most-14 actual values for each partner column."""
    days_in_month = calendar.monthrange(report_date.year, report_date.month)[1]
    completed_days = max(report_date.day - 1, 0)
    observed = [series[day] for day in range(1, completed_days + 1) if day in series]
    if not observed:
        return 0.0, 0.0
    average = sum(observed[-14:]) / min(len(observed), 14)
    cumulative = sum(series.get(day, average) for day in range(1, completed_days + 1))
    month_total = cumulative + average * (days_in_month - completed_days)
    return cumulative, month_total


def forecast_metric(summary: list[list[dict]], row: int, rows: list[list[dict]], report_date: date, label: str = "") -> str:
    target = cell_number(summary[row][1])
    if target is None or target <= 0:
        raise RuntimeError("The 日进度追踪 target cell is not a positive number.")
    cumulative = 0.0
    month_total = 0.0
    for series in daily_series(rows, report_date):
        current, projected = column_projection(series, report_date)
        cumulative += current
        month_total += projected
    completion_rate = cumulative / target * 100
    projected_rate = month_total / target * 100
    time_progress = formatted_percent(summary[row][13])
    gap = completion_rate - time_progress
    pace = "领先" if gap >= 0 else "落后"
    prefix = f"{label} " if label else ""
    return (
        f"🔴**{prefix}累计完成 {cumulative:.2f}　目标 {target:.2f}　完成率 {completion_rate:.1f}%　"
        f"时间进度 {time_progress:.1f}%　·　{pace}时间进度 {abs(gap):.1f}%　剩余目标 {target - cumulative:.2f}**\n"
        f"🔴**预计本月目标可达成 {month_total:.2f}　完成率 {projected_rate:.1f}%**"
    )


def forecast_text(summary: list[list[dict]], revenue_rows: list[list[dict]], users_rows: list[list[dict]], report_date: date) -> str:
    return (
        "➡️**血量（万美元）**\n\n"
        f"{forecast_metric(summary, 3, revenue_rows, report_date)}\n\n"
        "➡️**新增（万）**\n\n"
        f"{forecast_metric(summary, 5, users_rows, report_date, '360')}\n\n"
        f"[查看日进度追踪]({SHEET_URL})"
    )

def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare WPS daily-progress and forecast card content.")
    parser.add_argument("--output", required=True, help="UTF-8 file path for the daily-progress card body")
    parser.add_argument("--forecast-output", required=True, help="UTF-8 file path for the forecast card body")
    args = parser.parse_args()

    summary, revenue_rows, users_rows = request_values()
    report_date = datetime.now(BJ_TZ).date()
    Path(args.output).write_text(card_text(summary, revenue_rows, users_rows), encoding="utf-8")
    Path(args.forecast_output).write_text(forecast_text(summary, revenue_rows, users_rows, report_date), encoding="utf-8")
    # Do not print card bodies or any secret into Actions logs.
    print("WPS daily-progress and forecast content prepared.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Daily progress job failed: {exc}", file=sys.stderr)
        raise
