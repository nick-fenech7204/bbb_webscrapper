#!/usr/bin/env python
"""
Fetch full business-detail pages for every profile_url already sitting in a
CSV (e.g. one produced by run_search.py without --details), transform,
dedupe, and load into the configured sinks.

Useful whenever you already have listing-level data and want to enrich it
without re-running the search that produced it -- avoids redundant
/api/search requests for data you've already got.

    python scripts/fetch_details.py data/processed/hvac_wa_unique.csv
    python scripts/fetch_details.py data/processed/hvac_wa_unique.csv --url-column profile_url
"""
from __future__ import annotations

import argparse
import csv
import sys

from bbb_scraper.etl.dedupe import dedupe_records
from bbb_scraper.etl.extract import Extractor
from bbb_scraper.etl.transform import transform_detail
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.pipeline.registry import build_sinks_from_settings
from bbb_scraper.utils.stats import RECORDS_LOADED, RunStats

logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch business-detail pages for profile_urls already in a CSV"
    )
    parser.add_argument("csv_path", help="CSV with a profile_url column (e.g. from run_search.py)")
    parser.add_argument("--url-column", default="profile_url")
    parser.add_argument(
        "--progress-every", type=int, default=25, help="Log a progress line every N URLs"
    )
    args = parser.parse_args()

    configure_logging()
    stats = RunStats()

    with open(args.csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    urls = [r[args.url_column] for r in rows if r.get(args.url_column)]
    if not urls:
        logger.error("No non-empty '%s' values found in %s", args.url_column, args.csv_path)
        return 1
    logger.info("Fetching details for %d business profile URL(s) from %s", len(urls), args.csv_path)

    records = []
    with Extractor(stats=stats) as extractor:
        for i, url in enumerate(urls, 1):
            try:
                detail = extractor.extract_business(url)
                records.append(transform_detail(detail))
            except Exception:
                logger.exception("Failed to fetch detail for %s", url)
            if i % args.progress_every == 0 or i == len(urls):
                logger.info("Progress: %d/%d (%d succeeded so far)", i, len(urls), len(records))

    records = dedupe_records(records, stats=stats)

    for sink in build_sinks_from_settings():
        try:
            written = sink.load(records)
            stats.incr(RECORDS_LOADED, written)
        except Exception:
            logger.exception("Sink %r failed to load records", sink.name)

    logger.info("Done: %s", stats.summary_line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
