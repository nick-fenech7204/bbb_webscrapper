"""
Stable internal identifier for a business record.

Preference order:
  1. BBB's own ID if we have one (most reliable, prefixed so it's obviously
     BBB-sourced if this ID space is ever merged with another source).
  2. A deterministic hash of normalized (name, address, phone) so the same
     business scraped twice -- even without a captured BBB id -- still
     dedupes to the same internal id.

This intentionally never uses random/uuid4 -- the whole point is that
re-scraping the same business produces the same id, which is what makes
dedupe (etl/dedupe.py) and idempotent loads possible.
"""
from __future__ import annotations

import re

from bbb_scraper.utils.hashing import sha256_hex


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def generate_business_id(
    *,
    bbb_id: str | None = None,
    name: str | None = None,
    address: str | None = None,
    phone: str | None = None,
) -> str:
    if bbb_id:
        return f"bbb:{bbb_id}"

    fingerprint = "|".join(_normalize(v) for v in (name, address, phone))
    if not fingerprint.strip("|"):
        raise ValueError("Cannot generate an id without a bbb_id or at least one of name/address/phone")
    return f"gen:{sha256_hex(fingerprint, 20)}"
