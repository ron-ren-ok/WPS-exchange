import importlib.util
import unittest
from datetime import date
from email.message import EmailMessage
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "src" / "ccleaner_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("ccleaner", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)

PAGE = """WPS - CC - Toast - Installs
Split by Date & Geo
Country Code 2026-08-18 2026-08-19 2026-08-20 Total
BR 186 122 308
Total 2,203 721 4 2,928
Country Code 2026-08-18 2026-08-19 2026-08-20 Total
Total $0 $522 $94 $616
"""


class CCleanerPartnerSyncTests(unittest.TestCase):
    def test_parses_daily_install_total(self):
        self.assertEqual(SYNC.parse_ccleaner_page(PAGE), {
            date(2026, 8, 18): {"new_users": 2203, "blood_volume": 0},
            date(2026, 8, 19): {"new_users": 721, "blood_volume": 522},
            date(2026, 8, 20): {"new_users": 4, "blood_volume": 94},
        })

    def test_accepts_google_sheets_date_serial_numbers(self):
        self.assertEqual(SYNC.parse_day(45925), date(2025, 9, 25))

    def test_selects_anchor_and_previous_two_consecutive_days(self):
        records = {
            date(2026, 8, 21): {"new_users": 1},
            date(2026, 8, 22): {"new_users": 2},
            date(2026, 8, 23): {"new_users": 3},
            date(2026, 8, 24): {"new_users": 4},
        }
        selected, start, end = SYNC.latest_three_days(records)
        self.assertEqual((start, end), (date(2026, 8, 22), date(2026, 8, 24)))
        self.assertEqual(set(selected), {date(2026, 8, 22), date(2026, 8, 23), date(2026, 8, 24)})

    def test_skips_missing_days_in_the_default_three_day_window(self):
        records = {date(2026, 8, 20): {"new_users": 1}, date(2026, 8, 24): {"new_users": 4}}
        selected, start, end = SYNC.latest_three_days(records)
        self.assertEqual((start, end), (date(2026, 8, 22), date(2026, 8, 24)))
        self.assertEqual(selected, {date(2026, 8, 24): {"new_users": 4}})

    def test_uses_an_explicit_start_and_end_date(self):
        records = {date(2026, 8, 20): {"new_users": 1}, date(2026, 8, 22): {"new_users": 2}, date(2026, 8, 24): {"new_users": 4}}
        selected, start, end = SYNC.latest_three_days(records, start=date(2026, 8, 20), end=date(2026, 8, 24))
        self.assertEqual((start, end), (date(2026, 8, 20), date(2026, 8, 24)))
        self.assertEqual(set(selected), set(records))

    def test_overwrites_existing_new_users_and_preserves_blood_volume(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        day = date(2026, 8, 23)
        existing = {(day, "CCleaner", "气泡"): {"row": 9, "values": [46258, "CCleaner", "气泡", 1, 3.5]}}
        updates, appends, overwrites = SYNC.plan_writes(headers, existing, {day: {"new_users": 4}})
        self.assertEqual(appends, [])
        self.assertEqual(updates, [{"range": "'合作方新增血量'!D9", "values": [[4]]}])
        self.assertEqual(len(overwrites), 1)

    def test_overwrites_existing_blood_volume_when_reported(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        day = date(2026, 8, 23)
        existing = {(day, "CCleaner", "气泡"): {"row": 9, "values": [46258, "CCleaner", "气泡", 1, 3.5]}}
        updates, appends, overwrites = SYNC.plan_writes(headers, existing, {day: {"new_users": 4, "blood_volume": 5}})
        self.assertEqual(appends, [])
        self.assertEqual(updates, [{"range": "'合作方新增血量'!D9", "values": [[4]]}, {"range": "'合作方新增血量'!E9", "values": [[5]]}])
        self.assertEqual(len(overwrites), 2)
    def test_appends_without_unreported_blood_volume(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        day = date(2026, 8, 23)
        _, appends, _ = SYNC.plan_writes(headers, {}, {day: {"new_users": 4}})
        self.assertEqual(appends, [{"日期": day, "合作方": "CCleaner", "运营位": "气泡", "新增": 4}])

    def test_accepts_avast_style_forwarded_report(self):
        message = EmailMessage()
        message["From"] = "partner@wps.com"
        message.set_content("Forwarded message from no-reply-powerbi@microsoft.com")
        self.assertTrue(SYNC.verified_sender(message))


if __name__ == "__main__":
    unittest.main()
