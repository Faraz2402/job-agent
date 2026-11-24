"""Keyword-level edits to a .docx that keep every bit of formatting.

An edit replaces one exact phrase with another. To guarantee the look of the
document never changes, an edit is accepted only if the phrase:
  * appears exactly once in the document,
  * sits inside runs that all share the same formatting (so a bold number
    never turns plain, and plain text never turns bold),
  * is not inside a hyperlink (contact links stay untouched).
Text is only ever changed inside existing <w:t> elements: no paragraphs,
runs, styles, sizes, numbering or indentation are added or removed.

Truthfulness guards (the resume must stay factual):
  * a replacement may not introduce a number that is not in the phrase it
    replaces,
  * it may not introduce a forbidden claim (see FORBIDDEN),
  * total text may not grow by more than MAX_GROWTH, so the page count holds.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from docx import Document
from docx.oxml.ns import qn

MAX_GROWTH = 0.04          # +4% characters at most
MAX_EDITS = 25

# Claims Faraz cannot make. A replacement may only contain these words if the
# base resume already does.
FORBIDDEN = [
    r"\bpython\b", r"\btableau\b", r"\bqlik\b", r"\bcoupa\b", r"\blooker\b",
    r"\bpyspark\b", r"\bdatabricks\b", r"\bsnowflake\b", r"\bmachine learning\b",
    r"\bpeople manag", r"\bmanaged a team\b", r"\bled (?:supplier )?negotiations?\b",
    r"\bnegotiated\b", r"\bredlin", r"\bdrafted contracts?\b", r"\badvanced sql\b",
    r"\bexpert in sql\b", r"\bsql expert\b",
]

NUM_RE = re.compile(r"\d[\d,.]*")


@dataclass
class EditResult:
    find: str
    replace: str
    ok: bool
    reason: str = ""


@dataclass
class EditReport:
    applied: list[EditResult] = field(default_factory=list)
    rejected: list[EditResult] = field(default_factory=list)
    chars_before: int = 0
    chars_after: int = 0

    @property
    def growth(self) -> float:
        return (self.chars_after - self.chars_before) / max(1, self.chars_before)


def _rpr_key(run) -> str:
    rpr = run.find(qn("w:rPr"))
    if rpr is None:
        return ""
    rpr = copy.deepcopy(rpr)
    # Spell-check / language marks do not change how text looks.
    for el in list(rpr):
        if el.tag in (qn("w:lang"), qn("w:noProof"), qn("w:rtl")):
            rpr.remove(el)
    from lxml import etree
    return etree.tostring(rpr, method="c14n").decode()


def _in_hyperlink(run) -> bool:
    p = run.getparent()
    while p is not None:
        if p.tag == qn("w:hyperlink"):
            return True
        if p.tag == qn("w:p"):
            return False
        p = p.getparent()
    return False


def _text_runs(paragraph_el):
    """[(run, t_element, text)] for every <w:t> in document order."""
    out = []
    for r in paragraph_el.iter(qn("w:r")):
        for t in r.findall(qn("w:t")):
            out.append((r, t, t.text or ""))
    return out


def body_text(doc) -> str:
    return "\n".join(p.text for p in doc.paragraphs)


def _check_truth(find: str, replace: str, base_text: str) -> str | None:
    new_nums = set(NUM_RE.findall(replace)) - set(NUM_RE.findall(find))
    if new_nums:
        return f"adds numbers not in the original phrase: {sorted(new_nums)}"
    low_base = base_text.lower()
    for pat in FORBIDDEN:
        if re.search(pat, replace, re.I) and not re.search(pat, low_base, re.I):
            word = pat.replace("\\b", "")
            return f"adds a claim the resume can't support ({word})"
    if not replace.strip():
        return "empty replacement"
    return None


def _apply_one(doc, find: str, replace: str) -> str | None:
    hits = []
    for p in doc.paragraphs:
        runs = _text_runs(p._p)
        full = "".join(t for _, _, t in runs)
        start = full.find(find)
        while start != -1:
            hits.append((p, runs, start))
            start = full.find(find, start + 1)
    if not hits:
        return "phrase not found (copy it exactly from the base resume)"
    if len(hits) > 1:
        return f"phrase appears {len(hits)} times - make it longer so it is unique"
    p, runs, start = hits[0]
    end = start + len(find)
    # locate the runs covering [start, end)
    pos, covered = 0, []
    for r, t, txt in runs:
        a, b = pos, pos + len(txt)
        if b > start and a < end:
            covered.append((r, t, txt, a))
        pos = b
    if any(_in_hyperlink(r) for r, *_ in covered):
        return "phrase is inside a link - contact links are never changed"
    keys = {_rpr_key(r) for r, *_ in covered}
    if len(keys) > 1:
        return ("phrase mixes formatting (e.g. bold and plain) - edit only the "
                "bold part or only the plain part")
    # write: first covered <w:t> gets prefix + replacement + (suffix if same t)
    first_r, first_t, first_txt, first_a = covered[0]
    last_r, last_t, last_txt, last_a = covered[-1]
    prefix = first_txt[: start - first_a]
    suffix = last_txt[end - last_a:]
    if first_t is last_t:
        first_t.text = prefix + replace + suffix
    else:
        first_t.text = prefix + replace
        for r, t, txt, a in covered[1:-1]:
            t.text = ""
        last_t.text = suffix
    for t in {id(x[1]): x[1] for x in covered}.values():
        if t.text and (t.text[0] == " " or t.text[-1] == " "):
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return None


def apply_edits(base_path: str, edits: list[dict], out_path: str) -> EditReport:
    doc = Document(base_path)
    base_text = body_text(doc)
    report = EditReport(chars_before=len(base_text))
    for e in edits[:MAX_EDITS]:
        find, replace = e.get("find", ""), e.get("replace", "")
        why = _check_truth(find, replace, base_text) or _apply_one(doc, find, replace)
        res = EditResult(find, replace, ok=why is None, reason=why or e.get("why", ""))
        (report.applied if res.ok else report.rejected).append(res)
    for e in edits[MAX_EDITS:]:
        report.rejected.append(EditResult(e.get("find", ""), e.get("replace", ""), False,
                                          f"more than {MAX_EDITS} edits"))
    report.chars_after = len(body_text(doc))
    if report.growth > MAX_GROWTH:
        report.rejected.append(EditResult("(all)", "(all)", False,
            f"text grew {report.growth:.1%} (max {MAX_GROWTH:.0%}) - shorten replacements so it stays 2 pages"))
    doc.save(out_path)
    return report
