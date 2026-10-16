import json
from pathlib import Path

from conftest import StubFetcher, fx, make_pdf

from njboards.db import DB
from njboards.discover import discover, find_code_links
from njboards.platforms import civicplus
from njboards.ecode import fetch_code
from njboards.extract import extract_file
from njboards.fetch import fetch_all
from njboards.analyze import analyze_municipality
from njboards.report import render_markdown, write_summary_csv
from njboards.pipeline import run_discover, run_parse
from njboards.extract import extract_all

HOME = "https://www.example-nj.org/"


def _routes():
    return {
        HOME: (fx("home.html"), "text/html"),
        HOME + "boards/planning-board": (fx("planning_board.html"), "text/html"),
        HOME + "boards/zoning-board-of-adjustment": (fx("zoning_board.html"), "text/html"),
        HOME + "boards/planning-board/archive": ("<html><body><h1>Planning Board Minutes Archive</h1><a href='/docs/pb/2023-12-14-minutes.pdf'>December 14, 2023 Minutes</a></body></html>", "text/html"),
    }


def test_find_code_links():
    assert find_code_links(fx("home.html")) == {"ecode_id": "EX1234", "ecode_url": "https://ecode360.com/EX1234", "code_platform": "ecode360"}
    assert find_code_links('<a href="https://library.municode.com/nj/some_town/codes/code_of_ordinances">Code</a>')["code_platform"] == "municode"


def test_discover_generic_site():
    f = StubFetcher(_routes())
    disc = discover(f, HOME)
    kinds = {b["kind"] for b in disc.boards}
    assert {"planning", "zoning"} <= kinds
    assert disc.ecode_id == "EX1234"
    by_type = {}
    for d in disc.docs:
        by_type.setdefault(d.doc_type, []).append(d)
    assert len(by_type["minutes"]) >= 5  # 3 pb (one is 2018, still discovered) + archive + zba
    assert any(d.doc_type == "review_letter" and "engineer" in d.url for d in disc.docs)
    assert any(d.doc_type == "review_letter" and "planner" in d.url for d in disc.docs)
    assert any(d.doc_type == "site_plan" for d in disc.docs)
    assert any(d.doc_type == "checklist" for d in disc.docs)
    assert any(d.doc_type == "application" for d in disc.docs)
    assert any(d.doc_type == "resolution" for d in disc.docs)
    zba = [d for d in disc.docs if "/zba/" in d.url]
    assert zba and all(d.board_kind == "zoning" for d in zba)
    # non land-use pages were not crawled
    assert not any("environmental-commission" in u for u in f.requested)


def test_civicplus_adapter():
    base = "https://www.cp-town.org"
    f = StubFetcher({
        base + "/AgendaCenter": (fx("agenda_center.html"), "text/html"),
        base + "/AgendaCenter/Search/?term=&CIDs=12,&startDate=&endDate=&dateRange=&dateSelector=": (fx("agenda_center_search.html"), "text/html"),
        base + "/AgendaCenter/Search/?term=&CIDs=14,&startDate=&endDate=&dateRange=&dateSelector=": ("<html></html>", "text/html"),
    })
    cats = civicplus.agenda_center_categories(f, base + "/")
    assert {c["kind"] for c in cats} == {"planning", "zoning"}
    docs = civicplus.list_documents(f, base + "/", fx("agenda_center.html"), None)
    agendas = [d for d in docs if d.doc_type == "agenda"]
    assert len(agendas) == 2 and agendas[0].meeting_date == "2024-09-12" and agendas[0].board_kind == "planning"
    assert any(d.doc_type == "minutes" for d in docs)
    assert any("/ViewFile/Item/" in d.url for d in docs)


def test_extract_pdf_and_docx(tmp_path):
    pdf = make_pdf(tmp_path / "m.pdf", fx("minutes.txt"))
    info = extract_file(pdf)
    assert "Maple Street Holdings" in info["text"] and info["pages"] >= 1 and not info["needs_ocr"]


def test_full_offline_pipeline(data_dir, tmp_path):
    # build PDFs for the documents the board page links to
    docs = {
        "docs/pb/2024-09-12-minutes.pdf": fx("minutes.txt"),
        "docs/pb/2024-08-08-minutes.pdf": fx("minutes.txt").replace("September 12, 2024", "August 8, 2024"),
        "docs/pb/2024-09-12-agenda.pdf": "PLANNING BOARD AGENDA\nSeptember 12, 2024\n\nPUBLIC HEARINGS\n1. Application No. PB-24-07 – Maple Street Holdings, LLC – Block 412, Lot 3.01 – Preliminary and Final Major Site Plan\n2. Application No. PB-24-09 – Harbor Logistics, Inc. – Block 7, Lots 12 & 13 – Minor Site Plan\n",
        "docs/pb/PB-24-07/engineer-review-letter-8-29-24.pdf": fx("review_letter.txt"),
        "docs/pb/resolutions/PB-2024-18.pdf": fx("resolution.txt"),
        "docs/forms/site-plan-checklist.pdf": fx("checklist.txt"),
    }
    routes = _routes()
    for rel, text in docs.items():
        p = make_pdf(tmp_path / rel.replace("/", "_"), text)
        routes[HOME + rel] = (p.read_bytes(), "application/pdf")
    routes["https://ecode360.com/EX1234"] = (fx("ecode_toc.html"), "text/html")
    routes["https://ecode360.com/print/EX1234?guid=10000190"] = (fx("ecode_chapter.html"), "text/html")
    routes["https://ecode360.com/print/EX1234?guid=10000200"] = ("<html><body><h1>Chapter 200 Stormwater Management</h1><p>§ 200-1 Purpose.</p><p>Stormwater drainage detention text.</p></body></html>", "text/html")
    f = StubFetcher(routes)

    db = DB()
    db.upsert_municipality("example-somerset", "Example Township", county="Somerset", website=HOME)
    summary = run_discover(f, db, "example-somerset")
    assert summary["ecode_url"] == "https://ecode360.com/EX1234"
    assert summary["documents_new"] >= 10
    # 2018 minutes dropped by min_year
    assert not any(d["year"] == 2018 for d in db.documents(db.muni_id("example-somerset")))

    st = fetch_all(f, db, "example-somerset")
    assert st["fetched"] == len(docs)
    assert st["errors"] >= 1  # links with no stub (site plans, planner report...) are recorded as errors, not crashes
    ex = extract_all(db, "example-somerset")
    assert ex["extracted"] == len(docs) and ex["needs_ocr"] == 0
    ps = run_parse(db, "example-somerset")
    assert ps["parsed"] == len(docs) and ps["errors"] == 0

    code = fetch_code(f, "example-somerset", "https://ecode360.com/EX1234")
    assert code["n_chapters_fetched"] == 2
    assert len(code["checklist_items"]) >= 3
    assert any("shall not be waived" in w["text"] for w in code["waiver_provisions"])
    assert code["attachments"] and "Checklist" in code["attachments"][0]["title"]
    assert "parking" in code["sections_by_topic"]

    profile = analyze_municipality(db, "example-somerset")
    av = profile["availability"]
    assert av["posts_review_letters"] and av["posts_minutes"] and av["posts_resolutions"] and av["posts_site_plans"]
    pr = profile["professionals"]
    assert "Thomas J. Albright" in " ".join(pr["board_by_role"].get("engineer", []))
    regs = {r["name"]: r for r in pr["applicant_side_regulars"]}
    assert "James R. Kowalski" in regs and regs["James R. Kowalski"]["n_meetings"] >= 2
    assert "Peter Nowak" in regs
    wv = profile["waivers"]
    assert wv["denied_topics"].get("traffic impact study", 0) >= 1
    assert any("Traffic impact study" in t for r in wv["denied"] for t in r["item_text"])  # item 22 mapped via checklist
    assert wv["granted_topics"].get("landscape plan", 0) >= 1
    rc = profile["review_comments"]
    assert rc["n_review_letters"] == 1 and rc["category_counts"]["stormwater"] >= 4
    cd = profile["conditions"]
    assert cd["n_resolutions"] == 1 and cd["common_conditions"]
    ap = profile["applications"]
    assert ap["n_site_plan_applications"] >= 2
    assert profile["zoning_code"]["checklist_items_in_code"] >= 3

    md = render_markdown(profile)
    assert "Professionals who regularly testify" in md and "Kowalski" in md and "traffic impact study" in md
    csv_path = write_summary_csv([profile])
    assert "Albright" in csv_path.read_text()


def test_cli_smoke(data_dir, monkeypatch):
    from click.testing import CliRunner
    from njboards.cli import main
    csv = data_dir / "municipalities.csv"
    csv.write_text("slug,name,county,muni_type,website,ecode_id,ecode_url,planning_url,zoning_url,notes\n"
                   "t-x,T,X,Township,https://t.example/,,,,,\n")
    r = CliRunner().invoke(main, ["--data-dir", str(data_dir), "init"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(main, ["--data-dir", str(data_dir), "status"])
    assert r.exit_code == 0 and "t-x" in r.output
    r = CliRunner().invoke(main, ["--data-dir", str(data_dir), "analyze", "t-x"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(main, ["--data-dir", str(data_dir), "report", "t-x"])
    assert r.exit_code == 0 and (data_dir / "reports" / "t-x" / "profile.md").exists()
