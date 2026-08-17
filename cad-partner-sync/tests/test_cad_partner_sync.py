import importlib.util
import unittest
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "cad_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("cad_sync", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class CadPartnerSyncTests(unittest.TestCase):
    def test_reads_actual_source_shape_and_skips_summary_and_blank_operations(self):
        values = [
            ["日期", "运营位", "DWG安装量"],
            ["汇总", "", 38591],
            ["7月14日", "", 0],
            ["7月15日", "气泡", 62],
            ["7月16日", "气泡", 0],
            ["7月17日", "气泡", ""],
        ]
        records = SYNC.source_records(values, date(2026, 7, 1), date(2026, 8, 16))
        self.assertEqual(records[(date(2026, 7, 15), "CAD", "气泡")]["new_users"], 62)
        self.assertEqual(records[(date(2026, 7, 16), "CAD", "气泡")]["new_users"], 0)
        self.assertNotIn((date(2026, 7, 14), "CAD", ""), records)
        self.assertNotIn((date(2026, 7, 17), "CAD", "气泡"), records)

    def test_infers_year_safely_across_new_year(self):
        self.assertEqual(SYNC.parse_day("12月31日", date(2027, 1, 2)), date(2026, 12, 31))
        self.assertEqual(SYNC.parse_day("1月1日", date(2027, 1, 2)), date(2027, 1, 1))

    def test_rejects_missing_source_header(self):
        with self.assertRaisesRegex(RuntimeError, "DWG安装量"):
            SYNC.source_records([["日期", "运营位", "新增"]], date(2026, 1, 1), date(2026, 8, 16))

    def test_appends_new_users_without_blood_volume(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 7, 15), "CAD", "气泡")
        updates, appends, overwrites, conflicts = SYNC.plan_writes(headers, {}, {key: {"new_users": 62}})
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(conflicts, [])
        self.assertEqual(appends, [{"日期": date(2026, 7, 15), "合作方": "CAD", "运营位": "气泡", "新增": 62}])

    def test_updates_blank_new_users_without_touching_blood_volume(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 7, 15), "CAD", "气泡")
        existing = {key: {"row": 9, "values": [46218, "CAD", "气泡", "", 12.5]}}
        updates, appends, _, _ = SYNC.plan_writes(headers, existing, {key: {"new_users": 62}})
        self.assertEqual(appends, [])
        self.assertEqual(updates, [{"range": "'合作方新增血量'!D9", "values": [[62]]}])

    def test_existing_different_value_is_not_overwritten_by_default(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 7, 15), "CAD", "气泡")
        existing = {key: {"row": 9, "values": [46218, "CAD", "气泡", 60, ""]}}
        updates, appends, overwrites, conflicts = SYNC.plan_writes(headers, existing, {key: {"new_users": 62}})
        self.assertEqual(updates, [])
        self.assertEqual(appends, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(len(conflicts), 1)

    def test_first_run_includes_all_source_operations(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        source = {
            (date(2026, 7, 15), "CAD", "气泡"): {"new_users": 62},
            (date(2026, 8, 16), "CAD", "新运营位"): {"new_users": 8},
        }
        required = SYNC.required_source_records(headers, {}, source, date(2026, 7, 15), date(2026, 8, 16))
        self.assertEqual(required, source)


if __name__ == "__main__":
    unittest.main()
