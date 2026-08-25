#!/usr/bin/env python
"""
Example CLI: fetch + parse a single BBB business profile page and print the
normalized record. Useful while iterating on business_parser.py without
running a full search first.

    python scripts/run_business.py "https://www.bbb.org/us/tx/austin/profile/plumbers/example-0000-12345"
"""
from __future__ import annotations

import argparse
import json
import sys

from bbb_scraper.etl.extract import Extractor
from bbb_scraper.etl.transform import transform_detail
from bbb_scraper.logging_setup import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch + parse one BBB business profile page")
    parser.add_argument("url", help="Full BBB business profile URL")
    args = parser.parse_args()

    configure_logging()

    with Extractor() as extractor:
        detail = extractor.extract_business(args.url)

    print(json.dumps(transform_detail(detail), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
