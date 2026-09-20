#!/usr/bin/env python3
"""Build the daily user-growth KPI card from Google Sheets."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import requests


SPREADSHEET_ID = "1ICqHtXnUkg2HFskJYY3e8TtLPVNzlIWK2lUzcNM_lnc"
SOURCE_SHEET = "新增&月活提取"
TARGET_SHEET = "目标"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
UNIT_DIVISOR = 10_000
REQUIRED_SOURCE_HEADERS = ("日期", "渠道", "新增设备数", "近30日活跃设备数_MAD")
# Kept only for target-name compatibility while the sheet transitions from the
# original three groups to the full growth-channel matrix.  Every source channel
# is reported; this map is not a list of channels to include.
TARGET_ALIASES = {
    "三方换量": ("三方合作", "第三方"),
    "三方合作": ("第三方",),
    "安卓导PC": ("导量&裂变", "导量裂变"),
    "Affiliate": ("AFF联盟",),
    "整体": ("总计",),
}
# Retain the original three-source completeness guard during the source-table
# migration.  Other channels are added to this check as soon as they appear in
# the live source data.
LEGACY_REQUIRED_CHANNELS = ("三方换量", "安卓导PC", "Affiliate")
REPORT_SECTIONS = {
    "overall": ("整体",),
    "partner": ("三方合作", "Affiliate"),
    "other": ("安卓导PC", "SEM", "官网", "其他", "微软商店", "SEO", "Mac"),
}
MAX_SHEETS_ATTEMPTS = 3
SHEETS_RETRY_DELAY_SECONDS = 5


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def request_rows(session, sheet_range: str) -> list[list[dict]]:
    for attempt in range(MAX_SHEETS_ATTEMPTS):
        try:
            response = session.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}",
                params={"ranges": [sheet_range], "includeGridData": "true", "fields": "sheets(data(rowData(values(formattedValue,effectiveValue))))"},
                timeout=30,
            )
            response.raise_for_status()
            break
        except requests.exceptions.HTTPError as error:
            status_code = error.response.status_code if error.response is not None else None
            if status_code != 429 and (status_code is None or not 500 <= status_code < 600):
                raise
            if attempt == MAX_SHEETS_ATTEMPTS - 1:
                raise
            time.sleep(SHEETS_RETRY_DELAY_SECONDS)
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
    return request_rows(session, f"{SOURCE_SHEET}!A:D"), request_rows(session, f"{TARGET_SHEET}!A1:Z200")


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
    """Read any 2026 target columns grouped by MAU/新增.

    The target sheet uses a group-name row immediately above a ``Month`` row.
    This deliberately derives the channel set from those headers, rather than
    retaining a second hard-coded list in the workflow.
    """
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
                group = cell_text(groups, column) or cell_text(groups, column - 1)
                value = number(target_row[column] if len(target_row) > column else None)
                if group and value is not None:
                    sections[section][group] = value
            section = None
    if not sections["MAU"] and not sections["新增"]:
        raise RuntimeError(f"{TARGET_SHEET} target sections could not be parsed.")
    return sections


def report_channels(records: list[dict], targets: dict[str, dict[str, float]] | None = None) -> list[str]:
    """Return the source and target channel dimensions, with total first."""
    channels = sorted({record["channel"] for record in records})
    if targets:
        channels = sorted(set(channels) | set(targets["MAU"]) | set(targets["新增"]))
    return (["整体"] if "整体" in channels else []) + [channel for channel in channels if channel != "整体"]


def missing_data_notice(records: list[dict], expected_date: date) -> str | None:
    live_channels = report_channels(records)
    expected_channels = set(live_channels)
    if "三方换量" in expected_channels:
        expected_channels.update(LEGACY_REQUIRED_CHANNELS)
    relevant = [record for record in records if record["date"] <= expected_date and record["channel"] in expected_channels]
    if not relevant:
        return f"注意：{expected_date.month}月{expected_date.day}日数据为空，请检查。"
    first_date = min(record["date"] for record in relevant)
    present_by_date: dict[date, set[str]] = defaultdict(set)
    for record in relevant:
        if record.get("new") is not None and record.get("mau") is not None:
            present_by_date[record["date"]].add(record["channel"])
    current = first_date
    while current <= expected_date:
        missing = expected_channels - present_by_date.get(current, set())
        if missing:
            if len(missing) == len(expected_channels):
                return f"注意：{current.month}月{current.day}日数据为空，请检查。"
            labels = "、".join(dict.fromkeys(
                channel for channel in [*live_channels, *LEGACY_REQUIRED_CHANNELS] if channel in missing
            ))
            return f"注意：{current.month}月{current.day}日{labels}数据为空，请检查。"
        current += timedelta(days=1)
    return None


def beijing_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def report_date() -> date:
    return beijing_today() - timedelta(days=1)


def report_subtitle(sent_date: date, data_date: date) -> str:
    days_in_month = calendar.monthrange(data_date.year, data_date.month)[1]
    return f"{sent_date:%Y-%m-%d}，时间进度 {data_date.day / days_in_month:.1%}"

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


def matching_target(targets: dict[str, float], channel: str) -> float | None:
    for name in (channel, *TARGET_ALIASES.get(channel, ())):
        if name in targets:
            return targets[name]
    return None


def metric_line(label: str, actual: float, target: float | None) -> str:
    if target is None:
        return f"🔴{label} **{actual:.2f}万**（目标待同步）"
    return f"🔴{label} **{actual:.2f}万 / {target:.2f}万**（{actual / target:.1%}）"


def missing_actual_block(channel: str, new_target: float | None, mau_target: float | None) -> str:
    def target_text(target: float | None) -> str:
        return f"{target:.2f}万目标" if target is not None else "目标待同步"

    return (
        f"**➡️{channel}**\n\n"
        f"🔴昨日新增 **暂未回传**\n\n"
        f"🔴本月新增 **暂未回传 / {target_text(new_target)}**\n\n"
        f"🔴近30天 MAD **暂未回传 / {target_text(mau_target)}**"
    )


def report_text(
    source_rows: list[list[dict]],
    target_rows: list[list[dict]],
    expected_date: date | None = None,
    channels: tuple[str, ...] | None = None,
) -> str:
    records = source_records(source_rows)
    latest = expected_date or report_date()
    notice = missing_data_notice(records, latest)
    if notice:
        return notice
    records = [record for record in records if record["date"] <= latest]
    targets = target_config(target_rows, latest.month)
    month_start = latest.replace(day=1)
    days_in_month = calendar.monthrange(latest.year, latest.month)[1]
    blocks = []
    for channel in channels or tuple(report_channels(records, targets)):
        matched = [record for record in records if record["channel"] == channel]
        daily_new: dict[date, float] = defaultdict(float)
        daily_mau: dict[date, float] = {}
        for record in matched:
            if record["new"] is not None:
                daily_new[record["date"]] += record["new"] / UNIT_DIVISOR
            if record["mau"] is not None:
                daily_mau[record["date"]] = record["mau"] / UNIT_DIVISOR
        new_target = matching_target(targets["新增"], channel)
        mau_target = matching_target(targets["MAU"], channel)
        if latest not in daily_new or latest not in daily_mau:
            blocks.append(missing_actual_block(channel, new_target, mau_target))
            continue
        month_actual = sum(value for day, value in daily_new.items() if month_start <= day <= latest)
        daily_actual = month_actual / latest.day
        daily_target = new_target / days_in_month if new_target is not None else None
        sparkline, _ = weekly_sparkline(daily_new, latest)
        daily_average = (
            f"**{daily_actual:.2f}万 / {daily_target:.2f}万**"
            if daily_target is not None
            else f"**{daily_actual:.2f}万**（目标待同步）"
        )
        blocks.append(
            f"**➡️{channel}**\n\n"
            f"🔴昨日新增 **{daily_new[latest]:.2f}万**　|　本月日均 {daily_average}\n\n"
            f"{metric_line('本月新增', month_actual, new_target)}\n\n"
            f"{metric_line('近30天 MAD', daily_mau[latest], mau_target)}\n\n"
            f"🔴近12周新增日均 {sparkline}"
        )
    return "\n\n".join(blocks) + f"\n\n[查看用户增长运营数据]({SHEET_URL})"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare user-growth KPI report.")
    parser.add_argument("--output")
    parser.add_argument("--overall-output")
    parser.add_argument("--partner-output")
    parser.add_argument("--other-output")
    parser.add_argument("--subtitle-output")
    args = parser.parse_args()
    source_rows, target_rows = read_data()
    sent_date, data_date = beijing_today(), report_date()
    if args.output:
        Path(args.output).write_text(report_text(source_rows, target_rows, expected_date=data_date), encoding="utf-8")
    else:
        outputs = {
            "overall": args.overall_output,
            "partner": args.partner_output,
            "other": args.other_output,
        }
        missing = [section for section, output in outputs.items() if not output]
        if missing:
            parser.error("--output or all three section output paths are required.")
        for section, output in outputs.items():
            Path(output).write_text(
                report_text(source_rows, target_rows, expected_date=data_date, channels=REPORT_SECTIONS[section]),
                encoding="utf-8",
            )
    if args.subtitle_output:
        Path(args.subtitle_output).write_text(report_subtitle(sent_date, data_date), encoding="utf-8")
    print("Acquisition progress content prepared.")


if __name__ == "__main__":
    main()
