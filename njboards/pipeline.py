"""Glue: run discover -> fetch -> extract -> parse -> analyze -> report for a town."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .analyze import analyze_municipality
from .config import SETTINGS
from .db import DB, dump_json, row_to_dict
from .discover import discover
from .ecode import fetch_code
from .extract import extract_all
from .fetch import fetch_all
from .http import Fetcher
from .parse import parse_document
from .report import write_report

log = logging.getLogger("njboards.pipeline")

PARSEABLE = ("agenda", "minutes", "review_letter", "resolution", "checklist", "application", "site_plan", "other", "notice")


def run_discover(fetcher: Fetcher, db: DB, slug: str, max_pages: Optional[int] = None) -> dict:
    m = db.municipality(slug)
    if not m["website"]:
        raise RuntimeError(f"{slug}: no website on file; set one with `njboards add-town` or edit data/municipalities.csv")
    manual = [{"kind": b["kind"], "url": b["url"], "name": b["name"]} for b in db.boards(m["id"]) if b["source"] == "manual"]
    disc = discover(fetcher, m["website"], max_pages=max_pages, manual_boards=manual)
    board_ids: dict[str, int] = {}
    for b in disc.boards:
        board_ids[b["kind"]] = db.upsert_board(m["id"], b["kind"], b["name"], b["url"], platform=disc.platform, source=b.get("source", "discovered"))
    n_new = 0
    for d in disc.docs:
        if d.year and d.year < SETTINGS.min_year:
            continue
        before = db.conn.execute("SELECT COUNT(*) FROM documents WHERE muni_id=?", (m["id"],)).fetchone()[0]
        db.add_document(m["id"], d.url, d.title, d.doc_type, board_ids.get(d.board_kind or ""), d.board_kind, d.meeting_date, d.year)
        after = db.conn.execute("SELECT COUNT(*) FROM documents WHERE muni_id=?", (m["id"],)).fetchone()[0]
        n_new += after - before
    fields = {}
    if disc.homepage and disc.homepage.rstrip("/") != (m["website"] or "").rstrip("/") and not disc.homepage.startswith((m["website"] or "").rstrip("/")):
        fields["website"] = disc.homepage if disc.homepage.endswith("/") else disc.homepage + "/"
        fields["notes"] = ((m["notes"] or "") + " site redirected; updated").strip()
    if disc.ecode_id and not m["ecode_id"]:
        fields["ecode_id"] = disc.ecode_id
    if disc.ecode_url and not m["ecode_url"]:
        fields["ecode_url"] = disc.ecode_url
        fields["code_platform"] = disc.code_platform
    fields["status"] = "discovered"
    db.set_muni_fields(slug, **fields)
    summary = {"slug": slug, "platform": disc.platform, "pages_visited": disc.pages_visited, "boards": disc.boards,
               "documents_found": len(disc.docs), "documents_new": n_new, "ecode_url": disc.ecode_url or m["ecode_url"],
               "errors": disc.errors[:20]}
    dump_json(SETTINGS.reports_dir / slug / "discovery.json", summary)
    return summary


def run_parse(db: DB, slug: str, force: bool = False) -> dict:
    mid = db.muni_id(slug)
    out_dir = SETTINGS.parsed_dir / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"parsed": 0, "skipped": 0, "errors": 0}
    for d in db.documents(mid):
        if d["status"] not in ("extracted", "parsed") or not d["text_path"]:
            continue
        out = out_dir / f"{d['id']}.json"
        if out.exists() and d["status"] == "parsed" and not force:
            stats["skipped"] += 1
            continue
        text = Path(d["text_path"]).read_text(encoding="utf-8", errors="ignore")
        if len(text) < 40:
            stats["skipped"] += 1
            continue
        try:
            result = parse_document(d["doc_type"] or "other", text, d["title"] or "")
        except Exception as e:  # noqa: BLE001 - keep going on odd documents
            log.warning("parse failed for doc %s (%s): %s", d["id"], d["url"], e)
            db.update_document(d["id"], error=f"parse: {e}")
            stats["errors"] += 1
            continue
        result["doc_id"] = d["id"]
        result["url"] = d["url"]
        result["title"] = d["title"]
        result["board_kind"] = result.get("board_kind") or d["board_kind"]
        if not result.get("meeting_date") and d["meeting_date"]:
            result["meeting_date"] = d["meeting_date"]
        if result.get("meeting_date") and not d["meeting_date"]:
            db.update_document(d["id"], meeting_date=result["meeting_date"], year=int(result["meeting_date"][:4]))
        dump_json(out, result)
        db.update_document(d["id"], status="parsed")
        stats["parsed"] += 1
    return stats


def run_all(fetcher: Fetcher, db: DB, slug: str, fetch_limit: Optional[int] = None, with_code: bool = True,
            doc_types: Optional[list[str]] = None) -> dict:
    summary: dict = {"slug": slug}
    summary["discover"] = run_discover(fetcher, db, slug)
    summary["fetch"] = fetch_all(fetcher, db, slug, doc_types=doc_types, limit=fetch_limit)
    summary["extract"] = extract_all(db, slug)
    summary["parse"] = run_parse(db, slug)
    if with_code:
        m = db.municipality(slug)
        if m["ecode_url"] and (m["code_platform"] or "ecode360") == "ecode360":
            try:
                code = fetch_code(fetcher, slug, m["ecode_url"])
                summary["code"] = {"chapters": code["n_chapters_fetched"], "checklist_items": len(code["checklist_items"])}
            except Exception as e:  # noqa: BLE001
                summary["code"] = {"error": str(e)}
        else:
            summary["code"] = {"skipped": "no eCode360 URL on file"}
    profile = analyze_municipality(db, slug)
    summary["report"] = str(write_report(profile))
    return summary
