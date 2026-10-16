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

    def get(self, url: str, timeout: float = 90.0, max_wait: float = 25.0) -> tuple[int, bytes, str, str]:
        """Return (status, html_bytes, content_type, final_url). Waits out a challenge page."""
        self._start()
        assert self._page is not None
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        status = resp.status if resp else 0
        deadline = time.time() + max_wait
        html = self._page.content()
        while CHALLENGE.search(html[:20000]) and time.time() < deadline:
            self._page.wait_for_timeout(1500)
            html = self._page.content()
        if not CHALLENGE.search(html[:20000]):
            self._page.wait_for_timeout(int(self.settle * 1000))
            html = self._page.content()
            status = 200 if status in (403, 503) else status
        ctype = (resp.headers.get("content-type", "text/html") if resp else "text/html")
        return status, html.encode("utf-8"), ctype, self._page.url

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
