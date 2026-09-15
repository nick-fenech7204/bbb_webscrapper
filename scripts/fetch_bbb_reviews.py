#!/usr/bin/env python
"""
Fetch real written review text for businesses already in a checkpoint CSV
-- a genuinely separate fetch from the normal BBB scrape (confirmed
2026-09-15: BBB's profile page never embeds individual review text, only
the aggregate counts and a link to a `/customer-reviews` sub-page). Built
for the planned local-sentiment pass (Ollama, on Nick's own machine) --
each business's reviews land as one JSON-string cell (bbb_reviews), newest
first, so a downstream sentiment step can json.loads() it directly rather
than needing a second data shape.

Sequential, not concurrent, unlike bbb_scraper.webcheck's many-different-
hosts sweep -- this is repeated requests to the SAME host (bbb.org), so it
gets the same one-at-a-time pacing as every other BBB fetch in this
project (HttpClient's own rate limiting), not a worker pool.

Meant to run against a curated SUBSET, not every row in a big checkpoint --
"grab reviews for everything" on a list of thousands would be thousands of
extra real requests against BBB for businesses that were never going to
make a short list anyway. --top N (sorted by lead_priority_score, already
on every master-table checkpoint) is the intended normal use: fetch reviews
only for the businesses that already look like promising leads, honing the
list further rather than fetching blind.

Usage:
    python scripts/fetch_bbb_reviews.py data/processed/batch/plumbers--chicago-il.csv \\
        --top 50 --in-place

    python scripts/fetch_bbb_reviews.py data/processed/batch/roofing--phoenix-az.csv \\
        --max-businesses 20 --max-reviews-per-business 30 --output data/processed/roofing_phoenix_reviews.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bbb_scraper.etl.extract import Extractor
from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.utils.stats import RunStats

configure_logging()
logger = get_logger(__name__)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("-inf")  # unscored rows sort last, never crash the sort


def _select_rows(rows: list[dict], *, top: int | None, max_businesses: int | None,
                  score_field: str) -> list[dict]:
    if top is not None:
        rows = sorted(rows, key=lambda r: _num(r.get(score_field)), reverse=True)[:top]
    if max_businesses is not None:
        rows = rows[:max_businesses]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="A master-table/checkpoint CSV (data/processed/batch/*.csv)")
    ap.add_argument("--profile-url-field", default="bbb_profile_url",
                     help="Column holding each business's BBB profile URL (default: bbb_profile_url)")
    ap.add_argument("--score-field", default="lead_priority_score",
                     help="Column --top sorts by, descending (default: lead_priority_score)")
    ap.add_argument("--top", type=int, default=None,
                     help="Only fetch reviews for the top N rows by --score-field -- the intended "
                          "normal use (see module docstring), not a blind fetch-everything sweep")
    ap.add_argument("--max-businesses", type=int, default=None,
                     help="Hard cap regardless of --top, e.g. to combine with a specific row order already in the file")
    ap.add_argument("--max-reviews-per-business", type=int, default=50,
                     help="Cap per business (default: 50) -- see Extractor.extract_business_reviews's own "
                          "docstring for why (some businesses have thousands of reviews)")
    ap.add_argument("--output", type=Path, default=None,
                     help="Where to write the result (default: <path stem>--reviews<suffix>)")
    ap.add_argument("--in-place", action="store_true",
                     help="Overwrite --path directly instead of writing a new file "
                          "(a timestamped backup is written to data/processed/archive/ first, "
                          "same as scripts/check_dead_websites.py)")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"No such file: {args.path}")
        return 1

    with args.path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        all_rows = list(reader)
    print(f"Loaded {len(all_rows)} rows from {args.path}")

    if args.profile_url_field not in fieldnames:
        print(f"'{args.profile_url_field}' isn't a column in this file. "
              f"Columns present: {', '.join(fieldnames[:20])}{'...' if len(fieldnames) > 20 else ''}")
        return 1

    selected = _select_rows(all_rows, top=args.top, max_businesses=args.max_businesses,
                             score_field=args.score_field)
    selected_urls = {id(r) for r in selected}  # identity, not value -- rows aren't hashable/unique by content
    if args.top is None and args.max_businesses is None:
        print(f"No --top/--max-businesses given -- fetching reviews for all {len(all_rows)} rows. "
              f"This is a real request per business against bbb.org; consider --top N instead.")

    print(f"Fetching reviews for {len(selected)} business(es) (max {args.max_reviews_per_business} each)...")
    stats = RunStats()
    fetched = 0
    failed = 0
    total_reviews = 0
    started = time.time()

    with Extractor(stats=stats) as extractor:
        for i, row in enumerate(selected, 1):
            url = (row.get(args.profile_url_field) or "").strip()
            if not url:
                row["bbb_reviews"] = "[]"
                row["bbb_num_reviews_captured"] = 0
                continue
            try:
                reviews = extractor.extract_business_reviews(url, max_reviews=args.max_reviews_per_business)
            except Exception:
                logger.exception("Review fetch failed for %s", url)
                failed += 1
                row["bbb_reviews"] = "[]"
                row["bbb_num_reviews_captured"] = 0
                continue
            row["bbb_reviews"] = json.dumps([r.model_dump() for r in reviews], ensure_ascii=False)
            row["bbb_num_reviews_captured"] = len(reviews)
            fetched += 1
            total_reviews += len(reviews)
            if i % 10 == 0 or i == len(selected):
                elapsed = time.time() - started
                print(f"  {i}/{len(selected)} businesses ({total_reviews} reviews so far, {elapsed / 60:.1f}min)")

    # Unselected rows (everything outside --top/--max-businesses) get blank
    # review columns, not missing ones -- every row in the output CSV has
    # the same schema, whether or not it was actually fetched this run.
    for row in all_rows:
        if id(row) not in selected_urls:
            row.setdefault("bbb_reviews", "")
            row.setdefault("bbb_num_reviews_captured", "")

    if args.in_place:
        archive_dir = REPO_ROOT / "data" / "processed" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = archive_dir / f"{args.path.stem}.pre-reviews-{stamp}{args.path.suffix}"
        shutil.copy2(args.path, backup)
        print(f"Backed up original to {backup}")
        output_path = args.path
    else:
        output_path = args.output or args.path.with_name(f"{args.path.stem}--reviews{args.path.suffix}")

    out_fieldnames = list(fieldnames) + [
        f for f in ("bbb_reviews", "bbb_num_reviews_captured") if f not in fieldnames
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    elapsed = time.time() - started
    print(f"\nWrote {output_path}")
    print(f"{fetched} business(es) fetched successfully ({failed} failed), "
          f"{total_reviews} total review(s) captured, {elapsed / 60:.1f} min "
          f"({stats.as_dict().get('requests_sent')} BBB requests).")
    if not args.in_place:
        print(f"This wrote a new file, not {args.path} -- pass --in-place to overwrite it directly "
              f"(a backup is made first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
