import importlib.util
import unittest
from datetime import date
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

    def test_plans_append_without_writing_blood_volume(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        updates, appends, overwrites = SYNC.plan_writes(
            headers, {}, {(date(2026, 7, 14), "360", "换量弹窗"): {"new_users": 10}}, False
        )
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(appends, [{"日期": date(2026, 7, 14), "合作方": "360", "运营位": "换量弹窗", "新增": 10}])

    def test_updates_existing_new_users_only(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 7, 14), "360", "气泡")
        rows = {key: {"row": 99, "values": [46217, "360", "气泡", 8, ""]}}
        updates, appends, overwrites = SYNC.plan_writes(headers, rows, {key: {"new_users": 9}}, True)
        self.assertEqual(appends, [])
        self.assertEqual(len(updates), 1)
        self.assertIn("D99", updates[0]["range"])
        self.assertEqual(len(overwrites), 1)


if __name__ == "__main__":
    unittest.main()