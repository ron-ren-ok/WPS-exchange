"""Fetch Avast PBI PDF attachments from Gmail and sync verified daily metrics."""
import argparse
import email
import imaplib
import io
import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pdfplumber


SHEET_ID = "1vSBU84SFoVlXdaczYYAev8mC0PEfjRQyVSv8s2OAGW4"
SHEET_NAME = "合作方新增血量"
ORIGINAL_SENDER = "no-reply-powerbi@microsoft.com"
FORWARDER = "partner@wps.com"
HEADERS = ("日期", "合作方", "运营位", "新增", "血量")
PARTNER = "Avast"
RETRIES = 3
REQUEST_TIMEOUT = 30
SURFACES = {
    "popup": {"subject": "Avast AV - WPS - Daily PBI report", "operation": "换量弹窗", "optional": False},
    "bubble": {"subject": "Avast AV - WPS - Toast - Daily PBI report", "operation": "气泡", "optional": False},
    "uninstall_h5": {"subject": "Avast One - WPS - C - Daily Report PBI", "operation": "卸载后引导H5", "optional": True},
    "document_radar": {"subject": "Avast AV - WPS - E - Daily PBI report", "operation": "\u6587\u6863\u96f7\u8fbe", "optional": True},
}


def parse_day(value):
    text = str(value).strip()
    try:
        serial = float(text)
        if 20000 <= serial <= 80000:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"):
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


def parse_avast_page(page_text, diagnostics=None, positioned=None):
    """Return available daily metrics from the first PBI page.

    Power BI can refresh the installs and costs tables at different times.
    Each table is therefore validated against its own date header: a valid
    table is retained even when the corresponding metric is absent or lags.
    """
    total_pattern = r"(?im)^[^\w$\d\r\n]*(?:Grand[ \t]+)?Total[ \t]+(.+)$"
    totals = list(re.finditer(total_pattern, page_text))
    new_total = next((match for match in totals if "$" not in match.group(1)), None)
    blood_total = next(
        (match for match in totals if "$" in match.group(1) and (new_total is None or match.start() > new_total.start())),
        None,
    )
    date_token = (
        r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}"
        r"|\d{1,2}[./]\d{1,2}[./]\d{4}"
        r"|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}"
        r"|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
    )

    diagnostics = diagnostics if diagnostics is not None else []

    def header_days(region):
        # Use the last header block, excluding earlier report-generation dates.
        lines = region.splitlines()
        blocks, current = [], []
        for line in lines:
            tokens = re.findall(date_token, line, flags=re.IGNORECASE)
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

    def parse_metric(total, days, metric):
        if total is None:
            return {}
        if positioned is not None and metric in positioned:
            return positioned[metric]
        tokens = total.group(1).split()
        pattern = r"\d[\d,]*" if metric == "new_users" else r"\$[\d,]+(?:\.\d+)?"
        if not all(re.fullmatch(pattern, token) for token in tokens):
            diagnostics.append({"code": "invalid_metric", "metric": metric})
            return {}
        values = tokens
        expected_days = len(values) - 1  # final value is the grand total
        if expected_days < 1 or len(days) != expected_days or len(days) != len(set(days)):
            diagnostics.append({"code": "ambiguous_columns", "metric": metric,
                                "dates": [d.isoformat() for d in days]})
            return {}
        return dict(zip(days, map(number, values[:-1])))

    new_days = header_days(page_text[:new_total.start()]) if new_total else []
    new_metrics = parse_metric(
        new_total, new_days, "new_users",
    )
    blood_header_region = ""
    if blood_total:
        blood_header_region = (
            page_text[new_total.end():blood_total.start()]
            if new_total
            else page_text[:blood_total.start()]
        )
    blood_days = header_days(blood_header_region)
    # Share a header only if it is absent. Never replace an invalid own header.
    blood_metrics = parse_metric(blood_total, blood_days or new_days, "blood_volume")
    partial = new_metrics.keys() ^ blood_metrics.keys()
    if partial:
        diagnostics.append({"code": "partial_metrics", "dates": [d.isoformat() for d in sorted(partial)]})
    # A date is usable only when the report contains both metrics for that
    # date. Do not append a partially refreshed day and backfill it later.
    return {
        day: {"new_users": new_metrics[day], "blood_volume": blood_metrics[day]}
        for day in new_metrics.keys() & blood_metrics.keys()
    }

def positioned_metrics(page):
    """Preserve empty PDF columns using coordinates instead of shifting tokens."""
    lines = []
    for word in sorted(page.extract_words(), key=lambda w: (w["top"], w["x0"])):
        if not lines or abs(lines[-1][0]["top"] - word["top"]) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    result, seen = {}, set()
    for index, line in enumerate(lines):
        line.sort(key=lambda w: w["x0"])
        total = next((i for i, w in enumerate(line) if w["text"].lower() == "total"), None)
        if total is None:
            continue
        values = line[total + 1:]
        if not values:
            continue
        metric = "blood_volume" if any("$" in w["text"] for w in values) else "new_users"
        if metric in seen:
            continue
        seen.add(metric)
        pattern = r"\$[\d,]+(?:\.\d+)?" if metric == "blood_volume" else r"\d[\d,]*"
        if not all(re.fullmatch(pattern, w["text"]) for w in values):
            continue
        header = None
        for earlier in reversed(lines[:index]):
            dates = [w for w in earlier if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[./]\d{1,2}[./]\d{4}", w["text"])]
            if dates:
                header = (earlier, dates)
                break
        if header is None:
            continue
        earlier, dates = header
        days = [parse_day(w["text"]) for w in dates]
        grand = [w for w in earlier if w["x0"] > dates[-1]["x1"] and w["text"].lower() in ("grand", "total")]
        if not grand or len(days) != len(set(days)):
            continue
        centers = [(w["x0"] + w["x1"]) / 2 for w in dates]
        grand_center = (min(w["x0"] for w in grand) + max(w["x1"] for w in grand)) / 2
        boundaries = [(a + b) / 2 for a, b in zip(centers, centers[1:] + [grand_center])]
        parsed, valid, grand_values = {}, True, 0
        for word in values:
            center = (word["x0"] + word["x1"]) / 2
            column = next((i for i, right in enumerate(boundaries) if center < right), None)
            if column is None:
                grand_values += 1
                continue  # grand total
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
            raise ValueError("Avast attachment is empty")
        page = pdf.pages[0]
        return parse_avast_page(page.extract_text() or "", diagnostics, positioned_metrics(page))


def retryable(exc):
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in (408, 429, 500, 502, 503, 504) or isinstance(exc, (TimeoutError, ConnectionError, imaplib.IMAP4.abort)) or (
        isinstance(exc, OSError) and not isinstance(exc, (FileNotFoundError, PermissionError))
    )


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
    """Authenticate with an app password; OAuth refresh tokens are not used."""
    try:
        client = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=REQUEST_TIMEOUT)
        client.login(username.strip(), app_password.replace(" ", "").strip())
        return client
    except imaplib.IMAP4.error as exc:
        raise RuntimeError("Gmail IMAP login failed; check GMAIL_IMAP_USERNAME and GMAIL_APP_PASSWORD") from exc

def sheets_service(service_json):
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    from google_auth_httplib2 import AuthorizedHttp
    import httplib2
    creds = Credentials.from_service_account_info(json.loads(service_json), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets", "v4", http=AuthorizedHttp(creds, http=httplib2.Http(timeout=REQUEST_TIMEOUT)), cache_discovery=False)


def body_text(message):
    return "\n".join(
        part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in message.walk()
        if part.get_content_maintype() == "text" and part.get_payload(decode=True)
    )


def verified_sender(message):
    sent_by = message.get("From", "").lower()
    if ORIGINAL_SENDER in sent_by:
        return True
    return FORWARDER in sent_by and ORIGINAL_SENDER in body_text(message).lower()


def attachments(message):
    for part in message.walk():
        filename = part.get_filename() or ""
        if part.get_content_type() == "application/pdf" or filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                yield filename, payload


def select_all_mail(client):
    status, mailboxes = retry_call(client.list)
    if status != "OK":
        raise RuntimeError("Gmail IMAP mailbox listing failed")
    all_mail = next((item.decode("utf-8", "replace").rsplit('"', 2)[-2]
                     for item in mailboxes if b"\\All" in item), None)
    mailbox = all_mail or "INBOX"
    status, _ = retry_call(lambda: client.select(mailbox, readonly=True))
    if status != "OK":
        raise RuntimeError(f"Gmail IMAP could not open mailbox: {mailbox}")


def imap_messages(client, subject, sent_on, history_start=None):
    # Restrict the server-side search to the report date. This prevents old
    # messages with the same subject from being fetched or parsed.
    criteria = ("SENTSINCE", history_start.strftime("%d-%b-%Y"), "SENTBEFORE",
                (sent_on + timedelta(days=1)).strftime("%d-%b-%Y")) if history_start else (
                    "SENTON", sent_on.strftime("%d-%b-%Y"))
    status, data = retry_call(lambda: client.uid("search", None, *criteria, "SUBJECT", f'"{subject}"'))
    if status != "OK":
        raise RuntimeError("Gmail IMAP search failed")
    uids = data[0].split()
    if not uids:
        return
    # Gmail UIDs are monotonically increasing. Fetch only the newest matching
    # report instead of traversing older messages from the same day.
    selected = reversed(uids) if history_start else [uids[-1]]
    for uid in selected:
        status, payload = retry_call(lambda: client.uid("fetch", uid, "(RFC822)"))
        if status != "OK" or not payload or not isinstance(payload[0], tuple):
            raise RuntimeError(f"Gmail IMAP fetch failed for UID {uid.decode()}")
        message = email.message_from_bytes(payload[0][1])
        message["X-Sync-UID"] = uid.decode()
        yield message

def source_rows(client, surface, start, end, email_date, diagnostics=None, history=False):
    spec = SURFACES[surface]
    resolved = {}
    rejected = 0
    diagnostics = diagnostics if diagnostics is not None else []
    messages = imap_messages(client, spec["subject"], email_date, history_start=start) if history else imap_messages(client, spec["subject"], email_date)
    def checked_messages():
        try:
            yield from messages
        except (RuntimeError, OSError, imaplib.IMAP4.error) as exc:
            diagnostics.append({"code": "fetch_failure", "surface": surface,
                                "error_type": type(exc).__name__})
    seen = 0
    for message in checked_messages():
        seen += 1
        if not verified_sender(message):
            rejected += 1
            continue
        for filename, raw_pdf in attachments(message):
            issues = []
            try:
                parsed = pdf_rows(raw_pdf, issues)
            except Exception:
                diagnostics.append({"code": "parse_failure", "surface": surface,
                                    "uid": message.get("X-Sync-UID"), "attachment": filename})
                continue
            for issue in issues:
                if "dates" in issue:
                    issue["dates"] = [d for d in issue["dates"] if (start is None or start <= parse_day(d)) and parse_day(d) <= end]
                    if not issue["dates"]:
                        continue
                diagnostics.append(dict(issue, surface=surface, uid=message.get("X-Sync-UID"), attachment=filename))
            if not parsed and not issues:
                diagnostics.append({"code": "parse_failure", "surface": surface, "attachment": filename})
            print(json.dumps({"surface": surface, "uid": message.get("X-Sync-UID"), "attachment": filename,
                              "data_start": min(parsed).isoformat() if parsed else None,
                              "data_end": max(parsed).isoformat() if parsed else None}, ensure_ascii=False))
            for day, metrics in parsed.items():
                if (start is None or start <= day) and day <= end and day not in resolved:
                    resolved[day] = metrics
        if history and start is not None and len(resolved) == (end - start).days + 1:
            break
    status_start = start or (min(resolved) if resolved else end)
    unavailable = [status_start + timedelta(days=i) for i in range((end - status_start).days + 1) if status_start + timedelta(days=i) not in resolved]
    if unavailable and (resolved or seen or not spec["optional"]):
        diagnostics.append({"code": "missing_dates" if resolved else "missing_report" if not seen else "no_usable_data",
                            "surface": surface, "dates": [d.isoformat() for d in unavailable]})
    print(json.dumps({"surface": surface, "status": "available" if resolved else "missing_report" if not seen else "no_usable_data", "available_days": len(resolved), "unavailable_days": [d.isoformat() for d in unavailable], "rejected_messages": rejected}, ensure_ascii=False))
    return resolved


def col_name(index):
    result = ""
    while True:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        if index == 0:
            return result
        index -= 1


def value_at(row, column):
    values = row["values"] if isinstance(row, dict) else row
    return values[column] if column < len(values) else ""


def values_match(current, wanted):
    try:
        return abs(float(current) - float(wanted)) < 1e-9
    except (TypeError, ValueError):
        return str(current).replace(",", "") == str(wanted)


def get_sheet(service):
    values = sheets_execute(service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{SHEET_NAME}'!A:E",
        valueRenderOption="UNFORMATTED_VALUE",
        dateTimeRenderOption="SERIAL_NUMBER",
    )).get("values", [])
    if not values or len(values[0]) != len(set(values[0])) or any(header not in values[0] for header in HEADERS):
        raise RuntimeError("long-format target headers are missing or duplicated")
    headers = values[0]
    positions = {header: headers.index(header) for header in HEADERS}
    rows = {}
    for row_number, row in enumerate(values[1:], start=2):
        if not row or not value_at(row, positions["日期"]):
            continue
        key = (
            parse_day(value_at(row, positions["日期"])),
            str(value_at(row, positions["合作方"])).strip(),
            str(value_at(row, positions["运营位"])).strip(),
        )
        if key in rows:
            raise RuntimeError(f"duplicate long-format record: {key}")
        rows[key] = {"row": row_number, "values": row}
    return headers, rows




def plan_writes(headers, existing_rows, sources, allow_overwrite):
    positions = {header: headers.index(header) for header in HEADERS}
    updates, appends, conflicts, overwrites = [], [], [], []
    for surface, source in sources.items():
        operation = SURFACES[surface]["operation"]
        for day, metrics in sorted(source.items()):
            if set(metrics) != {"new_users", "blood_volume"}:
                continue
            row = existing_rows.get((day, PARTNER, operation))
            if row is None:
                appends.append({"日期": day, "合作方": PARTNER, "运营位": operation, "新增": metrics["new_users"], "血量": metrics["blood_volume"]})
                continue
            for header, metric in (("新增", "new_users"), ("血量", "blood_volume")):
                current, wanted = value_at(row, positions[header]), metrics[metric]
                if current in ("", None):
                    updates.append({"range": f"'{SHEET_NAME}'!{col_name(positions[header])}{row['row']}", "values": [[wanted]]})
                elif not values_match(current, wanted):
                    detail = f"{day} {PARTNER}/{operation}/{header}: sheet={current}, source={wanted}"
                    if allow_overwrite:
                        updates.append({"range": f"'{SHEET_NAME}'!{col_name(positions[header])}{row['row']}", "values": [[wanted]]})
                        overwrites.append(detail)
                    else:
                        conflicts.append(detail)
    if conflicts:
        raise RuntimeError("refusing to overwrite conflicts: " + "; ".join(conflicts))
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
            # A lost response may follow a successful append. Check before retry.
            fresh_headers, targets = get_sheet(service)
            if fresh_headers != headers:
                raise RuntimeError("target headers changed during append recovery") from exc
            positions = {header: headers.index(header) for header in HEADERS}
            pending = []
            for record in records:
                found = targets.get((record["日期"], PARTNER, record["运营位"]))
                if found is None:
                    pending.append(record)
                elif any(not values_match(value_at(found, positions[h]), record[h]) for h in ("新增", "血量")):
                    raise RuntimeError("append recovery found conflicting metrics") from exc
            if not pending:
                return
            if attempt == RETRIES:
                raise
            time.sleep(2 ** attempt)


def append_once(service, headers, records):
    positions = {header: headers.index(header) for header in HEADERS}
    values = []
    for record in records:
        row = [""] * len(headers)
        row[positions["日期"]] = record["日期"].isoformat()
        row[positions["合作方"]] = record["合作方"]
        row[positions["运营位"]] = record["运营位"]
        row[positions["新增"]] = record["新增"]
        row[positions["血量"]] = record["血量"]
        values.append(row)
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=f"'{SHEET_NAME}'!A1:{col_name(len(headers) - 1)}",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"majorDimension": "ROWS", "values": values},
    ).execute()


def verify_sources(service, sources):
    headers, rows = get_sheet(service)
    positions = {header: headers.index(header) for header in HEADERS}
    verified = 0
    for surface, source in sources.items():
        for day, metrics in source.items():
            row = rows.get((day, PARTNER, SURFACES[surface]["operation"]))
            if row is None or any(not values_match(value_at(row, positions[h]), metrics[m])
                                  for h, m in (("新增", "new_users"), ("血量", "blood_volume"))):
                raise RuntimeError(f"write verification failed: {day}/{surface}")
            verified += 1
    return verified


def sync_window(start_value, end_value, today):
    end = parse_day(end_value) if end_value else today - timedelta(days=1)
    start = parse_day(start_value) if start_value else end - timedelta(days=6)
    if start > end:
        raise ValueError("start date is after end date")
    return start, end, bool(start_value or end_value)


def run_sync(args, summary):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    start, end, history = sync_window(args.start_date, args.end_date, today)
    summary.update(start=start.isoformat(), end=end.isoformat(), mode="history" if history else "last_7_days")
    secrets = {name: os.environ.get(name) for name in ("GMAIL_IMAP_USERNAME", "GMAIL_APP_PASSWORD", "GOOGLE_SHEET_SERVICE_ACCOUNT_JSON")}
    if not all(secrets.values()):
        raise RuntimeError("missing required GitHub Actions secret")
    sheets = sheets_service(secrets["GOOGLE_SHEET_SERVICE_ACCOUNT_JSON"])
    headers, existing_rows = get_sheet(sheets)
    gmail = gmail_imap_client(secrets["GMAIL_IMAP_USERNAME"], secrets["GMAIL_APP_PASSWORD"])
    sources = {}
    try:
        select_all_mail(gmail)
        for surface in SURFACES:
            try:
                sources[surface] = source_rows(gmail, surface, start, end, today, summary["warnings"], history)
            except (RuntimeError, OSError, imaplib.IMAP4.error) as exc:
                summary["warnings"].append({"code": "fetch_failure", "surface": surface,
                                            "error_type": type(exc).__name__})
                sources[surface] = {}
    finally:
        try:
            gmail.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
    if not any(sources.values()):
        raise RuntimeError("all Avast surfaces have no usable complete daily metrics")
    updates, appends, overwrites = plan_writes(headers, existing_rows, sources, args.allow_overwrite)
    if updates:
        sheets_execute(sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=SHEET_ID, body={"valueInputOption": "USER_ENTERED", "data": updates}))
    append_rows(sheets, headers, appends)
    summary.update(updated_cells=len(updates), appended_rows=len(appends), overwrites=overwrites)
    summary["verified_records"] = verify_sources(sheets, sources)
    summary["status"] = "updated" if updates or appends else "no_changes"
    if any(w["code"] in ("fetch_failure", "parse_failure") for w in summary["warnings"]):
        raise RuntimeError("Avast source failures occurred; other complete records were synced and verified")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--status-file")
    args = parser.parse_args()
    summary = {"status": "running", "warnings": [], "updated_cells": 0,
               "appended_rows": 0, "verified_records": 0}
    try:
        run_sync(args, summary)
    except Exception:
        summary["status"] = "failed"
        raise
    finally:
        rendered = json.dumps(summary, ensure_ascii=False)
        if args.status_file:
            Path(args.status_file).write_text(rendered, encoding="utf-8")
        print(rendered)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
