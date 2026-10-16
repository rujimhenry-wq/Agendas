"""Discover planning / zoning board pages, document listings and the
municipality's online code (eCode360 / Municode) starting from its homepage."""
from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .classify import (NON_LAND_USE, classify_board, classify_link, looks_like_document)
from .config import SETTINGS
from .http import Fetcher
from .platforms import DocLink, civicplus, civicclerk, granicus
from .util import normalize_url, same_site

log = logging.getLogger("njboards.discover")

ECODE_ID = re.compile(r"https?://(?:www\.)?ecode360\.com/([A-Z]{2}\d{4})\b", re.I)
ECODE_GUID = re.compile(r"https?://(?:www\.)?ecode360\.com/(\d{6,})", re.I)
MUNICODE = re.compile(r"https?://(?:library\.)?municode\.com/nj/[a-z0-9_\-]+", re.I)
GENERALCODE = re.compile(r"https?://(?:www\.)?generalcode\.com/[^\s\"']*", re.I)
CLERKBASE = re.compile(r"https?://[a-z0-9\-]+\.clerkbase\.com[^\s\"']*", re.I)

FOLLOW_WORDS = re.compile(
    r"planning|zoning|adjustment|land\s*use|agenda|minutes|resolution|application|checklist|"
    r"site\s*plan|subdivision|variance|development|board|document|archive|forms?|permits?|"
    r"construction|building|engineer|ordinance|code|meeting|notice|hearing|pending|\b20[12]\d\b", re.I)
SKIP_WORDS = re.compile(
    r"login|logout|signin|calendar\.aspx|/calendar|rss|facebook|twitter|instagram|youtube|mailto:|tel:|javascript:|"
    r"\.(jpg|jpeg|png|gif|svg|css|js|ico|mp4|mp3)(\?|$)|#|translate|sitemap|/search|accessibility|"
    r"notify|alert|subscribe|employment|jobs|payment|pay-|tax|police|fire|library|recreation|senior|"
    r"health|court|election|vital|dog|license|garbage|recycling|news|press|events?/|photo|gallery", re.I)


@dataclass
class Discovery:
    homepage: str
    platform: Optional[str] = None
    boards: list[dict] = field(default_factory=list)           # {kind, name, url}
    docs: list[DocLink] = field(default_factory=list)
    pages_visited: int = 0
    ecode_id: Optional[str] = None
    ecode_url: Optional[str] = None
    code_platform: Optional[str] = None
    errors: list[str] = field(default_factory=list)

    def board_for(self, kind: str) -> Optional[dict]:
        return next((b for b in self.boards if b["kind"] == kind), None)


def _page_title(soup: BeautifulSoup) -> str:
    h = soup.find(["h1", "h2"])
    t = soup.title.get_text(" ", strip=True) if soup.title else ""
    return " | ".join(x for x in (h.get_text(" ", strip=True) if h else "", t) if x)


def _heading_context(a) -> str:
    """Nearest preceding heading + enclosing list item / row / paragraph."""
    parts = []
    parent = a.find_parent(["li", "tr", "p", "td", "div"])
    if parent is not None:
        parts.append(parent.get_text(" ", strip=True)[:200])
    h = a.find_previous(["h1", "h2", "h3", "h4", "h5", "strong", "b"])
    if h is not None:
        parts.append(h.get_text(" ", strip=True)[:120])
    return " | ".join(parts)


def find_code_links(html: str) -> dict:
    out: dict = {}
    m = ECODE_ID.search(html)
    if m:
        out["ecode_id"] = m.group(1).upper()
        out["ecode_url"] = f"https://ecode360.com/{out['ecode_id']}"
        out["code_platform"] = "ecode360"
        return out
    m = ECODE_GUID.search(html)
    if m:
        out["ecode_url"] = m.group(0)
        out["code_platform"] = "ecode360"
        return out
    for pat, name in ((MUNICODE, "municode"), (GENERALCODE, "generalcode"), (CLERKBASE, "clerkbase")):
        m = pat.search(html)
        if m:
            out["ecode_url"] = m.group(0)
            out["code_platform"] = name
            return out
    return out


def discover(fetcher: Fetcher, homepage: str, max_pages: Optional[int] = None,
             manual_boards: Optional[list[dict]] = None) -> Discovery:
    max_pages = max_pages or SETTINGS.max_pages_per_site
    homepage = homepage if homepage.startswith("http") else "https://" + homepage
    disc = Discovery(homepage=homepage)
    seen: set[str] = set()
    queue: deque[tuple[str, int, Optional[str], str]] = deque()  # url, depth, board hint, link text
    queue.append((normalize_url(homepage), 0, None, "home"))
    for b in manual_boards or []:
        queue.appendleft((normalize_url(b["url"]), 1, b["kind"], b.get("name", "")))
        disc.boards.append({"kind": b["kind"], "name": b.get("name") or f"{b['kind'].title()} Board", "url": b["url"], "source": "manual"})
    docs: dict[str, DocLink] = {}
    adapters_run: set[str] = set()

    while queue and disc.pages_visited < max_pages:
        url, depth, hint, ltext = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        r = fetcher.get(url)
        if not r.ok:
            if r.error:
                disc.errors.append(f"{url}: {r.error}")
            continue
        if not r.is_html:
            if looks_like_document(url, r.content_type):
                lc = classify_link(url, ltext, "", hint)
                docs.setdefault(url, DocLink(url=url, title=ltext, doc_type=lc.doc_type, board_kind=lc.board_kind or hint,
                                             meeting_date=lc.meeting_date, year=lc.year))
            continue
        disc.pages_visited += 1
        html = r.text
        soup = BeautifulSoup(html, "lxml")
        title = _page_title(soup)
        page_board = classify_board(title) or classify_board(url) or hint
        if page_board and NON_LAND_USE.search(title) and not classify_board(title):
            page_board = hint
        if depth == 0:
            disc.platform = disc.platform or (
                civicplus.NAME if civicplus.detect(html, url) else civicclerk.NAME if civicclerk.detect(html, url)
                else granicus.NAME if granicus.detect(html, url) else None)
        if not disc.ecode_url:
            for k, v in find_code_links(html).items():
                setattr(disc, k, v)

        # register board pages (one per kind, prefer pages whose title matches)
        if classify_board(title) and not disc.board_for(page_board or ""):
            disc.boards.append({"kind": page_board, "name": title.split("|")[0].strip(), "url": r.final_url, "source": "discovered"})

        # platform adapters
        if civicplus.detect(html, url) and "civicplus" not in adapters_run:
            adapters_run.add("civicplus")
            disc.platform = disc.platform or civicplus.NAME
            for d in civicplus.list_documents(fetcher, r.final_url, html, None):
                docs.setdefault(d.url, d)
        if civicclerk.detect(html, url):
            slug = civicclerk.portal_slug(url) or civicclerk.portal_slug(html)
            if slug and f"civicclerk:{slug}" not in adapters_run:
                adapters_run.add(f"civicclerk:{slug}")
                for d in civicclerk.list_documents(fetcher, url, html, None):
                    docs.setdefault(d.url, d)
        if granicus.detect(html, url) and page_board:
            for d in granicus.list_documents(fetcher, r.final_url, html, page_board):
                docs.setdefault(d.url, d)

        # generic links
        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            if not href or SKIP_WORDS.search(href):
                continue
            absu = urljoin(r.final_url, href)
            if not absu.startswith("http"):
                continue
            text = a.get_text(" ", strip=True)
            ctx = _heading_context(a)
            lc = classify_link(absu, text, ctx, page_board=page_board)
            is_doc = lc.is_document or (lc.doc_type in ("agenda", "minutes", "resolution") and not same_site(absu, homepage))
            land_use = bool(lc.board_kind) or bool(re.search(r"site\s*plan|land\s*use|subdivision|variance", text + " " + ctx, re.I))
            if is_doc:
                if land_use and lc.doc_type != "other" or (page_board and lc.doc_type != "other"):
                    nu = absu.split("#")[0]
                    if nu not in docs:
                        docs[nu] = DocLink(url=nu, title=text or ctx[:100], doc_type=lc.doc_type,
                                           board_kind=lc.board_kind or page_board, meeting_date=lc.meeting_date,
                                           year=lc.year, context=ctx, platform=disc.platform or "generic")
                continue
            # follow?
            if not same_site(absu, homepage) and not (civicclerk.detect("", absu) or granicus.detect("", absu)):
                continue
            blob = text + " " + href + " " + ctx[:80]
            if depth >= 3:
                continue
            if lc.board_kind or (page_board and FOLLOW_WORDS.search(blob)) or (depth == 0 and FOLLOW_WORDS.search(blob) and not NON_LAND_USE.search(text)):
                nu = normalize_url(absu)
                if nu not in seen:
                    # pages linked from a board page inherit its kind unless they name another board
                    queue.append((nu, depth + 1, lc.board_kind or page_board, text))
    disc.docs = list(docs.values())
    return disc
