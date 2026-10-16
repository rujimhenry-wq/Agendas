"""Municode Meetings / CivicClerk portals (https://<town>.portal.civicclerk.com).

The portal is a JS app; the backing OData API is public:
  https://<town>.api.civicclerk.com/v1/Events?$filter=...&$orderby=startDateTime desc
Each event lists publishedFiles with fileId / type (Agenda, Minutes, Packet...).
File stream: https://<town>.api.civicclerk.com/v1/Meetings/GetMeetingFileStream(fileId=<id>,plainText=false)
"""
from __future__ import annotations

import re
from typing import Optional

from ..classify import classify_board, classify_doc_type
from . import DocLink

NAME = "civicclerk"
_PORTAL = re.compile(r"https?://([a-z0-9\-]+)\.portal\.civicclerk\.com", re.I)


def detect(html: str, url: str) -> bool:
    return bool(_PORTAL.search(url) or _PORTAL.search(html))


def portal_slug(url_or_html: str) -> Optional[str]:
    m = _PORTAL.search(url_or_html)
    return m.group(1).lower() if m else None


def list_documents(fetcher, page_url: str, html: str, board_kind: Optional[str]) -> list[DocLink]:
    slug = portal_slug(page_url) or portal_slug(html)
    if not slug:
        return []
    api = f"https://{slug}.api.civicclerk.com/v1"
    out: list[DocLink] = []
    skip = 0
    while skip < 2000:
        url = f"{api}/Events?$orderby=startDateTime desc&$top=100&$skip={skip}"
        data = fetcher.get_json(url)
        if not data:
            break
        events = data.get("value", data if isinstance(data, list) else [])
        if not events:
            break
        for ev in events:
            name = ev.get("eventName") or ev.get("categoryName") or ""
            kind = classify_board(name)
            if not kind:
                continue
            if board_kind and kind not in (board_kind, "combined"):
                continue
            start = (ev.get("startDateTime") or "")[:10]
            year = int(start[:4]) if start[:4].isdigit() else None
            if year and year < fetcher.s.min_year:
                continue
            for f in ev.get("publishedFiles") or []:
                fid = f.get("fileId") or f.get("id")
                if not fid:
                    continue
                ftype = (f.get("type") or f.get("name") or "").lower()
                dt = classify_doc_type(ftype) if ftype else "other"
                if dt == "other" and "packet" in ftype:
                    dt = "site_plan"
                out.append(DocLink(
                    url=f"{api}/Meetings/GetMeetingFileStream(fileId={fid},plainText=false)",
                    title=f"{name} {ftype} {start}".strip(), doc_type=dt, board_kind=kind,
                    meeting_date=start or None, year=year, platform=NAME,
                    extra={"event_id": ev.get("id")},
                ))
        if len(events) < 100:
            break
        skip += 100
    return out
