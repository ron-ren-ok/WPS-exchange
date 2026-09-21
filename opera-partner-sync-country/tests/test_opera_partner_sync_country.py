import importlib.util
import io
import unittest
import zipfile
from datetime import date
from email.message import EmailMessage
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "src" / "opera_partner_sync_country.py"
spec = importlib.util.spec_from_file_location("sync", path)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class OperaCountrySyncTest(unittest.TestCase):
    def test_only_the_newest_matching_email_is_read(self):
        def message(attachment):
            result = EmailMessage()
            result["From"] = "Looker <noreply@lookermail.com>"
            result["Subject"] = sync.SUBJECT
            result.add_attachment(attachment, maintype="application", subtype="zip", filename="report.zip")
            return result.as_bytes()

        class FakeClient:
            def __init__(self):
                self.fetched_uids = []

            def list(self):
                return "OK", []

            def select(self, mailbox, readonly):
                return "OK", []

            def uid(self, command, *args):
                if command == "search":
                    return "OK", [b"100 200"]
                self.fetched_uids.append(args[0])
                messages = {b"100": message(b"historical"), b"200": message(b"newest")}
                return "OK", [(None, messages[args[0]])]

        client = FakeClient()
        self.assertEqual(list(sync.latest_zip_attachments(client)), [b"newest"])
        self.assertEqual(client.fetched_uids, [b"200"])

    def test_explicit_range_reads_matching_history_newest_first(self):
        class FakeClient:
            def __init__(self):
                self.fetched_uids = []

            def list(self):
                return "OK", []

            def select(self, mailbox, readonly):
                return "OK", []

            def uid(self, command, *args):
                if command == "search":
                    return "OK", [b"100 200"]
                self.fetched_uids.append(args[0])
                result = EmailMessage()
                result["From"] = "Looker <noreply@lookermail.com>"
                result["Subject"] = sync.SUBJECT
                result.add_attachment(args[0], maintype="application", subtype="zip", filename="report.zip")
                return "OK", [(None, result.as_bytes())]

        client = FakeClient()
        self.assertEqual(list(sync.zip_attachments(client, include_history=True)), [b"200", b"100"])
        self.assertEqual(client.fetched_uids, [b"200", b"100"])

    def test_historical_source_keeps_newest_duplicate_record(self):
        def report(day, installs):
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, "w") as archive:
                archive.writestr("report.csv", "date,campaign,country,new_users,blood_volume\n"
                                 f"{day},wpstest,DE,{installs},4\n")
            return raw.getvalue()

        result = sync.source_from_attachments(
            [report("2026-08-24", 8), report("2026-08-24", 2)],
            date(2026, 8, 24), date(2026, 8, 24))
        self.assertEqual(result[(date(2026, 8, 24), "DE", "气泡")],
                         {"new_users": 8, "blood_volume": 4})
    def test_zip_csv_is_aggregated_by_country_and_surface(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("links.csv", "Links Block Summary\nCrashes\n")
            archive.writestr(
                "report.csv",
                "date,campaign,country,new_users,blood_volume\n"
                "2026-08-24,wpstest2/opera.exe,IT,205,102.5\n"
                "2026-08-24,wpstest2/opera.exe,IT,5,2.5\n"
                "2026-08-24,wpstest,DE,8,4\n"
                "2026-08-24,wpstest,UNKNOWN,99,49.5\n",
            )
        result = sync.parse_report(raw.getvalue(), date(2026, 8, 24), date(2026, 8, 24))
        self.assertEqual(result[(date(2026, 8, 24), "IT", "换量弹窗")],
                         {"new_users": 210, "blood_volume": 105})
        self.assertEqual(result[(date(2026, 8, 24), "DE", "气泡")],
                         {"new_users": 8, "blood_volume": 4})


    def test_default_window_covers_three_calendar_days(self):
        self.assertEqual(sync.default_start(date(2026, 8, 25)), date(2026, 8, 23))


if __name__ == "__main__":
    unittest.main()
