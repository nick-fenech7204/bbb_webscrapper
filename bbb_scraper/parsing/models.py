"""
Normalized output shapes.

These are intentionally permissive (almost everything Optional) because the
exact fields available from BBB's embedded JSON aren't nailed down yet.
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
    """One row from a BBB search/listing page."""

    bbb_id: str | None = None
    name: str
    profile_url: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    rating: str | None = None
    accredited: bool | None = None
    categories: list[str] = Field(default_factory=list)

    search_query: str | None = None
    search_location: str | None = None
    source_page: int | None = None
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    raw_extra: dict[str, Any] = Field(default_factory=dict)


class BusinessDetail(BaseModel):
    """Full record from a BBB individual business-profile page."""

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
