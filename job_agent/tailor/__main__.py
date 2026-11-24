"""CLI: tailor resume + cover letter (.docx) and ATS-check them.

    python -m job_agent.tailor --base base.docx --spec spec.json --jd jd.txt --out-dir out/

spec.json:
{
  "company": "Opella",
  "role": "Procurement Analyst",
  "jd_keywords": ["spend analytics", "supplier performance"],      # optional extras
  "resume_edits": [
     {"find": "exact phrase from base resume", "replace": "new phrase", "why": "JD term"}
  ],
  "cover_letter": {
     "title_line": null,                       # optional; null keeps resume title
     "recipient_lines": ["Hiring Team", "Opella", "Hyderabad, India"],
     "greeting": "Dear Hiring Team,",
     "paragraphs": ["...", "...", "..."]
  }
}

Writes <out-dir>/Faraz_Ahmad_Resume_<Company>.docx,
<out-dir>/Faraz_Ahmad_Cover_Letter_<Company>.docx and <out-dir>/ats_report.json.
Exit code 0 = all ATS checks passed, 2 = something failed (see report).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import ats
from .cover import build_cover_letter
from .docx_edit import apply_edits


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40] or "Company"


def strip_folder_entries(path: Path) -> None:
    """Remove ZIP directory entries (Word via cloud co-editing can refuse them)."""
    import shutil
    import zipfile
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if not item.filename.endswith("/"):
                zout.writestr(item, zin.read(item.filename))
    shutil.move(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--jd", required=True, help="text file with the job description")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-pages", type=int, default=2)
    a = ap.parse_args(argv)

    spec = json.loads(Path(a.spec).read_text())
    jd = Path(a.jd).read_text()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag = safe_name(spec.get("company", "Company"))
    resume_path = out / f"Faraz_Ahmad_Resume_{tag}.docx"
    cover_path = out / f"Faraz_Ahmad_Cover_Letter_{tag}.docx"
    extra = spec.get("jd_keywords", [])

    before = ats.check(a.base, jd, extra, a.max_pages)
    edit_rep = apply_edits(a.base, spec.get("resume_edits", []), str(resume_path))
    strip_folder_entries(resume_path)
    after = ats.check(str(resume_path), jd, extra, a.max_pages)

    cl = spec.get("cover_letter")
    if cl:
        build_cover_letter(str(resume_path), str(cover_path), title_line=cl.get("title_line"),
                           recipient_lines=cl.get("recipient_lines", ["Hiring Team", spec.get("company", "")]),
                           greeting=cl.get("greeting", "Dear Hiring Team,"),
                           paragraphs=cl.get("paragraphs", []))
        strip_folder_entries(cover_path)
        cover_checks, cover_notes = ats.format_checks(str(cover_path), max_pages=1)
        # a cover letter has no resume headings / date list
        for k in ("standard_headings", "dates_present"):
            cover_checks.pop(k, None)
            cover_notes = [n for n in cover_notes if n != f"FAILED: {k}"]
    else:
        cover_checks, cover_notes = {}, ["no cover letter in spec"]

    growth_ok = not any(r.find == "(all)" for r in edit_rep.rejected)
    report = {
        "company": spec.get("company"), "role": spec.get("role"),
        "resume_file": resume_path.name, "cover_letter_file": cover_path.name if cl else None,
        "ats_score_base": before.score, "ats_score_tailored": after.score,
        "resume": after.as_dict(),
        "edits_applied": [{"find": r.find, "replace": r.replace} for r in edit_rep.applied],
        "edits_rejected": [{"find": r.find, "replace": r.replace, "reason": r.reason}
                           for r in edit_rep.rejected],
        "text_growth": round(edit_rep.growth, 4),
        "cover_letter_checks": cover_checks, "cover_letter_notes": cover_notes,
    }
    ok = after.passed_all and growth_ok and all(cover_checks.values())
    report["all_checks_passed"] = ok
    (out / "ats_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("ats_score_base", "ats_score_tailored",
                                              "all_checks_passed")} |
                     {"applied": len(edit_rep.applied), "rejected": len(edit_rep.rejected),
                      "missing": after.missing, "gaps": after.gaps,
                      "failed_checks": [k for k, v in {**after.checks, **cover_checks}.items() if not v]}))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
