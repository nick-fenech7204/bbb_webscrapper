"""
Extract: the only layer that combines scraping (network) with parsing.

Keeping this separate from transform/load means transform/load can be fully
unit tested without ever touching the network (they just consume
BusinessSummary / BusinessDetail objects or plain dicts), and parser
development (parsing/*) can run entirely against saved fixtures without this
module involved either. This is where the two meet.
"""
from __future__ import annotations

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.parsing.business_parser import parse_business_page
from bbb_scraper.parsing.models import BusinessDetail, BusinessSummary
from bbb_scraper.parsing.search_parser import parse_search_results
from bbb_scraper.reference.models import Category, Location
from bbb_scraper.scraping.business import BBBBusinessClient
from bbb_scraper.scraping.capture import RawCapture
from bbb_scraper.scraping.client import HttpClient
from bbb_scraper.scraping.search import BBBSearchClient
from bbb_scraper.utils.stats import RunStats

logger = get_logger(__name__)


class Extractor:
    def __init__(self, *, stats: RunStats | None = None, session_id: str | None = None):
        self.stats = stats or RunStats()
        self.http = HttpClient(stats=self.stats, session_id=session_id)
        self.capture = RawCapture()
        self.search_client = BBBSearchClient(self.http, capture=self.capture)
        self.business_client = BBBBusinessClient(self.http, capture=self.capture)

    def extract_search(
        self, category: Category, location: Location, max_pages: int = 1
    ) -> list[BusinessSummary]:
        all_summaries: list[BusinessSummary] = []
        for page in range(1, max_pages + 1):
            result = self.search_client.search(category, location, page=page)
            summaries = parse_search_results(
                result.html, category=category, location=location, page=page, stats=self.stats
            )
            logger.info(
                "Parsed %d listing record(s) from page %d for category=%r location=%r",
                len(summaries), page, category.name, location.display,
            )
            all_summaries.extend(summaries)
            if not summaries:
                # Empty page likely means we've run past the last page.
                break
        return all_summaries

    def extract_business(self, profile_url: str) -> BusinessDetail:
        result = self.business_client.fetch(profile_url)
        return parse_business_page(result.html, profile_url=profile_url, stats=self.stats)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "Extractor":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
