import importlib.util
import unittest
from email.message import EmailMessage
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "opera_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("opera", MODULE)
OPERA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPERA)

PDF_TEXT = """Opera for Computers distribution partner dashboard
Summary table
Day Campaign New Users Revenue
1 2026-07-12 wpstest 11,203 $943.79
2 2026-07-12 wpstest2/opera.exe 10,721 $709.46
3 2026-07-11 wpstest2/opera.exe 10,345 $678.62
4 2026-07-11 wpstest 13,228 $1,083.56
Performance
"""


class OperaTests(unittest.TestCase):
    def test_campaign_mapping(self):
        bubble = OPERA.parse_opera_text(PDF_TEXT, "wpstest")
        popup = OPERA.parse_opera_text(PDF_TEXT, "wpstest2/opera.exe")
        self.assertEqual(bubble[date(2026, 7, 12)], {"new_users": 11203, "blood_volume": 943.79})
        self.assertEqual(popup[date(2026, 7, 11)], {"new_users": 10345, "blood_volume": 678.62})


    def test_gx_utm_content_mapping(self):
        gx = """Summary table
Day Utm Content New Users Revenue
1 2026-08-27 toast 10,626 $1,169.95
2 2026-08-27 bundle 4,321 $456.78
"""
        self.assertEqual(OPERA.parse_opera_gx_text(gx, "toast")[date(2026, 8, 27)], {"new_users": 10626, "blood_volume": 1169.95})
        self.assertEqual(OPERA.parse_opera_gx_text(gx, "bundle")[date(2026, 8, 27)], {"new_users": 4321, "blood_volume": 456.78})

    def test_gx_latest_dashboard_mapping(self):
        gx = """Summary table
Day Utm Content New Users Revenue
1 2026-09-01 bundle 5,508 $706.46
2 2026-09-01 toast 468 $156.93
3 2026-08-31 toast 963 $322.05
"""
        self.assertEqual(OPERA.parse_opera_gx_text(gx, "bundle")[date(2026, 9, 1)], {"new_users": 5508, "blood_volume": 706.46})
        self.assertEqual(OPERA.parse_opera_gx_text(gx, "toast")[date(2026, 9, 1)], {"new_users": 468, "blood_volume": 156.93})


    def test_gx_fetches_only_the_latest_matching_message(self):
        class FakeImap:
            def list(self):
                return "OK", [b'* LIST (\\HasNoChildren \\All) "/" "[Gmail]/All Mail"']

            def select(self, _mailbox, readonly):
                self.readonly = readonly
                return "OK", [b"0"]

            def uid(self, *args):
                if args[0] == "search":
                    self.search_args = args
                    return "OK", [b"101 102"]
                self.fetch_args = args
                message = EmailMessage()
                message["From"] = OPERA.SENDER
                message.set_content("latest report")
                return "OK", [(b"RFC822", message.as_bytes())]

        client = FakeImap()
        messages = list(OPERA.gx_messages(client))
        self.assertEqual(len(messages), 1)
        self.assertEqual(client.search_args, ("search", None, "FROM", OPERA.SENDER, "SUBJECT", f'"{OPERA.GX_SUBJECT}"'))
        self.assertEqual(client.fetch_args, ("fetch", b"102", "(RFC822)"))


    def test_gx_appends_with_spaced_partner_name(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        updates, appends, overwrites = OPERA.gx_plan_writes(
            headers,
            {},
            {"bubble": {date(2026, 8, 27): {"new_users": 10, "blood_volume": 2}}},
            allow_overwrite=False,
        )
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(appends, [{
            "日期": date(2026, 8, 27),
            "合作方": "Opera GX",
            "运营位": "气泡",
            "新增": 10,
            "血量": 2,
        }])
    def test_gx_source_skips_incompatible_attachment(self):

        original_messages = OPERA.gx_messages
        original_attachments = OPERA.attachments
        original_parser = OPERA.parse_opera_gx_pdf
        try:
            OPERA.gx_messages = lambda _client: [{"From": OPERA.SENDER}, {"From": OPERA.SENDER}]
            OPERA.attachments = lambda _message: [b"pdf"]
            calls = iter([
                ValueError("OperaGX Summary table headers were not found"),
                {date(2026, 8, 27): {"new_users": 10, "blood_volume": 2}},
            ])
            def fake_parser(_raw_pdf, _utm_content):
                result = next(calls)
                if isinstance(result, Exception):
                    raise result
                return result
            OPERA.parse_opera_gx_pdf = fake_parser
            rows = OPERA.gx_source_rows(object(), "bubble", date(2026, 8, 27), date(2026, 8, 27))
            self.assertEqual(rows[date(2026, 8, 27)], {"new_users": 10, "blood_volume": 2})
        finally:
            OPERA.gx_messages = original_messages
            OPERA.attachments = original_attachments
            OPERA.parse_opera_gx_pdf = original_parser

    def test_rejects_duplicate_campaign_date(self):
        duplicate = PDF_TEXT.replace("Performance", "5 2026-07-12 wpstest 2 $1.00\nPerformance")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            OPERA.parse_opera_text(duplicate, "wpstest")

    def test_imap_subject_search_and_pdf_attachment(self):
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
        self.assertEqual(list(OPERA.imap_messages(client)), [])
        self.assertEqual(client.mailbox, ("[Gmail]/All Mail", True))
        self.assertEqual(client.uid_args, ("search", None, "SUBJECT", f'"{OPERA.SUBJECT}"'))
        message = EmailMessage()
        message["From"] = OPERA.SENDER
        message.set_content("report")
        message.add_attachment(b"pdf", maintype="application", subtype="pdf", filename="report.pdf")
        self.assertEqual(list(OPERA.attachments(message)), [b"pdf"])
    def test_column_names(self):
        self.assertEqual(OPERA.col_name(0), "A")
        self.assertEqual(OPERA.col_name(26), "AA")

    def test_plans_append_for_new_long_format_records(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        updates, appends, overwrites = OPERA.plan_writes(
            headers,
            {},
            {"popup": {date(2026, 7, 12): {"new_users": 10, "blood_volume": 2.5}}},
            allow_overwrite=False,
        )
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(appends, [{
            "日期": date(2026, 7, 12),
            "合作方": "Opera",
            "运营位": "换量弹窗",
            "新增": 10,
            "血量": 2.5,
        }])

    def test_updates_existing_long_format_record(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        rows = {(date(2026, 7, 12), "Opera", "气泡"): {"row": 99, "values": [46216, "Opera", "气泡", 10, 2]}}
        updates, appends, overwrites = OPERA.plan_writes(
            headers,
            rows,
            {"bubble": {date(2026, 7, 12): {"new_users": 11, "blood_volume": 2}}},
            allow_overwrite=True,
        )
        self.assertEqual(appends, [])
        self.assertEqual(len(updates), 1)
        self.assertIn("D99", updates[0]["range"])
        self.assertEqual(len(overwrites), 1)

if __name__ == "__main__":
    unittest.main()
