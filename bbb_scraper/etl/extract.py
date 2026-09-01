"""
Extract: the only layer that combines scraping (network) with parsing.

Keeping this separate from transform/load means transform/load can be fully
unit tested without ever touching the network (they just consume
BusinessSummary / BusinessDetail objects or plain dicts), and parser
development (parsing/*) can run entirely against saved fixtures without this
module involved either. This is where the two meet.
"""
from __future__ import annotations

from bbb_scraper.config import Settings, settings as default_settings
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
    def __init__(
        self,
        *,
        stats: RunStats | None = None,
        session_id: str | None = None,
        cfg: Settings | None = None,
    ):
        self.cfg = cfg or default_settings
        self.stats = stats or RunStats()
        self.http = HttpClient(self.cfg, stats=self.stats, session_id=session_id)
        self.capture = RawCapture()
        self.search_client = BBBSearchClient(self.http, capture=self.capture, cfg=self.cfg)
        self.business_client = BBBBusinessClient(self.http, capture=self.capture)

    def extract_search(
        self, category: Category, location: Location, max_pages: int | None = None
    ) -> list[BusinessSummary]:
        """Page through search results for one (category, location).

        Every response carries its own `page`/`pageSize`/`totalPages`/
        `totalResults` (confirmed 2026-08-31) -- used here to stop as soon
        as BBB says there's nothing more, and to detect truncation
        precisely: BBB caps `totalPages` at `cfg.bbb_max_search_pages` (15)
        regardless of `pageSize`, so a query with `totalResults` above
        `pageSize * totalPages` has more matches than pagination can ever
        reach (728 results behind a 15 x 15 = 225 reachable window, in the
        response this was modeled on). Getting the rest means running
        multiple narrower searches and deduping the results together, which
        isn't handled here (see etl/dedupe.py -- dedupe works on
        already-extracted records; this is about the extraction strategy
        feeding it).
        """
        cap = self.cfg.bbb_max_search_pages
        max_pages = min(max_pages, cap) if max_pages is not None else cap

        all_summaries: list[BusinessSummary] = []
        total_results = page_size = reported_pages = None

        for page in range(1, max_pages + 1):
            result = self.search_client.search(category, location, page=page)
            total_results = result.data.get("totalResults")
            page_size = result.data.get("pageSize")
            reported_pages = result.data.get("totalPages")

            summaries = parse_search_results(
                result.data, category=category, location=location, page=page, stats=self.stats
            )
            logger.info(
                "Parsed %d listing record(s) from page %d for category=%r location=%r",
                len(summaries), page, category.name, location.display,
            )
            all_summaries.extend(summaries)

            if not summaries:
                break
            if reported_pages is not None and page >= reported_pages:
                break  # fetched every page BBB says exists for this query

        if total_results is not None and page_size is not None and reported_pages is not None:
            reachable = page_size * reported_pages
            if total_results > reachable:
                logger.warning(
                    "category=%r location=%r reports %d total results but only %d "
                    "are reachable through pagination (pageSize=%d x totalPages=%d) "
                    "-- narrow this search and dedupe against other searches for the rest.",
                    category.name, location.display, total_results, reachable,
                    page_size, reported_pages,
                )

        return all_summaries

    def extract_business(self, profile_url: str, *, referer: str | None = None) -> BusinessDetail:
        result = self.business_client.fetch(profile_url, referer=referer)
        return parse_business_page(result.html, profile_url=profile_url, stats=self.stats)

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "Extractor":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
