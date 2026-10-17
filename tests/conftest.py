"""Shared fixtures: an offline Fetcher that serves pages from a dict, and an
isolated data directory per test."""
from __future__ import annotations

from pathlib import Path

import pytest

from njboards import config
from njboards.http import Fetcher, Response

FIX = Path(__file__).parent / "fixtures"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class StubFetcher(Fetcher):
    """Serves canned responses; records what was requested; never touches the network."""

    def __init__(self, routes: dict[str, tuple[bytes | str, str]]):
        super().__init__()
        self.s.delay_seconds = 0
        self.s.respect_robots = False
        self.routes = routes
        self.requested: list[str] = []

    def get(self, url, max_age_hours=None, use_cache=True, stream_limit=None, **kw):  # type: ignore[override]
        self.requested.append(url)
        key = url.split("#")[0]
        for k, (body, ctype) in self.routes.items():
            if key == k or key.rstrip("/") == k.rstrip("/"):
                data = body.encode("utf-8") if isinstance(body, str) else body
                return Response(url=url, final_url=url, status=200, content=data, content_type=ctype)
        return Response(url=url, final_url=url, status=404, content=b"", content_type="text/html")


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config.SETTINGS, "data_dir", tmp_path / "data")
    config.SETTINGS.ensure_dirs()
    return config.SETTINGS.data_dir


def make_pdf(path: Path, text: str) -> Path:
    """Write a simple multi-line text PDF with reportlab."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 750
    import textwrap
    for line in text.splitlines():
        for chunk in (textwrap.wrap(line, 95) or [""]):
            c.drawString(40, y, chunk)
            y -= 12
            if y < 40:
                c.showPage()
                y = 750
    c.save()
    return path
