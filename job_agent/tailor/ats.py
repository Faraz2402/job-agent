"""ATS checks for a tailored resume.

Two parts:
1. Keyword match score (0-100): how many of the job description's important
   terms appear in the resume. Terms come from a built-in vocabulary of
   procurement / BA / BI skills found in the JD, plus any extra keywords the
   caller passes (e.g. the ones Claude picked from the JD).
2. Format checks: things that make ATS parsers drop or garble content.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

# Built-in vocabulary. Each entry: canonical term -> regex alternatives.
VOCAB: dict[str, str] = {
    # procurement
    "procurement": r"procurement", "spend analysis": r"spend anal(?:ysis|ytics)",
    "tail spend": r"tail spend", "sourcing": r"sourcing", "strategic sourcing": r"strategic sourcing",
    "supplier management": r"supplier (?:relationship )?management", "supplier performance": r"supplier performance",
    "supplier consolidation": r"supplier consolidation", "vendor management": r"vendor management",
    "vendor onboarding": r"vendor onboarding|supplier onboarding", "vendor master": r"vendor master|supplier master",
    "contract management": r"contract management|contract lifecycle", "contract compliance": r"contract compliance",
    "procure-to-pay": r"procure[- ]to[- ]pay|\bp2p\b", "source-to-pay": r"source[- ]to[- ]pay|\bs2p\b",
    "purchase orders": r"purchase orders?|\bpo\b", "accounts payable": r"accounts payable",
    "rfx": r"\brf[xpqi]\b|request for (?:proposal|quotation)", "negotiation support": r"negotiation",
    "category management": r"category management", "cost savings": r"cost sav(?:ings|ing)",
    "spend visibility": r"spend visibility|cost visibility", "supply chain": r"supply chain",
    "sap mm": r"sap mm|\bsap\b", "sap ariba": r"ariba", "oracle netsuite": r"netsuite",
    "oracle": r"\boracle\b", "salesforce": r"salesforce", "erp": r"\berp\b",
    # BA
    "business analysis": r"business anal(?:ysis|yst)", "requirements gathering": r"requirements? (?:gathering|elicitation|analysis)",
    "stakeholder management": r"stakeholder", "process improvement": r"process improvement|process optimi[sz]ation",
    "uat": r"\buat\b|user acceptance testing", "user stories": r"user stor(?:y|ies)", "agile": r"\bagile\b|scrum",
    "jira": r"\bjira\b", "azure devops": r"azure devops", "servicenow": r"servicenow",
    "business case": r"business case", "gap analysis": r"gap analysis", "process mapping": r"process mapping|process flows?",
    "audit": r"\baudit", "compliance": r"compliance", "risk": r"\brisk\b",
    # BI / data
    "power bi": r"power ?bi", "dax": r"\bdax\b", "power query": r"power query", "data modeling": r"data model(?:l)?ing",
    "excel": r"\bexcel\b", "sql": r"\bsql\b", "dashboards": r"dashboards?", "kpi": r"\bkpis?\b",
    "data analysis": r"data analy(?:sis|tics)", "data visualization": r"data visuali[sz]ation",
    "reporting": r"reporting|reports?", "data quality": r"data quality|data integrity",
    "executive reporting": r"executive reporting|leadership reporting", "variance analysis": r"variance analysis",
    "forecasting": r"forecast", "copilot": r"copilot", "sharepoint": r"sharepoint", "powerpoint": r"powerpoint",
    "finance": r"\bfinance\b|financial",
    # gaps - shown if the JD asks for them so Faraz sees the gap; never added
    "python": r"\bpython\b", "tableau": r"\btableau\b", "coupa": r"\bcoupa\b", "qlik": r"\bqlik",
    "looker": r"\blooker\b", "snowflake": r"snowflake", "alteryx": r"alteryx",
}
GAP_TERMS = {"python", "tableau", "coupa", "qlik", "looker", "snowflake", "alteryx"}

STANDARD_HEADINGS = ["professional summary", "skills", "work experience", "education"]


@dataclass
class AtsReport:
    score: int
    matched: list[str]
    missing: list[str]
    gaps: list[str]                       # JD asks, Faraz doesn't have - don't fake
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def passed_all(self) -> bool:
        return all(self.checks.values())

    def as_dict(self) -> dict:
        return {"score": self.score, "matched": self.matched, "missing": self.missing,
                "gaps": self.gaps, "checks": self.checks, "passed_all_checks": self.passed_all,
                "notes": self.notes}


def doc_text(path: str) -> str:
    d = Document(path)
    return "\n".join(p.text for p in d.paragraphs)


def _has(term_re: str, text: str) -> bool:
    return re.search(term_re, text, re.I) is not None


def keyword_score(resume_text: str, jd_text: str, extra: list[str] | None = None):
    wanted: dict[str, str] = {k: v for k, v in VOCAB.items() if _has(v, jd_text)}
    for kw in extra or []:
        kw = kw.strip()
        if kw and kw.lower() not in wanted:
            wanted[kw.lower()] = re.escape(kw).replace(r"\ ", r"[\s-]+")
    gaps = sorted(k for k in wanted if k in GAP_TERMS and not _has(wanted[k], resume_text))
    scored = {k: v for k, v in wanted.items() if k not in gaps}
    matched = sorted(k for k, v in scored.items() if _has(v, resume_text))
    missing = sorted(k for k in scored if k not in matched)
    # gaps count against the score (honest), at half weight
    denom = len(scored) + 0.5 * len(gaps)
    score = round(100 * len(matched) / denom) if denom else 0
    return score, matched, missing, gaps


def page_count(path: str) -> int | None:
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, path],
                           check=True, capture_output=True, timeout=120)
        except Exception:
            return None
        pdf = next(Path(tmp).glob("*.pdf"), None)
        if not pdf:
            return None
        data = pdf.read_bytes()
        return len(re.findall(rb"/Type\s*/Page[^s]", data)) or None


def format_checks(path: str, max_pages: int = 2) -> tuple[dict[str, bool], list[str]]:
    d = Document(path)
    body = d.element.body
    xml = d.element.xml
    text_low = "\n".join(p.text for p in d.paragraphs).lower()
    notes: list[str] = []
    checks = {
        "file_is_docx": path.lower().endswith(".docx"),
        "no_tables": len(d.tables) == 0,
        "no_text_boxes": "txbxContent" not in xml,
        "no_images_or_shapes": body.find(".//" + qn("w:drawing")) is None and "<w:pict" not in xml,
        "single_column": not re.search(r'<w:cols [^>]*w:num="([2-9])"', xml),
        "standard_headings": all(h in text_low for h in STANDARD_HEADINGS),
        "email_in_body": re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text_low) is not None,
        "phone_in_body": re.search(r"\d{10}", text_low.replace(" ", "")) is not None,
        "contact_not_in_header_footer": True,
        "dates_present": len(re.findall(r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{4}", text_low)) >= 4,
    }
    for s in d.sections:
        hf = " ".join(p.text for p in s.header.paragraphs + s.footer.paragraphs)
        if "@" in hf or re.search(r"\d{10}", hf):
            checks["contact_not_in_header_footer"] = False
    pages = page_count(path)
    if pages is not None:
        checks[f"max_{max_pages}_pages"] = pages <= max_pages
        notes.append(f"pages: {pages}")
    else:
        notes.append("page count not checked (LibreOffice not installed)")
    for k, ok in checks.items():
        if not ok:
            notes.append(f"FAILED: {k}")
    return checks, notes


def check(resume_path: str, jd_text: str, extra_keywords: list[str] | None = None,
          max_pages: int = 2) -> AtsReport:
    text = doc_text(resume_path)
    score, matched, missing, gaps = keyword_score(text, jd_text, extra_keywords)
    checks, notes = format_checks(resume_path, max_pages)
    return AtsReport(score, matched, missing, gaps, checks, notes)
