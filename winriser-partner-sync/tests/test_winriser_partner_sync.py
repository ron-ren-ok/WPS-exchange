import importlib.util
import io
import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

MODULE = Path(__file__).resolve().parents[1] / "src" / "winriser_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("winriser", MODULE)
WINRISER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WINRISER)


class WinriserTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 7, 14)
        self.headers = list(WINRISER.HEADERS)
        self.source = {(self.day, "气泡"): {"new_users": 12, "blood_volume": 3.5}}

    def api(self, values):
        api = MagicMock()
        api.spreadsheets().values().get().execute.return_value = {"values": values}
        return api

    def record(self):
        return {"日期": self.day, "合作方": "Winriser", "运营位": "气泡", "新增": 12, "血量": 3.5}

    def test_parses_wps_parent_rows_and_ids(self):
        html = """
        <table><tr><th></th><th>Date</th><th>Source</th><th>Install Count</th><th>Spend-PPI($)</th></tr>
        <tr><td><input name="ctl00$ContentPlaceHolder1$grdSource$ctl02$key" value="527"></td><td>2026-07-14</td><td>WPS</td><td>100</td><td>50</td></tr></table>
        """
        self.assertEqual(WINRISER.parse_parent_rows(html, date(2026, 7, 14)), {date(2026, 7, 14): "527"})

    def test_parses_only_mapped_wps_child_sources(self):
        html = """
        <table><tr><th></th><th>Date</th><th>Source</th><th>Install Count</th><th>Spend-PPI($)</th></tr>
        <tr><td>-</td><td>2026-07-14</td><td>WPS</td><td>100</td><td>50</td></tr>
        <tr><td>-</td><td>2026-07-14</td><td>wnrwpsofc</td><td>12</td><td>3.5</td></tr>
        <tr><td>-</td><td>2026-07-14</td><td>wnrwpsofc_exchange</td><td>8</td><td>4</td></tr>
        <tr><td>-</td><td>2026-07-14</td><td>wnrwps_radar</td><td>6</td><td>2.4</td></tr>
        <tr><td>-</td><td>2026-07-14</td><td>wnrwpsofc2</td><td>80</td><td>40</td></tr>
        """
        rows = WINRISER.parse_report(html, date(2026, 7, 14))
        self.assertEqual(rows, {
            (date(2026, 7, 14), "气泡"): {"new_users": 12, "blood_volume": 3.5},
            (date(2026, 7, 14), "换量弹窗"): {"new_users": 8, "blood_volume": 4},
            (date(2026, 7, 14), "文档雷达"): {"new_users": 6, "blood_volume": 2.4},
        })

    def test_plans_append_for_all_mapped_records(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        source = {
            (date(2026, 7, 14), "气泡"): {"new_users": 12, "blood_volume": 3.5},
            (date(2026, 7, 14), "换量弹窗"): {"new_users": 8, "blood_volume": 4},
            (date(2026, 7, 14), "文档雷达"): {"new_users": 6, "blood_volume": 2.4},
        }
        updates, appends, overwrites = WINRISER.plan_writes(headers, {}, source, False)
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertCountEqual(appends, [
            {"日期": date(2026, 7, 14), "合作方": "Winriser", "运营位": "气泡", "新增": 12, "血量": 3.5},
            {"日期": date(2026, 7, 14), "合作方": "Winriser", "运营位": "换量弹窗", "新增": 8, "血量": 4},
            {"日期": date(2026, 7, 14), "合作方": "Winriser", "运营位": "文档雷达", "新增": 6, "血量": 2.4},
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

    def test_reads_past_10000_and_skips_other_partner_dates(self):
        values = [self.headers] + [["unparseable", "Other", "气泡", 0, 0]] * 10000
        values.append(["2026-07-14", "Winriser", "气泡", 12, 3.5])
        api = self.api(values)
        with patch.object(WINRISER, "parse_day", wraps=WINRISER.parse_day) as parse:
            headers, rows = WINRISER.get_sheet(api)
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(rows[(self.day, "Winriser", "气泡")]["row"], 10002)
        self.assertEqual(api.spreadsheets().values().get.call_args.kwargs["range"], "'合作方新增血量'!A:E")
        self.assertEqual(WINRISER.plan_writes(headers, rows, self.source, False), ([], [], []))

    def test_rejects_duplicate_winriser_target_keys(self):
        row = ["2026-07-14", "Winriser", "气泡", 12, 3.5]
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            WINRISER.get_sheet(self.api([self.headers, row, row]))

    def test_write_readback_checks_paired_metrics(self):
        row = ["2026-07-14", "Winriser", "气泡", 12, 3.5]
        self.assertEqual(WINRISER.verify_source(self.api([self.headers, row]), self.source)["verified_records"], 1)
        row[-1] = 3
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            WINRISER.verify_source(self.api([self.headers, row]), self.source)
        with self.assertRaisesRegex(RuntimeError, "missing"):
            WINRISER.verify_source(self.api([self.headers]), self.source)

    def test_retry_three_times_with_five_second_intervals(self):
        call = MagicMock(side_effect=[requests.Timeout(), requests.Timeout(), requests.Timeout(), "ok"])
        with patch.object(WINRISER.time, "sleep") as sleep:
            self.assertEqual(WINRISER.retry_call(call), "ok")
        self.assertEqual(call.call_count, 4)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(5)] * 3)

    def test_retry_exhaustion_and_permanent_errors(self):
        for exc, calls in ((requests.Timeout(), 4), (ValueError("bad"), 1)):
            call = MagicMock(side_effect=exc)
            with patch.object(WINRISER.time, "sleep") as sleep:
                with self.assertRaises(type(exc)):
                    WINRISER.retry_call(call)
            self.assertEqual(call.call_count, calls)
            self.assertEqual(sleep.call_count, calls - 1)

    def test_http_status_retry_classification(self):
        for status, expected in ((429, True), (503, True), (401, False), (400, False)):
            response = requests.Response()
            response.status_code = status
            self.assertEqual(WINRISER.retryable(requests.HTTPError(response=response)), expected)
        from types import SimpleNamespace
        error = RuntimeError("Google HTTP error")
        error.resp = SimpleNamespace(status=429)
        self.assertTrue(WINRISER.retryable(error))

    def test_tracker_retries_http_errors_before_parsing(self):
        session = MagicMock()
        bad, good = MagicMock(), MagicMock()
        response = requests.Response()
        response.status_code = 503
        bad.raise_for_status.side_effect = requests.HTTPError(response=response)
        session.get.side_effect = [bad, good]
        with patch.object(WINRISER.time, "sleep"):
            self.assertIs(WINRISER.tracker_request(session, "get", "https://example.test"), good)
        self.assertEqual(session.get.call_count, 2)

    def test_append_lost_response_does_not_duplicate(self):
        targets = {(self.day, "Winriser", "气泡"): {"row": 2, "values": ["2026-07-14", "Winriser", "气泡", 12, 3.5]}}
        with patch.object(WINRISER, "append_once", side_effect=requests.Timeout()) as append, patch.object(WINRISER, "get_sheet", return_value=(self.headers, targets)), patch.object(WINRISER.time, "sleep") as sleep:
            WINRISER.append_rows(MagicMock(), self.headers, [self.record()])
        self.assertEqual(append.call_count, 1)
        sleep.assert_called_once_with(5)

    def test_append_retries_only_missing_records(self):
        second = dict(self.record(), 运营位="换量弹窗")
        targets = {(self.day, "Winriser", "气泡"): {"row": 2, "values": ["2026-07-14", "Winriser", "气泡", 12, 3.5]}}
        with patch.object(WINRISER, "append_once", side_effect=[requests.Timeout(), None]) as append, patch.object(WINRISER, "get_sheet", return_value=(self.headers, targets)), patch.object(WINRISER.time, "sleep"):
            WINRISER.append_rows(MagicMock(), self.headers, [self.record(), second])
        self.assertEqual(append.call_args.args[2], [second])

    def test_append_stops_on_conflict_or_failed_recovery_read(self):
        targets = {(self.day, "Winriser", "气泡"): {"row": 2, "values": ["2026-07-14", "Winriser", "气泡", 99, 3.5]}}
        for kwargs in ({"return_value": (self.headers, targets)}, {"side_effect": requests.Timeout()}):
            with patch.object(WINRISER, "append_once", side_effect=requests.Timeout()) as append, patch.object(WINRISER, "get_sheet", **kwargs), patch.object(WINRISER.time, "sleep"):
                with self.assertRaises((RuntimeError, requests.Timeout)):
                    WINRISER.append_rows(MagicMock(), self.headers, [self.record()])
            self.assertEqual(append.call_count, 1)

    def test_append_retries_are_bounded(self):
        with patch.object(WINRISER, "append_once", side_effect=requests.Timeout()) as append, patch.object(WINRISER, "get_sheet", return_value=(self.headers, {})), patch.object(WINRISER.time, "sleep"):
            with self.assertRaises(requests.Timeout):
                WINRISER.append_rows(MagicMock(), self.headers, [self.record()])
        self.assertEqual(append.call_count, 4)

    def test_parse_report_reports_skips_and_keeps_zero(self):
        html = '''<tr><td>-</td><td>2026-07-14</td><td>wnrwpsofc</td><td>0</td><td>0</td></tr>
        <tr><td>-</td><td>2026-07-13</td><td>wnrwpsofc</td><td>1</td><td>1</td></tr>
        <tr><td>-</td><td>bad date</td><td>new_source</td><td>1</td><td>1</td></tr>
        <tr><td>-</td><td>2026-07-14</td><td>WPS</td><td>1</td><td>1</td></tr>
        <tr><td>broken</td></tr>'''
        diagnostics = {}
        rows = WINRISER.parse_report(html, self.day, self.day, diagnostics)
        self.assertEqual(rows[(self.day, "气泡")], {"new_users": 0, "blood_volume": 0})
        self.assertEqual(diagnostics, {"outside_range_rows": 1, "unmapped_sources": {"new_source": 1}, "aggregate_rows": 1, "malformed_rows": 1})

    def test_coverage_distinguishes_missing_from_zero(self):
        source = {(self.day, "气泡"): {"new_users": 0, "blood_volume": 0}}
        summary = WINRISER.coverage_summary(source, {self.day: "527"}, date(2026, 7, 15), self.day, {})
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["missing_dates"], ["2026-07-15"])
        self.assertEqual(len(summary["missing_records"]), 5)
        self.assertEqual(len(summary["zero_records"]), 1)
        self.assertIsNone(summary["latest_by_operation"]["文档雷达"])

    def test_complete_coverage_and_default_window(self):
        source = {(self.day, op): {"new_users": 1, "blood_volume": 1} for op in WINRISER.SOURCE_TO_OPERATION.values()}
        summary = WINRISER.coverage_summary(source, {self.day: "527"}, self.day, None, {})
        self.assertEqual(summary["status"], "complete")
        self.assertIsNone(summary["requested_range"]["start"])
        self.assertEqual(summary["missing_records"], [])

    def test_historical_options_refreshed_and_deduplicated(self):
        session = MagicMock()
        page = '''<input type="hidden" name="state" value="first">
        <select name="ctl00$ContentPlaceHolder1$ddSource"></select>
        <select name="ctl00$ContentPlaceHolder1$dddate"><option value="3">Recent</option><option value="7">Week</option><option value="3">Duplicate</option></select>
        <input name="ctl00$ContentPlaceHolder1$btnview" value="View Report">'''
        session.get.side_effect = [MagicMock(text=page), MagicMock(text=page.replace("first", "fresh"))]
        session.post.return_value.text = "report"
        self.assertEqual(list(WINRISER.fetch_parent_reports(session, True)), ["report", "report"])
        self.assertEqual(session.post.call_args_list[0].kwargs["data"]["state"], "first")
        self.assertEqual(session.post.call_args_list[1].kwargs["data"]["state"], "fresh")
        self.assertEqual(session.post.call_args_list[1].kwargs["data"]["ctl00$ContentPlaceHolder1$dddate"], "7")

    def test_parent_reports_filtered_and_children_fetched_once(self):
        html = '''<table><tr><th></th><th>Date</th><th>Source</th><th>Install Count</th><th>Spend-PPI($)</th></tr>
        <tr><td><input name="x$key" value="527"></td><td>2026-07-14</td><td>WPS</td><td>1</td><td>1</td></tr>
        <tr><td><input name="x$key" value="500"></td><td>2026-07-13</td><td>WPS</td><td>1</td><td>1</td></tr></table>'''
        child = '<tr><td>-</td><td>2026-07-14</td><td>wnrwpsofc</td><td>12</td><td>3.5</td></tr>'
        diagnostics = {}
        with patch.object(WINRISER, "fetch_parent_reports", return_value=iter(["prompt", html, html])), patch.object(WINRISER, "fetch_child_sources", return_value=child) as fetch:
            source, parents = WINRISER.collect_source(MagicMock(), self.day, self.day, diagnostics)
        self.assertEqual(source, self.source)
        self.assertEqual(parents, {self.day: "527"})
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(len(diagnostics["skipped_reports"]), 1)

    def test_reversed_dates_fail_before_network(self):
        with patch.object(WINRISER.sys, "argv", ["sync", "--start-date", "2026-07-15", "--end-date", "2026-07-14"]), patch.dict(WINRISER.os.environ, {"WINRISER_LOGIN_SECRET": "test", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON": "{}"}), patch.object(WINRISER.requests, "Session") as session:
            with self.assertRaisesRegex(RuntimeError, "start date"):
                WINRISER.main()
        session.assert_not_called()

    def test_collect_rejects_wrong_child_date(self):
        child = '<tr><td>-</td><td>2026-07-13</td><td>wnrwpsofc</td><td>1</td><td>1</td></tr>'
        with patch.object(WINRISER, "fetch_parent_reports", return_value=["parent"]), patch.object(WINRISER, "parse_parent_rows", return_value={self.day: "527"}), patch.object(WINRISER, "fetch_child_sources", return_value=child):
            with self.assertRaisesRegex(RuntimeError, "date differs"):
                WINRISER.collect_source(MagicMock(), self.day, None, {})

    def test_collect_rejects_conflicting_parent_ids(self):
        with patch.object(WINRISER, "fetch_parent_reports", return_value=["parent1", "parent2"]), patch.object(WINRISER, "parse_parent_rows", side_effect=[{self.day: "527"}, {self.day: "999"}]):
            with self.assertRaisesRegex(RuntimeError, "conflicting WPS parent"):
                WINRISER.collect_source(MagicMock(), self.day, self.day, {})

    def test_main_syncs_then_reads_back_and_reports_partial_coverage(self):
        api = self.api([])
        api.spreadsheets().values().get().execute.side_effect = [
            {"values": [self.headers]},
            {"values": [self.headers, ["2026-07-14", "Winriser", "气泡", 12, 3.5]]},
        ]
        output = io.StringIO()
        with patch.object(WINRISER.sys, "argv", ["sync", "--start-date", "2026-07-14", "--end-date", "2026-07-14"]), patch.dict(WINRISER.os.environ, {"WINRISER_LOGIN_SECRET": "test", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON": "{}"}), patch.object(WINRISER, "login"), patch.object(WINRISER, "collect_source", return_value=(self.source, {self.day: "527"})), patch.object(WINRISER, "sheets_service", return_value=api), patch.object(WINRISER.sys, "stdout", output):
            WINRISER.main()
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(summary["appended_rows"], 1)
        self.assertEqual(summary["verification"]["verified_records"], 1)
        self.assertEqual(api.spreadsheets().values().append().execute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
