#!/usr/bin/env python
"""
Example CLI: run a BBB search and pipe results through the full ETL pipeline
into whatever sinks are configured in .env (OUTPUT_SINKS).

    python scripts/run_search.py "plumbers" --location "Austin, TX" --pages 2
    python scripts/run_search.py "plumbers" --details   # also fetch each profile page
"""
from __future__ import annotations

import argparse
import json
import sys

from bbb_scraper.etl.pipeline import ETLPipeline
from bbb_scraper.logging_setup import configure_logging, get_logger

logger = get_logger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a BBB search through the ETL pipeline")
    parser.add_argument("query", help="Search text, e.g. 'plumbers'")
    parser.add_argument("--location", default=None, help="e.g. 'Austin, TX'")
    parser.add_argument("--pages", type=int, default=1, help="Max listing pages to fetch")
    parser.add_argument(
        "--details", action="store_true", help="Also fetch each business's profile page"
    )
    args = parser.parse_args()

    configure_logging()

    pipeline = ETLPipeline()
    result = pipeline.run_search(
        args.query, location=args.location, max_pages=args.pages, fetch_details=args.details
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
