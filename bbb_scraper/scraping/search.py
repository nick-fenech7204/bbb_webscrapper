"""
BBB search -- real JSON API, not HTML.

Confirmed 2026-08-31 against a captured working request: BBB's search
results are served directly as JSON from `/api/search`, so there's no
HTML/embedded-JSON unwrapping needed for search at all (unlike the
individual business profile page, which does still appear to embed its data
in a preloaded-state script -- see business.py / business_parser.py).

Params confirmed working end-to-end via live requests (2026-08-31):
    find_country = "USA"
    find_latlng  = "<lat>,<lon>"          confirmed working
    find_loc     = "<City, ST>"           also confirmed working, on its own,
                                           without find_latlng present at all
    find_text    = "<category phrase>"    e.g. "accredited cpa" or "CPA" --
                                           this IS the category; there's no
                                           separate opaque category id in the
                                           request itself
    find_type    = "Category"             static, always this exact value
    page         = 1..totalPages          response reports its own totalPages
                                           (capped at 15) -- see etl/extract.py

`find_latlng` is preferred when a Location carries lat/lon (more precise),
falling back to `find_loc` otherwise -- both paths are live-confirmed, not a
guess in either direction.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from bbb_scraper.config import Settings, settings as default_settings
from bbb_scraper.exceptions import ParsingError
from bbb_scraper.reference.models import Category, Location
from bbb_scraper.scraping.capture import CaptureResult, RawCapture
from bbb_scraper.scraping.client import HttpClient
from bbb_scraper.utils.hashing import sha256_hex


def build_search_params(
    category: Category, location: Location, page: int = 1, cfg: Settings | None = None
) -> dict[str, str | int]:
    cfg = cfg or default_settings
    params: dict[str, str | int] = {
        "find_country": cfg.bbb_find_country,
        "find_text": category.name,
        "find_type": "Category",
        "page": page,
    }
    if location.lat is not None and location.lon is not None:
        params["find_latlng"] = f"{location.lat},{location.lon}"
    else:
        params["find_loc"] = location.display
    return params


def build_referer(category: Category, location: Location, page: int = 1) -> str:
    """Best-effort referer mirroring the search HTML page a real browser
    would have been on when this XHR fired -- matches how real traffic
    looks, not required for the request to succeed.
    """
    params = {
        "find_country": "USA",
        "find_text": category.name,
        "find_loc": location.display,
        "find_type": "Category",
        "page": page,
    }
    return f"https://www.bbb.org/search?{urlencode(params)}"


@dataclass
class SearchPageResult:
    category: Category
    location: Location
    page: int
    status_code: int
    data: dict
    capture: CaptureResult


class BBBSearchClient:
    def __init__(
        self,
        http_client: HttpClient,
        capture: RawCapture | None = None,
        cfg: Settings | None = None,
    ):
        self.http = http_client
        self.capture = capture or RawCapture()
        self.cfg = cfg or default_settings

    def search(self, category: Category, location: Location, page: int = 1) -> SearchPageResult:
        params = build_search_params(category, location, page=page, cfg=self.cfg)
        referer = build_referer(category, location, page=page)
        # XHR-style headers, deliberately different from business.py's
        # document-navigation ones -- see business.py's module docstring.
        headers = {
            "accept": "*/*",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "referer": referer,
        }

        response = self.http.get(self.cfg.bbb_search_url, params=params, headers=headers)

        try:
            data = response.json()
        except ValueError as exc:
            raise ParsingError(
                f"Expected JSON from {self.cfg.bbb_search_url}, got something else "
                f"(status={response.status_code}, "
                f"content-type={response.headers.get('content-type')}). Usually means "
                "the session cookies expired or the proxy IP got Cloudflare-challenged "
                "-- see bbb_scraper/scraping/session.py."
            ) from exc

        identifier = f"{category.name}_{location.display}_p{page}_{sha256_hex(response.url, 8)}"
        captured = self.capture.save(
            kind="search",
            identifier=identifier,
            content=response.text,
            ext="json",
            url=response.url,
            status_code=response.status_code,
        )

        return SearchPageResult(
            category=category,
            location=location,
            page=page,
            status_code=response.status_code,
            data=data,
            capture=captured,
        )
