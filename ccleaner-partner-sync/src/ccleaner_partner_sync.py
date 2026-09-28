"""Sync complete CCleaner daily install/cost pairs from Gmail to Sheets."""
import argparse
import email
import imaplib
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from email.utils import parseaddr
from zoneinfo import ZoneInfo

import pdfplumber


SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
ORIGINAL_SENDER = "no-reply-powerbi@microsoft.com"
FORWARDER = "partner@wps.com"
SUBJECT = "CCleaner - WPS - B - Daily Report PBI"
PARTNER = "CCleaner"
OPERATION = "气泡"
HEADERS = ("日期", "合作方", "运营位", "新增", "血量")
RETRIES = 3
REQUEST_TIMEOUT = 30
DATE_TOKEN = (
    r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|\d{1,2}[./]\d{1,2}[./]\d{4}"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
    r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
)


def parse_day(value):
    text = str(value).strip()
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y",
                "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported date: {value!r}")


def number(value):
    text = str(value).replace("\u00a0", "").replace(",", "").replace("$", "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise ValueError(f"invalid numeric value: {value!r}")
    parsed = float(text)
    return int(parsed) if parsed.is_integer() else parsed


def header_days(region):
    """Find the last contiguous date-header block within this table only."""
    blocks, current = [], []
    for line in region.splitlines():
        tokens = re.findall(DATE_TOKEN, line, flags=re.IGNORECASE)
        if re.search(r"\b(?:generated|refreshed|printed)\b", line, re.IGNORECASE):
            tokens = []
        if tokens:
            current.extend(tokens)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return [parse_day(day) for day in blocks[-1]] if blocks else []


def parse_ccleaner_page(page_text, diagnostics=None, positioned=None):
    """Return only complete pairs; never infer where a missing column belongs."""
    total_pattern = r"(?im)^[^\w$\d\r\n]*(?:Grand[ \t]+)?Total[ \t]+(.+)$"
    diagnostics = diagnostics if diagnostics is not None else []
    metrics, previous_end = {}, 0
    for match in re.finditer(total_pattern, page_text):
        total_text = match.group(1)
        metric = "blood_volume" if "$" in total_text else "new_users"
        region = page_text[previous_end:match.start()]
        previous_end = match.end()
        if metric in metrics:
            continue
        days = header_days(region)
        metrics[metric] = {}
        if not days or len(days) != len(set(days)):
            diagnostics.append({"code": "invalid_date_header", "metric": metric})
            continue
        values = total_text.split()
        pattern = r"\$\d[\d,]*(?:\.\d+)?" if metric == "blood_volume" else r"\d[\d,]*"
        if not all(re.fullmatch(pattern, value) for value in values):
            diagnostics.append({"code": "invalid_metric", "metric": metric})
            continue
        if positioned is not None and metric in positioned:
            parsed = positioned[metric]
            if not set(parsed).issubset(days):
                diagnostics.append({"code": "positioned_header_mismatch", "metric": metric})
                continue
            metrics[metric] = parsed
        elif len(values) != len(days) + 1:
            diagnostics.append({"code": "ambiguous_columns", "metric": metric,
                                "dates": [day.isoformat() for day in days]})
        else:
            metrics[metric] = dict(zip(days, map(number, values[:-1])))
    if not metrics:
        raise ValueError("CCleaner daily-install Total row or date headers were not found")
    installs, costs = metrics.get("new_users", {}), metrics.get("blood_volume", {})
    partial = installs.keys() ^ costs.keys()
    if partial:
        diagnostics.append({"code": "partial_metrics", "dates": [day.isoformat() for day in sorted(partial)]})
    return {day: {"new_users": installs[day], "blood_volume": costs[day]}
            for day in installs.keys() & costs.keys()}


def positioned_metrics(page):
    """Use PDF column positions to retain other dates when one cell is blank."""
    lines = []
    for word in sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"])):
        if not lines or abs(lines[-1][0]["top"] - word["top"]) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    result, seen, table_start = {}, set(), 0
    for index, line in enumerate(lines):
        line.sort(key=lambda w: w["x0"])
        # A header may end in Total too; only numeric Total rows delimit tables.
        if not line or line[0]["text"].lower() not in ("total", "grand"):
            continue
        total = next((i for i, w in enumerate(line) if w["text"].lower() == "total"), None)
        if total is None or not line[total + 1:]:
            continue
        values = line[total + 1:]
        metric = "blood_volume" if any("$" in w["text"] for w in values) else "new_users"
        earlier_lines = lines[table_start:index]
        table_start = index + 1
        if metric in seen:
            continue
        seen.add(metric)
        pattern = r"\$\d[\d,]*(?:\.\d+)?" if metric == "blood_volume" else r"\d[\d,]*"
        if not all(re.fullmatch(pattern, w["text"]) for w in values):
            continue
        for earlier in reversed(earlier_lines):
            dates = [w for w in earlier if re.fullmatch(
                r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[./]\d{1,2}[./]\d{4}", w["text"])]
            if dates:
                break
        else:
            continue
        days = [parse_day(w["text"]) for w in dates]
        grand = [w for w in earlier if w["x0"] > dates[-1]["x1"] and w["text"].lower() in ("grand", "total")]
        if not grand or len(days) != len(set(days)):
            continue
        centers = [(w["x0"] + w["x1"]) / 2 for w in dates]
        grand_center = (min(w["x0"] for w in grand) + max(w["x1"] for w in grand)) / 2
        if centers != sorted(centers) or grand_center <= centers[-1]:
            continue
        boundaries = [(a + b) / 2 for a, b in zip(centers, centers[1:] + [grand_center])]
        parsed, valid, grand_values = {}, True, 0
        for word in values:
            center = (word["x0"] + word["x1"]) / 2
            column = next((i for i, right in enumerate(boundaries) if center < right), None)
            if column is None:
                grand_values += 1
                continue
            if days[column] in parsed or center < dates[0]["x0"] - 20:
                valid = False
                break
            parsed[days[column]] = number(word["text"])
        if valid and grand_values == 1:
            result[metric] = parsed
    return result


def pdf_rows(raw_pdf, diagnostics=None):
    with pdfplumber.open(io.BytesIO(raw_pdf)) as pdf:
        if not pdf.pages:
            raise ValueError("CCleaner attachment is empty")
        page = pdf.pages[0]
        return parse_ccleaner_page(page.extract_text() or "", diagnostics, positioned_metrics(page))


def sync_window(start_value, end_value, today):
    end = parse_day(end_value) if end_value else today - timedelta(days=1)
    start = parse_day(start_value) if start_value else end - timedelta(days=6)
    if start > end:
        raise ValueError("start date is after end date")
    return start, end, bool(start_value or end_value)


def retryable(exc):
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in (408, 429, 500, 502, 503, 504) or isinstance(exc, (TimeoutError, ConnectionError, imaplib.IMAP4.abort)) or (
        isinstance(exc, OSError) and not isinstance(exc, (FileNotFoundError, PermissionError)))


def retry_call(call):
    for attempt in range(RETRIES + 1):
        try:
            return call()
        except Exception as exc:
            if not retryable(exc) or attempt == RETRIES:
                raise
            time.sleep(2 ** attempt)


def sheets_execute(request):
    return retry_call(request.execute)


def gmail_imap_client(username, app_password):
    try:
        client = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=REQUEST_TIMEOUT)
        client.login(username.strip(), app_password.replace(" ", "").strip())
        return client
    except imaplib.IMAP4.abort:
        raise  # reconnect on a transient dropped connection, not an auth error
    except imaplib.IMAP4.error as exc:
        raise RuntimeError("Gmail IMAP login failed; check GMAIL_IMAP_USERNAME and GMAIL_APP_PASSWORD") from exc


def body_text(message):
    return "\n".join(
        part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in message.walk()
        if part.get_content_maintype() == "text" and part.get_payload(decode=True)
    )


def verified_sender(message):
    sent_by = parseaddr(message.get("From", ""))[1].lower()
    return sent_by == ORIGINAL_SENDER or (sent_by == FORWARDER and ORIGINAL_SENDER in body_text(message).lower())


def attachments(message):
    for part in message.walk():
        filename = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                yield filename, payload


def imap_messages(client, start, end):
    # Retry the full read session with a new connection in fetch_source.
    # Reusing a timed-out/aborted IMAP socket is unsafe.
    status, mailboxes = client.list()
    if status != "OK":
        raise RuntimeError("Gmail IMAP mailbox listing failed")
    all_mail = next((item.decode("utf-8", "replace").rsplit('"', 2)[-2] for item in mailboxes if b"\\All" in item), None)
    if client.select(all_mail or "INBOX", readonly=True)[0] != "OK":
        raise RuntimeError("Gmail IMAP could not open mailbox")
    status, data = client.uid("search", None, "SENTSINCE", start.strftime("%d-%b-%Y"),
                              "SENTBEFORE", (end + timedelta(days=1)).strftime("%d-%b-%Y"),
                              "SUBJECT", f'"{SUBJECT}"')
    if status != "OK":
        raise RuntimeError("Gmail IMAP search failed")
    for uid in reversed(data[0].split()):
        status, payload = client.uid("fetch", uid, "(BODY.PEEK[])")
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            raise RuntimeError(f"Gmail IMAP fetch failed for UID {uid.decode()}")
        message = email.message_from_bytes(payload[0][1])
        message["X-Sync-UID"] = uid.decode()
        yield message


def source_rows(client, start, end, today, history, diagnostics):
    resolved, seen = {}, 0
    # Routine runs allow today's delivery of yesterday's report; manual runs
    # search exactly the inclusive email-date range requested by the user.
    mail_end = end if history else today
    for message in imap_messages(client, start, mail_end):
        seen += 1
        if not verified_sender(message):
            diagnostics.append({"code": "unverified_sender", "uid": message.get("X-Sync-UID")})
            continue
        found = False
        for filename, raw_pdf in attachments(message):
            found = True
            issues = []
            origin = {"uid": message.get("X-Sync-UID"), "attachment": filename, "email_date": message.get("Date")}
            try:
                parsed = pdf_rows(raw_pdf, issues)
            except Exception as exc:
                diagnostics.append(dict(origin, code="parse_failure", error_type=type(exc).__name__))
                continue
            diagnostics.extend(dict(issue, **origin) for issue in issues)
            print(json.dumps(dict(origin, report_start=min(parsed).isoformat() if parsed else None,
                                  report_end=max(parsed).isoformat() if parsed else None), ensure_ascii=False))
            selected = {day: metrics for day, metrics in parsed.items() if start <= day <= end}
            if not selected:
                diagnostics.append(dict(origin, code="no_complete_data_in_range"))
                continue
            for day, metrics in selected.items():
                resolved.setdefault(day, metrics)  # newest complete pair wins
            if not history:
                if max(parsed) < end:
                    diagnostics.append(dict(origin, code="stale_report", latest_date=max(parsed).isoformat()))
                break
        if not found:
            diagnostics.append({"code": "no_pdf", "uid": message.get("X-Sync-UID")})
        if resolved and (not history or len(resolved) == (end - start).days + 1):
            break
    missing = [start + timedelta(days=i) for i in range((end - start).days + 1)
               if start + timedelta(days=i) not in resolved]
    if missing:
        diagnostics.append({"code": "missing_dates" if seen else "missing_report", "dates": [d.isoformat() for d in missing]})
    return resolved


def fetch_source(secrets, start, end, today, history, diagnostics):
    def attempt():
        gmail = gmail_imap_client(secrets["GMAIL_IMAP_USERNAME"], secrets["GMAIL_APP_PASSWORD"])
        try:
            return source_rows(gmail, start, end, today, history, diagnostics)
        finally:
            try:
                gmail.logout()
            except (OSError, imaplib.IMAP4.error):
                pass
    return retry_call(attempt)


def sheets_service(service_json):
    import httplib2
    from google.oauth2.service_account import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build
    credentials = Credentials.from_service_account_info(json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    http = AuthorizedHttp(credentials, http=httplib2.Http(timeout=REQUEST_TIMEOUT))
    return build("sheets", "v4", http=http, cache_discovery=False)


def value_at(row, column):
    values = row["values"] if isinstance(row, dict) else row
    return values[column] if column < len(values) else ""


def col_name(index):
    output = ""
    while True:
        index, remainder = divmod(index, 26)
        output = chr(65 + remainder) + output
        if index == 0:
            return output
        index -= 1


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def get_sheet(service):
    values = sheets_execute(service.spreadsheets().values().get(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A:E", valueRenderOption="UNFORMATTED_VALUE", dateTimeRenderOption="SERIAL_NUMBER")).get("values", [])
    if not values or len(values[0]) != len(set(values[0])) or any(header not in values[0] for header in HEADERS):
        raise RuntimeError("long-format target headers are missing or duplicated")
    headers, positions, records = values[0], {header: values[0].index(header) for header in HEADERS}, {}
    for row_number, row in enumerate(values[1:], start=2):
        if (str(value_at(row, positions["合作方"])).strip() != PARTNER
                or str(value_at(row, positions["运营位"])).strip() != OPERATION):
            continue
        if not row or not value_at(row, positions["日期"]):
            continue
        key = (parse_day(value_at(row, positions["日期"])), str(value_at(row, positions["合作方"])).strip(), str(value_at(row, positions["运营位"])).strip())
        if key in records:
            raise RuntimeError(f"duplicate long-format record: {key}")
        records[key] = {"row": row_number, "values": row}
    return headers, records


def plan_writes(headers, existing_rows, source):
    positions = {header: headers.index(header) for header in HEADERS}
    updates, appends, overwrites = [], [], []
    for day, metrics in sorted(source.items()):
        if any(metrics.get(metric) is None for metric in ("new_users", "blood_volume")):
            continue
        key = (day, PARTNER, OPERATION)
        row = existing_rows.get(key)
        if row is None:
            record = {"日期": day, "合作方": PARTNER, "运营位": OPERATION, "新增": metrics["new_users"]}
            if "blood_volume" in metrics:
                record["血量"] = metrics["blood_volume"]
            appends.append(record)
            continue
        for header, metric in (("新增", "new_users"), ("血量", "blood_volume")):
            if metric not in metrics:
                continue
            current, wanted = value_at(row, positions[header]), metrics[metric]
            if current in ("", None) or not values_match(current, wanted):
                updates.append({"range": f"'{SHEET_NAME}'!{col_name(positions[header])}{row['row']}", "values": [[wanted]]})
                if current not in ("", None):
                    overwrites.append(f"{day} {PARTNER}/{OPERATION}/{header}: sheet={current}, source={wanted}")
    return updates, appends, overwrites


def append_rows(service, headers, records):
    if not records:
        return
    pending = records
    for attempt in range(RETRIES + 1):
        try:
            append_once(service, headers, pending)
            return
        except Exception as exc:
            if not retryable(exc):
                raise
            # A lost response may follow a successful append. Reconcile keys
            # and both metrics before retrying only genuinely missing rows.
            fresh_headers, existing = get_sheet(service)
            if fresh_headers != headers:
                raise RuntimeError("target headers changed during append recovery") from exc
            positions = {header: headers.index(header) for header in HEADERS}
            pending = []
            for record in records:
                row = existing.get((record["日期"], PARTNER, OPERATION))
                if row is None:
                    pending.append(record)
                elif any(not values_match(value_at(row, positions[header]), record[header])
                         for header in ("新增", "血量")):
                    raise RuntimeError("append recovery found conflicting metrics") from exc
            if not pending:
                return
            if attempt == RETRIES:
                raise
            time.sleep(2 ** attempt)


def append_once(service, headers, records):
    positions = {header: headers.index(header) for header in HEADERS}
    rows = []
    for record in records:
        row = [""] * len(headers)
        for header in ("日期", "合作方", "运营位", "新增"):
            row[positions[header]] = record[header].isoformat() if header == "日期" else record[header]
        if "血量" in record:
            row[positions["血量"]] = record["血量"]
        rows.append(row)
    service.spreadsheets().values().append(spreadsheetId=SHEET_ID, range=f"'{SHEET_NAME}'!A1:{col_name(len(headers) - 1)}", valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body={"majorDimension": "ROWS", "values": rows}).execute()


def verify_source(service, source):
    headers, existing = get_sheet(service)
    positions = {header: headers.index(header) for header in HEADERS}
    for day, metrics in source.items():
        row = existing.get((day, PARTNER, OPERATION))
        if row is None or any(not values_match(value_at(row, positions[header]), metrics[metric])
                              for header, metric in (("新增", "new_users"), ("血量", "blood_volume"))):
            raise RuntimeError(f"write verification failed: {day}/{PARTNER}/{OPERATION}")
    return len(source)


def run_sync(args, summary):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    start, end, history = sync_window(args.start_date, args.end_date, today)
    summary.update(start=start.isoformat(), end=end.isoformat(), mode="history" if history else "last_7_days")
    secrets = {name: os.environ.get(name) for name in ("GMAIL_IMAP_USERNAME", "GMAIL_APP_PASSWORD", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
    if not all(secrets.values()):
        raise RuntimeError("missing required GitHub Actions secret")
    started = time.monotonic()
    source = fetch_source(secrets, start, end, today, history, summary["warnings"])
    summary["timings_seconds"]["gmail_and_pdf"] = round(time.monotonic() - started, 3)
    if not source:
        raise RuntimeError("CCleaner has no complete daily install/cost pairs in range; no writes performed")
    started = time.monotonic()
    sheets = sheets_service(secrets["GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"])
    headers, existing = get_sheet(sheets)
    summary["timings_seconds"]["sheet_read"] = round(time.monotonic() - started, 3)
    updates, appends, overwrites = plan_writes(headers, existing, source)
    started = time.monotonic()
    if updates:
        sheets_execute(sheets.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}))
    append_rows(sheets, headers, appends)
    summary.update(updated_cells=len(updates), appended_rows=len(appends), overwrites=overwrites)
    summary["timings_seconds"]["sheet_write"] = round(time.monotonic() - started, 3)
    started = time.monotonic()
    summary["verified_records"] = verify_source(sheets, source)
    summary["timings_seconds"]["sheet_verify"] = round(time.monotonic() - started, 3)
    summary["status"] = "updated" if updates or appends else "no_changes"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", help="Inclusive start date; defaults to six days before end date")
    parser.add_argument("--end-date", help="Inclusive end date; defaults to yesterday Beijing time")
    args = parser.parse_args()
    summary = {"status": "running", "warnings": [], "updated_cells": 0,
               "appended_rows": 0, "verified_records": 0, "timings_seconds": {}}
    try:
        run_sync(args, summary)
    except Exception:
        summary["status"] = "failed"
        raise
    finally:
        print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
