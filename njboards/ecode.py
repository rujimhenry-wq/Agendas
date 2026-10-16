"""Pull a municipality's zoning / land development chapters from eCode360
(General Code) and index the parts that matter for a site plan application:
submission checklists, waiver provisions, site plan review procedure,
parking, landscaping, lighting, signs, stormwater, bulk schedules, fees.

eCode360 URL shapes (as published):
  https://ecode360.com/<CODEID>              code home / table of contents (e.g. MO0456)
  https://ecode360.com/<guid>                chapter / article / section page (numeric guid)
  https://ecode360.com/print/<CODEID>?guid=<guid>     printable full chapter
  https://ecode360.com/attachment/<CODEID>/...pdf     attachments (checklists, schedules, maps)

The HTML is server-rendered; chapter pages carry section headings in
elements with ids/anchors equal to the section guid. Structure changes
occasionally, so the parser is deliberately loose: it works from headings,
anchors and text rather than specific CSS classes.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .config import SETTINGS
from .db import dump_json
from .http import Fetcher
from .util import clean_text, slugify, squash

log = logging.getLogger("njboards.ecode")

CHAPTER_WANT = re.compile(
    r"zoning|land\s+use|land\s+development|development\s+regulations|site\s+plan|subdivision|"
    r"unified\s+development|planning\s+board|board\s+of\s+adjustment|stormwater|storm\s+water|"
    r"sign(s|age)?\b|parking|landscap|lighting|tree|affordable|fees|escrow|flood|steep\s+slope|"
    r"environmental|soil|design\s+standards|improvements|performance\s+standards|redevelopment", re.I)
CHAPTER_SKIP = re.compile(r"christmas|sales|board\s+of\s+health|health\s+fees|commission\b|committee\b|authority\b|license|peddl|garage\s+sale|yard\s+sale|holiday", re.I)
CHAPTER_PRIORITY = re.compile(r"zoning|land\s+use|land\s+development|development\s+regulations|site\s+plan|subdivision|unified", re.I)

TOPICS: dict[str, re.Pattern] = {
    "checklist": re.compile(r"checklist|submission\s+requirements|application\s+requirements|contents\s+of\s+(site\s+plan|application|plat)|required\s+(information|submissions|documents)|details\s+required|plan\s+details|information\s+required", re.I),
    "completeness_waiver": re.compile(r"completeness|waiver\s+of\s+(submission|checklist|requirement)|waivers?\b|exceptions?\b", re.I),
    "site_plan_procedure": re.compile(r"site\s+plan\s+(review|approval|procedure|application)|preliminary\s+(site\s+plan|approval)|final\s+(site\s+plan|approval)|minor\s+site\s+plan|major\s+site\s+plan|site\s+plan\s+exemption|site\s+plan\s+waiver", re.I),
    "design_standards": re.compile(r"design\s+standards?|improvement\s+standards?|performance\s+standards?|general\s+(design|development)\s+(standards|requirements)|technical\s+standards", re.I),
    "parking": re.compile(r"off-?street\s+parking|parking\s+(requirements|standards|spaces|schedule)|loading", re.I),
    "landscaping": re.compile(r"landscap|buffer|screening|shade\s+tree|tree\s+(removal|replacement|protection)", re.I),
    "lighting": re.compile(r"\blighting|illumination|photometric", re.I),
    "signs": re.compile(r"\bsigns?\b|signage", re.I),
    "stormwater": re.compile(r"storm\s*water|drainage|detention|flood", re.I),
    "bulk_schedule": re.compile(r"schedule\s+of\s+(area|bulk|district|yard|dimensional)|bulk\s+(requirements|standards|schedule|regulations)|area\s+and\s+bulk|dimensional\s+(requirements|standards)|area,?\s+yard\s+and\s+(bulk|building)|zone\s+(district\s+)?(requirements|regulations|standards)", re.I),
    "fees_escrow": re.compile(r"\bfees?\b|escrow|guarantee|bond", re.I),
    "variances": re.compile(r"variance|40:55D-70|hardship|conditional\s+use", re.I),
    "affordable_housing": re.compile(r"affordable\s+housing|inclusionary|COAH|fair\s+share|mandatory\s+set-?aside", re.I),
    "ev_charging": re.compile(r"electric\s+vehicle|\bEV\b|EVSE|make-?ready|charging", re.I),
    "environmental": re.compile(r"environmental\s+impact|wetland|steep\s+slope|soil\s+(erosion|removal)|floodplain|riparian|tree\s+(removal|conservation)", re.I),
    "notice_hearing": re.compile(r"public\s+hearing|notice\s+(of\s+hearing|requirements)|hearing\s+procedure|40:55D-12", re.I),
}


def code_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"ecode360\.com/([A-Z]{2}\d{4})\b", url, re.I)
    return m.group(1).upper() if m else None


def _links(soup: BeautifulSoup, base: str) -> list[tuple[str, str]]:
    out = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        u = urljoin(base, href).split("#")[0]
        if "ecode360.com" in u:
            out.append((u, squash(text)))
    return out


def table_of_contents(fetcher: Fetcher, code_url: str) -> list[dict]:
    """Return [{'title':..., 'url':..., 'guid':..., 'wanted': bool}] for code chapters."""
    r = fetcher.get(code_url, max_age_hours=24 * 30)
    if not r.ok:
        raise RuntimeError(f"eCode360 fetch failed for {code_url}: {r.error or r.status}")
    soup = BeautifulSoup(r.text, "lxml")
    chapters: dict[str, dict] = {}
    for u, text in _links(soup, r.final_url):
        m = re.search(r"ecode360\.com/(\d{5,})", u)
        if not m:
            continue
        if not re.match(r"^(Chapter|Ch\.|Part|Article|Title|§|Appendix|A\d+|\d+[A-Z]?\b)", text, re.I) and not CHAPTER_WANT.search(text):
            continue
        guid = m.group(1)
        if guid in chapters:
            continue
        chapters[guid] = {"title": text[:160], "url": u, "guid": guid, "wanted": bool(CHAPTER_WANT.search(text)) and not CHAPTER_SKIP.search(text),
                          "priority": bool(CHAPTER_PRIORITY.search(text))}
    # If the TOC is collapsed and nothing is marked wanted, follow 'Part'/'Title' links one level
    if chapters and not any(c["wanted"] for c in chapters.values()):
        for c in list(chapters.values())[:40]:
            r2 = fetcher.get(c["url"], max_age_hours=24 * 30)
            if not r2.ok:
                continue
            for u, text in _links(BeautifulSoup(r2.text, "lxml"), r2.final_url):
                m = re.search(r"ecode360\.com/(\d{5,})", u)
                if m and CHAPTER_WANT.search(text) and m.group(1) not in chapters:
                    chapters[m.group(1)] = {"title": text[:160], "url": u, "guid": m.group(1), "wanted": True,
                                            "priority": bool(CHAPTER_PRIORITY.search(text))}
    return list(chapters.values())


def _page_sections_with_text(html: str) -> int:
    soup = BeautifulSoup(html, "lxml")
    return sum(1 for sec in _split_sections(soup, "x") if len(sec["text"]) > 300 and not COOKIE_HEADING.search(sec["heading"]))


def _chapter_html(fetcher: Fetcher, code_id: Optional[str], chapter: dict) -> tuple[list[str], str, str]:
    """Return (html_parts, final_url, mode).

    eCode360's robots.txt disallows its print view for bots, so the normal
    route is the chapter page, which is only a table of contents: section
    bodies are rendered on the article pages (one page per article, all of
    its sections) or, for chapters without articles, on section pages. The
    print view is used only where robots.txt permits it."""
    if code_id:
        pu = f"https://ecode360.com/print/{code_id}?guid={chapter['guid']}"
        if fetcher.allowed(pu):
            r = fetcher.get(pu, max_age_hours=24 * 30)
            if r.ok and r.is_html and len(r.content) > 200 and re.search(r"<h[1-6]|sectionTitle", r.text):
                return [r.text], r.final_url, "print"
    r = fetcher.get(chapter["url"], max_age_hours=24 * 30)
    if not (r.ok and r.is_html):
        return [], chapter["url"], "none"
    html = r.text
    soup = BeautifulSoup(html, "lxml")
    articles, sections_links = [], []
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        m = re.search(r"(?:ecode360\.com)?/?(\d{6,})(?:[#?]|$)", href)
        text = squash(a.get_text(" ", strip=True))
        if not m or m.group(1) in seen or m.group(1) == chapter["guid"]:
            continue
        if re.match(r"^(Article|Part|Division)\b", text, re.I):
            seen.add(m.group(1)); articles.append(m.group(1))
        elif re.match(r"^§", text):
            seen.add(m.group(1)); sections_links.append(m.group(1))
    parts: list[str] = []
    for guid in articles[:60]:
        r2 = fetcher.get(f"https://ecode360.com/{guid}", max_age_hours=24 * 30)
        if r2.ok and r2.is_html and _page_sections_with_text(r2.text):
            parts.append(r2.text)
    if parts:
        return parts, r.final_url, "articles"
    # no articles: a section page renders its siblings too; fetch more only if it did not
    covered = 0
    for guid in sections_links[:120]:
        if covered >= len(sections_links):
            break
        r2 = fetcher.get(f"https://ecode360.com/{guid}", max_age_hours=24 * 30)
        if r2.ok and r2.is_html:
            n = _page_sections_with_text(r2.text)
            if n:
                parts.append(r2.text)
                covered += max(n, 1)
    if parts:
        return parts, r.final_url, "sections"
    return [html], r.final_url, "page"


def fetch_chapter(fetcher: Fetcher, code_id: Optional[str], chapter: dict) -> dict:
    """Fetch a chapter and split it into sections."""
    parts, final, mode = _chapter_html(fetcher, code_id, chapter)
    if not parts:
        return {**chapter, "sections": [], "attachments": [], "error": "fetch failed"}
    attachments: list[dict] = []
    seen_att: set[str] = set()
    sections: list[dict] = []
    for html in parts:
        soup = BeautifulSoup(html, "lxml")
        for t in soup(["script", "style"]):  # eCode360 puts section titles inside <header> elements
            t.decompose()
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if ("/attachment/" in href or href.lower().endswith(".pdf")) and href not in seen_att:
                seen_att.add(href)
                attachments.append({"title": squash(a.get_text(" ", strip=True))[:160], "url": urljoin(final, href)})
        sections.extend(_split_sections(soup, chapter["title"]))
    seen, out = set(), []
    for s in sections:
        key = (s["heading"], len(s["text"]) // 50)  # section pages render their neighbours too
        if key in seen or COOKIE_HEADING.search(s["heading"]) and len(s["text"]) < 5000:
            continue
        seen.add(key)
        s["text"] = clean_text(s["text"])
        s["topics"] = [t for t, p in TOPICS.items() if p.search(s["heading"]) or len(p.findall(s["text"])) >= 3]
        s["chars"] = len(s["text"])
        out.append(s)
    return {**chapter, "final_url": final, "mode": mode, "sections": out, "attachments": attachments}


COOKIE_HEADING = re.compile(r"cookie|privacy|consent|preferences", re.I)
_TITLE_CLASS = re.compile(r"(section|chapter|article|part|schedule|attachment|title)Title|\btitle\b", re.I)
_HEAD_TEXT = re.compile(r"^(§\s*[\dA-Za-z][\d\-.:A-Za-z]*|Article\s+[\dIVXLC]+|ARTICLE\s+[\dIVXLC]+|Chapter\s+\d+|CHAPTER\s+\d+|Part\s+[\dIVXLC]+|Schedule\s+[A-Z\d]|Attachment\s+\d)")


def _split_sections(soup: BeautifulSoup, fallback_title: str) -> list[dict]:
    """Walk the document in order; every heading (h1-h6, or an element whose
    class marks it as a title, or a short block starting with '§ 12-3') opens
    a new section and all following text belongs to it. Walking the strings
    in document order avoids the duplication that nested containers cause."""
    from bs4 import NavigableString, Tag
    body = soup.body or soup
    sections: list[dict] = []
    cur: Optional[dict] = None
    heading_tag: Optional[Tag] = None
    for node in body.descendants:
        if isinstance(node, Tag):
            if node.name in ("script", "style"):
                continue
            classes = " ".join(node.get("class") or [])
            is_title = node.name in ("h1", "h2", "h3", "h4", "h5", "h6") or bool(_TITLE_CLASS.search(classes))
            if not is_title:
                continue
            if heading_tag is not None and heading_tag in node.parents:
                continue  # nested inside the heading we just opened (e.g. <a class="titleLink">)
            txt = squash(node.get_text(" ", strip=True))
            if not txt or len(txt) > 220:
                continue
            if not (node.name.startswith("h") or _HEAD_TEXT.match(txt)):
                continue
            cur = {"heading": txt, "anchor": node.get("id") or "", "text": ""}
            sections.append(cur)
            heading_tag = node
        elif isinstance(node, NavigableString):
            if node.parent is None or node.parent.name in ("script", "style"):
                continue
            if heading_tag is not None and heading_tag in node.parents:
                continue
            t = str(node)
            if not t.strip():
                continue
            if cur is None:
                cur = {"heading": fallback_title, "anchor": "", "text": ""}
                sections.append(cur)
            if len(cur["text"]) < 200000:
                block = node.parent.name in ("p", "div", "li", "td", "th", "tr", "br")
                cur["text"] += t.strip() + ("\n" if block else " ")
    return sections


CHECKLIST_HEADING = re.compile(r"checklist|plan\s+details|required\s+submissions?|submission\s+requirements|application\s+requirements|contents\s+of\s+(the\s+)?(site\s+plan|application|plat|preliminary|final)|details\s+required|information\s+(to\s+be\s+)?(required|shown|submitted)|required\s+(information|data|documents)|application\s+for\s+development|site\s+plan\s+(details|requirements|contents)|plat\s+(details|requirements)", re.I)


def _extract_checklist_items(sections: list[dict]) -> list[dict]:
    from .parse.checklist import parse_checklist
    items = []
    for s in sections:
        if CHECKLIST_HEADING.search(s["heading"]):
            parsed = parse_checklist(s["text"], title=s["heading"] + " " + s["text"][:300])
            for it in parsed["items"]:
                it["section"] = s["heading"]
                items.append(it)
    return items


def _waiver_provisions(sections: list[dict]) -> list[dict]:
    from .util import sentences
    out = []
    for s in sections:
        for sent in sentences(s["text"]):
            if re.search(r"\bwaiv(e|er|ed)\b", sent, re.I) and re.search(r"checklist|submission|requirement|completeness|board\s+may|shall\s+not\s+be\s+waived|cannot\s+be\s+waived", sent, re.I):
                out.append({"section": s["heading"], "text": squash(sent)[:500]})
    return out[:60]


ATTACHMENT_WANT = re.compile(r"checklist|application\s+requirements|submission|schedule\s+of\s+(area|bulk|zone|district|space|dimensional|yard)|bulk\s+(table|schedule|requirements)|area\s+and\s+bulk|fee\s+schedule|design\s+guidelines|parking\s+(schedule|standards)|sign\s+(matrix|schedule)", re.I)


def fetch_attachments(fetcher: Fetcher, out_dir, attachments: list[dict], max_n: int = 12) -> list[dict]:
    """Download checklist / bulk-schedule attachments, extract their text and
    parse checklist-like ones into items. Returns the enriched attachment list."""
    from .extract import extract_file
    from .parse.checklist import parse_checklist
    att_dir = out_dir / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for a in attachments:
        if not ATTACHMENT_WANT.search(a.get("title", "") + " " + a.get("url", "")) or not re.search(r"\.pdf(\?|$)", a["url"], re.I):
            continue
        if n >= max_n:
            break
        n += 1
        if not fetcher.allowed(a["url"]):
            a["error"] = "not fetched: robots.txt disallows this path for bots; download it by hand from the link"
            continue
        r = fetcher.get(a["url"])
        if not r.ok or not r.content.startswith(b"%PDF"):
            a["error"] = r.error or f"HTTP {r.status}"
            continue
        path = att_dir / (slugify(a["title"] or a["url"].rsplit("/", 1)[-1])[:80] + ".pdf")
        path.write_bytes(r.content)
        info = extract_file(path, ocr=True)
        (path.with_suffix(".txt")).write_text(info["text"], encoding="utf-8")
        a["local_path"] = str(path)
        a["chars"] = info["chars"]
        if re.search(r"checklist|application\s+requirements|submission", a.get("title", ""), re.I):
            parsed = parse_checklist(info["text"], title=a["title"])
            a["checklist_items"] = parsed["items"]
            a["checklists"] = parsed["checklists"]
    return attachments


def fetch_code(fetcher: Fetcher, muni_slug: str, code_url: str, max_chapters: int = 25) -> dict:
    code_id = code_id_from_url(code_url)
    if not code_id:
        from .discover import resolve_ecode_id
        code_id = resolve_ecode_id(fetcher, code_url)
        if code_id:
            code_url = f"https://ecode360.com/{code_id}"
    toc = table_of_contents(fetcher, code_url)
    wanted = [c for c in toc if c["wanted"]]
    wanted.sort(key=lambda c: (not c["priority"], c["title"]))
    chapters = [fetch_chapter(fetcher, code_id, c) for c in wanted[:max_chapters]]
    all_sections = [dict(s, chapter=c["title"], chapter_url=c["url"]) for c in chapters for s in c["sections"]]
    by_topic: dict[str, list[dict]] = {}
    for s in all_sections:
        for t in s["topics"]:
            by_topic.setdefault(t, []).append({"chapter": s["chapter"], "heading": s["heading"], "chars": s["chars"],
                                               "url": s["chapter_url"] + ("#" + s["anchor"] if s["anchor"] else "")})
    out_dir = SETTINGS.code_dir / muni_slug
    attachments = fetch_attachments(fetcher, out_dir, [a for c in chapters for a in c["attachments"]])
    checklist_items = _extract_checklist_items(all_sections)
    for a in attachments:
        for it in a.get("checklist_items", []):
            checklist_items.append(dict(it, section=a["title"], source="attachment"))
    result = {
        "code_url": code_url, "code_id": code_id, "n_chapters_total": len(toc), "n_chapters_fetched": len(chapters),
        "chapters": [{k: v for k, v in c.items() if k != "sections"} | {"n_sections": len(c["sections"])} for c in chapters],
        "sections_by_topic": by_topic,
        "checklist_items": checklist_items,
        "waiver_provisions": _waiver_provisions(all_sections),
        "attachments": attachments,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in chapters:
        (out_dir / f"{slugify(c['title'])[:80]}.txt").write_text(
            "\n\n".join(f"## {s['heading']}\n{s['text']}" for s in c["sections"]), encoding="utf-8")
    dump_json(out_dir / "code_index.json", result)
    return result
