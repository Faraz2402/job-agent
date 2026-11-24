"""Google Sheets access + the pure helpers that decide what to write.
The helpers are kept free of network calls so they can be unit-tested."""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from .models import Job
from .normalize import company_title_key, normalize_url

log = logging.getLogger(__name__)

# Tracker column positions (0-based) — must match the live sheet.
COL_COMPANY, COL_TITLE, COL_STATUS, COL_LINK = 0, 1, 2, 10
COL_LINK_STATUS, COL_LINK_CHECKED = 24, 25     # Y, Z

INBOX_HEADERS = [
    "Job Key", "Found On", "Source", "Company", "Title", "Location", "Remote",
    "Posted", "Job Link", "Apply Link", "Prefilter Score", "Matched Keywords",
    "Live Check", "Description", "Status", "Processed On", "Claude Score",
    "Claude Notes",
]
LOG_HEADERS = [
    "Date", "Time (IST)", "Mode", "Result", "Jobs Found", "Kept After Filters",
    "Added to Inbox", "Links Checked", "Marked Active", "Marked Expired",
    "Unknown", "Errors",
]
MAX_DESC = 20000


def fmt_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")          # 24-Nov-2026, as the tracker uses


def parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for f in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%y"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def _cell(row: list, i: int) -> str:
    return str(row[i]).strip() if i < len(row) and row[i] is not None else ""


def tracker_index(values: list[list]) -> tuple[set[str], set[str], list[str]]:
    """From tracker rows (header first) -> (normalized urls, company|title
    keys, raw urls)."""
    urls, cts, raw = set(), set(), []
    for row in values[1:]:
        link = _cell(row, COL_LINK)
        if link:
            raw.append(link)
            urls.add(normalize_url(link))
        comp, title = _cell(row, COL_COMPANY), _cell(row, COL_TITLE)
        if comp and title:
            cts.add(company_title_key(comp, title))
    return urls, cts, raw


def pick_linkcheck_rows(values, today: date, recheck_days: int,
                        skip_statuses: list[str], max_rows: int):
    """Rows to re-check: open status, has a link, never checked or checked
    more than `recheck_days` ago. Oldest check first. Returns
    [(sheet_row_number, company, url)]."""
    skip = {s.lower() for s in skip_statuses}
    picked = []
    for i, row in enumerate(values[1:], start=2):
        comp, url = _cell(row, COL_COMPANY), _cell(row, COL_LINK)
        if not comp or not url.startswith("http"):
            continue
        if _cell(row, COL_STATUS).lower() in skip:
            continue
        checked = parse_date(_cell(row, COL_LINK_CHECKED))
        if checked and (today - checked).days < recheck_days:
            continue
        picked.append((checked or date.min, i, comp, url))
    picked.sort(key=lambda x: (x[0], x[1]))
    return [(i, comp, url) for _, i, comp, url in picked[:max_rows]]


def inbox_row(job: Job, today: date) -> list:
    return [
        job.key, fmt_date(today), job.source, job.company, job.title,
        job.location, "Yes" if job.remote else "", job.posted, job.url,
        job.apply_url, job.prefilter_score, ", ".join(job.matched),
        job.live, (job.description or "")[:MAX_DESC], "New", "", "", "",
    ]


class Sheets:  # pragma: no cover - thin wrapper over gspread
    def __init__(self, spreadsheet_id: str, creds_json: str):
        import gspread

        info = json.loads(creds_json)
        self.gc = gspread.service_account_from_dict(info)
        self.sh = self.gc.open_by_key(spreadsheet_id)
        self.service_email = info.get("client_email", "")

    def ws(self, title: str, headers: list[str] | None = None):
        import gspread

        try:
            return self.sh.worksheet(title)
        except gspread.WorksheetNotFound:
            if headers is None:
                raise
            ws = self.sh.add_worksheet(title=title, rows=2000, cols=len(headers))
            ws.update(range_name="A1", values=[headers], value_input_option="RAW")
            return ws

    def tracker_values(self, tab: str) -> list[list]:
        return self.ws(tab).get("A1:AA")

    def inbox_keys(self, tab: str) -> set[str]:
        ws = self.ws(tab, INBOX_HEADERS)
        return {k for k in ws.col_values(1)[1:] if k}

    def append_inbox(self, tab: str, rows: list[list]) -> None:
        if rows:
            self.ws(tab, INBOX_HEADERS).append_rows(rows, value_input_option="RAW")

    def write_link_checks(self, tab: str, updates: list[tuple[int, str, str, str]]):
        """updates: (row, expected_company, Y value, Z value). Re-reads
        column A for each row first and skips any row whose company no
        longer matches (someone inserted/sorted rows)."""
        if not updates:
            return 0, 0
        ws = self.ws(tab)
        current = ws.batch_get([f"A{r}" for r, *_ in updates])
        data, skipped = [], 0
        for (row, expected, y, z), got in zip(updates, current):
            actual = got[0][0].strip() if got and got[0] else ""
            if actual != expected:
                skipped += 1
                log.warning("row %d: expected %r found %r - skipped", row, expected, actual)
                continue
            data.append({"range": f"Y{row}:Z{row}", "values": [[y, z]]})
        for i in range(0, len(data), 100):
            ws.batch_update(data[i:i + 100], value_input_option="RAW")
        return len(data), skipped

    def board_values(self, tab: str) -> list[list]:
        from .registry import BOARD_HEADERS
        return self.ws(tab, BOARD_HEADERS).get("A1:K")

    def write_boards(self, tab: str, values: list[list]) -> None:
        """The Boards tab is owned by this code: rewrite it in full."""
        from .registry import BOARD_HEADERS
        ws = self.ws(tab, BOARD_HEADERS)
        if ws.row_count < len(values) + 10:
            ws.add_rows(len(values) + 200 - ws.row_count)
        ws.clear()
        ws.update(range_name="A1", values=values, value_input_option="RAW")

    def log_run(self, tab: str, row: list) -> None:
        self.ws(tab, LOG_HEADERS).append_rows([row], value_input_option="RAW")
