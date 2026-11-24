"""Self-growing list of company job boards ("Boards" tab).

Every run:
  1. adds boards seen in the tracker, in config, and in the apply links of
     anything LinkedIn/Indeed/Naukri/Glassdoor/Google returned;
  2. searches the boards that were searched longest ago (capped per run);
  3. records results, and switches off boards that fail 3 runs in a row.
Pure functions only - the Sheets read/write lives in sheets.py."""
from __future__ import annotations

from datetime import date

from .ats_urls import AtsRef, WorkdayBoard, parse_ats_url

BOARD_HEADERS = [
    "Kind", "Board", "Host", "Site", "Company", "Discovered From",
    "First Seen", "Last Searched", "Jobs Last Run", "Fails", "Active",
]
MAX_FAILS = 3


def board_key(kind: str, board: str, host: str = "", site: str = "") -> str:
    if kind == "workday":
        return f"workday|{host.lower()}|{site.lower()}"
    return f"{kind}|{board.lower()}"


def ref_to_row(ref: AtsRef, company: str, source: str, today: str) -> dict:
    wd = ref.workday
    return {
        "Kind": ref.kind, "Board": ref.board,
        "Host": wd.host if wd else "", "Site": wd.site if wd else "",
        "Company": company or ref.board, "Discovered From": source,
        "First Seen": today, "Last Searched": "", "Jobs Last Run": "",
        "Fails": 0, "Active": "Yes",
    }


def row_key(row: dict) -> str:
    return board_key(row.get("Kind", ""), row.get("Board", ""),
                     row.get("Host", ""), row.get("Site", ""))


def rows_from_values(values: list[list]) -> list[dict]:
    if not values:
        return []
    head = values[0]
    out = []
    for v in values[1:]:
        if not any(v):
            continue
        row = {h: (v[i] if i < len(v) else "") for i, h in enumerate(head)}
        try:
            row["Fails"] = int(row.get("Fails") or 0)
        except ValueError:
            row["Fails"] = 0
        out.append(row)
    return out


def rows_to_values(rows: list[dict]) -> list[list]:
    return [BOARD_HEADERS] + [[r.get(h, "") for h in BOARD_HEADERS] for r in rows]


def add_discovered(rows: list[dict], urls_with_company, source: str,
                   today: str, blocked: set[str]) -> int:
    """urls_with_company: iterable of (url, company). Returns how many new
    boards were added."""
    known = {row_key(r) for r in rows}
    added = 0
    for url, company in urls_with_company:
        ref = parse_ats_url(url)
        if not ref:
            continue
        name = (company or ref.board or "").strip()
        if ref.board.lower() in blocked or name.lower() in blocked:
            continue
        row = ref_to_row(ref, name, source, today)
        k = row_key(row)
        if k in known:
            continue
        rows.append(row)
        known.add(k)
        added += 1
    return added


def pick_due(rows: list[dict], cap: int) -> list[dict]:
    """Active boards, never-searched first, then oldest search first."""
    active = [r for r in rows if str(r.get("Active", "Yes")).lower() != "no"]
    def sort_key(r):
        ls = r.get("Last Searched") or ""
        from .sheets import parse_date
        d = parse_date(ls)
        return (d is not None, d or date.min, r.get("First Seen", ""))
    return sorted(active, key=sort_key)[:cap]


def target_of(row: dict):
    """Turn a registry row into what the source functions expect."""
    if row["Kind"] == "workday":
        return WorkdayBoard(host=row["Host"], tenant=row["Board"], site=row["Site"])
    return row["Board"]


def record_result(row: dict, today: str, jobs_found: int | None) -> None:
    """jobs_found=None means the board errored."""
    row["Last Searched"] = today
    if jobs_found is None:
        row["Fails"] = int(row.get("Fails") or 0) + 1
        row["Jobs Last Run"] = "error"
        if row["Fails"] >= MAX_FAILS:
            row["Active"] = "No"
    else:
        row["Fails"] = 0
        row["Jobs Last Run"] = jobs_found
        row["Active"] = "Yes"
