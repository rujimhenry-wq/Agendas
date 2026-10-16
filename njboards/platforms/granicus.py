"""Granicus / Legistar style publishers: ViewPublisher.php tables with
Agenda / Minutes columns. Mostly plain links, so the adapter only tags them."""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..classify import classify_link
from . import DocLink

NAME = "granicus"


def detect(html: str, url: str) -> bool:
    return bool(re.search(r"[a-z0-9\-]+\.granicus\.com|ViewPublisher\.php|legistar\.com", url, re.I)
                or re.search(r"ViewPublisher\.php|[a-z0-9\-]+\.granicus\.com/(ViewPublisher|GeneratedAgenda|MetaViewer|player)|legistar\.com", html[:60000], re.I))


def list_documents(fetcher, page_url: str, html: str, board_kind: Optional[str]) -> list[DocLink]:
    soup = BeautifulSoup(html, "lxml")
    out: list[DocLink] = []
    for tr in soup.select("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        ctx = " ".join(cells)[:300]
        for a in tr.select("a[href]"):
            url = urljoin(page_url, a["href"])
            if "javascript:" in url:
                continue
            text = a.get_text(" ", strip=True)
            lc = classify_link(url, text, ctx, page_board=board_kind)
            if lc.doc_type in ("agenda", "minutes", "resolution") or lc.is_document:
                out.append(DocLink(url=url, title=text or ctx[:100], doc_type=lc.doc_type,
                                   board_kind=lc.board_kind, meeting_date=lc.meeting_date,
                                   year=lc.year, context=ctx, platform=NAME))
    return out
