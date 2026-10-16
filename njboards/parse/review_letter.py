"""Review letter parser (board engineer / planner / landscape architect /
traffic consultant letters): numbered comments by category, completeness
determination, waiver recommendations, author and firm."""
from __future__ import annotations

import re
from typing import Optional

from ..util import find_dates, squash
from .common import (addresses, app_numbers, app_types, block_lots, categorize, comment_key, decision,
                     extract_waivers, split_numbered, submission_topic, zones)
from .professionals import extract_professionals

RE_LINE = re.compile(r"^\s*(?:RE|Re|SUBJECT|Subject)\s*[:\-]\s*(.+(?:\n(?!\s*\n)[^\n]{0,120}){0,4})", re.M)
AUTHOR_TYPE = [
    ("engineer", re.compile(r"engineer|\bP\.?E\.?\b|engineering", re.I)),
    ("planner", re.compile(r"planner|\bP\.?P\.?\b|AICP|planning\s+consultant", re.I)),
    ("landscape architect", re.compile(r"landscape", re.I)),
    ("traffic", re.compile(r"traffic|PTOE", re.I)),
    ("fire official", re.compile(r"fire\s+(official|marshal|prevention|department|bureau)", re.I)),
    ("environmental commission", re.compile(r"environmental\s+commission", re.I)),
    ("historic preservation", re.compile(r"historic", re.I)),
    ("health", re.compile(r"board\s+of\s+health|health\s+department", re.I)),
    ("zoning officer", re.compile(r"zoning\s+officer|zoning\s+official", re.I)),
]
RECOMMEND_COMPLETE = re.compile(r"(recommend|consider|find|deem)\w*\s+(?:that\s+)?(?:the\s+application\s+)?(?:be\s+)?(?:deemed\s+)?(complete|incomplete)\b", re.I)


def letter_meta(text: str, title: str = "") -> dict:
    head = text[:4000]
    re_m = RE_LINE.search(head)
    re_line = squash(re_m.group(1)) if re_m else ""
    dates = find_dates(head, limit=2)
    author_blob = (title + " " + head[:1200] + " " + text[-2500:])
    author_type = next((n for n, p in AUTHOR_TYPE if p.search(author_blob)), "unknown")
    pros = extract_professionals(text[-3000:]) + extract_professionals(head[:1500])
    author = next((p for p in pros if p.credentials), None)
    comp = None
    m = RECOMMEND_COMPLETE.search(text)
    if m:
        comp = "incomplete" if "incomplete" in m.group(2).lower() else "complete"
    elif re.search(r"deemed\s+incomplete|is\s+incomplete", text, re.I):
        comp = "incomplete"
    elif re.search(r"deemed\s+complete|is\s+complete\b", text, re.I):
        comp = "complete"
    return {
        "re": re_line[:400],
        "letter_date": dates[0].isoformat() if dates else None,
        "author_type": author_type,
        "author": author.to_dict() if author else None,
        "app_numbers": app_numbers(re_line + " " + head)[:3],
        "block_lots": block_lots(re_line + " " + head)[:3],
        "addresses": addresses(re_line + " " + head[:1500])[:3],
        "zones": zones(head)[:2],
        "app_types": app_types(re_line + " " + head),
        "completeness": comp,
    }


def parse_review_letter(text: str, title: str = "") -> dict:
    meta = letter_meta(text, title)
    app = meta["app_numbers"][0] if meta["app_numbers"] else (meta["re"][:80] or None)
    items = split_numbered(text)
    comments = []
    for it in items:
        cats = categorize(it["text"])
        sec = it["section"].lower()
        if sec and not re.search(r"general|comment|review|follow", sec):
            # a section heading like 'STORMWATER MANAGEMENT' decides ties and fills in
            # for comments that have no strong category of their own
            sec_cats = categorize(it["section"], top=3)
            if cats[0] in ("general", "plan_corrections", "testimony", "completeness"):
                strong = [c for c in sec_cats if c != "general"]
                if strong:
                    cats = strong[:1] + [c for c in cats if c not in strong][:1]
            elif cats[0] not in sec_cats:
                for c in cats[1:]:
                    if c in sec_cats:
                        cats = [c] + [x for x in cats if x != c]
                        break
        comments.append({
            "number": it["number"], "section": it["section"], "text": it["text"][:1200],
            "categories": cats, "category": cats[0], "key": comment_key(it["text"]),
            "is_waiver": bool(re.search(r"\bwaiv", it["text"], re.I)),
            "waiver_topic": submission_topic(it["text"]) if re.search(r"\bwaiv", it["text"], re.I) else None,
            "requires_testimony": bool(re.search(r"testimony|testif|should\s+(address|describe|explain|discuss)\s+at|at\s+the\s+hearing", it["text"], re.I)),
            "requires_variance": bool(re.search(r"variance|design\s+waiver|exception\s+(is|will\s+be)\s+required|does\s+not\s+(comply|conform|meet)|non-?conform", it["text"], re.I)),
            "outside_agency": bool(re.search(r"county|NJDOT|DEP\b|NJDEP|soil\s+conservation|utilit(y|ies)\s+authority|MUA\b|water\s+company|fire\s+official", it["text"], re.I)),
        })
    waivers = [w.to_dict() for w in extract_waivers(text, source="review_letter", application=app)]
    return {**meta, "comments": comments, "n_comments": len(comments), "waivers": waivers,
            "categories_count": _count([c["category"] for c in comments])}


def _count(vals: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in vals:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
