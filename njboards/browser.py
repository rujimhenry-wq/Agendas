"""Headless-browser fallback for sites behind a JavaScript challenge
(eCode360 sits behind Cloudflare's "Just a moment..." interstitial, which
blocks plain HTTP clients regardless of user agent, and whose clearance
cookie is tied to the browser's TLS fingerprint so it cannot be reused by
requests).

Uses Playwright + the system Chromium when available; otherwise reports
itself unavailable and the caller keeps the HTTP error.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

log = logging.getLogger("njboards.browser")

CHALLENGE = re.compile(r"just a moment|cf-chl|challenge-platform|cf_chl_opt|enable javascript and cookies", re.I)
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CHROMIUM_CANDIDATES = ["/opt/pw-browsers/chromium", os.environ.get("NJBOARDS_CHROMIUM", "")]


def looks_like_challenge(status: int, body: bytes) -> bool:
    if status not in (403, 503, 429):
        return False
    return bool(CHALLENGE.search(body[:20000].decode("utf-8", "ignore")))


class Browser:
    """Lazy, long-lived headless Chromium page. One instance per process."""

    def __init__(self, user_agent: str = DEFAULT_UA, settle_seconds: float = 2.5):
        self.ua = user_agent
        self.settle = settle_seconds
        self._pw = None
        self._browser = None
        self._ctx = None
        self._page = None
        self.unavailable: Optional[str] = None
        self._cleared_hosts: set[str] = set()

    def available(self) -> bool:
        if self.unavailable:
            return False
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.unavailable = "playwright not installed (pip install playwright)"
            return False
        return True

    def _start(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        exe = next((c for c in CHROMIUM_CANDIDATES if c and os.path.exists(c)), None)
        kwargs = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
        if exe:
            kwargs["executable_path"] = exe
        try:
            self._browser = self._pw.chromium.launch(**kwargs)
        except Exception as e:  # noqa: BLE001
            self.unavailable = f"could not launch Chromium: {e}"
            raise
        self._ctx = self._browser.new_context(user_agent=self.ua, viewport={"width": 1280, "height": 900})
        self._page = self._ctx.new_page()

    def get(self, url: str, timeout: float = 180.0, max_wait: float = 45.0) -> tuple[int, bytes, str, str]:
        """Return (status, html_bytes, content_type, final_url). Waits out a challenge page.

        Print views of long chapters run to several megabytes, so the
        navigation timeout is generous and a timed-out navigation is retried once."""
        self._start()
        assert self._page is not None
        try:
            resp = self._page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        except Exception as e:  # noqa: BLE001 - usually a navigation timeout on a huge page
            log.info("browser retry for %s after: %s", url, str(e)[:120])
            self._page.wait_for_timeout(3000)
            resp = self._page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000 * 2)
        status = resp.status if resp else 0
        deadline = time.time() + max_wait
        html = self._page.content()
        while CHALLENGE.search(html[:20000]) and time.time() < deadline:
            self._page.wait_for_timeout(1500)
            html = self._page.content()
        if not CHALLENGE.search(html[:20000]):
            try:  # code pages fill in their sections after load
                self._page.wait_for_selector(".sectionTitle, .titleLink, main, #content, article", timeout=20000)
            except Exception:  # noqa: BLE001
                pass
            self._page.wait_for_timeout(int(self.settle * 1000))
            html = self._page.content()
            status = 200 if status in (403, 503) else status
        ctype = (resp.headers.get("content-type", "text/html") if resp else "text/html")
        return status, html.encode("utf-8"), ctype, self._page.url

    def download(self, url: str, timeout: float = 120.0) -> tuple[int, bytes, str, str]:
        """Fetch a binary file (PDF) through the browser so the challenge clearance applies.

        The page is parked on the file's origin (so the clearance cookie is sent)
        and the file is pulled with an in-page fetch() and returned as base64.
        Chromium's own network stack does the request, which is what Cloudflare
        fingerprints; Playwright's request API and plain HTTP clients are refused."""
        from urllib.parse import urlparse
        self._start()
        assert self._page is not None
        origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if not self._page.url.startswith(origin):
            self._page.goto(origin + "/", wait_until="domcontentloaded", timeout=timeout * 1000)
            deadline = time.time() + 25
            while CHALLENGE.search(self._page.content()[:20000]) and time.time() < deadline:
                self._page.wait_for_timeout(1500)
        js = """async ([u, ms]) => {
            const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), ms);
            const r = await fetch(u, {credentials: 'include', signal: ctl.signal});
            clearTimeout(t);
            const buf = await r.arrayBuffer(); const bytes = new Uint8Array(buf);
            let bin = ''; const CH = 0x8000;
            for (let i = 0; i < bytes.length; i += CH) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
            return {status: r.status, type: r.headers.get('content-type') || '', url: r.url, b64: btoa(bin)};
        }"""
        import base64
        res = self._page.evaluate(js, [url, int(timeout * 1000)])
        data = base64.b64decode(res["b64"]) if res.get("b64") else b""
        if CHALLENGE.search(data[:20000].decode("utf-8", "ignore")):
            return 403, data, res.get("type", ""), res.get("url", url)
        return int(res.get("status", 0)), data, res.get("type", ""), res.get("url", url)

    def close(self) -> None:
        for obj in (self._ctx, self._browser):
            try:
                if obj:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
        self._page = self._ctx = self._browser = self._pw = None


_SHARED: Optional[Browser] = None


def shared_browser() -> Browser:
    global _SHARED
    if _SHARED is None:
        _SHARED = Browser()
    return _SHARED
