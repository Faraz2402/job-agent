"""Is a job posting still open? Uses the employer board's own API where
possible (accurate), then falls back to reading the page."""
from __future__ import annotations

import re
from urllib.parse import quote

from .ats_urls import parse_ats_url
from .http import get

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"

CLOSED_PHRASES = [
    "no longer accepting applications",
    "no longer accepting",
    "position has been filled",
    "this position has been filled",
    "job is no longer available",
    "this job is no longer available",
    "job has expired",
    "this job has expired",
    "posting has expired",
    "no longer available",
    "job not found",
    "page you are looking for doesn't exist",
    "page you are looking for does not exist",
    "the job you are looking for is no longer open",
    "not accepting applications",
    "this requisition is no longer",
    "applications are closed",
    "job posting is closed",
]

UNREADABLE_HOSTS = ("linkedin.com",)


def check(session, url: str) -> tuple[str, str]:
    """Return (status, evidence)."""
    if not url:
        return UNKNOWN, "no url"
    low = url.lower()
    if any(h in low for h in UNREADABLE_HOSTS):
        return UNKNOWN, "site blocks automated checks"

    ref = parse_ats_url(url)
    if ref:
        try:
            res = _check_ats(session, ref)
            if res:
                return res
        except Exception:
            pass  # API changed or unreachable: fall back to the page check
    return _check_page(session, url)


def _check_ats(session, ref):
    if ref.kind == "workday" and ref.workday and ref.workday_path:
        r = get(session, f"{ref.workday.api_base}{ref.workday_path}")
        if r is None:
            return None
        if r.status_code in (404, 410):
            return CLOSED, "workday api 404"
        if r.status_code == 200:
            info = (r.json() or {}).get("jobPostingInfo") or {}
            if info.get("canApply") is False:
                return CLOSED, "workday: canApply=false"
            return (OPEN, "workday api") if info else (UNKNOWN, "workday: empty")
        return None
    if ref.kind == "greenhouse" and ref.job_id:
        r = get(session, f"https://boards-api.greenhouse.io/v1/boards/{ref.board}/jobs/{ref.job_id}")
        if r is None:
            return None
        if r.status_code == 404:
            return CLOSED, "greenhouse api 404"
        if r.status_code == 200:
            return OPEN, "greenhouse api"
        return None
    if ref.kind == "lever" and ref.job_id:
        r = get(session, f"https://api.lever.co/v0/postings/{ref.board}/{ref.job_id}")
        if r is None:
            return None
        if r.status_code == 404:
            return CLOSED, "lever api 404"
        if r.status_code == 200:
            return OPEN, "lever api"
        return None
    if ref.kind == "ashby" and ref.job_id:
        r = get(session, f"https://api.ashbyhq.com/posting-api/job-board/{quote(ref.board)}")
        if r is None or r.status_code != 200:
            return None
        ids = {j.get("id") for j in r.json().get("jobs", [])}
        urls = " ".join(j.get("jobUrl", "") for j in r.json().get("jobs", []))
        if ref.job_id in ids or ref.job_id in urls:
            return OPEN, "ashby board"
        return CLOSED, "not on ashby board"
    if ref.kind == "workable" and ref.job_id:
        r = get(session, f"https://apply.workable.com/api/v1/widget/accounts/{ref.board}")
        if r is None or r.status_code != 200:
            return None
        codes = {j.get("shortcode") for j in r.json().get("jobs", [])}
        return (OPEN, "workable board") if ref.job_id in codes else (CLOSED, "not on workable board")
    if ref.kind == "smartrecruiters" and ref.job_id:
        r = get(session, f"https://api.smartrecruiters.com/v1/companies/{ref.board}/postings/{ref.job_id}")
        if r is None:
            return None
        if r.status_code == 404:
            return CLOSED, "smartrecruiters api 404"
        if r.status_code == 200:
            return OPEN, "smartrecruiters api"
        return None
    return None


def classify_html(status_code: int, html: str) -> tuple[str, str]:
    """Pure function: decide from an HTTP status + page body."""
    if status_code in (404, 410):
        return CLOSED, f"HTTP {status_code}"
    if status_code != 200:
        return UNKNOWN, f"HTTP {status_code}"
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html or "",
                  flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).lower()
    for ph in CLOSED_PHRASES:
        if ph in text:
            return CLOSED, f'page says "{ph}"'
    if len(text) < 600:
        return UNKNOWN, "page needs JavaScript / too little text"
    signals = ("apply", "responsibilit", "qualification", "requirement",
               "experience", "job description")
    if sum(1 for s in signals if s in text) >= 2:
        return OPEN, "page shows job content"
    return UNKNOWN, "no job content found"


def _check_page(session, url):
    r = get(session, url, allow_redirects=True)
    if r is None:
        return UNKNOWN, "network error"
    # A job link that now redirects to a bare careers/search page is
    # usually a closed posting, but not reliably — report it as unknown.
    final = (getattr(r, "url", "") or "").rstrip("/").lower()
    if (r.status_code == 200 and final and final != url.rstrip("/").lower()
            and re.search(r"/(search|jobs|careers|job-search)(\?.*)?$", final)):
        return UNKNOWN, f"redirected to listing page ({final[:80]})"
    return classify_html(r.status_code, getattr(r, "text", "") or "")
