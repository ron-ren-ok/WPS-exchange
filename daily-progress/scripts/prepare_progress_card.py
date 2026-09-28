#!/usr/bin/env python3
"""Build one WPS text card with proportional bars; no public image storage."""

from __future__ import annotations

import argparse
import calendar
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import send_daily_progress as daily

COLORS = ("#3576dc", "#e5a239", "#3576dc", "#d4dae2")
MARKS = ("▰", "▰", "▱", "▰")
TEXT_GRAY = "#8c8c8c"


def metric_summary(records: list[dict], metric: str, target: float, cutoff: date, predicate=lambda r: True) -> dict | None:
    if target <= 0:
        raise ValueError("Monthly target must be greater than zero.")
    if not daily.metric_available(records, metric, predicate):
        return None
    actual = daily.actual_metric_summary(records, metric, cutoff, predicate)["cumulative"]
    forecast = daily.forecast_metric_summary(records, metric, cutoff, predicate)
    measured, projected = forecast["cumulative"], forecast["projected"]
    parts = [actual, measured - actual, projected - measured, max(target - projected, 0)]
    if any(value < -1e-9 for value in parts):
        raise ValueError("Negative values cannot be displayed as a progress bar.")
    return {"actual": actual, "projected": projected, "target": target, "parts": [max(v, 0) for v in parts]}


def bar_parts(summary: dict, width: int = 20) -> list[int]:
    """Largest remainder allocation keeps the bar length and zero-gap invariant."""
    if width < 1:
        raise ValueError("Progress bar width must be positive.")
    maximum = max(summary["target"], summary["projected"])
    raw = [value / maximum * width for value in summary["parts"]]
    counts = [int(value) for value in raw]
    remainder = width - sum(counts)
    # Zero-sized segments may never receive a display cell, notably after success.
    eligible = [i for i, value in enumerate(summary["parts"]) if value > 0]
    order = sorted(eligible, key=lambda i: raw[i] - counts[i], reverse=True)
    for i in order[:remainder]:
        counts[i] += 1
    if summary["parts"][3] > 0 and counts[3] == 0:
        largest = max(range(3), key=lambda i: counts[i])
        counts[largest] -= 1
        counts[3] = 1
    return counts


def progress_bar(summary: dict) -> str:
    counts = bar_parts(summary)
    width = sum(counts)
    marker = max(1, round(summary["target"] / max(summary["target"], summary["projected"]) * width))
    pieces, position = [], 0
    for i, count in enumerate(counts):
        end = position + count
        if position < marker <= end:
            before, after = marker - position, end - marker
            pieces.append(f"<font color='{COLORS[i]}'>{MARKS[i] * before}</font>")
            pieces.append("│")
            if after:
                pieces.append(f"<font color='{COLORS[i]}'>{MARKS[i] * after}</font>")
        elif count:
            pieces.append(f"<font color='{COLORS[i]}'>{MARKS[i] * count}</font>")
        position = end
    return "".join(pieces)


def metric_block(label: str, unit: str, summary: dict | None, target: float) -> str:
    if summary is None:
        return f"**{label}（月目标 {target:.2f}）**\n\n当月尚未回传，暂不预测"
    actual, projected = summary["actual"], summary["projected"]
    measured = actual + summary["parts"][1]
    actual_rate, projected_rate = actual / target * 100, projected / target * 100
    status = "✅ 实际已达标" if actual >= target else ("🟢 预计达标" if projected >= target else "🔴 预计未达标")
    if projected < target:
        outcome = f"**预计缺口 {target - projected:.2f} {unit}**"
    elif actual >= target:
        # Keep the user's confirmed concise outcome wording.
        outcome_unit = "万" if label == "血量" else unit
        outcome = f"**实际超目标 {actual - target:.2f} {outcome_unit} · 预计超目标 {projected - target:.2f} {outcome_unit}**"
    else:
        outcome_unit = "万" if label == "血量" else unit
        outcome = f"**预计超目标 {projected - target:.2f} {outcome_unit}**"
    return "\n\n".join([
        f"**{label}（月目标 {target:.2f}）**　{status}",
        f"已回传 **{actual:.2f}**　·　预测回传 **{measured:.2f}**　·　月底预测 **{projected:.2f}**",
        f"实际完成率 {actual_rate:.1f}%　·　预计完成率 {projected_rate:.1f}%",
        progress_bar(summary),
        outcome,
    ])


def card_elements(records: list[dict], targets: dict[str, float], cutoff: date) -> list[dict]:
    monthly = [r for r in records if (r["date"].year, r["date"].month) == (cutoff.year, cutoff.month) and r["date"] <= cutoff]
    if not monthly:
        raise ValueError(f"No source data for {cutoff:%Y-%m}.")
    revenue = metric_summary(monthly, "血量", targets["血量"], cutoff)
    users = metric_summary(monthly, "新增", targets["360新增"], cutoff, lambda r: r["partner"] == "360")
    partners = sorted({r["partner"] for r in monthly} | {"360"})
    returned = {r["partner"] for r in monthly if r["date"] == cutoff}
    missing = [p for p in partners if p not in returned]
    def gray(text: str) -> str:
        return f"<font color='{TEXT_GRAY}'>{text}</font>"

    status = "\n\n".join([gray(f"⚠ 数据不全 · {len(missing)} 个合作方"), gray('、'.join(missing))]) if missing else gray("✅ 数据完整")
    legend = "　".join([
        f"<font color='{color}'>{mark}</font> {gray(label)}"
        for color, mark, label in zip(COLORS, MARKS, ("已回传", "未回传", "后续预测", "预计缺口"))
    ]) + "　" + gray("│ 目标位置")
    def text_element(text: str) -> dict:
        return {"tag": "text", "content": {"type": "markdown", "text": text}}

    return [
        text_element(metric_block("血量", "万美元", revenue, targets["血量"])),
        {"tag": "hr", "style": "solid"},
        text_element(metric_block("360 新增", "万人", users, targets["360新增"])),
        {"tag": "hr", "style": "solid"},
        text_element("\n\n".join([status, legend, f"[查看合作方新增血量]({daily.SHEET_URL})"])),
    ]


def card_content(records: list[dict], targets: dict[str, float], cutoff: date) -> str:
    """Plain-text companion; separators are native elements in the sent card."""
    return "\n\n".join(element["content"]["text"] for element in card_elements(records, targets, cutoff) if element["tag"] == "text")


def card_subtitle(cutoff: date, report_date: date) -> str:
    days = calendar.monthrange(cutoff.year, cutoff.month)[1]
    return f"{report_date:%Y-%m-%d} · 时间进度 {cutoff.day / days:.0%} · 距月末 {days - cutoff.day} 天"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--subtitle-output", required=True)
    parser.add_argument("--elements-output", help="JSON card components including native horizontal rules")
    parser.add_argument("--end-date", help="Report cutoff date; defaults to yesterday Beijing time")
    args = parser.parse_args()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    cutoff = daily.parse_day(args.end_date) if args.end_date else today - timedelta(days=1)
    source, targets = daily.request_values()
    elements = card_elements(daily.long_records(source), daily.monthly_targets(targets, cutoff.month), cutoff)
    content = "\n\n".join(element["content"]["text"] for element in elements if element["tag"] == "text")
    Path(args.output).write_text(content, encoding="utf-8")
    if args.elements_output:
        Path(args.elements_output).write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")
    Path(args.subtitle_output).write_text(card_subtitle(cutoff, today), encoding="utf-8")
    print("WPS combined progress card prepared.")


if __name__ == "__main__":
    main()
