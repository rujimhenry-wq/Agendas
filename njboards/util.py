"""Small shared helpers: slugs, dates, hashing, text normalization."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import date, datetime
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

from dateutil import parser as dateparser

MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
MONTHS_ABBR = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"

DATE_PATTERNS = [
    # September 14, 2024  /  Sept. 14 2024
    re.compile(rf"\b((?:{MONTHS}|{MONTHS_ABBR})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{{2}})\b", re.I),
    # 14 September 2024
    re.compile(rf"\b(\d{{1,2}}\s+(?:{MONTHS}|{MONTHS_ABBR})\.?,?\s+(?:19|20)\d{{2}})\b", re.I),
    # 09/14/2024, 9-14-24, 2024-09-14
    re.compile(r"\b((?:19|20)\d{2}[-_./]\d{1,2}[-_./]\d{1,2})\b"),
    re.compile(r"\b(\d{1,2}[-_./]\d{1,2}[-_./](?:19|20)\d{2})\b"),
    re.compile(r"\b(\d{1,2}[-_./]\d{1,2}[-_./]\d{2})\b"),
    # CivicPlus style _09142024-1234 or 09142024 run together
    re.compile(r"(?<!\d)((?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:19|20)\d{2})(?!\d)"),
]


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "x"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha1_str(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "ignore")).hexdigest()


def normalize_url(url: str, base: Optional[str] = None) -> str:
    if base:
        url = urljoin(base, url)
    p = urlparse(url.strip())
    # drop fragments, normalize scheme/host case, sort query
    q = urlencode(sorted(parse_qsl(p.query, keep_blank_values=True)))
    path = p.path or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, p.params, q, ""))


def same_site(a: str, b: str) -> bool:
    ha, hb = urlparse(a).netloc.lower(), urlparse(b).netloc.lower()
    strip = lambda h: h[4:] if h.startswith("www.") else h
    return strip(ha) == strip(hb)


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower()


def parse_date_token(tok: str) -> Optional[date]:
    tok = tok.strip()
    m = re.fullmatch(r"(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])((?:19|20)\d{2})", tok)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    tok2 = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", tok)
    tok2 = tok2.replace("_", "/").replace(".", "/") if re.search(r"\d[._]\d", tok2) else tok2
    try:
        d = dateparser.parse(tok2, dayfirst=False, fuzzy=False)
    except (ValueError, OverflowError):
        return None
    if d is None:
        return None
    if d.year < 1990 or d.year > datetime.now().year + 2:
        return None
    return d.date()


def find_dates(text: str, limit: int = 20) -> list[date]:
    """Return dates found in text, in order of appearance (deduped)."""
    found: list[date] = []
    seen = set()
    for pat in DATE_PATTERNS:
        for m in pat.finditer(text):
            d = parse_date_token(m.group(1))
            if d and d not in seen:
                seen.add(d)
                found.append((m.start(), d))  # type: ignore[arg-type]
            if len(found) >= limit * 3:
                break
    found.sort(key=lambda t: t[0])  # type: ignore[index]
    return [d for _, d in found][:limit]  # type: ignore[misc]


def first_date(text: str) -> Optional[date]:
    ds = find_dates(text, limit=1)
    return ds[0] if ds else None


def clean_text(s: str) -> str:
    s = s.replace(" ", " ").replace("’", "'").replace("“", '"').replace("”", '"')
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def sentences(text: str) -> list[str]:
    """Crude sentence splitter that is robust to 'No.', 'Mr.', 'P.E.' etc.

    Also treats resolution clauses ('...; and\nWHEREAS, ...'), numbered item
    starts and punctuation-terminated lines as boundaries, while joining
    wrapped lines from PDF extraction.
    """
    text = text.replace("\f", "\n")
    # resolution / ordinance clause boundaries
    text = re.sub(r";\s*(?:and|or)?\s*(?=\n?\s*(?:WHEREAS|NOW,?\s+THEREFORE|BE\s+IT|AND\s+WHEREAS))", ". ", text)
    text = re.sub(r"\n\s*(?=(?:WHEREAS|NOW,?\s+THEREFORE|BE\s+IT\s+(?:FURTHER\s+)?RESOLVED)\b)", ". ", text)
    # a line ending in punctuation ends a sentence; a numbered item start begins one
    text = re.sub(r"([.!?;:])[ \t]*\n+", r"\1 ", text)
    text = re.sub(r"\n+[ \t]*(?=(?:\d{1,3}|[A-Za-z])[.)]\s+\S)", ". ", text)
    text = re.sub(r"\s+", " ", text)
    protected = re.sub(
        r"\b(Mr|Mrs|Ms|Dr|No|Nos|St|Esq|Jr|Sr|Inc|Co|Ltd|vs|Sec|Art|Blk|Lt|Lts|P\.E|P\.P|R\.A|L\.S|P\.L\.S|A\.I\.A|Twp|Bd|approx|Rev|Ord|a\.m|p\.m|sq|ft|et\s+al|c|d|N\.J\.S\.A|N\.J\.A\.C|P\.L)\.",
        lambda m: m.group(0).replace(".", "<DOT>"),
        text,
    )
    protected = re.sub(r"\b([A-Z])\.", r"\1<DOT>", protected)  # initials
    protected = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", protected)
    parts = re.split(r"(?<=[.!?;])\s+(?=[A-Z0-9(\"'§])", protected)
    out = []
    for p in parts:
        p = p.replace("<DOT>", ".").strip()
        if re.fullmatch(r"[.;\s]*", p):
            continue
        out.append(p)
    return out


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def uniq(items: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for i in items:
        k = i.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(i)
    return out
