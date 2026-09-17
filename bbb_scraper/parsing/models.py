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
    """BBB's raw `id` field (e.g. "0292_3089_178405"), used as-is. This is
    per-LISTING (one physical address), not per-company: a business with
    several branches shows up as several results sharing `business_id` +
    `bbb_office_id` but a different `bbb_id` per address -- confirmed
    2026-08-31 (see tests/fixtures/search_listing_sample.json, "Barnes,
    Dennig & Company" appears 3x, one per branch). An earlier version of
    this mapping used "{bbbId}-{businessId}" instead, on the wrong
    assumption that the third segment of the raw id was ephemeral/
    search-context-specific -- that collapsed distinct branches together at
    dedupe time. If you ever want company-level grouping instead of
    per-branch, group by `business_id` + `bbb_office_id`, not by `bbb_id`."""
    business_id: str | None = None
    """BBB's raw `businessId` -- identifies the company, shared across all
    of its branch listings. Combine with `bbb_office_id` for a company-level
    grouping key (not globally unique alone -- see bbb_id's docstring)."""
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


class BBBReview(BaseModel):
    """One written review off a business's own `/customer-reviews` sub-page
    -- a genuinely separate fetch from the profile page itself. Confirmed
    2026-09-15: the profile page only ever links to this sub-page and
    carries the aggregate counts (reviews_complaints below); it doesn't
    embed any individual review anywhere, so getting real review text
    needs this second request. See business_parser.parse_business_reviews_page.
    """

    review_id: str | None = None
    reviewer_name: str | None = None
    rating: int | None = None
    text: str | None = None
    date: str | None = None
    """ISO "YYYY-MM-DD", built from BBB's own {"day","month","year"} object
    -- an exact day, not just a month/year label (confirmed against a real
    page: BBB's own review list is already sorted newest-first by this same
    date, "reviewDate desc" per the page's own `sort` field)."""
    business_response_text: str | None = None
    business_response_date: str | None = None
    """Unconfirmed shape -- every real review sampled so far (10, one real
    business) had this null, so whether BBB uses the same {day,month,year}
    object as `date` or a plain string here has never actually been
    observed. Parsed defensively (see _map_review): a dict is formatted the
    same way as `date`, anything else is passed through as a raw string
    rather than guessed at."""

    # Real fields on the raw review object, captured for completeness even
    # though every one of them was also null across every real review
    # sampled so far (10, one real business) -- costs nothing extra since
    # they're already sitting in the same JSON object as everything above,
    # and a rebuttal/back-and-forth thread is exactly the kind of thing
    # worth having if a future fetch ever hits a business where it's
    # populated. Same "parsed defensively, shape unconfirmed where it
    # matters" treatment as business_response_date.
    customer_response_text: str | None = None
    """A customer's own follow-up after a business response -- distinct
    from the review's own original `text` and from `business_response_text`."""
    customer_response_date: str | None = None
    business_rebuttal_text: str | None = None
    """Distinct from business_response_text in BBB's own data -- likely
    BBB's formal dispute/rebuttal process rather than a casual reply,
    though unconfirmed (never observed populated)."""
    business_rebuttal_date: str | None = None
    has_extended_text: bool | None = None
    """True would mean `text` is a truncated preview and the real full
    review lives in `extended_text` instead -- never observed True in any
    real review sampled so far (`text` itself ran as long as 1,110
    characters unassisted), but capture is only 4 keys away for free from
    what's already parsed here, so it's here for if that ever isn't true."""
    extended_text: list[str] | None = None
    """2026-09-17, real scale (a 453-business Denver batch) found the raw
    field is actually a list of dicts (review-thread metadata, not text)
    on some real reviews, not the list[str] this was typed as from an
    earlier 10-review sample -- see business_parser._extended_text's own
    docstring for the parsing fix. Still unused downstream either way."""


class BBBReviewsPage(BaseModel):
    """One page of a business's `/customer-reviews?page=N` sub-page --
    pagination metadata plus that page's reviews. `total_pages`/`num_found`
    read straight off BBB's own `customerReviews` block (its own `sort` is
    "reviewDate desc, id desc" by default -- newest reviews come first,
    confirmed against a real page, not assumed)."""

    reviews: list[BBBReview] = Field(default_factory=list)
    page: int | None = None
    page_size: int | None = None
    total_pages: int | None = None
    num_found: int | None = None


class BusinessDetail(BaseModel):
    """Full record from a BBB individual business-profile page.

    Field set confirmed 2026-09-01 against a real captured page (see
    tests/fixtures/business_page_sample.html) -- no longer a placeholder.
    The page embeds a much richer object than the search API returns
    (`window.__PRELOADED_STATE__.businessProfile`); this is a curated
    subset, everything else lands in raw_extra (notably: license/regulatory
    details under orgDetails.license, and related articles/news).
    """

    bbb_id: str | None = None
    """Self-derived as "{bbbId}_{businessId}_{addressId}" (addressId parsed
    from the page's own urls.localProfile) -- mirrors BusinessSummary.bbb_id's
    per-listing (not per-company) granularity, so records from both parsers
    dedupe/join consistently. See BusinessSummary.bbb_id's docstring."""
    business_id: str | None = None
    bbb_office_id: str | None = None
    bbb_office_name: str | None = None
    is_multi_location: bool | None = None
    """BBB's own flag confirming a business can have multiple addresses --
    the same fact BusinessSummary.bbb_id's docstring found the hard way."""

    name: str
    profile_url: str | None = None
    phone: str | None = None
    email: str | None = None
    """BBB obfuscates emails in the page's JSON (e.g.
    "!~xK_bL!info__at__example__dot__com!~xK_bL!", decoded client-side by
    their own frontend JS before display -- not an access control, just
    scraper-unfriendly encoding of an already-public contact address).
    Decoded here the same way their JS would; raw value kept in raw_extra
    as "email_raw" in case the obfuscation scheme changes and this needs
    revisiting."""
    website: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    lat: float | None = None
    lon: float | None = None

    rating: str | None = None
    """BBB's letter rating, e.g. "A+" -- or "NR" (Not Rated), which is a
    real, valid value (confirmed on the page this was modeled on), not a
    missing-data signal. Independent of `accredited`."""
    accredited: bool | None = None
    accreditation_status: str | None = None
    years_in_business: int | None = None
    bbb_file_opened: str | None = None
    """ISO datetime string, kept as-is rather than parsed -- several sibling
    date fields (accreditationRevoked, newOwnerDate, ...) are legitimately
    null, so this stays a plain optional string rather than risking a parse
    step that has to special-case absence anyway."""
    business_started: str | None = None
    principal_contact: str | None = None
    """"{first} {last}, {title}" for the first contact flagged `isPrincipal`
    in the page data. None if no contact is marked principal. Quick-glance
    convenience -- see `contacts` for the full list (owners/managers aren't
    always flagged principal, and some businesses list several)."""
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    """Every listed contact (owner, management, etc.), not just the
    principal: [{"name": "Gerald Baum", "title": "President",
    "is_principal": true, "is_management": true, "is_primary": true}, ...].
    Empty list if the page lists none."""
    socials: list[dict[str, Any]] = Field(default_factory=list)
    """[{"platform": "facebook", "url": "https://..."}, ...] from the
    page's social media links. Empty list if none listed."""
    reviews_complaints: dict[str, Any] = Field(default_factory=dict)
    """Aggregate counts only: reviews_total, average_rating,
    complaints_total, complaints_closed_past_3yr,
    complaints_closed_past_12mo. Individual review TEXT is a separate
    fetch entirely -- see BBBReview / business_parser.parse_business_reviews_page
    (2026-09-15) -- the profile page itself never embeds it, only a link to
    a `/customer-reviews` sub-page."""
    categories: list[str] = Field(default_factory=list)
    primary_category_name: str | None = None
    primary_category_id: str | None = None
    organization_description: str | None = None
    entity_type: str | None = None
    """e.g. "Corporation", "LLC" -- from orgDetails.typeOfEntity.name."""

    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    raw_extra: dict[str, Any] = Field(default_factory=dict)
