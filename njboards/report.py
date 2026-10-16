"""Render a municipality profile (analyze.py output) as Markdown, and a
cross-town summary CSV for comparing municipalities side by side."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .config import SETTINGS


def _yn(v) -> str:
    return "yes" if v else "no"


def _table(headers: list[str], rows: Iterable[Iterable]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |")
    return "\n".join(out)


def render_markdown(profile: dict) -> str:
    m = profile["municipality"]
    av = profile["availability"]
    pr = profile["professionals"]
    rc = profile["review_comments"]
    wv = profile["waivers"]
    cd = profile["conditions"]
    ap = profile["applications"]
    code = profile.get("zoning_code")
    L: list[str] = []
    L.append(f"# {m['name']}{', ' + m['county'] + ' County' if m.get('county') else ''} — Land Use Board Profile\n")
    L.append(f"Website: {m.get('website') or 'unknown'}  ")
    L.append(f"Online code: {m.get('ecode_url') or 'not found'}{' (' + m['code_platform'] + ')' if m.get('code_platform') else ''}  ")
    L.append(f"Parsed documents: {profile['n_parsed_documents']}\n")

    L.append("## Boards\n")
    if profile["boards"]:
        L.append(_table(["Board", "Kind", "URL"], [(b["name"], b["kind"], b["url"]) for b in profile["boards"]]))
    else:
        L.append("_No board pages discovered yet._")
    L.append("")

    L.append("## What the town posts online\n")
    L.append(_table(["Document type", "Count"], av["counts"].items()) if av["counts"] else "_Nothing collected yet._")
    L.append("")
    L.append(f"- Agendas: {_yn(av['posts_agendas'])} · Minutes: {_yn(av['posts_minutes'])} · Resolutions: {_yn(av['posts_resolutions'])}")
    L.append(f"- **Review letters posted: {_yn(av['posts_review_letters'])}** · **Site plans / exhibits posted: {_yn(av['posts_site_plans'])}**")
    L.append(f"- Application forms: {_yn(av['posts_applications'])} · Checklists: {_yn(av['posts_checklists'])}")
    L.append(f"- Years covered: {av['years_covered'] or 'n/a'} · Scanned PDFs needing OCR: {av['needs_ocr']}\n")

    L.append("## Board professionals (engineer / planner / attorney)\n")
    if pr["board_by_role"]:
        for role, names in pr["board_by_role"].items():
            L.append(f"- **{role}**: {'; '.join(names[:4])}")
    else:
        L.append("_Not identified. Check the roll call at the top of recent minutes._")
    L.append("")

    L.append("## Professionals who regularly testify for applicants\n")
    regs = pr["applicant_side_regulars"]
    if regs:
        L.append(_table(["Name", "Credentials", "Role", "Firm", "Meetings", "Applications", "Testified"],
                        [(r["name"], r["credentials"], r["role"], r["firm"], r["n_meetings"], r["n_applications"], r["testified"]) for r in regs[:30]]))
    else:
        L.append(f"_No repeat professionals identified yet ({pr['applicant_side_all_count']} distinct names seen once)._")
    L.append("")

    L.append("## Common review comments\n")
    L.append(f"Review letters parsed: {rc['n_review_letters']} ({', '.join(f'{k}: {v}' for k, v in rc['author_types'].items()) or 'none'})\n")
    if rc["category_counts"]:
        L.append("**Comment categories (count):** " + ", ".join(f"{k} {v}" for k, v in list(rc["category_counts"].items())[:14]) + "\n")
    if rc["common_comments"]:
        L.append(_table(["Applications", "Times seen", "Category", "Example comment"],
                        [(g.get("applications", g["letters"]), g["count"], g["category"], g["examples"][0][:220]) for g in rc["common_comments"][:25]]))
    elif rc["n_review_letters"] == 0:
        L.append("_No review letters collected. If the town does not post them, request them under OPRA (N.J.S.A. 47:1A) for comparable recent site plan applications._")
    L.append("")
    if rc.get("flags"):
        f = rc["flags"]
        L.append(f"Comments asking for hearing testimony: {f.get('requires_testimony', 0)} · flagging variances/design waivers: {f.get('requires_variance', 0)} · outside-agency approvals: {f.get('outside_agency', 0)}\n")

    L.append("## Checklist waivers\n")
    L.append(f"Waiver mentions: {wv['n_waiver_mentions']} · by disposition: {wv['by_disposition'] or '{}'} · by kind: {wv['by_kind'] or '{}'}\n")
    if wv["denied_topics"]:
        L.append("**Waivers NOT granted / not recommended, by topic:** " + ", ".join(f"{k} ({v})" for k, v in wv["denied_topics"].items()) + "\n")
    if wv["granted_topics"]:
        L.append("**Waivers typically granted, by topic:** " + ", ".join(f"{k} ({v})" for k, v in list(wv["granted_topics"].items())[:12]) + "\n")
    if wv["denied"]:
        L.append("### Denied / not recommended waivers (evidence)\n")
        L.append(_table(["Date", "Source", "Kind", "Items", "Topic", "Text"],
                        [(r.get("date") or "", r["doc_type"], r["kind"], ", ".join(r.get("items", [])),
                          r.get("topic") or ("[" + (r.get("category") or "") + "]"), r["text"][:260]) for r in wv["denied"][:40]]))
    L.append("")

    L.append("## Common conditions of approval\n")
    L.append(f"Resolutions parsed: {cd['n_resolutions']} · decisions: {cd['decisions'] or '{}'}\n")
    if cd["common_conditions"]:
        L.append(_table(["Times", "Category", "Condition"], [(g["count"], g["category"], g["example"][:240]) for g in cd["common_conditions"][:20]]))
    L.append("")

    L.append("## Application mix\n")
    L.append(f"Types seen on agendas/minutes: {ap['application_types'] or '{}'}\n")
    L.append(f"Outcomes: {ap['outcomes'] or '{}'} · site plan applications identified: {ap['n_site_plan_applications']}\n")
    if ap["recent_site_plans"]:
        L.append(_table(["Date", "Board", "Application", "Applicant", "Block/Lot", "Types", "Status"],
                        [(r["date"] or "", r["board"] or "", r["application"], r["applicant"] or "",
                          "; ".join(f"B{b['block']} L{b['lots']}" for b in (r["block_lots"] or [])),
                          ", ".join(r["types"] or []), r["status"] or "") for r in ap["recent_site_plans"][:25]]))
    L.append("")

    L.append("## Zoning / land development code\n")
    if code:
        L.append(f"Code: {code['code_url']} (id {code['code_id'] or 'n/a'})\n")
        L.append("Chapters pulled: " + "; ".join(code["chapters"][:20]) + "\n")
        for topic, secs in code["sections_by_topic"].items():
            L.append(f"- **{topic}**: " + " · ".join(secs[:6]))
        if code["checklist_items"]:
            L.append(f"\nChecklist items found in code: {code['checklist_items_in_code']}")
        if code["waiver_provisions"]:
            L.append("\n**Waiver provisions in the ordinance:**")
            for w in code["waiver_provisions"][:10]:
                L.append(f"- ({w['section']}) {w['text']}")
        if code["attachments"]:
            L.append("\n**Attachments (checklists, schedules, maps):**")
            for a in code["attachments"][:20]:
                L.append(f"- [{a['title'] or 'attachment'}]({a['url']})")
    else:
        L.append("_Code not pulled. Run `njboards ecode <slug>` (needs the eCode360 URL; `discover` finds it when the town links it)._")
    L.append("")

    if profile.get("checklist_items"):
        L.append("## Submission checklist items (from posted checklists / code)\n")
        L.append(_table(["#", "Checklist", "Topic", "Waivable", "Requirement"],
                        [(i.get("number"), i.get("checklist"), i.get("topic") or "", "" if i.get("waivable") is None else _yn(i["waivable"]),
                          (i.get("text") or "")[:160]) for i in profile["checklist_items"][:120]]))
        L.append("")
    return "\n".join(L)


def write_report(profile: dict) -> Path:
    slug = profile["municipality"]["slug"]
    out = SETTINGS.reports_dir / slug / "profile.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(profile), encoding="utf-8")
    return out


SUMMARY_FIELDS = ["slug", "name", "county", "website", "code_url", "boards", "docs", "agendas", "minutes", "resolutions",
                  "review_letters", "site_plans", "applications", "checklists", "years", "board_engineer", "board_planner",
                  "board_attorney", "regular_applicant_pros", "top_comment_categories", "waivers_denied_topics",
                  "waivers_granted_topics", "n_site_plan_apps", "top_conditions"]


def write_summary_csv(profiles: list[dict], path: Path | None = None) -> Path:
    path = path or SETTINGS.reports_dir / "summary.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        for p in profiles:
            m, av, pr, rc, wv, cd, ap = (p["municipality"], p["availability"], p["professionals"], p["review_comments"],
                                         p["waivers"], p["conditions"], p["applications"])
            c = {k.lower().replace(" ", "_"): v for k, v in av["counts"].items()}
            br = pr["board_by_role"]
            w.writerow({
                "slug": m["slug"], "name": m["name"], "county": m.get("county"), "website": m.get("website"),
                "code_url": m.get("ecode_url"), "boards": "; ".join(f"{b['kind']}: {b['url']}" for b in p["boards"]),
                "docs": sum(av["counts"].values()), "agendas": c.get("agendas", 0), "minutes": c.get("minutes", 0),
                "resolutions": c.get("resolutions", 0), "review_letters": c.get("review_letters", 0),
                "site_plans": c.get("site_plans_/_exhibits", 0), "applications": c.get("application_forms", 0),
                "checklists": c.get("checklists", 0), "years": av["years_covered"],
                "board_engineer": "; ".join(br.get("engineer", [])[:2] + br.get("municipal engineer", [])[:1]),
                "board_planner": "; ".join(br.get("planner", [])[:2]),
                "board_attorney": "; ".join(br.get("attorney", [])[:2] + br.get("counsel", [])[:1]),
                "regular_applicant_pros": "; ".join(f"{r['name']} ({r['role'] or r['credentials']})" for r in pr["applicant_side_regulars"][:8]),
                "top_comment_categories": "; ".join(f"{k} {v}" for k, v in list(rc["category_counts"].items())[:6]),
                "waivers_denied_topics": "; ".join(f"{k} {v}" for k, v in list(wv["denied_topics"].items())[:6]),
                "waivers_granted_topics": "; ".join(f"{k} {v}" for k, v in list(wv["granted_topics"].items())[:6]),
                "n_site_plan_apps": ap["n_site_plan_applications"],
                "top_conditions": "; ".join(g["example"][:80] for g in cd["common_conditions"][:4]),
            })
    return path
