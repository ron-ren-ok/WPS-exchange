import importlib.util
import unittest
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


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

    def test_defaults_to_previous_seven_days(self):
        self.assertEqual(SYNC.sync_window(None, None, date(2026, 9, 28)),
                         (date(2026, 9, 21), date(2026, 9, 27), False))

    def test_end_only_defaults_to_seven_days(self):
        self.assertEqual(SYNC.sync_window(None, "2026-08-24", date(2026, 9, 28)),
                         (date(2026, 8, 18), date(2026, 8, 24), True))

    def test_explicit_range_and_invalid_range(self):
        self.assertEqual(SYNC.sync_window("2026-08-20", "2026-08-24", date(2026, 9, 28)),
                         (date(2026, 8, 20), date(2026, 8, 24), True))
        with self.assertRaises(ValueError):
            SYNC.sync_window("2026-08-25", "2026-08-24", date(2026, 9, 28))

    def test_does_not_update_partial_metrics(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        day = date(2026, 8, 23)
        existing = {(day, "CCleaner", "气泡"): {"row": 9, "values": [46258, "CCleaner", "气泡", 1, 3.5]}}
        updates, appends, overwrites = SYNC.plan_writes(headers, existing, {day: {"new_users": 4}})
        self.assertEqual(appends, [])
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(SYNC.plan_writes(headers, existing, {day: {"blood_volume": 8}}), ([], [], []))

    def test_overwrites_existing_blood_volume_when_reported(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        day = date(2026, 8, 23)
        existing = {(day, "CCleaner", "气泡"): {"row": 9, "values": [46258, "CCleaner", "气泡", 1, 3.5]}}
        updates, appends, overwrites = SYNC.plan_writes(headers, existing, {day: {"new_users": 4, "blood_volume": 5}})
        self.assertEqual(appends, [])
        self.assertEqual(updates, [{"range": "'合作方新增血量'!D9", "values": [[4]]}, {"range": "'合作方新增血量'!E9", "values": [[5]]}])
        self.assertEqual(len(overwrites), 2)
    def test_does_not_append_partial_metrics(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        day = date(2026, 8, 23)
        _, appends, _ = SYNC.plan_writes(headers, {}, {day: {"new_users": 4}})
        self.assertEqual(appends, [])

    def test_accepts_avast_style_forwarded_report(self):
        message = EmailMessage()
        message["From"] = "partner@wps.com"
        message.set_content("Forwarded message from no-reply-powerbi@microsoft.com")
        self.assertTrue(SYNC.verified_sender(message))


HEADERS = list(SYNC.HEADERS)


def report(uid, pdfs=()):
    message = EmailMessage()
    message["From"] = SYNC.ORIGINAL_SENDER
    message["X-Sync-UID"] = str(uid)
    message["Date"] = "Mon, 28 Sep 2026 01:00:00 +0800"
    message.set_content("Report")
    for filename, content in pdfs:
        message.add_attachment(content, maintype="application", subtype="pdf", filename=filename)
    return message


class ParserSafetyTests(unittest.TestCase):
    def test_accepts_every_advertised_date_format(self):
        for value in ("Sep 20, 2026", "September 20, 2026", "20 September 2026",
                      "Sep 20 2026", "20/09/2026", "2026/09/20", "20.09.2026"):
            with self.subTest(value=value):
                self.assertEqual(SYNC.parse_day(value), date(2026, 9, 20))

    def test_missing_column_cannot_shift_dates(self):
        issues = []
        page = PAGE.replace("Total 2,203 721 4 2,928", "Total 2,203 721 2,924")
        self.assertEqual(SYNC.parse_ccleaner_page(page, issues), {})
        self.assertIn("ambiguous_columns", [issue["code"] for issue in issues])

    def test_costs_cannot_borrow_install_header(self):
        first, cost = PAGE.rsplit("Country Code", 1)
        page = first + "Costs\n" + cost.split("\n", 1)[1]
        issues = []
        self.assertEqual(SYNC.parse_ccleaner_page(page, issues), {})
        self.assertIn("invalid_date_header", [issue["code"] for issue in issues])

    def test_only_intersection_of_own_date_headers_is_returned(self):
        page = ("Country Code 2026-08-18 2026-08-19 Total\nTotal 10 20 30\n"
                "Country Code 2026-08-19 2026-08-20 Total\nTotal $2 $3 $5\n")
        issues = []
        self.assertEqual(SYNC.parse_ccleaner_page(page, issues),
                         {date(2026, 8, 19): {"new_users": 20, "blood_volume": 2}})
        self.assertEqual(issues[0]["dates"], ["2026-08-18", "2026-08-20"])

    def test_install_only_and_cost_only_reports_are_skipped(self):
        for page in ("Country Code 2026-08-18 Total\nTotal 10 10\n",
                     "Country Code 2026-08-18 Total\nTotal $1 $1\n"):
            self.assertEqual(SYNC.parse_ccleaner_page(page), {})

    def test_duplicate_dates_are_diagnosed(self):
        issues = []
        self.assertEqual(SYNC.parse_ccleaner_page(PAGE.replace("2026-08-19", "2026-08-18"), issues), {})
        self.assertIn("invalid_date_header", [issue["code"] for issue in issues])

    def test_invalid_value_is_not_stripped_into_a_number(self):
        issues = []
        self.assertEqual(SYNC.parse_ccleaner_page(PAGE.replace("721", "721x"), issues), {})
        self.assertIn("invalid_metric", [issue["code"] for issue in issues])

    def test_blank_pdf_cell_retains_other_complete_dates(self):
        def word(text, x, top):
            return {"text": text, "x0": x - 10, "x1": x + 10, "top": top}
        words = []
        for top in (10, 30):
            words.extend(word(text, x, top) for text, x in
                         (("Country", 10), ("2026-08-18", 100), ("2026-08-19", 200),
                          ("2026-08-20", 300), ("Total", 400)))
        words.extend(word(text, x, 20) for text, x in
                     (("Total", 10), ("10", 100), ("30", 300), ("40", 400)))
        words.extend(word(text, x, 40) for text, x in
                     (("Total", 10), ("$1", 100), ("$2", 200), ("$3", 300), ("$6", 400)))
        page = Mock()
        page.extract_words.return_value = words
        positioned = SYNC.positioned_metrics(page)
        text = PAGE.replace("Total 2,203 721 4 2,928", "Total 10 30 40").replace("$0 $522 $94 $616", "$1 $2 $3 $6")
        self.assertEqual(SYNC.parse_ccleaner_page(text, positioned=positioned), {
            date(2026, 8, 18): {"new_users": 10, "blood_volume": 1},
            date(2026, 8, 20): {"new_users": 30, "blood_volume": 3},
        })

    def test_positional_costs_do_not_borrow_prior_table_header(self):
        def word(text, x, top):
            return {"text": text, "x0": x, "x1": x + 20, "top": top}
        page = Mock()
        page.extract_words.return_value = [word("2026-08-18", 100, 10), word("Total", 200, 10),
                                          word("Total", 0, 20), word("10", 100, 20), word("10", 200, 20),
                                          word("Total", 0, 30), word("$1", 100, 30), word("$1", 200, 30)]
        self.assertNotIn("blood_volume", SYNC.positioned_metrics(page))


class MailSelectionTests(unittest.TestCase):
    def test_imap_queries_inclusive_email_dates_and_fetches_newest_first(self):
        client = Mock()
        client.list.return_value = ("OK", [b'(\\All) "/" "[Gmail]/All Mail"'])
        client.select.return_value = ("OK", [b"2"])
        def uid(command, *args):
            if command == "search":
                return "OK", [b"10 11"]
            return "OK", [(b"data", report(args[0].decode()).as_bytes())]
        client.uid.side_effect = uid
        messages = list(SYNC.imap_messages(client, date(2026, 9, 21), date(2026, 9, 27)))
        self.assertEqual([m["X-Sync-UID"] for m in messages], ["11", "10"])
        client.uid.assert_any_call("search", None, "SENTSINCE", "21-Sep-2026", "SENTBEFORE", "28-Sep-2026",
                                   "SUBJECT", f'"{SYNC.SUBJECT}"')
        client.uid.assert_any_call("fetch", b"11", "(BODY.PEEK[])")

    def test_routine_falls_back_to_latest_usable_attachment_and_stops(self):
        day = date(2026, 9, 26)
        messages = [report(13), report(12, [("broken.pdf", b"bad"), ("report.pdf", b"good"), ("unused.pdf", b"unused")]),
                    report(11, [("old.pdf", b"old")])]
        issues = []
        with patch.object(SYNC, "imap_messages", return_value=iter(messages)) as mails, \
             patch.object(SYNC, "pdf_rows", side_effect=[ValueError("bad"), {day: {"new_users": 10, "blood_volume": 2}}]) as pdf:
            rows = SYNC.source_rows(Mock(), date(2026, 9, 21), date(2026, 9, 27), date(2026, 9, 28), False, issues)
        self.assertEqual(rows, {day: {"new_users": 10, "blood_volume": 2}})
        self.assertEqual(pdf.call_count, 2)
        self.assertEqual(mails.call_args.args[2], date(2026, 9, 28))
        self.assertTrue({"no_pdf", "parse_failure", "stale_report", "missing_dates"}.issubset({i["code"] for i in issues}))

    def test_history_merges_newest_complete_pairs_and_limits_data_range(self):
        first, second, outside = date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)
        messages = [report(12, [("new.pdf", b"new")]), report(11, [("old.pdf", b"old")])]
        with patch.object(SYNC, "imap_messages", return_value=iter(messages)) as mails, \
             patch.object(SYNC, "pdf_rows", side_effect=[{second: {"new_users": 20, "blood_volume": 2},
                                                          outside: {"new_users": 99, "blood_volume": 9}},
                                                         {first: {"new_users": 10, "blood_volume": 1},
                                                          second: {"new_users": 1, "blood_volume": 0}}]):
            rows = SYNC.source_rows(Mock(), first, second, date(2026, 9, 28), True, [])
        self.assertEqual(rows, {first: {"new_users": 10, "blood_volume": 1}, second: {"new_users": 20, "blood_volume": 2}})
        self.assertEqual(mails.call_args.args[1:], (first, second))

    def test_partial_metrics_from_different_reports_are_never_combined(self):
        day = date(2026, 8, 18)
        pages = ["Country Code 2026-08-18 Total\nTotal 10 10\n", "Country Code 2026-08-18 Total\nTotal $1 $1\n"]
        messages = [report(12, [("a.pdf", b"a")]), report(11, [("b.pdf", b"b")])]
        with patch.object(SYNC, "imap_messages", return_value=iter(messages)), \
             patch.object(SYNC, "pdf_rows", side_effect=lambda raw, issues: SYNC.parse_ccleaner_page(pages[0 if raw == b'a' else 1], issues)):
            self.assertEqual(SYNC.source_rows(Mock(), day, day, date(2026, 9, 28), True, []), {})

    def test_similar_sender_address_is_not_accepted(self):
        message = report(1)
        message.replace_header("From", "no-reply-powerbi@microsoft.com.evil.example")
        self.assertFalse(SYNC.verified_sender(message))

    def test_dropped_imap_connection_is_retried_with_new_session(self):
        clients = [Mock(), Mock()]
        secrets = {"GMAIL_IMAP_USERNAME": "test", "GMAIL_APP_PASSWORD": "test"}
        day = date(2026, 9, 27)
        source = {day: {"new_users": 10, "blood_volume": 2}}
        with patch.object(SYNC, "gmail_imap_client", side_effect=clients) as login, \
             patch.object(SYNC, "source_rows", side_effect=[SYNC.imaplib.IMAP4.abort("dropped"), source]), \
             patch.object(SYNC.time, "sleep"):
            self.assertEqual(SYNC.fetch_source(secrets, day, day, date(2026, 9, 28), False, []), source)
        self.assertEqual(login.call_count, 2)
        for client in clients:
            client.logout.assert_called_once()


class SheetReliabilityTests(unittest.TestCase):
    def test_reads_beyond_10000_and_ignores_other_partner_bad_dates_and_duplicates(self):
        service = Mock()
        values = [HEADERS] + [["bad date", "Opera", "气泡", 1, 2]] * 10000
        values.append(["2026-09-27", "CCleaner", "气泡", 10, 2])
        api = service.spreadsheets().values()
        api.get.return_value.execute.return_value = {"values": values}
        _, rows = SYNC.get_sheet(service)
        self.assertEqual(rows[(date(2026, 9, 27), "CCleaner", "气泡")]["row"], 10002)
        self.assertEqual(api.get.call_args.kwargs["range"], "'合作方新增血量'!A:E")

    def test_target_duplicates_still_fail(self):
        service = Mock()
        row = ["2026-09-27", "CCleaner", "气泡", 10, 2]
        service.spreadsheets().values().get.return_value.execute.return_value = {"values": [HEADERS, row, row]}
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            SYNC.get_sheet(service)

    def test_zero_pairs_are_valid_and_unchanged_rows_are_not_written(self):
        day = date(2026, 9, 27)
        source = {day: {"new_users": 0, "blood_volume": 0}}
        _, appends, _ = SYNC.plan_writes(HEADERS, {}, source)
        self.assertEqual(appends[0]["血量"], 0)
        existing = {(day, "CCleaner", "气泡"): {"row": 2, "values": [day.isoformat(), "CCleaner", "气泡", 0, 0]}}
        self.assertEqual(SYNC.plan_writes(HEADERS, existing, source), ([], [], []))

    def test_append_response_loss_does_not_duplicate_successful_row(self):
        day = date(2026, 9, 27)
        record = {"日期": day, "合作方": "CCleaner", "运营位": "气泡", "新增": 10, "血量": 2}
        existing = {(day, "CCleaner", "气泡"): {"row": 2, "values": [day.isoformat(), "CCleaner", "气泡", 10, 2]}}
        with patch.object(SYNC, "append_once", side_effect=TimeoutError) as append, \
             patch.object(SYNC, "get_sheet", return_value=(HEADERS, existing)):
            SYNC.append_rows(Mock(), HEADERS, [record])
        self.assertEqual(append.call_count, 1)

    def test_append_retries_only_missing_rows_after_partial_success(self):
        days = [date(2026, 9, 26), date(2026, 9, 27)]
        records = [{"日期": d, "合作方": "CCleaner", "运营位": "气泡", "新增": 10, "血量": 2} for d in days]
        existing = {(days[0], "CCleaner", "气泡"): {"row": 2, "values": [days[0].isoformat(), "CCleaner", "气泡", 10, 2]}}
        with patch.object(SYNC, "append_once", side_effect=[TimeoutError, None]) as append, \
             patch.object(SYNC, "get_sheet", return_value=(HEADERS, existing)), patch.object(SYNC.time, "sleep"):
            SYNC.append_rows(Mock(), HEADERS, records)
        self.assertEqual(append.call_args_list[1].args[2], [records[1]])

    def test_append_recovery_rejects_conflicts(self):
        day = date(2026, 9, 27)
        record = {"日期": day, "合作方": "CCleaner", "运营位": "气泡", "新增": 10, "血量": 2}
        existing = {(day, "CCleaner", "气泡"): {"row": 2, "values": [day.isoformat(), "CCleaner", "气泡", 99, 2]}}
        with patch.object(SYNC, "append_once", side_effect=TimeoutError), \
             patch.object(SYNC, "get_sheet", return_value=(HEADERS, existing)):
            with self.assertRaisesRegex(RuntimeError, "conflicting"):
                SYNC.append_rows(Mock(), HEADERS, [record])

    def test_verification_rejects_wrong_metrics(self):
        day = date(2026, 9, 27)
        source = {day: {"new_users": 10, "blood_volume": 2}}
        existing = {(day, "CCleaner", "气泡"): {"row": 2, "values": [day.isoformat(), "CCleaner", "气泡", 10, 2]}}
        with patch.object(SYNC, "get_sheet", return_value=(HEADERS, existing)):
            self.assertEqual(SYNC.verify_source(Mock(), source), 1)
            existing[next(iter(existing))]["values"][4] = 9
            with self.assertRaisesRegex(RuntimeError, "verification failed"):
                SYNC.verify_source(Mock(), source)

    def test_transient_retry_is_bounded_and_permission_error_is_not_retried(self):
        call = Mock(side_effect=[TimeoutError, 123])
        with patch.object(SYNC.time, "sleep"):
            self.assertEqual(SYNC.retry_call(call), 123)
            call = Mock(side_effect=TimeoutError)
            with self.assertRaises(TimeoutError):
                SYNC.retry_call(call)
            self.assertEqual(call.call_count, SYNC.RETRIES + 1)
            call = Mock(side_effect=PermissionError)
            with self.assertRaises(PermissionError):
                SYNC.retry_call(call)
            self.assertEqual(call.call_count, 1)

    def test_invalid_input_fails_before_connecting(self):
        summary = {"warnings": [], "timings_seconds": {}}
        with patch.object(SYNC, "gmail_imap_client") as gmail:
            with self.assertRaises(ValueError):
                SYNC.run_sync(SimpleNamespace(start_date="bad", end_date=None), summary)
            gmail.assert_not_called()

    def test_no_complete_source_causes_no_sheet_access(self):
        args = SimpleNamespace(start_date="2026-09-21", end_date="2026-09-27")
        summary = {"warnings": [], "timings_seconds": {}}
        env = {name: "test" for name in ("GMAIL_IMAP_USERNAME", "GMAIL_APP_PASSWORD", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
        with patch.dict(SYNC.os.environ, env), patch.object(SYNC, "gmail_imap_client"), \
             patch.object(SYNC, "source_rows", return_value={}), patch.object(SYNC, "sheets_service") as sheets:
            with self.assertRaisesRegex(RuntimeError, "no complete"):
                SYNC.run_sync(args, summary)
            sheets.assert_not_called()

    def test_run_sync_overwrites_appends_then_verifies_readback(self):
        args = SimpleNamespace(start_date="2026-09-21", end_date="2026-09-27")
        summary = {"warnings": [], "timings_seconds": {}}
        first, second = date(2026, 9, 26), date(2026, 9, 27)
        source = {first: {"new_users": 10, "blood_volume": 2}, second: {"new_users": 0, "blood_volume": 0}}
        existing = {(first, "CCleaner", "气泡"): {"row": 2, "values": [first.isoformat(), "CCleaner", "气泡", 1, 1]}}
        fresh = {(first, "CCleaner", "气泡"): {"row": 2, "values": [first.isoformat(), "CCleaner", "气泡", 10, 2]},
                 (second, "CCleaner", "气泡"): {"row": 3, "values": [second.isoformat(), "CCleaner", "气泡", 0, 0]}}
        service = Mock()
        env = {name: "test" for name in ("GMAIL_IMAP_USERNAME", "GMAIL_APP_PASSWORD", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
        with patch.dict(SYNC.os.environ, env), patch.object(SYNC, "fetch_source", return_value=source), \
             patch.object(SYNC, "sheets_service", return_value=service), \
             patch.object(SYNC, "get_sheet", side_effect=[(HEADERS, existing), (HEADERS, fresh)]), \
             patch.object(SYNC, "append_rows") as append:
            SYNC.run_sync(args, summary)
        self.assertEqual(summary["updated_cells"], 2)
        self.assertEqual(summary["appended_rows"], 1)
        self.assertEqual(summary["verified_records"], 2)
        self.assertEqual(summary["status"], "updated")
        self.assertEqual(append.call_args.args[2][0]["日期"], second)
        self.assertEqual(len(summary["timings_seconds"]), 4)

    def test_http_503_retries_but_http_403_does_not(self):
        class ApiError(Exception):
            def __init__(self, status):
                self.resp = SimpleNamespace(status=status)
        for status, expected in ((503, 2), (403, 1)):
            request = Mock()
            request.execute.side_effect = [ApiError(status), {"values": []}]
            with patch.object(SYNC.time, "sleep"):
                if status == 503:
                    self.assertEqual(SYNC.sheets_execute(request), {"values": []})
                else:
                    with self.assertRaises(ApiError):
                        SYNC.sheets_execute(request)
            self.assertEqual(request.execute.call_count, expected)


if __name__ == "__main__":
    unittest.main()
