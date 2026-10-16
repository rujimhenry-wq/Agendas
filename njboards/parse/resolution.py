"""Resolution-of-approval parser: decision, conditions of approval, relief
granted, witnesses, waivers."""
from __future__ import annotations

import re

from ..util import find_dates, sentences, squash
from .common import (addresses, app_numbers, app_types, block_lots, categorize, comment_key, decision,
                     extract_waivers, split_numbered, zones)
from .professionals import extract_professionals

CONDITIONS_HEAD = re.compile(r"subject\s+to\s+the\s+following\s+(?:terms\s+and\s+)?conditions|conditions\s+of\s+approval|following\s+conditions\s*:|upon\s+the\s+following\s+conditions|conditioned\s+(?:upon|on)\s+the\s+following", re.I)
RELIEF = re.compile(r"(?:variance|waiver|exception|relief)\s+(?:is|are|be)\s+(?:hereby\s+)?(?:granted|approved|denied)[^.]{0,300}|(?:granted|approved|denied)\s+(?:the\s+)?(?:following\s+)?(?:variances?|waivers?|relief|exceptions?)[^.]{0,300}", re.I)
VOTE = re.compile(r"(?:ayes?|in\s+favor|yes)\s*[:\-]?\s*([A-Za-z,.\s&]+?)(?:\n|nays?|no\s*[:\-]|opposed|abstain|absent|$)", re.I)


def parse_resolution(text: str, title: str = "") -> dict:
    head = text[:5000]
    dates = find_dates(title + "\n" + head, limit=4)
    app = app_numbers(head)
    pros = extract_professionals(text)
    witnesses = [p.to_dict() for p in pros if p.side != "board"]
    board_pros = [p.to_dict() for p in pros if p.side == "board"]

    conditions = []
    m = CONDITIONS_HEAD.search(text)
    if m:
        tail = text[m.end(): m.end() + 40000]
        # conditions usually run until 'BE IT FURTHER RESOLVED' / 'NOW, THEREFORE' / signature
        stop = re.search(r"BE\s+IT\s+FURTHER\s+RESOLVED|NOW,?\s+THEREFORE|CERTIFICATION|I\s+hereby\s+certify|ROLL\s+CALL|Motion\s+(by|to)|Those\s+in\s+favor", tail, re.I)
        tail = tail[: stop.start()] if stop and stop.start() > 200 else tail
        for it in split_numbered(tail):
            cats = categorize(it["text"])
            conditions.append({"number": it["number"], "text": it["text"][:1000], "category": cats[0],
                               "categories": cats, "key": comment_key(it["text"]),
                               "standard": bool(re.search(r"outside\s+agenc|other\s+(governmental\s+)?agenc|fees?\s+and\s+escrow|escrow|inspection\s+fees?|performance\s+guarantee|maintenance\s+guarantee|affordable\s+housing\s+(fee|contribution)|developer'?s?\s+agreement|county\s+planning\s+board\s+approval|soil\s+conservation|signed\s+and\s+sealed|revised\s+plans?\s+(shall|to)\s+be\s+submitted|compliance\s+plans?|in\s+accordance\s+with\s+the\s+testimony", it["text"], re.I))})
    relief = [squash(x.group(0))[:400] for x in RELIEF.finditer(text)][:12]
    # testimony summary sentences
    testimony_sents = [squash(s)[:400] for s in sentences(text) if re.search(r"testif|testimony", s, re.I)][:40]
    return {
        "meeting_date": dates[0].isoformat() if dates else None,
        "dates": [d.isoformat() for d in dates],
        "app_numbers": app[:3],
        "applicant": _applicant(head),
        "block_lots": block_lots(head)[:4],
        "addresses": addresses(head)[:3],
        "zones": zones(head)[:2],
        "app_types": app_types(head),
        "decision": decision(text[:3000] + " " + text[-4000:]) or decision(text),
        "conditions": conditions,
        "n_conditions": len(conditions),
        "relief": relief,
        "witnesses": witnesses,
        "board_professionals": board_pros,
        "waivers": [w.to_dict() for w in extract_waivers(text, source="resolution", application=app[0] if app else None)],
        "testimony": testimony_sents,
    }


def _applicant(head: str):
    m = re.search(r"(?:application|appl\.)\s+of\s+([A-Z][^,\n;]{2,100}?)(?:\s+for\b|,|;|\n|\s+\()", head, re.I)
    if m:
        return squash(m.group(1))
    m = re.search(r"\bApplicant\s*[:\-]\s*([^\n]{3,120})", head, re.I)
    if m:
        return squash(m.group(1))
    m = re.search(r"(?:IN\s+THE\s+MATTER\s+OF|RE:)\s+(?:THE\s+APPLICATION\s+OF\s+)?([A-Z][^\n,;]{2,100})", head)
    return squash(m.group(1)) if m else None
