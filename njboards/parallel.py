"""Run per-town pipeline steps across many municipalities in parallel.

Politeness is per host, and each municipality is its own host, so running
towns side by side does not hammer anyone. Each worker process opens its own
database connection and fetcher (and, if needed, its own headless browser).
"""
from __future__ import annotations

import logging
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable, Iterable, Optional

from .config import SETTINGS

log = logging.getLogger("njboards.parallel")


def _worker(slug: str, step: str, kwargs: dict, data_dir: str, settings_overrides: dict) -> dict:
    from pathlib import Path
    SETTINGS.data_dir = Path(data_dir)
    for k, v in settings_overrides.items():
        setattr(SETTINGS, k, v)
    SETTINGS.ensure_dirs()
    from .db import DB
    from .http import Fetcher
    from . import pipeline
    db = DB()
    try:
        if step == "discover":
            out = pipeline.run_discover(Fetcher(), db, slug, max_pages=kwargs.get("max_pages"))
        elif step == "run":
            out = pipeline.run_all(Fetcher(), db, slug, fetch_limit=kwargs.get("fetch_limit"),
                                   with_code=kwargs.get("with_code", False), doc_types=kwargs.get("doc_types"))
        elif step == "weekly":
            from .digest import weekly_town_pass
            out = weekly_town_pass(Fetcher(), db, slug, horizon_days=kwargs.get("horizon_days", 30),
                                   fetch_limit=kwargs.get("fetch_limit", 12), crawl_pages=kwargs.get("crawl_pages", 25),
                                   ocr=kwargs.get("ocr", False))
        else:
            raise ValueError(step)
        return {"slug": slug, "ok": True, "result": out}
    except Exception as e:  # noqa: BLE001
        return {"slug": slug, "ok": False, "error": f"{e}", "trace": traceback.format_exc()[-1500:]}
    finally:
        db.close()
        try:
            from .browser import _SHARED
            if _SHARED:
                _SHARED.close()
        except Exception:  # noqa: BLE001
            pass


def run_parallel(slugs: Iterable[str], step: str, workers: int = 6, progress: Optional[Callable[[dict], None]] = None,
                 **kwargs) -> list[dict]:
    slugs = list(slugs)
    overrides = {k: getattr(SETTINGS, k) for k in ("delay_seconds", "max_pages_per_site", "min_year", "ocr_enabled", "use_browser", "user_agent")}
    results = []
    if workers <= 1:
        for s in slugs:
            r = _worker(s, step, kwargs, str(SETTINGS.data_dir), overrides)
            results.append(r)
            if progress:
                progress(r)
        return results
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_worker, s, step, kwargs, str(SETTINGS.data_dir), overrides): s for s in slugs}
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001
                r = {"slug": futs[fut], "ok": False, "error": str(e)}
            results.append(r)
            if progress:
                progress(r)
    return results
