"""Agenda parser: meeting date, board, and the application items on the agenda."""
from __future__ import annotations

import re
from typing import Optional

from ..classify import classify_board
from ..util import find_dates, squash
from .common import (addresses, app_numbers, app_types, block_lots, decision, zones)

ITEM_START = re.compile(
    r"^\s*(?:(?:\d{1,3}|[A-Za-z]|[ivxIVX]{1,5})\s*[.):\-]\s+)?(?=.*(?:Block\s*\d|Application|Appl\.|App\.?\s*(?:No|#)|Case|Docket|Cal\.|Applicant|Site\s*Plan|Subdivision|Variance|\b(?:PB|ZB|ZBA|BOA|LUB)[-\s]?\d))",
    re.I | re.M)
FIELD = re.compile(r"^\s*(Applicant|Owner|Location|Address|Premises|Property|Block\s*/\s*Lot|Zone|Zoning|Application\s+(?:No|#|Type)|Type|Proposal|Description|Relief|Variances?|Attorney|Engineer|Planner|Architect|Status)\s*[:\-]\s*(.+)$", re.I | re.M)
SECTION_HEADERS = re.compile(r"^\s*(public\s+hearings?|new\s+business|old\s+business|continued|carried|completeness|resolutions?|memorializ|informal|concept|discussion|completeness\s+review|applications?\s+for\s+completeness|unfinished\s+business|administrative)", re.I)


def meeting_info(text: str, title: str = "") -> dict:
    head = text[:2500]
    dates = find_dates(title + "\n" + head, limit=3)
    board = classify_board(title + " " + head[:600]) or classify_board(text[:5000])
    kind = "regular"
    if re.search(r"special\s+meeting", head, re.I):
        kind = "special"
    elif re.search(r"reorganization|re-organization", head, re.I):
        kind = "reorganization"
    elif re.search(r"work\s*(shop|session)", head, re.I):
        kind = "workshop"
    return {"meeting_date": dates[0].isoformat() if dates else None, "board_kind": board, "meeting_kind": kind}


def split_items(text: str) -> list[dict]:
    """Split minutes/agenda text into application sections.

    An item starts at a line containing an application identifier (app number,
    Block/Lot, 'Applicant:' ...). Everything up to the next such line belongs
    to the item. Non-application sections (roll call, approval of minutes) are
    dropped unless they contain application markers.
    """
    lines = text.splitlines()
    starts = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or len(s) > 400:
            continue
        has_id = bool(block_lots(s) or app_numbers(s) or re.search(r"^\s*(Applicant|Application)\s*[:#]", s, re.I))
        looks_like_heading = len(s) < 220 and (has_id or (re.match(r"^\s*(?:\d{1,3}|[A-Za-z]|[ivxIVX]{1,5})\s*[.):\-]\s+", s) and re.search(r"site\s*plan|subdivision|variance|application", s, re.I)))
        if looks_like_heading:
            # avoid starting a new item for a 'Block/Lot' field line right after an item start
            if starts and i - starts[-1] <= 3 and not app_numbers(s) and not re.search(r"^\s*\d{1,3}\s*[.)]", s):
                continue
            starts.append(i)
    items = []
    for n, st in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        chunk = "\n".join(lines[st:end]).strip()
        if len(chunk) < 20:
            continue
        items.append(parse_item(chunk))
    return items


def _applicant_name(chunk: str, heading: str) -> Optional[str]:
    m = re.search(r"\bApplicant\s*(?:/\s*Owner)?\s*[:\-]\s*([^\n]{3,120})", chunk, re.I)
    if m:
        return squash(m.group(1)).rstrip(",;")
    m = re.search(r"\b(?:application|appl\.|app\.)\s+of\s+([A-Z][^,\n;]{2,100}?)(?:\s+for\b|,|;|\n|\s+-\s+|\s+–\s+)", chunk, re.I)
    if m:
        return squash(m.group(1))
    # "PB-24-03  ABC Holdings, LLC – Preliminary and Final Site Plan"
    h = re.sub(r"^\s*(?:\d{1,3}|[A-Za-z])\s*[.):\-]\s+", "", heading)
    h = re.sub(r"^\s*(?:Application|Appl\.|App\.|Case|Docket|Cal\.)?\s*(?:No\.?|#)?\s*[A-Z]{0,5}[-\s]?\d{1,4}[-/]\d{1,4}(?:[-/]\d{1,4})?[A-Z]?\s*[-–:,]?\s*", "", h, flags=re.I)
    parts = re.split(r"\s+[–\-—]\s+|:\s+|\s*,\s*(?=(?:Block|Lot|\d+\s+[A-Z]|Preliminary|Final|Minor|Major|Site|Use|Bulk|Variance|Subdivision))", h, maxsplit=1)
    cand = squash(parts[0]) if parts else ""
    if 3 <= len(cand) <= 100 and not re.match(r"^(Block|Lot|Preliminary|Final|Minor|Major|Site|Use|Bulk|Variance|Subdivision|Resolution|Public)", cand, re.I):
        return cand.rstrip(",;")
    return None


def parse_item(chunk: str) -> dict:
    heading = chunk.splitlines()[0].strip()
    fields = {m.group(1).lower(): squash(m.group(2)) for m in FIELD.finditer(chunk)}
    types = app_types(chunk)
    return {
        "heading": heading[:300],
        "app_numbers": app_numbers(chunk)[:3],
        "applicant": _applicant_name(chunk, heading),
        "block_lots": block_lots(chunk)[:4],
        "addresses": addresses(fields.get("location") or fields.get("address") or chunk[:600])[:3],
        "zones": zones(chunk[:1500])[:3],
        "app_types": types,
        "is_site_plan": any("site plan" in t for t in types),
        "status": decision(chunk),
        "fields": fields,
        "text": chunk[:6000],
    }


def parse_agenda(text: str, title: str = "") -> dict:
    info = meeting_info(text, title)
    items = split_items(text)
    return {**info, "items": items, "n_items": len(items),
            "n_site_plan_items": sum(1 for i in items if i["is_site_plan"])}
