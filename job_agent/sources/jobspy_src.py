"""LinkedIn / Indeed / Naukri via the open-source JobSpy library.

Each site is searched separately and slowly. If a site answers "blocked"
(HTTP 429/403) twice in a row, its remaining searches are skipped for the
day so it never escalates to a longer ban; the other sites carry on."""
from __future__ import annotations

import logging
import math
import time

from ..models import Job

log = logging.getLogger(__name__)

BLOCK_MARKERS = ("429", "403", "blocked", "too many requests", "rate limit")


class _BlockCounter(logging.Handler):
    """Counts JobSpy log lines that mean 'the site blocked us'."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.count = 0

    def emit(self, record):
        try:
            msg = record.getMessage().lower()
        except Exception:
            return
        if any(m in msg for m in BLOCK_MARKERS):
            self.count += 1


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    return str(v).strip()


def _row_to_job(row: dict) -> Job:
    site = _s(row.get("site")) or "jobspy"
    url = _s(row.get("job_url"))
    direct = _s(row.get("job_url_direct"))
    return Job(
        source=site,
        company=_s(row.get("company")),
        title=_s(row.get("title")),
        location=_s(row.get("location")),
        url=url,
        apply_url=direct if direct and direct != url else "",
        description=_s(row.get("description")),
        posted=_s(row.get("date_posted")),
        remote=bool(row.get("is_remote")) if _s(row.get("is_remote")) else False,
    )


def search(queries, locations, sites, *, hours_old, results, country_indeed,
           limits: dict, include_remote=False, errors=None, stats=None,
           scrape=None) -> list[Job]:
    """limits: pause_seconds, linkedin_pause_seconds, linkedin_results,
    stop_site_after_blocks. `scrape` lets tests inject a fake scraper."""
    if scrape is None:
        try:
            from jobspy import scrape_jobs as scrape
        except ImportError as e:  # pragma: no cover
            msg = f"jobspy not installed: {e}"
            log.error(msg)
            if errors is not None:
                errors.append(msg)
            return []

    plan = [(q, loc, False) for q in queries for loc in locations]
    if include_remote:
        plan += [(q, country_indeed, True) for q in queries]
    stop_after = int(limits.get("stop_site_after_blocks", 2))

    jobs: list[Job] = []
    for site in sites:
        pause = limits.get("linkedin_pause_seconds", 10) if site == "linkedin" \
            else limits.get("pause_seconds", 4)
        want = min(results, limits.get("linkedin_results", results)) if site == "linkedin" else results
        counter = _BlockCounter()
        site_logger = logging.getLogger(f"jobspy.{site}")
        site_logger.addHandler(counter)
        ok = blocked = streak = found = 0
        try:
            for i, (q, loc, remote) in enumerate(plan):
                before = counter.count
                try:
                    kwargs = dict(site_name=[site], search_term=q, location=loc,
                                  results_wanted=want, hours_old=hours_old,
                                  country_indeed=country_indeed, verbose=0)
                    if site == "linkedin":
                        kwargs["linkedin_fetch_description"] = True
                    if remote:
                        kwargs["is_remote"] = True
                    df = scrape(**kwargs)
                    rows = df.to_dict("records") if df is not None else []
                except Exception as e:  # sites block / change often
                    rows = []
                    msg = str(e).lower()
                    if any(m in msg for m in BLOCK_MARKERS):
                        counter.count += 1
                    elif errors is not None:
                        errors.append(f"{site} '{q}' @ {loc}: {type(e).__name__}: {str(e)[:120]}")
                for r in rows:
                    jobs.append(_row_to_job(r))
                found += len(rows)
                if counter.count > before:
                    blocked += 1
                    streak += 1
                    if streak >= stop_after:
                        left = len(plan) - i - 1
                        msg = (f"{site}: blocked {streak}x in a row - skipped the last "
                               f"{left} {site} searches today to avoid a ban")
                        log.warning(msg)
                        if errors is not None:
                            errors.append(msg)
                        break
                else:
                    ok += 1
                    streak = 0
                time.sleep(pause)
        finally:
            site_logger.removeHandler(counter)
        log.info("jobspy %s: %d ok, %d blocked, %d jobs", site, ok, blocked, found)
        if stats is not None:
            stats[site] = {"ok": ok, "blocked": blocked, "jobs": found}
    return jobs
