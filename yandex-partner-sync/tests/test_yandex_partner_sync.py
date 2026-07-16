import importlib.util
import unittest
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "yandex_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("sync", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class SyncTests(unittest.TestCase):
    def test_dynamic_column_names(self):
        self.assertEqual(SYNC.column_name(0), "A")
        self.assertEqual(SYNC.column_name(25), "Z")
        self.assertEqual(SYNC.column_name(26), "AA")

    def test_nonblank_difference_requires_explicit_override(self):
        headers = ["日期", "Yandex换量弹窗新增", "Yandex换量弹窗血量"]
        rows = {date(2026, 7, 14): {"row": 294, "values": ["2026/7/14", "146", "146"]}}
        profile = {"target_headers": ["Yandex换量弹窗新增", "Yandex换量弹窗血量"]}
        source = {date(2026, 7, 14): {"new_users": 178, "blood_volume": 178}}
        with self.assertRaises(RuntimeError):
            SYNC.planned_updates(headers, rows, source, profile, False)
        self.assertEqual(len(SYNC.planned_updates(headers, rows, source, profile, True)), 2)


    def test_missing_data_backfill_starts_at_earliest_gap(self):
        headers = ["日期", "Yandex换量弹窗新增", "Yandex换量弹窗血量", "Yandex气泡新增", "Yandex气泡血量"]
        profiles = [{"target_headers": headers[1:3]}, {"target_headers": headers[3:5]}]
        rows = {
            date(2026, 7, 13): {"values": ["2026/7/13", "", "", "153", "153"]},
            date(2026, 7, 14): {"values": ["2026/7/14", "178", "178", "158", "158"]},
        }
        self.assertEqual(SYNC.first_missing_day(headers, rows, profiles, date(2026, 7, 15)), date(2026, 7, 13))
if __name__ == "__main__":
    unittest.main()
