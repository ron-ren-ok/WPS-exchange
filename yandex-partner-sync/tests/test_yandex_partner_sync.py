import importlib.util
import unittest
from datetime import date
from pathlib import Path

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

    def test_missing_data_backfill_starts_at_earliest_gap(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        profiles = [{"surface": "换量弹窗"}, {"surface": "气泡"}]
        rows = {
            (date(2026, 7, 13), "Yandex", "换量弹窗"): {"values": [46216, "Yandex", "换量弹窗", "", ""]},
            (date(2026, 7, 13), "Yandex", "气泡"): {"values": [46216, "Yandex", "气泡", 153, 153]},
            (date(2026, 7, 14), "Yandex", "换量弹窗"): {"values": [46217, "Yandex", "换量弹窗", 178, 178]},
            (date(2026, 7, 14), "Yandex", "气泡"): {"values": [46217, "Yandex", "气泡", 158, 158]},
        }
        self.assertEqual(SYNC.first_missing_day(headers, rows, profiles, date(2026, 7, 15)), date(2026, 7, 13))


if __name__ == "__main__":
    unittest.main()