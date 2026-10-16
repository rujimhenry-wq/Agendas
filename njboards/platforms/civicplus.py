"""CivicPlus (CivicEngage) sites: /AgendaCenter and /DocumentCenter.

AgendaCenter exposes a category id per board and a search endpoint that
returns every agenda/minutes file for a category. File URLs embed the
meeting date: /AgendaCenter/ViewFile/Agenda/_09142024-1234
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..classify import classify_board, classify_link
from . import DocLink

NAME = "civicplus"

_CAT = re.compile(r"/AgendaCenter/([A-Za-z0-9\-]+?)-(\d+)$")


def detect(html: str, url: str) -> bool:
    return "/AgendaCenter" in html or "civicplus" in html.lower() or "/DocumentCenter/" in html


def base_of(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def agenda_center_categories(fetcher, site_url: str) -> list[dict]:
    """Return [{'id': '12', 'name': 'Planning Board', 'url': ..., 'kind': 'planning'}] for land-use boards."""
    base = base_of(site_url)
    r = fetcher.get(base + "/AgendaCenter")
    if not r.ok or not r.is_html:
        return []
    soup = BeautifulSoup(r.text, "lxml")
    cats: dict[str, dict] = {}
    for a in soup.select("a[href*='/AgendaCenter/']"):
        href = a.get("href", "")
        m = _CAT.search(href.split("?")[0])
        if not m:
            continue
        name = a.get_text(" ", strip=True) or m.group(1).replace("-", " ")
        kind = classify_board(name)
        if not kind:
            continue
        cid = m.group(2)
        cats.setdefault(cid, {"id": cid, "name": name, "url": urljoin(base, href), "kind": kind})
    # Headings (h2) sometimes carry the category name while links say "View All"
    for h in soup.select("h2, h3"):
        name = h.get_text(" ", strip=True)
        kind = classify_board(name)
        if not kind:
            continue
        a = h.find_next("a", href=_CAT)
        if a:
            m = _CAT.search(a["href"].split("?")[0])
            if m:
                cats.setdefault(m.group(2), {"id": m.group(2), "name": name, "url": urljoin(base, a["href"]), "kind": kind})
                cats[m.group(2)]["name"] = name
                cats[m.group(2)]["kind"] = kind
    return list(cats.values())


def list_category_files(fetcher, site_url: str, cat: dict, min_year: int = 2000) -> list[DocLink]:
    base = base_of(site_url)
    out: dict[str, DocLink] = {}
    urls = [
        f"{base}/AgendaCenter/Search/?term=&CIDs={cat['id']},&startDate=&endDate=&dateRange=&dateSelector=",
        cat["url"],
    ]
    # category pages are paginated by year via ?year=YYYY on some versions
    for y in range(2035, min_year - 1, -1):
        urls.append(f"{cat['url']}?year={y}")
    seen_pages = set()
    empty_years = 0
    for u in urls:
        if u in seen_pages:
            continue
        seen_pages.add(u)
        r = fetcher.get(u)
        if not r.ok or not r.is_html:
            continue
        before = len(out)
        _collect_files(r.text, base, cat, out)
        if "?year=" in u:
            empty_years = empty_years + 1 if len(out) == before else 0
            if empty_years >= 4:
                break
    return [d for d in out.values() if (d.year or 9999) >= min_year]


def _collect_files(html: str, base: str, cat: dict, out: dict[str, DocLink]) -> None:
    soup = BeautifulSoup(html, "lxml")
    for a in soup.select("a[href*='/AgendaCenter/ViewFile/'], a[href*='/AgendaCenter/PreviousVersions/'], a[href*='/DocumentCenter/View/']"):
        href = a.get("href", "")
        url = urljoin(base, href)
        text = a.get_text(" ", strip=True)
        row = a.find_parent("tr") or a.find_parent("li") or a.find_parent("div")
        ctx = row.get_text(" ", strip=True)[:300] if row else ""
        lc = classify_link(url, text, ctx, page_board=cat["kind"])
        kind = "agenda" if "/ViewFile/Agenda/" in url else "minutes" if "/ViewFile/Minutes/" in url else lc.doc_type
        if "/ViewFile/Item/" in url:
            kind = "site_plan" if lc.doc_type in ("other", "site_plan") else lc.doc_type
        title = text or ctx[:120] or url.rsplit("/", 1)[-1]
        if url not in out:
            out[url] = DocLink(url=url, title=title, doc_type=kind, board_kind=cat["kind"],
                               meeting_date=lc.meeting_date, year=lc.year, context=ctx, platform=NAME)


def list_documents(fetcher, page_url: str, html: str, board_kind: Optional[str]) -> list[DocLink]:
    """Called for any page on a CivicPlus site; returns AgendaCenter files for land-use boards."""
    docs: list[DocLink] = []
    for cat in agenda_center_categories(fetcher, page_url):
        if board_kind and cat["kind"] != board_kind and cat["kind"] != "combined":
            continue
        docs.extend(list_category_files(fetcher, page_url, cat, min_year=fetcher.s.min_year))
    return docs
