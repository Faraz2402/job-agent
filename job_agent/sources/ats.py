"""Company job boards via their public JSON APIs (no login, no cost).
Postings returned here are live on the employer's own board."""
from __future__ import annotations

import logging
import time
from urllib.parse import quote

from ..ats_urls import WorkdayBoard
from ..http import get, post_json
from ..models import Job
from ..normalize import html_to_text

log = logging.getLogger(__name__)


def _title_ok(title: str, terms: list[str]) -> bool:
    t = (title or "").lower()
    return any(x.lower() in t for x in terms)


def greenhouse(session, token, title_terms) -> list[Job]:
    r = get(session, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true")
    if r is None or r.status_code != 200:
        raise RuntimeError(f"greenhouse {token}: HTTP {getattr(r, 'status_code', 'error')}")
    out = []
    for j in r.json().get("jobs", []):
        if not _title_ok(j.get("title"), title_terms):
            continue
        out.append(Job(
            source=f"greenhouse:{token}", company=token, title=j.get("title", ""),
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""), apply_url=j.get("absolute_url", ""),
            description=html_to_text(j.get("content", "")),
            posted=j.get("updated_at", ""), live="open",
        ))
    return out


def lever(session, company, title_terms) -> list[Job]:
    r = get(session, f"https://api.lever.co/v0/postings/{company}?mode=json")
    if r is None or r.status_code != 200:
        raise RuntimeError(f"lever {company}: HTTP {getattr(r, 'status_code', 'error')}")
    out = []
    for j in r.json():
        if not _title_ok(j.get("text"), title_terms):
            continue
        cats = j.get("categories") or {}
        out.append(Job(
            source=f"lever:{company}", company=company, title=j.get("text", ""),
            location=cats.get("location", "") or "",
            url=j.get("hostedUrl", ""), apply_url=j.get("hostedUrl", ""),
            description=j.get("descriptionPlain", "") or "",
            posted=str(j.get("createdAt", "")), live="open",
            remote="remote" in (cats.get("location", "") or "").lower(),
        ))
    return out


def ashby(session, org, title_terms) -> list[Job]:
    r = get(session, f"https://api.ashbyhq.com/posting-api/job-board/{quote(org)}")
    if r is None or r.status_code != 200:
        raise RuntimeError(f"ashby {org}: HTTP {getattr(r, 'status_code', 'error')}")
    out = []
    for j in r.json().get("jobs", []):
        if not _title_ok(j.get("title"), title_terms):
            continue
        out.append(Job(
            source=f"ashby:{org}", company=org, title=j.get("title", ""),
            location=j.get("location", "") or "",
            url=j.get("jobUrl", ""), apply_url=j.get("jobUrl", ""),
            description=j.get("descriptionPlain", "") or "",
            posted=j.get("publishedAt", ""), live="open",
            remote=bool(j.get("isRemote")),
        ))
    return out


def smartrecruiters(session, company, title_terms) -> list[Job]:
    r = get(session, f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100")
    if r is None or r.status_code != 200:
        raise RuntimeError(f"smartrecruiters {company}: HTTP {getattr(r, 'status_code', 'error')}")
    out = []
    for j in r.json().get("content", []):
        if not _title_ok(j.get("name"), title_terms):
            continue
        pid = j.get("id", "")
        loc = j.get("location") or {}
        desc = ""
        d = get(session, f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}")
        if d is not None and d.status_code == 200:
            secs = (d.json().get("jobAd") or {}).get("sections") or {}
            desc = "\n\n".join(html_to_text((secs.get(k) or {}).get("text", ""))
                               for k in ("jobDescription", "qualifications"))
        url = f"https://jobs.smartrecruiters.com/{company}/{pid}"
        out.append(Job(
            source=f"smartrecruiters:{company}",
            company=(j.get("company") or {}).get("name", company),
            title=j.get("name", ""),
            location=", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
            url=url, apply_url=url, description=desc,
            posted=j.get("releasedDate", ""), live="open",
            remote=bool(loc.get("remote")),
        ))
    return out


def workday(session, board: WorkdayBoard, search_terms, title_terms,
            pause=1.0) -> list[Job]:
    seen, out = set(), []
    for term in search_terms:
        r = post_json(session, f"{board.api_base}/jobs",
                      {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": term})
        if r is None or r.status_code != 200:
            raise RuntimeError(f"workday {board.tenant}/{board.site}: HTTP {getattr(r, 'status_code', 'error')}")
        for p in r.json().get("jobPostings", []):
            path = p.get("externalPath", "")
            if not path or path in seen or not _title_ok(p.get("title"), title_terms):
                continue
            seen.add(path)
            desc, company = "", board.tenant
            d = get(session, f"{board.api_base}{path}")
            if d is not None and d.status_code == 200:
                info = d.json().get("jobPostingInfo") or {}
                desc = html_to_text(info.get("jobDescription", ""))
                company = (d.json().get("hiringOrganization") or {}).get("name") or company
            url = f"{board.public_base}{path}"
            out.append(Job(
                source=f"workday:{board.tenant}", company=company,
                title=p.get("title", ""), location=p.get("locationsText", "") or "",
                url=url, apply_url=url, description=desc,
                posted=p.get("postedOn", ""), live="open",
            ))
            time.sleep(pause)
        time.sleep(pause)
    return out


def workable(session, account, title_terms) -> list[Job]:
    r = get(session, f"https://apply.workable.com/api/v1/widget/accounts/{account}?details=true")
    if r is None or r.status_code != 200:
        raise RuntimeError(f"workable {account}: HTTP {getattr(r, 'status_code', 'error')}")
    data = r.json()
    company = data.get("name") or account
    out = []
    for j in data.get("jobs", []):
        if not _title_ok(j.get("title"), title_terms):
            continue
        url = j.get("url") or j.get("shortlink") or ""
        loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        out.append(Job(
            source=f"workable:{account}", company=company, title=j.get("title", ""),
            location=loc, url=url, apply_url=url,
            description=html_to_text(j.get("description", "")),
            posted=j.get("published_on", "") or j.get("created_at", ""),
            live="open", remote=bool(j.get("telecommuting")),
        ))
    return out


FETCHERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby,
    "smartrecruiters": smartrecruiters, "workable": workable,
}


def collect_registry(session, due_rows: list[dict], cfg: dict, errors: list,
                     today: str) -> list[Job]:
    """Search each due board; record the outcome on its registry row."""
    from ..registry import record_result, target_of

    title_terms = cfg["filters"]["title_must_match_any"]
    wd_terms = cfg["ats"].get("workday_search_terms", ["procurement"])
    jobs: list[Job] = []
    for row in due_rows:
        kind = row["Kind"]
        try:
            target = target_of(row)
            if kind == "workday":
                found = workday(session, target, wd_terms, title_terms)
            elif kind in FETCHERS:
                found = FETCHERS[kind](session, target, title_terms)
            else:
                continue
            # Use the registry's company name when the API gives a slug only.
            for j in found:
                if row.get("Company") and j.company.lower() == str(row["Board"]).lower():
                    j.company = row["Company"]
            jobs += found
            record_result(row, today, len(found))
        except Exception as e:
            record_result(row, today, None)
            msg = f"{kind} board {row.get('Host') or row.get('Board')}: {str(e)[:160]}"
            log.warning(msg)
            errors.append(msg)
    return jobs
