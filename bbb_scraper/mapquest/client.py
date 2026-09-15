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

**Proved out at real volume, then fully integrated, 2026-09-15.** A 100-
request real sample metro (scripts/fetch_mapquest_reviews.py) ran clean --
zero failures, zero 429s -- at a conservative between-request delay.

**Proxy went through two real, live-tested iterations the same day before
landing correctly -- see bbb_scraper/angi/client.py's own module docstring
for the fuller incident writeup (both clients hit and fixed the identical
issue):**
  1. First: proxied + periodic sticky-session rotation (same shape as
     Angi's own original design) instead of a deliberate delay. Failed
     100% of requests on the very first real batch run -- `curl: (7)
     CONNECT tunnel failed, response 407`, every single search. Root
     cause: a `-session-{id}` suffix is Decodo's *sticky*-session
     mechanism (confirmed against Decodo's own docs), not "rotation" --
     manufacturing a fresh one-off sticky session this often almost
     certainly tripped a concurrent-sticky-session account limit.
  2. Nick's call once this was traced down: no sticky sessions, ever --
     go bare (Decodo's own "rotating" mode by default), and get a
     genuinely fresh exit IP on every request, not just periodically.
     Confirmed live that bare-but-reused doesn't actually rotate (Decodo
     rotates per new *connection*, not per request over a kept-alive
     one) -- `_new_session()` below builds a fresh Session (bare proxy)
     on every single request for real per-request rotation, matching
     "a new proxy per request" exactly.
"""
from __future__ import annotations

import time
from typing import Any

from curl_cffi import requests as curl_requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.exceptions import RateLimitedError, ScrapeError
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.mapquest.models import MapQuestMatch, MapQuestReview
from bbb_scraper.scraping.proxies import get_proxies
from bbb_scraper.utils.rate_limit import RateLimiter

logger = get_logger(__name__)

# Never actually observed against this endpoint (unlike Angi's own real
# 429) -- kept anyway as a defensive cooldown, same value/reasoning as
# bbb_scraper/angi/client.py's own _RATE_LIMIT_COOLDOWN_SECONDS: respond to
# an actual rate-limit signal with a real cooldown, not just a fast retry.
_RATE_LIMIT_COOLDOWN_SECONDS = 25.0

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
    def __init__(self, cfg: Settings | None = None, *, use_proxy: bool = True):
        self.cfg = cfg or default_settings
        self.use_proxy = use_proxy
        self.rate_limiter = RateLimiter(self.cfg.mapquest_min_delay_seconds, self.cfg.mapquest_max_delay_seconds)
        self.session = self._new_session()

    def _new_session(self) -> curl_requests.Session:
        """A brand-new Session -- and therefore, when proxied, a brand-new
        connection to the proxy gateway, which is what actually earns a
        fresh exit IP (see module docstring; changing .proxies on a
        *reused* Session does not). Bare proxy username, no session_id --
        Decodo's own "rotating" mode, never sticky. Mirrors
        bbb_scraper.angi.client.AngiClient._new_session exactly."""
        session = curl_requests.Session()
        session.headers.update(_DEFAULT_HEADERS)
        if self.use_proxy:
            proxies = get_proxies(self.cfg)
            if proxies:
                session.proxies.update(proxies)
            else:
                logger.warning("mapquest: use_proxy=True but get_proxies() returned nothing -- check PROXY_* in .env")
        return session

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
               retry=retry_if_exception_type((curl_requests.exceptions.RequestException, RateLimitedError)))
        def _do_request():
            # A fresh session (-> fresh proxy connection -> fresh exit IP,
            # see module docstring) on every attempt, retries included.
            self.session = self._new_session()
            self.rate_limiter.wait()
            response = self.session.post(
                self.cfg.mapquest_graphql_url, json=payload, timeout=self.cfg.mapquest_timeout_seconds,
            )
            if response.status_code == 429:
                logger.warning(
                    "mapquest: 429 for %r -- cooling down %.0fs before retrying",
                    query_text, _RATE_LIMIT_COOLDOWN_SECONDS,
                )
                time.sleep(_RATE_LIMIT_COOLDOWN_SECONDS)
                raise RateLimitedError(f"MapQuest returned 429 for {query_text!r}")
            response.raise_for_status()
            return response

        try:
            response = _do_request()
        except (curl_requests.exceptions.RequestException, RateLimitedError) as exc:
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
