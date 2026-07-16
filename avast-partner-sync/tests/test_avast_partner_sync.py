import importlib.util
import unittest
from datetime import date
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "src" / "avast_partner_sync.py"
SPEC = importlib.util.spec_from_file_location("avast", MODULE)
AVAST = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AVAST)

PAGE = """Split by Date & Geo
Country Code 2026-07-14 2026-07-15 Grand Total
RU 178 173 351
Total 178 173 351
Total $178 $173 $351
Costs / Installations / CPI
Total 999 999 999
"""


class AvastTests(unittest.TestCase):
    def test_parses_first_non_dollar_total_and_next_dollar_total(self):
        self.assertEqual(AVAST.parse_avast_page(PAGE), {
            date(2026, 7, 14): {"new_users": 178, "blood_volume": 178},
            date(2026, 7, 15): {"new_users": 173, "blood_volume": 173},
        })

    def test_rejects_reordered_totals(self):
        bad = PAGE.replace("Total 178 173 351\nTotal $178 $173 $351", "Total $178 $173 $351\nTotal 178 173 351")
        with self.assertRaisesRegex(ValueError, "immediately follow"):
            AVAST.parse_avast_page(bad)

    def test_column_names(self):
        self.assertEqual(AVAST.col_name(0), "A")
        self.assertEqual(AVAST.col_name(25), "Z")
        self.assertEqual(AVAST.col_name(26), "AA")


if __name__ == "__main__":
    unittest.main()