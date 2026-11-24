from datetime import date
from pathlib import Path

import yaml

from job_agent.ats_urls import discover_boards, parse_ats_url
from job_agent.filters import reject_reason
from job_agent.liveness import CLOSED, OPEN, UNKNOWN, check, classify_html
from job_agent.models import Job
from job_agent.normalize import company_title_key, normalize_url
from job_agent.run import select_new, todays_queries
from job_agent.scoring import min_years, prefilter
from job_agent.sheets import inbox_row, pick_linkcheck_rows, tracker_index, INBOX_HEADERS

CFG = yaml.safe_load((Path(__file__).parent.parent / "config.yaml").read_text())


# ---------- normalize ----------
def test_normalize_strips_tracking_but_keeps_job_id():
    a = normalize_url("https://www.wellsfargojobs.com/en/jobs/r-563376/x/?publisher=Linkedin&utm_source=joveo&jClickId=abc")
    b = normalize_url("https://wellsfargojobs.com/en/jobs/r-563376/x")
    assert a == b
    assert "jk=91de" in normalize_url("https://in.indeed.com/viewjob?jk=91de&from=serp")


def test_normalize_workday_language_segment():
    a = normalize_url("https://3m.wd1.myworkdayjobs.com/en-US/Search/job/X_R1")
    b = normalize_url("https://3m.wd1.myworkdayjobs.com/Search/job/X_R1?source=LinkedIn")
    assert a == b


def test_company_title_key():
    assert company_title_key("Mitsubishi UFJ (MUFG)", "Senior Analyst - BI") == \
        company_title_key("mitsubishi ufj mufg", "senior analyst bi")


# ---------- ATS URL parsing ----------
def test_parse_workday():
    r = parse_ats_url("https://iff.wd5.myworkdayjobs.com/en-US/IFF_Careers/job/Hyderabad-GBS-India/Governance-Analyst---Procurement_R22011-1?source=LinkedIn")
    assert r.kind == "workday"
    assert r.workday.host == "iff.wd5.myworkdayjobs.com"
    assert r.workday.site == "IFF_Careers"
    assert r.workday.api_base == "https://iff.wd5.myworkdayjobs.com/wday/cxs/iff/IFF_Careers"
    assert r.workday_path == "/job/Hyderabad-GBS-India/Governance-Analyst---Procurement_R22011-1"


def test_parse_other_ats():
    assert parse_ats_url("https://boards.greenhouse.io/acme/jobs/12345").job_id == "12345"
    assert parse_ats_url("https://job-boards.greenhouse.io/acme?gh_jid=999").job_id == "999"
    lv = parse_ats_url("https://jobs.lever.co/zycus/abc-def")
    assert (lv.kind, lv.board, lv.job_id) == ("lever", "zycus", "abc-def")
    ash = parse_ats_url("https://jobs.ashbyhq.com/PAR%20Technology/5daaa961-a0bb-43a8-93bc-7f6a4ebe71db")
    assert ash.kind == "ashby" and ash.board == "PAR Technology"
    sr = parse_ats_url("https://jobs.smartrecruiters.com/EnviriCorporation/744000152830149?ref=Simplify")
    assert (sr.board, sr.job_id) == ("EnviriCorporation", "744000152830149")
    assert parse_ats_url("https://careers.amgen.com/en/job/x") is None


def test_discover_boards_dedupes():
    b = discover_boards([
        "https://3m.wd1.myworkdayjobs.com/en-US/Search/job/A_1",
        "https://3m.wd1.myworkdayjobs.com/Search/job/B_2",
        "https://jobs.lever.co/zycus/1",
        "https://example.com/job",
    ])
    assert len(b["workday"]) == 1 and b["lever"] == {"zycus"}


# ---------- filters ----------
def _job(**kw):
    base = dict(source="t", company="BASF", title="Procurement Analyst", location="Hyderabad, India")
    base.update(kw)
    return Job(**base)


def test_filters():
    assert reject_reason(_job(), CFG) is None
    assert "agency" in reject_reason(_job(company="ABC Staffing Pvt Ltd"), CFG)
    assert "blocked" in reject_reason(_job(company="Indeed"), CFG)
    assert "not a target role" in reject_reason(_job(title="Marketing Executive"), CFG)
    assert "excluded" in reject_reason(_job(title="Procurement Intern"), CFG)
    assert "location" in reject_reason(_job(location="Houston, TX"), CFG)
    assert reject_reason(_job(location="Houston, TX", remote=True), CFG) is None
    assert reject_reason(_job(location=""), CFG) is None
    assert "closed" in reject_reason(_job(live="closed"), CFG)


# ---------- scoring ----------
def test_prefilter_rewards_fit_and_penalises_mismatch():
    good = _job(title="Senior Procurement Analyst",
                description="Spend analysis in Power BI with DAX, SAP Ariba and NetSuite. 5+ years experience. P2P, vendor master.")
    bad = _job(title="Procurement Associate",
               description="Python and Tableau. Manage a team. 12+ years experience.")
    gs, gm = prefilter(good)
    bs, _ = prefilter(bad)
    assert gs >= 75 and "power bi" in gm
    assert bs < 40


def test_min_years():
    assert min_years("Requires 5-7 years of experience") == 5
    assert min_years("10+ years in procurement") == 10
    assert min_years("no number here") is None


# ---------- liveness ----------
def test_classify_html():
    body = "<html>" + ("Responsibilities and qualifications. Experience. Apply now. " * 30) + "</html>"
    assert classify_html(200, body)[0] == OPEN
    assert classify_html(404, "")[0] == CLOSED
    assert classify_html(200, body + " This job is no longer available ")[0] == CLOSED
    assert classify_html(200, "<div id=root></div><script>x</script>")[0] == UNKNOWN
    assert classify_html(403, "")[0] == UNKNOWN


class _Resp:
    def __init__(self, code, payload=None, text="", url=""):
        self.status_code, self._p, self.text, self.url = code, payload, text, url

    def json(self):
        return self._p


class _Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp
        return _Resp(500)

    post = get


def test_liveness_uses_workday_api():
    url = "https://3m.wd1.myworkdayjobs.com/en-US/Search/job/Bengaluru/Analyst_R1"
    s = _Session({"https://3m.wd1.myworkdayjobs.com/wday/cxs/3m/Search/job/": _Resp(404)})
    assert check(s, url)[0] == CLOSED
    s = _Session({"https://3m.wd1.myworkdayjobs.com/wday/cxs/3m/Search/job/":
                  _Resp(200, {"jobPostingInfo": {"title": "x", "canApply": True}})})
    assert check(s, url)[0] == OPEN


def test_liveness_linkedin_is_unknown_without_network():
    s = _Session({})
    assert check(s, "https://www.linkedin.com/jobs/view/123")[0] == UNKNOWN
    assert s.calls == []


# ---------- sheets helpers ----------
TRACKER = [
    ["Company", "Title", "Status"] + [""] * 7 + ["Job Posting Link"] + [""] * 13 + ["Job Link Status", "Link Checked On"],
    ["3M", "Analytics Manager", "Applied"] + [""] * 7 + ["https://3m.wd1.myworkdayjobs.com/en-US/Search/job/A_1"] + [""] * 13 + ["Active", "23-Nov-2026"],
    ["Qnity", "Senior Procurement Analyst", "Rejected"] + [""] * 7 + ["https://careers.qnity.com/x"] + [""] * 15,
    ["NTT Data", "Senior BI Analyst", "Applied"] + [""] * 7 + ["https://ntt.wd501.myworkdayjobs.com/External/job/B_2"] + [""] * 13 + ["Active", "10-Nov-2026"],
    ["PwC", "Senior Consultant", "Applied"] + [""] * 7 + ["https://pwc.in/x"],
]


def test_tracker_index():
    urls, cts, raw = tracker_index(TRACKER)
    assert normalize_url("https://3m.wd1.myworkdayjobs.com/Search/job/A_1") in urls
    assert company_title_key("Qnity", "Senior Procurement Analyst") in cts
    assert len(raw) == 4


def test_pick_linkcheck_rows():
    rows = pick_linkcheck_rows(TRACKER, date(2026, 11, 24), 3, ["Rejected", "Offer", "Dropped"], 10)
    # 3M checked yesterday -> skip; Qnity rejected -> skip; PwC never checked first, then NTT
    assert [r[0] for r in rows] == [5, 4]
    assert rows[0][1] == "PwC"


def test_inbox_row_shape():
    j = _job(description="x" * 30000)
    row = inbox_row(j, date(2026, 11, 24))
    assert len(row) == len(INBOX_HEADERS)
    assert row[1] == "24-Nov-2026" and row[14] == "New"
    assert len(row[13]) == 20000


# ---------- selection pipeline ----------
def test_select_new_dedupes_against_tracker_and_within_batch():
    urls, cts, _ = tracker_index(TRACKER)
    s = _Session({})
    desc = "Power BI spend analytics SAP Ariba NetSuite vendor master 5 years"
    jobs = [
        _job(company="Qnity", title="Senior Procurement Analyst", url="https://a.com/1", description=desc, live="open"),
        _job(company="BASF", title="Procurement Analyst", url="https://a.com/2", description=desc, live="open"),
        _job(company="BASF", title="Procurement Analyst", url="https://b.com/2", apply_url="https://basf.jobs/2", description=desc, live="open"),
        _job(company="ABC Staffing", title="Procurement Analyst", url="https://a.com/3", description=desc, live="open"),
    ]
    kept, stats = select_new(jobs, urls, cts, set(), CFG, s, check_live=False)
    assert len(kept) == 1 and kept[0].apply_url == "https://basf.jobs/2"
    assert stats["dupes"] == 1 and stats["rejected"] == 1


def test_queries_rotate():
    from datetime import datetime
    q_mon, intl = todays_queries(CFG, datetime(2026, 11, 23))   # Monday
    assert "senior business analyst" in q_mon and "procurement analyst" in q_mon and intl == []
    _, intl_sun = todays_queries(CFG, datetime(2026, 11, 29))   # Sunday
    assert any(loc == "Singapore" for _, loc, _ in intl_sun)


# ---------- board registry ----------
from job_agent import registry


def test_registry_grows_and_dedupes():
    rows = []
    n = registry.add_discovered(rows, [
        ("https://3m.wd1.myworkdayjobs.com/en-US/Search/job/A_1", "3M"),
        ("https://3m.wd1.myworkdayjobs.com/Search/job/B_2", "3M"),          # same board
        ("https://jobs.smartrecruiters.com/EnviriCorporation/744000152830149", "Enviri"),
        ("https://jobs.lever.co/jobgether/abc", "Jobgether"),              # blocked
        ("https://www.linkedin.com/jobs/view/1", "X"),                     # not a board
    ], "linkedin", "24-Nov-2026", {"jobgether"})
    assert n == 2 and len(rows) == 2
    assert registry.add_discovered(rows, [("https://3m.wd1.myworkdayjobs.com/Search", "")],
                                   "tracker", "25-Nov-2026", set()) == 0


def test_registry_rotation_and_failures():
    rows = registry.rows_from_values(registry.rows_to_values([
        {"Kind": "lever", "Board": "a", "Last Searched": "23-Nov-2026", "Active": "Yes", "Fails": 0},
        {"Kind": "lever", "Board": "b", "Last Searched": "", "Active": "Yes", "Fails": 0},
        {"Kind": "lever", "Board": "c", "Last Searched": "20-Nov-2026", "Active": "Yes", "Fails": 0},
        {"Kind": "lever", "Board": "d", "Last Searched": "", "Active": "No", "Fails": 3},
    ]))
    due = registry.pick_due(rows, 2)
    assert [r["Board"] for r in due] == ["b", "c"]       # never-searched, then oldest
    r = rows[0]
    for _ in range(3):
        registry.record_result(r, "24-Nov-2026", None)
    assert r["Active"] == "No" and r["Fails"] == 3
    registry.record_result(r, "25-Nov-2026", 4)
    assert r["Active"] == "Yes" and r["Fails"] == 0 and r["Jobs Last Run"] == 4


def test_workable_url():
    w = parse_ats_url("https://apply.workable.com/proarch/j/ABC123DEF/")
    assert (w.kind, w.board, w.job_id) == ("workable", "proarch", "ABC123DEF")


def test_unnamed_client_postings_rejected():
    j = _job(company="Some Co", description="This position is listed on behalf of a partner company.")
    assert "unnamed client" in reject_reason(j, CFG)
    assert "blocked" in reject_reason(_job(company="Jobgether"), CFG)


def test_collect_registry_records_results():
    from job_agent.sources import ats as ats_src
    rows = [{"Kind": "lever", "Board": "keyloop", "Company": "Keyloop", "Fails": 0, "Active": "Yes"},
            {"Kind": "greenhouse", "Board": "gone", "Company": "Gone", "Fails": 0, "Active": "Yes"}]
    s = _Session({
        "https://api.lever.co/v0/postings/keyloop": _Resp(200, [
            {"text": "Procurement Analyst", "hostedUrl": "https://jobs.lever.co/keyloop/1",
             "categories": {"location": "Hyderabad"}, "descriptionPlain": "Power BI"},
            {"text": "Software Engineer", "hostedUrl": "https://jobs.lever.co/keyloop/2", "categories": {}}]),
        "https://boards-api.greenhouse.io/v1/boards/gone": _Resp(404),
    })
    errs = []
    jobs = ats_src.collect_registry(s, rows, CFG, errs, "24-Nov-2026")
    assert len(jobs) == 1 and jobs[0].company == "Keyloop" and jobs[0].live == "open"
    assert rows[0]["Jobs Last Run"] == 1 and rows[1]["Fails"] == 1 and len(errs) == 1


def test_ba_and_data_analyst_roles_kept_and_scored():
    ba = _job(company="Amgen", title="Senior Business Analyst",
              description="Requirements gathering, user stories, UAT, Power BI dashboards, stakeholder management, Salesforce. 6+ years.")
    da = _job(company="Novartis", title="Senior Data Analyst - Power BI",
              description="Power BI, DAX, data model, executive reporting, SQL. 5-8 years.")
    heavy = _job(company="X", title="Data Analyst",
                 description="Python, PySpark, Databricks, machine learning, SQL. 4 years.")
    for j in (ba, da):
        assert reject_reason(j, CFG) is None
        assert prefilter(j)[0] >= 70
    assert prefilter(heavy)[0] < prefilter(da)[0] - 25
    assert "excluded" in reject_reason(_job(title="Junior Data Analyst"), CFG)
    assert reject_reason(_job(title="Business Analyst - Salesforce"), CFG) is None   # 'sales' no longer blocks Salesforce
    assert reject_reason(_job(title="Data Engineer"), CFG) is not None


def test_senior_associate_not_penalised():
    a = prefilter(_job(title="Senior Associate - Business Analyst", description="Power BI"))[0]
    b = prefilter(_job(title="Associate - Business Analyst", description="Power BI"))[0]
    assert a - b >= 10
