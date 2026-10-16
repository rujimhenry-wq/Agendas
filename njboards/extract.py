"""Turn fetched files into plain text (data/text/<muni>/<doc_id>.txt)."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

from .config import SETTINGS
from .db import DB
from .util import clean_text

log = logging.getLogger("njboards.extract")


def pdf_text(path: Path) -> tuple[str, int]:
    """Return (text, n_pages). Tries pdfplumber, then pdftotext."""
    text, pages = "", 0
    try:
        import pdfplumber  # type: ignore
        with pdfplumber.open(str(path)) as pdf:
            pages = len(pdf.pages)
            chunks = []
            for p in pdf.pages:
                try:
                    chunks.append(p.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
                except Exception:  # noqa: BLE001 - per-page failures are common with odd PDFs
                    chunks.append("")
            text = "\n\f\n".join(chunks)
    except Exception as e:  # noqa: BLE001
        log.debug("pdfplumber failed on %s: %s", path, e)
    if len(text.strip()) < 50 * max(pages, 1) and shutil.which("pdftotext"):
        try:
            out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=120)
            alt = out.stdout.decode("utf-8", "ignore")
            if len(alt.strip()) > len(text.strip()):
                text = alt
                pages = pages or alt.count("\f") + 1
        except (subprocess.SubprocessError, OSError) as e:
            log.debug("pdftotext failed on %s: %s", path, e)
    return text, pages


def ocr_pdf(path: Path, max_pages: int = 40) -> str:
    """Optional OCR (requires tesseract + pdftoppm). Returns '' if unavailable."""
    if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
        return ""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["pdftoppm", "-r", "200", "-l", str(max_pages), "-png", str(path), f"{td}/p"],
                       capture_output=True, timeout=600)
        out = []
        for img in sorted(Path(td).glob("p*.png")):
            res = subprocess.run(["tesseract", str(img), "-", "--psm", "6"], capture_output=True, timeout=300)
            out.append(res.stdout.decode("utf-8", "ignore"))
        return "\n\f\n".join(out)


def docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError):
        return ""
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return re.sub(r"<[^>]+>", "", xml)


def html_text(raw: bytes) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for t in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        t.decompose()
    main = soup.find("main") or soup.find(id=re.compile("content|main", re.I)) or soup.body or soup
    return main.get_text("\n", strip=True)


def extract_file(path: Path, ocr: Optional[bool] = None) -> dict:
    ext = path.suffix.lower()
    info = {"pages": 0, "method": None, "needs_ocr": False}
    text = ""
    if ext == ".pdf":
        text, pages = pdf_text(path)
        info["pages"] = pages
        info["method"] = "pdf"
        if len(text.strip()) < 80 * max(pages, 1):
            info["needs_ocr"] = True
            if ocr if ocr is not None else SETTINGS.ocr_enabled:
                o = ocr_pdf(path)
                if len(o.strip()) > len(text.strip()):
                    text, info["method"], info["needs_ocr"] = o, "ocr", False
    elif ext == ".docx":
        text, info["method"] = docx_text(path), "docx"
    elif ext in (".html", ".htm"):
        text, info["method"] = html_text(path.read_bytes()), "html"
    elif ext in (".txt", ".rtf"):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw) if ext == ".rtf" else raw
        info["method"] = ext[1:]
    info["text"] = clean_text(text)
    info["chars"] = len(info["text"])
    return info


def extract_all(db: DB, muni_slug: str, force: bool = False, ocr: Optional[bool] = None) -> dict:
    mid = db.muni_id(muni_slug)
    out_dir = SETTINGS.text_dir / muni_slug
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"extracted": 0, "needs_ocr": 0, "empty": 0, "errors": 0}
    for d in db.documents(mid):
        if d["status"] not in ("fetched", "extracted", "parsed") or not d["local_path"]:
            continue
        if d["status"] in ("extracted", "parsed") and d["text_path"] and not force:
            continue
        src = Path(d["local_path"])
        if not src.exists():
            db.update_document(d["id"], status="error", error="local file missing")
            stats["errors"] += 1
            continue
        try:
            info = extract_file(src, ocr=ocr)
        except Exception as e:  # noqa: BLE001
            db.update_document(d["id"], status="error", error=f"extract: {e}")
            stats["errors"] += 1
            continue
        tp = out_dir / f"{d['id']}.txt"
        tp.write_text(info["text"], encoding="utf-8")
        if info["needs_ocr"]:
            stats["needs_ocr"] += 1
        if info["chars"] < 50:
            stats["empty"] += 1
        db.update_document(d["id"], status="extracted", text_path=str(tp),
                           error="needs OCR (scanned)" if info["needs_ocr"] else None)
        stats["extracted"] += 1
    return stats
