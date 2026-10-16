"""Minutes parser: application sections, who testified, board professionals'
remarks, waivers and outcomes."""
from __future__ import annotations

import re

from ..util import squash
from .agenda import meeting_info, split_items
from .common import categorize, decision, extract_waivers, primary_category
from .professionals import attributed_remarks, board_roster, extract_professionals

VOTE = re.compile(r"\b(?:ayes?|yes|in\s+favor)\s*[:\-]?\s*(\d+)\b.*?\b(?:nays?|no|opposed)\s*[:\-]?\s*(\d+)\b|\bvote\s+(?:of|was)\s+(\d+)\s*[-–to]+\s*(\d+)", re.I | re.S)


def parse_minutes(text: str, title: str = "") -> dict:
    info = meeting_info(text, title)
    header = text[:6000]
    roster = board_roster(header)
    items = split_items(text)
    all_pros = []
    for it in items:
        app = it["app_numbers"][0] if it["app_numbers"] else (it["applicant"] or it["heading"][:60])
        pros = extract_professionals(it["text"], application=app)
        it["professionals"] = [p.to_dict() for p in pros]
        it["waivers"] = [w.to_dict() for w in extract_waivers(it["text"], source="minutes", application=app)]
        it["decision"] = decision(it["text"])
        vm = VOTE.search(it["text"])
        it["vote"] = (f"{vm.group(1) or vm.group(3)}-{vm.group(2) or vm.group(4)}") if vm else None
        it["board_remarks"] = attributed_remarks(it["text"], roster)
        for r in it["board_remarks"]:
            r["category"] = primary_category(r["text"])
        it["topics"] = categorize(it["text"], top=5)
        all_pros.extend(pros)
    # professionals outside item sections (e.g. roll-call header)
    header_pros = [p for p in extract_professionals(header) if p.side == "board"]
    waivers_all = [w.to_dict() for w in extract_waivers(text, source="minutes")] if not items else []
    return {
        **info,
        "board_roster": roster,
        "board_professionals": [p.to_dict() for p in header_pros],
        "items": items,
        "n_items": len(items),
        "professionals": [p.to_dict() for p in all_pros],
        "waivers": waivers_all,
    }
