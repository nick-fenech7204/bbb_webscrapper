"""
Phone-number dedup for the lead-list pipeline (bbb_scraper.match).

Deliberately separate from etl/dedupe.py's per-*listing* dedup, which stays
exactly as it is for the general scraping pipeline -- that one is correct
on purpose (a business with several branches is meant to stay several rows
there; collapsing them by an id that didn't distinguish branches was a
real, fixed bug -- see its own history).

This one is for the lead-list / matching pipeline specifically. Nick's
call 2026-09-11, prompted by a real screenshot: one Chicago plumbing
company (Goode Plumbing) showed up 4x in the published lead table, once
per BBB branch address, all four sharing one phone number -- correct BBB
data, but not what a lead list should read as. Phone number is the
record's identity here, always: the same normalized 10-digit number is
the same lead, first one seen wins. Records with no phone are never
merged with each other -- a missing phone isn't a real shared identity,
so each keeps its own row.
"""
from __future__ import annotations

from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.match.normalize import phone_key

logger = get_logger(__name__)


def dedupe_by_phone(
    records: list[dict[str, Any]], *, phone_field: str = "phone"
) -> list[dict[str, Any]]:
    """Collapse records sharing a normalized phone number into the first
    one seen. `phone_field` lets this work on either a raw BBB/Yelp record
    (`"phone"`) or a match.merge master-table row (`"bbb_phone"`).
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    dropped = 0
    for r in records:
        key = phone_key(r.get(phone_field))
        if key is None:
            out.append(r)  # no phone to key on -- never merged away
            continue
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(r)
    if dropped:
        logger.info("dedupe_by_phone: dropped %d record(s) sharing a phone with an earlier one "
                    "(%d -> %d)", dropped, len(records), len(out))
    return out
