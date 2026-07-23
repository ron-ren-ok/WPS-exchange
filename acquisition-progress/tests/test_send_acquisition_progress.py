import importlib.util
import unittest
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


if __name__ == "__main__":
    unittest.main()
