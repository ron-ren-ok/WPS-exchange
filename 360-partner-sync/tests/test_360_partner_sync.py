import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "360_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("sync360", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class Sync360Tests(unittest.TestCase):
    def test_parses_three_source_headers_and_skips_summary(self):
        values = [
            ["日期", "360-1", "360-2", "360-3"],
            ["汇总", 100, 200, 3],
            ["2026-07-14", 10, 20, 3],
            ["2026-07-15", 0, "", 4],
        ]
        records = SYNC.source_records(values, date(2026, 7, 14), date(2026, 7, 15))
        self.assertEqual(records[(date(2026, 7, 14), "360", "换量弹窗")]["new_users"], 10)
        self.assertEqual(records[(date(2026, 7, 14), "360", "气泡")]["new_users"], 20)
        self.assertEqual(records[(date(2026, 7, 15), "360", "换量弹窗")]["new_users"], 0)
        self.assertNotIn((date(2026, 7, 15), "360", "气泡"), records)

    def test_keeps_available_source_records_when_some_metrics_are_blank(self):
        values = [
            ["日期", "360-1", "360-2", "360-3"],
            ["2026-07-08", 10, 20, ""],
        ]
        records = SYNC.source_records(values, date(2026, 7, 8), date(2026, 7, 8))
        required = {
            (date(2026, 7, 8), "360", "换量弹窗"),
            (date(2026, 7, 8), "360", "气泡"),
            (date(2026, 7, 8), "360", "卸载后引导H5"),
        }
        self.assertEqual(set(records), required - {(date(2026, 7, 8), "360", "卸载后引导H5")})

    def test_plans_append_without_writing_blood_volume(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        updates, appends, overwrites, skipped_conflicts = SYNC.plan_writes(
            headers, {}, {(date(2026, 7, 14), "360", "换量弹窗"): {"new_users": 10}}, False
        )
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(skipped_conflicts, [])
        self.assertEqual(appends, [{"日期": date(2026, 7, 14), "合作方": "360", "运营位": "换量弹窗", "新增": 10}])

    def test_updates_existing_new_users_only(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 7, 14), "360", "气泡")
        rows = {key: {"row": 99, "values": [46217, "360", "气泡", 8, ""]}}
        updates, appends, overwrites, skipped_conflicts = SYNC.plan_writes(headers, rows, {key: {"new_users": 9}}, True)
        self.assertEqual(appends, [])
        self.assertEqual(len(updates), 1)
        self.assertIn("D99", updates[0]["range"])
        self.assertEqual(len(overwrites), 1)
        self.assertEqual(skipped_conflicts, [])


    def test_skips_existing_different_value_by_default(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 7, 14), "360", "气泡")
        rows = {key: {"row": 99, "values": [46217, "360", "气泡", 8, ""]}}
        updates, appends, overwrites, skipped_conflicts = SYNC.plan_writes(
            headers, rows, {key: {"new_users": 9}}
        )
        self.assertEqual(updates, [])
        self.assertEqual(appends, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(len(skipped_conflicts), 1)

    def test_plans_only_missing_or_blank_records(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        day = date(2026, 7, 14)
        existing = {
            (day, "360", "换量弹窗"): {"row": 2, "values": [46217, "360", "换量弹窗", 10, ""]},
            (day, "360", "气泡"): {"row": 3, "values": [46217, "360", "气泡", "", ""]},
        }
        missing = SYNC.missing_keys(headers, existing, day, day, explicit_start=day)
        self.assertEqual(missing, {
            (day, "360", "气泡"),
            (day, "360", "卸载后引导H5"),
        })

    def test_default_only_checks_recent_window_and_new_days(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        end = date(2026, 7, 31)
        old = date(2026, 1, 1)
        existing = {
            (end - timedelta(days=1), "360", "换量弹窗"): {"row": 2, "values": [46234, "360", "换量弹窗", 10, ""]},
            (end - timedelta(days=1), "360", "气泡"): {"row": 3, "values": [46234, "360", "气泡", 10, ""]},
        }
        missing = SYNC.missing_keys(headers, existing, old, end)
        self.assertNotIn((old + timedelta(days=1), "360", "卸载后引导H5"), missing)
        self.assertIn((end - timedelta(days=13), "360", "卸载后引导H5"), missing)
        self.assertIn((end, "360", "换量弹窗"), missing)
        self.assertIn((end, "360", "气泡"), missing)

if __name__ == "__main__":
    unittest.main()