"""Polite HTTP fetcher: identifies itself, honors robots.txt, rate-limits per
host, retries with backoff and caches responses on disk."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib import robotparser
from urllib.parse import urlparse

import requests

from .browser import looks_like_challenge, shared_browser
from .config import SETTINGS, Settings
from .util import sha1_str

log = logging.getLogger("njboards.http")


@dataclass
class Response:
    url: str
    final_url: str
    status: int
    content: bytes
    content_type: str
    from_cache: bool = False
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    @property
    def text(self) -> str:
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return self.content.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.content.decode("utf-8", "ignore")

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type or self.content[:300].lstrip().lower().startswith((b"<!doctype html", b"<html"))

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type or self.content[:5] == b"%PDF-"


class Fetcher:
    def __init__(self, settings: Settings = SETTINGS, session: Optional[requests.Session] = None):
        self.s = settings
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": self.s.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, Optional[robotparser.RobotFileParser]] = {}
        self.s.cache_dir.mkdir(parents=True, exist_ok=True)

    # --- cache ---------------------------------------------------------
    def _cache_paths(self, url: str) -> tuple[Path, Path]:
        key = sha1_str(url)
        d = self.s.cache_dir / key[:2]
        return d / (key + ".bin"), d / (key + ".json")

    def _cache_get(self, url: str, max_age_hours: Optional[float]) -> Optional[Response]:
        body, meta = self._cache_paths(url)
        if not (body.exists() and meta.exists()):
            return None
        ttl = self.s.cache_ttl_hours if max_age_hours is None else max_age_hours
        if ttl >= 0 and (time.time() - meta.stat().st_mtime) > ttl * 3600:
            return None
        try:
            m = json.loads(meta.read_text())
        except json.JSONDecodeError:
            return None
        return Response(url=url, final_url=m.get("final_url", url), status=m.get("status", 200),
                        content=body.read_bytes(), content_type=m.get("content_type", ""), from_cache=True)

    def _cache_put(self, r: Response) -> None:
        body, meta = self._cache_paths(r.url)
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(r.content)
        meta.write_text(json.dumps({"final_url": r.final_url, "status": r.status, "content_type": r.content_type}))

    # --- robots ----------------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.s.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                resp = self.session.get(base + "/robots.txt", timeout=self.s.timeout_seconds)
                if resp.status_code == 200 and resp.text.strip():
                    rp.parse(resp.text.splitlines())
                    self._robots[base] = rp
                else:
                    self._robots[base] = None
            except requests.RequestException:
                self._robots[base] = None
        rp = self._robots[base]
        if rp is None:
            return True
        return rp.can_fetch(self.s.user_agent, url) or rp.can_fetch("*", url)

    # --- fetch -----------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_hit.get(host, 0.0)
        wait = self.s.delay_seconds - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.time()

    def get(self, url: str, max_age_hours: Optional[float] = None, use_cache: bool = True,
            stream_limit: Optional[int] = None) -> Response:
        if use_cache:
            cached = self._cache_get(url, max_age_hours)
            if cached:
                return cached
        if not self.allowed(url):
            return Response(url, url, 0, b"", "", error="blocked by robots.txt")
        limit = stream_limit or self.s.max_download_bytes
        err = None
        for attempt in range(self.s.max_retries + 1):
            self._throttle(url)
            try:
                with self.session.get(url, timeout=self.s.timeout_seconds, stream=True, allow_redirects=True) as resp:
                    ctype = resp.headers.get("Content-Type", "").lower()
                    clen = resp.headers.get("Content-Length")
                    if clen and int(clen) > limit:
                        return Response(url, resp.url, resp.status_code, b"", ctype, error=f"too large ({clen} bytes)")
                    chunks, size = [], 0
                    for chunk in resp.iter_content(chunk_size=65536):
                        chunks.append(chunk)
                        size += len(chunk)
                        if size > limit:
                            return Response(url, resp.url, resp.status_code, b"", ctype, error="too large")
                    r = Response(url, resp.url, resp.status_code, b"".join(chunks), ctype)
                if looks_like_challenge(r.status, r.content):
                    br = self._browser_get(url)
                    if br is not None:
                        if use_cache:
                            self._cache_put(br)
                        return br
                    return Response(url, r.final_url, r.status, r.content, r.content_type,
                                    error="blocked by JavaScript challenge (install playwright for the browser fallback)")
                if r.status in (429, 500, 502, 503, 504) and attempt < self.s.max_retries:
                    time.sleep(2 ** attempt * 2)
                    continue
                if r.ok and use_cache:
                    self._cache_put(r)
                return r
            except requests.RequestException as e:
                err = str(e)
                log.debug("fetch error %s (attempt %d): %s", url, attempt, err)
                time.sleep(2 ** attempt)
        return Response(url, url, 0, b"", "", error=err or "request failed")

    def _browser_get(self, url: str) -> Optional[Response]:
        if not self.s.use_browser:
            return None
        b = shared_browser()
        if not b.available():
            log.warning("challenge page at %s and no browser fallback: %s", url, b.unavailable)
            return None
        try:
            if re.search(r"\.(pdf|docx?|zip)(\?|$)|/document/\d+", url, re.I):
                status, body, ctype, final = b.download(url)
            else:
                status, body, ctype, final = b.get(url)
        except Exception as e:  # noqa: BLE001
            log.warning("browser fetch failed for %s: %s", url, e)
            return None
        if looks_like_challenge(status, body) or status >= 400:
            return Response(url, final, status, body, ctype, error=f"challenge not cleared (HTTP {status})")
        return Response(url, final, status, body, ctype)

    def get_json(self, url: str, **kw):
        r = self.get(url, **kw)
        if not r.ok:
            return None
        try:
            return json.loads(r.text)
        except json.JSONDecodeError:
            return None
