"""Find official municipal websites without leaning on rate-limited third parties.

Two strategies, both robots-friendly and cheap:

1. County directories. Each of NJ's 21 counties lists its municipalities with
   links on its own site. We crawl a county site shallowly for a page whose
   links match municipality names and harvest the external links.
2. Domain guessing. NJ towns use predictable domains (cranfordnj.org,
   teanecknj.gov, twp.howell.nj.us, montclairnjusa.org...). We try a short
   list of candidates and keep one whose homepage mentions the town and New
   Jersey.

A found site is marked 'verify' in the notes column; discovery will confirm it
by finding planning / zoning board pages.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .http import Fetcher
from .util import slugify

log = logging.getLogger("njboards.websites")

COUNTY_SITES = {
    "Atlantic": "https://www.atlantic-county.org/", "Bergen": "https://www.co.bergen.nj.us/",
    "Burlington": "https://www.co.burlington.nj.us/", "Camden": "https://www.camdencounty.com/",
    "Cape May": "https://www.capemaycountynj.gov/", "Cumberland": "https://www.co.cumberland.nj.us/",
    "Essex": "https://www.essexcountynj.org/", "Gloucester": "https://www.gloucestercountynj.gov/",
    "Hudson": "https://www.hcnj.us/", "Hunterdon": "https://www.co.hunterdon.nj.us/",
    "Mercer": "https://www.mercercounty.org/", "Middlesex": "https://www.middlesexcountynj.gov/",
    "Monmouth": "https://www.co.monmouth.nj.us/", "Morris": "https://www.morriscountynj.gov/",
    "Ocean": "https://www.co.ocean.nj.us/", "Passaic": "https://www.passaiccountynj.org/",
    "Salem": "https://www.salemcountynj.gov/", "Somerset": "https://www.co.somerset.nj.us/",
    "Sussex": "https://www.sussex.nj.us/", "Union": "https://ucnj.org/", "Warren": "https://www.co.warren.nj.us/",
}
NOT_MUNI_HOSTS = re.compile(r"facebook|twitter|instagram|youtube|linkedin|google|nj\.gov$|state\.nj\.us$|co\.[a-z]+\.nj\.us|county|wikipedia|"
                            r"njlm|usa\.gov|census|weather|flickr|vimeo|adobe|apple\.com|microsoft|civicplus|granicus|revize|govoffice", re.I)
GENERIC_WORDS = {"township", "borough", "city", "town", "village", "of", "the", "and", "new", "jersey", "nj"}


def name_tokens(name: str) -> list[str]:
    toks = [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t and t not in GENERIC_WORDS]
    return toks or [slugify(name).replace("-", "")]


def _host_matches(host: str, name: str) -> bool:
    flat = re.sub(r"[^a-z0-9]", "", host.lower())
    toks = name_tokens(name)
    joined = "".join(toks)
    if len(joined) >= 5 and joined in flat:
        return True
    return all(t in flat for t in toks if len(t) >= 4) and any(len(t) >= 4 for t in toks)


# ----------------------------------------------------------------- county directories
def county_directory_links(fetcher: Fetcher, county: str, max_pages: int = 25) -> dict[str, str]:
    """Return {hostname: url} of municipal-looking external links found on the county site."""
    base = COUNTY_SITES.get(county)
    if not base:
        return {}
    seen, queue, found = set(), [base], {}
    while queue and len(seen) < max_pages:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r = fetcher.get(url, max_age_hours=24 * 14)
        if not r.ok or not r.is_html:
            continue
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href]"):
            href = urljoin(r.final_url, a.get("href", "")).split("#")[0]
            text = a.get_text(" ", strip=True)
            p = urlparse(href)
            if not p.scheme.startswith("http"):
                continue
            if p.netloc.lower().replace("www.", "") == urlparse(base).netloc.lower().replace("www.", ""):
                if re.search(r"municipal|town|borough|cities|communit|links|directory|government", text + " " + p.path, re.I) and len(seen) + len(queue) < max_pages * 2:
                    queue.append(href)
            elif not NOT_MUNI_HOSTS.search(p.netloc) and re.search(r"\.(gov|org|us|net|com)$", p.netloc, re.I):
                found.setdefault(p.netloc.lower(), f"{p.scheme}://{p.netloc}/")
    return found


# ----------------------------------------------------------------- domain guessing
def candidate_domains(name: str, muni_type: str = "") -> list[str]:
    toks = name_tokens(name)
    core = "".join(toks)
    dashed = "-".join(toks)
    t = (muni_type or "").lower()
    c = []
    for stem in dict.fromkeys([core, dashed]):
        c += [f"www.{stem}nj.org", f"www.{stem}nj.gov", f"www.{stem}-nj.org", f"www.{stem}nj.com", f"www.{stem}.nj.us",
              f"www.{stem}nj.net", f"{stem}nj.org", f"{stem}nj.gov", f"www.{stem}.org", f"www.{stem}.com"]
        if "township" in t or not t:
            c += [f"www.{stem}twp.org", f"www.{stem}township.org", f"www.{stem}twp.com", f"twp.{stem}.nj.us", f"www.twp.{stem}.nj.us",
                  f"www.{stem}township.com", f"www.{stem}-twp.org", f"{stem}township.org", f"www.{stem}twp.nj.us", f"www.{stem}townshipnj.org"]
        if "borough" in t or not t:
            c += [f"www.{stem}boro.org", f"www.{stem}boro.com", f"www.{stem}borough.org", f"www.{stem}boronj.org", f"www.boroughof{stem}.org",
                  f"www.{stem}boro.net", f"www.{stem}-boro.org", f"{stem}boro.org"]
        if "city" in t or not t:
            c += [f"www.cityof{stem}.org", f"www.cityof{stem}.com", f"www.{stem}city.org", f"www.{stem}citynj.org", f"www.cityof{stem}nj.org", f"www.ci.{stem}.nj.us"]
        if "town" in t:
            c += [f"www.townof{stem}.org", f"www.townof{stem}.com", f"www.{stem}town.org", f"www.townof{stem}nj.org"]
        if "village" in t:
            c += [f"www.villageof{stem}.org", f"www.{stem}village.org"]
    return list(dict.fromkeys(c))


def verify_site(fetcher: Fetcher, url: str, name: str) -> bool:
    r = fetcher.get(url, max_age_hours=24 * 14, stream_limit=3_000_000)
    if not r.ok or not r.is_html:
        return False
    text = BeautifulSoup(r.text[:400000], "lxml").get_text(" ", strip=True).lower()
    toks = name_tokens(name)
    if not any(t in text for t in toks):
        return False
    return bool(re.search(r"new jersey|\bnj\b", text)) and bool(re.search(r"municipal|township|borough|council|mayor|clerk|zoning|planning|ordinance", text))


def guess_website(fetcher: Fetcher, name: str, muni_type: str = "", max_tries: int = 14) -> Optional[str]:
    # each candidate is a different host, and most do not exist: no retries, short timeout
    fetcher.s.delay_seconds = min(fetcher.s.delay_seconds, 0.5)
    fetcher.s.max_retries = 0
    fetcher.s.timeout_seconds = min(fetcher.s.timeout_seconds, 10.0)
    for host in candidate_domains(name, muni_type)[:max_tries]:
        for scheme in ("https://",):
            url = scheme + host + "/"
            try:
                if verify_site(fetcher, url, name):
                    return url
            except Exception as e:  # noqa: BLE001
                log.debug("guess %s: %s", url, e)
    return None


# ----------------------------------------------------------------- orchestration
def find_websites(rows: list[dict], workers: int = 8, progress=None) -> dict[str, str]:
    """rows: municipality dicts (name, county, muni_type, website). Returns {slug: website} for new finds."""
    todo = [r for r in rows if not r.get("website")]
    found: dict[str, str] = {}
    # 1. county directories (one crawl per county, shared across its towns)
    counties = sorted({r.get("county", "") for r in todo if r.get("county")})
    links_by_county: dict[str, dict[str, str]] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(counties) or 1)) as ex:
        futs = {ex.submit(county_directory_links, Fetcher(), c): c for c in counties}
        for fut in as_completed(futs):
            try:
                links_by_county[futs[fut]] = fut.result()
            except Exception as e:  # noqa: BLE001
                log.warning("county %s: %s", futs[fut], e)
                links_by_county[futs[fut]] = {}
    for r in todo:
        for host, url in links_by_county.get(r.get("county", ""), {}).items():
            if _host_matches(host, r["name"]):
                found[r["slug"]] = url
                break
    # one site matched several towns (Neptune City / Neptune Township, Andover Borough / Township):
    # keep the town whose type the host names; otherwise drop the ambiguous match entirely
    by_url: dict[str, list[dict]] = {}
    for r in todo:
        if r["slug"] in found:
            by_url.setdefault(found[r["slug"]], []).append(r)
    for url, rs in by_url.items():
        if len(rs) < 2:
            continue
        host = urlparse(url).netloc.lower()
        def fits(r):
            t = (r.get("muni_type") or r["name"]).lower()
            return (("borough" in t and re.search(r"boro", host)) or ("township" in t and re.search(r"twp|township", host))
                    or ("city" in t and re.search(r"city", host)) or ("village" in t and "village" in host) or ("town" == t.strip() and "town" in host))
        keep = [r for r in rs if fits(r)]
        for r in rs:
            if len(keep) != 1 or r is not keep[0]:
                found.pop(r["slug"], None)
    if progress:
        for slug, url in found.items():
            progress(slug, url, "county directory")
    # 2. domain guessing for the rest
    rest = [r for r in todo if r["slug"] not in found]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(guess_website, Fetcher(), r["name"], r.get("muni_type", "")): r for r in rest}
        for fut in as_completed(futs):
            r = futs[fut]
            try:
                url = fut.result()
            except Exception as e:  # noqa: BLE001
                log.debug("guess failed %s: %s", r["slug"], e)
                url = None
            if url:
                found[r["slug"]] = url
                if progress:
                    progress(r["slug"], url, "domain guess")
    return found
