#!/usr/bin/env python
"""
Publish a scraped CSV (from any pipeline run) into site/data/ for the static
site to read -- this is the "how does new data get onto the public site"
step, deliberately separate from scraping itself: the site is genuinely
static (no live backend, no scraping exposed publicly -- see README's
"Static site" section) and only ever reads pre-generated files like the ones
this script writes.

Usage:
    python scripts/publish_site_data.py data/processed/miami_car_dealers_full.csv \\
        --industry "Car Dealers" --metro "Miami, FL"

Run it again with a different CSV/--industry/--metro to add another dataset
-- it updates site/data/manifest.json (adding or replacing that one entry,
not touching others) rather than starting over each time.

Field selection: keeps everything a site visitor would find useful (contact
info, ratings, categories, a link back to the real BBB profile) and drops
our own internal bookkeeping (our internal `id`, BBB's raw bbb_id/
business_id/office id, record_type, source_page/search_category*) -- see
_PUBLIC_FIELDS below to add/remove a column.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SITE_DATA_DIR = Path(__file__).resolve().parent.parent / "site" / "data"
MANIFEST_PATH = SITE_DATA_DIR / "manifest.json"

# Ordered -- this is also the column order in the exported CSV on the site.
_PUBLIC_FIELDS = [
    "name", "principal_contact", "phone", "email", "website",
    "address", "city", "state", "postal_code",
    "rating", "accredited", "accreditation_status",
    "years_in_business", "business_started",
    "primary_category_name", "categories",
    "contacts", "socials", "reviews_complaints",
    "organization_description", "entity_type",
    "lat", "lon", "profile_url", "scraped_at",
]

_JSON_FIELDS = {"categories", "contacts", "socials", "reviews_complaints"}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "dataset"


def load_records(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    records = []
    for row in rows:
        record = {}
        for field in _PUBLIC_FIELDS:
            value = row.get(field, "")
            if field in _JSON_FIELDS:
                # flatten_record (used when these were written to CSV)
                # JSON-encoded list/dict fields as strings -- decode back.
                try:
                    value = json.loads(value) if value else ([] if field != "reviews_complaints" else {})
                except json.JSONDecodeError:
                    value = [] if field != "reviews_complaints" else {}
            record[field] = value
        records.append(record)
    return records


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"generated_at": None, "datasets": []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="A CSV produced by the pipeline (e.g. from a metro sweep)")
    parser.add_argument("--industry", required=True, help='e.g. "Car Dealers"')
    parser.add_argument("--metro", required=True, help='e.g. "Miami, FL"')
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"No such file: {args.csv_path}")
        return 1

    records = load_records(args.csv_path)
    dataset_id = f"{slugify(args.industry)}--{slugify(args.metro)}"
    filename = f"{dataset_id}.json"

    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DATA_DIR / filename).write_text(
        json.dumps(records, indent=2, default=str), encoding="utf-8"
    )

    manifest = load_manifest()
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["datasets"] = [d for d in manifest["datasets"] if d["id"] != dataset_id]
    manifest["datasets"].append(
        {
            "id": dataset_id,
            "industry": args.industry,
            "metro": args.metro,
            "record_count": len(records),
            "file": filename,
        }
    )
    manifest["datasets"].sort(key=lambda d: (d["metro"], d["industry"]))
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Published {len(records)} record(s) to site/data/{filename}")
    print(f"Manifest now has {len(manifest['datasets'])} dataset(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
