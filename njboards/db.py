"""SQLite registry of municipalities, boards and documents.

Parsed results are stored as JSON files (data/parsed/<muni>/<doc_id>.json) and
aggregated by analyze.py; the database only tracks what exists and where.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import SETTINGS

SCHEMA = """
CREATE TABLE IF NOT EXISTS municipalities (
    id INTEGER PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    county TEXT,
    muni_type TEXT,
    website TEXT,
    ecode_id TEXT,
    ecode_url TEXT,
    code_platform TEXT,
    notes TEXT,
    status TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS boards (
    id INTEGER PRIMARY KEY,
    muni_id INTEGER NOT NULL REFERENCES municipalities(id),
    kind TEXT NOT NULL,            -- planning | zoning | combined | unknown
    name TEXT,
    url TEXT NOT NULL,
    platform TEXT,
    source TEXT,                   -- discovered | manual
    UNIQUE(muni_id, url)
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    muni_id INTEGER NOT NULL REFERENCES municipalities(id),
    board_id INTEGER REFERENCES boards(id),
    board_kind TEXT,
    url TEXT NOT NULL,
    title TEXT,
    doc_type TEXT,                 -- agenda | minutes | resolution | review_letter | ...
    meeting_date TEXT,
    year INTEGER,
    local_path TEXT,
    text_path TEXT,
    sha256 TEXT,
    content_type TEXT,
    bytes INTEGER,
    status TEXT,                   -- discovered | fetched | extracted | parsed | error | skipped
    error TEXT,
    discovered_at TEXT,
    fetched_at TEXT,
    UNIQUE(muni_id, url)
);
CREATE INDEX IF NOT EXISTS idx_docs_muni_type ON documents(muni_id, doc_type);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    muni_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    kind TEXT,                     -- board_page | listing | home | other
    crawled_at TEXT,
    UNIQUE(muni_id, url)
);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else SETTINGS.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # --- municipalities -------------------------------------------------
    def upsert_municipality(self, slug: str, name: str, **fields: Any) -> int:
        fields = {k: v for k, v in fields.items() if v is not None}
        cols = ["slug", "name", "updated_at"] + list(fields)
        vals = [slug, name, now_iso()] + list(fields.values())
        upd = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "slug")
        with self.tx() as c:
            c.execute(
                f"INSERT INTO municipalities ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT(slug) DO UPDATE SET {upd}",
                vals,
            )
        return self.muni_id(slug)

    def muni_id(self, slug: str) -> int:
        row = self.conn.execute("SELECT id FROM municipalities WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise KeyError(f"unknown municipality slug: {slug}")
        return int(row["id"])

    def municipality(self, slug: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM municipalities WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise KeyError(f"unknown municipality slug: {slug}")
        return row

    def municipalities(self, county: Optional[str] = None) -> list[sqlite3.Row]:
        if county:
            return self.conn.execute(
                "SELECT * FROM municipalities WHERE lower(county)=lower(?) ORDER BY name", (county,)
            ).fetchall()
        return self.conn.execute("SELECT * FROM municipalities ORDER BY name").fetchall()

    def set_muni_fields(self, slug: str, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE municipalities SET {sets}, updated_at=? WHERE slug=?",
                      [*fields.values(), now_iso(), slug])

    # --- boards -----------------------------------------------------------
    def upsert_board(self, muni_id: int, kind: str, name: str, url: str,
                     platform: Optional[str] = None, source: str = "discovered") -> int:
        with self.tx() as c:
            c.execute(
                "INSERT INTO boards (muni_id, kind, name, url, platform, source) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(muni_id, url) DO UPDATE SET kind=excluded.kind, name=excluded.name, "
                "platform=COALESCE(excluded.platform, boards.platform)",
                (muni_id, kind, name, url, platform, source),
            )
        row = self.conn.execute("SELECT id FROM boards WHERE muni_id=? AND url=?", (muni_id, url)).fetchone()
        return int(row["id"])

    def boards(self, muni_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM boards WHERE muni_id=? ORDER BY kind, name", (muni_id,)).fetchall()

    # --- pages ------------------------------------------------------------
    def record_page(self, muni_id: int, url: str, title: str, kind: str) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO pages (muni_id, url, title, kind, crawled_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(muni_id, url) DO UPDATE SET title=excluded.title, kind=excluded.kind, crawled_at=excluded.crawled_at",
                (muni_id, url, title, kind, now_iso()),
            )

    # --- documents --------------------------------------------------------
    def add_document(self, muni_id: int, url: str, title: str, doc_type: str,
                     board_id: Optional[int], board_kind: Optional[str],
                     meeting_date: Optional[str], year: Optional[int]) -> int:
        with self.tx() as c:
            c.execute(
                "INSERT INTO documents (muni_id, board_id, board_kind, url, title, doc_type, meeting_date, year, status, discovered_at) "
                "VALUES (?,?,?,?,?,?,?,?,'discovered',?) "
                "ON CONFLICT(muni_id, url) DO UPDATE SET "
                "  title=COALESCE(documents.title, excluded.title), "
                "  doc_type=CASE WHEN documents.doc_type IS NULL OR documents.doc_type='other' THEN excluded.doc_type ELSE documents.doc_type END, "
                "  board_id=COALESCE(documents.board_id, excluded.board_id), "
                "  board_kind=COALESCE(documents.board_kind, excluded.board_kind), "
                "  meeting_date=COALESCE(documents.meeting_date, excluded.meeting_date), "
                "  year=COALESCE(documents.year, excluded.year)",
                (muni_id, board_id, board_kind, url, title, doc_type, meeting_date, year, now_iso()),
            )
        row = self.conn.execute("SELECT id FROM documents WHERE muni_id=? AND url=?", (muni_id, url)).fetchone()
        return int(row["id"])

    def update_document(self, doc_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE documents SET {sets} WHERE id=?", [*fields.values(), doc_id])

    def documents(self, muni_id: int, doc_type: Optional[str] = None,
                  status: Optional[str] = None, board_kind: Optional[str] = None) -> list[sqlite3.Row]:
        q = "SELECT * FROM documents WHERE muni_id=?"
        args: list[Any] = [muni_id]
        if doc_type:
            q += " AND doc_type=?"
            args.append(doc_type)
        if status:
            q += " AND status=?"
            args.append(status)
        if board_kind:
            q += " AND board_kind=?"
            args.append(board_kind)
        q += " ORDER BY meeting_date DESC, id"
        return self.conn.execute(q, args).fetchall()

    def document(self, doc_id: int) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def doc_type_counts(self, muni_id: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT doc_type, COUNT(*) n FROM documents WHERE muni_id=? GROUP BY doc_type", (muni_id,)
        ).fetchall()
        return {r["doc_type"] or "other": int(r["n"]) for r in rows}


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
