"""Classify links/documents by type and board, from URL + link text + context."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse

from .util import first_date, parse_date_token

DOC_TYPES = [
    "agenda", "minutes", "resolution", "application", "checklist", "review_letter",
    "site_plan", "notice", "ordinance", "fee_schedule", "schedule", "other",
]

# Order matters: more specific patterns first.
_DOC_RULES: list[tuple[str, re.Pattern]] = [
    ("checklist", re.compile(r"check[\s-]?list|completeness\s+(form|check|review\s+form)|submission\s+requirements", re.I)),
    ("fee_schedule", re.compile(r"\bfee(s)?\s+(schedule|ordinance)|escrow\s+(schedule|fee)|application\s+fees?\b", re.I)),
    ("application", re.compile(
        r"\bapplication(s)?\s+(form|package|packet|for\s+development|instructions)|development\s+application|"
        r"\bapp(lication)?\s+form|land\s+use\s+application|site\s+plan\s+application\b(?!.*(agenda|minutes|resolution))|"
        r"variance\s+application|subdivision\s+application|zoning\s+permit\s+application|"
        r"\bapplications?\b(?!.*(agenda|minutes|resolution|hearing|pending|current))", re.I)),
    ("review_letter", re.compile(
        r"review\s+(letter|memo|report|comments?)|engineer'?s?\s+(review|report|letter|comments?)|"
        r"planner'?s?\s+(review|report|letter|comments?)|completeness\s+(review|letter|report|determination)|"
        r"technical\s+(review|comments?)|professional(s')?\s+(reports?|reviews?|letters?|comments?)|"
        r"(board|township|borough|city|town|village)\s+(engineer|planner|landscape\s+architect|traffic)\s*(ing)?\s*(review|report|letter|memo)|"
        r"staff\s+(report|review|memo)|\bTRC\b|technical\s+review\s+committee", re.I)),
    ("notice", re.compile(r"(legal|public|hearing)\s+notice|notice\s+of\s+(hearing|decision|adoption)", re.I)),
    ("resolution", re.compile(r"\bresolution(s)?\b|\bres(o|olutions?)?[-_ ]?\d|\bmemorializ", re.I)),
    ("agenda", re.compile(r"\bagenda", re.I)),
    ("minutes", re.compile(r"\bminutes?\b|\bmins\b|\bmeeting\s+summary", re.I)),
    ("notice", re.compile(r"\bnotices?\b", re.I)),
    ("ordinance", re.compile(r"ordinance|ecode360|municode|code\s*book|zoning\s+(map|code|regulations)|land\s+(use|development)\s+(ordinance|code|regulations)|\bLDO\b|\bULDO\b|\bUDO\b", re.I)),
    ("schedule", re.compile(r"meeting\s+(schedule|dates|calendar)|schedule\s+of\s+meetings|annual\s+(meeting\s+)?schedule", re.I)),
    ("site_plan", re.compile(
        r"site\s+plan(s)?(?!\s+(application|checklist|ordinance|review))|plan\s+set|\bplans\b|drawings|survey|"
        r"architectural|elevations|landscap(e|ing)\s+plan|grading\s+plan|stormwater\s+(management\s+)?(report|plan)|"
        r"traffic\s+(impact\s+)?(study|report)|environmental\s+impact|\bEIS\b|subdivision\s+plat|exhibits?\b|submission\s+documents?|"
        r"application\s+(documents|materials|exhibits|submission)", re.I)),
]

DOC_EXT = re.compile(r"\.(pdf|docx?|rtf|txt|pptx?|xlsx?|dwg|zip)(\?|$)", re.I)
DOC_HOST_HINT = re.compile(r"/DocumentCenter/View/|/AgendaCenter/ViewFile/|/document_center/|/docs?/|/files?/|/uploads?/|/wp-content/uploads/|/vertical/sites/|/media/|/ArchiveCenter/|/documents?/|/getfile|/download|/filedownload|viewfile|showdocument|/Archive\.aspx|/minutes/|/agendas/|/resolutions/|civicclerk|granicus|legistar|boarddocs|/ViewPublisher|/MeetingDetail|/GetMeetingFile|/Public/Document", re.I)

_BOARD_RULES: list[tuple[str, re.Pattern]] = [
    ("combined", re.compile(r"land\s+use\s+board|joint\s+land\s+use|planning\s*(and|&|/)\s*zoning|zoning\s*(and|&|/)\s*planning|unified\s+planning|development\s+review\s+board|\bLUB\b|planning\s+board\s*/\s*board\s+of\s+adjustment|combined\s+(planning|land\s+use)", re.I)),
    ("zoning", re.compile(r"zoning\s+board|board\s+of\s+adjustment|board\s+of\s+zoning|\bZBA\b|\bZBOA\b|\bBOA\b|\bBZA\b|zoning\s+bd|adjustment\s+board", re.I)),
    ("planning", re.compile(r"planning\s+board|planning\s+bd|planning\s+commission|\bPB\b(?!\s*&)|plan(ning)?\s*bd", re.I)),
]

NON_LAND_USE = re.compile(
    r"\b(council|committee|commission|township\s+committee|mayor|board\s+of\s+(health|education|recreation|fire|ethics|trustees|commissioners)|"
    r"environmental\s+commission|shade\s+tree|historic\s+preservation|library|rent\s+(leveling|control)|open\s+space|"
    r"police|fire\s+district|sewerage|utilities\s+authority|parking\s+authority|redevelopment\s+agency|"
    r"recreation|senior|budget|municipal\s+court|elections?|tax|water)\b", re.I)

LAND_USE_HINT = re.compile(r"planning|zoning|adjustment|land\s+use|site\s+plan|subdivision|variance|development", re.I)


@dataclass
class LinkClass:
    doc_type: str
    board_kind: Optional[str]
    is_document: bool
    meeting_date: Optional[str]
    year: Optional[int]
    confidence: float


def classify_board(text: str) -> Optional[str]:
    for kind, pat in _BOARD_RULES:
        if pat.search(text):
            return kind
    return None


def classify_doc_type(text: str) -> str:
    for kind, pat in _DOC_RULES:
        if pat.search(text):
            return kind
    return "other"


def looks_like_document(url: str, content_type: Optional[str] = None) -> bool:
    if content_type and any(t in content_type for t in ("pdf", "msword", "officedocument", "rtf", "octet-stream")):
        return True
    return bool(DOC_EXT.search(url) or DOC_HOST_HINT.search(url))


def _year_from(*texts: str) -> Optional[int]:
    for t in texts:
        m = re.search(r"\b(20[0-4]\d|199\d)\b", t)
        if m:
            return int(m.group(1))
    return None


def classify_link(url: str, link_text: str = "", context: str = "", page_board: Optional[str] = None) -> LinkClass:
    """Classify a link found on a municipal page.

    url: absolute url; link_text: anchor text; context: nearby text (heading,
    parent list, breadcrumb); page_board: board kind of the page it was found on.
    """
    path = unquote(urlparse(url).path)
    fname = path.rsplit("/", 1)[-1]
    blob = " | ".join(x for x in (link_text, fname, path.replace("/", " "), context) if x)
    conf = 0.9
    vf = re.search(r"/AgendaCenter/ViewFile/(Agenda|Minutes|Item)/", url, re.I)
    if vf:  # CivicPlus encodes the type in the path; link text is often just an icon
        doc_type = {"agenda": "agenda", "minutes": "minutes"}.get(vf.group(1).lower(), classify_doc_type(link_text + " " + context) if link_text.strip() else "site_plan")
    else:
        doc_type = classify_doc_type(link_text + " " + fname)
    if doc_type == "other":
        doc_type = classify_doc_type(blob)
        conf = 0.6
    board = classify_board(link_text + " " + path) or classify_board(context) or page_board
    if board is None and LAND_USE_HINT.search(blob) is None and NON_LAND_USE.search(blob):
        board = None
    date = None
    m = re.search(r"_((?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:19|20)\d{2})-\d+", url)
    if m:
        d = parse_date_token(m.group(1))
        date = d.isoformat() if d else None
    if not date:
        d = first_date(link_text) or first_date(fname.replace("_", " ").replace("-", " ")) or first_date(context[:200])
        date = d.isoformat() if d else None
    year = int(date[:4]) if date else _year_from(link_text, fname, context[:200])
    return LinkClass(doc_type=doc_type, board_kind=board, is_document=looks_like_document(url),
                     meeting_date=date, year=year, confidence=conf)


def is_land_use_page(text: str) -> bool:
    return bool(classify_board(text)) or bool(re.search(r"site\s+plan|land\s+use|subdivision|variance", text, re.I))
