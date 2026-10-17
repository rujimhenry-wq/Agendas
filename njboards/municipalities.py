"""Municipality registry: load the seed CSV, import the full NJ list, find websites."""
from __future__ import annotations

import csv
import logging
import re
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

from bs4 import BeautifulSoup

from .config import SETTINGS
from .db import DB
from .http import Fetcher
from .util import slugify

log = logging.getLogger("njboards.munis")

CSV_FIELDS = ["slug", "name", "county", "muni_type", "website", "ecode_id", "ecode_url",
              "planning_url", "zoning_url", "notes", "wiki_url"]

WIKI_LIST_URL = "https://en.wikipedia.org/wiki/List_of_municipalities_in_New_Jersey"
NJ_COUNTIES = [
    "Atlantic", "Bergen", "Burlington", "Camden", "Cape May", "Cumberland", "Essex", "Gloucester",
    "Hudson", "Hunterdon", "Mercer", "Middlesex", "Monmouth", "Morris", "Ocean", "Passaic", "Salem",
    "Somerset", "Sussex", "Union", "Warren",
]


def make_slug(name: str, county: Optional[str] = None) -> str:
    base = slugify(name)
    if county:
        base = f"{base}-{slugify(county)}"
    return base


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if not r.get("slug"):
            r["slug"] = make_slug(r["name"], r.get("county"))
    return rows


def write_csv(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in CSV_FIELDS})


def load_into_db(db: DB, path: Optional[Path] = None) -> int:
    """Load/merge the municipalities CSV into the database. Returns count."""
    rows = read_csv(path or SETTINGS.municipalities_file)
    n = 0
    for r in rows:
        if not r.get("name"):
            continue
        db.upsert_municipality(
            r["slug"], r["name"].strip(), county=r.get("county") or None, muni_type=r.get("muni_type") or None,
            website=(r.get("website") or "").strip() or None, ecode_id=(r.get("ecode_id") or "").strip() or None,
            ecode_url=(r.get("ecode_url") or "").strip() or None, notes=r.get("notes") or None,
        )
        mid = db.muni_id(r["slug"])
        for col, kind in (("planning_url", "planning"), ("zoning_url", "zoning")):
            u = (r.get(col) or "").strip()
            if u:
                db.upsert_board(mid, kind, f"{kind.title()} Board (manual)", u, source="manual")
        n += 1
    return n


def export_from_db(db: DB, path: Path) -> int:
    existing = {r["slug"]: r for r in read_csv(path)}
    rows = []
    for m in db.municipalities():
        boards = {b["kind"]: b["url"] for b in db.boards(m["id"])}
        rows.append({
            "slug": m["slug"], "name": m["name"], "county": m["county"], "muni_type": m["muni_type"],
            "website": m["website"], "ecode_id": m["ecode_id"], "ecode_url": m["ecode_url"],
            "planning_url": boards.get("planning", ""), "zoning_url": boards.get("zoning", ""),
            "notes": m["notes"], "wiki_url": existing.get(m["slug"], {}).get("wiki_url", ""),
        })
    write_csv(path, rows)
    return len(rows)


# --- full list import -------------------------------------------------------

def _match_key(name: str, county: str) -> str:
    base = re.sub(r"\b(township|borough|city|town|village|of|the)\b", " ", name, flags=re.I)
    return slugify(base) + "|" + slugify(county or "")


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """Merge rows that describe the same municipality under different slugs
    ('Hamilton Township' vs 'Hamilton', Mercer). The Wikipedia-derived row keeps
    its slug; the other row's website / code / board URLs are carried over."""
    by_key: dict[str, dict] = {}
    for r in rows:
        k = _match_key(r["name"], r.get("county", ""))
        if k not in by_key:
            by_key[k] = dict(r)
            continue
        keep = by_key[k]
        # prefer the row that came from the Wikipedia list (has wiki_url)
        if r.get("wiki_url") and not keep.get("wiki_url"):
            keep, r = dict(r), keep
        for col in CSV_FIELDS:
            if not keep.get(col) and r.get(col):
                keep[col] = r[col]
        by_key[k] = keep
    return list(by_key.values())


def import_from_wikipedia(fetcher: Fetcher) -> list[dict]:
    """Scrape the Wikipedia list of NJ municipalities (name, county, type)."""
    r = fetcher.get(WIKI_LIST_URL, max_age_hours=24 * 30)
    if not r.ok:
        raise RuntimeError(f"could not fetch {WIKI_LIST_URL}: {r.error or r.status}")
    soup = BeautifulSoup(r.text, "lxml")
    rows: list[dict] = []
    for table in soup.select("table.wikitable"):
        headers = [th.get_text(" ", strip=True).lower() for th in table.select("tr th")]
        if not any("municipality" in h or "name" in h for h in headers) or not any("county" in h for h in headers):
            continue
        hidx = {h: i for i, h in enumerate(headers)}
        name_i = next((i for h, i in hidx.items() if "municipality" in h or h == "name"), 0)
        county_i = next((i for h, i in hidx.items() if "county" in h), None)
        type_i = next((i for h, i in hidx.items() if "type" in h or "form" in h), None)
        for tr in table.select("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) <= max(name_i, county_i or 0):
                continue
            name = tds[name_i].get_text(" ", strip=True)
            name = re.sub(r"\[.*?\]", "", name).strip()
            if not name or name.lower() in ("municipality", "name"):
                continue
            link = tds[name_i].find("a", href=re.compile(r"^(https?://en\.wikipedia\.org)?/wiki/"))
            wiki_url = (link["href"] if link["href"].startswith("http") else "https://en.wikipedia.org" + link["href"]) if link else ""
            county = tds[county_i].get_text(" ", strip=True) if county_i is not None else ""
            county = county.replace(" County", "").strip()
            mtype = tds[type_i].get_text(" ", strip=True) if type_i is not None else ""
            if county and county not in NJ_COUNTIES:
                continue
            rows.append({"slug": make_slug(name, county), "name": name, "county": county, "muni_type": mtype, "wiki_url": wiki_url})
    # dedupe
    seen, out = set(), []
    for r in rows:
        if r["slug"] not in seen:
            seen.add(r["slug"])
            out.append(r)
    return out


# --- website discovery ------------------------------------------------------

_INFOBOX_BAD = re.compile(r"wikipedia|wikimedia|archive\.org|census\.gov|facebook|twitter", re.I)


def website_from_wikipedia(fetcher: Fetcher, wiki_url: str) -> Optional[str]:
    """The municipality's Wikipedia article infobox carries an official 'Website' row."""
    if not wiki_url:
        return None
    r = fetcher.get(wiki_url, max_age_hours=24 * 90)
    if not r.ok:
        return None
    soup = BeautifulSoup(r.text, "lxml")
    th = soup.find("th", string=re.compile(r"^\s*Website\s*$"))
    if not th:
        return None
    a = th.find_next("a", href=re.compile(r"^https?://"))
    if not a or _INFOBOX_BAD.search(a["href"]):
        return None
    p = urlparse(a["href"].strip())
    return f"{p.scheme}://{p.netloc.lower()}/"


_BAD_HOSTS = re.compile(r"wikipedia|facebook|twitter|instagram|linkedin|yelp|zillow|realtor|nj\.com|patch\.com|"
                        r"tapinto|google|youtube|njspotlight|usnews|niche\.com|city-data|wikimapia|mapquest|"
                        r"indeed|glassdoor|redfin|trulia|census|state\.nj\.us/?$|nj\.gov/?$", re.I)
_GOV_HINT = re.compile(r"\.(gov|us|org|net|com)$", re.I)


def find_website(fetcher: Fetcher, name: str, county: Optional[str] = None) -> Optional[str]:
    """Best-effort: query DuckDuckGo's HTML endpoint for the official municipal site.

    Returns the first plausible result or None. Always verify the result.
    """
    q = f"{name} {county + ' County' if county else ''} NJ official municipal website planning board"
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(q)
    r = fetcher.get(url, max_age_hours=24 * 30)
    if not r.ok:
        return None
    soup = BeautifulSoup(r.text, "lxml")
    token = slugify(re.sub(r"\b(township|borough|city|town|village|of)\b", "", name, flags=re.I)).replace("-", "")
    best = None
    for a in soup.select("a.result__a, a.result__url, a[href]"):
        href = a.get("href", "")
        if "duckduckgo.com/l/" in href:
            qs = parse_qs(urlparse(href).query)
            href = unquote(qs.get("uddg", [""])[0])
        if not href.startswith("http"):
            continue
        host = urlparse(href).netloc.lower()
        if _BAD_HOSTS.search(host):
            continue
        hostflat = host.replace("-", "").replace(".", "")
        if token[:5] and token[:5] in hostflat:
            p = urlparse(href)
            return f"{p.scheme}://{p.netloc}/"
        if best is None and _GOV_HINT.search(host):
            p = urlparse(href)
            best = f"{p.scheme}://{p.netloc}/"
    return best
