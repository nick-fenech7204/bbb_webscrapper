"""
Parse a BBB `/api/search` JSON response into BusinessSummary records.

Confirmed 2026-08-31 against a real captured response (see
tests/fixtures/search_listing_sample.json) -- this mapping is no longer a
guess. The response's top-level shape:

    {
      "page": 1, "pageSize": 15, "totalPages": 15, "totalResults": 728,
      "results": [ {...one business...}, ... ],
      "filters": {...category/state filter options...},
      "relatedCategories": [...], "mostPopularCategories": [...],
      ...
    }

`totalPages`/`totalResults`/`pageSize` are read by etl/extract.py to decide
when to stop paging -- not needed here, this module only maps `results`.
"""
from __future__ import annotations

from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.parsing.models import BusinessSummary
from bbb_scraper.reference.models import Category, Location
from bbb_scraper.utils.stats import PAGES_PARSED, PARSE_FAILURES, RECORDS_EXTRACTED, RunStats

logger = get_logger(__name__)

BBB_BASE_URL = "https://www.bbb.org"

# Keys explicitly mapped below -- everything else on a result item lands in
# raw_extra automatically, so nothing gets silently dropped as BBB's schema
# is explored further (e.g. serviceArea*, requestAQuoteUrl*, tobText/tobId,
# charitySeal/isCharity/accreditedCharity, businessLoginUrl, logoUri, ...).
_MAPPED_KEYS = {
    "id", "businessId", "businessName", "reportUrl", "localReportUrl", "phone",
    "address", "city", "state", "postalcode", "location", "rating", "ratingScore",
    "bbbMember", "categories", "bbbId", "bbbName",
}


def parse_search_results(
    data: dict[str, Any],
    *,
    category: Category | None = None,
    location: Location | None = None,
    page: int | None = None,
    stats: RunStats | None = None,
) -> list[BusinessSummary]:
    stats = stats or RunStats()
    records: list[BusinessSummary] = []

    for item in _iter_listing_items(data):
        try:
            summary = _map_listing_item(item, category=category, location=location, page=page)
            records.append(summary)
            stats.incr(RECORDS_EXTRACTED)
        except Exception:
            stats.incr(PARSE_FAILURES)
            logger.exception("Failed to map a listing item, skipping it")

    stats.incr(PAGES_PARSED)
    if not records:
        logger.warning(
            "No listing records extracted for category=%r location=%r page=%s -- "
            "genuinely no results, or BBB changed its response shape (check "
            "_iter_listing_items in search_parser.py)",
            category.name if category else None, location.display if location else None, page,
        )
    return records


def _iter_listing_items(data: dict[str, Any]):
    results = data.get("results")
    if isinstance(results, list):
        yield from results


def parse_response_center(data: dict[str, Any]) -> tuple[float, float] | None:
    """The resolved center lat/lon BBB's own geocoding attached to this
    response's `location` block. Confirmed 2026-09-02: only populated when
    the search used a place-name `find_loc` -- a `find_latlng`-based search
    has no named place to resolve, so `location` stays null there. This is
    what `Extractor.extract_search_coverage` uses instead of a separate
    geocoding step/library: the first search against a named location
    already tells us where BBB thinks its center is.
    """
    location = data.get("location")
    if not isinstance(location, dict):
        return None
    lat_lng = location.get("latLng")
    if not lat_lng or "," not in lat_lng:
        return None
    try:
        lat_str, lon_str = lat_lng.split(",", 1)
        return float(lat_str), float(lon_str)
    except ValueError:
        return None


def _parse_latlon(location_str: str | None) -> tuple[float | None, float | None]:
    """BBB's per-result `location` field is a plain "lat,lon" string."""
    if not location_str or "," not in location_str:
        return None, None
    try:
        lat_str, lon_str = location_str.split(",", 1)
        return float(lat_str), float(lon_str)
    except ValueError:
        return None, None


def _map_listing_item(
    item: dict[str, Any],
    *,
    category: Category | None,
    location: Location | None,
    page: int | None,
) -> BusinessSummary:
    # `id` (e.g. "0292_3089_178405") is per-LISTING, not per-company -- a
    # business with several branch addresses shows up as several results
    # sharing the same bbbId+businessId but a different `id` per address
    # (confirmed 2026-08-31: e.g. "Barnes, Dennig & Company" appeared 3x on
    # one page, one per branch). Using bbbId+businessId as the stable id
    # would silently collapse distinct branches together at dedupe time --
    # `id` is the one that's actually unique per row.
    #
    # Same trap, different field: `reportUrl` points to the business's
    # CANONICAL address and is identical across every branch's row (so
    # fetching it for a non-canonical branch silently returns a different
    # branch's page). `localReportUrl` carries the branch-specific
    # /addressId/N suffix and is only non-null for non-canonical branches --
    # confirmed 2026-09-01 (Copper Advisors LLC's Greer row has a distinct
    # localReportUrl while its Greenville/canonical row has none; both rows
    # share the same reportUrl). Prefer it when present.
    report_url = item.get("localReportUrl") or item.get("reportUrl")
    profile_url = f"{BBB_BASE_URL}{report_url}" if report_url else None

    phones = item.get("phone") or []
    phone = phones[0] if phones else None

    lat, lon = _parse_latlon(item.get("location"))

    categories = [c["name"] for c in (item.get("categories") or []) if c.get("name")]

    raw_extra = {k: v for k, v in item.items() if k not in _MAPPED_KEYS}
    if len(phones) > 1:
        raw_extra["phones"] = phones
    if item.get("categories"):
        raw_extra["categories_full"] = item["categories"]  # [{id, name}, ...]

    return BusinessSummary(
        bbb_id=item.get("id"),
        business_id=item.get("businessId"),
        name=item.get("businessName") or "UNKNOWN",
        profile_url=profile_url,
        phone=phone,
        address=item.get("address"),
        city=item.get("city"),
        state=item.get("state"),
        postal_code=item.get("postalcode"),
        lat=lat,
        lon=lon,
        rating=item.get("rating"),
        rating_score=item.get("ratingScore"),
        accredited=item.get("bbbMember"),
        categories=categories,
        bbb_office_id=item.get("bbbId"),
        bbb_office_name=item.get("bbbName"),
        search_category_id=category.id if category else None,
        search_category_name=category.name if category else None,
        search_location=location.display if location else None,
        source_page=page,
        raw_extra=raw_extra,
    )
