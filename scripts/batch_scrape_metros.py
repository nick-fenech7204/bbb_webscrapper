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
launched as a background process (the Streamlit page does this for you;
from a plain terminal, background it yourself).

Per metro:
  1. extract_search_metro_coverage (same as a single metro sweep) -> BBB
     records, transformed + deduped (+ optionally --details).
  2. unless --no-yelp: one Yelp Fusion `search_area` for the metro (~5 API
     calls), matched to the BBB records. Best-effort -- if the key is
     missing, the daily quota is nearly spent, or a call fails, Yelp is
     dropped for the rest of the batch and metros just come out BBB-only.
  3. write data/processed/batch/<industry-slug>--<metro-id>.csv -- the wide
     BBB|Yelp master table (bbb_* / yelp_* columns + derived-intelligence
     columns; yelp_* blank when there was no match). This file's existence
     is the resume marker: a metro already checkpointed for this industry
     is skipped on a re-run (--force to redo).
  4. append the BBB records (not the wide table) into the shared
     data/processed/businesses.csv sink, same as every other run.
  5. unless --no-publish: publish that metro to site/data/ locally (BBB
     fields + the matched yelp_name/rating/review_count/url + our derived
     intelligence columns -- raw Yelp beyond those four stays out).
  Steps 3-5 are wrapped: a failure there is logged and this metro is
  skipped, the rest of the batch keeps going rather than the whole run
  dying (this used to be able to kill hours of already-finished, already-
  correct work over a bug in a print statement -- see git history).

After the whole batch: rebuilds data/processed/<industry-slug>_all_metros.csv
(every metro run for this industry so far, concatenated), then unless
--no-deploy, and only if at least one metro actually ran this time, pushes
site/ live (S3 sync + CloudFront invalidation) once -- see
scripts/deploy_site.py. A deploy problem is reported but never makes the
batch itself look like it failed; the scrape + local publish already
succeeded regardless, and `python scripts/deploy_site.py` alone retries it.
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

import deploy_site
from publish_site_data import publish_master_rows, slugify

from bbb_scraper.etl.dedupe import dedupe_records
from bbb_scraper.etl.extract import Extractor
from bbb_scraper.etl.transform import transform_detail, transform_summary
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.match.enrich import enrich_bbb_with_yelp, open_yelp_enrichment
from bbb_scraper.pipeline.registry import build_sinks_from_settings
from bbb_scraper.pipeline.sinks.csv_sink import CSVSink
from bbb_scraper.reference.metros import MetroDirectory
from bbb_scraper.reference.models import Category, Metro, parse_location
from bbb_scraper.scraping.search import build_referer
from bbb_scraper.utils.stats import RunStats

configure_logging()
logger = get_logger(__name__)

BATCH_DIR = REPO_ROOT / "data" / "processed" / "batch"


def scrape_one_metro(
    category: Category, metro: Metro, *,
    radius_miles: float, min_population: int, max_pages_per_place: int,
    fetch_details: bool, stats: RunStats,
) -> list[dict]:
    """One metro's worth of BBB records, deduped -- details-first if
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
        fieldnames = sorted({key for row in all_rows for key in row})
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
        "--yelp", action=argparse.BooleanOptionalAction, default=True,
        help="Enrich each metro with a Yelp Fusion search (~5 API calls/metro) matched to "
        "the BBB rows (default: on). Best-effort: no key / low quota / a failed call just "
        "means BBB-only output for the rest of the batch, never an error.",
    )
    parser.add_argument(
        "--publish", action=argparse.BooleanOptionalAction, default=True,
        help="Publish each metro's BBB fields to site/ as soon as it's done (default: on)",
    )
    parser.add_argument(
        "--deploy", action=argparse.BooleanOptionalAction, default=True,
        help="Push site/ live (S3 sync + CloudFront invalidation) once at the end of the batch, "
        "if at least one metro actually ran (default: on). Needs the AWS CLI configured -- see "
        "scripts/deploy_site.py; a missing/broken AWS setup prints a message here and the batch "
        "still finishes normally, it just doesn't go live. --no-deploy to only publish locally.",
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

    yelp_state = open_yelp_enrichment(args.yelp)
    yelp_note = "on" if yelp_state.enabled else f"off ({yelp_state.reason_off})"
    print(f"Batch: {len(metros)} metro(s), industry={args.industry!r}, "
          f"radius={args.radius}mi, min_population={args.min_population}, "
          f"pages_per_place={args.pages_per_place}, details={args.details}, "
          f"yelp={yelp_note}, publish={args.publish}, deploy={args.deploy}")

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

        # Everything from here on (checkpoint, shared sinks, publish) is
        # wrapped: a batch runs unattended for hours across many metros, so
        # a bug in this post-scrape step (there was one -- see below) must
        # never be allowed to kill metros still queued behind it. Whatever
        # already reached disk (checkpoint, businesses.csv, the site JSON)
        # stays written either way; only this metro's `done` count and
        # summary line are skipped on failure.
        try:
            master_rows = enrich_bbb_with_yelp(records, args.industry, metro.seed_location, yelp_state)
            CSVSink(checkpoint_path).load(master_rows)

            for sink in build_sinks_from_settings():
                try:
                    sink.load(records)  # BBB records only -- businesses.csv stays a pure BBB log
                except Exception:
                    logger.exception("Shared sink %r failed to load", sink.name)

            elapsed = time.monotonic() - start
            matched = sum(r.get("match_status") == "matched" for r in master_rows)
            print(f"[{i}/{len(metros)}] {metro.name}: {len(records)} BBB businesses"
                  f"{f', {matched} matched to Yelp' if yelp_state.enabled else ''} "
                  f"({elapsed:.0f}s, {stats.as_dict().get('requests_sent')} BBB requests)")

            if args.publish:
                # master_rows, not `records` -- so the site gets our derived
                # intelligence columns too. publish_master_rows drops every
                # raw yelp_* field, so the public site stays BBB-only.
                entry = publish_master_rows(master_rows, args.industry, metro.name)
                print(f"    published -> site/data/{entry['file']} "
                      f"({entry['yelp_matched']} matched to Yelp, top lead {entry['top_lead_score']})")
        except Exception:
            logger.exception("Metro %r: checkpoint/publish step failed", metro.name)
            print(f"[{i}/{len(metros)}] {metro.name}: scraped OK but the checkpoint/publish step "
                  f"FAILED (see log) -- continuing with the rest. Re-run with --force to redo this metro.")
            continue

        done += 1

    all_metros_path = rebuild_all_metros_file(industry_slug)
    print(f"\nBatch complete: {done} metro(s) run, {skipped} skipped (already done).")
    if yelp_state.reason_off and args.yelp:
        print(f"Note: Yelp enrichment stopped partway -- {yelp_state.reason_off}")
    print(f"Compiled file: {all_metros_path}")

    if args.deploy and args.publish and done > 0:
        # One deploy at the very end, not per metro: metros already publish
        # locally to site/data/ as they finish (above), so this is just the
        # "make the accumulated local changes live" step. Never lets a
        # deploy problem look like the batch itself failed -- the scrape
        # already succeeded and is safely on disk either way; --no-deploy
        # or a manual `python scripts/deploy_site.py` retry always still
        # works if this trips.
        print("\nDeploying to the live site...")
        try:
            rc = deploy_site.main()
        except Exception:
            logger.exception("Deploy step raised unexpectedly")
            rc = 1
        if rc != 0:
            print("Deploy failed (see above) -- the scrape + local publish are still fine; "
                  "re-run `python scripts/deploy_site.py` once the problem's fixed.")
    elif args.deploy and done == 0:
        print("\nNothing new this run -- skipping deploy (site/ has nothing changed to push).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
