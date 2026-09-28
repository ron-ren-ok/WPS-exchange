import importlib.util
import unittest
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

MODULE = Path(__file__).resolve().parents[1] / "src" / "yandex_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("sync", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class SyncTests(unittest.TestCase):
    def test_oauth_token_accepts_plain_and_prefixed_values(self):
        self.assertEqual(SYNC.normalize_oauth_token("abc"), "abc")
        self.assertEqual(SYNC.normalize_oauth_token(" OAuth abc "), "abc")
        self.assertEqual(SYNC.normalize_oauth_token("OAuth\r\nabc\r\n"), "abc")

    def test_source_coverage_allows_only_a_leading_history_gap(self):
        start = date(2025, 9, 25)
        end = date(2026, 2, 13)
        totals = {date(2026, 2, 11): [1, 1], date(2026, 2, 12): [1, 1], date(2026, 2, 13): [1, 1]}
        self.assertEqual(SYNC.validate_source_coverage(totals, start, end), date(2026, 2, 11))
        with_gap = {date(2026, 2, 11): [1, 1], date(2026, 2, 13): [1, 1]}
        SYNC.validate_source_coverage(with_gap, start, end)
        self.assertEqual(with_gap[date(2026, 2, 12)], [0, 0])

    def test_request_blocks_exclude_dashboard_template_field(self):
        profile = SYNC.load_profile("popup")
        blocks = SYNC.build_blocks(profile, date(2026, 7, 23), date(2026, 7, 23))
        filters = next(block for block in blocks if block["id"] == "filters")
        fields = {field["id"]: field for field in filters["fields"]}
        self.assertNotIn("templates", fields)
        self.assertEqual(fields["period"]["value"], "2026.07.23-2026.07.23")
    def test_dynamic_column_names(self):
        self.assertEqual(SYNC.column_name(0), "A")
        self.assertEqual(SYNC.column_name(25), "Z")
        self.assertEqual(SYNC.column_name(26), "AA")

    def test_plans_append_for_new_long_format_record(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        profile = {"surface": "换量弹窗"}
        updates, appends, overwrites = SYNC.planned_writes(
            headers, {}, {date(2026, 7, 14): {"new_users": 178, "blood_volume": 178}}, profile, False
        )
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(appends, [{"日期": date(2026, 7, 14), "合作方": "Yandex", "运营位": "换量弹窗", "新增": 178, "血量": 178}])

    def test_nonblank_difference_requires_explicit_override(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        rows = {(date(2026, 7, 14), "Yandex", "换量弹窗"): {"row": 294, "values": [46217, "Yandex", "换量弹窗", 146, 146]}}
        profile = {"surface": "换量弹窗"}
        source = {date(2026, 7, 14): {"new_users": 178, "blood_volume": 178}}
        with self.assertRaises(RuntimeError):
            SYNC.planned_writes(headers, rows, source, profile, False)
        updates, appends, overwrites = SYNC.planned_writes(headers, rows, source, profile, True)
        self.assertEqual(len(updates), 2)
        self.assertEqual(appends, [])
        self.assertEqual(len(overwrites), 2)

    def test_default_window_is_seven_days_ending_yesterday(self):
        with patch.object(SYNC, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 9, 28, 3)
            self.assertEqual(SYNC.requested_dates(), (date(2026, 9, 21), date(2026, 9, 27)))

    def test_manual_dates_are_inclusive_and_override_window(self):
        self.assertEqual(SYNC.requested_dates("2026-07-01", "2026-07-31"), (date(2026, 7, 1), date(2026, 7, 31)))
        self.assertEqual(SYNC.requested_dates(end_date="2026-07-14"), (date(2026, 7, 8), date(2026, 7, 14)))
        with patch.object(SYNC, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 9, 28, 3)
            self.assertEqual(SYNC.requested_dates("2026-09-01"), (date(2026, 9, 1), date(2026, 9, 27)))
        with self.assertRaisesRegex(RuntimeError, "after end"):
            SYNC.requested_dates("2026-07-15", "2026-07-14")

    def test_missing_trailing_dates_remain_pending(self):
        day = date(2026, 7, 14)
        totals = {day: [1, 1]}
        SYNC.validate_source_coverage(totals, day, date(2026, 7, 15))
        self.assertEqual(totals, {day: [1, 1]})
        with self.assertRaisesRegex(RuntimeError, "no data"):
            SYNC.validate_source_coverage({}, day, day)

    def test_fetch_preserves_inferred_zero_provenance(self):
        profile = SYNC.load_profile("bubble")
        payload = {"rows": [{"default_field_dt": f"2026-07-{day}", "default_field_country": "Russia", "default_field_pack_id": 1157518, "msetupstatistics_setups": 3, "default_fixed_partner_reward_metric": 2} for day in (13, 15)]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch.object(SYNC, "urlopen", return_value=response):
            result = SYNC.fetch_daily(profile, date(2026, 7, 13), date(2026, 7, 15), "test")
        self.assertTrue(result[date(2026, 7, 14)]["inferred_zero"])
        self.assertFalse(result[date(2026, 7, 13)]["inferred_zero"])
        payload["rows"][0]["default_field_dt"] = "2026-07-12"
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch.object(SYNC, "urlopen", return_value=response), self.assertRaisesRegex(RuntimeError, "outside requested"):
            SYNC.fetch_daily(profile, date(2026, 7, 13), date(2026, 7, 15), "test")

    def test_missing_source_protects_nonzero_even_outside_available_days(self):
        day = date(2026, 7, 14)
        for metrics in (None, {"new_users": 0, "blood_volume": 0, "inferred_zero": True}):
            rows = {(day, "Yandex", "气泡"): {"values": [day.isoformat(), "Yandex", "气泡", 0, 5]}}
            source = {} if metrics is None else {day: metrics}
            with self.subTest(metrics=metrics), self.assertRaisesRegex(RuntimeError, "refusing all writes"):
                SYNC.protect_missing_source(list(SYNC.HEADERS), rows, source, {"surface": "气泡"}, day, day)

    def test_explicit_source_zero_can_overwrite_but_inferred_zero_cannot(self):
        day = date(2026, 7, 14)
        rows = {(day, "Yandex", "气泡"): {"row": 2, "values": [day.isoformat(), "Yandex", "气泡", 5, 5]}}
        source = {day: {"new_users": 0, "blood_volume": 0}}
        SYNC.protect_missing_source(list(SYNC.HEADERS), rows, source, {"surface": "气泡"}, day, day)
        self.assertEqual(len(SYNC.planned_writes(list(SYNC.HEADERS), rows, source, {"surface": "气泡"}, True)[0]), 2)
        source[day]["inferred_zero"] = True
        with self.assertRaisesRegex(RuntimeError, "refusing inferred zero"):
            SYNC.planned_writes(list(SYNC.HEADERS), rows, source, {"surface": "气泡"}, True)

    def test_inferred_zero_allows_existing_zero_and_unrelated_nonzero(self):
        day = date(2026, 7, 14)
        rows = {(day, "Yandex", "气泡"): {"values": [day.isoformat(), "Yandex", "气泡", 0, ""]}, (day, "Opera", "气泡"): {"values": [day.isoformat(), "Opera", "气泡", 5, 5]}}
        SYNC.protect_missing_source(list(SYNC.HEADERS), rows, {}, {"surface": "气泡"}, day, day)

    def test_full_sheet_read_includes_records_beyond_ten_thousand(self):
        service = MagicMock()
        api = service.spreadsheets.return_value.values.return_value
        api.get.return_value.execute.return_value = {"values": [list(SYNC.HEADERS)] + [[]] * 10000 + [["2026-07-14", "Yandex", "气泡", 1, 1]]}
        _, rows = SYNC.sheet_rows(service)
        self.assertEqual(rows[(date(2026, 7, 14), "Yandex", "气泡")]["row"], 10002)
        self.assertEqual(api.get.call_args.kwargs["range"], "'合作方新增血量'!A:E")

    def test_sheet_read_rejects_duplicate_keys(self):
        service = MagicMock()
        row = ["2026-07-14", "Yandex", "气泡", 1, 1]
        service.spreadsheets.return_value.values.return_value.get.return_value.execute.return_value = {"values": [list(SYNC.HEADERS), row, row]}
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            SYNC.sheet_rows(service)

    def test_transient_retries_are_bounded_and_auth_errors_are_not_retried(self):
        for error in (HTTPError("test", 429, "limited", {"retry-after": "7"}, None), HTTPError("test", 503, "busy", {}, None), TimeoutError(), URLError("connection lost")):
            action = MagicMock(side_effect=[error, "ok"])
            with self.subTest(error=error), patch.object(SYNC.time, "sleep") as sleep:
                self.assertEqual(SYNC.with_retry(action, "test"), "ok")
                self.assertEqual(action.call_count, 2)
                sleep.assert_called_once()
        action = MagicMock(side_effect=TimeoutError())
        with patch.object(SYNC.time, "sleep") as sleep, self.assertRaises(TimeoutError):
            SYNC.with_retry(action, "test")
        self.assertEqual(action.call_count, 4)
        self.assertEqual(sleep.call_count, 3)
        action = MagicMock(side_effect=HTTPError("test", 401, "denied", {}, None))
        with self.assertRaises(HTTPError):
            SYNC.with_retry(action, "test")
        self.assertEqual(action.call_count, 1)
        self.assertEqual(SYNC.retry_delay(HTTPError("test", 429, "limited", {"retry-after": "7"}, None), 0), 7)

    def append_fixture(self):
        day = date(2026, 7, 14)
        record = {"日期": day, "合作方": "Yandex", "运营位": "气泡", "新增": 3, "血量": 2}
        rows = {(day, "Yandex", "气泡"): {"values": [day.isoformat(), "Yandex", "气泡", 3, 2]}}
        return record, rows

    def test_append_timeout_after_commit_is_confirmed_without_resend(self):
        record, rows = self.append_fixture()
        with patch.object(SYNC, "append_once", side_effect=TimeoutError()) as append, patch.object(SYNC, "sheet_rows", return_value=(list(SYNC.HEADERS), rows)):
            SYNC.append_rows(None, list(SYNC.HEADERS), [record])
        append.assert_called_once()

    def test_append_uncertain_outcome_or_conflict_is_not_resent(self):
        record, rows = self.append_fixture()
        for error, current_rows, message in ((TimeoutError(), {}, "outcome uncertain"), (HTTPError("test", 503, "busy", {}, None), {}, "outcome uncertain"), (TimeoutError(), {(record["日期"], "Yandex", "气泡"): {"values": ["2026-07-14", "Yandex", "气泡", 99, 2]}}, "conflicting record")):
            with self.subTest(message=message), patch.object(SYNC, "append_once", side_effect=error) as append, patch.object(SYNC, "sheet_rows", return_value=(list(SYNC.HEADERS), current_rows)), self.assertRaisesRegex(RuntimeError, message):
                SYNC.append_rows(None, list(SYNC.HEADERS), [record])
            append.assert_called_once()

    def test_append_429_retries_only_absent_records(self):
        record, rows = self.append_fixture()
        second = dict(record, 日期=date(2026, 7, 15))
        with patch.object(SYNC, "append_once", side_effect=[HTTPError("test", 429, "limited", {}, None), None]) as append, patch.object(SYNC, "sheet_rows", return_value=(list(SYNC.HEADERS), rows)), patch.object(SYNC.time, "sleep"):
            SYNC.append_rows(None, list(SYNC.HEADERS), [record, second])
        self.assertEqual(append.call_count, 2)
        self.assertEqual(append.call_args.args[2], [second])

    def test_append_429_retries_are_bounded(self):
        record, _ = self.append_fixture()
        with patch.object(SYNC, "append_once", side_effect=HTTPError("test", 429, "limited", {}, None)) as append, patch.object(SYNC, "sheet_rows", return_value=(list(SYNC.HEADERS), {})), patch.object(SYNC.time, "sleep"), self.assertRaises(HTTPError):
            SYNC.append_rows(None, list(SYNC.HEADERS), [record])
        self.assertEqual(append.call_count, 4)

    def test_verification_fails_on_missing_or_changed_metrics(self):
        record, rows = self.append_fixture()
        profiles = [{"profile_id": "bubble", "surface": "气泡"}]
        source = {"bubble": {record["日期"]: {"new_users": 3, "blood_volume": 2}}}
        with patch.object(SYNC, "sheet_rows", return_value=(list(SYNC.HEADERS), rows)):
            self.assertEqual(SYNC.verify_writes(None, list(SYNC.HEADERS), profiles, source), 1)
        for current_rows in ({}, {(record["日期"], "Yandex", "气泡"): {"values": ["2026-07-14", "Yandex", "气泡", 3, 99]}}):
            with patch.object(SYNC, "sheet_rows", return_value=(list(SYNC.HEADERS), current_rows)), self.assertRaisesRegex(RuntimeError, "readback mismatch"):
                SYNC.verify_writes(None, list(SYNC.HEADERS), profiles, source)

    def test_main_plans_all_profiles_before_any_write(self):
        record, rows = self.append_fixture()
        profiles = [{"profile_id": "popup", "surface": "换量弹窗"}, {"profile_id": "bubble", "surface": "气泡"}]
        # Popup could append, but bubble's missing source must prevent all writes.
        source = {record["日期"]: {"new_users": 1, "blood_volume": 1}}
        service = MagicMock()
        with patch.dict(SYNC.os.environ, {"YANDEX_DISTRIBUTION_TOKEN": "test", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON": "{}"}), patch.object(SYNC.sys, "argv", ["sync", "--start-date", "2026-07-14", "--end-date", "2026-07-14"]), patch.object(SYNC, "google_service", return_value=service), patch.object(SYNC, "sheet_rows", return_value=(list(SYNC.HEADERS), rows)), patch.object(SYNC, "load_profile", side_effect=profiles), patch.object(SYNC, "fetch_daily", side_effect=[source, {}]), patch.object(SYNC, "append_rows") as append, self.assertRaisesRegex(RuntimeError, "refusing all writes"):
            SYNC.main()
        append.assert_not_called()
        service.spreadsheets.assert_not_called()

    def test_main_default_overwrites_and_explicit_disable_prevents_writes(self):
        day = date(2026, 7, 14)
        profiles = [{"profile_id": "popup", "surface": "换量弹窗"}, {"profile_id": "bubble", "surface": "气泡"}]
        rows = {(day, "Yandex", profile["surface"]): {"row": index + 2, "values": [day.isoformat(), "Yandex", profile["surface"], 1, 1]} for index, profile in enumerate(profiles)}
        source = {day: {"new_users": 3, "blood_volume": 2}}
        for flag in ([], ["--no-allow-overwrite"]):
            service = MagicMock()
            with self.subTest(flag=flag), patch.dict(SYNC.os.environ, {"YANDEX_DISTRIBUTION_TOKEN": "test", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON": "{}"}), patch.object(SYNC.sys, "argv", ["sync", "--start-date", "2026-07-14", "--end-date", "2026-07-14"] + flag), patch.object(SYNC, "google_service", return_value=service), patch.object(SYNC, "sheet_rows", return_value=(list(SYNC.HEADERS), rows)), patch.object(SYNC, "load_profile", side_effect=profiles), patch.object(SYNC, "fetch_daily", return_value=source), patch.object(SYNC, "append_rows"), patch.object(SYNC, "verify_writes", return_value=2) as verify:
                if flag:
                    with self.assertRaisesRegex(RuntimeError, "conflicts"):
                        SYNC.main()
                    service.spreadsheets.assert_not_called()
                    verify.assert_not_called()
                else:
                    SYNC.main()
                    api = service.spreadsheets.return_value.values.return_value
                    self.assertEqual(len(api.batchUpdate.call_args.kwargs["body"]["data"]), 4)
                    verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
