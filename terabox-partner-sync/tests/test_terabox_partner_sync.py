import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "terabox_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("syncterabox", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class TeraBoxSyncTests(unittest.TestCase):
    def test_reads_bubble_new_users_from_column_b_and_skips_summary(self):
        values = [["日期", "气泡新增"], ["汇总", 99], ["2026-08-03", 17], ["2026-08-04", 0], ["2026-08-05", ""]]
        records = SYNC.source_records(values, date(2026, 8, 3), date(2026, 8, 5))
        self.assertEqual(records[(date(2026, 8, 3), "Terabox", "气泡")]["new_users"], 17)
        self.assertEqual(records[(date(2026, 8, 4), "Terabox", "气泡")]["new_users"], 0)
        self.assertNotIn((date(2026, 8, 5), "Terabox", "气泡"), records)

    def test_appends_new_users_without_blood_volume(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 8, 3), "Terabox", "气泡")
        updates, appends, overwrites, conflicts = SYNC.plan_writes(headers, {}, {key: {"new_users": 17}})
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(conflicts, [])
        self.assertEqual(appends, [{"日期": date(2026, 8, 3), "合作方": "Terabox", "运营位": "气泡", "新增": 17}])

    def test_existing_blood_volume_is_untouched(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 8, 3), "Terabox", "气泡")
        existing = {key: {"row": 9, "values": [46237, "Terabox", "气泡", "", 3.2]}}
        updates, appends, _, _ = SYNC.plan_writes(headers, existing, {key: {"new_users": 17}})
        self.assertEqual(appends, [])
        self.assertEqual(updates, [{"range": "'合作方新增血量'!D9", "values": [[17]]}])

    def test_default_plan_is_incremental_and_repairs_recent_gap(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        end = date(2026, 8, 5)
        existing = {(date(2026, 8, 4), "Terabox", "气泡"): {"row": 5, "values": [46238, "Terabox", "气泡", 17, ""]}}
        missing = SYNC.missing_keys(headers, existing, date(2026, 1, 1), end)
        self.assertIn((date(2026, 8, 5), "Terabox", "气泡"), missing)
        self.assertIn((date(2026, 7, 23), "Terabox", "气泡"), missing)
        self.assertNotIn((date(2026, 1, 2), "Terabox", "气泡"), missing)


if __name__ == "__main__":
    unittest.main()
