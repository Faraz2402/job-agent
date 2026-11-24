"""Cover letter .docx built from the base resume itself, so it has the same
page size, margins, font (Cambria), sizes, name block and clickable links.

The first 3 paragraphs of the resume (name, title line, contact line with
links) are kept as the letterhead. Everything after is removed and replaced
by the letter, written with the resume's own body-paragraph formatting.
"""
from __future__ import annotations

import copy
import datetime as dt

from docx import Document
from docx.oxml.ns import qn

HEADER_PARAS = 3          # name, title, contact line
BODY_TEMPLATE_IDX = 4     # the Professional Summary paragraph


def _plain_run_rpr(p_el):
    for r in p_el.findall(qn("w:r")):
        rpr = r.find(qn("w:rPr"))
        if rpr is None or rpr.find(qn("w:b")) is None:
            return copy.deepcopy(rpr) if rpr is not None else None
    return None


def build_cover_letter(base_path: str, out_path: str, *, title_line: str | None,
                       recipient_lines: list[str], greeting: str, paragraphs: list[str],
                       closing: str = "Sincerely,", signature: str = "Faraz Ahmad",
                       date: str | None = None) -> None:
    doc = Document(base_path)
    body = doc.element.body
    paras = body.findall(qn("w:p"))
    template = copy.deepcopy(paras[BODY_TEMPLATE_IDX])
    run_rpr = _plain_run_rpr(template)
    ppr = template.find(qn("w:pPr"))

    if title_line:
        # keep the resume's title line formatting; just change the words
        tps = paras[1].iter(qn("w:t"))
        first = next(tps, None)
        if first is not None:
            first.text = title_line
            for t in tps:
                t.text = ""

    for p in paras[HEADER_PARAS:]:
        body.remove(p)
    sect = body.find(qn("w:sectPr"))

    def add(text: str, space_after: int | None = 160, bold: bool = False):
        p = copy.deepcopy(template)
        for child in list(p):
            if child.tag != qn("w:pPr"):
                p.remove(child)
        if p.find(qn("w:pPr")) is None and ppr is not None:
            p.insert(0, copy.deepcopy(ppr))
        pp = p.find(qn("w:pPr"))
        if pp is not None and space_after is not None:
            sp = pp.get_or_add_spacing()   # inserts in schema order
            sp.set(qn("w:after"), str(space_after))
            sp.set(qn("w:before"), "0")
        if text:
            r = p.makeelement(qn("w:r"), {})
            if run_rpr is not None:
                rp = copy.deepcopy(run_rpr)
                r.append(rp)
            if bold:
                rp = r.find(qn("w:rPr"))
                if rp is None:
                    rp = r.makeelement(qn("w:rPr"), {})
                    r.insert(0, rp)
                rp.append(rp.makeelement(qn("w:b"), {}))
            t = r.makeelement(qn("w:t"), {})
            t.text = text
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            r.append(t)
            p.append(r)
        sect.addprevious(p)

    add("", 120)
    add(date or dt.date.today().strftime("%d %B %Y").lstrip("0"), 200)
    for i, line in enumerate(recipient_lines):
        add(line, 0 if i < len(recipient_lines) - 1 else 200)
    add(greeting, 160)
    for para in paragraphs:
        add(para, 160)
    add(closing, 0)
    add(signature, 0, bold=True)
    doc.save(out_path)
