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
    def test_data_anomaly_and_red_alert_both_trigger_by_header_name(self):
        headers = ["合作方", "昨日新增", "次留", "较大盘", "7留", "较大盘", "昨日卸载率", "较大盘", "次留较近4个同星期", "7留较近4个同星期", "卸载较近4个同星期", "预警等级", "预警原因", "数据异常原因"]
        data = [
            ["360", "26,010", "19.9%", "-29.6%", "10.8%", "-29.1%", "0.008%", "-99.3%", "-5.9%", "9.1%", "-99.8%", "数据异常", "卸载率低于5%", "卸载率低于5%"],
            ["Terabox", "2,144", "11.9%", "-57.9%", "6.3%", "-58.8%", "0.047%", "-95.5%", "-20.5%", "-6.5%", "-99.7%", "红色预警", "次留低于大盘58%", ""],
        ]
        rows = [[], [], [], [{"formattedValue": value} for value in headers]]
        rows.extend([[{"formattedValue": value} for value in row] for row in data])
        partners = MODULE.triggered_partners(rows)
        self.assertEqual([partner["name"] for partner in partners], ["360", "Terabox"])
        self.assertEqual(partners[0]["data_reason"], "卸载率低于5%")