import importlib.util
import unittest
from email.message import EmailMessage
from datetime import date
from pathlib import Path
from unittest.mock import patch

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

    def test_accepts_grand_total_rows(self):
        grand = PAGE.replace("Total 178 173 351", "Grand Total 178 173 351").replace(
            "Total $178 $173 $351",
            "Grand Total $178 $173 $351",
        )
        self.assertEqual(AVAST.parse_avast_page(grand), {
            date(2026, 7, 14): {"new_users": 178, "blood_volume": 178},
            date(2026, 7, 15): {"new_users": 173, "blood_volume": 173},
        })

    def test_accepts_power_bi_glyph_before_total(self):
        decorated = PAGE.replace("Total 178 173 351", "\ue116 Total 178 173 351").replace(
            "Total $178 $173 $351",
            "\ue116 Total $178 $173 $351",
        )
        self.assertEqual(
            AVAST.parse_avast_page(decorated)[date(2026, 7, 15)]["blood_volume"],
            173,
        )
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

    def test_accepts_country_code_header_split_across_lines(self):
        wrapped = PAGE.replace(
            "Country Code 2026-07-14 2026-07-15 Grand Total",
            "Country\nCode   2026-07-14\n2026-07-15   Grand Total",
        )
        self.assertEqual(AVAST.parse_avast_page(wrapped), {
            date(2026, 7, 14): {"new_users": 178, "blood_volume": 178},
            date(2026, 7, 15): {"new_users": 173, "blood_volume": 173},
        })

    def test_recovers_date_header_when_country_code_is_omitted(self):
        omitted = PAGE.replace("Country Code ", "")
        self.assertEqual(
            AVAST.parse_avast_page(omitted)[date(2026, 7, 14)]["new_users"],
            178,
        )

    def test_accepts_us_style_date_headers(self):
        us_dates = PAGE.replace(
            "2026-07-14 2026-07-15",
            "7/14/2026 7/15/2026",
        )
        self.assertEqual(set(AVAST.parse_avast_page(us_dates)), {
            date(2026, 7, 14),
            date(2026, 7, 15),
        })

    def test_recovers_dates_without_table_labels_or_grand_total(self):
        extracted = PAGE.replace(
            "Country Code 2026-07-14 2026-07-15 Grand Total",
            "Report generated 2026-07-01\n2026-07-14 2026-07-15",
        )
        self.assertEqual(set(AVAST.parse_avast_page(extracted)), {
            date(2026, 7, 14),
            date(2026, 7, 15),
        })

    def test_accepts_month_name_date_headers(self):
        named = PAGE.replace(
            "Country Code 2026-07-14 2026-07-15 Grand Total",
            "14 Jul 2026 15 Jul 2026",
        )
        self.assertEqual(set(AVAST.parse_avast_page(named)), {
            date(2026, 7, 14),
            date(2026, 7, 15),
        })
    def test_rejects_page_without_date_header(self):
        bad = PAGE.replace("Country Code 2026-07-14 2026-07-15 Grand Total\n", "")
        with self.assertRaisesRegex(ValueError, "date header"):
            AVAST.parse_avast_page(bad)
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

    def test_maps_e_report_to_document_radar(self):
        spec = AVAST.SURFACES["document_radar"]
        self.assertEqual(spec["subject"], "Avast AV - WPS - E - Daily PBI report")
        self.assertEqual(spec["operation"], "\u6587\u6863\u96f7\u8fbe")
        updates, appends, overwrites = AVAST.plan_writes(
            ["\u65e5\u671f", "\u5408\u4f5c\u65b9", "\u8fd0\u8425\u4f4d", "\u65b0\u589e", "\u8840\u91cf"],
            {},
            {"document_radar": {date(2026, 8, 16): {"new_users": 25, "blood_volume": 8.5}}},
            allow_overwrite=False,
        )
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(appends, [{
            "\u65e5\u671f": date(2026, 8, 16),
            "\u5408\u4f5c\u65b9": "Avast",
            "\u8fd0\u8425\u4f4d": "\u6587\u6863\u96f7\u8fbe",
            "\u65b0\u589e": 25,
            "\u8840\u91cf": 8.5,
        }])

    def test_missing_required_surface_does_not_block_other_surfaces(self):
        requested_day = date(2026, 8, 16)
        with patch.object(AVAST, "imap_messages", return_value=[]) as messages:
            rows = AVAST.source_rows(None, "popup", requested_day, requested_day, requested_day)
        self.assertEqual(rows, {})
        messages.assert_called_once_with(
            None, AVAST.SURFACES["popup"]["subject"], requested_day
        )

    def test_new_surface_accepts_all_pdf_days_without_lower_bound(self):
        reports = {
            date(2026, 8, 14): {"new_users": 401, "blood_volume": 153},
            date(2026, 8, 15): {"new_users": 328, "blood_volume": 103},
        }
        with (
            patch.object(AVAST, "imap_messages", return_value=[object()]),
            patch.object(AVAST, "verified_sender", return_value=True),
            patch.object(AVAST, "attachments", return_value=[b"pdf"]),
            patch.object(AVAST, "pdf_rows", return_value=reports),
        ):
            all_rows = AVAST.source_rows(
                None, "document_radar", None, date(2026, 8, 16), date(2026, 8, 17)
            )
            explicit_rows = AVAST.source_rows(
                None, "document_radar", date(2026, 8, 15), date(2026, 8, 16), date(2026, 8, 17)
            )
        self.assertEqual(all_rows, reports)
        self.assertEqual(set(explicit_rows), {date(2026, 8, 15)})


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
    def test_imap_search_is_limited_to_the_report_date_and_subject(self):
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
        self.assertEqual(list(AVAST.imap_messages(client, "Avast report", date(2026, 7, 26))), [])
        self.assertEqual(client.mailbox, ("[Gmail]/All Mail", True))
        self.assertEqual(client.uid_args, ("search", None, "SENTON", "26-Jul-2026", "SUBJECT", '"Avast report"'))
    def test_imap_fetches_only_the_newest_matching_message(self):
        class FakeImap:
            def list(self):
                return "OK", [b'* LIST (\\HasNoChildren \\All) "/" "[Gmail]/All Mail"']

            def select(self, mailbox, readonly):
                return "OK", [b"0"]

            def uid(self, *args):
                self.calls.append(args)
                if args[0] == "search":
                    return "OK", [b"41 42"]
                return "OK", [(b"42", b"From: no-reply-powerbi@microsoft.com\n\nbody")]

        client = FakeImap()
        client.calls = []
        messages = list(AVAST.imap_messages(client, "Avast report", date(2026, 7, 26)))
        self.assertEqual(len(messages), 1)
        self.assertEqual(client.calls[-1], ("fetch", b"42", "(RFC822)"))
    def test_column_names(self):
        self.assertEqual(AVAST.col_name(0), "A")
        self.assertEqual(AVAST.col_name(25), "Z")
        self.assertEqual(AVAST.col_name(26), "AA")


if __name__ == "__main__":
    unittest.main()