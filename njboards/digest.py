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
from .util import sentences, squash

FIRM = r"(?:[A-Z][\w&.'\-]*[ ,]*){1,6}(?:LLC|L\.L\.C\.|Inc\.?|P\.?C\.?|P\.?A\.?|LLP|Associates|Assoc\.?|Group|Engineering|Engineers|Consultants|Consulting|Partners|Design|Co\.|Corp\.?|Surveying|& Associates)"
PREPARED_BY = re.compile(r"\bprepared\s+by\s+(?:[A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,3},?\s+(?:(?:(?:P\.?E\.?|P\.?L\.?S\.?|L\.?S\.?|P\.?P\.?|AIA|R\.?A\.?)[.,]?\s*)+(?:of\s+)?|of\s+))?([A-Z][\w&.'\-]*(?:[ ,]+[A-Z][\w&.'\-]*){0,6}[ ,]*(?:LLC|L\.L\.C\.|Inc\.?|P\.?C\.?|P\.?A\.?|LLP|Associates|Assoc\.?|Group|Engineering|Engineers|Consultants|Consulting|Partners|Design|Co\.|Corp\.?|Surveying|Surveys|Surveyors))", re.I)
ENGINEER_FIELD = re.compile(r"\b(?:applicant'?s?\s+)?engineer(?:\s+of\s+record)?\s*[:\-]\s*([A-Z][^\n;|]{3,100})", re.I)
WEEK_NAMES = re.compile(r"\b(first|second|third|fourth|last|1st|2nd|3rd|4th)\s+(monday|tuesday|wednesday|thursday)\b", re.I)


def _dt(s: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except ValueError:
        return None


# --------------------------------------------------------------- per-town pass
def weekly_town_pass(fetcher: Fetcher, db: DB, slug: str, horizon_days: int = 30, fetch_limit: int = 12,
                     crawl_pages: int = 25, ocr: bool = False) -> dict:
    """Refresh one town cheaply: re-crawl its known board pages (depth-limited),
    download agendas dated within the horizon plus the newest minutes and
    resolutions, extract and parse them."""
    m = db.municipality(slug)
    if not m["website"]:
        return {"slug": slug, "skipped": "no website"}
    mid = m["id"]
    boards = [{"kind": b["kind"], "url": b["url"], "name": b["name"]} for b in db.boards(mid)]
    disc = discover(fetcher, m["website"], max_pages=crawl_pages if boards else SETTINGS.max_pages_per_site, manual_boards=boards)
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
        elif d["doc_type"] in ("application", "site_plan") and md and today - timedelta(days=14) <= md <= horizon:
            wanted.append(d)
    wanted.sort(key=lambda d: (d["doc_type"] != "agenda", d["meeting_date"] or ""), reverse=False)
    stats = {"slug": slug, "new_docs": new, "fetched": 0, "errors": 0}
    for d in wanted[:fetch_limit]:
        from .fetch import fetch_document
        ok = fetch_document(fetcher, db, d, slug)
        stats["fetched" if ok else "errors"] += 1
    stats["extracted"] = extract_all(db, slug, ocr=ocr)["extracted"]
    stats["parsed"] = run_parse(db, slug)["parsed"]
    db.set_muni_fields(slug, status="weekly " + today.isoformat())
    return stats


# --------------------------------------------------------------- what is proposed
PROPOSAL_FIELD = re.compile(r"\b(?:proposal|proposed\s+(?:use|development|project|improvements?)|description(?:\s+of\s+(?:project|application|proposal))?|nature\s+of\s+(?:application|request)|project|relief\s+(?:sought|requested)|purpose)\s*[:\-]\s*([^\n]{8,240})", re.I)
PROPOSAL_WORDS = re.compile(r"\b(construct\w*|propos\w*|build\w*|addition|convert\w*|demoli\w*|erect\w*|install\w*|renovat\w*|expan\w*|new\s+(?:single|two|multi|mixed|commercial|residential|dwelling|home|house|building|warehouse|retail|office|restaurant)|"
                            r"dwelling|units?\b|apartments?|townho\w+|condominium|warehouse|distribution|self[-\s]storage|retail|restaurant|office|medical|daycare|day\s+care|school|church|house\s+of\s+worship|gas\s+station|car\s+wash|hotel|cannabis|dispensary|"
                            r"parking\s+(?:lot|garage|deck)|solar|cell\s+tower|wireless|drive[-\s]thru|subdivide|subdivision\s+of|square\s+f|sq\.?\s*ft|\bsf\b|stor(?:y|ies)\b|bedroom|garage|deck|pool|fence|shed|addition)", re.I)
_PROPOSAL_SKIP = re.compile(r"^(?:block|lot|applicant|owner|attorney|engineer|planner|zone|zoning|address|location|premises|application|carried|continued|resolution|memorializ|minutes|motion|roll|present|absent|adjourn|page\s+no|file\s+[A-Z]{1,4}-?\d)\b", re.I)


def describe(it: dict, max_len: int = 170) -> str:
    """A short description of what the applicant proposes, from the item text."""
    fields = it.get("fields") or {}
    for key in ("proposal", "description", "project", "relief", "type"):
        for fk, fv in fields.items():
            if fk.startswith(key) and fv and len(fv) >= 8:
                return squash(fv)[:max_len].rstrip(" ,;")
    text = it.get("text") or ""
    m = PROPOSAL_FIELD.search(text)
    if m:
        return squash(m.group(1))[:max_len].rstrip(" ,;")
    best, best_score = "", 0
    for sent in sentences(text[:4000]):
        s_ = squash(sent)
        if len(s_) < 15 or _PROPOSAL_SKIP.match(s_):
            continue
        hits = len(PROPOSAL_WORDS.findall(s_))
        # prefer sentences that say what is being built, penalise boilerplate
        score = hits - (2 if re.search(r"hereby|notice|pursuant|N\.J\.S\.A|public\s+hearing|opened\s+to\s+the\s+public", s_, re.I) else 0)
        if score > best_score:
            best, best_score = s_, score
    if best_score <= 0:
        return ""
    m = re.search(r"\b(?:the\s+)?applicants?\s+(?:is\s+|are\s+)?(?:seeks|seeking|requests?|requesting|proposes|proposing|has\s+applied|wishes|intends)\s+(?:approval\s+)?(?:for\s+|to\s+)?(.+)$", best, re.I) \
        or re.search(r"\b(?:for|to)\s+((?:the\s+)?(?:construct\w*|build\w*|erect\w*|install\w*|convert\w*|demoli\w*|renovat\w*|expan\w*|subdivide|permit|allow|operate|establish|create|develop|add)\b.+)$", best, re.I) \
        or re.search(r"\b((?:preliminary|final|minor|major|amended)\s+(?:and\s+final\s+)?(?:major\s+|minor\s+)?(?:site\s+plan|subdivision)\b.+)$", best, re.I)
    if m:
        best = m.group(1)
    best = re.sub(r"\s+(?:on\s+)?(?:the\s+)?(?:property|premises)\s+(?:known\s+as|located\s+at)\s.*$", "", best, flags=re.I)
    return best[:max_len].rstrip(" ,;.") 


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
            if len(firm) < 80 and firm not in out and not re.search(r"architect|planning|\blaw\b|esq|attorney|counsel|LLP$", firm, re.I):
                out.append(firm)
        for m in ENGINEER_FIELD.finditer(t):
            v = squash(m.group(1)).rstrip(",.")
            if 3 < len(v) < 90 and v not in out and not re.match(r"^(review|report|letter|comments|the|board|township|borough|city)\b", v, re.I):
                out.append(v)
    return out[:3]


_PERSON = r"([A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+(?:[A-Z][a-z]+|O'[A-Z][a-z]+|Mc[A-Z][a-z]+|De[A-Z][a-z]+)){1,2}(?:,?\s+(?:Jr|Sr|II|III)\.?)?)"
_FIRM = r"((?:[A-Z][\w&.'\-]*[ ,]*){1,6}(?:LLC|LLP|L\.L\.C\.|P\.?C\.?|P\.?A\.?|Esqs?\.?|Law\s+(?:Group|Firm|Offices?)|Associates|Engineering|Engineers|Consultants|Group|Inc\.?))"
TEAM_FIELDS = {
    "attorney": [re.compile(r"\b(?:attorney|counsel)\s*[:\-]\s*" + _PERSON, re.I * 0),
                 re.compile(r"\brepresented\s+by\s+" + _PERSON + r"(?:,\s*Esq\.?)?"),
                 re.compile(_PERSON + r",\s*Esq(?:uire)?\.?(?:,?\s+(?:of|from)\s+" + _FIRM + r")?")],
    "planner": [re.compile(r"\b(?:planner|planning\s+consultant)\s*[:\-]\s*" + _PERSON),
                re.compile(_PERSON + r",\s*(?:P\.?P\.?|AICP)\b")],
    "traffic engineer": [re.compile(r"\btraffic\s+(?:engineer|consultant|expert)\s*[:\-]?\s*" + _PERSON),
                         re.compile(_PERSON + r",\s*(?:P\.?E\.?,?\s*)?PTOE\b")],
    "architect": [re.compile(r"\barchitect\s*[:\-]\s*" + _PERSON),
                  re.compile(_PERSON + r",\s*(?:AIA|R\.?A\.?)\b")],
}
TEAM_LABELS = {"engineer": "Eng", "attorney": "Atty", "planner": "Planner", "traffic engineer": "Traffic", "architect": "Arch"}
_ROLE_FAMILY = {"engineer": "engineer", "civil engineer": "engineer", "professional engineer": "engineer", "municipal engineer": "engineer",
                "surveyor": "engineer", "traffic engineer": "traffic engineer", "attorney": "attorney", "counsel": "attorney", "esquire": "attorney",
                "planner": "planner", "professional planner": "planner", "architect": "architect", "landscape architect": "architect"}


def team_for(it: dict, texts: list[str]) -> dict[str, list[str]]:
    """The applicant's professionals on this job, by role: engineer, attorney, planner, traffic engineer, architect."""
    team: dict[str, list[str]] = {}

    def add(role: str, label: str) -> None:
        label = squash(label).strip(" ,.;")
        if len(label) < 4 or label.lower().startswith(("the ", "board ", "township ", "borough ", "city ")):
            return
        team.setdefault(role, [])
        if label not in team[role] and len(team[role]) < 2:
            team[role].append(label)

    for p in it.get("professionals", []):
        if p.get("side") == "board":
            continue
        fam = _ROLE_FAMILY.get((p.get("role") or "").lower())
        if not fam:
            from .parse.professionals import role_from_creds
            fam = _ROLE_FAMILY.get(role_from_creds(p.get("credentials") or ""))
        if not fam:
            continue
        add(fam, p["name"] + (f", {p['credentials']}" if p.get("credentials") else "") + (f" ({p['firm']})" if p.get("firm") else ""))
    for e in engineer_for(it, texts):
        add("engineer", e)
    from .parse.professionals import NON_NAME_WORDS
    for t in texts:
        for role, pats in TEAM_FIELDS.items():
            for pat in pats:
                for m in pat.finditer(t):
                    name = m.group(1)
                    if any(tok.lower().strip(".,") in NON_NAME_WORDS for tok in name.split()):
                        continue
                    firm = m.group(2) if m.lastindex and m.lastindex >= 2 and m.group(2) else ""
                    add(role, name + (f" ({squash(firm)})" if firm else ""))
    return team


def fmt_team(team: dict[str, list[str]]) -> str:
    return "; ".join(f"{TEAM_LABELS[r]}: {', '.join(v)}" for r, v in team.items() if v and r in TEAM_LABELS)


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


HEADING_ROW = re.compile(r"^\s*(?:[IVX]+\.|[A-Z]\.|\d+\.)?\s*(?:(?:applications?|public\s+hearings?|new\s+business|old\s+business|resolutions?|minutes|completeness|memorializ\w*|discussion|adjournment|roll\s+call|open\s+(?:to\s+the\s+)?public|correspondence|unfinished\s+business|continued(?:\s+applications?)?|carried|other\s+business|agenda|work\s*session|pending\s+applications?|hearings?)\s*[/&,]?\s*)+:?\s*$", re.I)
JUNK_APPLICANT = re.compile(r"^\s*(\(?[a-z]\)?|application\s+documents?|documents?|app\.?|continued|carried|hearing|variance|site\s+plan|the|re|cont'?d?)\s*[:.]?\s*$", re.I)


def _clean_applicant(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    name = re.sub(r"\s*\((?:application\s+)?documents?\)\s*", " ", name, flags=re.I)
    # cut trailing status text: "TESLA, INC. 1199 AMBOY AVE IS CARRIED TO DECEMBER 14"
    name = re.split(r"\s+(?:is\s+|was\s+|has\s+been\s+)?(?:carried|continued|adjourned|postponed|rescheduled|withdrawn|tabled|approved|denied|dismissed)\b", name, maxsplit=1, flags=re.I)[0]
    name = re.split(r"\s+(?:total\s+for\s+account|block\s+\d|lots?\s+\d|tax\s+map|as\s+applicant|as\s+owner|seeking|to\s+be\s+(?:heard|carried)?|\(carryover|carryover)\b", name, maxsplit=1, flags=re.I)[0]
    name = re.sub(r"^(?:carryover\s+)?case\s*#?\s*[\w-]+\s*$", "", name, flags=re.I)
    name = re.sub(r"\s*[-–]\s*(application\s+)?documents?\s*$", "", name, flags=re.I)
    name = squash(name).strip(" -–:,.")
    if not name or JUNK_APPLICANT.match(name) or len(name) < 3:
        return None
    if re.search(r"%[0-9A-F]{2}|\.pdf\b|https?://|\bp\d{2,4}-\d{2,4}\b", name, re.I):
        return None  # link text or page references, not a party
    if re.search(r"\b(Road|Rd\.?|Avenue|Ave\.?|Street|St\.?|Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|Place|Pl\.?|Way|Terrace|Boulevard|Blvd\.?|Route|Highway|Pike|Turnpike|Parkway|Circle)\s*$", name, re.I) \
            and not re.search(r"\b(LLC|Inc|Corp|Associates|Partners|Realty|Holdings|Properties|Group)\b", name, re.I):
        return None  # an address used as the item heading
    return name


def _is_real_item(it: dict) -> bool:
    heading = it.get("heading") or ""
    if HEADING_ROW.match(heading) or HEADING_ROW.match(_app_key(it)):
        return False
    return bool(it.get("app_numbers") or it.get("block_lots") or it.get("applicant") or it.get("app_types"))


def _app_key(it: dict) -> str:
    if it.get("app_numbers"):
        return it["app_numbers"][0]
    if it.get("block_lots"):
        b = it["block_lots"][0]
        return f"B{b['block']} L{b['lots']}"
    return (_clean_applicant(it.get("applicant")) or it.get("heading") or "")[:50]


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
        elif doc.get("doc_type") in ("application", "site_plan", "other"):
            head = (doc.get("head_text") or "")[:4000]
            for key in (doc.get("app_numbers") or []) + [f"B{b['block']} L{b['lots']}" for b in doc.get("block_lots", [])]:
                mention_texts[key].append(head)
            title = (doc.get("title") or "").lower()
            if title:
                mention_texts["title:" + title[:60]].append(head)
        for it in doc.get("items", []):
            mention_texts[_app_key(it)].append(it.get("text", "")[:3000])
            ap = _clean_applicant(it.get("applicant"))
            if ap:
                mention_texts["applicant:" + ap.lower()].append(it.get("text", "")[:3000])

    def texts_for(it: dict) -> list[str]:
        key = _app_key(it)
        out = list(mention_texts.get(key, [])) + [it.get("text", "")]
        ap = _clean_applicant(it.get("applicant"))
        if ap:
            out += mention_texts.get("applicant:" + ap.lower(), [])
            last = ap.split()[-1].lower()
            out += [t for k, v in mention_texts.items() if k.startswith("title:") and last in k for t in v]
        return out
    upcoming: dict[str, dict] = {}
    recent: list[dict] = []
    new_docs = []
    for doc in sorted(parsed, key=lambda d: d.get("meeting_date") or ""):
        md = _dt(doc.get("meeting_date"))
        dt = doc.get("doc_type")
        if dt == "agenda" and md and today - timedelta(days=3) <= md <= horizon:
            for it in doc.get("items", []):
                if not _is_real_item(it):
                    continue
                key = _app_key(it)
                e = upcoming.setdefault(key, {"meeting_date": md.isoformat(), "board": doc.get("board_kind"), "application": key,
                                              "applicant": _clean_applicant(it.get("applicant")), "block_lots": it.get("block_lots"),
                                              "addresses": it.get("addresses"), "types": it.get("app_types"),
                                              "proposal": describe(it), "engineers": engineer_for(it, texts_for(it)),
                                              "team": team_for(it, texts_for(it))})
                e["meeting_date"] = min(e["meeting_date"], md.isoformat())
                if not e["engineers"]:
                    e["engineers"] = engineer_for(it, texts_for(it))
                if not e.get("team"):
                    e["team"] = team_for(it, texts_for(it))
                if not e.get("proposal"):
                    e["proposal"] = describe(it)
        elif dt in ("minutes", "resolution") and md and md >= since - timedelta(days=21):
            if dt == "minutes":
                for it in doc.get("items", []):
                    if not _is_real_item(it) or not (it.get("decision") or it.get("app_types")):
                        continue
                    recent.append({"meeting_date": md.isoformat(), "board": doc.get("board_kind"), "application": _app_key(it),
                                   "applicant": _clean_applicant(it.get("applicant")), "types": it.get("app_types"), "decision": it.get("decision"),
                                   "vote": it.get("vote"), "proposal": describe(it), "engineers": engineer_for(it, texts_for(it)),
                                   "team": team_for(it, texts_for(it)), "source": "minutes"})
            else:
                recent.append({"meeting_date": md.isoformat(), "board": doc.get("board_kind"),
                               "application": (doc.get("app_numbers") or [None])[0] or (doc.get("applicant") or doc.get("title") or "")[:50],
                               "applicant": doc.get("applicant"), "types": doc.get("app_types"), "decision": doc.get("decision"),
                               "n_conditions": doc.get("n_conditions"),
                               "engineers": [f"{w['name']}, {w['credentials']}" + (f" ({w['firm']})" if w.get("firm") else "")
                                             for w in doc.get("witnesses", []) if re.search(r"engineer", w.get("role", ""), re.I)][:2],
                               "team": team_for({"professionals": doc.get("witnesses", [])}, [" ".join(doc.get("testimony", []))[:3000]]),
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
         "Professionals = the applicant's engineer (Eng), attorney (Atty), planner, traffic engineer and architect where the agenda, minutes, resolution, review letter or application packet names them; blank when not stated.\n"]
    by_county: dict[str, list[dict]] = defaultdict(list)
    for t in d["towns"]:
        by_county[t["county"] or "Unknown"].append(t)
    # statewide headline: biggest upcoming site plans
    big = [(t["name"], u) for t in d["towns"] for u in t["upcoming"] if any("major site plan" in x or "subdivision" in x for x in (u["types"] or []))]
    if big:
        L.append("## Major site plans and subdivisions on upcoming agendas\n")
        L.append("| Date | Town | Board | Application | Applicant | What's proposed | Types | Professionals |")
        L.append("|---|---|---|---|---|---|---|---|")
        for name, u in sorted(big, key=lambda x: x[1]["meeting_date"])[:60]:
            L.append(f"| {u['meeting_date']} | {name} | {u['board'] or ''} | {u['application']} | {u['applicant'] or ''} | {u.get('proposal') or ''} | {_fmt_types(u['types'])} | {fmt_team(u.get('team') or {})} |")
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
                L.append("| Date | Board | Application | Applicant | Block/Lot | What's proposed | Types | Professionals |")
                L.append("|---|---|---|---|---|---|---|---|")
                for u in t["upcoming"][:20]:
                    bl = "; ".join(f"B{b['block']} L{b['lots']}" for b in (u["block_lots"] or []))
                    L.append(f"| {u['meeting_date']} | {u['board'] or ''} | {u['application']} | {u['applicant'] or ''} | {bl} | {u.get('proposal') or ''} | {_fmt_types(u['types'])} | {fmt_team(u.get('team') or {})} |")
            if t["recent"]:
                L.append("\n**Recent decisions / hearings**\n")
                L.append("| Date | Board | Application | Applicant | What's proposed | Types | Outcome | Professionals |")
                L.append("|---|---|---|---|---|---|---|---|")
                for r in t["recent"][:15]:
                    outcome = (r.get("decision") or "heard") + (f" ({r['vote']})" if r.get("vote") else "") + (f", {r['n_conditions']} conditions" if r.get("n_conditions") else "")
                    L.append(f"| {r['meeting_date']} | {r['board'] or ''} | {r['application']} | {r['applicant'] or ''} | {r.get('proposal') or ''} | {_fmt_types(r['types'])} | {outcome} | {fmt_team(r.get('team') or {})} |")
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
