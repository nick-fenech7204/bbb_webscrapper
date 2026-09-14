#!/usr/bin/env python
"""
Fetch Angi's companylist category taxonomy and write it to
data/reference/angi_categories.json.

Angi's own category list is only reachable by rendering a real "companylist"
city hub page (e.g. https://www.angi.com/companylist/us/ny/albany/) and
reading its "Top categories" links -- there's no separate directory-of-
categories endpoint. Confirmed 2026-09-14 the same label/slug pairs show up
on every city's hub page (checked Lansing, NY against Albany, NY) -- it's
Angi's one global taxonomy, not something that varies per city, so any real
city slug works as the source page.

Deliberately NOT using bbb_scraper.scraping.client.HttpClient here -- that
wrapper applies BBB-specific machinery (a captured BBB session's cookies,
BBB's own 403/429-means-blocked/rate-limited semantics) that has nothing to
do with fetching a page from a different site entirely. This is a single
one-off unproxied request, the same shape confirmed to work cleanly against
Angi all session (no bot wall observed, unlike bbb.org).

    python scripts/fetch_angi_categories.py
    python scripts/fetch_angi_categories.py --url https://www.angi.com/companylist/us/ny/albany/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bbb_scraper.config import settings
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.scraping.capture import RawCapture

logger = get_logger(__name__)

DEFAULT_CATEGORIES_URL = "https://www.angi.com/companylist/us/ny/albany/"

# Category links on a companylist hub page are bare `{slug}.htm` hrefs (no
# path) -- confirmed distinctive enough on a real page that filtering on
# this shape alone finds exactly the category list, no need to locate the
# right container element.
_CATEGORY_HREF_RE = re.compile(r"^[a-z0-9-]+\.htm$")


def _parse_categories_page(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    categories: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _CATEGORY_HREF_RE.match(href):
            continue
        slug = href[: -len(".htm")]
        if slug in seen:
            continue
        name = a.get_text(strip=True)
        if not name:
            continue
        seen.add(slug)
        categories.append({"id": slug, "name": name, "slug": slug})
    categories.sort(key=lambda c: c["name"])
    return categories


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Angi's companylist category taxonomy")
    parser.add_argument("--url", default=DEFAULT_CATEGORIES_URL)
    parser.add_argument("--out", default=None, help="Defaults to settings.angi_categories_file")
    args = parser.parse_args()

    configure_logging()

    logger.info("GET %s", args.url)
    response = curl_requests.get(args.url, impersonate=settings.http_impersonate or "chrome150", timeout=20)
    response.raise_for_status()

    RawCapture().save(
        kind="angi_categories", identifier="companylist_hub", content=response.text,
        ext="html", url=args.url, status_code=response.status_code,
    )

    categories = _parse_categories_page(response.text)
    if len(categories) < 100:
        logger.warning(
            "Only parsed %d categories -- Angi's page structure may have changed; "
            "inspect the captured HTML before trusting this output.",
            len(categories),
        )
    logger.info("Parsed %d categories", len(categories))

    out_path = Path(args.out) if args.out else settings.angi_categories_file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(categories, indent=2) + "\n", encoding="utf-8")
    logger.info("Wrote %d categories to %s", len(categories), out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
