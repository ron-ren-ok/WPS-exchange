import importlib.util
import unittest
from email.message import EmailMessage
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "avast_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("avast", MODULE)
AVAST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AVAST)

PAGE = """Split by Date & Geo
Country Code 2026-07-14 2026-07-15 Grand Total
RU 178 173 351
Total 178 173 351
Total $178 $173 $351
Costs / Installations / CPI
Total 999 999 999
"""


class AvastTests(unittest.TestCase):
    def test_parses_first_non_dollar_total_and_next_dollar_total(self):
        self.assertEqual(AVAST.parse_avast_page(PAGE), {
            date(2026, 7, 14): {"new_users": 178, "blood_volume": 178},
            date(2026, 7, 15): {"new_users": 173, "blood_volume": 173},
        })

    def test_rejects_reordered_totals(self):
        bad = PAGE.replace("Total 178 173 351\nTotal $178 $173 $351", "Total $178 $173 $351\nTotal 178 173 351")
        with self.assertRaisesRegex(ValueError, "immediately follow"):
            AVAST.parse_avast_page(bad)

    def test_accepts_repeated_country_headers_for_two_pbi_tables(self):
        repeated = PAGE.replace(
            "Total $178 $173 $351",
            "Country Code 2026-07-14 2026-07-15 Grand Total\nTotal $178 $173 $351",
        )
        self.assertEqual(AVAST.parse_avast_page(repeated)[date(2026, 7, 14)]["new_users"], 178)
    def test_plans_append_for_new_h5_long_format_record(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        updates, appends, overwrites = AVAST.plan_writes(
            headers,
            {},
            {"uninstall_h5": {date(2026, 7, 21): {"new_users": 12, "blood_volume": 3.5}}},
            allow_overwrite=False,
        )
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(appends, [{
            "日期": date(2026, 7, 21),
            "合作方": "Avast",
            "运营位": "卸载后引导H5",
            "新增": 12,
            "血量": 3.5,
        }])

    def test_updates_existing_long_format_record(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 7, 21), "Avast", "气泡")
        rows = {key: {"row": 99, "values": [46224, "Avast", "气泡", 10, 2]}}
        updates, appends, overwrites = AVAST.plan_writes(
            headers,
            rows,
            {"bubble": {date(2026, 7, 21): {"new_users": 11, "blood_volume": 2}}},
            allow_overwrite=True,
        )
        self.assertEqual(appends, [])
        self.assertEqual(len(updates), 1)
        self.assertIn("D99", updates[0]["range"])
        self.assertEqual(len(overwrites), 1)
    def test_accepts_forwarded_message_and_pdf_attachment(self):
        message = EmailMessage()
        message["From"] = "partner@wps.com"
        message.set_content("Forwarded message from no-reply-powerbi@microsoft.com")
        message.add_attachment(b"pdf", maintype="application", subtype="pdf", filename="report.pdf")
        self.assertTrue(AVAST.verified_sender(message))
        self.assertEqual(list(AVAST.attachments(message)), [b"pdf"])
    def test_imap_search_uses_standard_quoted_subject(self):
        class FakeImap:
            def __init__(self):
                self.uid_args = None

            def list(self):
                return "OK", [b'* LIST (\\HasNoChildren \\All) "/" "[Gmail]/All Mail"']

            def select(self, mailbox, readonly):
                self.mailbox = (mailbox, readonly)
                return "OK", [b"0"]

            def uid(self, *args):
                self.uid_args = args
                return "OK", [b""]

        client = FakeImap()
        self.assertEqual(list(AVAST.imap_messages(client, "Avast report")), [])
        self.assertEqual(client.mailbox, ("[Gmail]/All Mail", True))
        self.assertEqual(client.uid_args, ("search", None, "SUBJECT", '"Avast report"'))
    def test_column_names(self):
        self.assertEqual(AVAST.col_name(0), "A")
        self.assertEqual(AVAST.col_name(25), "Z")
        self.assertEqual(AVAST.col_name(26), "AA")


if __name__ == "__main__":
    unittest.main()