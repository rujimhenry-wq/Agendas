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
        chapters[guid] = {"title": text[:160], "url": u, "guid": guid, "wanted": bool(CHAPTER_WANT.search(text)),
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


def fetch_chapter(fetcher: Fetcher, code_id: Optional[str], chapter: dict) -> dict:
    """Fetch a chapter (printable view when possible) and split into sections."""
    urls = []
    if code_id:
        urls.append(f"https://ecode360.com/print/{code_id}?guid={chapter['guid']}")
    urls.append(chapter["url"])
    html, final = "", chapter["url"]
    for u in urls:
        r = fetcher.get(u, max_age_hours=24 * 30)
        if r.ok and r.is_html and len(r.content) > 200:
            html, final = r.text, r.final_url
            break
    if not html:
        return {**chapter, "sections": [], "attachments": [], "error": "fetch failed"}
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer"]):
        t.decompose()
    attachments = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/attachment/" in href or href.lower().endswith(".pdf"):
            attachments.append({"title": squash(a.get_text(" ", strip=True))[:160], "url": urljoin(final, href)})
    # Split by headings. eCode360 marks sections with headings whose text begins with '§'.
    sections: list[dict] = []
    cur: Optional[dict] = None
    body = soup.body or soup
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "p", "li", "td", "div", "span"]):
        if el.name in ("div", "span") and el.find(["h1", "h2", "h3", "h4", "h5", "p", "li"]):
            continue  # container; its children will be visited
        txt = squash(el.get_text(" ", strip=True))
        if not txt:
            continue
        is_head = el.name.startswith("h") or re.match(r"^§\s*[\d\-.:A-Za-z]+\b", txt) or re.match(r"^(Article|ARTICLE|Chapter|CHAPTER)\s+[\dIVXLC]+", txt)
        if is_head and len(txt) < 220:
            cur = {"heading": txt, "anchor": el.get("id") or (el.find("a") or {}).get("name") or "", "text": ""}
            sections.append(cur)
        elif cur is not None and len(cur["text"]) < 60000:
            cur["text"] += txt + "\n"
        elif cur is None:
            cur = {"heading": chapter["title"], "anchor": "", "text": txt + "\n"}
            sections.append(cur)
    # dedupe repeated text (print view nests headings)
    seen, out = set(), []
    for s in sections:
        key = (s["heading"], s["text"][:200])
        if key in seen:
            continue
        seen.add(key)
        s["text"] = clean_text(s["text"])
        s["topics"] = [t for t, p in TOPICS.items() if p.search(s["heading"]) or len(p.findall(s["text"])) >= 3]
        s["chars"] = len(s["text"])
        out.append(s)
    return {**chapter, "final_url": final, "sections": out, "attachments": attachments}


def _extract_checklist_items(sections: list[dict]) -> list[dict]:
    from .parse.checklist import parse_checklist
    items = []
    for s in sections:
        if "checklist" in s["topics"] or re.search(r"checklist", s["heading"], re.I):
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


def fetch_code(fetcher: Fetcher, muni_slug: str, code_url: str, max_chapters: int = 25) -> dict:
    code_id = code_id_from_url(code_url)
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
    result = {
        "code_url": code_url, "code_id": code_id, "n_chapters_total": len(toc), "n_chapters_fetched": len(chapters),
        "chapters": [{k: v for k, v in c.items() if k != "sections"} | {"n_sections": len(c["sections"])} for c in chapters],
        "sections_by_topic": by_topic,
        "checklist_items": _extract_checklist_items(all_sections),
        "waiver_provisions": _waiver_provisions(all_sections),
        "attachments": [a for c in chapters for a in c["attachments"]],
    }
    out_dir = SETTINGS.code_dir / muni_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in chapters:
        (out_dir / f"{slugify(c['title'])[:80]}.txt").write_text(
            "\n\n".join(f"## {s['heading']}\n{s['text']}" for s in c["sections"]), encoding="utf-8")
    dump_json(out_dir / "code_index.json", result)
    return result
