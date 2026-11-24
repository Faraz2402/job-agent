from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .normalize import normalize_url, company_title_key


@dataclass
class Job:
    source: str                 # e.g. "indeed", "linkedin", "workday:3m"
    company: str
    title: str
    location: str = ""
    url: str = ""               # where it was found
    apply_url: str = ""         # employer's own page, if known
    description: str = ""
    posted: str = ""            # as the board states it
    remote: bool = False
    live: str = "unknown"       # open / closed / unknown
    prefilter_score: int = 0
    matched: list[str] = field(default_factory=list)

    @property
    def best_url(self) -> str:
        """Employer page preferred over aggregator page."""
        return self.apply_url or self.url

    @property
    def norm_urls(self) -> set[str]:
        return {normalize_url(u) for u in (self.url, self.apply_url) if u}

    @property
    def ct_key(self) -> str:
        return company_title_key(self.company, self.title)

    @property
    def key(self) -> str:
        basis = normalize_url(self.best_url) or self.ct_key
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
