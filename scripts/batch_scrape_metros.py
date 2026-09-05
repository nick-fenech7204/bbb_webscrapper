#!/usr/bin/env python
"""
Batch metro scraper: run one industry across many metros in one sitting,
checkpointing after every metro (safe to interrupt and resume -- a
re-run skips metros already done) and publishing each one straight to the
static site as soon as it finishes, so `site/` grows incrementally instead
of needing one big all-or-nothing run.

Usage:
    python scripts/batch_scrape_metros.py --industry "Car Dealers" \\
        --metros miami-fl,tampa-fl,orlando-fl

    python scripts/batch_scrape_metros.py --industry "Car Dealers" --all-metros

    python scripts/run_search.py --list-metros   # see available metro ids

Meant to run a long time (many metros x many requests each) -- built to be
launched as a background process (the Streamlit "Batch scraper" page does
this for you; from a plain terminal, background it yourself).

Per metro:
  1. extract_search_metro_coverage (same as a single metro sweep).
  2. transform + dedupe.
  3. optionally --details: fetch full contact info for every unique result
     (off by default -- roughly doubles time per metro; see --help).
  4. write data/processed/batch/<industry-slug>--<metro-id>.csv (the
     resumability checkpoint -- a metro whose file already exists here is
     skipped on a re-run, use --force to redo it anyway).
  5. unless --no-publish: publish that CSV to site/ immediately (calls
     publish_site_data.publish_dataset directly, not as a subprocess).
  6. append into the shared data/processed/businesses.csv sink too, same
     as every other run in this project.

After the whole batch, also rebuilds data/processed/<industry-slug>_all_metros.csv
-- every metro run for this industry so far, concatenated, for a single
"the whole list" file to hand to a mentor/for analysis. Rebuilt from the
per-metro checkpoint files each time (cheap, always consistent), not
incrementally appended.
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # for publish_site_data below

from bbb_scraper.etl.dedupe import dedupe_records  # noqa: E402
from bbb_scraper.etl.extract import Extractor  # noqa: E402
from bbb_scraper.etl.transform import transform_detail, transform_summary  # noqa: E402
from bbb_scraper.logging_setup import configure_logging, get_logger  # noqa: E402
from bbb_scraper.pipeline.registry import build_sinks_from_settings  # noqa: E402
from bbb_scraper.pipeline.sinks.csv_sink import CSVSink  # noqa: E402
from bbb_scraper.reference.metros import MetroDirectory  # noqa: E402
from bbb_scraper.reference.models import Category, Metro, parse_location  # noqa: E402
from bbb_scraper.scraping.search import build_referer  # noqa: E402
from bbb_scraper.utils.stats import RunStats  # noqa: E402
from publish_site_data import publish_dataset, slugify  # noqa: E402

configure_logging()
logger = get_logger(__name__)

BATCH_DIR = REPO_ROOT / "data" / "processed" / "batch"


def scrape_one_metro(
    category: Category, metro: Metro, *,
    radius_miles: float, min_population: int, max_pages_per_place: int,
    fetch_details: bool, stats: RunStats,
) -> list[dict]:
    """One metro's worth of records, deduped -- details-first if
    fetch_details, same merge pattern already proven on the real Miami
    car-dealers run (a business's detail record wins over its own summary
    on id collision, since it's fetched first and dedupe keeps first-seen).
    """
    seed_location = parse_location(metro.seed_location)
    with Extractor(stats=stats) as extractor:
        summaries = extractor.extract_search_metro_coverage(
            category, metro, radius_miles=radius_miles,
            min_population=min_population, max_pages_per_place=max_pages_per_place,
        )
        summary_records = [transform_summary(s) for s in summaries]

        if not fetch_details:
            return dedupe_records(summary_records, stats=stats)

        detail_records = []
        for summary in summaries:
            if not summary.profile_url:
                continue
            try:
                referer = build_referer(category, seed_location, page=summary.source_page or 1)
                detail = extractor.extract_business(summary.profile_url, referer=referer)
                detail_records.append(transform_detail(detail))
            except Exception:
                logger.exception("Detail fetch failed for %s", summary.profile_url)

    return dedupe_records(detail_records + summary_records, stats=stats)


def rebuild_all_metros_file(industry_slug: str) -> Path:
    checkpoint_files = sorted(BATCH_DIR.glob(f"{industry_slug}--*.csv"))
    all_rows: list[dict] = []
    for path in checkpoint_files:
        with path.open(newline="", encoding="utf-8") as f:
            all_rows.extend(csv.DictReader(f))

    out_path = REPO_ROOT / "data" / "processed" / f"{industry_slug}_all_metros.csv"
    if all_rows:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = sorted({key for row in all_rows for key in row.keys()})
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--industry", required=True, help='e.g. "Car Dealers"')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--metros", help="Comma-separated metro ids, e.g. miami-fl,tampa-fl")
    group.add_argument("--all-metros", action="store_true", help="Every metro in data/reference/metros.json")
    parser.add_argument("--radius", type=float, default=40.0, help="Metro sweep radius in miles (default: 40)")
    parser.add_argument("--min-population", type=int, default=25_000, help="Population floor (default: 25000)")
    parser.add_argument("--pages-per-place", type=int, default=15, help="Max pages per swept place (default: 15)")
    parser.add_argument(
        "--details", action="store_true",
        help="Fetch full contact details for every unique business (off by default -- "
        "roughly doubles time per metro; a broad first pass usually wants breadth over "
        "detail, enrich a specific subset with details later if needed)",
    )
    parser.add_argument(
        "--publish", action=argparse.BooleanOptionalAction, default=True,
        help="Publish each metro to site/ as soon as it's done (default: on: --no-publish to skip)",
    )
    parser.add_argument("--force", action="store_true", help="Redo metros that already have a checkpoint file")
    args = parser.parse_args()

    directory = MetroDirectory.load()
    if args.all_metros:
        metros = directory.all()
    else:
        ids = [m.strip() for m in args.metros.split(",") if m.strip()]
        metros = []
        for metro_id in ids:
            metro = directory.get(metro_id)
            if metro is None:
                print(f"No metro matched '{metro_id}'. Run scripts/run_search.py --list-metros to see valid ids.")
                return 1
            metros.append(metro)

    if not metros:
        print("No metros selected.")
        return 1

    category = Category(id=slugify(args.industry), name=args.industry)
    industry_slug = slugify(args.industry)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Batch: {len(metros)} metro(s), industry={args.industry!r}, "
          f"radius={args.radius}mi, min_population={args.min_population}, "
          f"pages_per_place={args.pages_per_place}, details={args.details}, publish={args.publish}")

    done = 0
    skipped = 0
    for i, metro in enumerate(metros, 1):
        checkpoint_path = BATCH_DIR / f"{industry_slug}--{metro.id}.csv"
        if checkpoint_path.exists() and not args.force:
            print(f"[{i}/{len(metros)}] {metro.name}: already done (checkpoint exists) -- skipping. Use --force to redo.")
            skipped += 1
            continue

        start = time.monotonic()
        print(f"[{i}/{len(metros)}] {metro.name}: starting...")
        stats = RunStats()
        try:
            records = scrape_one_metro(
                category, metro,
                radius_miles=args.radius, min_population=args.min_population,
                max_pages_per_place=args.pages_per_place, fetch_details=args.details,
                stats=stats,
            )
        except Exception:
            logger.exception("Metro %r failed -- skipping to the next one", metro.name)
            print(f"[{i}/{len(metros)}] {metro.name}: FAILED (see log) -- continuing with the rest")
            continue

        CSVSink(checkpoint_path).load(records)
        for sink in build_sinks_from_settings():
            try:
                sink.load(records)
            except Exception:
                logger.exception("Shared sink %r failed to load", sink.name)

        elapsed = time.monotonic() - start
        print(f"[{i}/{len(metros)}] {metro.name}: {len(records)} unique businesses "
              f"({elapsed:.0f}s, {stats.as_dict().get('requests_sent')} requests)")

        if args.publish:
            entry = publish_dataset(checkpoint_path, args.industry, metro.name)
            print(f"    published -> site/data/{entry['file']}")

        done += 1

    all_metros_path = rebuild_all_metros_file(industry_slug)
    print(f"\nBatch complete: {done} metro(s) run, {skipped} skipped (already done).")
    print(f"Compiled file: {all_metros_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
