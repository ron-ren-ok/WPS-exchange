import importlib.util
import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path
from requests import HTTPError, Response

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "send_acquisition_progress.py"
SPEC = importlib.util.spec_from_file_location("acquisition_progress", MODULE)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def cell(text=None, number=None):
    result = {}
    if text is not None: result["formattedValue"] = str(text)
    if number is not None: result["effectiveValue"] = {"numberValue": number}
    return result


def http_error(status_code):
    response = Response()
    response.status_code = status_code
    return HTTPError(response=response)


class AcquisitionProgressTests(unittest.TestCase):
    def test_request_rows_retries_503_twice_with_five_second_delays(self):
        response = type("Response", (), {"raise_for_status": lambda self: None, "json": lambda self: {"sheets": [{"data": [{"rowData": []}]}]}})()
        session = type("Session", (), {"get": __import__("unittest").mock.Mock(side_effect=[http_error(503), http_error(503), response])})()
        with patch.object(REPORT.time, "sleep") as sleep:
            self.assertEqual(REPORT.request_rows(session, "Source!A:D"), [])
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(sleep.call_args_list, [__import__("unittest").mock.call(5), __import__("unittest").mock.call(5)])

    def test_request_rows_does_not_retry_403(self):
        session = type("Session", (), {"get": __import__("unittest").mock.Mock(side_effect=http_error(403))})()
        with patch.object(REPORT.time, "sleep") as sleep:
            with self.assertRaises(HTTPError):
                REPORT.request_rows(session, "Source!A:D")
        self.assertEqual(session.get.call_count, 1)
        sleep.assert_not_called()

    def test_source_records_use_headers_not_fixed_columns(self):
        rows = [[cell("渠道"), cell("新增设备数"), cell("日期"), cell("近30日活跃设备数_MAD")], [cell("Affiliate"), cell(number=10000), cell(number=46225), cell(number=20000)]]
        record = REPORT.source_records(rows)[0]
        self.assertEqual(record["channel"], "Affiliate")
        self.assertEqual(record["date"], date(2026, 7, 22))
        self.assertEqual(record["new"], 10000)

    def test_weekly_trend_exposes_direction(self):
        series = {date(2026, 5, 1) + __import__("datetime").timedelta(days=offset): float(offset + 1) for offset in range(84)}
        _, trend = REPORT.weekly_sparkline(series, date(2026, 7, 23))
        self.assertIn("上涨", trend)


    def test_missing_expected_day_returns_direct_notice(self):
        rows = [
            [cell("日期"), cell("渠道"), cell("新增设备数"), cell("近30日活跃设备数_MAD")],
            [cell(number=46224), cell("三方换量"), cell(number=10000), cell(number=10000)],
            [cell(number=46224), cell("安卓导PC"), cell(number=10000), cell(number=10000)],
            [cell(number=46224), cell("Affiliate"), cell(number=10000), cell(number=10000)],
        ]
        self.assertEqual(REPORT.report_text(rows, [], expected_date=date(2026, 7, 22)), "注意：7月22日数据为空，请检查。")

    def test_missing_single_channel_names_the_channel(self):
        records = [
            {"date": date(2026, 7, 22), "channel": "三方换量", "new": 1, "mau": 1},
            {"date": date(2026, 7, 22), "channel": "安卓导PC", "new": 1, "mau": 1},
        ]
        self.assertEqual(REPORT.missing_data_notice(records, date(2026, 7, 22)), "注意：7月22日Affiliate数据为空，请检查。")


    def test_partial_channel_data_sends_direct_notice(self):
        rows = [
            [cell("日期"), cell("渠道"), cell("新增设备数"), cell("近30日活跃设备数_MAD")],
            [cell(number=46226), cell("三方换量"), cell(), cell(number=10000)],
            [cell(number=46226), cell("安卓导PC"), cell(number=10000), cell(number=10000)],
            [cell(number=46226), cell("Affiliate"), cell(number=10000), cell(number=10000)],
        ]
        self.assertEqual(REPORT.report_text(rows, [], expected_date=date(2026, 7, 23)), "注意：7月23日三方换量数据为空，请检查。")


    def test_card_metrics_use_paragraph_breaks(self):
        rows = [[cell("日期"), cell("渠道"), cell("新增设备数"), cell("近30日活跃设备数_MAD")]]
        for channel in ("三方换量", "安卓导PC", "Affiliate"):
            rows.append([cell(number=46225), cell(channel), cell(number=10000), cell(number=20000)])
        targets = {"新增": {"第三方": 1, "导量裂变": 1, "AFF联盟": 1}, "MAU": {"三方合作": 1, "导量&裂变": 1, "AFF联盟": 1}}
        with patch.object(REPORT, "target_config", return_value=targets):
            text = REPORT.report_text(rows, [], expected_date=date(2026, 7, 22))
        self.assertIn("**➡️三方换量**\n\n🔴昨日新增", text)
        self.assertIn("）\n\n🔴近30天 MAD", text)
        self.assertNotIn("数据截至", text)
        self.assertNotIn("`", text)

    def test_live_source_dimensions_are_all_reported_and_unmapped_targets_are_flagged(self):
        rows = [[cell("日期"), cell("渠道"), cell("新增设备数"), cell("近30日活跃设备数_MAD")]]
        for channel in ("三方合作", "安卓导PC", "SEM", "官网", "Affiliate", "其他", "微软商店", "SEO", "整体"):
            rows.append([cell(number=46225), cell(channel), cell(number=10000), cell(number=20000)])
        targets = {"新增": {"三方合作": 1}, "MAU": {"三方合作": 2}}
        with patch.object(REPORT, "target_config", return_value=targets):
            text = REPORT.report_text(rows, [], expected_date=date(2026, 7, 22))
        self.assertIn("**➡️SEM**", text)
        self.assertIn("**➡️整体**", text)
        self.assertIn("目标待同步", text)
        self.assertIn("**1.00万 / 1.00万**", text)

    def test_target_config_accepts_new_channel_headers(self):
        rows = [
            [cell("MAU")],
            [cell("活跃"), cell("SEM"), cell(), cell("官网"), cell()],
            [cell("Month"), cell("2025"), cell("2026-目标"), cell("2025"), cell("2026-目标")],
            [cell("9月"), cell(number=1), cell(number=200), cell(number=1), cell(number=300)],
            [cell("新增")],
            [cell("新增"), cell("SEM"), cell(), cell("官网"), cell()],
            [cell("Month"), cell("2025"), cell("2026-目标"), cell("2025"), cell("2026-目标")],
            [cell("9月"), cell(number=1), cell(number=20), cell(number=1), cell(number=30)],
        ]
        self.assertEqual(REPORT.target_config(rows, 9), {"MAU": {"SEM": 200, "官网": 300}, "新增": {"SEM": 20, "官网": 30}})

    def test_target_config_accepts_one_2026_target_column_per_channel(self):
        rows = [
            [cell("MAU")],
            [cell("渠道"), cell("SEM"), cell("Mac")],
            [cell("Month"), cell("2026-目标"), cell("2026-目标")],
            [cell("9月"), cell(number=720.7), cell(number=121.8)],
            [cell("新增")],
            [cell("渠道"), cell("SEM"), cell("Mac")],
            [cell("Month"), cell("2026-目标"), cell("2026-目标")],
            [cell("9月"), cell(number=280), cell(number=14.7)],
        ]
        self.assertEqual(
            REPORT.target_config(rows, 9),
            {"MAU": {"SEM": 720.7, "Mac": 121.8}, "新增": {"SEM": 280, "Mac": 14.7}},
        )

    def test_target_only_channel_is_reported_as_not_returned(self):
        rows = [
            [cell("日期"), cell("渠道"), cell("新增设备数"), cell("近30日活跃设备数_MAD")],
            [cell(number=46225), cell("SEM"), cell(number=10000), cell(number=20000)],
        ]
        targets = {"新增": {"SEM": 1, "Mac": 2}, "MAU": {"SEM": 2, "Mac": 3}}
        with patch.object(REPORT, "target_config", return_value=targets):
            text = REPORT.report_text(rows, [], expected_date=date(2026, 7, 22))
        self.assertIn("**➡️Mac**", text)
        self.assertIn("暂未回传 / 2.00万目标", text)

    def test_report_sections_keep_the_requested_channels_separate(self):
        rows = [[cell("日期"), cell("渠道"), cell("新增设备数"), cell("近30日活跃设备数_MAD")]]
        channels = ("整体", "三方合作", "Affiliate", "安卓导PC", "SEM", "官网", "其他", "微软商店", "SEO", "Mac")
        for channel in channels:
            rows.append([cell(number=46225), cell(channel), cell(number=10000), cell(number=20000)])
        targets = {"新增": {channel: 1 for channel in channels}, "MAU": {channel: 2 for channel in channels}}
        with patch.object(REPORT, "target_config", return_value=targets):
            partner = REPORT.report_text(rows, [], expected_date=date(2026, 7, 22), channels=REPORT.REPORT_SECTIONS["partner"])
            other = REPORT.report_text(rows, [], expected_date=date(2026, 7, 22), channels=REPORT.REPORT_SECTIONS["other"])
        self.assertIn("**➡️三方合作**", partner)
        self.assertIn("**➡️Affiliate**", partner)
        self.assertNotIn("**➡️整体**", partner)
        self.assertIn("**➡️Mac**", other)
        self.assertNotIn("**➡️Affiliate**", other)


    def test_subtitle_includes_send_date_and_elapsed_month_progress(self):
        self.assertEqual(REPORT.report_subtitle(date(2026, 7, 23), date(2026, 7, 22)), "2026-07-23，时间进度 71.0%")


if __name__ == "__main__":
    unittest.main()
