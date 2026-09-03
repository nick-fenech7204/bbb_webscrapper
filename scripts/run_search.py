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

Pass --coverage to sweep multiple nearby search anchors instead of a single
search -- works around BBB's location search not actually scoping to a local
radius (see ETLPipeline.run_search's docstring). `--location` should be a
city/state or ZIP, not raw lat/lon, since the sweep needs BBB to resolve a
center point for it first:

    python scripts/run_search.py --category "Heating and Air Conditioning" \
        --location "Miami, FL" --coverage --radius 25 --num-points 12

Pass --metro to sweep every real, substantial city/CDP near a curated major
metro instead -- reaches real local coverage --coverage's lat/lon anchors
can't (confirmed 2026-09-02, see ETLPipeline.run_search's docstring).
--location is ignored/not needed in this mode:

    python scripts/run_search.py --category "Car Dealers" --metro miami-fl \
        --metro-radius 40 --metro-min-population 25000
    python scripts/run_search.py --list-metros   # see available --metro ids
"""
from __future__ import annotations

import argparse
import json
import sys

from bbb_scraper.etl.pipeline import ETLPipeline
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.reference.categories import CategoryDirectory
from bbb_scraper.reference.metros import MetroDirectory
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
    parser.add_argument(
        "--pages", type=int, default=1,
        help="Max listing pages to fetch (BBB caps at 15 pages / ~300 results per search)",
    )
    parser.add_argument(
        "--details", action="store_true", help="Also fetch each business's profile page"
    )
    parser.add_argument(
        "--list-categories",
        metavar="KEYWORD",
        default=None,
        help="Print categories matching KEYWORD (or all, if omitted) and exit",
    )
    parser.add_argument(
        "--coverage", action="store_true",
        help="Sweep multiple nearby search anchors instead of a single search "
        "(see ETLPipeline.run_search docstring). --location should be a "
        "city/state or ZIP, not lat/lon. --pages is ignored in this mode "
        "-- use --max-pages-per-point instead.",
    )
    parser.add_argument(
        "--radius", type=float, default=25.0,
        help="Coverage mode: sweep radius in miles around --location (default: 25)",
    )
    parser.add_argument(
        "--num-points", type=int, default=16,
        help="Coverage mode: number of search anchors within --radius (default: 16)",
    )
    parser.add_argument(
        "--max-pages-per-point", type=int, default=2,
        help="Coverage mode: max listing pages to fetch per anchor (default: 2 -- "
        "kept low since anchors overlap; see extract_search_coverage docstring)",
    )
    parser.add_argument(
        "--metro", default=None, metavar="ID",
        help="Sweep every real, substantial city/CDP near this curated metro "
        "(id from data/reference/metros.json, e.g. 'miami-fl') instead of a "
        "single --location search. See --list-metros.",
    )
    parser.add_argument(
        "--metro-radius", type=float, default=40.0,
        help="Metro mode: sweep radius in miles around the metro's center (default: 40)",
    )
    parser.add_argument(
        "--metro-min-population", type=int, default=25_000,
        help="Metro mode: only sweep nearby places with at least this population "
        "(default: 25000 -- keeps a big metro's sweep to real substantial cities)",
    )
    parser.add_argument(
        "--metro-max-pages-per-place", type=int, default=15,
        help="Metro mode: max listing pages to fetch per swept place (default: 15, "
        "full depth -- unlike coverage mode's anchors, each place here is a real "
        "named search in its own right, not an arbitrary nearby point)",
    )
    parser.add_argument(
        "--list-metros", action="store_true", help="Print available --metro ids and exit"
    )
    args = parser.parse_args()

    configure_logging()
    directory = CategoryDirectory.load()

    if args.list_metros:
        for m in MetroDirectory.load().all():
            print(f"{m.id}\t{m.name}")
        return 0

    if args.list_categories is not None:
        matches = directory.search(args.list_categories) if args.list_categories else directory.all()
        for c in matches:
            print(f"{c.id}\t{c.name}")
        return 0

    category = resolve_category(directory, args.category) if args.category else prompt_category(directory)

    metro = None
    if args.metro:
        metro = MetroDirectory.load().get(args.metro)
        if metro is None:
            print(f"No metro matched '{args.metro}'. Try --list-metros to see available ids.")
            return 1
        location = None
    else:
        location = parse_location(args.location) if args.location else prompt_location()

    if metro:
        logger.info(
            "Metro search: category=%r metro=%r radius=%gmi min_population=%d "
            "max_pages_per_place=%d",
            category.name, metro.name, args.metro_radius, args.metro_min_population,
            args.metro_max_pages_per_place,
        )
    elif args.coverage:
        logger.info(
            "Coverage search: category=%r location=%r radius=%gmi num_points=%d "
            "max_pages_per_point=%d",
            category.name, location.display, args.radius, args.num_points,
            args.max_pages_per_point,
        )
    else:
        logger.info("Searching category=%r location=%r", category.name, location.display)

    pipeline = ETLPipeline()
    result = pipeline.run_search(
        category, location, max_pages=args.pages, fetch_details=args.details,
        coverage=args.coverage, radius_miles=args.radius, num_points=args.num_points,
        max_pages_per_point=args.max_pages_per_point,
        metro=metro, metro_radius_miles=args.metro_radius,
        metro_min_population=args.metro_min_population,
        metro_max_pages_per_place=args.metro_max_pages_per_place,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
