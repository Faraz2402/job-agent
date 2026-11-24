"""Cheap keyword prefilter. It only decides which jobs are worth Claude's
time; Claude does the real resume-to-JD scoring afterwards."""
from __future__ import annotations

import re

from .models import Job

TITLE_TERMS = {
    "procurement": 14, "spend": 14, "p2p": 12, "procure": 12, "s2p": 12,
    "sourcing": 8, "vendor": 8, "supplier": 8, "purchasing": 6,
    "contract": 6, "software asset": 8, "analyst": 6, "analytics": 6,
    "business analyst": 6,
}

DESC_TERMS = {
    "power bi": 12, "dax": 5, "power query": 4, "ariba": 8, "sap mm": 6,
    "netsuite": 8, "salesforce": 4, "spend analy": 8, "tail spend": 5,
    "procure-to-pay": 6, "procure to pay": 6, "p2p": 4, "source-to-pay": 4,
    "contract compliance": 5, "supplier performance": 4, "vendor master": 5,
    "supplier master": 4, "dashboard": 3, "kpi": 3, "excel": 2,
    "servicenow": 2, "jira": 1, "uat": 2, "vendor onboarding": 4,
    "supplier onboarding": 4, "contract management": 3,
}

PENALTIES = {
    "python": -6, "tableau": -2, "people management": -8,
    "manage a team": -8, "team of": -4, "redlin": -6, "draft contracts": -6,
    "lead negotiations": -6, "coupa": -1,
}

TITLE_PENALTIES = {
    "associate": -10, "junior": -12, "jr.": -12, "category manager": -6,
    "lead": -2, "manager": -3,
}

YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*years?",
    re.IGNORECASE,
)


def min_years(text: str) -> int | None:
    found = [int(m.group(1)) for m in YEARS_RE.finditer(text or "")
             if 0 < int(m.group(1)) <= 25]
    return min(found) if found else None


def prefilter(job: Job) -> tuple[int, list[str]]:
    title = (job.title or "").lower()
    desc = (job.description or "").lower()
    score = 30
    matched: list[str] = []

    title_pts = 0
    for term, pts in TITLE_TERMS.items():
        if term in title:
            title_pts += pts
            matched.append(term)
    score += min(title_pts, 30)

    desc_pts = 0
    for term, pts in DESC_TERMS.items():
        if term in desc:
            desc_pts += pts
            matched.append(term)
    score += min(desc_pts, 40)

    for term, pts in PENALTIES.items():
        if term in desc:
            score += pts
            matched.append(f"-{term}")
    for term, pts in TITLE_PENALTIES.items():
        if term in title:
            score += pts
            matched.append(f"-title:{term}")

    yrs = min_years(desc)
    if yrs is not None:
        if yrs >= 12:
            score -= 20
            matched.append(f"-{yrs}+yrs")
        elif yrs >= 10:
            score -= 10
            matched.append(f"-{yrs}+yrs")
        elif yrs <= 2:
            score -= 8
            matched.append(f"-only{yrs}yrs")

    if not desc:
        matched.append("no-description")
    return max(0, min(100, score)), sorted(set(matched), key=matched.index)
