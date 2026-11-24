from __future__ import annotations

from .models import Job


def _has_any(text: str, terms: list[str]) -> str | None:
    t = (text or "").lower()
    for term in terms:
        if term.lower() in t:
            return term
    return None


def agency_tag(job: Job, cfg: dict) -> str | None:
    """Agencies and 'on behalf of a client' postings are kept, only tagged."""
    f = cfg["filters"]
    hint = _has_any(job.company, f.get("agency_hints", []))
    if hint:
        return f"agency ({hint})"
    hint = _has_any((job.description or "")[:1500], f.get("description_agency_hints", []))
    if hint:
        return "posted for an unnamed client"
    return None


def reject_reason(job: Job, cfg: dict) -> str | None:
    """Return why a job should be dropped, or None to keep it."""
    f = cfg["filters"]
    company = (job.company or "").strip()
    if not company or not job.title:
        return "missing company or title"
    blocked = [c.lower() for c in f.get("blocked_companies", []) or []]
    if company.lower() in blocked:
        return f"blocked company ({company})"
    if not _has_any(job.title, f.get("title_must_match_any", [])):
        return "title not a target role"
    bad = _has_any(job.title, f.get("title_exclude", []))
    if bad:
        return f"excluded title term ({bad})"
    loc_terms = f.get("location_must_match_any", [])
    if job.location and loc_terms and not job.remote:
        if not _has_any(job.location, loc_terms):
            return f"location out of scope ({job.location})"
    if job.live == "closed":
        return "posting closed"
    return None
