"""
Batch website-liveness enrichment: many BBB records in, the same records
back out with website_dead / website_status / website_checked_at added
(bare or bbb_-prefixed depending on `website_field` -- see
derive_output_fields). Two things a single check_website() call doesn't
give you on its own:

  1. **Dedup by URL.** Several BBB branch listings (or several businesses
     that happen to share a site) often carry the identical website --
     checked once, not once per row.
  2. **A read-through disk cache with a TTL.** A site that was dead last
     month is still probably dead today; re-checking every business every
     batch run would be a lot of needless traffic to a lot of unrelated
     third-party sites for no new information. Cached under
     data/raw/webcheck/cache.json by default, same "cache real network
     calls to disk" philosophy as the Yelp client.

Concurrency is bounded (ThreadPoolExecutor, default 10 workers) -- this is
I/O-bound waiting on many *different* hosts, not the repeated-single-host
pattern BBB scraping has to pace politely; a modest worker count is a
normal level of concurrency for a one-off liveness sweep, not a burst.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.webcheck.checker import WebsiteCheck, _normalize_url, check_website

logger = get_logger(__name__)


class WebsiteCheckCache:
    """Read-through disk cache, keyed by normalized URL. Not thread-safe for
    concurrent writers -- check_websites() only ever saves it once, from the
    main thread, after all worker threads have finished."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Webcheck cache at %s unreadable -- starting fresh", path)
                self._data = {}

    def get(self, url: str) -> dict[str, Any] | None:
        return self._data.get(url)

    def is_fresh(self, url: str, ttl_days: int) -> bool:
        entry = self._data.get(url)
        if not entry:
            return False
        try:
            checked_at = datetime.fromisoformat(entry["checked_at"])
        except (KeyError, ValueError):
            return False
        age_days = (datetime.now(timezone.utc) - checked_at).total_seconds() / 86400
        return age_days < ttl_days

    def set(self, url: str, check: WebsiteCheck) -> None:
        self._data[url] = asdict(check)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)  # atomic, same pattern as the batch scraper's checkpoints


def derive_output_fields(website_field: str) -> tuple[str, str, str]:
    """"website" -> ("website_dead", "website_status", "website_checked_at");
    "bbb_website" -> the bbb_-prefixed versions -- so this writes back in
    whatever "shape" the input already is (a raw BBB record vs. a
    master-table/checkpoint row where every BBB field is bbb_-prefixed)."""
    if website_field.endswith("website"):
        prefix = website_field[: -len("website")]
    else:
        prefix = ""
    return f"{prefix}website_dead", f"{prefix}website_status", f"{prefix}website_checked_at"


def check_websites(
    records: list[dict],
    *,
    website_field: str = "website",
    cfg: Settings | None = None,
    max_workers: int | None = None,
    ttl_days: int | None = None,
    cache_path: Path | None = None,
    on_progress=None,
) -> list[dict]:
    """Returns NEW record dicts (doesn't mutate the input) with
    website_dead (bool) / website_status (str) / website_checked_at (ISO)
    added -- field names bare or bbb_-prefixed to match `website_field`
    (see derive_output_fields). Records with no website field just get
    status="no_website", dead=False.

    `on_progress(done, total)`, if given, is called after each unique URL
    finishes checking -- for a CLI progress line on a run that can take a
    while across thousands of businesses.
    """
    cfg = cfg or default_settings
    max_workers = max_workers or cfg.webcheck_max_workers
    ttl_days = ttl_days if ttl_days is not None else cfg.webcheck_cache_ttl_days
    cache = WebsiteCheckCache(cache_path or (cfg.raw_data_dir / "webcheck" / "cache.json"))
    dead_field, status_field, checked_field = derive_output_fields(website_field)

    normalized_by_record = [_normalize_url(r.get(website_field, "")) for r in records]
    to_check = sorted({u for u in normalized_by_record if u and not cache.is_fresh(u, ttl_days)})

    total = len(to_check)
    done = 0
    if to_check:
        logger.info("webcheck: %d unique website(s) to check (%d cached/fresh)",
                     total, len({u for u in normalized_by_record if u}) - total)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(check_website, url, cfg=cfg): url for url in to_check}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 -- one bad future must not lose the rest
                    logger.warning("webcheck: unexpected failure for %s: %s", url, exc)
                    result = WebsiteCheck(url=url, status="check_failed", dead=False,
                                           http_status=None,
                                           checked_at=datetime.now(timezone.utc).isoformat())
                cache.set(url, result)
                done += 1
                if on_progress:
                    on_progress(done, total)
        cache.save()

    out: list[dict] = []
    for record, normalized in zip(records, normalized_by_record):
        row = dict(record)
        if not normalized:
            row[dead_field] = False
            row[status_field] = "no_website"
            row[checked_field] = ""
        else:
            entry = cache.get(normalized) or {}
            row[dead_field] = bool(entry.get("dead", False))
            row[status_field] = entry.get("status", "check_failed")
            row[checked_field] = entry.get("checked_at", "")
        out.append(row)
    return out
