#!/usr/bin/env python
"""
Fetch BBB's full industry/category taxonomy and write it to
data/reference/categories.json.

TODO(you): `_parse_categories_page` is a stub -- fill it in once you've
found where BBB exposes the full category list (a directory/browse page,
or embedded JSON similar to search-listing results) and know its structure.
Everything else here (fetch via HttpClient, capture raw response, write the
JSON file CategoryDirectory expects) is already wired up.

    python scripts/fetch_categories.py
    python scripts/fetch_categories.py --url https://www.bbb.org/some/categories/page
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.reference.models import Category
from bbb_scraper.scraping.capture import RawCapture
from bbb_scraper.scraping.client import HttpClient

logger = get_logger(__name__)

# Placeholder -- confirm the real URL that lists all BBB categories.
DEFAULT_CATEGORIES_URL = "https://www.bbb.org/categories"


def _parse_categories_page(html: str) -> list[Category]:
    """TODO(you): extract the full category list from `html`.

    Likely candidates once you inspect the real page:
      - a `<script type="application/json">` blob (use
        `bbb_scraper.parsing.json_extract.extract_json_scripts`)
      - a `window.X = {...}` preloaded-state assignment (use
        `extract_window_assignment`)
      - plain `<a>` links in an HTML directory listing (use BeautifulSoup
        directly)

    Must return one Category(id=..., name=..., slug=...) per industry/category.
    """
    raise NotImplementedError(
        "_parse_categories_page is a placeholder -- inspect a real BBB "
        "categories page and implement extraction (see docstring)."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch BBB's category taxonomy")
    parser.add_argument("--url", default=DEFAULT_CATEGORIES_URL)
    parser.add_argument("--out", default=None, help="Defaults to settings.categories_file")
    args = parser.parse_args()

    configure_logging()

    with HttpClient() as http:
        response = http.get(args.url)

    capture = RawCapture()
    capture.save(
        kind="categories",
        identifier="categories_page",
        content=response.text,
        ext="html",
        url=args.url,
        status_code=response.status_code,
    )

    categories = _parse_categories_page(response.text)
    logger.info("Parsed %d categories", len(categories))

    out_path = Path(args.out) if args.out else settings.categories_file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([c.model_dump() for c in categories], indent=2), encoding="utf-8"
    )
    logger.info("Wrote %d categories to %s", len(categories), out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
