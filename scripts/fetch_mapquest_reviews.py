#!/usr/bin/env python
"""
Fetch real Yelp-sourced review text for businesses already in a checkpoint
CSV, via MapQuest's own unauthenticated GraphQL search API -- a second real
review source alongside scripts/fetch_bbb_reviews.py (BBB) and Angi's own
built-in review capture. See bbb_scraper/mapquest/client.py's module
docstring for exactly how this endpoint was found and confirmed real.

One search per business (name + the business's own city/state resolved to
an approximate coordinate via bbb_scraper.reference.cities -- MapQuest's
own API only needs "the right metro," not a precise geocode, confirmed
live), matched to the right candidate by phone
(bbb_scraper.mapquest.matcher.find_business) -- same never-fatal,
best-effort treatment as every other enrichment step in this project: a
business that can't be resolved or has no city in the reference data just
comes out with empty review columns, never a crashed run.

Same "curated subset, not a blind sweep" design as fetch_bbb_reviews.py --
--top N sorts by lead_priority_score first. This endpoint has never been
run at real volume (see bbb_scraper/mapquest/client.py's own docstring);
start small.

Usage:
    python scripts/fetch_mapquest_reviews.py data/processed/batch/plumbers--chicago-il.csv \\
        --top 50 --in-place
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bbb_scraper.logging_setup import configure_logging, get_logger
from bbb_scraper.mapquest.client import MapQuestClient
from bbb_scraper.mapquest.matcher import find_business
from bbb_scraper.reference.cities import CityDirectory

configure_logging()
logger = get_logger(__name__)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("-inf")


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
    ap.add_argument("--name-field", default="bbb_name")
    ap.add_argument("--phone-field", default="bbb_phone")
    ap.add_argument("--city-field", default="bbb_city")
    ap.add_argument("--state-field", default="bbb_state")
    ap.add_argument("--score-field", default="lead_priority_score",
                     help="Column --top sorts by, descending (default: lead_priority_score)")
    ap.add_argument("--top", type=int, default=None,
                     help="Only fetch reviews for the top N rows by --score-field -- the intended "
                          "normal use (see module docstring), not a blind fetch-everything sweep")
    ap.add_argument("--max-businesses", type=int, default=None,
                     help="Hard cap regardless of --top")
    ap.add_argument("--output", type=Path, default=None,
                     help="Where to write the result (default: <path stem>--mapquest<suffix>)")
    ap.add_argument("--in-place", action="store_true",
                     help="Overwrite --path directly instead of writing a new file "
                          "(a timestamped backup is written to data/processed/archive/ first, "
                          "same as scripts/fetch_bbb_reviews.py)")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"No such file: {args.path}")
        return 1

    with args.path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        all_rows = list(reader)
    print(f"Loaded {len(all_rows)} rows from {args.path}")

    for field in (args.name_field, args.phone_field, args.city_field, args.state_field):
        if field not in fieldnames:
            print(f"'{field}' isn't a column in this file. "
                  f"Columns present: {', '.join(fieldnames[:20])}{'...' if len(fieldnames) > 20 else ''}")
            return 1

    selected = _select_rows(all_rows, top=args.top, max_businesses=args.max_businesses,
                             score_field=args.score_field)
    selected_ids = {id(r) for r in selected}
    if args.top is None and args.max_businesses is None:
        print(f"No --top/--max-businesses given -- fetching reviews for all {len(all_rows)} rows. "
              f"This endpoint has never been run at real volume; consider --top N instead.")

    print(f"Fetching MapQuest reviews for {len(selected)} business(es)...")
    city_directory = CityDirectory.load()
    started = time.time()
    fetched = 0
    matched = 0
    unmatched = 0
    no_city = 0
    failed = 0
    total_reviews = 0

    with MapQuestClient() as client:
        for i, row in enumerate(selected, 1):
            name = (row.get(args.name_field) or "").strip()
            phone = row.get(args.phone_field) or None
            city_name = (row.get(args.city_field) or "").strip()
            state = (row.get(args.state_field) or "").strip()

            city = city_directory.get(city_name, state) if city_name and state else None
            if city is None:
                no_city += 1
                row["mapquest_url"] = ""
                row["mapquest_review_count"] = ""
                row["mapquest_reviews"] = ""
                continue

            try:
                candidates = client.search(name, latitude=city.lat, longitude=city.lon)
                match = find_business(candidates, name=name, phone=phone)
            except Exception:
                logger.exception("MapQuest search failed for %r", name)
                failed += 1
                row["mapquest_url"] = ""
                row["mapquest_review_count"] = ""
                row["mapquest_reviews"] = ""
                continue

            fetched += 1
            if match is None:
                unmatched += 1
                row["mapquest_url"] = ""
                row["mapquest_review_count"] = ""
                row["mapquest_reviews"] = "[]"
                continue

            matched += 1
            total_reviews += len(match.reviews)
            row["mapquest_url"] = match.url or ""
            row["mapquest_review_count"] = match.review_count
            row["mapquest_reviews"] = json.dumps([asdict(r) for r in match.reviews], ensure_ascii=False)

            if i % 10 == 0 or i == len(selected):
                elapsed = time.time() - started
                print(f"  {i}/{len(selected)} businesses ({matched} matched, {total_reviews} reviews so far, "
                      f"{elapsed / 60:.1f}min)")

    for row in all_rows:
        if id(row) not in selected_ids:
            row.setdefault("mapquest_url", "")
            row.setdefault("mapquest_review_count", "")
            row.setdefault("mapquest_reviews", "")

    if args.in_place:
        archive_dir = REPO_ROOT / "data" / "processed" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = archive_dir / f"{args.path.stem}.pre-mapquest-{stamp}{args.path.suffix}"
        shutil.copy2(args.path, backup)
        print(f"Backed up original to {backup}")
        output_path = args.path
    else:
        output_path = args.output or args.path.with_name(f"{args.path.stem}--mapquest{args.path.suffix}")

    out_fieldnames = list(fieldnames) + [
        f for f in ("mapquest_url", "mapquest_review_count", "mapquest_reviews") if f not in fieldnames
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    elapsed = time.time() - started
    print(f"\nWrote {output_path}")
    print(f"{fetched} search(es) attempted ({failed} failed, {no_city} skipped -- city not in reference data), "
          f"{matched} matched to a real MapQuest business ({unmatched} searched with no confident match), "
          f"{total_reviews} total review(s) captured, {elapsed / 60:.1f} min.")
    if not args.in_place:
        print(f"This wrote a new file, not {args.path} -- pass --in-place to overwrite it directly "
              f"(a backup is made first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
