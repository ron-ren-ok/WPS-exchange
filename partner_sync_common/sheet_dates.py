"""Repair chronological date rows without disturbing existing partner data."""
from datetime import date, timedelta


EXCEL_EPOCH = date(1899, 12, 30)


def date_serial(day):
    return (day - EXCEL_EPOCH).days


def date_repair_plan(rows, cutoff):
    """Return missing in-place dates and trailing days through ``cutoff``.

    Internal gaps are safe only when the physical row distance exactly matches
    the calendar-day distance. Any other layout is ambiguous and must stop.
    """
    if not rows:
        raise RuntimeError("target sheet has no dated rows")
    ordered = sorted(((item["row"], day) for day, item in rows.items()))
    internal = []
    for (row, day), (next_row, next_day) in zip(ordered, ordered[1:]):
        day_gap, row_gap = (next_day - day).days, next_row - row
        if day_gap <= 1:
            continue
        if row_gap != day_gap:
            raise RuntimeError(
                f"cannot safely repair date gap {day} to {next_day}: "
                f"calendar gap={day_gap}, row gap={row_gap}"
            )
        internal.extend((row + offset, day + timedelta(days=offset)) for offset in range(1, day_gap))
    last_row, last_day = ordered[-1]
    trailing = [last_day + timedelta(days=offset) for offset in range(1, max(0, (cutoff - last_day).days) + 1)]
    return internal, last_row, trailing


def first_missing_date(rows, cutoff):
    internal, last_row, trailing = date_repair_plan(rows, cutoff)
    candidates = [day for _, day in internal] + trailing
    return min(candidates) if candidates else cutoff


def sheet_id(service, spreadsheet_id, sheet_name):
    sheets = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute().get("sheets", [])
    matches = [item["properties"]["sheetId"] for item in sheets if item["properties"].get("title") == sheet_name]
    if len(matches) != 1:
        raise RuntimeError(f"target sheet not found uniquely: {sheet_name}")
    return matches[0]


def ensure_date_rows(service, spreadsheet_id, sheet_name, rows, cutoff):
    """Fill internal blank date cells and insert trailing date rows through cutoff.

    Only date cells are written. New trailing rows copy the preceding row's
    format and data validation, while all pre-existing later rows are shifted
    intact by the Sheets API.
    """
    internal, last_row, trailing = date_repair_plan(rows, cutoff)
    if not internal and not trailing:
        return []
    tab_id = sheet_id(service, spreadsheet_id, sheet_name)
    requests = []
    for row, _ in internal:
        requests.extend([
            {"copyPaste": {"source": {"sheetId": tab_id, "startRowIndex": row - 2, "endRowIndex": row - 1}, "destination": {"sheetId": tab_id, "startRowIndex": row - 1, "endRowIndex": row}, "pasteType": "PASTE_FORMAT", "pasteOrientation": "NORMAL"}},
            {"copyPaste": {"source": {"sheetId": tab_id, "startRowIndex": row - 2, "endRowIndex": row - 1}, "destination": {"sheetId": tab_id, "startRowIndex": row - 1, "endRowIndex": row}, "pasteType": "PASTE_DATA_VALIDATION", "pasteOrientation": "NORMAL"}},
        ])
    if trailing:
        start_index, end_index = last_row, last_row + len(trailing)
        requests.extend([
            {"insertDimension": {"range": {"sheetId": tab_id, "dimension": "ROWS", "startIndex": start_index, "endIndex": end_index}, "inheritFromBefore": True}},
            {"copyPaste": {"source": {"sheetId": tab_id, "startRowIndex": last_row - 1, "endRowIndex": last_row}, "destination": {"sheetId": tab_id, "startRowIndex": start_index, "endRowIndex": end_index}, "pasteType": "PASTE_FORMAT", "pasteOrientation": "NORMAL"}},
            {"copyPaste": {"source": {"sheetId": tab_id, "startRowIndex": last_row - 1, "endRowIndex": last_row}, "destination": {"sheetId": tab_id, "startRowIndex": start_index, "endRowIndex": end_index}, "pasteType": "PASTE_DATA_VALIDATION", "pasteOrientation": "NORMAL"}},
        ])
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()
    dates = list(internal) + [(last_row + index, day) for index, day in enumerate(trailing, start=1)]
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "USER_ENTERED", "data": [
            {"range": f"'{sheet_name}'!A{row}", "values": [[date_serial(day)]]}
            for row, day in dates
        ]},
    ).execute()
    return [day for _, day in dates]
