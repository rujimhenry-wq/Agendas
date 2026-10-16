"""Document parsers. parse_document() dispatches on doc_type and returns a JSON-able dict."""
from __future__ import annotations

from .agenda import parse_agenda
from .checklist import parse_checklist
from .minutes import parse_minutes
from .resolution import parse_resolution
from .review_letter import parse_review_letter
from .common import app_numbers, block_lots, app_types, extract_waivers, categorize
from .professionals import extract_professionals


def parse_generic(text: str, title: str = "") -> dict:
    head = text[:4000]
    return {
        "app_numbers": app_numbers(head)[:3], "block_lots": block_lots(head)[:3], "app_types": app_types(head),
        "professionals": [dict(p.to_dict(), side=("board" if p.side == "board" else "applicant")) for p in extract_professionals(text)],
        "waivers": [w.to_dict() for w in extract_waivers(text, source="other")],
        "topics": categorize(text, top=5),
    }


PARSERS = {
    "agenda": parse_agenda,
    "minutes": parse_minutes,
    "review_letter": parse_review_letter,
    "resolution": parse_resolution,
    "checklist": parse_checklist,
}


def parse_document(doc_type: str, text: str, title: str = "") -> dict:
    fn = PARSERS.get(doc_type, parse_generic)
    result = fn(text, title)
    result["doc_type"] = doc_type
    result["chars"] = len(text)
    return result
