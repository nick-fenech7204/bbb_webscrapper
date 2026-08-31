"""
Normalized output shapes.

`raw_extra` on both models is a catch-all for whatever the field-mapping
functions in search_parser.py / business_parser.py don't explicitly map yet
-- that way iterating on the mapping doesn't lose data, it just leaves it
unstructured until you promote a field to a first-class attribute.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class BusinessSummary(BaseModel):
    """One row from BBB's `/api/search` response.

    Field set confirmed 2026-08-31 against a real captured response (see
    tests/fixtures/search_listing_sample.json) -- this is no longer a guess.
    """

    bbb_id: str | None = None
    """Stable id: "{bbbId}-{businessId}" from the response. Not BBB's raw
    `id` field -- that one includes a third, search-context-specific segment
    that isn't guaranteed stable across different searches for the same
    business (kept in raw_extra as "search_result_id")."""
    name: str
    profile_url: str | None = None
    phone: str | None = None
    """First number from BBB's `phone` list. Full list kept in raw_extra
    (~20% of listings carry more than one)."""
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    lat: float | None = None
    lon: float | None = None
    rating: str | None = None
    rating_score: float | None = None
    accredited: bool | None = None
    """From BBB's `bbbMember` flag. Semantically this reads as "BBB
    Accredited Business" and was true for every result in the response this
    was modeled on (a search already scoped to accredited businesses) -- if
    you see it False somewhere, that's the first real confirmation of what
    it means when accreditation *isn't* present."""
    categories: list[str] = Field(default_factory=list)
    """Category names only (e.g. "CPA"). Each category's own BBB id is kept
    in raw_extra's "categories" (full [{id, name}, ...]) since Category.id
    in reference/ is an unrelated internal key, not this one."""
    bbb_office_id: str | None = None
    """Which local/regional BBB office serves this business (e.g. "0292"),
    distinct from the business's own id."""
    bbb_office_name: str | None = None

    search_category_id: str | None = None
    search_category_name: str | None = None
    search_location: str | None = None
    source_page: int | None = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    raw_extra: dict[str, Any] = Field(default_factory=dict)


class BusinessDetail(BaseModel):
    """Full record from a BBB individual business-profile page.

    Unlike BusinessSummary above, this field set is still a placeholder --
    we haven't captured a real profile page yet. See business_parser.py.
    """

    bbb_id: str | None = None
    name: str
    profile_url: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None

    rating: str | None = None
    accredited: bool | None = None
    accreditation_status: str | None = None
    years_in_business: str | None = None
    bbb_file_opened: str | None = None
    business_started: str | None = None
    principal_contact: str | None = None
    categories: list[str] = Field(default_factory=list)

    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    raw_extra: dict[str, Any] = Field(default_factory=dict)
