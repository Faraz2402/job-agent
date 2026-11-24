"""Entry point.

    python -m job_agent.run --mode all          # search + link check
    python -m job_agent.run --mode search
    python -m job_agent.run --mode linkcheck
    python -m job_agent.run --mode all --dry-run   # read only, writes nothing

Credentials: env GOOGLE_SERVICE_ACCOUNT_JSON (the whole key file as text)
or GOOGLE_SERVICE_ACCOUNT_FILE (path to it).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import liveness, registry
from .filters import reject_reason
from .http import make_session
from .models import Job
from .scoring import prefilter
from .sheets import (Sheets, fmt_date, inbox_row, pick_linkcheck_rows,
                     tracker_index)
from .sources import ats as ats_src
from .sources import jobspy_src

IST = timezone(timedelta(hours=5, minutes=30))
log = logging.getLogger("job_agent")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_creds() -> str | None:
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        return raw
    p = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if p and Path(p).exists():
        return Path(p).read_text(encoding="utf-8")
    return None


def todays_queries(cfg: dict, now: datetime):
    s = cfg["search"]
    wd = now.weekday()
    qmap = {int(k): v for k, v in (s.get("queries_by_weekday") or {}).items()}
    queries = qmap.get(wd, [])
    intl = s.get("international") or {}
    intl_plan = []
    if intl and int(intl.get("weekday", -1)) == wd:
        intl_plan = [(q, m["location"], m["country_indeed"])
                     for q in intl.get("queries", []) for m in intl.get("markets", [])]
    return queries, intl_plan


BOARD_URL = {
    "greenhouse": "https://boards.greenhouse.io/{}",
    "lever": "https://jobs.lever.co/{}",
    "ashby": "https://jobs.ashbyhq.com/{}",
    "smartrecruiters": "https://jobs.smartrecruiters.com/{}",
    "workable": "https://apply.workable.com/{}",
}


def config_seed_urls(cfg: dict) -> list[tuple[str, str]]:
    """Boards listed in config.yaml, as (url, company) pairs."""
    a = cfg.get("ats") or {}
    out = [(u, "") for u in a.get("workday") or []]
    for kind, tmpl in BOARD_URL.items():
        for name in a.get(kind) or []:
            out.append((tmpl.format(name), ""))
    return out


def blocked_set(cfg: dict) -> set[str]:
    return {c.lower() for c in cfg["filters"].get("blocked_companies", [])}


def select_new(jobs: list[Job], tracker_urls, tracker_cts, inbox_keys, cfg,
               session, check_live=True):
    """Dedupe, filter, prefilter-score, verify. Returns (kept, stats)."""
    stats = {"found": len(jobs), "dupes": 0, "rejected": 0, "closed": 0, "low": 0}
    seen, candidates = set(), []
    for j in jobs:
        if (j.key in seen or j.key in inbox_keys or j.ct_key in tracker_cts
                or j.norm_urls & tracker_urls):
            stats["dupes"] += 1
            continue
        seen.add(j.key)
        why = reject_reason(j, cfg)
        if why:
            stats["rejected"] += 1
            continue
        j.prefilter_score, j.matched = prefilter(j)
        if j.prefilter_score < cfg["scoring"]["min_prefilter_score"]:
            stats["low"] += 1
            continue
        candidates.append(j)

    # Collapse the same job seen on several boards (keep best-scored).
    by_ct: dict[str, Job] = {}
    for j in candidates:
        cur = by_ct.get(j.ct_key)
        if cur is None or (j.apply_url and not cur.apply_url) or j.prefilter_score > cur.prefilter_score:
            by_ct[j.ct_key] = j
    candidates = sorted(by_ct.values(), key=lambda x: -x.prefilter_score)

    cap = cfg["scoring"]["max_inbox_rows_per_run"]
    kept = []
    for j in candidates:
        if len(kept) >= cap:
            break
        if check_live and j.live != "open":
            status, _ = liveness.check(session, j.best_url)
            j.live = status
            if status == "closed":
                stats["closed"] += 1
                continue
        kept.append(j)
    stats["kept"] = len(kept)
    return kept, stats


def run(args) -> int:
    cfg = load_config(args.config)
    now = datetime.now(IST)
    today = now.date()
    errors: list[str] = []
    summary = {"date": fmt_date(today), "mode": args.mode, "dry_run": args.dry_run}

    creds = load_creds()
    sheets = None
    tracker_values: list[list] = [[]]
    inbox_keys: set[str] = set()
    board_rows: list[dict] = []
    if creds:
        try:
            sheets = Sheets(cfg["spreadsheet_id"], creds)
            tracker_values = sheets.tracker_values(cfg["tracker_tab"])
            inbox_keys = sheets.inbox_keys(cfg["inbox_tab"])
            board_rows = registry.rows_from_values(sheets.board_values(cfg["boards_tab"]))
            log.info("tracker rows: %d", len(tracker_values) - 1)
        except Exception as e:
            msg = f"Google Sheets: {type(e).__name__}: {e}"
            log.error(msg)
            if not args.dry_run:
                print(json.dumps({"error": msg}))
                return 2
            errors.append(msg)
    elif not args.dry_run:
        log.error("No Google credentials. Set GOOGLE_SERVICE_ACCOUNT_JSON or use --dry-run.")
        return 2

    tracker_urls, tracker_cts, raw_urls = tracker_index(tracker_values)
    session = make_session()

    # ---------------- search ----------------
    if args.mode in ("all", "search"):
        s = cfg["search"]
        queries, intl_plan = todays_queries(cfg, now)
        jobs: list[Job] = []
        if queries and not args.skip_jobspy:
            jobs += jobspy_src.search(
                queries, s["locations"], s["sites"], hours_old=s["hours_old"],
                results=s["results_per_query"], country_indeed=s["country_indeed"],
                pause=s.get("pause_seconds", 4), include_remote=s.get("include_remote", False),
                errors=errors)
            for q, loc, country in intl_plan:
                jobs += jobspy_src.search(
                    [q], [loc], (s.get("international") or {}).get("sites", ["indeed", "linkedin"]),
                    hours_old=s["hours_old"], results=s["results_per_query"],
                    country_indeed=country, pause=s.get("pause_seconds", 4), errors=errors)
        # ---- company boards: grow the registry, then search the due ones ----
        t = fmt_date(today)
        blocked = blocked_set(cfg)
        before = len(board_rows)
        registry.add_discovered(board_rows, [(u, "") for u in raw_urls], "tracker", t, blocked)
        registry.add_discovered(board_rows, config_seed_urls(cfg), "config", t, blocked)
        # Any job board link seen in LinkedIn/Indeed/Naukri/Glassdoor/Google
        # results (procurement titles only) becomes a board to watch.
        title_terms = cfg["filters"]["title_must_match_any"]
        for j in jobs:
            if any(x in (j.title or "").lower() for x in title_terms):
                registry.add_discovered(
                    board_rows, [(j.apply_url, j.company), (j.url, j.company)],
                    j.source, t, blocked)
        new_boards = len(board_rows) - before
        due = registry.pick_due(board_rows, cfg["ats"].get("max_boards_per_run", 80))
        if not args.skip_ats:
            jobs += ats_src.collect_registry(session, due, cfg, errors, t)
        if sheets and not args.dry_run:
            try:
                sheets.write_boards(cfg["boards_tab"], registry.rows_to_values(board_rows))
            except Exception as e:
                errors.append(f"could not save Boards tab: {e}")
        by_kind: dict[str, int] = {}
        for r in board_rows:
            by_kind[r["Kind"]] = by_kind.get(r["Kind"], 0) + 1
        summary["boards"] = {"total": len(board_rows), "new_this_run": new_boards,
                             "searched_this_run": len(due), "by_kind": by_kind}

        kept, stats = select_new(jobs, tracker_urls, tracker_cts, inbox_keys, cfg, session)
        summary["search"] = stats
        rows = [inbox_row(j, today) for j in kept]
        if sheets and not args.dry_run:
            sheets.append_inbox(cfg["inbox_tab"], rows)
        summary["added_to_inbox"] = len(rows) if not args.dry_run else 0
        summary["top"] = [
            {"company": j.company, "title": j.title, "location": j.location,
             "score": j.prefilter_score, "live": j.live, "url": j.best_url}
            for j in kept[:15]]

    # ---------------- link check ----------------
    if args.mode in ("all", "linkcheck") and len(tracker_values) > 1:
        lc = cfg["linkcheck"]
        rows = pick_linkcheck_rows(tracker_values, today, lc["recheck_after_days"],
                                   lc["skip_statuses"], lc["max_rows_per_run"])
        updates, counts = [], {"open": 0, "closed": 0, "unknown": 0}
        for row, company, url in rows:
            status, _ = liveness.check(session, url)
            counts[status] += 1
            if status == "open":
                updates.append((row, company, "Active", fmt_date(today)))
            elif status == "closed":
                updates.append((row, company, "Expired", fmt_date(today)))
        written = skipped = 0
        if sheets and not args.dry_run:
            written, skipped = sheets.write_link_checks(cfg["tracker_tab"], updates)
        summary["linkcheck"] = {"checked": len(rows), **counts,
                                "written": written, "skipped_row_mismatch": skipped}

    summary["errors"] = errors[:50]
    if sheets and not args.dry_run:
        se, lcs = summary.get("search", {}), summary.get("linkcheck", {})
        try:
            sheets.log_run(cfg["log_tab"], [
                fmt_date(today), now.strftime("%H:%M"), args.mode,
                "OK" if not errors else f"OK with {len(errors)} warnings",
                se.get("found", ""), se.get("kept", ""), summary.get("added_to_inbox", ""),
                lcs.get("checked", ""), lcs.get("open", ""), lcs.get("closed", ""),
                lcs.get("unknown", ""), " | ".join(errors[:8])[:4000],
            ])
        except Exception as e:
            log.error("could not write Pipeline Log: %s", e)

    out = json.dumps(summary, indent=2, default=str)
    print(out)
    Path(args.summary).write_text(out, encoding="utf-8")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="job_agent")
    p.add_argument("--mode", choices=["all", "search", "linkcheck"], default="all")
    p.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.yaml"))
    p.add_argument("--dry-run", action="store_true", help="read only; write nothing")
    p.add_argument("--skip-jobspy", action="store_true")
    p.add_argument("--skip-ats", action="store_true")
    p.add_argument("--summary", default="run_summary.json")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
