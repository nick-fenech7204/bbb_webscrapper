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
from bbb_scraper.reference.models import Category, Location
from bbb_scraper.utils.stats import RunStats, RECORDS_LOADED

logger = get_logger(__name__)


class ETLPipeline:
    def __init__(self, sinks: list[Sink] | None = None, stats: RunStats | None = None):
        self.stats = stats or RunStats()
        self.sinks = sinks if sinks is not None else build_sinks_from_settings()

    def run_search(
        self,
        category: Category,
        location: Location,
        max_pages: int | None = None,
        fetch_details: bool = False,
    ) -> dict[str, Any]:
        """Search BBB by category + location, optionally follow through to
        each business's profile page, transform, dedupe, and load into every
        configured sink.

        `max_pages=None` (the default) pages through up to BBB's own cap
        (`cfg.bbb_max_search_pages`, currently 15 / ~300 results) -- pass a
        smaller number while testing to avoid burning through pages.
        """
        with Extractor(stats=self.stats) as extractor:
            summaries = extractor.extract_search(category, location, max_pages=max_pages)

            records: list[dict[str, Any]] = [transform_summary(s) for s in summaries]

            if fetch_details:
                for summary in summaries:
                    if not summary.profile_url:
                        continue
                    try:
                        detail = extractor.extract_business(summary.profile_url)
                        records.append(transform_detail(detail))
                    except Exception:
                        logger.exception("Failed to extract business detail for %s", summary.profile_url)

        records = dedupe_records(records, stats=self.stats)
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
