"""
Parse a BBB search/listing page into BusinessSummary records.

TODO(you): `_iter_listing_items` and `_map_listing_item` encode a *placeholder*
schema (see tests/fixtures/search_listing_sample.html) since we haven't
inspected real BBB listing JSON yet. Once you've captured a real page:
  1. Save it to tests/fixtures/search_listing_sample.html (replacing the
     placeholder).
  2. Adjust SCRIPT_TYPE / _iter_listing_items to find the right script
     tag(s) and array of items.
  3. Adjust _map_listing_item's field paths.
  4. tests/parsing/test_search_parser.py will tell you when it's right.

Everything else (the public `parse_search_results` signature, capture,
ETL wiring) does not need to change.
"""
from __future__ import annotations

from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.parsing.json_extract import extract_json_scripts
from bbb_scraper.parsing.models import BusinessSummary
from bbb_scraper.utils.stats import RunStats, PAGES_PARSED, PARSE_FAILURES, RECORDS_EXTRACTED

logger = get_logger(__name__)

# Placeholder: adjust once real markup is inspected.
SCRIPT_TYPE = "application/json"


def parse_search_results(
    html: str,
    *,
    query: str | None = None,
    location: str | None = None,
    page: int | None = None,
    stats: RunStats | None = None,
) -> list[BusinessSummary]:
    stats = stats or RunStats()
    records: list[BusinessSummary] = []

    for blob in extract_json_scripts(html, content_type=SCRIPT_TYPE):
        for item in _iter_listing_items(blob):
            try:
                summary = _map_listing_item(
                    item, query=query, location=location, page=page
                )
                records.append(summary)
                stats.incr(RECORDS_EXTRACTED)
            except Exception:
                stats.incr(PARSE_FAILURES)
                logger.exception("Failed to map a listing item, skipping it")

    stats.incr(PAGES_PARSED)
    if not records:
        logger.warning(
            "No listing records extracted -- BBB markup may have changed "
            "(check SCRIPT_TYPE / _iter_listing_items in search_parser.py)"
        )
    return records


def _iter_listing_items(blob: dict[str, Any]):
    """Yield each individual business's raw dict from a parsed JSON blob.

    Placeholder assumption: `{"results": [ {...}, {...} ]}`. Adjust to match
    the real shape once inspected.
    """
    results = blob.get("results")
    if isinstance(results, list):
        yield from results


def _map_listing_item(
    item: dict[str, Any],
    *,
    query: str | None,
    location: str | None,
    page: int | None,
) -> BusinessSummary:
    """Map one raw listing-item dict to BusinessSummary.

    Placeholder field paths -- adjust to match real BBB listing JSON.
    """
    known_keys = {
        "id", "businessName", "phone", "address", "city", "state",
        "postalCode", "url", "rating", "accredited", "categories",
    }

    summary = BusinessSummary(
        bbb_id=item.get("id"),
        name=item.get("businessName") or item.get("name") or "UNKNOWN",
        profile_url=item.get("url"),
        phone=item.get("phone"),
        address=item.get("address"),
        city=item.get("city"),
        state=item.get("state"),
        postal_code=item.get("postalCode"),
        rating=item.get("rating"),
        accredited=item.get("accredited"),
        categories=item.get("categories") or [],
        search_query=query,
        search_location=location,
        source_page=page,
        raw_extra={k: v for k, v in item.items() if k not in known_keys},
    )
    return summary
