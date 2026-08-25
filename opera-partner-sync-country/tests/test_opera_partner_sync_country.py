import importlib.util
import io
import unittest
import zipfile
from datetime import date
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "src" / "opera_partner_sync_country.py"
spec = importlib.util.spec_from_file_location("sync", path)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class OperaCountrySyncTest(unittest.TestCase):
    def test_zip_csv_is_aggregated_by_country_and_surface(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr(
                "report.csv",
                "date,campaign,country,new_users,blood_volume\n"
                "2026-08-24,wpstest2/opera.exe,IT,205,102.5\n"
                "2026-08-24,wpstest2/opera.exe,IT,5,2.5\n"
                "2026-08-24,wpstest,DE,8,4\n",
            )
        result = sync.parse_report(raw.getvalue(), date(2026, 8, 24), date(2026, 8, 24))
        self.assertEqual(result[(date(2026, 8, 24), "IT", "换量弹窗")],
                         {"new_users": 210, "blood_volume": 105})
        self.assertEqual(result[(date(2026, 8, 24), "DE", "气泡")],
                         {"new_users": 8, "blood_volume": 4})


if __name__ == "__main__":
    unittest.main()
