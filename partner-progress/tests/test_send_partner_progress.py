import importlib.util
import re
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "send_partner_progress.py"
SPEC = importlib.util.spec_from_file_location("partner_progress", MODULE)
PROGRESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROGRESS)


def cell(text=None, number=None):
    result = {}
    if text is not None:
        result["formattedValue"] = str(text)
    if number is not None:
        result["effectiveValue"] = {"numberValue": number}
    return result


class PartnerProgressTests(unittest.TestCase):
    def test_partner_activity_uses_shared_inclusive_42_day_window_and_either_metric(self):
        cutoff = date(2026, 9, 27)
        names = ("recent", "boundary", "expired", "revenue_zero", "new_zero", "blank", "future", "new_only")
        configs = {name: {"name": name, "target_metric": "revenue", "target": 1.0} for name in names}
        configs["new_only"]["target_metric"] = "new"

        def record(name, offset, new=None, revenue=None):
            return {"partner": name, "date": cutoff - timedelta(days=offset),
                    "operation": "气泡", "new": new, "revenue": revenue}

        records = [record("recent", 0, revenue=1.0), record("boundary", 41, revenue=1.0),
                   record("expired", 42, revenue=1.0), record("revenue_zero", 0, revenue=0.0),
                   record("new_zero", 0, new=0.0), record("new_zero", 50, revenue=1.0),
                   record("blank", 0), record("blank", 50, revenue=1.0),
                   record("future", -1, revenue=1.0), record("new_only", 1, new=2.0)]
        with patch.object(PROGRESS, "target_config", return_value=configs) as config:
            partners = PROGRESS.build_partners(records, [], cutoff)
        config.assert_called_once_with([], 9)
        self.assertEqual([partner["name"] for partner in partners],
                         ["recent", "boundary", "revenue_zero", "new_zero", "new_only"])

    def test_inactive_partner_is_restored_when_data_returns_without_changing_targets(self):
        cutoff = date(2026, 9, 27)
        config = {"CAD": {"name": "CAD", "target_metric": "new", "target": 1.0}}
        records = [{"partner": "CAD", "date": cutoff - timedelta(days=42),
                    "operation": "气泡", "new": 1.0, "revenue": None}]
        with patch.object(PROGRESS, "target_config", return_value=config):
            self.assertEqual(PROGRESS.build_partners(records, [], cutoff), [])
            records.append({"partner": "CAD", "date": cutoff, "operation": "气泡", "new": 0.0, "revenue": None})
            self.assertEqual(PROGRESS.build_partners(records, [], cutoff), [config["CAD"]])

    def test_all_inactive_partners_produce_empty_status_cards_instead_of_failure(self):
        cutoff = date(2026, 9, 27)
        def row(day, name):
            return [cell(number=(day - date(1899, 12, 30)).days), cell(name), cell("气泡"), cell(number=100), cell()]
        rows = [[cell(header) for header in PROGRESS.REQUIRED_SOURCE_HEADERS],
                row(cutoff - timedelta(days=42), "CAD"), row(cutoff, "Unconfigured")]
        targets = [[cell(), cell("合作方新增目标"), cell("合作方新增实际")],
                   [cell("月份"), cell("CAD"), cell("CAD")],
                   [cell("9月"), cell(number=1), cell(number=999)]]
        reports = PROGRESS.report_texts(rows, targets)
        for report in reports.values():
            self.assertIn("暂无符合条件的合作方", report)
            self.assertNotIn("**CAD**", report)

    def test_long_table_uses_headers_and_aggregates_all_operations(self):
        rows = [
            [cell("运营位"), cell("血量"), cell("日期"), cell("合作方"), cell("新增")],
            [cell("气泡"), cell(number=100), cell(number=46217), cell("Opera"), cell(number=1000)],
            [cell("换量弹窗"), cell(number=200), cell(number=46217), cell("Opera"), cell(number=2000)],
        ]
        records = PROGRESS.source_records(rows)
        self.assertEqual(records[0]["partner"], "Opera")
        self.assertEqual(records[0]["operation"], "气泡")
        partners = [{"name": "Opera", "target_metric": "revenue", "target": 3}]
        series = PROGRESS.make_series(records, partners)
        self.assertAlmostEqual(series["Opera"]["new"][date(2026, 7, 14)], 0.3)
        self.assertEqual(series["Opera"]["revenue"][date(2026, 7, 14)], 0.03)

    def test_long_table_range_has_no_fixed_row_cap(self):
        self.assertIn('f"{SOURCE_SHEET}!A:E"', Path(MODULE).read_text(encoding="utf-8"))

    def test_target_config_uses_only_target_columns_not_actual_columns(self):
        target_rows = [
            [cell(), cell(), cell(), cell(), cell(), cell("合作方预算目标"), cell(), cell(), cell(), cell("合作方预算实际"), cell(), cell(), cell(), cell("合作方新增目标"), cell(), cell("合作方新增实际")],
            [cell("月份"), cell(), cell(), cell(), cell(), cell("Opera"), cell("Yandex"), cell("Avast"), cell("Winriser"), cell("Opera"), cell("Yandex"), cell("Avast"), cell("Winriser"), cell("360"), cell("Terabox"), cell("360")],
            [cell("7月"), cell(), cell(), cell(), cell(), cell(number=6), cell(number=3.8), cell(number=10.2), cell(number=3), cell(number=6.32), cell(number=1.02), cell(number=8.38), cell(number=0.93), cell(number=60), cell(number=15), cell(number=59.24)],
        ]
        targets = PROGRESS.target_config(target_rows, 7)
        self.assertEqual(targets["Opera"], {"name": "Opera", "target_metric": "revenue", "target": 6.0})
        self.assertEqual(targets["360"], {"name": "360", "target_metric": "new", "target": 60.0})
    def test_daily_comparison_is_latest_date_against_same_day_last_week(self):
        latest = date(2026, 9, 27)
        series = {latest - timedelta(days=offset): 1.0 for offset in range(21)}
        series.update({latest - timedelta(days=offset): 3.0 for offset in range(7, 14)})
        series.update({latest - timedelta(days=offset): 10.0 for offset in range(7)})
        series[latest], series[date(2026, 9, 20)] = 20.0, 5.0
        # 9/27 vs 9/20 is 20/5; other weeks must not add extra percentages.
        series[date(2026, 9, 6)] = 99999.0
        expected = "**20.00** ｜↑ **300.0%** "
        for label in ("新增", "血量"):
            self.assertEqual(PROGRESS.metric_line(label, series, latest), f"{label}：{expected}")

    def test_missing_is_distinct_from_zero_and_zero_baseline_is_unavailable(self):
        latest = date(2026, 9, 27)
        self.assertEqual(PROGRESS.average({latest: 0.0}, latest, 7), 0.0)
        self.assertIsNone(PROGRESS.average({}, latest, 7))
        self.assertEqual(PROGRESS.percent_change(0.0, 2.0), "↓ **100.0%** ")
        self.assertEqual(PROGRESS.percent_change(1.0, 0.0), "**—** ")
        self.assertEqual(PROGRESS.percent_change(None, 2.0), "**—** ")
        self.assertEqual(PROGRESS.percent_change(2.0, 2.0), "→ **0.0%** ")
        self.assertEqual(PROGRESS.metric_line("新增", {}, latest), "新增：**—** ｜**—** ")

    def test_incomplete_windows_average_reported_dates_without_zero_filling(self):
        latest = date(2026, 9, 27)
        series = {latest: 8.0, latest - timedelta(days=6): 0.0,
                  latest - timedelta(days=7): 2.0, latest - timedelta(days=14): 1.0}
        self.assertEqual(PROGRESS.metric_line("血量", series, latest),
                         "血量：**8.00** ｜↑ **300.0%** ")
        self.assertEqual(PROGRESS.weekly_averages(series, latest), [None, None, None, 1.0, 2.0, 4.0])

    def test_six_week_means_are_oldest_first_with_inclusive_seven_day_windows(self):
        latest = date(2026, 9, 27)
        series = {}
        values = [0.0, 1.0, 2.0, 3.0, 4.0, 7.0]
        for index, value in enumerate(values):
            end = latest - timedelta(days=7 * (5 - index))
            for offset in range(7):
                series[end - timedelta(days=offset)] = value
        # Neither side of the 42-day range may affect the six means.
        series[date(2026, 8, 16)] = 99999.0
        series[date(2026, 9, 28)] = 99999.0
        self.assertEqual(PROGRESS.weekly_averages(series, latest), values)
        self.assertEqual(PROGRESS.weekly_sparkline(series, latest), "▁▂▃▄▅█")
        self.assertEqual(series[date(2026, 9, 21)], 7.0)
        self.assertEqual(series[date(2026, 9, 27)], 7.0)

    def test_six_trend_positions_distinguish_missing_weeks_from_real_zero(self):
        latest = date(2026, 9, 27)
        zeros = {latest - timedelta(days=offset): 0.0 for offset in range(42)}
        self.assertEqual(PROGRESS.weekly_sparkline(zeros, latest), "▁▁▁▁▁▁")
        self.assertEqual(PROGRESS.weekly_sparkline({}, latest), "······")
        self.assertEqual(PROGRESS.weekly_sparkline({latest: 3.0}, latest), "·····█")
        line = PROGRESS.metric_line("血量", {latest: 3.0}, latest, show_trend=True)
        self.assertTrue(line.endswith("·····█"))

    def test_heading_preserves_monthly_forecast_and_actual_completion(self):
        latest = date(2026, 9, 27)
        series = {date(2026, 9, 1) + timedelta(days=i): 1.0 for i in range(27)}
        series[date(2026, 8, 31)] = 999.0
        self.assertEqual(PROGRESS.partner_heading("Opera", series, latest, 20.0),
                         "> <font color='#000000'>**Opera** · 9/27｜**✅135%→140%→150%** </font>  \n"
                         "> <font color='#000000'>累计 **27.00→28.00→30.00** </font>")
        self.assertIn("⏳54%→56%→60%", PROGRESS.partner_heading("Opera", series, latest, 50.0))
        self.assertIn("9/27｜**—→—→—", PROGRESS.partner_heading("Opera", series, latest, 0.0))

    def test_next_day_is_cumulative_and_uses_same_14_day_average_as_month_end(self):
        latest = date(2026, 9, 27)
        series = {date(2026, 9, 1) + timedelta(days=i): 1.0 for i in range(27)}
        series[date(2026, 9, 27)] = 15.0
        # September cumulative=41; trailing 14-day mean=2; next-day=43; month-end=47.
        heading = PROGRESS.partner_heading("360", series, latest, 50.0)
        self.assertIn("⏳82%→86%→94%", heading.splitlines()[0])
        self.assertIn("累计 **41.00→43.00→47.00", heading.splitlines()[1])
        self.assertEqual(heading.count("⏳"), 1)
        self.assertNotIn("✅", heading)

    def test_exact_target_is_achieved_and_predictions_have_no_achievement_icons(self):
        latest = date(2026, 9, 27)
        heading = PROGRESS.partner_heading("CAD", {latest: 1.0}, latest, 1.0)
        self.assertIn("✅100%→200%→400%", heading.splitlines()[0])
        self.assertIn("累计 **1.00→2.00→4.00", heading.splitlines()[1])
        self.assertEqual(heading.count("✅"), 1)

    def test_month_end_next_day_does_not_use_previous_month_target(self):
        latest = date(2026, 2, 28)
        heading = PROGRESS.partner_heading("Opera", {latest: 2.0}, latest, 4.0)
        self.assertIn("⏳50%→—→50%", heading.splitlines()[0])
        self.assertIn("累计 **2.00→—→2.00", heading.splitlines()[1])

    def test_zero_daily_rate_keeps_zero_forecasts_and_zero_target_has_no_percentages(self):
        latest = date(2026, 9, 27)
        heading = PROGRESS.partner_heading("CAD", {latest: 0.0}, latest, 1.0)
        self.assertIn("⏳0%→0%→0%", heading.splitlines()[0])
        self.assertIn("累计 **0.00→0.00→0.00", heading.splitlines()[1])
        invalid_target = PROGRESS.partner_heading("CAD", {latest: 1.0}, latest, 0.0)
        self.assertNotIn("%", invalid_target)
        self.assertNotIn("✅", invalid_target)
        self.assertNotIn("⏳", invalid_target)

    def test_reports_split_partners_keep_latest_dates_and_real_paragraph_breaks(self):
        def source_row(day, name, new_value, revenue_value=None):
            serial = (day - date(1899, 12, 30)).days
            return [cell(number=serial), cell(name), cell("气泡"),
                    cell(number=new_value), cell(number=revenue_value)]

        rows = [[cell(h) for h in PROGRESS.REQUIRED_SOURCE_HEADERS],
                source_row(date(2026, 9, 27), "Opera", 400, 200),
                source_row(date(2026, 9, 28), "360", 10000),
                source_row(date(2026, 9, 26), "CAD", 2000)]
        targets = [[cell(), cell("合作方预算目标"), cell("合作方预算实际"),
                    cell("合作方新增目标"), cell(), cell("合作方新增实际")],
                   [cell("月份"), cell("Opera"), cell("Opera"), cell("360"), cell("CAD"), cell("360")],
                   [cell("9月"), cell(number=1), cell(number=999), cell(number=2), cell(number=3), cell(number=999)]]
        reports = PROGRESS.report_texts(rows, targets)
        revenue, new = reports["revenue"], reports["new"]
        self.assertIn("**Opera** · 9/27", revenue)
        self.assertNotIn("360 ·", revenue)
        self.assertNotIn("CAD ·", revenue)
        self.assertIn("**360** · 9/28", new)
        self.assertIn("**CAD** · 9/26", new)
        self.assertNotIn("Opera ·", new)
        self.assertNotIn("血量：", new)
        self.assertIn("</font>\n\n新增：", revenue)
        self.assertIn("\n\n血量：", revenue)
        self.assertNotIn("28日", revenue)
        self.assertNotIn("近12周", revenue)
        self.assertNotIn("\\n", revenue)
        self.assertEqual(revenue.count("> <font color='#000000'>**"), 1)
        self.assertEqual(new.count("> <font color='#000000'>**"), 2)
        revenue_new = next(line for line in revenue.splitlines() if line.startswith("新增："))
        revenue_blood = next(line for line in revenue.splitlines() if line.startswith("血量："))
        self.assertIsNone(re.search(r"[▁▂▃▄▅▆▇█·]{6}$", revenue_new))
        self.assertRegex(revenue_blood, r"[▁▂▃▄▅▆▇█·]{6}$")
        for line in new.splitlines():
            if line.startswith("新增："):
                self.assertRegex(line, r"[▁▂▃▄▅▆▇█·]{6}$")
        for report in reports.values():
            body, footer = report.split("\n\n---\n\n")
            self.assertNotIn("绝对值｜", body)
            self.assertNotIn("万人", report)
            self.assertNotIn("万美元", report)
            self.assertTrue(footer.startswith("<font color='#d4dae2'>┃</font> <font color='#808080'>"))
            self.assertLess(footer.index("顺序：累计→次日→月末"), footer.index("[查看明细]"))
            self.assertNotIn("三值顺序", footer)
            self.assertNotIn("本期7日均环比", footer)
            self.assertNotIn("上期7日均环比", footer)
            self.assertIsNone(re.search(r"(?m)^> ", footer))

    def test_missing_daily_new_users_in_revenue_report_are_not_zero_filled(self):
        latest = date(2026, 9, 27)
        line = PROGRESS.metric_line("新增", {latest - timedelta(days=1): 5.0}, latest)
        self.assertTrue(line.startswith("新增：**—** ｜**—** "))

    def test_mobile_bold_tokens_have_trailing_spaces_and_quote_has_hard_break(self):
        latest = date(2026, 9, 27)
        series = {latest: 2.0, latest - timedelta(days=7): 1.0}
        heading = PROGRESS.partner_heading("Opera GX", series, latest, 1.0)
        self.assertEqual(len(heading.splitlines()), 2)
        self.assertNotIn("累计", heading.splitlines()[0])
        self.assertNotIn("%", heading.splitlines()[1])
        self.assertIn("</font>  \n> ", heading)
        self.assertIn("**Opera GX** · 9/27｜**", heading)
        for text in (heading, PROGRESS.metric_line("新增", series, latest),
                     PROGRESS.metric_line("血量", {}, latest)):
            matches = list(re.finditer(r"\*\*(.*?)\*\*(.)", text))
            self.assertTrue(matches)
            self.assertTrue(all(match.group(2) == " " for match in matches))
            self.assertTrue(all("9/27" not in match.group(1) for match in matches))


    def test_target_config_finds_named_blocks_after_leading_rows(self):
        rows = [
            [{"formattedValue": "\u8bf4\u660e"}],
            [{}, {"formattedValue": "\u5408\u4f5c\u65b9\u9884\u7b97\u76ee\u6807"}, {}, {"formattedValue": "\u5408\u4f5c\u65b9\u65b0\u589e\u76ee\u6807"}],
            [{"formattedValue": "\u6708\u4efd"}, {"formattedValue": "Avast"}, {"formattedValue": "Opera"}, {"formattedValue": "360"}],
            [
                {"formattedValue": "7\u6708"},
                {"effectiveValue": {"numberValue": 10.7}},
                {"effectiveValue": {"numberValue": 6.0}},
                {"effectiveValue": {"numberValue": 60.0}},
            ],
        ]
        targets = PROGRESS.target_config(rows, 7)
        self.assertEqual(targets["Avast"]["target_metric"], "revenue")
        self.assertEqual(targets["Avast"]["target"], 10.7)
        self.assertEqual(targets["360"]["target_metric"], "new")
        self.assertEqual(targets["360"]["target"], 60.0)

if __name__ == "__main__":
    unittest.main()
