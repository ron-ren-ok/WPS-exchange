import importlib.util
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "send_partner_health_alert.py"
SPEC = importlib.util.spec_from_file_location("partner_health_alert", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def data_row(day, partner, new_users=None, d1=None, uninstall=None):
    return MODULE.DataRow(date.fromisoformat(day), partner, {"new_users": new_users, "d1": d1, "uninstall": uninstall})


def text_cell(value):
    return {"formattedValue": value}


def number_cell(value):
    return {"formattedValue": str(value), "effectiveValue": {"numberValue": value}}


class PartnerHealthAlertTests(unittest.TestCase):
    def test_each_rule_requires_absolute_and_relative_thresholds(self):
        new_rule, d1_rule, uninstall_rule = MODULE.RULES
        self.assertIsNone(MODULE.alert_direction(1_150, 1_000, new_rule))
        self.assertIsNone(MODULE.alert_direction(120, 100, new_rule))
        self.assertEqual(MODULE.alert_direction(1_250, 1_000, new_rule), "上涨")
        self.assertIsNone(MODULE.alert_direction(0.171, 0.20, d1_rule))
        self.assertEqual(MODULE.alert_direction(0.16, 0.20, d1_rule), "下跌")
        self.assertIsNone(MODULE.alert_direction(0.33, 0.30, uninstall_rule))
        self.assertEqual(MODULE.alert_direction(0.40, 0.30, uninstall_rule), "上涨")

    def test_zero_is_valid_and_nonzero_change_has_infinite_relative_change(self):
        self.assertEqual(MODULE.alert_direction(100, 0, MODULE.RULES[0]), "上涨")
        self.assertEqual(MODULE.relative_change(0, 0), 0)

    def test_same_direction_continuous_alert_is_suppressed(self):
        rows = [
            data_row("2026-08-15", "A", new_users=990), data_row("2026-08-16", "A", new_users=1_000),
            data_row("2026-08-22", "A", new_users=1_240), data_row("2026-08-23", "A", new_users=1_250),
        ]
        _, alerts, _ = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertEqual(alerts, [])

    def test_direction_change_creates_a_new_alert(self):
        rows = [
            data_row("2026-08-15", "A", new_users=990), data_row("2026-08-16", "A", new_users=1_000),
            data_row("2026-08-22", "A", new_users=1_240), data_row("2026-08-23", "A", new_users=700),
        ]
        _, alerts, _ = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertEqual([(item.partner, item.metric, item.direction) for item in alerts], [("A", "new_users", "下跌")])

    def test_majority_same_direction_is_data_anomaly_not_partner_alert(self):
        rows = []
        for partner, baseline, current in (("A", 1_000, 1_300), ("B", 1_000, 1_400), ("C", 1_000, 1_050)):
            rows.extend([data_row("2026-08-16", partner, new_users=baseline), data_row("2026-08-23", partner, new_users=current)])
        _, alerts, anomalies = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertFalse(any(alert.metric == "new_users" for alert in alerts))
        self.assertTrue(any("2/3 个可比较合作方同时异常上涨" in item for item in anomalies))

    def test_terabox_latest_values_do_not_trigger(self):
        rows = [
            data_row("2026-08-15", "Terabox", d1=0.123655914),
            data_row("2026-08-16", "Terabox", new_users=3231, uninstall=0.3302383163),
            data_row("2026-08-22", "Terabox", d1=0.1003572589),
            data_row("2026-08-23", "Terabox", new_users=3029, uninstall=0.3489600528),
        ]
        latest_dates, alerts, anomalies = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertEqual(alerts, [])
        self.assertEqual(anomalies, [])
        self.assertEqual(latest_dates["new_users"], date(2026, 8, 23))
        self.assertEqual(latest_dates["d1"], date(2026, 8, 22))

    def test_missing_expected_date_is_reported(self):
        rows = [data_row("2026-08-20", "A", new_users=100)]
        _, _, anomalies = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertTrue(any("新增缺少应有日期 2026-08-23" in item for item in anomalies))

    def test_partial_newer_d1_is_ignored_until_mature(self):
        rows = [
            data_row("2026-08-15", "A", d1=0.20), data_row("2026-08-16", "A", d1=0.20),
            data_row("2026-08-22", "A", d1=0.16), data_row("2026-08-23", "A", d1=0.05),
        ]
        latest_dates, alerts, _ = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertEqual(latest_dates["d1"], date(2026, 8, 22))
        self.assertEqual([(item.metric, item.current_date, item.direction) for item in alerts], [("d1", date(2026, 8, 22), "下跌")])

    def test_one_partner_missing_current_data_is_reported(self):
        rows = [
            data_row("2026-08-16", "A", new_users=1_000), data_row("2026-08-16", "B", new_users=900),
            data_row("2026-08-23", "A", new_users=1_050),
        ]
        _, _, anomalies = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertTrue(any("新增缺少合作方数据：B" in item for item in anomalies))

    def test_historical_formula_issue_does_not_repeat_forever(self):
        old_issue = (date(2026, 1, 1), "旧公式错误")
        messages = [message for issue_date, message in [old_issue] if issue_date in MODULE.relevant_dates(date(2026, 8, 24))]
        self.assertEqual(messages, [])

    def test_parse_rows_uses_data_extract_headers(self):
        headers = ["日期", "合作方", "新增设备数", "次日留存率", "7日留存率", "大盘次日留存率", "大盘7日留存率", "当日卸载设备数", "当日卸载率", "大盘当日卸载率"]
        raw_rows = [
            [text_cell(value) for value in headers],
            [text_cell("2026-08-23"), text_cell("Terabox"), number_cell(3029), {}, {}, {}, {}, number_cell(1057), number_cell(0.3489600528), number_cell(0.07283230358)],
        ]
        rows, issues = MODULE.parse_rows(raw_rows)
        self.assertEqual(issues, [])
        self.assertEqual(rows[0].data_date, date(2026, 8, 23))
        self.assertEqual(rows[0].values, {"new_users": 3029.0, "d1": None, "uninstall": 0.3489600528})

    def test_markdown_uses_real_double_newlines_for_visual_lines(self):
        alert = MODULE.Alert("CAD", "d1", "下跌", date(2026, 8, 22), date(2026, 8, 15), 0.15, 0.20, -0.05, -0.25)
        result = MODULE.alert_markdown({"d1": date(2026, 8, 22)}, [alert], [])
        self.assertIn("\n\n- 当前", result)
        self.assertIn("\n\n- 上周同日", result)
        self.assertIn("\n\n- 变化：绝对值 -5.0%；环比 -25.0%", result)
        self.assertIn("\n\n- 近14天趋势：", result)
        self.assertNotIn("\n- 当前", result.replace("\n\n- 当前", ""))


    def test_sparkline_renders_fourteen_points(self):
        result = MODULE.sparkline([float(value) for value in range(14)])
        self.assertEqual(len(result), 14)
        self.assertEqual(result[0], "▁")
        self.assertEqual(result[-1], "█")

    def test_partner_trend_keeps_missing_days_visible(self):
        end_date = date(2026, 8, 23)
        rows = [
            data_row((end_date - timedelta(days=offset)).isoformat(), "A", new_users=float(offset))
            for offset in range(0, 14, 2)
        ]
        index = {(row.data_date, row.partner): row for row in rows}
        result = MODULE.partner_trend(index, "A", "new_users", end_date)
        self.assertEqual(len(result), 14)
        self.assertIn("·", result)


if __name__ == "__main__":
    unittest.main()
