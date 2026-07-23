#!/usr/bin/env python3
"""Prepare WPS daily-progress cards from Google Sheets without AI."""

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
SOURCE_SHEET_NAME = "合作方返回数据"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=1377957533"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
BJ_TZ = ZoneInfo("Asia/Shanghai")
UNIT_DIVISOR = 10_000


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


def cell_number(cell: dict | None) -> float | None:
    if not cell:
        return None
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


def cell_date(cell: dict | None) -> date | None:
    serial = cell.get("effectiveValue", {}).get("numberValue") if cell else None
    return date(1899, 12, 30) + timedelta(days=int(float(serial))) if serial is not None else None


def formatted_percent(cell: dict) -> float:
    text = cell.get("formattedValue", "").strip()
    if text.endswith("%"):
        return float(text[:-1].replace(",", ""))
    value = cell_number(cell)
    if value is None:
        raise RuntimeError("The 日进度追踪 time-progress cell is not numeric.")
    return value * 100 if 0 <= value <= 1 else value


def request_rows(session: google.auth.transport.requests.AuthorizedSession, sheet_range: str) -> list[list[dict]]:
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


def request_values() -> list[list[list[dict]]]:
    info = json.loads(required("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"))
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(credentials)
    return [
        request_rows(session, f"{SHEET_NAME}!A1:P6"),
        request_rows(session, f"{SHEET_NAME}!A10:I40"),
        request_rows(session, f"{SHEET_NAME}!J10:R40"),
        request_rows(session, f"{SOURCE_SHEET_NAME}!A1:ZZ1000"),
    ]


def partner_name(header: str) -> str | None:
    match = re.match(r"^(.+?)(?:换量弹窗|气泡)(?:新增|血量)$", header)
    if match:
        return match.group(1)
    match = re.match(r"^(.+?)(?:新增|血量)$", header)
    return match.group(1) if match else None


def partner_columns(source_rows: list[list[dict]]) -> dict[str, dict[str, tuple[int, ...]]]:
    if not source_rows:
        raise RuntimeError("合作方返回数据 is empty.")
    grouped: dict[str, dict[str, list[int]]] = {}
    for index, cell in enumerate(source_rows[0][1:], start=1):
        header = cell.get("formattedValue", "").strip()
        name = partner_name(header)
        if not name:
            continue
        metric = "revenue" if header.endswith("血量") else "new"
        grouped.setdefault(name, {"new": [], "revenue": []})[metric].append(index)
    return {name: {metric: tuple(columns) for metric, columns in values.items()} for name, values in grouped.items()}


def source_row_for_day(source_rows: list[list[dict]], wanted: date) -> list[dict] | None:
    for row in source_rows[1:]:
        if row and cell_date(row[0]) == wanted:
            return row
    return None


def incomplete_partners(source_rows: list[list[dict]], cutoff: date) -> list[str]:
    """A partner is complete when at least one of its positions returned yesterday."""
    row = source_row_for_day(source_rows, cutoff)
    incomplete: list[str] = []
    for name, metrics in partner_columns(source_rows).items():
        columns = metrics["new"] + metrics["revenue"]
        if not row or not any(cell_number(row[column]) is not None for column in columns if len(row) > column):
            incomplete.append(name)
    return incomplete


def card_text(summary: list[list[dict]], source_rows: list[list[dict]], report_date: date) -> str:
    def metric(row: int, label: str = "") -> str:
        prefix = f"{label} " if label else ""
        return (
            f"🔴**{prefix}累计完成 {row_value(summary, row, 3)}　目标 {row_value(summary, row, 1)}　"
            f"完成率 {row_value(summary, row, 5)}　时间进度 {row_value(summary, row, 13)}　·　"
            f"{row_value(summary, row, 15)}　剩余目标 {row_value(summary, row, 7)}　·　"
            f"后续日均需完成 {row_value(summary, row, 11)}**"
        )

    incomplete = incomplete_partners(source_rows, report_date - timedelta(days=1))
    status = "✅ 数据完整" if not incomplete else f"⚠️ {'；'.join(f'{name}数据不全' for name in incomplete)}"
    return (
        "➡️**血量（万美元）**\n\n"
        f"{metric(3)}\n\n"
        "➡️**新增（万）**\n\n"
        f"{metric(5, '360')}\n\n"
        f"➡️**数据状态：** {status}\n\n[查看日进度追踪]({SHEET_URL})"
    )


def partner_series(source_rows: list[list[dict]], columns: tuple[int, ...], report_date: date) -> dict[date, float]:
    """Daily partner total. Any reported position is counted; blank positions are not zero-filled."""
    values: dict[date, float] = {}
    for row in source_rows[1:]:
        row_date = cell_date(row[0] if row else None)
        if not row_date or (row_date.year, row_date.month) != (report_date.year, report_date.month) or row_date >= report_date:
            continue
        daily = [cell_number(row[column]) for column in columns if len(row) > column and cell_number(row[column]) is not None]
        if daily:
            values[row_date] = sum(daily) / UNIT_DIVISOR
    return values


def project_partner(series: dict[date, float], report_date: date) -> tuple[float, float]:
    """Fill from a partner's last returned date through yesterday and month-end."""
    if not series:
        return 0.0, 0.0
    last_actual = max(series)
    recent = [value for day, value in sorted(series.items()) if day <= last_actual][-14:]
    average = sum(recent) / len(recent)
    actual_total = sum(value for day, value in series.items() if day <= last_actual)
    measured_through_yesterday = actual_total + average * max(0, (report_date - timedelta(days=1) - last_actual).days)
    days_in_month = calendar.monthrange(report_date.year, report_date.month)[1]
    month_total = actual_total + average * max(0, days_in_month - last_actual.day)
    return measured_through_yesterday, month_total


def forecast_metric(summary: list[list[dict]], summary_row: int, source_rows: list[list[dict]], report_date: date, metric: str, label: str = "") -> str:
    target = cell_number(summary[summary_row][1])
    if target is None or target <= 0:
        raise RuntimeError("The 日进度追踪 target cell is not a positive number.")
    cumulative = 0.0
    month_total = 0.0
    for partner, columns in partner_columns(source_rows).items():
        if metric == "new" and partner != "360":
            continue
        series = partner_series(source_rows, columns[metric], report_date)
        current, projected = project_partner(series, report_date)
        cumulative += current
        month_total += projected
    completion_rate = cumulative / target * 100
    projected_rate = month_total / target * 100
    completed_days = max(report_date.day - 1, 0)
    days_in_month = calendar.monthrange(report_date.year, report_date.month)[1]
    time_progress = completed_days / days_in_month * 100
    gap = completion_rate - time_progress
    pace = "领先" if gap >= 0 else "落后"
    prefix = f"{label} " if label else ""
    return (
        f"🔴**{prefix}测算累计完成 {cumulative:.2f}　目标 {target:.2f}　完成率 {completion_rate:.1f}%　"
        f"时间进度 {time_progress:.1f}%　·　{pace}时间进度 {abs(gap):.1f}%　剩余目标 {max(target - cumulative, 0):.2f}**\n\n"
        f"🔴**预计本月目标可达成 {month_total:.2f}　完成率 {projected_rate:.1f}%**"
    )


def forecast_text(summary: list[list[dict]], source_rows: list[list[dict]], report_date: date) -> str:
    return (
        "➡️**血量（万美元）**\n\n"
        f"{forecast_metric(summary, 3, source_rows, report_date, 'revenue')}\n\n"
        "➡️**新增（万）**\n\n"
        f"{forecast_metric(summary, 5, source_rows, report_date, 'new', '360')}\n\n"
        f"[查看日进度追踪]({SHEET_URL})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare WPS daily-progress and forecast card content.")
    parser.add_argument("--output", required=True, help="UTF-8 file path for the daily-progress card body")
    parser.add_argument("--forecast-output", required=True, help="UTF-8 file path for the forecast card body")
    args = parser.parse_args()
    summary, _revenue_rows, _users_rows, source_rows = request_values()
    report_date = datetime.now(BJ_TZ).date()
    Path(args.output).write_text(card_text(summary, source_rows, report_date), encoding="utf-8")
    Path(args.forecast_output).write_text(forecast_text(summary, source_rows, report_date), encoding="utf-8")
    print("WPS daily-progress and forecast content prepared.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Daily progress job failed: {exc}", file=sys.stderr)
        raise
