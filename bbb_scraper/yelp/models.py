"""
Normalized Yelp Fusion output shape.

Field names deliberately overlap `bbb_scraper.parsing.models.BusinessSummary`
where the concept is the same (name / phone / address / city / state /
postal_code / lat / lon / categories) so that a downstream merge step and
the CSV sinks don't need source-specific branches. Yelp-only fields
(review_count, price, yelp_*, distance_meters, ...) are additive.

`rating` is Yelp's 0-5 float, NOT BBB's letter grade -- different source,
different scale, kept distinct on purpose.

Confirmed 2026-09-10 against a real businesses/search response (see
tests/fixtures/yelp_search_car_dealers_jacksonville.json).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class YelpBusiness(BaseModel):
    yelp_id: str
    """Yelp's opaque business id (e.g. "OkQQ0-P2gyO4FamOKZLUcg"). Stable,
    the join key for the details + reviews + match endpoints."""
    yelp_alias: str | None = None
    """URL slug (e.g. "arlington-toyota-jacksonville-2"). Also accepted by
    the businesses/{id} endpoint in place of yelp_id."""
    yelp_url: str | None = None
    """Public yelp.com profile URL (carries API-attribution query params as
    returned; kept verbatim)."""

    name: str
    phone: str | None = None
    """E.164 as Yelp returns it, e.g. "+19043029611". Normalize at match
    time, not here."""
    display_phone: str | None = None

    address: str | None = None
    """location.address1 only. address2/address3 (suite lines) kept in
    raw_extra."""
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    """location.zip_code."""
    lat: float | None = None
    lon: float | None = None

    rating: float | None = None
    """0.0-5.0, half-star increments. 0.0 with review_count 0 means "no
    ratings yet", not "rated zero"."""
    review_count: int | None = None
    price: str | None = None
    """"$".."$$$$" or None -- frequently absent (was absent for every
    result in the fixture)."""
    is_closed: bool | None = None
    """Yelp's "permanently closed" flag."""

    categories: list[str] = Field(default_factory=list)
    """Category titles (e.g. "Car Dealers"). Yelp's category aliases kept in
    raw_extra["categories"] as the full [{alias, title}, ...]."""
    transactions: list[str] = Field(default_factory=list)
    """e.g. ["delivery", "pickup"] -- mostly food; usually empty otherwise."""

    distance_meters: float | None = None
    """Straight-line distance from the search center, when the query was
    geographic. Yelp-provided; not recomputed."""
    image_url: str | None = None

    search_term: str | None = None
    search_location: str | None = None
    source_page: int | None = None
    """0-based offset / 50 of the page this record came from."""
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    raw_extra: dict[str, Any] = Field(default_factory=dict)
