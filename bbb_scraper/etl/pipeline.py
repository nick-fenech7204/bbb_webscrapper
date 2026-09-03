"""
ETL orchestration: Extract -> Transform -> Dedupe -> Load.

`ETLPipeline` wires the layers together but stays thin -- it shouldn't grow
BBB-specific or destination-specific logic. If you need a new step (e.g.
enrichment before load), add it as its own function/module and call it from
`run`, same as dedupe.
"""
from __future__ import annotations

from typing import Any

from bbb_scraper.etl.dedupe import dedupe_records
from bbb_scraper.etl.extract import Extractor
from bbb_scraper.etl.transform import transform_detail, transform_summary
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.pipeline.base import Sink
from bbb_scraper.pipeline.registry import build_sinks_from_settings
from bbb_scraper.reference.models import Category, Location, Metro, parse_location
from bbb_scraper.scraping.search import build_referer
from bbb_scraper.utils.stats import RunStats, RECORDS_LOADED

logger = get_logger(__name__)


class ETLPipeline:
    def __init__(self, sinks: list[Sink] | None = None, stats: RunStats | None = None):
        self.stats = stats or RunStats()
        self.sinks = sinks if sinks is not None else build_sinks_from_settings()

    def run_search(
        self,
        category: Category,
        location: Location | None = None,
        max_pages: int | None = None,
        fetch_details: bool = False,
        *,
        coverage: bool = False,
        radius_miles: float = 25.0,
        num_points: int = 16,
        max_pages_per_point: int = 2,
        metro: Metro | None = None,
        metro_radius_miles: float = 40.0,
        metro_min_population: int = 25_000,
        metro_max_pages_per_place: int = 15,
    ) -> dict[str, Any]:
        """Search BBB by category + location, optionally follow through to
        each business's profile page, transform, dedupe, and load into every
        configured sink. Three mutually exclusive modes -- pick one:

        Plain search (default): `location` required, one search.
        `max_pages=None` (the default) pages through up to BBB's own cap
        (`cfg.bbb_max_search_pages`, currently 15 / ~300 results) -- pass a
        smaller number while testing to avoid burning through pages.

        `coverage=True`: `location` required. Switches to
        `Extractor.extract_search_coverage` -- sweeps `num_points` lat/lon
        anchors within `radius_miles` of `location` (each to
        `max_pages_per_point`, not the usual full depth -- see that
        method's docstring for why) to work around BBB's location search
        not actually scoping to a local radius. `max_pages` is ignored.

        `metro=<a Metro>`: `location` not needed (ignored if given).
        Switches to `Extractor.extract_search_metro_coverage` -- sweeps
        every real, substantial (`metro_min_population`+) named city/CDP
        within `metro_radius_miles` of the metro's own resolved center,
        each to `metro_max_pages_per_place`. Confirmed 2026-09-02 this
        reaches real local results `coverage=True` cannot -- BBB's "local"
        pool is tied to the specific named place searched, not just
        proximity to a point. `max_pages`/`coverage` are ignored.

        (If this parameter list keeps growing, it's probably time to
        collapse coverage/metro into a small "search mode" options object
        instead of three more kwargs each -- not done here since three
        already-working modes isn't quite there yet.)
        """
        with Extractor(stats=self.stats) as extractor:
            if metro is not None:
                location = parse_location(metro.seed_location)
                summaries = extractor.extract_search_metro_coverage(
                    category, metro, radius_miles=metro_radius_miles,
                    min_population=metro_min_population,
                    max_pages_per_place=metro_max_pages_per_place,
                )
            elif coverage:
                if location is None:
                    raise ValueError("coverage=True requires a location")
                summaries = extractor.extract_search_coverage(
                    category, location, radius_miles=radius_miles,
                    num_points=num_points, max_pages_per_point=max_pages_per_point,
                )
            else:
                if location is None:
                    raise ValueError("location is required unless metro is given")
                summaries = extractor.extract_search(category, location, max_pages=max_pages)

            summary_records = [transform_summary(s) for s in summaries]
            detail_records: list[dict[str, Any]] = []

            if fetch_details:
                for summary in summaries:
                    if not summary.profile_url:
                        continue
                    try:
                        # Referer matching the search that surfaced this business
                        # -- what a real user's click-through would send.
                        referer = build_referer(category, location, page=summary.source_page or 1)
                        detail = extractor.extract_business(summary.profile_url, referer=referer)
                        detail_records.append(transform_detail(detail))
                    except Exception:
                        logger.exception("Failed to extract business detail for %s", summary.profile_url)

        # Details first: dedupe_records keeps the FIRST occurrence of a given
        # id. A business's detail record can legitimately compute the same
        # id as its own summary record (confirmed 2026-09-02: happens
        # whenever the profile was reached via an /addressId/N-suffixed URL,
        # since that N is also embedded in the search result's own raw id) --
        # when that happens we want the richer detail record to win, not the
        # sparser summary that happened to get transformed first.
        records = dedupe_records(detail_records + summary_records, stats=self.stats)
        self._load(records)

        logger.info("ETL run complete: %s", self.stats.summary_line())
        return {"stats": self.stats.as_dict(), "record_count": len(records)}

    def _load(self, records: list[dict[str, Any]]) -> None:
        for sink in self.sinks:
            try:
                written = sink.load(records)
                self.stats.incr(RECORDS_LOADED, written)
            except Exception:
                logger.exception("Sink %r failed to load records", sink.name)
