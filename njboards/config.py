"""Paths and tunable settings. Everything lives under DATA_DIR (default ./data)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default):
    v = os.environ.get(name)
    if v is None:
        return default
    if isinstance(default, bool):
        return v.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(v)
    if isinstance(default, float):
        return float(v)
    return v


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("NJBOARDS_DATA", "data")).resolve())
    user_agent: str = _env(
        "NJBOARDS_UA",
        "njboards/0.1 (+https://github.com/rujimhenry-wq/Agendas; public-records research; set NJBOARDS_UA to add your contact)",
    )
    # polite crawling
    delay_seconds: float = _env("NJBOARDS_DELAY", 1.5)
    timeout_seconds: float = _env("NJBOARDS_TIMEOUT", 30.0)
    max_retries: int = _env("NJBOARDS_RETRIES", 3)
    respect_robots: bool = _env("NJBOARDS_ROBOTS", True)
    cache_ttl_hours: float = _env("NJBOARDS_CACHE_TTL", 24.0 * 7)
    max_download_bytes: int = _env("NJBOARDS_MAX_BYTES", 60 * 1024 * 1024)
    # discovery limits
    max_pages_per_site: int = _env("NJBOARDS_MAX_PAGES", 150)
    max_docs_per_board: int = _env("NJBOARDS_MAX_DOCS", 400)
    min_year: int = _env("NJBOARDS_MIN_YEAR", 2019)
    ocr_enabled: bool = _env("NJBOARDS_OCR", False)
    use_browser: bool = _env("NJBOARDS_BROWSER", True)   # headless Chromium for JS-challenge sites (eCode360)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "njboards.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def text_dir(self) -> Path:
        return self.data_dir / "text"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def code_dir(self) -> Path:
        return self.data_dir / "code"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def municipalities_file(self) -> Path:
        return self.data_dir / "municipalities.csv"

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir, self.cache_dir, self.raw_dir, self.text_dir,
            self.parsed_dir, self.code_dir, self.reports_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


SETTINGS = Settings()
