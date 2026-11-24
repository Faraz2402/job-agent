"""Recognise company job-board (ATS) URLs and pull out what the public
APIs need. Pure functions, no network."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass(frozen=True)
class WorkdayBoard:
    host: str       # e.g. 3m.wd1.myworkdayjobs.com
    tenant: str     # e.g. 3m
    site: str       # e.g. Search

    @property
    def api_base(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    @property
    def public_base(self) -> str:
        return f"https://{self.host}/en-US/{self.site}"


@dataclass(frozen=True)
class AtsRef:
    kind: str                       # workday/greenhouse/lever/ashby/smartrecruiters
    board: str                      # token/company/org (workday: tenant)
    job_id: str = ""                # posting id if the URL points to one
    workday: WorkdayBoard | None = None
    workday_path: str = ""          # "/job/..." part for Workday


_WD = re.compile(
    r"^(?P<tenant>[a-z0-9_-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com$", re.I)


def parse_ats_url(url: str) -> AtsRef | None:
    if not url:
        return None
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    segs = [unquote(s) for s in p.path.split("/") if s]

    m = _WD.match(host)
    if m:
        if segs and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", segs[0]):
            segs = segs[1:]
        if not segs:
            return None
        site = segs[0]
        board = WorkdayBoard(host=host, tenant=m.group("tenant").lower(), site=site)
        path = ""
        if "job" in segs[1:]:
            i = segs.index("job", 1)
            raw = p.path
            idx = raw.find("/job/")
            path = raw[idx:] if idx >= 0 else "/" + "/".join(segs[i:])
        return AtsRef("workday", board.tenant, workday=board, workday_path=path)

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
        if not segs:
            return None
        job_id = ""
        if len(segs) >= 3 and segs[1] == "jobs":
            job_id = segs[2]
        q = parse_qs(p.query)
        if "gh_jid" in q:
            job_id = q["gh_jid"][0]
        return AtsRef("greenhouse", segs[0].lower(), job_id)

    if host == "jobs.lever.co":
        if not segs:
            return None
        return AtsRef("lever", segs[0].lower(), segs[1] if len(segs) > 1 else "")

    if host == "jobs.ashbyhq.com":
        if not segs:
            return None
        return AtsRef("ashby", segs[0], segs[1] if len(segs) > 1 else "")

    if host == "apply.workable.com":
        if not segs or segs[0] in ("api", "j"):
            return None
        job_id = ""
        if len(segs) >= 2 and segs[1] == "j" and len(segs) >= 3:
            job_id = segs[2]
        elif len(segs) >= 4 and segs[1] == "jobs" and segs[2] == "view":
            job_id = segs[3].split(".")[0]
        return AtsRef("workable", segs[0].lower(), job_id)

    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"):
        if not segs:
            return None
        job_id = ""
        if len(segs) > 1:
            mm = re.match(r"(\d{6,})", segs[1])
            job_id = mm.group(1) if mm else ""
        return AtsRef("smartrecruiters", segs[0], job_id)

    return None


def discover_boards(urls: list[str]) -> dict[str, set]:
    """Collect every distinct company board seen in a list of URLs."""
    out: dict[str, set] = {
        "workday": set(), "greenhouse": set(), "lever": set(),
        "ashby": set(), "smartrecruiters": set(), "workable": set(),
    }
    for u in urls:
        ref = parse_ats_url(u)
        if not ref:
            continue
        if ref.kind == "workday" and ref.workday:
            out["workday"].add(ref.workday)
        else:
            out[ref.kind].add(ref.board)
    return out
