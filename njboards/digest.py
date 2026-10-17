"""Weekly digest: what each town's boards decided recently and what is on
their agendas for the coming weeks, with the applicant's engineer named per job.

weekly_town_pass() is the light per-town refresh (known board pages, new
agendas / minutes / resolutions only). build_digest() aggregates every town's
parsed documents into one Markdown + JSON digest.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import SETTINGS
from .db import DB, dump_json, load_json, row_to_dict
from .discover import discover
from .extract import extract_all
from .fetch import fetch_all
from .http import Fetcher
from .parse.common import app_numbers, block_lots
from .pipeline import run_parse
from .util import squash

FIRM = r"(?:[A-Z][\w&.'\-]*[ ,]*){1,6}(?:LLC|L\.L\.C\.|Inc\.?|P\.?C\.?|P\.?A\.?|LLP|Associates|Assoc\.?|Group|Engineering|Engineers|Consultants|Consulting|Partners|Design|Co\.|Corp\.?|Surveying|& Associates)"
PREPARED_BY = re.compile(r"(?:plans?|drawings?|survey|report|site\s+plan[s]?|set)[^.\n]{0,80}?\bprepared\s+by\s+(?:[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,3},?\s+(?:P\.?E\.?,?\s*)?(?:of\s+)?)?(" + FIRM + ")", re.I)
ENGINEER_FIELD = re.compile(r"\b(?:applicant'?s?\s+)?engineer(?:\s+of\s+record)?\s*[:\-]\s*([A-Z][^\n;|]{3,100})", re.I)
WEEK_NAMES = re.compile(r"\b(first|second|third|fourth|last|1st|2nd|3rd|4th)\s+(monday|tuesday|wednesday|thursday)\b", re.I)


def _dt(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


# --------------------------------------------------------------- per-town pass
def weekly_town_pass(fetcher: Fetcher, db: DB, slug: str, horizon_days: int = 30, fetch_limit: int = 12) -> dict:
    """Refresh one town cheaply: re-crawl its known board pages (depth-limited),
    download agendas dated within the horizon plus the newest minutes and
    resolutions, extract and parse them."""
    m = db.municipality(slug)
    if not m["website"]:
        return {"slug": slug, "skipped": "no website"}
    mid = m["id"]
    boards = [{"kind": b["kind"], "url": b["url"], "name": b["name"]} for b in db.boards(mid)]
    disc = discover(fetcher, m["website"], max_pages=40 if boards else SETTINGS.max_pages_per_site, manual_boards=boards)
    board_ids = {b["kind"]: db.upsert_board(mid, b["kind"], b["name"], b["url"], platform=disc.platform, source=b.get("source", "discovered")) for b in disc.boards}
    new = 0
    for d in disc.docs:
        if d.year and d.year < SETTINGS.min_year:
            continue
        before = db.conn.execute("SELECT COUNT(*) FROM documents WHERE muni_id=?", (mid,)).fetchone()[0]
        db.add_document(mid, d.url, d.title, d.doc_type, board_ids.get(d.board_kind or ""), d.board_kind, d.meeting_date, d.year)
        new += db.conn.execute("SELECT COUNT(*) FROM documents WHERE muni_id=?", (mid,)).fetchone()[0] - before
    today = date.today()
    horizon = today + timedelta(days=horizon_days)
    lookback = today - timedelta(days=45)
    # choose what to download: agendas in the window, recent minutes/resolutions, recent review letters
    wanted = []
    for d in db.documents(mid, status="discovered"):
        md = _dt(d["meeting_date"])
        if d["doc_type"] == "agenda" and md and today - timedelta(days=7) <= md <= horizon:
            wanted.append(d)
        elif d["doc_type"] in ("minutes", "resolution", "review_letter") and (md is None or md >= lookback):
            wanted.append(d)
    wanted.sort(key=lambda d: (d["doc_type"] != "agenda", d["meeting_date"] or ""), reverse=False)
    stats = {"slug": slug, "new_docs": new, "fetched": 0, "errors": 0}
    for d in wanted[:fetch_limit]:
        from .fetch import fetch_document
        ok = fetch_document(fetcher, db, d, slug)
        stats["fetched" if ok else "errors"] += 1
    stats["extracted"] = extract_all(db, slug, ocr=True)["extracted"]  # OCR when tesseract is installed
    stats["parsed"] = run_parse(db, slug)["parsed"]
    db.set_muni_fields(slug, status="weekly " + today.isoformat())
    return stats


# --------------------------------------------------------------- engineers
def engineer_for(item: dict, texts: list[str]) -> list[str]:
    """Applicant-side engineer (person and/or firm) for an application item."""
    out: list[str] = []
    for p in item.get("professionals", []):
        if p.get("side") == "board":
            continue
        if re.search(r"engineer|surveyor", p.get("role", ""), re.I) or re.search(r"\bP\.?E\b|PLS|L\.S\.", p.get("credentials", "")):
            label = p["name"] + (f", {p['credentials']}" if p.get("credentials") else "") + (f" ({p['firm']})" if p.get("firm") else "")
            if label not in out:
                out.append(label)
    for t in texts:
        for m in PREPARED_BY.finditer(t):
            firm = squash(m.group(1)).rstrip(",.")
            if len(firm) < 80 and firm not in out and not re.search(r"architect|planning|law|esq", firm, re.I):
                out.append(firm)
        for m in ENGINEER_FIELD.finditer(t):
            v = squash(m.group(1)).rstrip(",.")
            if 3 < len(v) < 90 and v not in out and not re.match(r"^(review|report|letter|comments|the|board|township|borough|city)\b", v, re.I):
                out.append(v)
    return out[:3]


# --------------------------------------------------------------- digest
def _load_parsed(slug: str) -> list[dict]:
    d = SETTINGS.parsed_dir / slug
    out = []
    if d.exists():
        for p in d.glob("*.json"):
            try:
                out.append(load_json(p))
            except json.JSONDecodeError:
                pass
    return out


def _app_key(it: dict) -> str:
    if it.get("app_numbers"):
        return it["app_numbers"][0]
    if it.get("block_lots"):
        b = it["block_lots"][0]
        return f"B{b['block']} L{b['lots']}"
    return (it.get("applicant") or it.get("heading") or "")[:50]


def town_digest(db: DB, slug: str, since: date, horizon_days: int = 30) -> Optional[dict]:
    m = row_to_dict(db.municipality(slug))
    parsed = _load_parsed(slug)
    if not parsed:
        return None
    today = date.today()
    horizon = today + timedelta(days=horizon_days)
    # index: application key -> texts that mention it (for engineer lookup)
    mention_texts: dict[str, list[str]] = defaultdict(list)
    for doc in parsed:
        if doc.get("doc_type") == "review_letter":
            key = (doc.get("app_numbers") or [None])[0]
            blob = " ".join(doc.get("documents_reviewed", [])) + " " + doc.get("re", "")
            if key:
                mention_texts[key].append(blob)
            for b in doc.get("block_lots", []):
                mention_texts[f"B{b['block']} L{b['lots']}"].append(blob)
        for it in doc.get("items", []):
            mention_texts[_app_key(it)].append(it.get("text", "")[:3000])
    upcoming: dict[str, dict] = {}
    recent: list[dict] = []
    new_docs = []
    for doc in sorted(parsed, key=lambda d: d.get("meeting_date") or ""):
        md = _dt(doc.get("meeting_date"))
        dt = doc.get("doc_type")
        if dt == "agenda" and md and today - timedelta(days=3) <= md <= horizon:
            for it in doc.get("items", []):
                key = _app_key(it)
                e = upcoming.setdefault(key, {"meeting_date": md.isoformat(), "board": doc.get("board_kind"), "application": key,
                                              "applicant": it.get("applicant"), "block_lots": it.get("block_lots"),
                                              "addresses": it.get("addresses"), "types": it.get("app_types"),
                                              "engineers": engineer_for(it, mention_texts.get(key, []) + [it.get("text", "")])})
                e["meeting_date"] = min(e["meeting_date"], md.isoformat())
        elif dt in ("minutes", "resolution") and md and md >= since - timedelta(days=21):
            if dt == "minutes":
                for it in doc.get("items", []):
                    if not (it.get("decision") or it.get("app_types")):
                        continue
                    recent.append({"meeting_date": md.isoformat(), "board": doc.get("board_kind"), "application": _app_key(it),
                                   "applicant": it.get("applicant"), "types": it.get("app_types"), "decision": it.get("decision"),
                                   "vote": it.get("vote"), "engineers": engineer_for(it, mention_texts.get(_app_key(it), []) + [it.get("text", "")]),
                                   "source": "minutes"})
            else:
                recent.append({"meeting_date": md.isoformat(), "board": doc.get("board_kind"),
                               "application": (doc.get("app_numbers") or [None])[0] or (doc.get("applicant") or doc.get("title") or "")[:50],
                               "applicant": doc.get("applicant"), "types": doc.get("app_types"), "decision": doc.get("decision"),
                               "n_conditions": doc.get("n_conditions"),
                               "engineers": [f"{w['name']}, {w['credentials']}" + (f" ({w['firm']})" if w.get("firm") else "")
                                             for w in doc.get("witnesses", []) if re.search(r"engineer", w.get("role", ""), re.I)][:2],
                               "source": "resolution"})
    # documents that appeared since the last digest
    for d in db.documents(m["id"]):
        if d["discovered_at"] and _dt(d["discovered_at"]) and _dt(d["discovered_at"]) >= since:
            new_docs.append({"type": d["doc_type"], "title": d["title"], "date": d["meeting_date"], "url": d["url"]})
    # next meetings known from agendas even without items
    meetings = sorted({doc["meeting_date"] for doc in parsed if doc.get("doc_type") == "agenda" and _dt(doc.get("meeting_date")) and today <= _dt(doc["meeting_date"]) <= horizon})
    return {
        "slug": slug, "name": m["name"], "county": m["county"], "website": m["website"],
        "upcoming_meetings": meetings, "upcoming": sorted(upcoming.values(), key=lambda r: r["meeting_date"]),
        "recent": sorted(recent, key=lambda r: r["meeting_date"], reverse=True)[:25],
        "new_documents": new_docs[:40], "n_new_documents": len(new_docs),
    }


def build_digest(db: DB, since: Optional[date] = None, horizon_days: int = 30) -> dict:
    since = since or (date.today() - timedelta(days=7))
    towns = []
    for m in db.municipalities():
        td = town_digest(db, m["slug"], since, horizon_days)
        if td:
            towns.append(td)
    towns.sort(key=lambda t: (t["county"] or "", t["name"]))
    return {"generated": datetime.now().isoformat(timespec="minutes"), "since": since.isoformat(), "horizon_days": horizon_days,
            "n_towns_with_data": len(towns), "n_towns_total": len(db.municipalities()),
            "n_upcoming_items": sum(len(t["upcoming"]) for t in towns), "n_recent_decisions": sum(len(t["recent"]) for t in towns),
            "towns": towns}


def _fmt_types(types) -> str:
    return ", ".join(types or []) or "—"


def render_digest(d: dict) -> str:
    L = [f"# NJ Land Use Boards — Weekly Digest ({d['generated'][:10]})\n",
         f"Covers activity since {d['since']} and agendas through the next {d['horizon_days']} days. "
         f"{d['n_towns_with_data']} of {d['n_towns_total']} municipalities have parsed records. "
         f"Upcoming agenda items: {d['n_upcoming_items']} · recent decisions: {d['n_recent_decisions']}.\n",
         "Engineer = the applicant's engineer named in the agenda, minutes, resolution or review letter (blank when not stated).\n"]
    by_county: dict[str, list[dict]] = defaultdict(list)
    for t in d["towns"]:
        by_county[t["county"] or "Unknown"].append(t)
    # statewide headline: biggest upcoming site plans
    big = [(t["name"], u) for t in d["towns"] for u in t["upcoming"] if any("major site plan" in x or "subdivision" in x for x in (u["types"] or []))]
    if big:
        L.append("## Major site plans and subdivisions on upcoming agendas\n")
        L.append("| Date | Town | Board | Application | Applicant | Types | Engineer |")
        L.append("|---|---|---|---|---|---|---|")
        for name, u in sorted(big, key=lambda x: x[1]["meeting_date"])[:60]:
            L.append(f"| {u['meeting_date']} | {name} | {u['board'] or ''} | {u['application']} | {u['applicant'] or ''} | {_fmt_types(u['types'])} | {'; '.join(u['engineers'])} |")
        L.append("")
    for county in sorted(by_county):
        L.append(f"## {county} County\n")
        for t in by_county[county]:
            if not (t["upcoming"] or t["recent"] or t["new_documents"]):
                continue
            L.append(f"### {t['name']}\n")
            if t["upcoming_meetings"]:
                L.append("Upcoming meetings with posted agendas: " + ", ".join(t["upcoming_meetings"]) + "  ")
            if t["upcoming"]:
                L.append("\n**Coming up**\n")
                L.append("| Date | Board | Application | Applicant | Block/Lot | Types | Engineer |")
                L.append("|---|---|---|---|---|---|---|")
                for u in t["upcoming"][:20]:
                    bl = "; ".join(f"B{b['block']} L{b['lots']}" for b in (u["block_lots"] or []))
                    L.append(f"| {u['meeting_date']} | {u['board'] or ''} | {u['application']} | {u['applicant'] or ''} | {bl} | {_fmt_types(u['types'])} | {'; '.join(u['engineers'])} |")
            if t["recent"]:
                L.append("\n**Recent decisions / hearings**\n")
                L.append("| Date | Board | Application | Applicant | Types | Outcome | Engineer |")
                L.append("|---|---|---|---|---|---|---|")
                for r in t["recent"][:15]:
                    outcome = (r.get("decision") or "heard") + (f" ({r['vote']})" if r.get("vote") else "") + (f", {r['n_conditions']} conditions" if r.get("n_conditions") else "")
                    L.append(f"| {r['meeting_date']} | {r['board'] or ''} | {r['application']} | {r['applicant'] or ''} | {_fmt_types(r['types'])} | {outcome} | {'; '.join(r['engineers'])} |")
            if t["new_documents"]:
                L.append(f"\nNew documents since last digest: {t['n_new_documents']} (" +
                         ", ".join(sorted({x['type'] or 'other' for x in t['new_documents']})) + ")")
            L.append("")
    return "\n".join(L)


def write_digest(d: dict) -> Path:
    out_dir = SETTINGS.data_dir / "digests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = d["generated"][:10]
    (out_dir / f"{stamp}.md").write_text(render_digest(d), encoding="utf-8")
    dump_json(out_dir / f"{stamp}.json", d)
    (out_dir / "latest.md").write_text(render_digest(d), encoding="utf-8")
    (out_dir / "state.json").write_text(json.dumps({"last_digest": stamp}), encoding="utf-8")
    return out_dir / f"{stamp}.md"


def last_digest_date() -> Optional[date]:
    p = SETTINGS.data_dir / "digests" / "state.json"
    if p.exists():
        try:
            return _dt(json.loads(p.read_text()).get("last_digest"))
        except (json.JSONDecodeError, ValueError):
            return None
    return None
