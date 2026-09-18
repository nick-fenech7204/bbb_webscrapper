"""
Batch Facebook enrichment: many records in (each carrying a `socials` list,
same shape BBB's own business_parser.py already produces), the same records
back out with facebook_* columns added when a `platform: "facebook"` entry
was found. Mirrors bbb_scraper.webcheck.enrich's shape (dedup by URL, a
read-through disk cache with a TTL, never fatal) with one real difference:
sequential with FacebookClient's own pacing/proxy rotation, not
ThreadPoolExecutor -- this is the repeated-single-host pattern (facebook.com,
every request) webcheck's own module docstring explicitly says its
concurrency model is wrong for, not webcheck's many-different-hosts case.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.exceptions import ScrapeError
from bbb_scraper.facebook.client import FacebookClient
from bbb_scraper.facebook.models import FacebookProfile
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.match.merge import recompute_intel

logger = get_logger(__name__)

CACHE_SAVE_EVERY = 25

_OUTPUT_FIELDS = [
    "status", "name", "categories", "address", "phone", "email", "website",
    "hours_status", "recommend_percentage", "review_count", "reviews_url",
    "followers_count", "talking_about_count", "checkins_count", "bio",
    "confirmed_owner", "price_range", "service_areas", "social_links",
]


class FacebookCache:
    """Read-through disk cache, keyed by the facebook.com URL. Same
    not-thread-safe-for-concurrent-writers contract as
    webcheck.enrich.WebsiteCheckCache -- fine here since this runs
    sequentially, never from multiple worker threads."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Facebook cache at %s unreadable -- starting fresh", path)
                self._data = {}

    def is_fresh(self, url: str, ttl_days: int) -> bool:
        entry = self._data.get(url)
        if not entry:
            return False
        try:
            checked_at = datetime.fromisoformat(entry["checked_at"])
        except (KeyError, ValueError):
            return False
        return (datetime.now(timezone.utc) - checked_at).total_seconds() / 86400 < ttl_days

    def get(self, url: str) -> dict[str, Any] | None:
        return self._data.get(url)

    def set(self, url: str, profile: FacebookProfile) -> None:
        entry = asdict(profile)
        entry["checked_at"] = datetime.now(timezone.utc).isoformat()
        self._data[url] = entry

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp_path.replace(self.path)
        except OSError:
            logger.warning("Facebook cache write failed for %s (will retry on the next periodic save)",
                            self.path, exc_info=True)


def _facebook_url(record: dict[str, Any], socials_field: str) -> str | None:
    """Real bug, caught 2026-09-18 by a live batch test finding 0/439 real
    Cincinnati dentists had a Facebook link even though 81 genuinely did:
    build_master_table's own _row() JSON-encodes every list/dict BBB field
    (including socials) into a STRING the moment a master row is built
    (2026-09-17, a real fix for a different problem -- see merge.py's own
    comment there) -- so by the time this runs, bbb_socials is
    '[{"platform": "facebook", ...}]' as TEXT, not a live list. Iterating a
    string with `for item in ...` walks its individual characters, none of
    which are dicts, so this silently found nothing, every time, without
    ever raising. Same decode-if-string handling
    scripts/publish_site_data.py's _decode_json_field already has for this
    identical ambiguity -- that path (site JSON) was never broken, only
    this one (reading a live master row mid-pipeline) was."""
    socials = record.get(socials_field)
    if isinstance(socials, str):
        try:
            socials = json.loads(socials) if socials else []
        except json.JSONDecodeError:
            socials = []
    for item in socials or []:
        if isinstance(item, dict) and item.get("platform") == "facebook" and item.get("url"):
            return item["url"]
    return None


def _bbb_prefix(socials_field: str) -> str:
    """"socials" -> ""; "bbb_socials" -> "bbb_" -- which bbb_email/bbb_website
    (or bare email/website) fields the backfill below writes into, matching
    whatever "shape" the input record already is. NOT the output prefix for
    this module's own facebook_* fields, which are always plain "facebook_"
    (a real, distinct top-level source, same as angi_* -- not a sub-field of
    whichever socials field happened to supply the URL, which an earlier,
    wrong version of this function assumed)."""
    return socials_field[: -len("socials")] if socials_field.endswith("socials") else ""


def _backfill_bbb_contact(row: dict[str, Any], bbb_prefix: str) -> None:
    """Nick's call, 2026-09-18: "use [Facebook] to... enrichment of email if
    blank from the BBB." Only fills a genuinely blank bbb_email -- never
    overwrites a real one Facebook might disagree with. Records provenance
    (`{bbb_prefix}email_source`) only when a backfill actually happened, so
    a rep can tell "BBB had this on file" from "we found this on Facebook,
    BBB's own profile didn't have one" -- same spirit as this project's
    existing most_recent_review_source tracking."""
    email_field = f"{bbb_prefix}email"
    if not row.get(email_field) and row.get("facebook_email"):
        row[email_field] = row["facebook_email"]
        row[f"{bbb_prefix}email_source"] = "facebook"


def enrich_with_facebook(
    records: list[dict],
    *,
    socials_field: str = "socials",
    cfg: Settings | None = None,
    ttl_days: int | None = None,
    cache_path: Path | None = None,
    client: FacebookClient | None = None,
    on_progress=None,
) -> list[dict]:
    """Returns NEW record dicts (doesn't mutate the input) with facebook_*
    fields added for any record whose `socials_field` carries a
    `{"platform": "facebook", "url": ...}` entry. Records with none get
    facebook_status="no_facebook_link" and nothing else. Never raises --
    a real per-URL fetch failure (network/HTTP, after FacebookClient's own
    retries) is logged and that one record comes back
    facebook_status="check_failed", same fail-open contract as every other
    best-effort enrichment step in this project (webcheck, Yelp).

    Also backfills `{bbb_prefix}email` when it's blank and Facebook has one
    (see _backfill_bbb_contact) -- every real caller in this pipeline is a
    BBB-sourced row (that's where the socials list itself came from), so
    this is always relevant, not an opt-in. Calls merge.recompute_intel on
    every returned row (same self-contained-rescore contract as
    bbb_scraper.angi.enrich.enrich_with_angi) so lead_priority_score
    reflects the new facebook_* data immediately -- callers don't need a
    separate recompute pass.

    `client`, if given, is used as-is and NOT closed here (caller's own,
    e.g. shared across metros the way Yelp's YelpEnrichmentState shares
    one client for a whole batch) -- pass one in tests instead of
    monkeypatching. Default (None) builds and closes a real, proxied
    FacebookClient for just this call.
    """
    cfg = cfg or default_settings
    ttl_days = ttl_days if ttl_days is not None else cfg.facebook_cache_ttl_days
    cache = FacebookCache(cache_path or (cfg.raw_data_dir / "facebook" / "cache.json"))
    bbb_prefix = _bbb_prefix(socials_field)

    urls_by_record = [_facebook_url(r, socials_field) for r in records]
    to_fetch = sorted({u for u in urls_by_record if u and not cache.is_fresh(u, ttl_days)})

    total = len(to_fetch)
    if to_fetch:
        logger.info("facebook: %d unique page(s) to fetch (%d cached/fresh)",
                     total, len({u for u in urls_by_record if u}) - total)
        owns_client = client is None
        active_client = client or FacebookClient(cfg=cfg)
        try:
            for done, url in enumerate(to_fetch, start=1):
                try:
                    profile = active_client.fetch_profile(url)
                except ScrapeError as exc:
                    logger.warning("facebook: fetch failed for %s: %s", url, exc)
                    profile = FacebookProfile(url=url, status="check_failed")
                except Exception as exc:
                    logger.warning("facebook: unexpected failure for %s: %s", url, exc, exc_info=True)
                    profile = FacebookProfile(url=url, status="check_failed")
                cache.set(url, profile)
                if on_progress:
                    on_progress(done, total)
                if done % CACHE_SAVE_EVERY == 0:
                    cache.save()
        finally:
            if owns_client:
                active_client.close()
        cache.save()

    out: list[dict] = []
    for record, url in zip(records, urls_by_record):
        row = dict(record)
        if not url:
            row["facebook_status"] = "no_facebook_link"
            for field in _OUTPUT_FIELDS[1:]:
                row[f"facebook_{field}"] = None
        else:
            entry = cache.get(url) or {}
            for field in _OUTPUT_FIELDS:
                row[f"facebook_{field}"] = entry.get(field)
            _backfill_bbb_contact(row, bbb_prefix)
        out.append(recompute_intel(row))
    return out
