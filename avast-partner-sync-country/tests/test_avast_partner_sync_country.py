import importlib.util
import unittest
from datetime import date
from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "src" / "avast_partner_sync_country.py"
SPEC = importlib.util.spec_from_file_location("sync", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)


class AvastCountrySyncTests(unittest.TestCase):
    def test_csv_aggregates_the_four_requested_campaigns(self):
        raw = (
            "Date,Campaign,Country Code,Install Count,PPI\n"
            "2026-08-23,mmm_wps_ppi_008_595_b,DE,10,5\n"
            "2026-08-23,mmm_wps_ppi_008_595_b,DE,2,1\n"
            "2026-08-24,mmm_wps_ppi_008_595_a,US,4,2.5\n"
            "2026-08-24,mmm_wps_ppi_008_595_e,BR,6,3\n"
            "2026-08-24,mmm_wps_ppi_008_595_c,IT,8,4\n"
            "2026-08-24,mmm_wps_ppi_008_595_b,,999,999\n"
            "2026-08-24,ignored,US,99,99\n"
        ).encode()
        rows = SYNC.parse_report(raw)
        self.assertEqual(rows[(date(2026, 8, 23), "DE", "avast气泡")], {"new": 12})
        self.assertEqual(rows[(date(2026, 8, 24), "US", "avast换量弹窗")], {"new": 4})
        self.assertEqual(rows[(date(2026, 8, 24), "BR", "avast文档雷达")], {"new": 6})
        self.assertEqual(rows[(date(2026, 8, 24), "IT", "avast卸载后弹出H5")], {"new": 8})
    def test_finds_campaign_column_by_the_requested_ids_and_uses_latest_data_day(self):
        raw = (
            "Date,Sub ID,Country Code,Install Count,Total Revenue\n"
            "2026-08-21,mmm_wps_ppi_008_595_b,DE,1,1\n"
            "2026-08-22,mmm_wps_ppi_008_595_b,DE,2,2\n"
            "2026-08-23,mmm_wps_ppi_008_595_b,DE,3,3\n"
            "2026-08-24,mmm_wps_ppi_008_595_b,DE,4,4\n"
        ).encode()
        selected, start, end = SYNC.latest_three_days(SYNC.parse_report(raw))
        self.assertEqual((start, end), (date(2026, 8, 22), date(2026, 8, 24)))
        self.assertEqual(set(day for day, _, _ in selected), {date(2026, 8, 22), date(2026, 8, 23), date(2026, 8, 24)})

    def test_existing_key_is_overwritten(self):
        headers = ["日期", "合作方", "国家代码", "运营位", "新增", "血量"]
        key = (date(2026, 8, 24), "Avast", "US", "avast换量弹窗")
        updates, appends, overwrites = SYNC.plan(headers, {key: (9, [46258, "Avast", "US", "avast换量弹窗", 1, 1])}, {(date(2026, 8, 24), "US", "avast换量弹窗"): {"new": 4, "blood": 2.5}})
        self.assertEqual(appends, [])
        self.assertEqual(len(updates), 2)
        self.assertEqual(len(overwrites), 2)

    def test_applies_matching_country_and_operation_price(self):
        source = {(date(2026, 8, 24), "US", "avast换量弹窗"): {"new": 4}}
        priced, missing = SYNC.apply_prices(source, {("US", "换量弹窗"): 1.8})
        self.assertEqual(missing, [])
        self.assertEqual(priced, {(date(2026, 8, 24), "US", "avast换量弹窗"): {"new": 4, "blood": 7.2}})

    def test_keeps_new_users_when_price_is_missing(self):
        priced, missing = SYNC.apply_prices({(date(2026, 8, 24), "US", "avast文档雷达"): {"new": 4}}, {})
        self.assertEqual(priced, {(date(2026, 8, 24), "US", "avast文档雷达"): {"new": 4}})
        self.assertEqual(missing, ["2026-08-24 US/文档雷达"])
    def test_accepts_only_the_requested_mailbox_sender_and_subject_prefix(self):
        self.assertEqual(SYNC.MAILBOX, "54lingbai@gmail.com")
        self.assertEqual(SYNC.SENDER, "online.acquisition@avast.com")
        self.assertEqual(SYNC.SUBJECT_PREFIX, "Daily Performance Snapshot from Avast | WPS |")


if __name__ == "__main__":
    unittest.main()
