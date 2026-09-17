"""Data shapes for a MapQuest search response."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MapQuestReview:
    """One review off a MapQuest business search result -- Yelp-sourced
    (MapQuest's own GenericBusiness.reviews field, confirmed 2026-09-15),
    truncated to ~200 characters same as MapQuest's own rendered pages
    (short reviews come through complete; nothing here re-fetches a fuller
    version -- there isn't one at this endpoint)."""

    text: str | None = None
    rating: float | None = None
    """0-5, half-star increments -- MapQuest's own API returns this 0-10
    (confirmed: a real 5-star Yelp review came back as rating=10), halved
    here at parse time so this lines up with BBB/Angi/Yelp's own 0-5 scale
    rather than carrying a fourth different number range downstream."""
    date: str | None = None  # ISO "YYYY-MM-DD", exact -- MapQuest returns it directly, no conversion needed
    reviewer_name: str | None = None
    title: str | None = None  # never observed non-null in real data seen so far, captured in case it is elsewhere


@dataclass
class MapQuestMatch:
    """One resolved business from a MapQuest search -- the candidate a
    caller picked (see matcher.find_business), not necessarily the only
    node the API returned for a query (a search can return several
    same/similar-named businesses; picking the right one is the caller's
    job, not this module's)."""

    mapquest_id: str
    name: str | None = None
    url: str | None = None
    phone: str | None = None  # E.164 as MapQuest returns it, e.g. "+19283015529"
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    rating_provider: str | None = None  # e.g. "YELP" -- confirms the source, not assumed
    rating_value: float | None = None  # the business's aggregate rating, provider's own scale (Yelp: 0-5)
    rating_url: str | None = None
    """A real link back to the rating's own source page -- e.g. a genuine
    yelp.com/biz/... URL (with MapQuest's own attribution params) when
    rating_provider is "YELP", confirmed live 2026-09-17: `rating { url }`
    is a real GraphQL field (found via schema-validation probing, same
    method as everything else on this endpoint -- see client.py's module
    docstring), just never requested before. Distinct from `url` above,
    which is always MapQuest's OWN page for the business, not the
    original source's."""
    categories: list[str] = field(default_factory=list)
    description: str | None = None
    review_count: int = 0  # reviews.totalCount -- may exceed len(reviews) if not every review was returned
    reviews: list[MapQuestReview] = field(default_factory=list)
