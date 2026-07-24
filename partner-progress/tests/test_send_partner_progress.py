import importlib.util
import unittest
from datetime import date, timedelta
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "send_partner_progress.py"
SPEC = importlib.util.spec_from_file_location("partner_progress", MODULE)
PROGRESS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROGRESS)


def cell(text=None, number=None):
    result = {}
    if text is not None:
        result["formattedValue"] = str(text)
    if number is not None:
        result["effectiveValue"] = {"numberValue": number}
    return result


class PartnerProgressTests(unittest.TestCase):
    def test_long_table_uses_headers_and_aggregates_all_operations(self):
        rows = [
            [cell("运营位"), cell("血量"), cell("日期"), cell("合作方"), cell("新增")],
            [cell("气泡"), cell(number=100), cell(number=46217), cell("Opera"), cell(number=1000)],
            [cell("换量弹窗"), cell(number=200), cell(number=46217), cell("Opera"), cell(number=2000)],
        ]
        records = PROGRESS.source_records(rows)
        self.assertEqual(records[0]["partner"], "Opera")
        self.assertEqual(records[0]["operation"], "气泡")
        partners = [{"name": "Opera", "target_metric": "revenue", "target": 3}]
        series = PROGRESS.make_series(records, partners)
        self.assertAlmostEqual(series["Opera"]["new"][date(2026, 7, 14)], 0.3)
        self.assertEqual(series["Opera"]["revenue"][date(2026, 7, 14)], 0.03)

    def test_long_table_range_has_no_fixed_row_cap(self):
        self.assertIn('f"{SOURCE_SHEET}!A:E"', Path(MODULE).read_text(encoding="utf-8"))

    def test_weekly_prediction_line_has_twelve_spark_columns(self):
        series = {date(2026, 5, 1) + timedelta(days=offset): float(offset + 1) for offset in range(84)}
        line = PROGRESS.weekly_prediction_line("revenue", series, date(2026, 7, 23))
        chart = line.split()[-1].rstrip("**")
        self.assertEqual(len(chart), 12)


if __name__ == "__main__":
    unittest.main()