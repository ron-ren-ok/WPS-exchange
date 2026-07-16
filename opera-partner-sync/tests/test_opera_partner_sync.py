import importlib.util
import unittest
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "opera_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("opera", MODULE)
OPERA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPERA)

PDF_TEXT = """Opera for Computers distribution partner dashboard
Summary table
Day Campaign New Users Revenue
1 2026-07-12 wpstest 11,203 $943.79
2 2026-07-12 wpstest2/opera.exe 10,721 $709.46
3 2026-07-11 wpstest2/opera.exe 10,345 $678.62
4 2026-07-11 wpstest 13,228 $1,083.56
Performance
"""


class OperaTests(unittest.TestCase):
    def test_campaign_mapping(self):
        bubble = OPERA.parse_opera_text(PDF_TEXT, "wpstest")
        popup = OPERA.parse_opera_text(PDF_TEXT, "wpstest2/opera.exe")
        self.assertEqual(bubble[date(2026, 7, 12)], {"new_users": 11203, "blood_volume": 943.79})
        self.assertEqual(popup[date(2026, 7, 11)], {"new_users": 10345, "blood_volume": 678.62})

    def test_rejects_duplicate_campaign_date(self):
        duplicate = PDF_TEXT.replace("Performance", "5 2026-07-12 wpstest 2 $1.00\nPerformance")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            OPERA.parse_opera_text(duplicate, "wpstest")

    def test_column_names(self):
        self.assertEqual(OPERA.col_name(0), "A")
        self.assertEqual(OPERA.col_name(26), "AA")


if __name__ == "__main__":
    unittest.main()