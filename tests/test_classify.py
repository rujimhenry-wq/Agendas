from njboards.classify import classify_board, classify_doc_type, classify_link, looks_like_document


def test_doc_types():
    assert classify_doc_type("September 12, 2024 Agenda") == "agenda"
    assert classify_doc_type("September 12, 2024 Minutes") == "minutes"
    assert classify_doc_type("Resolution PB-2024-18 Harbor Logistics") == "resolution"
    assert classify_doc_type("Engineer's Review Letter 8/29/24") == "review_letter"
    assert classify_doc_type("Planner's Report") == "review_letter"
    assert classify_doc_type("Site Plan Checklist") == "checklist"
    assert classify_doc_type("Land Use Application Form") == "application"
    assert classify_doc_type("Site Plan Application") == "application"
    assert classify_doc_type("PB-24-07 Maple Street Holdings – Site Plans") == "site_plan"
    assert classify_doc_type("Fee Schedule") == "fee_schedule"
    assert classify_doc_type("Land Use Ordinance Chapter 190") == "ordinance"
    assert classify_doc_type("2024 Meeting Schedule") == "schedule"


def test_board_kinds():
    assert classify_board("Planning Board") == "planning"
    assert classify_board("Zoning Board of Adjustment") == "zoning"
    assert classify_board("Board of Adjustment Minutes") == "zoning"
    assert classify_board("Land Use Board") == "combined"
    assert classify_board("Planning & Zoning Board") == "combined"
    assert classify_board("Township Council") is None


def test_classify_link_dates_and_board():
    lc = classify_link("https://x.org/AgendaCenter/ViewFile/Minutes/_09122024-1234", "Minutes", "Planning Board")
    assert lc.doc_type == "minutes" and lc.meeting_date == "2024-09-12" and lc.year == 2024 and lc.board_kind == "planning"
    lc = classify_link("https://x.org/docs/pb/2024-08-08-minutes.pdf", "August 8, 2024 Minutes", "", page_board="planning")
    assert lc.meeting_date == "2024-08-08" and lc.is_document and lc.board_kind == "planning"
    lc = classify_link("https://x.org/docs/zba/2024-09-18-agenda.pdf", "September 18, 2024 Agenda", "Zoning Board of Adjustment")
    assert lc.board_kind == "zoning"


def test_looks_like_document():
    assert looks_like_document("https://x.org/a/b.pdf")
    assert looks_like_document("https://x.org/DocumentCenter/View/123/Thing")
    assert not looks_like_document("https://x.org/boards/planning-board")
