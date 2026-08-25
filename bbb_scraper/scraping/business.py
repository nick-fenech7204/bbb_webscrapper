"""
Individual BBB business-profile page requests.

Unlike search, there's no URL to construct here -- profile URLs come from
the search-results parser (each listing item links to its own profile page).
This module just fetches + captures whatever URL it's handed.
"""
from __future__ import annotations

from dataclasses import dataclass

from bbb_scraper.scraping.capture import CaptureResult, RawCapture
from bbb_scraper.scraping.client import HttpClient
from bbb_scraper.utils.hashing import sha256_hex


@dataclass
class BusinessPageResult:
    url: str
    status_code: int
    html: str
    capture: CaptureResult


class BBBBusinessClient:
    def __init__(self, http_client: HttpClient, capture: RawCapture | None = None):
        self.http = http_client
        self.capture = capture or RawCapture()

    def fetch(self, url: str) -> BusinessPageResult:
        response = self.http.get(url)

        identifier = sha256_hex(url, 24)
        captured = self.capture.save(
            kind="business",
            identifier=identifier,
            content=response.text,
            ext="html",
            url=url,
            status_code=response.status_code,
        )

        return BusinessPageResult(
            url=url,
            status_code=response.status_code,
            html=response.text,
            capture=captured,
        )
