import importlib.util
import unittest
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "send_daily_progress.py"
SPEC = importlib.util.spec_from_file_location("daily_progress", MODULE)
DAILY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DAILY)


class DailyProgressTests(unittest.TestCase):
    def test_long_records_uses_header_names_not_positions(self):
        rows = [
            [{"formattedValue": "运营位"}, {"formattedValue": "血量"}, {"formattedValue": "合作方"}, {"formattedValue": "日期"}, {"formattedValue": "新增"}],
            [
                {"formattedValue": "气泡"},
                {"effectiveValue": {"numberValue": 250}},
                {"formattedValue": "NewPartner"},
                {"effectiveValue": {"numberValue": 46217}},
                {"effectiveValue": {"numberValue": 1000}},
            ],
        ]
        records = DAILY.long_records(rows)
        self.assertEqual(records[0]["partner"], "NewPartner")
        self.assertEqual(records[0]["operation"], "气泡")
        self.assertEqual(records[0]["新增"], 1000)
        self.assertEqual(records[0]["血量"], 250)

    def test_report_auto_includes_new_partner_and_operation_for_revenue(self):
        records = [
            {"date": date(2026, 7, 1), "partner": "360", "operation": "换量弹窗", "新增": 10000, "血量": None},
            {"date": date(2026, 7, 2), "partner": "360", "operation": "气泡", "新增": 20000, "血量": None},
            {"date": date(2026, 7, 1), "partner": "NewPartner", "operation": "H5", "新增": 3000, "血量": 5000},
            {"date": date(2026, 7, 2), "partner": "Existing", "operation": "气泡", "新增": 4000, "血量": 10000},
        ]
        daily, forecast = DAILY.report(records, {"血量": 10, "360新增": 10}, date(2026, 7, 2))
        self.assertIn("累计完成 3.00", daily)
        self.assertIn("360 \u7d2f\u8ba1\u5b8c\u6210 3.00", daily)
        self.assertIn("NewPartner\u6570\u636e\u4e0d\u5168", daily)
        self.assertNotIn("\u6570\u636e\u72b6\u6001", forecast)
        self.assertIn("预计本月目标可达成", forecast)

    def test_monthly_targets_use_column_b_and_360_target_block(self):
        rows = [
            [{}, {}, {"formattedValue": "\u5408\u4f5c\u65b9\u65b0\u589e\u76ee\u6807"}],
            [{"formattedValue": "\u6708\u4efd"}, {"formattedValue": "\u65e7\u65b0\u589e\u76ee\u6807"}, {"formattedValue": "360"}],
            [
                {"formattedValue": "7\u6708"},
                {"effectiveValue": {"numberValue": 23}},
                {"effectiveValue": {"numberValue": 60}},
            ],
        ]
        self.assertEqual(DAILY.monthly_targets(rows, 7), {"\u8840\u91cf": 23.0, "360\u65b0\u589e": 60.0})


    def test_report_keeps_blood_card_when_360_has_not_returned(self):
        records = [
            {"date": date(2026, 8, 1), "partner": "Avast", "operation": "\u6c14\u6ce1", "\u65b0\u589e": 1000, "\u8840\u91cf": 10000},
            {"date": date(2026, 8, 2), "partner": "Avast", "operation": "\u6c14\u6ce1", "\u65b0\u589e": 2000, "\u8840\u91cf": 20000},
        ]
        daily, forecast = DAILY.report(records, {"\u8840\u91cf": 23, "360\u65b0\u589e": 60}, date(2026, 8, 2))
        self.assertIn("360 \u65b0\u589e\uff1a\u5f53\u6708\u6682\u672a\u56de\u4f20", daily)
        self.assertIn("360\u6570\u636e\u4e0d\u5168", daily)
        self.assertIn("360 \u65b0\u589e\u76ee\u6807\u9884\u6d4b\uff1a\u5f53\u6708\u6682\u672a\u56de\u4f20\uff0c\u6682\u4e0d\u6d4b\u7b97", forecast)


    def test_report_includes_partner_level_blood_details(self):
        records = [
            {"date": date(2026, 8, 1), "partner": "Avast", "operation": "bubble", "\u65b0\u589e": 1000, "\u8840\u91cf": 5000},
            {"date": date(2026, 8, 2), "partner": "Opera", "operation": "popup", "\u65b0\u589e": 2000, "\u8840\u91cf": 10000},
            {"date": date(2026, 8, 2), "partner": "360", "operation": "bubble", "\u65b0\u589e": 3000, "\u8840\u91cf": None},
        ]
        daily, forecast = DAILY.report(records, {"\u8840\u91cf": 25, "360\u65b0\u589e": 60}, date(2026, 8, 2))
        self.assertIn("\u5408\u4f5c\u65b9\u660e\u7ec6", daily)
        self.assertIn("Avast 0.50\u4e07\u7f8e\u5143", daily)
        self.assertIn("Opera 1.00\u4e07\u7f8e\u5143", daily)
        self.assertIn("\u5408\u4f5c\u65b9\u9884\u6d4b", forecast)

if __name__ == "__main__":
    unittest.main()
