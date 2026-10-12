import importlib.util
import unittest
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import Mock, patch


MODULE = Path(__file__).resolve().parents[1] / "src" / "ccleaner_partner_sync_country.py"
SPEC = importlib.util.spec_from_file_location("ccleaner_country", MODULE)
SYNC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SYNC)
COOKIE = "mmm_ccl_ppi_008_617_b"
DAY = date(2026, 10, 10)
KEY = (DAY, "US", "气泡")


def report(installs):
    return f"date,cookie,country,installs\n2026-10-10,{COOKIE},US,{installs}\n".encode()


class CCleanerCountrySyncTests(unittest.TestCase):
    def test_actual_attachment_schema_and_sample(self):
        raw = (Path(__file__).parent / "fixtures" / "wps_sample.csv").read_bytes()
        source = SYNC.parse_report(raw)
        selected, start, end = SYNC.latest_three_days(source)
        self.assertEqual((start, end), (date(2026, 10, 8), DAY))
        self.assertEqual(selected, {
            (date(2026, 10, 8), "DE", "气泡"): {"new": 175},
            (date(2026, 10, 9), "DE", "气泡"): {"new": 380},
            (DAY, "DE", "气泡"): {"new": 101},
        })

    def test_cookie_wins_over_campaign_and_other_partners_are_excluded(self):
        raw = (
            "date,campaign,cookie,country,installs,PPI\n"
            f"2026-10-10,other,{COOKIE},us,10,999\n"
            f"2026-10-10,other,{COOKIE},US,2,999\n"
            "bad-date,other,mmm_wps_ppi_008_595_b,invalid,invalid,999\n"
            f"2026-10-10,{COOKIE},other,US,999,999\n"
            f"2026-10-10,other,{COOKIE},,999,999\n"
            f"2026-10-10,other,{COOKIE},DE,0,999\n"
        ).encode()
        self.assertEqual(SYNC.parse_report(raw), {
            KEY: {"new": 12}, (DAY, "DE", "气泡"): {"new": 0},
        })

    def test_unique_cookie_column_fallback(self):
        self.assertEqual(SYNC.parse_report(report(3).replace(b"cookie", b"Sub ID")), {KEY: {"new": 3}})

    def test_ambiguous_cookie_column_is_rejected(self):
        raw = f"date,id1,id2,country,installs\n2026-10-10,{COOKIE},{COOKIE},US,2\n".encode()
        with self.assertRaisesRegex(RuntimeError, "uniquely locate"):
            SYNC.parse_report(raw)

    def test_invalid_target_country_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "invalid CCleaner country code"):
            SYNC.parse_report(report(3).replace(b",US,", b",USA,"))

    def test_default_and_explicit_windows(self):
        source = {(date(2026, 10, day), "US", "气泡"): {"new": day} for day in range(5, 11)}
        selected, start, end = SYNC.latest_three_days(source)
        self.assertEqual((start, end, len(selected)), (date(2026, 10, 8), DAY, 3))
        selected, start, end = SYNC.latest_three_days(source, date(2026, 10, 5), date(2026, 10, 9))
        self.assertEqual((start, end, len(selected)), (date(2026, 10, 5), date(2026, 10, 9), 5))
        _, start, end = SYNC.latest_three_days(source, end=date(2026, 10, 12))
        self.assertEqual((start, end), (date(2026, 10, 8), DAY))

    def test_empty_and_reversed_windows_are_rejected(self):
        for start, end in ((date(2026, 10, 11), DAY), (None, date(2026, 10, 1))):
            with self.assertRaises(RuntimeError):
                SYNC.latest_three_days({KEY: {"new": 3}}, start, end)

    def test_prices_use_ccleaner_and_country_and_bubble(self):
        api = Mock()
        api.spreadsheets().values().get().execute.return_value = {"values": [
            list(SYNC.PRICE_HEADERS),
            ["Avast", "US", "气泡", 99],
            ["CCleaner", "US", "气泡", 1.5],
            ["CCleaner", "US", "换量弹窗", 50],
        ]}
        priced, missing = SYNC.apply_prices({KEY: {"new": 4}}, SYNC.price_index(api))
        self.assertEqual(priced, {KEY: {"new": 4, "blood": 6}})
        self.assertEqual(missing, [])

    def test_missing_price_preserves_existing_blood_and_appends_blank(self):
        source, missing = SYNC.apply_prices({KEY: {"new": 4}}, {})
        self.assertEqual(missing, ["2026-10-10 US/气泡"])
        found = {(DAY, "CCleaner", "US", "气泡"): (2, [DAY.isoformat(), "CCleaner", "US", "气泡", 2, 9])}
        updates, appends, _ = SYNC.plan(list(SYNC.HEADERS), found, source)
        self.assertEqual(updates, [{"range": "'合作方新增血量分国家'!E2", "values": [[4]]}])
        self.assertEqual(appends, [])
        _, appends, _ = SYNC.plan(list(SYNC.HEADERS), {}, source)
        self.assertEqual((appends[0]["合作方"], appends[0]["运营位"], appends[0]["血量"]), ("CCleaner", "气泡", ""))

    def test_unchanged_values_do_not_write(self):
        found = {(DAY, "CCleaner", "US", "气泡"): (2, [DAY.isoformat(), "CCleaner", "US", "气泡", 4, 6])}
        self.assertEqual(SYNC.plan(list(SYNC.HEADERS), found, {KEY: {"new": 4, "blood": 6}}), ([], [], []))

    def test_reads_same_verified_mail_and_csv_attachments_only(self):
        def mail(sender, subject, filename):
            msg = EmailMessage()
            msg["From"], msg["Subject"] = sender, subject
            msg.set_content("report")
            msg.add_attachment(report(3), maintype="text", subtype="csv", filename=filename)
            return msg.as_bytes()
        messages = {
            b"1": mail(SYNC.SENDER, SYNC.SUBJECT_PREFIX + "2026-10-10", "report.csv"),
            b"2": mail("other@example.com", SYNC.SUBJECT_PREFIX, "report.csv"),
            b"3": mail(SYNC.SENDER, "Other report", "report.csv"),
            b"4": mail(SYNC.SENDER, SYNC.SUBJECT_PREFIX, "report.pdf"),
        }
        client = Mock()
        client.list.return_value = ("OK", [])
        client.select.return_value = ("OK", [])
        client.uid.side_effect = lambda command, *args: (
            ("OK", [b"1 2 3 4"]) if command == "search"
            else ("OK", [(b"RFC822", messages[args[0]])])
        )
        self.assertEqual(SYNC.SUBJECT_PREFIX, "Daily Performance Snapshot from Avast | WPS |")
        self.assertEqual(list(SYNC.reports(client)), [report(3)])
        client.select.assert_called_once_with("INBOX", readonly=True)

    def test_newer_report_is_used_and_mail_connection_is_closed(self):
        api, client = Mock(), Mock()
        secrets = {name: "test" for name in ("GMAIL_IMAP_USERNAME", "GMAIL_APP_PASSWORD", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
        with patch.dict(SYNC.os.environ, secrets), patch.object(SYNC.sys, "argv", ["sync"]), \
                patch.object(SYNC, "gmail", return_value=client), \
                patch.object(SYNC, "reports", return_value=iter([report(7), report(2)])), \
                patch.object(SYNC, "service", return_value=api), \
                patch.object(SYNC, "price_index", return_value={("US", "气泡"): 1.5}), \
                patch.object(SYNC, "targets", side_effect=[
                    (list(SYNC.HEADERS), {}),
                    (list(SYNC.HEADERS), {(DAY, "CCleaner", "US", "气泡"): (2, [DAY.isoformat(), "CCleaner", "US", "气泡", 7, 10.5])}),
                ]), \
                patch.object(SYNC, "append") as append, patch("builtins.print"):
            SYNC.main()
        client.logout.assert_called_once()
        record = append.call_args.args[2][0]
        self.assertEqual((record["新增"], record["血量"]), (7, 10.5))

    def test_readback_rejects_wrong_or_missing_values(self):
        for found in ({}, {(DAY, "CCleaner", "US", "气泡"): (2, [DAY.isoformat(), "CCleaner", "US", "气泡", 2, 9])}):
            with patch.object(SYNC, "targets", return_value=(list(SYNC.HEADERS), found)):
                with self.assertRaisesRegex(RuntimeError, "write verification"):
                    SYNC.verify(Mock(), {KEY: {"new": 7, "blood": 10.5}})


if __name__ == "__main__":
    unittest.main()
