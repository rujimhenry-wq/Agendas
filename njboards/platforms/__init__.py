"""Platform adapters. Each adapter exposes:

    detect(html, url) -> bool
    list_documents(fetcher, page_url, html, board_kind) -> list[DocLink]

Municipal sites in NJ overwhelmingly run on a handful of CMS products
(CivicPlus, Revize, GovOffice, Municode/CivicClerk, Granicus, Edmunds...),
most of which just expose plain links the generic crawler handles. The
adapters here cover the ones with structured listings worth using directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DocLink:
    url: str
    title: str
    doc_type: str = "other"
    board_kind: Optional[str] = None
    meeting_date: Optional[str] = None
    year: Optional[int] = None
    context: str = ""
    platform: str = "generic"
    extra: dict = field(default_factory=dict)


from . import civicplus, civicclerk, granicus, ecodedocs  # noqa: E402

ADAPTERS = [civicplus, civicclerk, granicus, ecodedocs]


def detect_platform(html: str, url: str) -> Optional[str]:
    for mod in ADAPTERS:
        if mod.detect(html, url):
            return mod.NAME
    return None
