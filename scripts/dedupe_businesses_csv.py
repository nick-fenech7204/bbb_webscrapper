#!/usr/bin/env python
"""
Collapse data/processed/businesses.csv rows that share a phone number into
one -- the same identity rule the lead-list pipeline uses (see
bbb_scraper/match/dedupe.py's dedupe_by_phone). Keeps the most recently
scraped snapshot for each phone, not just the first one appended, since
this file spans separate runs on separate dates and a later scrape is
generally the fresher, more accurate one.

Deliberately NOT wired into CSVSink's normal append behavior -- businesses
.csv stays the plain, fast, uncurated append-only log day to day (that's
still the right default: every run's real, raw results land in it, and
mixing in a "check the whole file so far on every write" cost isn't worth
paying for something you only want occasionally). Run this by hand
whenever you want a cleaned-up snapshot; a timestamped backup of the
original is written to data/processed/archive/ first, every time, unless
--no-backup.

Usage:
    python scripts/dedupe_businesses_csv.py
    python scripts/dedupe_businesses_csv.py --path data/processed/other.csv
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bbb_scraper.config import settings
from bbb_scraper.match.dedupe import dedupe_by_phone


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, default=settings.csv_output_path,
                    help="CSV to dedupe in place (default: settings.csv_output_path, businesses.csv)")
    ap.add_argument("--phone-field", default="phone", help="column holding the phone number (default: phone)")
    ap.add_argument("--no-backup", action="store_true", help="skip writing a timestamped copy first")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"No such file: {args.path}")
        return 1

    with args.path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    print(f"Loaded {len(rows)} rows from {args.path}")

    if not args.no_backup:
        archive_dir = REPO_ROOT / "data" / "processed" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = archive_dir / f"{args.path.stem}.pre-phone-dedup-{stamp}{args.path.suffix}"
        shutil.copy2(args.path, backup)
        print(f"Backed up original to {backup}")

    # Newest-first, so dedupe_by_phone's first-seen-wins keeps the most
    # recently scraped snapshot for a given phone, not just whichever run
    # happened to scrape it first.
    rows.sort(key=lambda r: r.get("scraped_at") or "", reverse=True)
    deduped = dedupe_by_phone(rows, phone_field=args.phone_field)

    with args.path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deduped)

    print(f"{len(rows)} -> {len(deduped)} rows ({len(rows) - len(deduped)} duplicate-by-phone removed)")
    print(f"Rewrote {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
