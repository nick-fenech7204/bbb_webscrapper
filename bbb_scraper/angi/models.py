"""Data shapes for Angi listing pages and individual business profile pages."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ListingPage:
    """One page of a (category, metro) companylist search."""

    profile_urls: list[str]  # relative paths, e.g. /companylist/us/il/.../foo-reviews-123.htm
    result_count: int | None  # total businesses matching, across all pages
    page_size: int | None
    page: int | None


@dataclass
class RatingBreakdown:
    star: int
    count: int
    percentage: float


@dataclass
class Review:
    """One written review off a business's own profile page -- comes free
    with the same detail-page fetch that gets name/phone/categories/etc.
    (embedded directly in the page's own flight data, up to whatever page
    size Angi renders server-side, ~25 -- see parsing.py's docstring for
    why pagination beyond that first page isn't chased: no confirmed link
    pattern for it, and reviews already arrive newest-first, which is
    exactly what matters most for a recency-weighted read anyway)."""

    text: str | None = None
    rating: int | None = None
    reviewer_name: str | None = None
    date_label: str | None = None  # Angi's own granularity: "April 2026", not an exact day
    is_verified: bool | None = None
    job_label: str | None = None  # what the review is actually about, e.g. "Roof Repair"
    business_response_text: str | None = None


@dataclass
class BusinessDetail:
    """Everything pulled off one business's own Angi profile page. Fields
    are None/empty when a business simply hasn't filled that part of its
    profile in -- never guessed or defaulted to something that looks real.
    """

    profile_url: str  # absolute URL, the record's own stable identifier
    name: str | None = None
    phone: str | None = None
    website: str | None = None
    address: str | None = None  # full "street, city, state zip" as shown
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None

    overall_rating: float | None = None
    review_count: int | None = None
    rating_breakdown: list[RatingBreakdown] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)

    categories: list[str] = field(default_factory=list)  # services offered
    about_us: str | None = None
    # Raw "typeId:N" codes (e.g. "typeId:5=20"), not decoded to a label like
    # "20 years of experience" -- see parsing.py's parse_business_detail for
    # why (no confirmed typeId->label mapping; an honest code beats a guess).
    highlights: list[str] = field(default_factory=list)

    is_paid_pro: bool | None = None
    is_corporate_account: bool | None = None
    is_super_service_award_winner: bool | None = None
    bonded: bool | None = None
    insured: bool | None = None
    licenses: list[str] = field(default_factory=list)

    # Set by the orchestrator (scraper.py), not derivable from the profile
    # page alone -- which (category, metro) search this business was found
    # through, for traceability back to the run that produced a given row.
    searched_category: str | None = None
    searched_metro: str | None = None
