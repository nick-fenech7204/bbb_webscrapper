"""
BBB search/listing requests.

TODO(you): confirm the exact search URL/query params from a real browser or
curl capture -- `build_search_url` below is a best-guess placeholder based on
BBB's public URL pattern (bbb.org/search?find_text=...&find_loc=...&page=...).
Everything downstream (capture, parsing) doesn't care what the URL looks
like, so this is the only function you should need to touch.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from bbb_scraper.scraping.capture import CaptureResult, RawCapture
from bbb_scraper.scraping.client import HttpClient
from bbb_scraper.utils.hashing import sha256_hex

BASE_SEARCH_URL = "https://www.bbb.org/search"


def build_search_url(query: str, location: str | None = None, page: int = 1) -> str:
    params = {"find_text": query, "page": page}
    if location:
        params["find_loc"] = location
    return f"{BASE_SEARCH_URL}?{urlencode(params)}"


@dataclass
class SearchPageResult:
    url: str
    query: str
    location: str | None
    page: int
    status_code: int
    html: str
    capture: CaptureResult


class BBBSearchClient:
    def __init__(self, http_client: HttpClient, capture: RawCapture | None = None):
        self.http = http_client
        self.capture = capture or RawCapture()

    def search(self, query: str, location: str | None = None, page: int = 1) -> SearchPageResult:
        url = build_search_url(query, location=location, page=page)
        response = self.http.get(url)

        identifier = f"{query}_{location or 'any'}_p{page}_{sha256_hex(url, 8)}"
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
            query=query,
            location=location,
            page=page,
            status_code=response.status_code,
            html=response.text,
            capture=captured,
        )
