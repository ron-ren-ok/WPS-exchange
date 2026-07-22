import importlib.util
import unittest
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "winriser_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("winriser", MODULE)
WINRISER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WINRISER)


class WinriserTests(unittest.TestCase):
    def test_plans_append_for_new_bubble_record(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        updates, appends, overwrites = WINRISER.plan_writes(
            headers, {}, {date(2026, 7, 14): {"new_users": 12, "blood_volume": 3.5}}, False
        )
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(appends, [{"日期": date(2026, 7, 14), "合作方": "Winriser", "运营位": "气泡", "新增": 12, "血量": 3.5}])

    def test_updates_existing_bubble_record(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        rows = {(date(2026, 7, 14), "Winriser", "气泡"): {"row": 99, "values": [46217, "Winriser", "气泡", 10, 2]}}
        updates, appends, overwrites = WINRISER.plan_writes(
            headers, rows, {date(2026, 7, 14): {"new_users": 11, "blood_volume": 2}}, True
        )
        self.assertEqual(appends, [])
        self.assertEqual(len(updates), 1)
        self.assertIn("D99", updates[0]["range"])
        self.assertEqual(len(overwrites), 1)


if __name__ == "__main__":
    unittest.main()