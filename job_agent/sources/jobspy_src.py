"""LinkedIn / Indeed / Naukri / Glassdoor via the open-source JobSpy
library. Each site is tried separately so one blocked site doesn't stop
the others."""
from __future__ import annotations

import logging
import math
import time

from ..models import Job

log = logging.getLogger(__name__)


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
           pause, include_remote=False, errors=None) -> list[Job]:
    try:
        from jobspy import scrape_jobs
    except ImportError as e:  # pragma: no cover
        msg = f"jobspy not installed: {e}"
        log.error(msg)
        if errors is not None:
            errors.append(msg)
        return []

    plan = [(q, loc, False) for q in queries for loc in locations]
    if include_remote:
        plan += [(q, country_indeed, True) for q in queries]

    jobs: list[Job] = []
    for site in sites:
        site_ok = 0
        for q, loc, remote in plan:
            try:
                kwargs = dict(
                    site_name=[site], search_term=q, location=loc,
                    results_wanted=results, hours_old=hours_old,
                    country_indeed=country_indeed, verbose=0,
                )
                if site == "linkedin":
                    kwargs["linkedin_fetch_description"] = True
                if site == "google":
                    where = "remote India" if remote else loc
                    kwargs["google_search_term"] = f"{q} jobs near {where} since yesterday"
                if remote:
                    kwargs["is_remote"] = True
                df = scrape_jobs(**kwargs)
                rows = df.to_dict("records") if df is not None else []
                for r in rows:
                    jobs.append(_row_to_job(r))
                site_ok += 1
            except Exception as e:  # sites block / change often
                msg = f"{site} '{q}' @ {loc}: {type(e).__name__}: {str(e)[:160]}"
                log.warning(msg)
                if errors is not None:
                    errors.append(msg)
            time.sleep(pause)
        log.info("jobspy %s: %d/%d searches ok", site, site_ok, len(plan))
    return jobs
