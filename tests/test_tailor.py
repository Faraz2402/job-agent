import json
import zipfile
from pathlib import Path

import pytest
from docx import Document

from job_agent.tailor import ats
from job_agent.tailor.cover import build_cover_letter
from job_agent.tailor.docx_edit import apply_edits


def _base(tmp_path: Path) -> str:
    d = Document()
    d.add_paragraph("Faraz Ahmad")
    d.add_paragraph("SENIOR BUSINESS ANALYST – PROCUREMENT")
    d.add_paragraph("8839173101 | faraz@example.com | Hyderabad, India")
    p = d.add_paragraph("Professional Summary")
    p.runs[0].bold = True
    p = d.add_paragraph("Analyst with ")
    p.add_run("8+ years").bold = True
    p.add_run(" in spend analysis using Power BI.")
    for h in ("Skills", "Work Experience", "Education"):
        d.add_paragraph(h)
    d.add_paragraph("Jul 2023 – Mar 2026, Dec 2022 – Jul 2023, Mar 2019 – Dec 2022, Jun 2016 – Dec 2017")
    path = tmp_path / "base.docx"
    d.save(path)
    return str(path)


def test_edit_keeps_bold_runs(tmp_path):
    base = _base(tmp_path)
    out = tmp_path / "o.docx"
    rep = apply_edits(base, [{"find": "spend analysis using Power BI",
                              "replace": "spend analytics using Power BI"}], str(out))
    assert len(rep.applied) == 1
    p = Document(out).paragraphs[4]
    assert [r.bold for r in p.runs] == [None, True, None]
    assert p.runs[1].text == "8+ years"


@pytest.mark.parametrize("find,replace,why", [
    ("spend analysis", "spend analysis in Python", "can't support"),
    ("spend analysis", "spend analysis of 500 suppliers", "numbers"),
    ("with 8+ years", "with 8+ years total", "mixes formatting"),
    ("nope", "x", "not found"),
])
def test_edit_guards(tmp_path, find, replace, why):
    base = _base(tmp_path)
    rep = apply_edits(base, [{"find": find, "replace": replace}], str(tmp_path / "o.docx"))
    assert not rep.applied and why in rep.rejected[0].reason


def test_ats_score_and_gaps(tmp_path):
    base = _base(tmp_path)
    jd = "We need spend analysis, Power BI dashboards and Tableau. Supplier onboarding."
    rep = ats.check(base, jd, max_pages=2)
    assert "power bi" in rep.matched and "tableau" in rep.gaps
    assert rep.checks["no_tables"] and rep.checks["standard_headings"]


def test_cover_letter_keeps_header(tmp_path):
    base = _base(tmp_path)
    out = tmp_path / "c.docx"
    build_cover_letter(base, str(out), title_line=None, recipient_lines=["Hiring Team", "X"],
                       greeting="Dear Hiring Team,", paragraphs=["One.", "Two."])
    texts = [p.text for p in Document(out).paragraphs]
    assert texts[0] == "Faraz Ahmad" and "One." in texts and texts[-1] == "Faraz Ahmad"
    assert "Professional Summary" not in texts
