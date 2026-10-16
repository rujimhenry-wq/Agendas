"""Submission checklist parser: numbered requirements, which are flagged
waivable, and which checklist (site plan / subdivision / variance) they belong to."""
from __future__ import annotations

import re

from ..util import squash
from .common import split_numbered, submission_topic

SECTION_KIND = [
    ("minor site plan", re.compile(r"minor\s+site\s*plan", re.I)),
    ("preliminary major site plan", re.compile(r"preliminary\s+(major\s+)?site\s*plan", re.I)),
    ("final major site plan", re.compile(r"final\s+(major\s+)?site\s*plan", re.I)),
    ("site plan", re.compile(r"site\s*plan", re.I)),
    ("minor subdivision", re.compile(r"minor\s+subdivision", re.I)),
    ("preliminary major subdivision", re.compile(r"preliminary\s+(major\s+)?subdivision", re.I)),
    ("final major subdivision", re.compile(r"final\s+(major\s+)?subdivision", re.I)),
    ("subdivision", re.compile(r"subdivision", re.I)),
    ("variance", re.compile(r"variance", re.I)),
    ("conditional use", re.compile(r"conditional\s+use", re.I)),
    ("general", re.compile(r"general|all\s+applications", re.I)),
]
WAIVABLE = re.compile(r"\bwaiv(able|er\s+(may|can)\s+be\s+(requested|granted))|\bmay\s+be\s+waived|\bW\b\s*$|\(W\)|\bwaiver\s+requested", re.I)
NOT_WAIVABLE = re.compile(r"not\s+waivable|cannot\s+be\s+waived|may\s+not\s+be\s+waived|\bNW\b|\(NW\)", re.I)


def parse_checklist(text: str, title: str = "") -> dict:
    items = split_numbered(text, min_len=8)
    out = []
    for it in items:
        sec = it["section"] or title
        kind = next((k for k, p in SECTION_KIND if p.search(sec)), None) or next((k for k, p in SECTION_KIND if p.search(title)), "unknown")
        out.append({
            "number": it["number"], "section": it["section"], "checklist": kind,
            "text": it["text"][:600], "topic": submission_topic(it["text"]),
            "waivable": None if not (WAIVABLE.search(it["text"]) or NOT_WAIVABLE.search(it["text"]))
            else not NOT_WAIVABLE.search(it["text"]),
        })
    kinds = sorted({i["checklist"] for i in out})
    return {"items": out, "n_items": len(out), "checklists": kinds,
            "mentions_waivers": bool(re.search(r"\bwaiv", text, re.I)),
            "fee_mentions": [squash(s)[:200] for s in re.findall(r"[^.\n]*\$\s?[\d,]+[^.\n]*", text)[:10]]}
