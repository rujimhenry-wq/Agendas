"""Aggregate parsed documents into a per-municipality profile:
document availability, board + testifying professionals, common review
comments, checklist waivers (granted / denied), common conditions of
approval, application mix and outcomes, zoning code pointers."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

from .config import SETTINGS
from .db import DB, dump_json, load_json, row_to_dict

DOC_LABELS = {
    "agenda": "Agendas", "minutes": "Minutes", "resolution": "Resolutions", "review_letter": "Review letters",
    "site_plan": "Site plans / exhibits", "application": "Application forms", "checklist": "Checklists",
    "notice": "Notices", "ordinance": "Ordinances / code", "fee_schedule": "Fee schedules", "schedule": "Meeting schedules", "other": "Other",
}


def _load_parsed(muni_slug: str) -> list[dict]:
    d = SETTINGS.parsed_dir / muni_slug
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(load_json(p))
        except json.JSONDecodeError:
            continue
    return out


def _norm_name(n: str) -> str:
    n = re.sub(r"\s+", " ", n.strip())
    n = re.sub(r",?\s+(Jr|Sr|II|III|IV)\.?$", "", n)
    return n.title() if n.isupper() else n


def professionals_summary(parsed: list[dict]) -> dict:
    """Who testifies for applicants, and who the board's professionals are."""
    applicant: dict[str, dict] = {}
    board: dict[str, dict] = {}
    for doc in parsed:
        meeting = doc.get("meeting_date") or doc.get("letter_date")
        pros = list(doc.get("professionals", [])) + list(doc.get("witnesses", [])) + list(doc.get("board_professionals", []))
        for it in doc.get("items", []):
            pros.extend(it.get("professionals", []))
        for p in pros:
            name = _norm_name(p.get("name", ""))
            if not name:
                continue
            bucket = board if p.get("side") == "board" else applicant
            e = bucket.setdefault(name, {"name": name, "credentials": "", "role": "", "firm": "", "meetings": set(),
                                         "applications": set(), "mentions": 0, "testified": 0})
            e["mentions"] += p.get("mentions", 1)
            e["testified"] += 1 if p.get("testified") else 0
            if meeting:
                e["meetings"].add(meeting)
            if p.get("application"):
                e["applications"].add(str(p["application"])[:60])
            for k in ("credentials", "role", "firm"):
                if p.get(k) and not e[k]:
                    e[k] = p[k]

    def finish(d: dict, key: str) -> list[dict]:
        rows = []
        for e in d.values():
            rows.append({**e, "meetings": sorted(e["meetings"]), "n_meetings": len(e["meetings"]),
                         "applications": sorted(e["applications"])[:15], "n_applications": len(e["applications"])})
        rows.sort(key=lambda r: (-r["n_meetings"], -r["mentions"], r["name"]))
        return rows

    app_rows = finish(applicant, "applicant")
    # "regularly" = appears at 2+ meetings or on 2+ applications, or testified at 2+
    regular = [r for r in app_rows if r["n_meetings"] >= 2 or r["n_applications"] >= 2 or r["testified"] >= 2]
    by_role: dict[str, list[dict]] = defaultdict(list)
    for r in regular:
        by_role[r["role"] or "unspecified"].append(r)
    board_rows = finish(board, "board")
    board_by_role: dict[str, list[str]] = defaultdict(list)
    for r in board_rows:
        board_by_role[r["role"] or "unspecified"].append(f"{r['name']}{' (' + r['firm'] + ')' if r['firm'] else ''} [{r['n_meetings']} mtgs]")
    return {
        "applicant_side_regulars": regular[:60],
        "applicant_side_by_role": {k: v[:15] for k, v in by_role.items()},
        "applicant_side_all_count": len(app_rows),
        "board_professionals": board_rows[:30],
        "board_by_role": dict(board_by_role),
    }


def comments_summary(parsed: list[dict], top_n: int = 15) -> dict:
    """Common review-letter comments and board-professional remarks."""
    cat_counts: Counter = Counter()
    groups: dict[str, dict] = {}
    letters = 0
    flags = Counter()
    author_types: Counter = Counter()
    for doc in parsed:
        if doc.get("doc_type") == "review_letter":
            letters += 1
            author_types[doc.get("author_type") or "unknown"] += 1
            for c in doc.get("comments", []):
                cat_counts[c["category"]] += 1
                g = groups.setdefault(c["key"], {"category": c["category"], "count": 0, "examples": [], "letters": set()})
                g["count"] += 1
                g["letters"].add(doc.get("re") or doc.get("letter_date") or "")
                if len(g["examples"]) < 2:
                    g["examples"].append(c["text"][:300])
                for f in ("requires_testimony", "requires_variance", "outside_agency", "is_waiver"):
                    if c.get(f):
                        flags[f] += 1
        elif doc.get("doc_type") == "minutes":
            for it in doc.get("items", []):
                for r in it.get("board_remarks", []):
                    cat_counts["[minutes] " + r["category"]] += 1
    common = sorted(groups.values(), key=lambda g: (-g["count"], g["category"]))
    common = [{**g, "letters": len(g["letters"])} for g in common if g["count"] >= 2][:top_n * 2] or \
             [{**g, "letters": len(g["letters"])} for g in common[:top_n]]
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for g in sorted(groups.values(), key=lambda g: -g["count"]):
        if len(by_cat[g["category"]]) < 5:
            by_cat[g["category"]].append({"count": g["count"], "example": g["examples"][0]})
    return {"n_review_letters": letters, "author_types": dict(author_types),
            "category_counts": dict(cat_counts.most_common()), "common_comments": common,
            "by_category": dict(by_cat), "flags": dict(flags)}


def waivers_summary(parsed: list[dict], checklist_items: Optional[list[dict]] = None) -> dict:
    """Checklist / design waivers by disposition and topic; denied ones listed."""
    rows: list[dict] = []
    for doc in parsed:
        src = doc.get("doc_type")
        meeting = doc.get("meeting_date") or doc.get("letter_date")
        ws = list(doc.get("waivers", []))
        for it in doc.get("items", []):
            ws.extend(it.get("waivers", []))
        for w in ws:
            rows.append({**w, "doc_type": src, "date": meeting})
    # map item numbers to checklist text where we know the town's checklist
    lookup = {}
    for ci in checklist_items or []:
        lookup.setdefault(str(ci.get("number")), ci.get("text", "")[:120])
    for r in rows:
        r["item_text"] = [lookup.get(i) for i in r.get("items", []) if lookup.get(i)]
    by_disp = Counter(r["disposition"] for r in rows)
    by_kind = Counter(r["kind"] for r in rows)
    topic_disp: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r.get("topic"):
            topic_disp[r["topic"]][r["disposition"]] += 1
    denied = [r for r in rows if r["disposition"] == "denied"]
    denied.sort(key=lambda r: (r.get("topic") or "zzz", r.get("date") or ""))
    not_approved_topics = Counter(r["topic"] for r in denied if r.get("topic"))
    granted_topics = Counter(r["topic"] for r in rows if r["disposition"] == "granted" and r.get("topic"))
    return {
        "n_waiver_mentions": len(rows), "by_disposition": dict(by_disp), "by_kind": dict(by_kind),
        "topics_by_disposition": {t: dict(c) for t, c in sorted(topic_disp.items(), key=lambda kv: -sum(kv[1].values()))},
        "denied_topics": dict(not_approved_topics.most_common()),
        "granted_topics": dict(granted_topics.most_common()),
        "denied": denied[:80],
        "requested_or_deferred": [r for r in rows if r["disposition"] in ("requested", "deferred")][:40],
    }


def conditions_summary(parsed: list[dict], top_n: int = 20) -> dict:
    cat_counts: Counter = Counter()
    groups: dict[str, dict] = {}
    n_res = 0
    decisions: Counter = Counter()
    for doc in parsed:
        if doc.get("doc_type") != "resolution":
            continue
        n_res += 1
        decisions[doc.get("decision") or "unknown"] += 1
        for c in doc.get("conditions", []):
            cat_counts[c["category"]] += 1
            g = groups.setdefault(c["key"], {"category": c["category"], "count": 0, "example": c["text"][:300], "standard": c.get("standard", False)})
            g["count"] += 1
    common = sorted(groups.values(), key=lambda g: -g["count"])[:top_n]
    return {"n_resolutions": n_res, "decisions": dict(decisions), "category_counts": dict(cat_counts.most_common()),
            "common_conditions": common}


def applications_summary(parsed: list[dict]) -> dict:
    types: Counter = Counter()
    outcomes: Counter = Counter()
    by_app: dict[str, dict] = {}
    for doc in sorted(parsed, key=lambda d: d.get("meeting_date") or ""):
        if doc.get("doc_type") not in ("agenda", "minutes"):
            continue
        for it in doc.get("items", []):
            for t in it.get("app_types", []):
                types[t] += 1
            if it.get("decision"):
                outcomes[it["decision"]] += 1
            if it.get("is_site_plan"):
                key = (it["app_numbers"][0] if it["app_numbers"] else (it.get("applicant") or it["heading"][:50]))
                status = it.get("decision") or it.get("status")
                row = by_app.get(key)
                if row is None:
                    by_app[key] = {"date": doc.get("meeting_date"), "first_seen": doc.get("meeting_date"), "board": doc.get("board_kind"),
                                   "application": key, "applicant": it.get("applicant"), "block_lots": it.get("block_lots"),
                                   "types": it.get("app_types"), "status": status, "hearings": 1}
                else:  # later document: newest status wins, count hearings
                    row["hearings"] += 1
                    row["date"] = doc.get("meeting_date") or row["date"]
                    row["status"] = status or row["status"]
                    row["applicant"] = row["applicant"] or it.get("applicant")
                    row["types"] = row["types"] or it.get("app_types")
    site_plans = sorted(by_app.values(), key=lambda r: r["date"] or "", reverse=True)
    return {"application_types": dict(types.most_common()), "outcomes": dict(outcomes.most_common()),
            "recent_site_plans": site_plans[:40], "n_site_plan_applications": len(site_plans)}


def availability(db: DB, muni_id: int) -> dict:
    counts = db.doc_type_counts(muni_id)
    docs = db.documents(muni_id)
    years = sorted({d["year"] for d in docs if d["year"]})
    status = Counter(d["status"] for d in docs)
    by_board = Counter(d["board_kind"] or "unknown" for d in docs)
    return {
        "counts": {DOC_LABELS.get(k, k): v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])},
        "posts_agendas": counts.get("agenda", 0) > 0,
        "posts_minutes": counts.get("minutes", 0) > 0,
        "posts_resolutions": counts.get("resolution", 0) > 0,
        "posts_review_letters": counts.get("review_letter", 0) > 0,
        "posts_site_plans": counts.get("site_plan", 0) > 0,
        "posts_applications": counts.get("application", 0) > 0,
        "posts_checklists": counts.get("checklist", 0) > 0,
        "years_covered": f"{years[0]}–{years[-1]}" if years else None,
        "status": dict(status), "by_board": dict(by_board),
        "needs_ocr": sum(1 for d in docs if (d["error"] or "").startswith("needs OCR")),
    }


def code_summary(muni_slug: str) -> Optional[dict]:
    p = SETTINGS.code_dir / muni_slug / "code_index.json"
    if not p.exists():
        return None
    idx = load_json(p)
    return {
        "code_url": idx.get("code_url"), "code_id": idx.get("code_id"),
        "chapters": [c["title"] for c in idx.get("chapters", [])],
        "sections_by_topic": {t: [f"{s['heading']} ({s['chapter']})" for s in v[:8]] for t, v in idx.get("sections_by_topic", {}).items()},
        "checklist_items_in_code": len(idx.get("checklist_items", [])),
        "checklist_items": idx.get("checklist_items", [])[:200],
        "waiver_provisions": idx.get("waiver_provisions", [])[:20],
        "attachments": idx.get("attachments", [])[:40],
    }


def analyze_municipality(db: DB, muni_slug: str) -> dict:
    m = row_to_dict(db.municipality(muni_slug))
    parsed = _load_parsed(muni_slug)
    code = code_summary(muni_slug)
    checklist_items = []
    if code:
        checklist_items.extend(code.get("checklist_items", []))
    for doc in parsed:
        if doc.get("doc_type") == "checklist":
            checklist_items.extend(doc.get("items", []))
    profile = {
        "municipality": {k: m.get(k) for k in ("slug", "name", "county", "muni_type", "website", "ecode_id", "ecode_url", "code_platform")},
        "boards": [row_to_dict(b) for b in db.boards(m["id"])],
        "availability": availability(db, m["id"]),
        "n_parsed_documents": len(parsed),
        "professionals": professionals_summary(parsed),
        "review_comments": comments_summary(parsed),
        "waivers": waivers_summary(parsed, checklist_items),
        "conditions": conditions_summary(parsed),
        "applications": applications_summary(parsed),
        "checklist_items": checklist_items[:250],
        "zoning_code": code,
    }
    dump_json(SETTINGS.reports_dir / muni_slug / "profile.json", profile)
    return profile
