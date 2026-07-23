import importlib.util
import unittest
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "winriser_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("winriser", MODULE)
WINRISER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WINRISER)


class WinriserTests(unittest.TestCase):
    def test_resolves_child_source_selector_values(self):
        soup = WINRISER.BeautifulSoup("""
            <select><option value="0">All Source</option><option value="6888">wnrwpsofc - Winriser</option><option value="7777">wnrwpsofc_exchange</option></select>
        """, "html.parser")
        self.assertEqual(WINRISER.source_option_value(soup.select_one("select"), "wnrwpsofc"), "6888")
        self.assertEqual(WINRISER.source_option_value(soup.select_one("select"), "wnrwpsofc_exchange"), "7777")

    def test_parses_only_mapped_wps_child_sources(self):
        html = """
        <table><tr><th></th><th>Date</th><th>Source</th><th>Install Count</th><th>Spend-PPI($)</th></tr>
        <tr><td>-</td><td>2026-07-14</td><td>WPS</td><td>100</td><td>50</td></tr>
        <tr><td>-</td><td>2026-07-14</td><td>wnrwpsofc</td><td>12</td><td>3.5</td></tr>
        <tr><td>-</td><td>2026-07-14</td><td>wnrwpsofc_exchange</td><td>8</td><td>4</td></tr>
        <tr><td>-</td><td>2026-07-14</td><td>wnrwpsofc2</td><td>80</td><td>40</td></tr></table>
        """
        rows = WINRISER.parse_report(html, date(2026, 7, 14))
        self.assertEqual(rows, {
            (date(2026, 7, 14), "气泡"): {"new_users": 12, "blood_volume": 3.5},
            (date(2026, 7, 14), "换量弹窗"): {"new_users": 8, "blood_volume": 4},
        })

    def test_plans_append_for_bubble_and_exchange_records(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        source = {
            (date(2026, 7, 14), "气泡"): {"new_users": 12, "blood_volume": 3.5},
            (date(2026, 7, 14), "换量弹窗"): {"new_users": 8, "blood_volume": 4},
        }
        updates, appends, overwrites = WINRISER.plan_writes(headers, {}, source, False)
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertCountEqual(appends, [
            {"日期": date(2026, 7, 14), "合作方": "Winriser", "运营位": "气泡", "新增": 12, "血量": 3.5},
            {"日期": date(2026, 7, 14), "合作方": "Winriser", "运营位": "换量弹窗", "新增": 8, "血量": 4},
        ])

    def test_updates_existing_exchange_record(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        rows = {(date(2026, 7, 14), "Winriser", "换量弹窗"): {"row": 99, "values": [46217, "Winriser", "换量弹窗", 10, 2]}}
        source = {(date(2026, 7, 14), "换量弹窗"): {"new_users": 11, "blood_volume": 2}}
        updates, appends, overwrites = WINRISER.plan_writes(headers, rows, source, True)
        self.assertEqual(appends, [])
        self.assertEqual(len(updates), 1)
        self.assertIn("D99", updates[0]["range"])
        self.assertEqual(len(overwrites), 1)


if __name__ == "__main__":
    unittest.main()