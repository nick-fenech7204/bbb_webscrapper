"""
MapQuest GraphQL search client.

**How this was found, 2026-09-15 -- not guessed, not reverse-engineered by
this project.** Nick found, via his own browser devtools on a real
mapquest.com search, that the site's frontend calls an unauthenticated
GraphQL endpoint directly and handed over the real captured request. Every
field used here was independently confirmed against live responses before
being relied on:
  - The endpoint takes a free-text business name (`filter.query`) plus
    approximate coordinates (`coordinates`, `location: {near: ...}`) --
    coordinates only need to bias results toward the right metro, not be
    exact; this project's own city-level reference data
    (bbb_scraper.reference.cities) is plenty (confirmed live: Glendale,
    AZ's city-center coordinates correctly resolved "Platinum Exteriors
    Inc" out of Arizona vs. an unrelated same-named Virginia business).
  - A search can return several same/similar-named candidates (confirmed:
    2-3 nodes for several real searches) -- picking the right one is
    mapquest.matcher.find_business's job, not this client's.
  - The matched business's `reviews` field returns real Yelp-sourced
    review text, rating, exact date, and reviewer name *directly in the
    search response* -- no second page fetch needed (unlike BBB's own
    review sub-page, which genuinely is a separate request). Confirmed via
    GraphQL's own schema-validation errors (asking for an invalid field
    returns a clear "Cannot query field X" error, not silent garbage) that
    `rating`/`date`/`title`/`author { name }` are real, valid Review
    fields even though the specific request Nick captured only asked for
    `body`.
  - Review text is truncated to ~200 characters (short reviews come
    through complete) -- same fundamental limit as MapQuest's own rendered
    pages; there is no fuller version available at this endpoint.
  - `reviews` does NOT accept pagination args (confirmed: `reviews(first:
    50)` is a real GraphQL validation error, "Unknown argument"): whatever
    count the API decides to return for a business is all that's
    available here, no further chasing.

**Never tested at real volume.** Every check above was one-off, targeted,
real requests (see this project's own ethical-scraping-boundary practice)
-- rate-limited/retried here the same conservative way Angi's client
started (bbb_scraper.angi.client), not assumed safe to run hard just
because it's unauthenticated.
"""
from __future__ import annotations

from typing import Any

from curl_cffi import requests as curl_requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.exceptions import ScrapeError
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.mapquest.models import MapQuestMatch, MapQuestReview
from bbb_scraper.utils.rate_limit import RateLimiter

logger = get_logger(__name__)

# Every field here was confirmed to actually resolve against the real API
# (see the module docstring) -- nothing speculative left in.
_SEARCH_QUERY = """query SearchQuery($coordinates: GeoPointInput!, $filter: SearchInput!, $first: Int = 20) {
  search(location: {near: $coordinates}, filter: $filter, first: $first) {
    nodes {
      ... on GenericBusiness {
        id
        name
        url
        phone
        location { street region postcode locality __typename }
        rating { provider value __typename }
        categories(withUrlOnly: true) { nodes { name __typename } __typename }
        description
        reviews {
          totalCount
          nodes { body rating date title author { name __typename } __typename }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}"""

_DEFAULT_HEADERS = {
    "accept": "application/graphql-response+json, application/graphql+json, application/json",
    "content-type": "application/json",
    "origin": "https://www.mapquest.com",
    "referer": "https://www.mapquest.com/",
    "x-graphql-client-name": "com.mapquest.consumer",
    "x-graphql-client-version": "2.0.0-client",
}


class MapQuestClient:
    def __init__(self, cfg: Settings | None = None):
        self.cfg = cfg or default_settings
        self.rate_limiter = RateLimiter(self.cfg.mapquest_min_delay_seconds, self.cfg.mapquest_max_delay_seconds)
        self.session = curl_requests.Session()
        self.session.headers.update(_DEFAULT_HEADERS)

    def search(
        self, query_text: str, *, latitude: float, longitude: float, first: int = 5
    ) -> list[MapQuestMatch]:
        """One search call -> every real business node MapQuest returned.
        `first` defaults small (5) -- matching only ever needs a handful of
        candidates (see mapquest.matcher.find_business), not the site's own
        20-result default."""
        payload = {
            "operationName": "SearchQuery",
            "query": _SEARCH_QUERY,
            "variables": {
                "coordinates": {"latitude": latitude, "longitude": longitude},
                "filter": {"query": query_text},
                "first": first,
            },
        }

        @retry(reraise=True, stop=stop_after_attempt(self.cfg.mapquest_max_retries),
               wait=wait_exponential(multiplier=1.5, min=1, max=15),
               retry=retry_if_exception_type(curl_requests.exceptions.RequestException))
        def _do_request():
            self.rate_limiter.wait()
            response = self.session.post(
                self.cfg.mapquest_graphql_url, json=payload, timeout=self.cfg.mapquest_timeout_seconds,
            )
            response.raise_for_status()
            return response

        try:
            response = _do_request()
        except curl_requests.exceptions.RequestException as exc:
            raise ScrapeError(f"MapQuest search failed for {query_text!r}: {exc}") from exc

        data = response.json()
        errors = data.get("errors")
        if errors:
            raise ScrapeError(f"MapQuest search returned GraphQL errors for {query_text!r}: {errors}")

        nodes = ((data.get("data") or {}).get("search") or {}).get("nodes") or []
        # A bare `Place` node (a road, a landmark -- no business data) has
        # no `id` since this query only requests GenericBusiness's fields;
        # filtering on a real id rather than a specific __typename string
        # is more defensive against an unconfirmed alternate concrete type.
        return [_map_node(n) for n in nodes if isinstance(n, dict) and n.get("id")]

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> MapQuestClient:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def _map_review(item: dict[str, Any]) -> MapQuestReview:
    author = item.get("author") or {}
    rating = item.get("rating")
    return MapQuestReview(
        text=item.get("body"),
        rating=(rating / 2.0) if isinstance(rating, (int, float)) else None,
        date=item.get("date"),
        reviewer_name=author.get("name"),
        title=item.get("title"),
    )


def _map_node(node: dict[str, Any]) -> MapQuestMatch:
    location = node.get("location") or {}
    rating = node.get("rating") or {}
    categories_block = node.get("categories") or {}
    reviews_block = node.get("reviews") or {}
    return MapQuestMatch(
        mapquest_id=str(node.get("id")),
        name=node.get("name"),
        url=node.get("url"),
        phone=node.get("phone"),
        street=location.get("street"),
        city=location.get("locality"),
        state=location.get("region"),
        zip_code=location.get("postcode"),
        rating_provider=rating.get("provider"),
        rating_value=rating.get("value"),
        categories=[
            c["name"] for c in (categories_block.get("nodes") or [])
            if isinstance(c, dict) and c.get("name")
        ],
        description=node.get("description"),
        review_count=reviews_block.get("totalCount") or 0,
        reviews=[_map_review(r) for r in (reviews_block.get("nodes") or []) if isinstance(r, dict)],
    )
