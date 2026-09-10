"""
Yelp Fusion API client -- thin, quota-aware, disk-cached.

Design notes:
  - **Official API, not scraping.** Bearer-token auth against api.yelp.com.
    No proxy, no browser impersonation -- this is an identified caller and
    should look like one. (curl_cffi is used only because it's already the
    project's HTTP library; no `impersonate=` is set.)
  - **Read-through disk cache.** Every response is written to
    {raw_data_dir}/yelp/{endpoint}/{param-hash}.json and served from there
    on the next identical call. The free tier's quota is small; parser
    iteration and tests must not spend it. Pass `use_cache=False` to force
    a live call.
  - **Quota is discovered, not assumed.** Yelp returns the real limits in
    every response's RateLimit-* headers; `last_rate_limit` holds the most
    recent set. We never hardcode "300/day" or "5000/month" -- we read it.

Endpoints wrapped (v3):
  - businesses/search      -> search(term, location=..., ...)
  - businesses/{id|alias}  -> business(id_or_alias)
  - businesses/matches     -> business_match(name, address1, city, state, ...)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from curl_cffi import requests as curl_requests

from bbb_scraper.config import Settings
from bbb_scraper.config import settings as default_settings
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.utils.hashing import sha256_hex
from bbb_scraper.utils.rate_limit import RateLimiter

logger = get_logger(__name__)

# Yelp caps businesses/search at 50 results per call and offset+limit <= 240.
YELP_SEARCH_MAX_LIMIT = 50
YELP_SEARCH_MAX_OFFSET = 240


class YelpConfigError(Exception):
    """YELP_API_KEY is missing -- nothing to authenticate with."""


class YelpAPIError(Exception):
    """A Yelp API call returned a non-2xx status."""

    def __init__(self, status_code: int, body: str, url: str):
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"Yelp API {status_code} for {url}: {body[:300]}")

    @property
    def is_rate_limited(self) -> bool:
        return self.status_code == 429


class YelpClient:
    def __init__(
        self,
        cfg: Settings | None = None,
        *,
        use_cache: bool = True,
        cache_dir: Path | None = None,
    ):
        self.cfg = cfg or default_settings
        if not self.cfg.yelp_api_key:
            raise YelpConfigError(
                "YELP_API_KEY is not set -- add it to .env "
                "(get a key at https://www.yelp.com/developers/v3/manage_app)"
            )
        self.use_cache = use_cache
        self.cache_dir = cache_dir or (self.cfg.raw_data_dir / "yelp")
        self.rate_limiter = RateLimiter(
            self.cfg.yelp_min_delay_seconds, self.cfg.yelp_max_delay_seconds
        )
        self.session = curl_requests.Session()  # plain HTTP -- no impersonate
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.cfg.yelp_api_key}",
                "Accept": "application/json",
            }
        )
        # Most recent RateLimit-* header set, e.g.
        # {"limit": 5000, "remaining": 4998, "resettime": "2026-09-11T00:00:00+00:00"}
        self.last_rate_limit: dict[str, Any] = {}
        self.calls_made = 0  # live calls this client instance made (cache hits don't count)

    # --- public endpoint wrappers ----------------------------------------

    def search(
        self,
        term: str | None = None,
        *,
        location: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
        limit: int = YELP_SEARCH_MAX_LIMIT,
        offset: int = 0,
        sort_by: str | None = None,
        categories: str | None = None,
        radius: int | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """One page of businesses/search. Returns Yelp's raw JSON
        ({"businesses": [...], "total": N, "region": {...}}).

        Provide `location` (a place string Yelp geocodes) OR
        `latitude`+`longitude`. `term` is the free-text query (industry
        phrase); omit it to get everything near the location.
        """
        if location is None and (latitude is None or longitude is None):
            raise ValueError("search() needs either location= or both latitude= and longitude=")
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if term:
            params["term"] = term
        if location:
            params["location"] = location
        if latitude is not None:
            params["latitude"] = latitude
        if longitude is not None:
            params["longitude"] = longitude
        if sort_by:
            params["sort_by"] = sort_by
        if categories:
            params["categories"] = categories
        if radius is not None:
            params["radius"] = radius  # metres, Yelp max 40000
        params.update(extra)
        return self._get("businesses/search", params)

    def business(self, id_or_alias: str) -> dict[str, Any]:
        """Full detail for one business by Yelp id or alias (the URL slug)."""
        return self._get(f"businesses/{id_or_alias}", {})

    def business_match(
        self,
        *,
        name: str,
        address1: str,
        city: str,
        state: str,
        country: str = "US",
        **extra: Any,
    ) -> dict[str, Any]:
        """Resolve a known name+address to its Yelp listing (1 call).
        Purpose-built for reconciling an existing BBB record."""
        params = {
            "name": name,
            "address1": address1,
            "city": city,
            "state": state,
            "country": country,
            **extra,
        }
        return self._get("businesses/matches", params)

    # --- internals ------------------------------------------------------

    def _cache_path(self, path: str, params: dict[str, Any]) -> Path:
        key = path + "?" + json.dumps(params, sort_keys=True, separators=(",", ":"))
        digest = sha256_hex(key, 32)
        endpoint_dir = path.replace("/", "_")
        return self.cache_dir / endpoint_dir / f"{digest}.json"

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        cache_path = self._cache_path(path, params)
        if self.use_cache and cache_path.exists():
            logger.info("yelp cache hit  %s %s", path, _short_params(params))
            return json.loads(cache_path.read_text(encoding="utf-8"))

        self.rate_limiter.wait()
        url = f"{self.cfg.yelp_api_base_url}/{path}"
        logger.info("yelp GET        %s %s", path, _short_params(params))
        resp = self.session.get(url, params=params, timeout=self.cfg.http_timeout_seconds)
        self.calls_made += 1
        self._record_rate_limit(resp)

        if resp.status_code >= 400:
            raise YelpAPIError(resp.status_code, resp.text, resp.url)

        data = resp.json()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    def _record_rate_limit(self, resp: curl_requests.Response) -> None:
        found: dict[str, Any] = {}
        for header, value in resp.headers.items():
            hl = header.lower().replace("x-", "").replace("-", "")
            if hl == "ratelimitlimit":
                found["limit"] = _maybe_int(value)
            elif hl == "ratelimitremaining":
                found["remaining"] = _maybe_int(value)
            elif hl == "ratelimitresettime":
                found["resettime"] = value
        if found:
            self.last_rate_limit = found
            logger.info(
                "yelp quota      limit=%s remaining=%s reset=%s",
                found.get("limit"), found.get("remaining"), found.get("resettime"),
            )


def _maybe_int(value: str) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _short_params(params: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items() if k not in ("limit", "offset"))
