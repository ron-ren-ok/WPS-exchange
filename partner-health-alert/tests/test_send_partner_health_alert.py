import importlib.util
from datetime import date
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "send_partner_health_alert.py"
SPEC = importlib.util.spec_from_file_location("partner_health_alert", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def data_row(day, partner, new_users=None, d1=None, uninstall=None):
    return MODULE.DataRow(
        date.fromisoformat(day),
        partner,
        {"new_users": new_users, "d1": d1, "uninstall": uninstall},
    )


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
            data_row("2026-08-15", "A", new_users=990),
            data_row("2026-08-16", "A", new_users=1_000),
            data_row("2026-08-22", "A", new_users=1_240),
            data_row("2026-08-23", "A", new_users=1_250),
        ]
        _, alerts, _ = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertEqual(alerts, [])

    def test_direction_change_creates_a_new_alert(self):
        rows = [
            data_row("2026-08-15", "A", new_users=990),
            data_row("2026-08-16", "A", new_users=1_000),
            data_row("2026-08-22", "A", new_users=1_240),
            data_row("2026-08-23", "A", new_users=700),
        ]
        _, alerts, _ = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertEqual([(item.partner, item.metric, item.direction) for item in alerts], [("A", "new_users", "下跌")])

    def test_majority_same_direction_is_data_anomaly_not_partner_alert(self):
        rows = []
        for partner, baseline, current in (("A", 1_000, 1_300), ("B", 1_000, 1_400), ("C", 1_000, 1_050)):
            rows.extend([
                data_row("2026-08-16", partner, new_users=baseline),
                data_row("2026-08-23", partner, new_users=current),
            ])
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

    def test_stale_data_is_reported(self):
        rows = [data_row("2026-08-20", "A", new_users=100)]
        _, _, anomalies = MODULE.analyze(rows, date(2026, 8, 24))
        self.assertTrue(any("新增数据滞后 4 天" in item for item in anomalies))

    def test_markdown_uses_real_double_newlines_for_visual_lines(self):
        alert = MODULE.Alert(
            "CAD", "d1", "下跌", date(2026, 8, 22), date(2026, 8, 15),
            0.15, 0.20, -0.05, -0.25,
        )
        result = MODULE.alert_markdown({"d1": date(2026, 8, 22)}, [alert], [])
        self.assertIn("\n\n- 当前", result)
        self.assertIn("\n\n- 上周同日", result)
        self.assertIn("\n\n- 变化", result)
        self.assertNotIn("\n- 当前", result.replace("\n\n- 当前", ""))


if __name__ == "__main__":
    unittest.main()
