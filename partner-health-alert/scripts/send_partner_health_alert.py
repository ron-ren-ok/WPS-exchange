#!/usr/bin/env python3
"""Send a WPS alert only for data anomalies or red partner-health alerts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import google.auth.transport.requests
from google.oauth2 import service_account


SPREADSHEET_ID = "1CXEdn4HWqRRgMD0gjm0sj8aqdpMwZFi4JHT3j3-Nn7Q"
SHEET_NAME = "数据总览"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid=1388813723"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
ERROR_PREFIXES = ("#REF!", "#DIV/0!", "#VALUE!", "#N/A", "#NAME?", "#NUM!", "#ERROR!")


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
    grids = [grid for sheet in response.json().get("sheets", []) for grid in sheet.get("data", [])]
    if len(grids) != len(ranges):
        raise RuntimeError(f"Google Sheets returned {len(grids)} ranges, expected {len(ranges)}.")
    return [[row.get("values", []) for row in grid.get("rowData", [])] for grid in grids]


def read_overview() -> tuple[list[list[dict]], list[list[dict]]]:
    info = json.loads(required("GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"))
    credentials = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    session = google.auth.transport.requests.AuthorizedSession(credentials)
    return tuple(request_rows(session, [f"{SHEET_NAME}!A1:N34", f"{SHEET_NAME}!P1:Q10"]))


def formula_errors(market_rows: list[list[dict]]) -> list[str]:
    metrics = []
    for row in market_rows:
        metric, value = cell_text(row, 0), cell_text(row, 1)
        if metric and value.startswith(ERROR_PREFIXES):
            metrics.append(metric)
    return metrics


def data_anomalies(overview_rows: list[list[dict]], market_rows: list[list[dict]]) -> tuple[str, list[str]]:
    latest_text = cell_text(overview_rows[1], 1) if len(overview_rows) > 1 else ""
    latest_date = cell_date(overview_rows[1], 1) if len(overview_rows) > 1 else None
    anomalies = []
    if latest_date is None:
        anomalies.append("数据最新日期为空或格式无法识别")
    else:
        age = (datetime.now(ZoneInfo("Asia/Shanghai")).date() - latest_date).days
        if age > 1:
            anomalies.append(f"数据最新日期滞后 {age} 天")
    broken_metrics = formula_errors(market_rows)
    if broken_metrics:
        anomalies.append(f"大盘指标公式错误：{'、'.join(broken_metrics)}")
    return latest_text or "未知", anomalies


def red_partners(overview_rows: list[list[dict]]) -> list[dict[str, str]]:
    partners = []
    for row in overview_rows[4:]:
        if not cell_text(row, 0) or cell_text(row, 12) != "红色预警":
            continue
        partners.append({
            "name": cell_text(row, 0),
            "new_users": cell_text(row, 1),
            "d1": cell_text(row, 2),
            "d1_market": cell_text(row, 3),
            "d7": cell_text(row, 4),
            "d7_market": cell_text(row, 5),
            "uninstall": cell_text(row, 6),
            "uninstall_market": cell_text(row, 7),
            "d1_baseline": cell_text(row, 9),
            "d7_baseline": cell_text(row, 10),
            "uninstall_baseline": cell_text(row, 11),
            "level": cell_text(row, 12),
            "reason": cell_text(row, 13),
        })
    return partners


def partner_block(partner: dict[str, str], anomaly_reason: str) -> str:
    return "\n".join([
        f"## {partner['name']}：{partner['level']} | {partner['reason'] or '未填写预警原因'}",
        f"- 昨日新增：{partner['new_users'] or '—'}",
        f"- 次留：{partner['d1'] or '—'}（较大盘 {partner['d1_market'] or '—'}；较近4个同星期 {partner['d1_baseline'] or '—'}）",
        f"- 7留：{partner['d7'] or '—'}（较大盘 {partner['d7_market'] or '—'}；较近4个同星期 {partner['d7_baseline'] or '—'}）",
        f"- 昨日卸载率：{partner['uninstall'] or '—'}（较大盘 {partner['uninstall_market'] or '—'}；较近4个同星期 {partner['uninstall_baseline'] or '—'}）",
        f"- 数据异常原因：{anomaly_reason}",
    ])


def alert_markdown(data_date: str, partners: list[dict[str, str]], anomalies: list[str]) -> str:
    anomaly_reason = "；".join(anomalies) if anomalies else "无"
    blocks = ["# 🚨 三方换量用户健康度预警", f"数据日期：{data_date}", ""]
    if partners:
        blocks.extend(partner_block(partner, anomaly_reason) for partner in partners)
    else:
        blocks.extend([
            "## 数据总览：数据异常 | 未发现红色预警",
            "- 昨日新增：—",
            "- 次留：—（较大盘 —；较近4个同星期 —）",
            "- 7留：—（较大盘 —；较近4个同星期 —）",
            "- 昨日卸载率：—（较大盘 —；较近4个同星期 —）",
            f"- 数据异常原因：{anomaly_reason}",
        ])
    return "\n\n".join(blocks) + f"\n\n[查看数据总览]({SHEET_URL})"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a WPS partner-health alert only when it is triggered.")
    parser.add_argument("--output", required=True, help="UTF-8 alert file path; not created when no alert is triggered")
    args = parser.parse_args()
    overview_rows, market_rows = read_overview()
    data_date, anomalies = data_anomalies(overview_rows, market_rows)
    partners = red_partners(overview_rows)
    if not partners and not anomalies:
        print("No data anomaly or red alert. Alert file not created.")
        return
    Path(args.output).write_text(alert_markdown(data_date, partners, anomalies), encoding="utf-8")
    print(f"Partner-health alert prepared: {len(partners)} red partner(s), {len(anomalies)} data anomaly/anomalies.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Partner-health alert failed: {exc}", file=sys.stderr)
        raise
