import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "send_partner_health_alert.py"
SPEC = importlib.util.spec_from_file_location("partner_health_alert", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PartnerHealthAlertTests(unittest.TestCase):
    def test_red_partner_markdown_matches_approved_template(self):
        partner = {
            "name": "360", "level": "红色预警", "reason": "次留低于大盘33%", "new_users": "25,476",
            "d1": "14.9%", "d1_market": "-33.4%", "d1_baseline": "-6.4%",
            "d7": "11.3%", "d7_market": "-23.9%", "d7_baseline": "14.1%",
            "uninstall": "0.004%", "uninstall_market": "-99.6%", "uninstall_baseline": "-99.9%",
        }
        result = MODULE.alert_markdown("2026-07-20", [partner], ["大盘指标公式错误：次日留存率"])
        self.assertIn("# 🚨 三方换量用户健康度预警", result)
        self.assertIn("## 360：红色预警 | 次留低于大盘33%", result)
        self.assertIn("- 数据异常原因：大盘指标公式错误：次日留存率", result)

    def test_data_anomaly_without_red_partner_has_system_block(self):
        result = MODULE.alert_markdown("2026-07-20", [], ["数据最新日期滞后 2 天"])
        self.assertIn("## 数据总览：数据异常 | 未发现红色预警", result)
        self.assertIn("- 数据异常原因：数据最新日期滞后 2 天", result)

    def test_formula_errors_only_collect_error_values(self):
        rows = [
            [{"formattedValue": "次日留存率"}, {"formattedValue": "#REF!"}],
            [{"formattedValue": "7日留存率"}, {"formattedValue": "12.0%"}],
        ]
        self.assertEqual(MODULE.formula_errors(rows), ["次日留存率"])