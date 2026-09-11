"""Deduplicate transformed records by their stable `id` (see identifiers.py)."""
from __future__ import annotations

from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.utils.stats import RECORDS_DEDUPED, RunStats

logger = get_logger(__name__)


def dedupe_records(
    records: list[dict[str, Any]],
    *,
    key: str = "id",
    stats: RunStats | None = None,
) -> list[dict[str, Any]]:
    """Keep the first occurrence of each `key` value, preserving order.

    Later records with the same key are dropped (in a future iteration this
    is the natural place to instead *merge* summary + detail records for the
    same business rather than discarding one).
    """
    stats = stats or RunStats()
    seen: set[Any] = set()
    deduped: list[dict[str, Any]] = []
    dropped = 0

    for record in records:
        record_key = record.get(key)
        if record_key in seen:
            dropped += 1
            continue
        seen.add(record_key)
        deduped.append(record)

    if dropped:
        logger.info("Deduped %d duplicate record(s) by '%s'", dropped, key)
    stats.incr(RECORDS_DEDUPED, dropped)
    return deduped
