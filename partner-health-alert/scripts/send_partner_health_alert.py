#!/usr/bin/env python3
"""Prepare a WPS webhook card for partner health data anomalies and alerts."""

from __future__ import annotations

import argparse
import calendar
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import google.auth.transport.requests
from google.oauth2 import service_account


SPREADSHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "数据解压"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=198957158"
DATA_RANGE = f"{SHEET_NAME}!P:Y"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
ERROR_PREFIXES = ("#REF!", "#DIV/0!", "#VALUE!", "#N/A", "#NAME?", "#NUM!", "#ERROR!")


@dataclass(frozen=True)
class MetricRule:
    key: str
    label: str
    header: str
    absolute_threshold: float
    relative_threshold: float
    mature_age_days: int
    percent: bool


@dataclass(frozen=True)
class DataRow:
    data_date: date
    partner: str
    values: dict[str, float | None]


@dataclass(frozen=True)
class Alert:
    partner: str
    metric: str
    direction: str
    current_date: date
    baseline_date: date
    current: float
    baseline: float
    difference: float
    relative_change: float
    trend: str = ""


RULES = (
    MetricRule("new_users", "新增", "新增设备数", 50, 0.20, 1, False),
    MetricRule("d1", "次日留存", "次日留存率", 0.03, 0.15, 2, True),
    MetricRule("uninstall", "卸载率", "当日卸载率", 0.03, 0.30, 1, True),
)


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def cell_text(row: list[dict], index: int) -> str:
    return row[index].get("formattedValue", "").strip() if len(row) > index else ""


def cell_date(row: list[dict], index: int) -> date | None:
    if len(row) <= index:
        return None
    numeric = row[index].get("effectiveValue", {}).get("numberValue")
    if numeric is not None:
        return date(1899, 12, 30) + timedelta(days=int(float(numeric)))
    text = cell_text(row, index)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def cell_number(row: list[dict], index: int) -> float | None:
    if len(row) <= index:
        return None
    numeric = row[index].get("effectiveValue", {}).get("numberValue")
    if numeric is not None:
        return float(numeric)
    text = cell_text(row, index)
    if not text or text.startswith(ERROR_PREFIXES):
        return None
    try:
        if text.endswith("%"):
            return float(text[:-1].replace(",", "")) / 100
        return float(text.replace(",", ""))
    except ValueError:
        return None


def request_rows(session: google.auth.transport.requests.AuthorizedSession) -> list[list[dict]]:
    response = session.get(
        f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}",
        params={
            "ranges": [DATA_RANGE],
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


def read_data() -> list[list[dict]]:
    info = json.loads(required("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"))
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(credentials)
    return request_rows(session)


def column_index(headers: list[str], name: str) -> int:
    try:
        return headers.index(name)
    except ValueError as exc:
        raise RuntimeError(f"数据解压缺少表头：{name}") from exc


def parse_rows(sheet_rows: list[list[dict]]) -> tuple[list[DataRow], list[tuple[date, str]]]:
    if not sheet_rows:
        raise RuntimeError("数据解压 P:Y 没有返回任何数据。")
    headers = [cell_text(sheet_rows[0], index) for index in range(len(sheet_rows[0]))]
    columns = {
        "date": column_index(headers, "日期"),
        "partner": column_index(headers, "合作方"),
        **{rule.key: column_index(headers, rule.header) for rule in RULES},
    }
    parsed: list[DataRow] = []
    issues: list[tuple[date, str]] = []
    for row in sheet_rows[1:]:
        data_date = cell_date(row, columns["date"])
        partner = cell_text(row, columns["partner"])
        if not data_date and not partner:
            continue
        if data_date is None or not partner:
            continue
        values: dict[str, float | None] = {}
        for rule in RULES:
            text = cell_text(row, columns[rule.key])
            if text.startswith(ERROR_PREFIXES):
                issues.append((data_date, f"{data_date} {partner} {rule.label}公式错误：{text}"))
            values[rule.key] = cell_number(row, columns[rule.key])
        parsed.append(DataRow(data_date, partner, values))
    return parsed, list(dict.fromkeys(issues))


def relevant_dates(today: date) -> set[date]:
    dates: set[date] = set()
    for rule in RULES:
        current = today - timedelta(days=rule.mature_age_days)
        dates.update((current, current - timedelta(days=1), current - timedelta(days=7), current - timedelta(days=8)))
    return dates


def relative_change(current: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0 if current == 0 else math.copysign(math.inf, current - baseline)
    return (current - baseline) / baseline


def alert_direction(current: float, baseline: float, rule: MetricRule) -> str | None:
    difference = current - baseline
    relative = relative_change(current, baseline)
    if abs(difference) < rule.absolute_threshold or abs(relative) < rule.relative_threshold:
        return None
    return "上涨" if difference > 0 else "下跌"


def format_value(value: float, percent: bool) -> str:
    return f"{value:.1%}" if percent else f"{value:,.0f}"


def format_difference(value: float, percent: bool) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1%}" if percent else f"{sign}{value:,.0f}"


def format_relative(value: float) -> str:
    if math.isinf(value):
        return "+∞" if value > 0 else "-∞"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1%}"


def format_trend(values: list[float | None], percent: bool) -> str:
    segments: list[str] = []
    series: list[str] = []
    missing = 0
    previous: float | None = None

    for value in values:
        if value is None:
            if series:
                segments.append(" ".join(series))
                series = []
                previous = None
            missing += 1
            continue
        if missing:
            segments.append(f"缺失×{missing}")
            missing = 0
        if previous is not None:
            series.append("↑" if value > previous else "↓" if value < previous else "→")
        series.append(format_value(value, percent))
        previous = value

    if series:
        segments.append(" ".join(series))
    if missing:
        segments.append(f"缺失×{missing}")
    return " ｜ ".join(segments) if segments else "数据不足"


def partner_trend(
    index: dict[tuple[date, str], DataRow], partner: str, metric: str, end_date: date, percent: bool
) -> str:
    start_date = three_months_before(end_date)
    values: list[float | None] = []
    trend_date = start_date + timedelta(days=(end_date.weekday() - start_date.weekday()) % 7)
    while trend_date <= end_date:
        row = index.get((trend_date, partner))
        values.append(row.values[metric] if row else None)
        trend_date += timedelta(days=7)
    return format_trend(values, percent)


def three_months_before(value: date) -> date:
    month = value.month - 3
    year = value.year
    if month <= 0:
        month += 12
        year -= 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def analyze(rows: list[DataRow], today: date) -> tuple[dict[str, date], list[Alert], list[str]]:
    index = {(row.data_date, row.partner): row for row in rows}
    latest_dates: dict[str, date] = {}
    alerts: list[Alert] = []
    anomalies: list[str] = []

    for rule in RULES:
        available_dates = [row.data_date for row in rows if row.values[rule.key] is not None]
        if not available_dates:
            anomalies.append(f"{rule.label}没有有效数据")
            continue
        current_date = today - timedelta(days=rule.mature_age_days)
        if current_date not in available_dates:
            latest_date = max(available_dates)
            latest_dates[rule.key] = latest_date
            anomalies.append(f"{rule.label}缺少应有日期 {current_date} 的完整数据；最新有效日期 {latest_date}")
            continue
        latest_dates[rule.key] = current_date

        baseline_date = current_date - timedelta(days=7)
        previous_date = current_date - timedelta(days=1)
        previous_baseline_date = current_date - timedelta(days=8)
        current_partners = {
            row.partner for row in rows
            if row.data_date == current_date and row.values[rule.key] is not None
        }
        expected_partners = {
            row.partner for row in rows
            if row.data_date in {previous_date, baseline_date} and row.values[rule.key] is not None
        }
        missing_partners = sorted(expected_partners - current_partners)
        if missing_partners:
            anomalies.append(f"{current_date} {rule.label}缺少合作方数据：{'、'.join(missing_partners)}")

        comparable = 0
        states: dict[str, str | None] = {}
        candidates: list[Alert] = []
        for partner in sorted(current_partners):
            row = index[(current_date, partner)]
            baseline_row = index.get((baseline_date, partner))
            baseline = baseline_row.values[rule.key] if baseline_row else None
            if baseline is None:
                continue
            comparable += 1
            current = row.values[rule.key]
            assert current is not None
            direction = alert_direction(current, baseline, rule)
            states[partner] = direction
            if direction is None:
                continue

            previous_row = index.get((previous_date, partner))
            previous_baseline_row = index.get((previous_baseline_date, partner))
            previous_direction = None
            if previous_row and previous_baseline_row:
                previous_current = previous_row.values[rule.key]
                previous_baseline = previous_baseline_row.values[rule.key]
                if previous_current is not None and previous_baseline is not None:
                    previous_direction = alert_direction(previous_current, previous_baseline, rule)
            if previous_direction == direction:
                continue
            candidates.append(Alert(
                partner=partner,
                metric=rule.key,
                direction=direction,
                current_date=current_date,
                baseline_date=baseline_date,
                current=current,
                baseline=baseline,
                difference=current - baseline,
                relative_change=relative_change(current, baseline),
                trend=partner_trend(index, partner, rule.key, current_date, rule.percent),
            ))

        for direction in ("上涨", "下跌"):
            affected = sum(state == direction for state in states.values())
            if comparable >= 2 and affected > comparable / 2:
                anomalies.append(f"{current_date} {rule.label}：{affected}/{comparable} 个可比较合作方同时异常{direction}")
                candidates = []
                break
        alerts.extend(candidates)

    return latest_dates, alerts, anomalies


def alert_block(alert: Alert) -> str:
    rule = next(rule for rule in RULES if rule.key == alert.metric)
    return "\n\n".join([
        f"## {alert.partner}｜{rule.label}异常{alert.direction}",
        f"- 当前（{alert.current_date}）：{format_value(alert.current, rule.percent)}",
        f"- 上周同日（{alert.baseline_date}）：{format_value(alert.baseline, rule.percent)}",
        f"- 变化：绝对值 {format_difference(alert.difference, rule.percent)}；环比 {format_relative(alert.relative_change)}",
        f"- 近3个月同周期趋势：{alert.trend}",
    ])


def alert_markdown(latest_dates: dict[str, date], alerts: list[Alert], anomalies: list[str]) -> str:
    date_parts = [f"{rule.label} {latest_dates[rule.key]}" for rule in RULES if rule.key in latest_dates]
    blocks = ["# 三方换量合作方健康告警", f"数据日期：{'；'.join(date_parts) or '未知'}"]
    if anomalies:
        blocks.append("\n\n".join(["## 数据异常", *(f"- {item}" for item in anomalies)]))
    blocks.extend(alert_block(alert) for alert in alerts)
    blocks.append(f"[查看数据解压]({SHEET_URL})")
    return "\n\n".join(blocks)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a WPS partner-health alert when an anomaly starts.")
    parser.add_argument("--output", required=True, help="UTF-8 alert file path; not created when no alert is triggered")
    parser.add_argument("--today", help="Override Asia/Shanghai date for replay/testing (YYYY-MM-DD)")
    args = parser.parse_args()
    today = date.fromisoformat(args.today) if args.today else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    rows, parse_issues = parse_rows(read_data())
    latest_dates, alerts, anomalies = analyze(rows, today)
    anomalies = [message for issue_date, message in parse_issues if issue_date in relevant_dates(today)] + anomalies
    if not alerts and not anomalies:
        print("No data anomaly or new partner alert. Alert file not created.")
        return
    Path(args.output).write_text(alert_markdown(latest_dates, alerts, anomalies), encoding="utf-8")
    print(f"Partner-health alert prepared: {len(alerts)} alert(s), {len(anomalies)} data anomaly/anomalies.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Partner-health alert failed: {exc}", file=sys.stderr)
        raise
