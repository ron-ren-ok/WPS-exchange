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

    def test_monthly_targets_find_headers_dynamically(self):
        rows = [
            [{"formattedValue": "说明"}],
            [{"formattedValue": "我方新增目标"}, {"formattedValue": "月份"}, {"formattedValue": "当月目标"}],
            [
                {"effectiveValue": {"numberValue": 60}},
                {"formattedValue": "7月"},
                {"effectiveValue": {"numberValue": 23}},
            ],
        ]
        self.assertEqual(DAILY.monthly_targets(rows, 7), {"血量": 23.0, "360新增": 60.0})


if __name__ == "__main__":
    unittest.main()
