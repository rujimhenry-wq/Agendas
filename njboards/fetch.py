"""Download discovered documents into data/raw/<muni>/<board>/<type>/."""
from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .classify import looks_like_document
from .config import SETTINGS
from .db import DB, now_iso
from .http import Fetcher
from .util import sha256_bytes, slugify

log = logging.getLogger("njboards.fetch")

EXT_BY_TYPE = {
    "application/pdf": ".pdf", "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/html": ".html", "text/plain": ".txt", "application/rtf": ".rtf",
}


def _ext_for(url: str, content_type: str, content: bytes) -> str:
    if content[:5] == b"%PDF-":
        return ".pdf"
    if content[:2] == b"PK" and b"word/" in content[:4000]:
        return ".docx"
    ct = content_type.split(";")[0].strip().lower()
    if ct in EXT_BY_TYPE:
        return EXT_BY_TYPE[ct]
    m = re.search(r"\.(pdf|docx?|rtf|txt|html?)(\?|$)", unquote(urlparse(url).path), re.I)
    if m:
        return "." + m.group(1).lower()
    guess = mimetypes.guess_extension(ct) if ct else None
    return guess or ".bin"


def _single_doc_link(html: str, base: str) -> Optional[str]:
    """Landing pages ('ShowDocument', 'View PDF') often wrap one real file."""
    soup = BeautifulSoup(html, "lxml")
    cands = []
    for tag in soup.select("a[href], iframe[src], embed[src], object[data], meta[http-equiv='refresh']"):
        href = tag.get("href") or tag.get("src") or tag.get("data") or ""
        if tag.name == "meta":
            m = re.search(r"url=(.+)", tag.get("content", ""), re.I)
            href = m.group(1).strip("'\" ") if m else ""
        if href and looks_like_document(href) and re.search(r"\.pdf|\.docx?", href, re.I):
            cands.append(urljoin(base, href))
    cands = list(dict.fromkeys(cands))
    return cands[0] if len(cands) == 1 else None


def fetch_document(fetcher: Fetcher, db: DB, doc, muni_slug: str) -> bool:
    """Fetch one document row. Returns True if fetched."""
    r = fetcher.get(doc["url"])
    if not r.ok:
        db.update_document(doc["id"], status="error", error=r.error or f"HTTP {r.status}")
        return False
    content, ctype, final = r.content, r.content_type, r.final_url
    if r.is_html and not looks_like_document(final, ctype):
        inner = _single_doc_link(r.text, final)
        if inner and inner != doc["url"]:
            r2 = fetcher.get(inner)
            if r2.ok and not r2.is_html:
                content, ctype, final = r2.content, r2.content_type, r2.final_url
    ext = _ext_for(final, ctype, content)
    date = doc["meeting_date"] or (str(doc["year"]) if doc["year"] else "undated")
    name = slugify((doc["title"] or unquote(urlparse(final).path).rsplit("/", 1)[-1])[:80])
    folder = SETTINGS.raw_dir / muni_slug / (doc["board_kind"] or "unknown") / (doc["doc_type"] or "other")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{date}_{name}_{doc['id']}{ext}"
    path.write_bytes(content)
    db.update_document(doc["id"], status="fetched", local_path=str(path), sha256=sha256_bytes(content),
                       content_type=ctype, bytes=len(content), fetched_at=now_iso(), error=None)
    return True


def fetch_all(fetcher: Fetcher, db: DB, muni_slug: str, doc_types: Optional[list[str]] = None,
              limit: Optional[int] = None, refetch_errors: bool = False) -> dict:
    mid = db.muni_id(muni_slug)
    rows = [d for d in db.documents(mid) if d["status"] == "discovered" or (refetch_errors and d["status"] == "error")]
    if doc_types:
        rows = [d for d in rows if d["doc_type"] in doc_types]
    stats = {"fetched": 0, "errors": 0, "skipped": 0}
    for i, d in enumerate(rows):
        if limit and i >= limit:
            stats["skipped"] += len(rows) - i
            break
        ok = fetch_document(fetcher, db, d, muni_slug)
        stats["fetched" if ok else "errors"] += 1
    return stats
