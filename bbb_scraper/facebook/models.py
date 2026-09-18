"""Data shape for a parsed Facebook business page."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FacebookProfile:
    """One business's Facebook "About" panel, parsed from a real page
    fetch -- see client.py's module docstring for what's confirmed live
    and what isn't. Every field is optional (None / empty) since a real
    page can genuinely be missing any of them (see parser.py -- a
    franchise-managed page skips the phone card entirely, a low-review
    page shows no percentage at all, etc.); nothing here should be
    treated as guaranteed present."""

    url: str
    status: str = "ok"
    """"ok" (a normal profile fetch) or "unavailable" -- confirmed live,
    2026-09-18: one real business's page (out of 15 tested) rendered no
    `og:title`/`og:description` at all and no About-card data either, just
    a login-prompt component -- a real state some pages are in for a
    plain logged-out visit (reason unconfirmed: could be an
    age/region-restricted page, could be something the page owner
    enabled, could be something else), not a parsing failure. Every other
    field stays at its default (None/empty) when this is "unavailable" --
    treat that as genuinely unknown, not as "this business has no phone/
    email/etc.\""""
    name: str | None = None
    categories: list[str] = field(default_factory=list)
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    """As Facebook's own About card shows it -- usually a bare domain,
    but confirmed live (Ponce Dental Group, 2026-09-18) it can still
    carry the business's own UTM/tracking query string, so this is NOT
    guaranteed to be a clean domain the way BBB's `website` field is
    expected to be. Strip query/fragment at display time same as
    site/js/app.js's cleanDomain() does for BBB's own website field."""
    hours_status: str | None = None  # Facebook's own phrasing, e.g. "Open now" / "Closed now"
    recommend_percentage: int | None = None
    """0-100. None when Facebook itself shows "Not yet rated" rather than
    a percentage (confirmed live, Ponce Dental Group, 4 reviews) -- not a
    parse failure, a real state Facebook renders below some review-count
    threshold we haven't pinned down. review_count is still present
    either way."""
    review_count: int | None = None
    reviews_url: str | None = None
    followers_count: int | None = None
    talking_about_count: int | None = None
    """Facebook's own "N talking about this" figure -- confirmed live to
    be independently optional from followers_count/checkins_count (a real
    quiet page had followers + were-here but no talking-about at all), so
    don't assume all three are always present together."""
    checkins_count: int | None = None  # Facebook's own "N were here"
    bio: str | None = None
    confirmed_owner: str | None = None
    """The legal entity name behind a page, when Facebook shows one
    (INTRO_CARD_CONFIRMED_OWNER_LABEL, confirmed live: "Erie Construction
    Mid-West Inc." for a page whose display name is "Erie Home") -- a
    real legitimacy signal, worth cross-checking against BBB's own legal
    name; only present on some pages, not a claim every business has
    this "verified"."""
    price_range: str | None = None  # e.g. "$$" -- INTRO_CARD_BUSINESS_PRICE, confirmed live
    service_areas: list[str] = field(default_factory=list)
    """Cities/counties Facebook shows the business serving
    (INTRO_CARD_BUSINESS_SERVICE_AREA, confirmed live: a solar installer
    listing 14 real CA/NV counties) -- only present for businesses that
    filled this in on Facebook (service-area trades like solar/plumbing/
    HVAC more than fixed-location ones like dentists, in what's been
    tested so far, but not confirmed as a hard rule)."""
    social_links: list[dict[str, str]] = field(default_factory=list)
    """[{"platform": "instagram", "url": "https://..."}, ...] -- other
    accounts Facebook's own About card links (INTRO_CARD_OTHER_ACCOUNT,
    confirmed live: Instagram/X/YouTube all seen). Same shape as BBB's
    own `socials` field on purpose. Only ever what Facebook itself
    surfaces on the About card -- not a claim this is the business's full
    social footprint."""
