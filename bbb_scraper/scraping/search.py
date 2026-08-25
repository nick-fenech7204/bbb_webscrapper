"""
BBB search/listing requests, filtered by industry/category + location.

TODO(you): confirm the exact search URL/query params from a real browser or
curl capture. `build_search_url` currently sends both `find_category`
(assumed to take Category.id) and `find_text` (category name, as a
belt-and-suspenders text fallback in case BBB's search wants free text even
when filtering by category) plus `find_loc`. Adjust the param names/values
to match what BBB's search actually accepts once you've inspected a real
request -- everything downstream (capture, parsing) doesn't care what the
URL looks like, so this is the only function you should need to touch.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from bbb_scraper.reference.models import Category, Location
from bbb_scraper.scraping.capture import CaptureResult, RawCapture
from bbb_scraper.scraping.client import HttpClient
from bbb_scraper.utils.hashing import sha256_hex

BASE_SEARCH_URL = "https://www.bbb.org/search"


def build_search_url(category: Category, location: Location, page: int = 1) -> str:
    params = {
        "find_category": category.id,
        "find_text": category.name,
        "find_loc": location.display,
        "page": page,
    }
    return f"{BASE_SEARCH_URL}?{urlencode(params)}"


@dataclass
class SearchPageResult:
    url: str
    category: Category
    location: Location
    page: int
    status_code: int
    html: str
    capture: CaptureResult


class BBBSearchClient:
    def __init__(self, http_client: HttpClient, capture: RawCapture | None = None):
        self.http = http_client
        self.capture = capture or RawCapture()

    def search(self, category: Category, location: Location, page: int = 1) -> SearchPageResult:
        url = build_search_url(category, location, page=page)
        response = self.http.get(url)

        identifier = f"{category.id}_{location.display}_p{page}_{sha256_hex(url, 8)}"
        captured = self.capture.save(
            kind="search",
            identifier=identifier,
            content=response.text,
            ext="html",
            url=url,
            status_code=response.status_code,
        )

        return SearchPageResult(
            url=url,
            category=category,
            location=location,
            page=page,
            status_code=response.status_code,
            html=response.text,
            capture=captured,
        )
