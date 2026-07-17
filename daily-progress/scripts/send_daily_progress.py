#!/usr/bin/env python3
"""Send the current daily-progress values from Google Sheets to a WPS group bot.

This job only reads the spreadsheet's existing formula results.  It does not
call an AI service or alter the spreadsheet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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

def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare WPS daily-progress card content.")
    parser.add_argument("--output", required=True, help="UTF-8 file path for the WPS card body")
    args = parser.parse_args()

    summary, revenue_rows, users_rows = request_values()
    text = card_text(summary, revenue_rows, users_rows)
    Path(args.output).write_text(text, encoding="utf-8")
    # Do not print the card body or any secret into Actions logs.
    print("WPS daily-progress content prepared.")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Daily progress job failed: {exc}", file=sys.stderr)
        raise
