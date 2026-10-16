"""eCode360 "Public Documents" portal (General Code hosts minutes, agendas,
resolutions and ordinances for some towns alongside their code):

  https://ecode360.com/<CODE>/documents/<Category>          e.g. Minutes, Agendas, Legislation
  https://ecode360.com/documents/pub/<CODE>/<Category>/?subCategory=Planning+Board

One page lists every sub-category (board), year and document:
  div.pubDocCategoryPaper > h2.pubDocCategoryTitle  (board name)
                           > h3.pubDocYearRow        (year)
                           > a[href='/<CODE>/document/<id>.pdf']  (document)
The whole site is behind Cloudflare, so the fetcher's browser fallback is
used for listing and downloading alike.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..classify import classify_board, classify_link
from . import DocLink

NAME = "ecodedocs"
CATEGORIES = ["Minutes", "Agendas", "Legislation", "Resolutions", "Ordinances", "Documents"]
PORTAL = re.compile(r"ecode360\.com/(?:documents/pub/([A-Z]{2}\d{4})|([A-Z]{2}\d{4})/documents?)", re.I)


def detect(html: str, url: str) -> bool:
    return bool(PORTAL.search(url) or PORTAL.search(html))


def code_from(url_or_html: str) -> Optional[str]:
    m = PORTAL.search(url_or_html)
    return (m.group(1) or m.group(2)).upper() if m else None


def _parse_listing(html: str, base: str, category: str, min_year: int) -> list[DocLink]:
    soup = BeautifulSoup(html, "lxml")
    out: list[DocLink] = []
    for paper in soup.select("div.pubDocCategoryPaper, div[id^=category-]"):
        title_el = paper.select_one("h2.pubDocCategoryTitle, h2[id^=category-label]")
        board_name = title_el.get_text(" ", strip=True) if title_el else ""
        kind = classify_board(board_name)
        if not kind:
            continue
        year = None
        for el in paper.find_all(["h3", "a"]):
            if el.name == "h3":
                m = re.search(r"\b(19|20)\d{2}\b", el.get_text(" ", strip=True))
                year = int(m.group(0)) if m else None
                continue
            href = el.get("href", "")
            if not re.search(r"/document/\d+", href):
                continue
            if year and year < min_year:
                continue
            text = el.get_text(" ", strip=True)
            url = urljoin(base, href)
            lc = classify_link(url, text, f"{board_name} {category}", page_board=kind)
            dt = lc.doc_type
            if dt in ("other", "site_plan") and category.lower() in ("minutes", "agendas"):
                dt = category.lower().rstrip("s")
            if category.lower() == "legislation" and dt == "other":
                dt = "resolution" if re.search(r"resolution|\bres\b", text, re.I) else "ordinance"
            out.append(DocLink(url=url, title=text or f"{board_name} {category}", doc_type=dt, board_kind=kind,
                               meeting_date=lc.meeting_date, year=lc.year or year, context=f"{board_name} {category}",
                               platform=NAME))
    return out


def list_documents(fetcher, page_url: str, html: str, board_kind: Optional[str], code: Optional[str] = None) -> list[DocLink]:
    code = code or code_from(page_url) or code_from(html)
    if not code:
        return []
    docs: dict[str, DocLink] = {}
    if not fetcher.allowed(f"https://ecode360.com/{code}/documents/Minutes"):
        import logging
        logging.getLogger("njboards.ecodedocs").warning(
            "records for %s are on eCode360's documents portal (https://ecode360.com/%s/documents/Minutes), "
            "which its robots.txt closes to bots; they are not collected automatically", code, code)
        return []
    for cat in CATEGORIES:
        url = f"https://ecode360.com/{code}/documents/{cat}"
        r = fetcher.get(url, max_age_hours=24 * 3)
        if not r.ok or not r.is_html or "pubDoc" not in r.text:
            continue
        for d in _parse_listing(r.text, "https://ecode360.com/", cat, fetcher.s.min_year):
            docs.setdefault(d.url, d)
    return list(docs.values())
