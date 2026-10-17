"""njboards command line.

Typical use:
    njboards init                         # load data/municipalities.csv into the db
    njboards run princeton-mercer         # discover -> fetch -> extract -> parse -> code -> report
    njboards run --county Union           # every town in a county
    njboards report --all                 # regenerate reports + summary.csv
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .analyze import analyze_municipality
from .config import SETTINGS
from .db import DB, row_to_dict
from .ecode import fetch_code
from .extract import extract_all
from .fetch import fetch_all
from .http import Fetcher
from .municipalities import (export_from_db, find_website, import_from_wikipedia, load_into_db, make_slug, read_csv, write_csv)  # noqa: E501
from .pipeline import run_all, run_discover, run_parse
from .report import write_report, write_summary_csv


def _slugs(db: DB, slug: Optional[str], county: Optional[str], all_: bool) -> list[str]:
    if slug:
        return [slug]
    if county:
        return [m["slug"] for m in db.municipalities(county)]
    if all_:
        return [m["slug"] for m in db.municipalities()]
    raise click.UsageError("give a municipality slug, --county NAME, or --all")


def _echo_json(obj) -> None:
    click.echo(json.dumps(obj, indent=2, default=str))


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="debug logging")
@click.option("--data-dir", type=click.Path(file_okay=False), help="override data directory (default ./data)")
@click.version_option(__version__)
def main(verbose: bool, data_dir: Optional[str]) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if data_dir:
        SETTINGS.data_dir = Path(data_dir).resolve()
    SETTINGS.ensure_dirs()


@main.command()
@click.option("--csv", "csv_path", type=click.Path(exists=True, dir_okay=False), help="municipalities CSV (default data/municipalities.csv)")
def init(csv_path: Optional[str]) -> None:
    """Create the database and load the municipality list."""
    db = DB()
    n = load_into_db(db, Path(csv_path) if csv_path else None)
    click.echo(f"loaded {n} municipalities into {SETTINGS.db_path}")


@main.command("add-town")
@click.argument("name")
@click.option("--county", required=True)
@click.option("--website", required=True, help="homepage, e.g. https://www.townname.org/")
@click.option("--ecode", help="eCode360 URL or code id (e.g. https://ecode360.com/XX1234)")
@click.option("--planning-url", help="planning board page")
@click.option("--zoning-url", help="zoning board / board of adjustment page")
def add_town(name: str, county: str, website: str, ecode: Optional[str], planning_url: Optional[str], zoning_url: Optional[str]) -> None:
    """Add or update one municipality (also appends to data/municipalities.csv)."""
    db = DB()
    slug = make_slug(name, county)
    ecode_url = None
    if ecode:
        ecode_url = ecode if ecode.startswith("http") else f"https://ecode360.com/{ecode.upper()}"
    db.upsert_municipality(slug, name, county=county, website=website, ecode_url=ecode_url,
                           ecode_id=(ecode.upper() if ecode and not ecode.startswith("http") else None),
                           code_platform="ecode360" if ecode_url else None)
    mid = db.muni_id(slug)
    if planning_url:
        db.upsert_board(mid, "planning", "Planning Board (manual)", planning_url, source="manual")
    if zoning_url:
        db.upsert_board(mid, "zoning", "Zoning Board of Adjustment (manual)", zoning_url, source="manual")
    export_from_db(db, SETTINGS.municipalities_file)
    click.echo(f"saved {slug}")


@main.command("import-towns")
@click.option("--find-websites", is_flag=True, help="also search for each town's website (slow; verify results)")
@click.option("--limit", type=int, default=None)
def import_towns(find_websites: bool, limit: Optional[int]) -> None:
    """Import the full list of NJ municipalities from Wikipedia into the CSV + db."""
    db = DB()
    fetcher = Fetcher()
    rows = import_from_wikipedia(fetcher)
    existing = {r["slug"]: r for r in read_csv(SETTINGS.municipalities_file)}
    merged = []
    for r in rows[: limit or None]:
        old = existing.get(r["slug"], {})
        merged.append({**r, **{k: v for k, v in old.items() if v}})
    for slug, old in existing.items():
        if slug not in {r["slug"] for r in merged}:
            merged.append(old)
    if find_websites:
        from .municipalities import website_from_wikipedia
        fetcher.s.delay_seconds = max(fetcher.s.delay_seconds, 2.5)  # be gentle with Wikipedia
        for r in merged:
            if r.get("website"):
                continue
            w = website_from_wikipedia(fetcher, r.get("wiki_url", "")) or find_website(fetcher, r["name"], r.get("county"))
            if w:
                r["website"] = w
                r["notes"] = (r.get("notes") or "") + " website from Wikipedia infobox; verify"
                click.echo(f"{r['slug']}: {w}")
            else:
                click.echo(f"{r['slug']}: no website found", err=True)
            write_csv(SETTINGS.municipalities_file, merged)  # checkpoint
    from .municipalities import dedupe_rows
    merged = dedupe_rows(merged)
    write_csv(SETTINGS.municipalities_file, merged)
    n = load_into_db(db)
    click.echo(f"{len(rows)} municipalities imported; {n} rows now in {SETTINGS.municipalities_file}")


@main.command("find-websites")
@click.option("--workers", type=int, default=8)
def find_websites_cmd(workers: int) -> None:
    """Fill in missing municipal websites from county directories and domain patterns (no third parties)."""
    from .websites import find_websites
    db = DB()
    rows = read_csv(SETTINGS.municipalities_file)
    def progress(slug, url, how):
        click.echo(f"{slug}: {url} ({how})")
    found = find_websites(rows, workers=workers, progress=progress)
    for r in rows:
        if r["slug"] in found:
            r["website"] = found[r["slug"]]
            r["notes"] = ((r.get("notes") or "") + " website auto-found; verify").strip()
    write_csv(SETTINGS.municipalities_file, rows)
    load_into_db(db)
    click.echo(f"found {len(found)} websites; {sum(1 for r in rows if r['website'])} of {len(rows)} rows now have one")


@main.command("find-website")
@click.argument("slug")
def find_website_cmd(slug: str) -> None:
    """Look up the official website for a town (best effort; verify)."""
    db = DB()
    m = db.municipality(slug)
    w = find_website(Fetcher(), m["name"], m["county"])
    if w:
        db.set_muni_fields(slug, website=w)
        export_from_db(db, SETTINGS.municipalities_file)
    click.echo(w or "not found")


@main.command()
@click.argument("slug", required=False)
@click.option("--county")
@click.option("--all", "all_", is_flag=True)
@click.option("--max-pages", type=int, default=None)
@click.option("--workers", type=int, default=1, help="towns to crawl side by side (each town is its own host)")
@click.option("--skip-done", is_flag=True, help="skip towns that already have boards discovered")
def discover(slug: Optional[str], county: Optional[str], all_: bool, max_pages: Optional[int], workers: int, skip_done: bool) -> None:
    """Find board pages, document links and the online code for a town."""
    db = DB()
    slugs = [s for s in _slugs(db, slug, county, all_) if db.municipality(s)["website"]]
    if skip_done:
        slugs = [s for s in slugs if not db.boards(db.muni_id(s))]
    if workers > 1:
        from .parallel import run_parallel
        db.close()
        def report(r):
            click.echo(f"{r['slug']}: " + (f"{r['result']['documents_found']} docs, boards {[b['kind'] for b in r['result']['boards']]}" if r["ok"] else f"ERROR {r['error']}"))
        res = run_parallel(slugs, "discover", workers=workers, progress=report, max_pages=max_pages)
        click.echo(f"done: {sum(1 for r in res if r['ok'])} ok, {sum(1 for r in res if not r['ok'])} failed")
        return
    fetcher = Fetcher()
    for s in slugs:
        try:
            _echo_json(run_discover(fetcher, db, s, max_pages=max_pages))
        except Exception as e:  # noqa: BLE001
            click.echo(f"{s}: ERROR {e}", err=True)


@main.command()
@click.argument("slug", required=False)
@click.option("--county")
@click.option("--all", "all_", is_flag=True)
@click.option("--types", help="comma-separated doc types (agenda,minutes,resolution,review_letter,checklist,application,site_plan)")
@click.option("--limit", type=int, default=None, help="max documents to download per town")
@click.option("--retry-errors", is_flag=True)
def fetch(slug: Optional[str], county: Optional[str], all_: bool, types: Optional[str], limit: Optional[int], retry_errors: bool) -> None:
    """Download discovered documents."""
    db = DB()
    fetcher = Fetcher()
    dt = types.split(",") if types else None
    for s in _slugs(db, slug, county, all_):
        click.echo(f"{s}: {fetch_all(fetcher, db, s, doc_types=dt, limit=limit, refetch_errors=retry_errors)}")


@main.command()
@click.argument("slug", required=False)
@click.option("--county")
@click.option("--all", "all_", is_flag=True)
@click.option("--force", is_flag=True)
@click.option("--ocr/--no-ocr", default=None, help="OCR scanned PDFs (needs tesseract + pdftoppm)")
def extract(slug: Optional[str], county: Optional[str], all_: bool, force: bool, ocr: Optional[bool]) -> None:
    """Extract text from downloaded files."""
    db = DB()
    for s in _slugs(db, slug, county, all_):
        click.echo(f"{s}: {extract_all(db, s, force=force, ocr=ocr)}")


@main.command()
@click.argument("slug", required=False)
@click.option("--county")
@click.option("--all", "all_", is_flag=True)
@click.option("--force", is_flag=True)
def parse(slug: Optional[str], county: Optional[str], all_: bool, force: bool) -> None:
    """Parse extracted text into structured JSON (items, professionals, comments, waivers...)."""
    db = DB()
    for s in _slugs(db, slug, county, all_):
        click.echo(f"{s}: {run_parse(db, s, force=force)}")


@main.command()
@click.argument("slug")
@click.option("--url", help="eCode360 code URL (overrides what discover found)")
@click.option("--max-chapters", type=int, default=25)
def ecode(slug: str, url: Optional[str], max_chapters: int) -> None:
    """Pull zoning / land development chapters from eCode360."""
    db = DB()
    m = db.municipality(slug)
    code_url = url or m["ecode_url"]
    if not code_url:
        raise click.UsageError(f"{slug}: no eCode360 URL known. Pass --url https://ecode360.com/XX1234")
    if url and not m["ecode_url"]:
        db.set_muni_fields(slug, ecode_url=url, code_platform="ecode360")
    res = fetch_code(Fetcher(), slug, code_url, max_chapters=max_chapters)
    click.echo(f"{slug}: {res['n_chapters_fetched']} chapters, {len(res['checklist_items'])} checklist items, "
               f"{len(res['waiver_provisions'])} waiver provisions, {len(res['attachments'])} attachments -> {SETTINGS.code_dir / slug}")


@main.command()
@click.argument("slug", required=False)
@click.option("--county")
@click.option("--all", "all_", is_flag=True)
def analyze(slug: Optional[str], county: Optional[str], all_: bool) -> None:
    """Aggregate parsed documents into profile.json (no network)."""
    db = DB()
    for s in _slugs(db, slug, county, all_):
        p = analyze_municipality(db, s)
        click.echo(f"{s}: {p['n_parsed_documents']} docs -> {SETTINGS.reports_dir / s / 'profile.json'}")


@main.command()
@click.argument("slug", required=False)
@click.option("--county")
@click.option("--all", "all_", is_flag=True)
def report(slug: Optional[str], county: Optional[str], all_: bool) -> None:
    """Write Markdown profiles and the cross-town summary.csv."""
    db = DB()
    profiles = []
    for s in _slugs(db, slug, county, all_):
        p = analyze_municipality(db, s)
        profiles.append(p)
        click.echo(f"{s}: {write_report(p)}")
    click.echo(f"summary: {write_summary_csv(profiles)}")


@main.command()
@click.argument("slug", required=False)
@click.option("--county")
@click.option("--all", "all_", is_flag=True)
@click.option("--limit", type=int, default=None, help="max documents to download per town")
@click.option("--types", help="comma-separated doc types to download")
@click.option("--no-code", is_flag=True, help="skip eCode360")
@click.option("--workers", type=int, default=1, help="towns to process side by side")
def run(slug: Optional[str], county: Optional[str], all_: bool, limit: Optional[int], types: Optional[str], no_code: bool, workers: int) -> None:
    """Full pipeline for one town, a county, or everything."""
    db = DB()
    slugs = [s for s in _slugs(db, slug, county, all_) if db.municipality(s)["website"]]
    if workers > 1:
        from .parallel import run_parallel
        db.close()
        def report(r):
            click.echo(f"{r['slug']}: " + ("ok" if r["ok"] else f"ERROR {r['error']}"))
        run_parallel(slugs, "run", workers=workers, progress=report, fetch_limit=limit, with_code=not no_code,
                     doc_types=types.split(",") if types else None)
        db = DB()
        profiles = [analyze_municipality(db, s) for s in slugs]
        click.echo(f"summary: {write_summary_csv(profiles)}")
        return
    fetcher = Fetcher()
    profiles = []
    for s in slugs:
        try:
            summary = run_all(fetcher, db, s, fetch_limit=limit, with_code=not no_code,
                              doc_types=types.split(",") if types else None)
            _echo_json(summary)
            profiles.append(analyze_municipality(db, s))
        except Exception as e:  # noqa: BLE001
            click.echo(f"{s}: ERROR {e}", err=True)
            db.set_muni_fields(s, status=f"error: {e}"[:200])
    if profiles:
        click.echo(f"summary: {write_summary_csv(profiles)}")


@main.command()
@click.option("--county")
@click.option("--all", "all_", is_flag=True, default=True, help="all towns with a website (default)")
@click.option("--workers", type=int, default=6)
@click.option("--horizon", type=int, default=30, help="days ahead to look for agendas")
@click.option("--limit", type=int, default=12, help="documents to download per town this week")
@click.option("--since", default=None, help="YYYY-MM-DD; default: last digest date or 7 days ago")
@click.option("--no-crawl", is_flag=True, help="only build the digest from what is already parsed")
def weekly(county: Optional[str], all_: bool, workers: int, horizon: int, limit: int, since: Optional[str], no_crawl: bool) -> None:
    """Weekly refresh of every town plus the statewide digest (data/digests/<date>.md)."""
    from datetime import date, timedelta
    from .digest import build_digest, last_digest_date, write_digest
    db = DB()
    slugs = [m["slug"] for m in db.municipalities(county)] if county else [m["slug"] for m in db.municipalities()]
    slugs = [s for s in slugs if db.municipality(s)["website"]]
    if not no_crawl:
        from .parallel import run_parallel
        db.close()
        def report(r):
            click.echo(f"{r['slug']}: " + (str(r["result"]) if r["ok"] else f"ERROR {r['error']}"))
        res = run_parallel(slugs, "weekly", workers=workers, progress=report, horizon_days=horizon, fetch_limit=limit)
        click.echo(f"refresh done: {sum(1 for r in res if r['ok'])} ok, {sum(1 for r in res if not r['ok'])} failed")
        db = DB()
    since_d = date.fromisoformat(since) if since else (last_digest_date() or date.today() - timedelta(days=7))
    d = build_digest(db, since=since_d, horizon_days=horizon)
    path = write_digest(d)
    export_from_db(db, SETTINGS.municipalities_file)  # discovered board pages seed next week's run
    click.echo(f"digest: {path} ({d['n_towns_with_data']} towns, {d['n_upcoming_items']} upcoming items, {d['n_recent_decisions']} recent decisions)")


@main.command()
def prune() -> None:
    """Drop towns no longer in data/municipalities.csv and records collected under a since-changed website."""
    from .db import prune as _prune
    db = DB()
    rows = read_csv(SETTINGS.municipalities_file)
    stats = _prune(db, {r["slug"] for r in rows}, {r["slug"]: r.get("website") or "" for r in rows})
    import shutil
    for slug in stats["purged_slugs"]:  # parsed text/JSON of purged records would otherwise feed the digest
        for d in (SETTINGS.parsed_dir / slug, SETTINGS.text_dir / slug, SETTINGS.raw_dir / slug):
            shutil.rmtree(d, ignore_errors=True)
    click.echo(f"{ {k: v for k, v in stats.items() if k != 'purged_slugs'} } purged: {len(stats['purged_slugs'])} towns")


@main.command()
@click.argument("slug", required=False)
def status(slug: Optional[str]) -> None:
    """Show what has been collected."""
    db = DB()
    rows = [db.municipality(slug)] if slug else db.municipalities()
    for m in rows:
        counts = db.doc_type_counts(m["id"])
        st = db.conn.execute("SELECT status, COUNT(*) n FROM documents WHERE muni_id=? GROUP BY status", (m["id"],)).fetchall()
        click.echo(f"{m['slug']:<40} {m['website'] or '-':<45} code={'yes' if m['ecode_url'] else 'no':<3} "
                   f"docs={sum(counts.values()):<5} {dict(counts)} {dict((r['status'], r['n']) for r in st)}")


@main.command("list-docs")
@click.argument("slug")
@click.option("--type", "doc_type")
def list_docs(slug: str, doc_type: Optional[str]) -> None:
    """List discovered documents for a town."""
    db = DB()
    for d in db.documents(db.muni_id(slug), doc_type=doc_type):
        click.echo(f"{d['id']:>6} {d['status']:<10} {d['doc_type'] or '-':<13} {d['board_kind'] or '-':<9} {d['meeting_date'] or '':<10} {d['title'][:60] if d['title'] else '':<60} {d['url']}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
