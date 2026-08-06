import importlib.util
import unittest
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "terabox_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("syncterabox", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class TeraBoxSyncTests(unittest.TestCase):
    def test_reads_operations_from_long_source_and_skips_summary(self):
        values = [["TeraBox 每日数据"], ["更新时间：2026-08-05"], ["日期", "运营位", "设备新增（uv）"], ["汇总", "", 99], ["2026-08-03", "气泡", 17], ["2026-08-04", "气泡", 0], ["2026-08-05", "气泡", ""], ["2026-08-05", "文档雷达", 6]]
        records = SYNC.source_records(values, date(2026, 8, 3), date(2026, 8, 5))
        self.assertEqual(records[(date(2026, 8, 3), "Terabox", "气泡")]["new_users"], 17)
        self.assertEqual(records[(date(2026, 8, 4), "Terabox", "气泡")]["new_users"], 0)
        self.assertNotIn((date(2026, 8, 5), "Terabox", "气泡"), records)
        self.assertEqual(records[(date(2026, 8, 5), "Terabox", "文档雷达")]["new_users"], 6)

    def test_rejects_missing_header_row_with_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "设备新增"):
            SYNC.source_records([["title"], ["not a table"]], date(2026, 8, 3), date(2026, 8, 5))

    def test_appends_new_users_without_blood_volume(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 8, 3), "Terabox", "气泡")
        updates, appends, overwrites, conflicts = SYNC.plan_writes(headers, {}, {key: {"new_users": 17}})
        self.assertEqual(updates, [])
        self.assertEqual(overwrites, [])
        self.assertEqual(conflicts, [])
        self.assertEqual(appends, [{"日期": date(2026, 8, 3), "合作方": "Terabox", "运营位": "气泡", "新增": 17}])

    def test_existing_blood_volume_is_untouched(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        key = (date(2026, 8, 3), "Terabox", "气泡")
        existing = {key: {"row": 9, "values": [46237, "Terabox", "气泡", "", 3.2]}}
        updates, appends, _, _ = SYNC.plan_writes(headers, existing, {key: {"new_users": 17}})
        self.assertEqual(appends, [])
        self.assertEqual(updates, [{"range": "'合作方新增血量'!D9", "values": [[17]]}])

    def test_new_source_operation_is_included_without_code_mapping(self):
        end = date(2026, 8, 5)
        source = {(date(2026, 8, 5), "Terabox", "气泡"): {"new_users": 17}, (date(2026, 8, 5), "Terabox", "新运营位"): {"new_users": 6}}
        required = SYNC.required_source_records(["日期", "合作方", "运营位", "新增", "血量"], {}, source, date(2026, 1, 1), end)
        self.assertEqual(required, source)

    def test_normal_run_is_incremental_and_repairs_recent_source_gap(self):
        headers = ["日期", "合作方", "运营位", "新增", "血量"]
        end = date(2026, 8, 5)
        source = {(date(2026, 7, 23), "Terabox", "气泡"): {"new_users": 10}, (date(2026, 8, 5), "Terabox", "气泡"): {"new_users": 17}}
        existing = {(date(2026, 8, 4), "Terabox", "气泡"): {"row": 5, "values": [46238, "Terabox", "气泡", 16, ""]}}
        required = SYNC.required_source_records(headers, existing, source, date(2026, 1, 1), end)
        self.assertEqual(set(required), set(source))


if __name__ == "__main__":
    unittest.main()