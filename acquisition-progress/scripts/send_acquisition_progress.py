#!/usr/bin/env python3
"""Build the daily acquisition, partner, and affiliate progress card from Google Sheets."""

from __future__ import annotations

import argparse
import calendar
import json
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path


SPREADSHEET_ID = "1ICqHtXnUkg2HFskJYY3e8TtLPVNzlIWK2lUzcNM_lnc"
SOURCE_SHEET = "新增&月活提取"
TARGET_SHEET = "目标"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
UNIT_DIVISOR = 10_000
REQUIRED_SOURCE_HEADERS = ("日期", "渠道", "新增设备数", "近30日活跃设备数_MAD")
CHANNELS = (
    {"label": "三方", "source": "三方换量", "target": "三方合作", "new_target": "第三方"},
    {"label": "导量", "source": "安卓导PC", "target": "导量&裂变", "new_target": "导量裂变"},
    {"label": "Affiliate", "source": "Affiliate", "target": "AFF联盟", "new_target": "AFF联盟"},
)


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def request_rows(session, sheet_range: str) -> list[list[dict]]:
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}",
        params={"ranges": [sheet_range], "includeGridData": "true", "fields": "sheets(data(rowData(values(formattedValue,effectiveValue))))"},
        timeout=30,
    )
    response.raise_for_status()
    grids = [grid for sheet in response.json().get("sheets", []) for grid in sheet.get("data", [])]
    if len(grids) != 1:
        raise RuntimeError(f"Google Sheets returned {len(grids)} ranges, expected 1.")
    return [row.get("values", []) for row in grids[0].get("rowData", [])]


def read_data() -> tuple[list[list[dict]], list[list[dict]]]:
    import google.auth.transport.requests
    from google.oauth2 import service_account

    info = json.loads(required("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"))
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(credentials)
    return request_rows(session, f"{SOURCE_SHEET}!A:D"), request_rows(session, f"{TARGET_SHEET}!A1:J40")


def cell_text(row: list[dict], index: int) -> str:
    return row[index].get("formattedValue", "").strip() if len(row) > index else ""


def number(cell: dict | None) -> float | None:
    if not cell:
        return None
    value = cell.get("effectiveValue", {}).get("numberValue")
    if value is not None:
        return float(value)
    try:
        return float(cell.get("formattedValue", "").replace(",", "").strip())
    except ValueError:
        return None


def sheet_date(cell: dict | None) -> date | None:
    value = number(cell)
    return date(1899, 12, 30) + timedelta(days=int(value)) if value is not None else None


def source_records(rows: list[list[dict]]) -> list[dict]:
    if not rows:
        raise RuntimeError(f"{SOURCE_SHEET} is empty.")
    headers = {cell_text(rows[0], index): index for index in range(len(rows[0])) if cell_text(rows[0], index)}
    missing = [header for header in REQUIRED_SOURCE_HEADERS if header not in headers]
    if missing:
        raise RuntimeError(f"{SOURCE_SHEET} is missing headers: {', '.join(missing)}")
    records = []
    for row in rows[1:]:
        row_date = sheet_date(row[headers["日期"]] if len(row) > headers["日期"] else None)
        channel = cell_text(row, headers["渠道"])
        new = number(row[headers["新增设备数"]] if len(row) > headers["新增设备数"] else None)
        mau = number(row[headers["近30日活跃设备数_MAD"]] if len(row) > headers["近30日活跃设备数_MAD"] else None)
        if row_date and channel and (new is not None or mau is not None):
            records.append({"date": row_date, "channel": channel, "new": new, "mau": mau})
    if not records:
        raise RuntimeError(f"{SOURCE_SHEET} does not contain usable records.")
    return records


def target_config(rows: list[list[dict]], month: int) -> dict[str, dict[str, float]]:
    sections = {"MAU": {}, "新增": {}}
    section = None
    for index, row in enumerate(rows):
        first = cell_text(row, 0)
        if first in sections:
            section = first
            continue
        if section and first == "Month" and index + 1 < len(rows):
            groups, target_headers = rows[index - 1], row
            target_row = next((candidate for candidate in rows[index + 1:] if cell_text(candidate, 0) == f"{month}月"), None)
            if target_row is None:
                raise RuntimeError(f"{TARGET_SHEET} does not contain a {month}月 {section} target row.")
            for column in range(1, len(target_headers)):
                if cell_text(target_headers, column) != "2026-目标":
                    continue
                group = cell_text(groups, column - 1) or cell_text(groups, column)
                value = number(target_row[column] if len(target_row) > column else None)
                if group and value is not None:
                    sections[section][group] = value
            section = None
    if not sections["MAU"] or not sections["新增"]:
        raise RuntimeError(f"{TARGET_SHEET} target sections could not be parsed.")
    return sections


def weekly_sparkline(series: dict[date, float], latest: date) -> tuple[str, str]:
    buckets = []
    for bucket in range(11, -1, -1):
        end = latest - timedelta(days=bucket * 7)
        values = [series.get(end - timedelta(days=offset), 0) for offset in range(7)]
        buckets.append(sum(values) / 7)
    low, high = min(buckets), max(buckets)
    glyphs = "▁▂▃▄▅▆▇█"
    sparkline = "".join(glyphs[0] if high == low else glyphs[round((value - low) / (high - low) * 7)] for value in buckets)
    if buckets[0] == 0:
        trend = "— 数据不足"
    else:
        change = (buckets[-1] / buckets[0] - 1) * 100
        trend = f"{'↑上涨' if change > 0 else '↓下跌' if change < 0 else '→持平'} {change:+.1f}%"
    return sparkline, trend


def report_text(source_rows: list[list[dict]], target_rows: list[list[dict]]) -> str:
    records = source_records(source_rows)
    latest = max(record["date"] for record in records)
    targets = target_config(target_rows, latest.month)
    month_start = latest.replace(day=1)
    days_in_month = calendar.monthrange(latest.year, latest.month)[1]
    blocks = [f"数据截至 **{latest:%Y-%m-%d}**；单位：新增 / MAD 均为万"]
    for config in CHANNELS:
        matched = [record for record in records if record["channel"] == config["source"]]
        daily_new: dict[date, float] = defaultdict(float)
        daily_mau: dict[date, float] = {}
        for record in matched:
            if record["new"] is not None:
                daily_new[record["date"]] += record["new"] / UNIT_DIVISOR
            if record["mau"] is not None:
                daily_mau[record["date"]] = record["mau"] / UNIT_DIVISOR
        if latest not in daily_new or latest not in daily_mau:
            raise RuntimeError(f"{config['label']} is missing data for {latest:%Y-%m-%d}.")
        new_target = targets["新增"].get(config["new_target"])
        mau_target = targets["MAU"].get(config["target"])
        if new_target is None or mau_target is None:
            raise RuntimeError(f"{config['label']} target mapping is missing for {latest.month}月.")
        month_actual = sum(value for day, value in daily_new.items() if month_start <= day <= latest)
        daily_actual = month_actual / latest.day
        daily_target = new_target / days_in_month
        sparkline, _ = weekly_sparkline(daily_new, latest)
        blocks.append(
            f"**👉🏻{config['label']}**\n"
            f"▪️昨日新增 **{daily_new[latest]:.2f}万**　|　本月日均 **{daily_actual:.2f}万 / {daily_target:.2f}万** 本月新增 **{month_actual:.2f}万 / {new_target:.2f}万**（{month_actual / new_target:.1%}）\n"
            f"▪️近30天 MAD **{daily_mau[latest]:.2f}万 / {mau_target:.2f}万**（{daily_mau[latest] / mau_target:.1%}）\n"
            f"▪️近12周新增日均 `{sparkline}`"
        )
    return "\n\n".join(blocks) + f"\n\n[查看三方&导量运营数据]({SHEET_URL})"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare acquisition progress report.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source_rows, target_rows = read_data()
    Path(args.output).write_text(report_text(source_rows, target_rows), encoding="utf-8")
    print("Acquisition progress content prepared.")


if __name__ == "__main__":
    main()
