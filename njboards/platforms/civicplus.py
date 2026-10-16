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
    # Newer AgendaCenter pages: <h2>Planning Board</h2> followed by javascript:changeYear(2026, 5, 'a0')
    for h in soup.select("h2, h3"):
        name = h.get_text(" ", strip=True)
        kind = classify_board(name)
        if not kind:
            continue
        a = h.find_next("a", href=re.compile(r"changeYear\("))
        if a:
            m = re.search(r"changeYear\(\s*\d{4}\s*,\s*(\d+)", a["href"])
            if m:
                cid = m.group(1)
                cats[cid] = {"id": cid, "name": name, "url": f"{base}/AgendaCenter/{name.replace(' ', '-')}-{cid}", "kind": kind}
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
    """All agenda / minutes / packet files for a category.

    The search endpoint without dates returns only the current year, so we
    query one calendar year at a time back to `min_year`, stopping after
    three consecutive empty years.
    """
    import datetime as _dt
    base = base_of(site_url)
    out: dict[str, DocLink] = {}
    this_year = _dt.date.today().year
    empty = 0
    for y in range(this_year + 1, min_year - 1, -1):
        u = (f"{base}/AgendaCenter/Search/?term=&CIDs={cat['id']},&startDate=01/01/{y}&endDate=12/31/{y}"
             f"&dateRange=&dateSelector=")
        r = fetcher.get(u)
        if not r.ok or not r.is_html:
            empty += 1
            if empty >= 3:
                break
            continue
        before = len(out)
        _collect_files(r.text, base, cat, out)
        empty = empty + 1 if len(out) == before else 0
        if empty >= 3 and y < this_year:
            break
    if not out:  # older AgendaCenter versions ignore dates; fall back to the plain search and category page
        for u in (f"{base}/AgendaCenter/Search/?term=&CIDs={cat['id']},&startDate=&endDate=&dateRange=&dateSelector=", cat["url"]):
            r = fetcher.get(u)
            if r.ok and r.is_html:
                _collect_files(r.text, base, cat, out)
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


def categories_in_html(html: str, base: str) -> list[dict]:
    """Land-use category links found on any page (e.g. a board page linking /AgendaCenter/Planning-Board-5)."""
    out = {}
    for m in re.finditer(r"href=\"(/AgendaCenter/([A-Za-z0-9\-]+?)-(\d+))\"", html):
        name = m.group(2).replace("-", " ")
        kind = classify_board(name)
        if kind:
            out[m.group(3)] = {"id": m.group(3), "name": name, "url": base + m.group(1), "kind": kind}
    return list(out.values())


def list_documents(fetcher, page_url: str, html: str, board_kind: Optional[str]) -> list[DocLink]:
    """Called for any page on a CivicPlus site; returns AgendaCenter files for land-use boards."""
    docs: list[DocLink] = []
    cats = {c["id"]: c for c in agenda_center_categories(fetcher, page_url)}
    for c in categories_in_html(html, base_of(page_url)):
        cats.setdefault(c["id"], c)
    for cat in cats.values():
        if board_kind and cat["kind"] != board_kind and cat["kind"] != "combined":
            continue
        docs.extend(list_category_files(fetcher, page_url, cat, min_year=fetcher.s.min_year))
    return docs
