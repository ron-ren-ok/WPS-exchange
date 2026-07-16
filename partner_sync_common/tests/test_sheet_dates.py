from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from partner_sync_common.sheet_dates import date_repair_plan, first_missing_date


def rows(*items):
    return {day: {"row": row, "values": [day.isoformat()]} for row, day in items}


def test_repairs_internal_blank_date_row_and_trailing_days():
    snapshot = rows(
        (10, date(2026, 7, 10)),
        (12, date(2026, 7, 12)),
    )
    internal, last_row, trailing = date_repair_plan(snapshot, date(2026, 7, 14))
    assert internal == [(11, date(2026, 7, 11))]
    assert last_row == 12
    assert trailing == [date(2026, 7, 13), date(2026, 7, 14)]
    assert first_missing_date(snapshot, date(2026, 7, 14)) == date(2026, 7, 11)


def test_rejects_ambiguous_internal_layout():
    snapshot = rows(
        (10, date(2026, 7, 10)),
        (13, date(2026, 7, 12)),
    )
    try:
        date_repair_plan(snapshot, date(2026, 7, 12))
    except RuntimeError as exc:
        assert "cannot safely repair" in str(exc)
    else:
        raise AssertionError("ambiguous date layout should stop")
