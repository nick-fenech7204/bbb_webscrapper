#!/usr/bin/env python
"""
Enrich an existing master-table CSV (build_master_table output, e.g. one of
data/processed/batch/*.csv) with Angi data (a scripts/scrape_angi_category.py
CSV), matched by exact phone number -- see bbb_scraper/angi/enrich.py.

    python scripts/enrich_with_angi.py \\
        data/processed/batch/plumbers--chicago-il.csv \\
        data/processed/angi/plumbers--chicago-il.csv \\
        --in-place

Adds angi_<field> columns (name/phone/website/address/overall_rating/
review_count/categories/about_us/is_super_service_award_winner/bonded/
insured/profile_url) to every row, blank on a row with no matching Angi
phone, and refreshes every derived-intelligence column (lead_priority_score,
on_angi, ...) so they reflect the new signal immediately -- see merge.py's
_lead_priority_score docstring for why this never changes the score of a
row that isn't actually matched to Angi.

Doesn't republish to site/data/ itself -- run scripts/publish_site_data.py
--master afterward (same two-step shape as check_dead_websites.py).
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bbb_scraper.angi.enrich import enrich_with_angi
from bbb_scraper.logging_setup import configure_logging, get_logger

logger = get_logger(__name__)


def _read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("master_csv", type=Path, help="A build_master_table CSV (bbb_*/yelp_* columns)")
    parser.add_argument("angi_csv", type=Path, help="A scripts/scrape_angi_category.py output CSV")
    out_group = parser.add_mutually_exclusive_group(required=True)
    out_group.add_argument("--output", type=Path, help="Write the enriched CSV here")
    out_group.add_argument("--in-place", action="store_true", help="Overwrite master_csv")
    args = parser.parse_args()

    configure_logging()

    if not args.master_csv.exists():
        print(f"No such file: {args.master_csv}")
        return 1
    if not args.angi_csv.exists():
        print(f"No such file: {args.angi_csv}")
        return 1

    master_rows = _read_csv(args.master_csv)
    angi_records = _read_csv(args.angi_csv)
    enriched = enrich_with_angi(master_rows, angi_records)

    matched = sum(1 for r in enriched if r.get("on_angi") == 1)
    out_path = args.master_csv if args.in_place else args.output
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(enriched[0].keys()) if enriched else [])
        writer.writeheader()
        writer.writerows(enriched)

    print(f"Matched {matched}/{len(master_rows)} rows to {len(angi_records)} Angi record(s) by phone")
    print(f"Wrote {len(enriched)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
