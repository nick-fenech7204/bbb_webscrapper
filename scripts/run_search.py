#!/usr/bin/env python
"""
Example CLI: run a BBB search by industry/category + location and pipe
results through the full ETL pipeline into whatever sinks are configured in
.env (OUTPUT_SINKS).

Category and location can be passed as flags, or left out for an
interactive prompt:

    python scripts/run_search.py --category plumbers --location "Austin, TX"
    python scripts/run_search.py --category plumbers --location 78701 --pages 2 --details
    python scripts/run_search.py                      # interactive prompts
    python scripts/run_search.py --list-categories plumb   # discovery only
"""
from __future__ import annotations

import argparse
import json
import sys

from bbb_scraper.etl.pipeline import ETLPipeline
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.reference.categories import CategoryDirectory
from bbb_scraper.reference.models import Category, Location, parse_location

logger = get_logger(__name__)


def resolve_category(directory: CategoryDirectory, text: str) -> Category:
    """Resolve a --category flag value (id, slug, exact name, or partial
    name) to exactly one Category, or exit with the candidates if ambiguous.
    """
    exact = directory.get(text)
    if exact:
        return exact

    matches = directory.search(text)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"'{text}' matched multiple categories -- be more specific, or use the id:")
        for c in matches:
            print(f"  {c.id}\t{c.name}")
        sys.exit(1)

    print(f"No category matched '{text}'. Try --list-categories to browse available ones.")
    sys.exit(1)


def prompt_category(directory: CategoryDirectory) -> Category:
    if not directory.all():
        print(
            "No categories loaded (data/reference/categories.json is empty/missing). "
            "See scripts/fetch_categories.py, or pass --category with a raw id."
        )
        sys.exit(1)

    while True:
        text = input("Industry/category (keyword, or 'list' to see all): ").strip()
        if text.lower() == "list":
            for c in directory.all():
                print(f"  {c.id}\t{c.name}")
            continue

        matches = directory.search(text)
        if not matches:
            print("No matches -- try again.")
            continue
        if len(matches) == 1:
            return matches[0]

        print("Multiple matches:")
        for i, c in enumerate(matches, 1):
            print(f"  [{i}] {c.name} ({c.id})")
        choice = input(f"Select 1-{len(matches)}: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(matches):
            return matches[int(choice) - 1]
        print("Invalid selection -- try again.")


def prompt_location() -> Location:
    text = input("Location (city, ST or ZIP code): ").strip()
    while not text:
        text = input("Location (city, ST or ZIP code): ").strip()
    return parse_location(text)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a BBB category + location search through the ETL pipeline"
    )
    parser.add_argument(
        "--category", default=None, help="Category id/slug/name, e.g. 'plumbers'"
    )
    parser.add_argument("--location", default=None, help="'City, ST' or ZIP code")
    parser.add_argument("--pages", type=int, default=1, help="Max listing pages to fetch")
    parser.add_argument(
        "--details", action="store_true", help="Also fetch each business's profile page"
    )
    parser.add_argument(
        "--list-categories",
        metavar="KEYWORD",
        default=None,
        help="Print categories matching KEYWORD (or all, if omitted) and exit",
    )
    args = parser.parse_args()

    configure_logging()
    directory = CategoryDirectory.load()

    if args.list_categories is not None:
        matches = directory.search(args.list_categories) if args.list_categories else directory.all()
        for c in matches:
            print(f"{c.id}\t{c.name}")
        return 0

    category = resolve_category(directory, args.category) if args.category else prompt_category(directory)
    location = parse_location(args.location) if args.location else prompt_location()

    logger.info("Searching category=%r location=%r", category.name, location.display)

    pipeline = ETLPipeline()
    result = pipeline.run_search(
        category, location, max_pages=args.pages, fetch_details=args.details
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
