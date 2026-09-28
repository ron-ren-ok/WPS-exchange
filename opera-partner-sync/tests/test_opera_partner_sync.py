import importlib.util
import unittest
from email.message import EmailMessage
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

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

    def test_gx_recall_routes_to_uninstall_guidance(self):
        gx = """Summary table
Day Utm Content New Users Revenue
1 2026-09-27 toast 100 $10.00
2 2026-09-27 bundle 200 $20.00
3 2026-09-27 recall 1,234 $56.78
"""
        day = date(2026, 9, 27)
        with patch.object(OPERA, "gx_messages", return_value=[{"From": OPERA.SENDER}]), \
             patch.object(OPERA, "attachments", return_value=[b"pdf"]), \
             patch.object(OPERA, "extract_pdf_text", return_value=gx) as extractor:
            sources = OPERA.dashboard_source_rows(object(), day, day, gx=True, include_history=False)
        extractor.assert_called_once_with(b"pdf")
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        existing = {(day, "Opera GX", "气泡"): {"row": 10, "values": [day, "Opera GX", "气泡", 100, 10]},
                    (day, "Opera GX", "换量弹窗"): {"row": 11, "values": [day, "Opera GX", "换量弹窗", 200, 20]}}
        updates, appends, overwrites = OPERA.gx_plan_writes(headers, existing, sources, allow_overwrite=False)
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(appends, [{"日期": day, "合作方": "Opera GX", "运营位": "卸载引导", "新增": 1234, "血量": 56.78}])

    def test_gx_missing_recall_does_not_create_zero_rows(self):
        gx = """Summary table
Day Utm Content New Users Revenue
1 2026-09-27 toast 100 $10.00
"""
        day = date(2026, 9, 27)
        with patch.object(OPERA, "gx_messages", return_value=[{"From": OPERA.SENDER}]), \
             patch.object(OPERA, "attachments", return_value=[b"pdf"]), \
             patch.object(OPERA, "extract_pdf_text", return_value=gx) as extractor:
            source = OPERA.gx_source_rows(object(), "recall", day, day)
        self.assertEqual(source, {})
        self.assertEqual(OPERA.gx_plan_writes(list(OPERA.HEADERS), {}, {"recall": source}, allow_overwrite=False), ([], [], []))


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
        gx = "Summary table\nDay Utm Content New Users Revenue\n1 2026-08-27 toast 10 $2.00\n"
        with patch.object(OPERA, "gx_messages", return_value=[{"From": OPERA.SENDER}]), \
             patch.object(OPERA, "attachments", return_value=[b"other", b"pdf"]), \
             patch.object(OPERA, "extract_pdf_text", side_effect=["not a dashboard", gx]):
            rows = OPERA.gx_source_rows(object(), "bubble", date(2026, 8, 27), date(2026, 8, 27))
        self.assertEqual(rows[date(2026, 8, 27)], {"new_users": 10, "blood_volume": 2})

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
        self.assertEqual(client.uid_args, ("search", None, "FROM", OPERA.SENDER, "SUBJECT", f'"{OPERA.SUBJECT}"'))
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

class PerformanceTests(unittest.TestCase):
    def test_recent_mail_filter_and_newest_first(self):
        client = MagicMock()
        client.list.return_value = ("OK", [])
        client.select.return_value = ("OK", [])
        message = EmailMessage()
        message["From"] = OPERA.SENDER
        message.set_content("report")
        def uid(*args):
            if args[0] == "search":
                return "OK", [b"101 102"]
            return "OK", [(b"RFC822", message.as_bytes())]
        client.uid.side_effect = uid
        list(OPERA.imap_messages(client, since=date(2026, 9, 22)))
        self.assertEqual(client.uid.call_args_list[0].args, ("search", None, "FROM", OPERA.SENDER, "SUBJECT", f'"{OPERA.SUBJECT}"', "SINCE", "22-Sep-2026"))
        self.assertEqual([call.args[1] for call in client.uid.call_args_list[1:]], [b"102", b"101"])

    def test_one_extraction_for_both_opera_positions_and_early_stop(self):
        seen = []
        def messages(*args, **kwargs):
            for index in range(3):
                seen.append(index)
                yield {"From": OPERA.SENDER}
        with patch.object(OPERA, "imap_messages", side_effect=messages), \
             patch.object(OPERA, "attachments", return_value=[b"pdf"]), \
             patch.object(OPERA, "extract_pdf_text", return_value=PDF_TEXT) as extractor:
            sources = OPERA.dashboard_source_rows(object(), date(2026, 7, 11), date(2026, 7, 12))
        self.assertEqual(seen, [0])
        extractor.assert_called_once_with(b"pdf")
        self.assertEqual(len(sources["bubble"]), 2)
        self.assertEqual(len(sources["popup"]), 2)

    def test_history_fills_gap_and_keeps_newest_values(self):
        latest = PDF_TEXT.replace("4 2026-07-11 wpstest 13,228 $1,083.56", "")
        older = PDF_TEXT.replace("11,203 $943.79", "1 $1.00")
        with patch.object(OPERA, "imap_messages", return_value=[{"From": OPERA.SENDER}] * 2), \
             patch.object(OPERA, "attachments", return_value=[b"pdf"]), \
             patch.object(OPERA, "extract_pdf_text", side_effect=[latest, older]):
            sources = OPERA.dashboard_source_rows(object(), date(2026, 7, 11), date(2026, 7, 12))
        self.assertEqual(sources["bubble"][date(2026, 7, 12)]["new_users"], 11203)
        self.assertEqual(sources["bubble"][date(2026, 7, 11)]["new_users"], 13228)

    def test_gx_history_filters_dates_and_keeps_newest_values(self):
        header = "Summary table\nDay Utm Content New Users Revenue\n"
        latest = header + "1 2026-09-27 toast 100 $10.00\n2 2026-09-28 recall 999 $99.00\n"
        older = header + "1 2026-09-27 toast 50 $5.00\n2 2026-09-26 recall 20 $2.00\n"
        with patch.object(OPERA, "gx_messages", return_value=[{"From": OPERA.SENDER}] * 2) as messages, \
             patch.object(OPERA, "attachments", return_value=[b"pdf"]), \
             patch.object(OPERA, "extract_pdf_text", side_effect=[latest, older]) as extractor:
            sources = OPERA.dashboard_source_rows(object(), date(2026, 9, 26), date(2026, 9, 27), gx=True, since=date(2026, 9, 26), include_history=True)
        messages.assert_called_once_with(unittest.mock.ANY, since=date(2026, 9, 26), include_history=True)
        self.assertEqual(extractor.call_count, 2)
        self.assertEqual(sources["bubble"][date(2026, 9, 27)]["new_users"], 100)
        self.assertEqual(sources["recall"], {date(2026, 9, 26): {"new_users": 20, "blood_volume": 2}})

    def test_continued_summary_on_later_page_is_retained(self):
        pdf = MagicMock()
        first, second = MagicMock(), MagicMock()
        first.extract_text.return_value = "Summary table\nDay Campaign New Users Revenue\n1 2026-07-12 wpstest 10 $2.00"
        second.extract_text.return_value = "2 2026-07-12 wpstest2/opera.exe 20 $3.00"
        pdf.pages = [first, second]
        with patch.object(OPERA.pdfplumber, "open") as opened:
            opened.return_value.__enter__.return_value = pdf
            sources = OPERA.parse_dashboard_text(OPERA.extract_pdf_text(b"pdf"))
        self.assertEqual(sources["popup"][date(2026, 7, 12)]["new_users"], 20)

    def test_main_default_and_explicit_windows(self):
        for argv, expected_start, expected_end, since, history in [
            (["sync"], date(2026, 9, 21), date(2026, 9, 27), date(2026, 9, 22), False),
            (["sync", "--start-date", "2026-08-01", "--end-date", "2026-08-10"], date(2026, 8, 1), date(2026, 8, 10), date(2026, 8, 1), True),
            (["sync", "--end-date", "2026-08-10"], date(2026, 8, 4), date(2026, 8, 10), date(2026, 8, 4), True),
        ]:
            with self.subTest(argv=argv), \
                 patch.object(OPERA.sys, "argv", argv), \
                 patch.dict(OPERA.os.environ, {"GMAIL_IMAP_USERNAME": "test", "GMAIL_APP_PASSWORD": "test", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON": "{}"}), \
                 patch.object(OPERA, "datetime") as clock, \
                 patch.object(OPERA, "sheets_service"), \
                 patch.object(OPERA, "get_sheet", return_value=(list(OPERA.HEADERS), {})), \
                 patch.object(OPERA, "gmail_imap_client") as gmail, \
                 patch.object(OPERA, "dashboard_source_rows", return_value={}) as loader, \
                 patch.object(OPERA, "append_rows"):
                clock.now.return_value = datetime(2026, 9, 28)
                clock.strptime.side_effect = datetime.strptime
                OPERA.main()
            self.assertEqual(loader.call_args_list[0].args, (gmail.return_value, expected_start, expected_end))
            self.assertEqual(loader.call_args_list[0].kwargs, {"since": since})
            self.assertEqual(loader.call_args_list[1].kwargs, {"gx": True, "since": since, "include_history": history})

    def test_invalid_range_stops_before_gmail_or_writes(self):
        with patch.object(OPERA.sys, "argv", ["sync", "--start-date", "2026-09-28", "--end-date", "2026-09-27"]), \
             patch.dict(OPERA.os.environ, {"GMAIL_IMAP_USERNAME": "test", "GMAIL_APP_PASSWORD": "test", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON": "{}"}), \
             patch.object(OPERA, "sheets_service"), \
             patch.object(OPERA, "get_sheet", return_value=(list(OPERA.HEADERS), {})), \
             patch.object(OPERA, "gmail_imap_client") as gmail:
            with self.assertRaisesRegex(RuntimeError, "start date is after end date"):
                OPERA.main()
            gmail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
