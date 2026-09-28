import importlib.util
import unittest
import argparse
import os
import json
import tempfile
from email.message import EmailMessage
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

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


def make_pdf(labels):
    """Small real PDF fixture without adding a production/test dependency."""
    stream = "\n".join(f"BT /F1 12 Tf {x} {y} Td ({text}) Tj ET" for x, y, text in labels).encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 400] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    raw, offsets = b"%PDF-1.4\n", [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(raw)
    raw += b"xref\n0 6\n0000000000 65535 f \n"
    raw += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return raw + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


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
    def test_skips_dates_when_the_tables_have_different_cutoffs(self):
        partial = PAGE.replace(
            "Total $178 $173 $351",
            "Country Code 2026-07-14 Grand Total\nTotal $178 $178",
        )
        self.assertEqual(AVAST.parse_avast_page(partial), {
            date(2026, 7, 14): {"new_users": 178, "blood_volume": 178},
        })

    def test_skips_report_when_the_blood_table_is_absent(self):
        only_new = PAGE.replace("Total $178 $173 $351\n", "")
        self.assertEqual(AVAST.parse_avast_page(only_new), {})

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
    def test_skips_page_without_date_header(self):
        bad = PAGE.replace("Country Code 2026-07-14 2026-07-15 Grand Total\n", "")
        self.assertEqual(AVAST.parse_avast_page(bad), {})
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

    def test_skips_partial_metric_without_writing_any_cell(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 7, 21), "Avast", "气泡")
        rows = {key: {"row": 99, "values": [46224, "Avast", "气泡", "", 2]}}
        updates, appends, overwrites = AVAST.plan_writes(
            headers,
            rows,
            {"bubble": {date(2026, 7, 21): {"new_users": 11}}},
            allow_overwrite=False,
        )
        self.assertEqual(appends, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(updates, [])

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
            patch.object(AVAST, "imap_messages", return_value=[EmailMessage()]),
            patch.object(AVAST, "verified_sender", return_value=True),
            patch.object(AVAST, "attachments", return_value=[("report.pdf", b"pdf")]),
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
        self.assertEqual(list(AVAST.attachments(message)), [("report.pdf", b"pdf")])
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
        AVAST.select_all_mail(client)
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

    def test_skips_new_only_and_blood_only_dates_and_reports_them(self):
        page = ("Country Code 2026-09-09 2026-09-10 Grand Total\nTotal 10 20 30\n"
                "Country Code 2026-09-10 2026-09-11 Grand Total\nTotal $2 $3 $5\n")
        issues = []
        self.assertEqual(AVAST.parse_avast_page(page, issues), {
            date(2026, 9, 10): {"new_users": 20, "blood_volume": 2}})
        self.assertEqual(issues, [{"code": "partial_metrics", "dates": ["2026-09-09", "2026-09-11"]}])

    def test_shared_header_with_missing_value_never_right_aligns(self):
        page = ("Country Code 2026-09-09 2026-09-10 2026-09-11 Grand Total\n"
                "Total 10 20 30 60\nTotal $1 $2 $3\n")
        issues = []
        self.assertEqual(AVAST.parse_avast_page(page, issues), {})
        self.assertIn("ambiguous_columns", [i["code"] for i in issues])

    def test_invalid_blood_header_is_not_replaced_with_new_header(self):
        page = PAGE.replace("Total $178 $173 $351", "Country Code 2026-07-14 2026-07-14 Grand Total\nTotal $178 $173 $351")
        self.assertEqual(AVAST.parse_avast_page(page), {})

    def test_negative_cost_is_not_silently_made_positive(self):
        self.assertEqual(AVAST.parse_avast_page(PAGE.replace("$178", "-$178")), {})

    def test_month_name_with_comma(self):
        self.assertEqual(AVAST.parse_day("Jul 14, 2026"), date(2026, 7, 14))

    def test_pdf_column_positions_keep_other_days_when_middle_cell_is_empty(self):
        # A real in-memory PDF checks extraction as well as the parser.
        labels = []
        for x, text in ((150, "2026-09-09"), (250, "2026-09-10"), (350, "2026-09-11"), (450, "Grand Total")):
            labels.append((x, 350, text))
        labels.append((30, 320, "Total"))
        for x, text in ((170, "10"), (270, "20"), (470, "30")):
            labels.append((x, 320, text))  # last day has blood only
        labels.append((30, 290, "Total"))
        for x, text in ((170, "$1"), (370, "$3"), (470, "$4")):
            labels.append((x, 290, text))  # middle day has new users only
        issues = []
        self.assertEqual(AVAST.pdf_rows(make_pdf(labels), issues), {
            date(2026, 9, 9): {"new_users": 10, "blood_volume": 1}})
        self.assertEqual(issues, [{"code": "partial_metrics", "dates": ["2026-09-10", "2026-09-11"]}])

    def test_zero_is_complete_data_and_must_be_written(self):
        self.assertEqual(AVAST.parse_avast_page(PAGE.replace("178", "0")), {
            date(2026, 7, 14): {"new_users": 0, "blood_volume": 0},
            date(2026, 7, 15): {"new_users": 173, "blood_volume": 173}})

    def test_parse_failure_in_one_attachment_preserves_other_complete_data(self):
        message = EmailMessage()
        metrics = {date(2026, 9, 27): {"new_users": 10, "blood_volume": 1}}
        issues = []
        with patch.object(AVAST, "imap_messages", return_value=[message]), \
             patch.object(AVAST, "verified_sender", return_value=True), \
             patch.object(AVAST, "attachments", return_value=[("bad.pdf", b"bad"), ("good.pdf", b"good")]), \
             patch.object(AVAST, "pdf_rows", side_effect=[ValueError("bad PDF"), metrics]):
            self.assertEqual(AVAST.source_rows(None, "popup", date(2026, 9, 27), date(2026, 9, 27), date(2026, 9, 28), issues), metrics)
        self.assertEqual(issues[0]["code"], "parse_failure")

    def test_optional_missing_mail_is_quiet_and_required_missing_mail_warns(self):
        with patch.object(AVAST, "imap_messages", return_value=[]):
            optional, required = [], []
            for surface, issues in (("uninstall_h5", optional), ("popup", required)):
                AVAST.source_rows(None, surface, date(2026, 9, 27), date(2026, 9, 27), date(2026, 9, 28), issues)
        self.assertEqual(optional, [])
        self.assertEqual(required[0]["code"], "missing_report")

    def test_default_window_is_previous_seven_calendar_days(self):
        self.assertEqual(AVAST.sync_window(None, None, date(2026, 9, 28)),
                         (date(2026, 9, 21), date(2026, 9, 27), False))

    def test_explicit_range_is_inclusive_and_enables_history(self):
        self.assertEqual(AVAST.sync_window("2026-08-01", "2026-08-31", date(2026, 9, 28)),
                         (date(2026, 8, 1), date(2026, 8, 31), True))
        self.assertEqual(AVAST.sync_window(None, "2026-08-31", date(2026, 9, 28)),
                         (date(2026, 8, 25), date(2026, 8, 31), True))
        self.assertEqual(AVAST.sync_window("2026-09-01", None, date(2026, 9, 28)),
                         (date(2026, 9, 1), date(2026, 9, 27), True))

    def test_reversed_range_rejected_before_services(self):
        with self.assertRaises(ValueError):
            AVAST.sync_window("2026-09-27", "2026-09-21", date(2026, 9, 28))

    def test_history_search_fetches_newest_first_and_includes_later_revisions(self):
        client = MagicMock()
        def uid(*args):
            if args[0] == "search":
                return "OK", [b"41 42"]
            return "OK", [(b"data", b"From: no-reply-powerbi@microsoft.com\n\nbody")]
        client.uid.side_effect = uid
        messages = list(AVAST.imap_messages(client, "report", date(2026, 9, 28), date(2026, 8, 1)))
        self.assertEqual([m["X-Sync-UID"] for m in messages], ["42", "41"])
        self.assertEqual(client.uid.call_args_list[0].args, (
            "search", None, "SENTSINCE", "01-Aug-2026", "SENTBEFORE", "29-Sep-2026", "SUBJECT", '"report"'))
        client.list.assert_not_called()
        client.select.assert_not_called()

    def test_fetch_failure_is_not_missing_mail(self):
        client = MagicMock()
        client.uid.side_effect = [("OK", [b"42"]), ("NO", [b"failure"])]
        with self.assertRaisesRegex(RuntimeError, "fetch failed"):
            list(AVAST.imap_messages(client, "report", date(2026, 9, 28)))

    def test_historical_reports_merge_by_date_with_newest_complete_value_winning(self):
        days = [date(2026, 8, 1), date(2026, 8, 2)]
        metrics = lambda n: {"new_users": n, "blood_volume": n / 10}
        messages = [EmailMessage(), EmailMessage()]
        with patch.object(AVAST, "imap_messages", return_value=messages) as search, \
             patch.object(AVAST, "verified_sender", return_value=True), \
             patch.object(AVAST, "attachments", return_value=[("report.pdf", b"pdf")]), \
             patch.object(AVAST, "pdf_rows", side_effect=[{days[1]: metrics(20)}, {days[0]: metrics(10), days[1]: metrics(99)}]):
            rows = AVAST.source_rows(None, "popup", days[0], days[1], date(2026, 9, 28), history=True)
        self.assertEqual(rows, {days[0]: metrics(10), days[1]: metrics(20)})
        self.assertEqual(search.call_args.kwargs, {"history_start": days[0]})

    def test_later_history_fetch_failure_preserves_already_read_complete_days(self):
        def messages():
            yield EmailMessage()
            raise RuntimeError("fetch failed")
        metrics = {date(2026, 9, 27): {"new_users": 10, "blood_volume": 1}}
        issues = []
        with patch.object(AVAST, "imap_messages", return_value=messages()), \
             patch.object(AVAST, "verified_sender", return_value=True), \
             patch.object(AVAST, "attachments", return_value=[("report.pdf", b"pdf")]), \
             patch.object(AVAST, "pdf_rows", return_value=metrics):
            self.assertEqual(AVAST.source_rows(None, "popup", date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28), issues, True), metrics)
        self.assertIn("fetch_failure", [i["code"] for i in issues])

    def test_sheet_record_beyond_10000_is_found_instead_of_appended(self):
        service = MagicMock()
        service.spreadsheets().values().get().execute.return_value = {
            "values": [list(AVAST.HEADERS)] + [[]] * 10000 + [["2026-09-27", "Avast", "气泡", 10, 1]]}
        headers, rows = AVAST.get_sheet(service)
        self.assertEqual(rows[(date(2026, 9, 27), "Avast", "气泡")]["row"], 10002)
        self.assertEqual(service.spreadsheets().values().get.call_args.kwargs["range"], "'合作方新增血量'!A:E")
        self.assertEqual(AVAST.plan_writes(headers, rows, {"bubble": {
            date(2026, 9, 27): {"new_users": 10, "blood_volume": 1}}}, True), ([], [], []))

    def test_retry_is_bounded_and_non_transient_errors_not_retried(self):
        call = MagicMock(side_effect=TimeoutError())
        with patch.object(AVAST.time, "sleep") as sleep, self.assertRaises(TimeoutError):
            AVAST.retry_call(call)
        self.assertEqual(call.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2, 4])
        call = MagicMock(side_effect=ValueError())
        with self.assertRaises(ValueError):
            AVAST.retry_call(call)
        self.assertEqual(call.call_count, 1)

    def test_lost_append_response_does_not_duplicate_record(self):
        record = {"日期": date(2026, 9, 27), "合作方": "Avast", "运营位": "气泡", "新增": 10, "血量": 1}
        rows = {(record["日期"], "Avast", "气泡"): {"row": 22, "values": ["2026-09-27", "Avast", "气泡", 10, 1]}}
        with patch.object(AVAST, "append_once", side_effect=TimeoutError()) as append, \
             patch.object(AVAST, "get_sheet", return_value=(list(AVAST.HEADERS), rows)):
            AVAST.append_rows(None, list(AVAST.HEADERS), [record])
        self.assertEqual(append.call_count, 1)

    def test_append_recovery_retries_only_missing_records(self):
        records = [{"日期": date(2026, 9, day), "合作方": "Avast", "运营位": "气泡", "新增": 10, "血量": 1} for day in (26, 27)]
        rows = {(date(2026, 9, 26), "Avast", "气泡"): {"row": 22, "values": ["2026-09-26", "Avast", "气泡", 10, 1]}}
        with patch.object(AVAST, "append_once", side_effect=[TimeoutError(), None]) as append, \
             patch.object(AVAST, "get_sheet", return_value=(list(AVAST.HEADERS), rows)), patch.object(AVAST.time, "sleep"):
            AVAST.append_rows(None, list(AVAST.HEADERS), records)
        self.assertEqual(append.call_args.args[2], [records[1]])

    def test_append_recovery_conflict_stops_without_another_append(self):
        record = {"日期": date(2026, 9, 27), "合作方": "Avast", "运营位": "气泡", "新增": 10, "血量": 1}
        rows = {(record["日期"], "Avast", "气泡"): {"row": 22, "values": ["2026-09-27", "Avast", "气泡", 99, 1]}}
        with patch.object(AVAST, "append_once", side_effect=TimeoutError()) as append, \
             patch.object(AVAST, "get_sheet", return_value=(list(AVAST.HEADERS), rows)), self.assertRaisesRegex(RuntimeError, "conflicting"):
            AVAST.append_rows(None, list(AVAST.HEADERS), [record])
        self.assertEqual(append.call_count, 1)

    def test_write_readback_detects_missing_or_conflicting_values(self):
        source = {"bubble": {date(2026, 9, 27): {"new_users": 10, "blood_volume": 1}}}
        with patch.object(AVAST, "get_sheet", return_value=(list(AVAST.HEADERS), {})), self.assertRaisesRegex(RuntimeError, "verification failed"):
            AVAST.verify_sources(None, source)
        rows = {(date(2026, 9, 27), "Avast", "气泡"): {"row": 2, "values": ["2026-09-27", "Avast", "气泡", 10, 2]}}
        with patch.object(AVAST, "get_sheet", return_value=(list(AVAST.HEADERS), rows)), self.assertRaisesRegex(RuntimeError, "verification failed"):
            AVAST.verify_sources(None, source)

    def run_mock_sync(self, side_effect, rows=None):
        args = argparse.Namespace(start_date=None, end_date=None, allow_overwrite=True)
        summary = {"status": "running", "warnings": []}
        gmail = MagicMock()
        with patch.dict(os.environ, {"GMAIL_IMAP_USERNAME": "test", "GMAIL_APP_PASSWORD": "test", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON": "{}"}), \
             patch.object(AVAST, "sheets_service", return_value=MagicMock()), \
             patch.object(AVAST, "get_sheet", return_value=(list(AVAST.HEADERS), rows or {})), \
             patch.object(AVAST, "gmail_imap_client", return_value=gmail), \
             patch.object(AVAST, "select_all_mail") as select, \
             patch.object(AVAST, "source_rows", side_effect=side_effect), \
             patch.object(AVAST, "append_rows") as append, \
             patch.object(AVAST, "verify_sources", return_value=1) as verify:
            try:
                AVAST.run_sync(args, summary)
            except RuntimeError as exc:
                return summary, str(exc), append, verify, select
        return summary, None, append, verify, select

    def test_all_surfaces_missing_fails_instead_of_success(self):
        summary, error, append, verify, select = self.run_mock_sync([{}, {}, {}, {}])
        self.assertIn("all Avast surfaces", error)
        append.assert_not_called()
        verify.assert_not_called()
        select.assert_called_once()

    def test_one_surface_fetch_failure_does_not_block_other_writes(self):
        summary, error, append, verify, select = self.run_mock_sync([
            RuntimeError("fetch failed"), {date(2026, 9, 27): {"new_users": 10, "blood_volume": 1}}, {}, {}])
        self.assertIn("other complete records were synced", error)
        append.assert_called_once()
        verify.assert_called_once()
        self.assertEqual(summary["warnings"][0]["code"], "fetch_failure")
        select.assert_called_once()

    def test_matching_existing_data_is_reported_as_no_changes(self):
        rows = {(date(2026, 9, 27), "Avast", "气泡"): {"row": 2, "values": ["2026-09-27", "Avast", "气泡", 10, 1]}}
        summary, error, append, verify, select = self.run_mock_sync([
            {}, {date(2026, 9, 27): {"new_users": 10, "blood_volume": 1}}, {}, {}], rows)
        self.assertIsNone(error)
        self.assertEqual(summary["status"], "no_changes")
        self.assertEqual(summary["appended_rows"], 0)
        verify.assert_called_once()

    def test_failed_main_writes_status_file_for_webhook(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "status.json"
            with patch("sys.argv", ["avast", "--status-file", str(path)]), \
                 patch.object(AVAST, "run_sync", side_effect=RuntimeError("empty")), self.assertRaises(RuntimeError):
                AVAST.main()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "failed")
    def test_column_names(self):
        self.assertEqual(AVAST.col_name(0), "A")
        self.assertEqual(AVAST.col_name(25), "Z")
        self.assertEqual(AVAST.col_name(26), "AA")


if __name__ == "__main__":
    unittest.main()
