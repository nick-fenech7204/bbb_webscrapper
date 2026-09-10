"""
Map a Yelp Fusion businesses/search (or businesses/{id}) payload to
YelpBusiness records.

Anything not explicitly mapped below lands in `raw_extra` -- same
never-silently-drop policy as the BBB parsers.
"""
from __future__ import annotations

from typing import Any

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.yelp.models import YelpBusiness

logger = get_logger(__name__)

# Top-level keys on a business object we promote to first-class fields.
# Everything else (attributes, business_hours, is_claimed, ...) -> raw_extra.
_MAPPED_KEYS = {
    "id", "alias", "url", "name", "phone", "display_phone",
    "location", "coordinates", "rating", "review_count", "price",
    "is_closed", "categories", "transactions", "distance", "image_url",
}


def parse_yelp_business(
    raw: dict[str, Any],
    *,
    search_term: str | None = None,
    search_location: str | None = None,
    source_page: int | None = None,
) -> YelpBusiness:
    location = raw.get("location") or {}
    coords = raw.get("coordinates") or {}
    categories = raw.get("categories") or []

    raw_extra = {k: v for k, v in raw.items() if k not in _MAPPED_KEYS}
    # Keep the parts of nested objects we didn't hoist.
    if location.get("address2") or location.get("address3"):
        raw_extra["address_extra"] = {
            "address2": location.get("address2"),
            "address3": location.get("address3"),
        }
    if categories:
        raw_extra["categories"] = categories  # full [{alias, title}, ...]

    return YelpBusiness(
        yelp_id=raw["id"],
        yelp_alias=raw.get("alias"),
        yelp_url=raw.get("url"),
        name=raw["name"],
        phone=raw.get("phone") or None,
        display_phone=raw.get("display_phone") or None,
        address=location.get("address1") or None,
        city=location.get("city") or None,
        state=location.get("state") or None,
        postal_code=location.get("zip_code") or None,
        lat=coords.get("latitude"),
        lon=coords.get("longitude"),
        rating=raw.get("rating"),
        review_count=raw.get("review_count"),
        price=raw.get("price") or None,
        is_closed=raw.get("is_closed"),
        categories=[c.get("title") for c in categories if c.get("title")],
        transactions=list(raw.get("transactions") or []),
        distance_meters=raw.get("distance"),
        image_url=raw.get("image_url") or None,
        search_term=search_term,
        search_location=search_location,
        source_page=source_page,
        raw_extra=raw_extra,
    )


def parse_yelp_search_response(
    data: dict[str, Any],
    *,
    search_term: str | None = None,
    search_location: str | None = None,
    source_page: int | None = None,
) -> list[YelpBusiness]:
    """Map the `businesses` array of one search response. `total` and
    `region` are the caller's concern (pagination) -- not touched here."""
    out: list[YelpBusiness] = []
    for item in data.get("businesses", []):
        try:
            out.append(
                parse_yelp_business(
                    item,
                    search_term=search_term,
                    search_location=search_location,
                    source_page=source_page,
                )
            )
        except Exception:
            logger.exception("failed to parse a Yelp business record; skipping")
    return out
