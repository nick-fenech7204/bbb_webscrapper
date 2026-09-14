#!/usr/bin/env python
"""
Check whether the websites on a CSV's businesses are actually live --
a second, separate sales angle alongside reputation scoring: a business
with a dead or parked website is a lead for "we'll build you a website",
independent of whether their BBB/Yelp reputation needs help too.

Works on either shape of CSV in this project:
  - A raw BBB CSV (data/processed/businesses.csv, a plain --metros run's
    checkpoint) -- bare `website` column. Use --website-field website
    (the default).
  - A master-table/checkpoint CSV (data/processed/batch/<slug>--<metro>.csv)
    -- every BBB field is bbb_-prefixed. Use --website-field bbb_website.
    This is the more useful target in practice: after this writes
    bbb_website_dead/bbb_website_status back into that checkpoint,
    `python scripts/publish_site_data.py --master <that file> --industry
    ... --metro ...` picks them straight up (build_master_table /
    recompute_intel read whichever BBB fields are on the row already, this
    included) and republishes the dataset with the new signal live.

Bounded concurrency (default 10 workers) against many *different* hosts --
this is a one-off liveness sweep, not repeated hits on one site, so a
modest worker count is normal, not a burst. Results are cached to disk
(data/raw/webcheck/cache.json by default, 30-day TTL) -- re-running this
across metros, or re-running it later, doesn't re-check a URL that was
already confirmed dead or alive recently.

Usage:
    python scripts/check_dead_websites.py data/processed/batch/electricians--los-angeles-ca.csv \\
        --website-field bbb_website

    python scripts/check_dead_websites.py data/processed/businesses.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bbb_scraper.config import settings
from bbb_scraper.webcheck.enrich import check_websites, derive_output_fields


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="CSV to check (a raw BBB CSV or a master-table checkpoint)")
    ap.add_argument("--website-field", default="website",
                     help="Column holding each business's website URL (default: website; "
                          "use bbb_website for a master-table/checkpoint CSV)")
    ap.add_argument("--output", type=Path, default=None,
                     help="Where to write the result (default: <path stem>--webcheck<suffix>)")
    ap.add_argument("--in-place", action="store_true",
                     help="Overwrite --path directly instead of writing a new file "
                          "(a timestamped backup is written to data/processed/archive/ first, "
                          "same as scripts/dedupe_businesses_csv.py)")
    ap.add_argument("--max-workers", type=int, default=None, help=f"default: {settings.webcheck_max_workers}")
    ap.add_argument("--ttl-days", type=int, default=None,
                     help=f"skip re-checking a URL confirmed within this many days (default: "
                          f"{settings.webcheck_cache_ttl_days})")
    ap.add_argument("--cache-file", type=Path, default=None,
                     help="default: data/raw/webcheck/cache.json")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"No such file: {args.path}")
        return 1

    with args.path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    print(f"Loaded {len(rows)} rows from {args.path}")

    if args.website_field not in fieldnames:
        print(f"'{args.website_field}' isn't a column in this file. "
              f"Columns present: {', '.join(fieldnames[:20])}{'...' if len(fieldnames) > 20 else ''}")
        return 1

    if args.in_place:
        archive_dir = REPO_ROOT / "data" / "processed" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = archive_dir / f"{args.path.stem}.pre-webcheck-{stamp}{args.path.suffix}"
        shutil.copy2(args.path, backup)
        print(f"Backed up original to {backup}")
        output_path = args.path
    else:
        output_path = args.output or args.path.with_name(f"{args.path.stem}--webcheck{args.path.suffix}")

    def _progress(done: int, total: int) -> None:
        if done == total or done % 20 == 0:
            print(f"  checked {done}/{total} unique website(s)...")

    checked = check_websites(
        rows, website_field=args.website_field,
        max_workers=args.max_workers, ttl_days=args.ttl_days,
        cache_path=args.cache_file, on_progress=_progress,
    )

    dead_field, status_field, checked_field = derive_output_fields(args.website_field)
    out_fieldnames = list(fieldnames) + [
        f for f in (dead_field, status_field, checked_field) if f not in fieldnames
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(checked)

    counts = collections.Counter(r[status_field] for r in checked)
    dead_count = sum(1 for r in checked if r[dead_field])
    print(f"\nWrote {output_path}")
    print(f"{dead_count} of {len(checked)} business(es) have a dead website "
          f"({counts.get('dead_404', 0)} 404, {counts.get('dead_unreachable', 0)} unreachable, "
          f"{counts.get('dead_parked', 0)} parked/for-sale).")
    print("Full status breakdown: " + ", ".join(f"{status}={n}" for status, n in counts.most_common()))
    if not args.in_place:
        print(f"This wrote a new file, not {args.path} -- pass --in-place to overwrite it directly "
              f"(a backup is made first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
