"""
Individual BBB business-profile page requests.

Unlike search, there's no URL to construct here -- profile URLs come from
the search-results parser (each listing item links to its own profile page).
This module just fetches + captures whatever URL it's handed.

Headers here are confirmed 2026-09-01 against a real captured request --
deliberately different from search.py's (a document navigation sends a
different Accept and sec-fetch-* profile than an XHR does in a real
browser; sending the wrong one for the request type is a mismatch anti-bot
systems can key on). Only the fields that plausibly differ per-request are
set here; shared/stable ones (user-agent, sec-ch-ua, cookies) still come
from the session file via HttpClient -- see scraping/session.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from bbb_scraper.scraping.capture import CaptureResult, RawCapture
from bbb_scraper.scraping.client import HttpClient
from bbb_scraper.utils.hashing import sha256_hex

_DOCUMENT_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


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

    def fetch(self, url: str, *, referer: str | None = None) -> BusinessPageResult:
        """`referer` should be the search-results URL a real user would have
        clicked through from, when the caller has it (Extractor does, via
        the search it just ran) -- falls back to BBB's own search page,
        which is still more plausible than sending no referer at all for a
        page that's realistically never reached any other way.
        """
        headers = {**_DOCUMENT_HEADERS, "referer": referer or "https://www.bbb.org/search"}
        response = self.http.get(url, headers=headers)

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
