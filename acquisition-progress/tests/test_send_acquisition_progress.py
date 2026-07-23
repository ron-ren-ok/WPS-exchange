import importlib.util
import unittest
from unittest.mock import patch
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "send_acquisition_progress.py"
SPEC = importlib.util.spec_from_file_location("acquisition_progress", MODULE)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


def cell(text=None, number=None):
    result = {}
    if text is not None: result["formattedValue"] = str(text)
    if number is not None: result["effectiveValue"] = {"numberValue": number}
    return result


class AcquisitionProgressTests(unittest.TestCase):
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
            {"date": date(2026, 7, 22), "channel": "三方换量"},
            {"date": date(2026, 7, 22), "channel": "安卓导PC"},
        ]
        self.assertEqual(REPORT.missing_data_notice(records, date(2026, 7, 22)), "注意：7月22日Affiliate数据为空，请检查。")


    def test_card_metrics_use_paragraph_breaks(self):
        rows = [[cell("日期"), cell("渠道"), cell("新增设备数"), cell("近30日活跃设备数_MAD")]]
        for channel in ("三方换量", "安卓导PC", "Affiliate"):
            rows.append([cell(number=46225), cell(channel), cell(number=10000), cell(number=20000)])
        targets = {"新增": {"第三方": 1, "导量裂变": 1, "AFF联盟": 1}, "MAU": {"三方合作": 1, "导量&裂变": 1, "AFF联盟": 1}}
        with patch.object(REPORT, "target_config", return_value=targets):
            text = REPORT.report_text(rows, [], expected_date=date(2026, 7, 22))
        self.assertIn("**👉🏻三方**\n\n🔴昨日新增", text)
        self.assertIn("）\n\n🔴近30天 MAD", text)
        self.assertNotIn("数据截至", text)
        self.assertNotIn("`", text)


    def test_subtitle_includes_send_date_and_elapsed_month_progress(self):
        self.assertEqual(REPORT.report_subtitle(date(2026, 7, 23), date(2026, 7, 22)), "2026-07-23，时间进度 71.0%")


if __name__ == "__main__":
    unittest.main()
