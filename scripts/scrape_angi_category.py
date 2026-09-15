#!/usr/bin/env python
"""
Scrape one Angi (category, metro) companylist search down to every business's
own profile page, and write the result to CSV.

    python scripts/scrape_angi_category.py --state il --city chicago \\
        --category plumbing --metro-label "Chicago, IL" --max-businesses 100 \\
        --output data/processed/angi/plumbers--chicago-il.csv

`--category` is an Angi URL slug (data/reference/angi_categories.json's
`slug` -- NOT a guessable slugification of the display name, e.g. "HVAC
Companies" is `hvac`, "Antenna Repair" is `tv-antenna`; pass --category-name
instead to look the slug up by display name). `--state`/`--city` are Angi's
own URL slugs, confirmed by browsing to
https://www.angi.com/companylist/us/{state}/ first if unsure -- they don't
necessarily match BBB's spelling for the same place.

`--max-businesses` matters: many real categories run into the hundreds or
low thousands of results for one city (confirmed before this script was
ever written -- Chicago Plumbers alone is 1091) -- there's no default cap
here on purpose (explicit is safer than a surprising default for something
that drives real request volume against a real site), but pass one.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bbb_scraper.angi.models import BusinessDetail
from bbb_scraper.angi.scraper import scrape_category
from bbb_scraper.config import settings
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.reference.categories import CategoryDirectory

logger = get_logger(__name__)

_CSV_FIELDS = [
    "name", "phone", "website", "address", "street", "city", "state", "zip_code",
    "overall_rating", "review_count",
    "rating_5_star_pct", "rating_4_star_pct", "rating_3_star_pct", "rating_2_star_pct", "rating_1_star_pct",
    "is_paid_pro", "is_corporate_account", "is_super_service_award_winner",
    "bonded", "insured", "licenses",
    "categories", "num_categories", "about_us", "highlights",
    "searched_category", "searched_metro", "profile_url",
]


def _round_or_none(value: float | None, digits: int) -> float | None:
    return round(value, digits) if value is not None else None


def _row(d: BusinessDetail) -> dict:
    breakdown_by_star = {b.star: b.percentage for b in d.rating_breakdown}
    return {
        "name": d.name, "phone": d.phone, "website": d.website,
        "address": d.address, "street": d.street, "city": d.city,
        "state": d.state, "zip_code": d.zip_code,
        # Angi's own overallRating is a raw float division (e.g.
        # 4.933734939759036) -- real precision, not a display value, so
        # round it for a CSV a person actually reads.
        "overall_rating": _round_or_none(d.overall_rating, 2),
        "review_count": d.review_count,
        "rating_5_star_pct": _round_or_none(breakdown_by_star.get(5), 1),
        "rating_4_star_pct": _round_or_none(breakdown_by_star.get(4), 1),
        "rating_3_star_pct": _round_or_none(breakdown_by_star.get(3), 1),
        "rating_2_star_pct": _round_or_none(breakdown_by_star.get(2), 1),
        "rating_1_star_pct": _round_or_none(breakdown_by_star.get(1), 1),
        "is_paid_pro": d.is_paid_pro, "is_corporate_account": d.is_corporate_account,
        "is_super_service_award_winner": d.is_super_service_award_winner,
        "bonded": d.bonded, "insured": d.insured,
        "licenses": "; ".join(d.licenses),
        "categories": "; ".join(d.categories), "num_categories": len(d.categories),
        "about_us": d.about_us, "highlights": "; ".join(d.highlights),
        "searched_category": d.searched_category, "searched_metro": d.searched_metro,
        "profile_url": d.profile_url,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", required=True, help='Angi URL slug, e.g. "il"')
    parser.add_argument("--city", required=True, help='Angi URL slug, e.g. "chicago"')
    cat_group = parser.add_mutually_exclusive_group(required=True)
    cat_group.add_argument("--category", help='Angi category slug, e.g. "plumbing"')
    cat_group.add_argument("--category-name", help='Angi category display name, e.g. "Plumbers" -- '
                            "looked up in data/reference/angi_categories.json")
    parser.add_argument("--metro-label", default=None, help='Display label, e.g. "Chicago, IL" (default: City, ST)')
    parser.add_argument("--max-businesses", type=int, default=None,
                         help="Cap total businesses fetched (recommended -- see module docstring)")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--use-proxy", action=argparse.BooleanOptionalAction, default=True,
        help="Route through PROXY_* (.env), rotating sessions every "
        "settings.angi_proxy_rotate_every requests (default: on -- a real run drew real 429s "
        "on a direct connection; pass --no-use-proxy only for local debugging).",
    )
    args = parser.parse_args()

    configure_logging()

    category_slug = args.category
    category_label = args.category
    if args.category_name:
        directory = CategoryDirectory.load(settings.angi_categories_file)
        match = directory.search(args.category_name)
        if not match:
            print(f"No Angi category matches {args.category_name!r} -- see data/reference/angi_categories.json")
            return 1
        if len(match) > 1:
            print(f"{args.category_name!r} matched {len(match)} categories, pick one with --category <slug>:")
            for c in match:
                print(f"  {c.slug!r} -- {c.name}")
            return 1
        category_slug = match[0].slug
        category_label = match[0].name

    metro_label = args.metro_label or f"{args.city.replace('-', ' ').title()}, {args.state.upper()}"

    print(f"Scraping Angi: category={category_label!r} ({category_slug}), metro={metro_label!r} "
          f"({args.state}/{args.city}), max_businesses={args.max_businesses}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    count = 0
    skipped_empty = 0

    def on_progress(stage: str, done, total) -> None:
        if stage == "listing":
            print(f"  listing page {done}" + (f"/{total}" if total else ""))
        elif done % 10 == 0 or done == total:
            print(f"  {done}" + (f"/{total}" if total else "") + " businesses fetched")

    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for detail in scrape_category(
            args.state, args.city, category_slug,
            category_label=category_label, metro_label=metro_label,
            max_businesses=args.max_businesses, use_proxy=args.use_proxy, on_progress=on_progress,
        ):
            if detail.name is None:
                # Genuinely nothing gathered (exhausted _MAX_DETAIL_ATTEMPTS
                # without ever seeing the full page variant -- see
                # scraper.py) -- a blank CSV row would look like a data
                # error, not the "tried, didn't get it" it actually is.
                skipped_empty += 1
                continue
            writer.writerow(_row(detail))
            f.flush()  # a long run is worth being able to tail/interrupt safely, same reasoning as
            count += 1  # the batch scraper's periodic checkpoints -- see streamlit_app.py's module docstring

    elapsed = time.time() - started
    print(f"\nWrote {count} businesses to {args.output} in {elapsed / 60:.1f} min"
          + (f" ({skipped_empty} never returned full data, skipped)" if skipped_empty else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
