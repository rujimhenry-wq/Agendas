from conftest import fx

from njboards.parse import parse_document
from njboards.parse.common import (app_numbers, block_lots, app_types, decision, extract_waivers, categorize,
                                   split_numbered, waiver_disposition)
from njboards.parse.professionals import extract_professionals, board_roster


def test_identifiers():
    s = "Application No. PB-24-07 – Maple Street Holdings, LLC – Block 412, Lot 3.01 – 118 Maple Street – C-2 Zone"
    assert app_numbers(s) == ["PB-24-07"]
    assert block_lots(s) == [{"block": "412", "lots": "3.01"}]
    assert block_lots("Block 7, Lots 12 & 13") == [{"block": "7", "lots": "12 & 13"}]
    assert "preliminary & final major site plan" in app_types("Preliminary and Final Major Site Plan with bulk variances")
    assert "bulk variance (c)" in app_types("c(2) variances for front yard setback")
    assert "minor site plan" in app_types("Minor Site Plan for a warehouse addition")
    assert decision("The application was approved with conditions.") == "approved_with_conditions"
    assert decision("The application was carried to October 10, 2024") == "carried"
    assert decision("the variance is hereby denied") == "denied"


def test_waiver_dispositions():
    assert waiver_disposition("We have no objection to the Board granting this waiver.") == "granted"
    assert waiver_disposition("We do not recommend that this waiver be granted; a traffic impact study must be submitted.") == "denied"
    assert waiver_disposition("The Board denied the design waiver.") == "denied"
    assert waiver_disposition("The waiver was granted.") == "granted"
    assert waiver_disposition("The applicant requested a waiver from checklist item 14.") == "requested"
    assert waiver_disposition("The Board Engineer did not support the waiver for the Traffic Impact Study.") == "denied"


def test_extract_waivers_from_minutes():
    ws = extract_waivers(fx("minutes.txt"), source="minutes")
    denied = [w for w in ws if w.disposition == "denied"]
    granted = [w for w in ws if w.disposition == "granted"]
    assert any(w.topic == "traffic impact study" for w in denied)
    assert any(w.kind == "design" and "buffer" in w.text.lower() for w in denied)
    assert any(w.topic == "landscape plan" for w in granted)
    # item numbers picked up from the request sentence context
    assert any("14" in w.items and "22" in w.items for w in ws)


def test_professionals_minutes():
    pros = extract_professionals(fx("minutes.txt"))
    by = {p.name: p for p in pros}
    assert by["Thomas J. Albright"].side == "board" and by["Thomas J. Albright"].role == "engineer"
    assert "P.E." in by["Thomas J. Albright"].credentials and "Albright & Keller" in by["Thomas J. Albright"].firm
    assert by["Susan Hale"].side == "board" and by["Susan Hale"].role == "planner"
    assert by["Karen M. Whitfield"].side == "board" and by["Karen M. Whitfield"].role == "attorney"
    assert by["James R. Kowalski"].side == "applicant" and by["James R. Kowalski"].testified
    assert by["Anthony Petrillo"].role == "planner" and by["Anthony Petrillo"].testified
    assert by["Laura Bennett"].role == "architect" and by["Laura Bennett"].testified
    assert by["Peter Nowak"].testified and "Nowak Civil Engineering" in by["Peter Nowak"].firm
    assert by["Michael D. Torres"].role == "attorney" and by["Michael D. Torres"].side == "applicant"
    roster = board_roster(fx("minutes.txt")[:3000])
    assert "Thomas J. Albright" in roster["engineer"] and "Susan Hale" in roster["planner"]


def test_parse_minutes_items():
    res = parse_document("minutes", fx("minutes.txt"), "September 12, 2024 Minutes")
    assert res["meeting_date"] == "2024-09-12" and res["board_kind"] == "planning"
    apps = {a for it in res["items"] for a in it["app_numbers"]}
    assert {"PB-24-07", "PB-24-09"} <= apps
    first = next(it for it in res["items"] if "PB-24-07" in it["app_numbers"])
    assert first["applicant"] == "Maple Street Holdings, LLC"
    assert first["is_site_plan"] and first["decision"] == "carried"
    assert any(r["role"] == "engineer" and "drainage" in r["text"].lower() for r in first["board_remarks"])
    second = next(it for it in res["items"] if "PB-24-09" in it["app_numbers"])
    assert second["decision"] == "approved_with_conditions" and second["vote"] == "6-0"
    assert any(w["disposition"] == "denied" and w["kind"] == "design" for w in second["waivers"])


def test_parse_review_letter():
    res = parse_document("review_letter", fx("review_letter.txt"), "Engineer's Review Letter")
    assert res["app_numbers"] == ["PB-24-07"]
    assert res["block_lots"][0] == {"block": "412", "lots": "3.01"}
    assert res["author_type"] == "engineer"
    assert res["author"]["name"] == "Thomas J. Albright"
    assert res["completeness"] == "complete"
    assert res["n_comments"] == 21
    cats = {c["number"]: c["category"] for c in res["comments"]}
    assert cats["4"] == "stormwater" and cats["5"] == "stormwater"
    assert cats["11"] == "parking" and cats["16"] == "lighting"
    assert cats["19"] == "outside_agencies"
    c11 = next(c for c in res["comments"] if c["number"] == "11")
    assert c11["requires_variance"] and c11["requires_testimony"]
    ws = {w["topic"]: w["disposition"] for w in res["waivers"] if w["topic"]}
    assert ws.get("traffic impact study") == "denied"
    assert ws.get("environmental impact statement") == "granted"
    assert ws.get("aerial photograph") == "granted"


def test_parse_resolution():
    res = parse_document("resolution", fx("resolution.txt"), "Resolution PB-2024-18")
    assert "PB-24-09" in res["app_numbers"]
    assert res["decision"] in ("approved", "approved_with_conditions")
    assert res["n_conditions"] == 8
    cats = {c["number"]: c["category"] for c in res["conditions"]}
    assert cats["2"] == "outside_agencies" and cats["6"] == "lighting" and cats["8"] == "operations"
    assert any(c["standard"] for c in res["conditions"])
    assert any(w["name"] == "Peter Nowak" for w in res["witnesses"])
    denied = [w for w in res["waivers"] if w["disposition"] == "denied"]
    assert any(w["kind"] == "design" for w in denied)
    assert any(w["disposition"] == "granted" and w["topic"] == "landscape plan" for w in res["waivers"])


def test_parse_checklist():
    res = parse_document("checklist", fx("checklist.txt"), "Site Plan Checklist")
    assert res["n_items"] == 22
    by = {i["number"]: i for i in res["items"]}
    assert by["1"]["waivable"] is False and by["14"]["waivable"] is True
    assert by["14"]["topic"] == "environmental impact statement"
    assert by["22"]["topic"] == "traffic impact study"
    assert by["7"]["checklist"] == "preliminary major site plan"


def test_split_numbered_sections():
    items = split_numbered("STORMWATER\n1. Revise the drainage calcs for the basin.\ncontinued line here.\n2) Provide a maintenance plan for the system.\n")
    assert len(items) == 2 and items[0]["section"] == "STORMWATER" and "continued" in items[0]["text"]
    assert categorize("Street trees shall be provided along the frontage")[0] == "landscaping"
