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


def select_public_fields(record: dict) -> dict:
    """Extract just the public-site fields from an already in-memory,
    natively-typed record (straight from transform_summary/transform_detail,
    before any CSV round-trip -- `categories`/`contacts`/etc. are still real
    lists/dicts, not yet flattened to JSON strings). Used by
    `publish_records` below for publishing straight out of a running search
    (the Streamlit UI), without writing a CSV first. `load_records` below is
    the CSV-round-trip counterpart -- same field selection, plus decoding
    those fields back out of their flattened string form.
    """
    result = {}
    for field in _PUBLIC_FIELDS:
        value = record.get(field)
        if field in _JSON_FIELDS:
            result[field] = value if value is not None else ([] if field != "reviews_complaints" else {})
        else:
            # Normalize None -> "" same as load_records' CSV path (a CSV
            # cell can't be None to begin with) -- keeps a dataset published
            # from memory and one published from a CSV byte-for-byte
            # identical in shape, not just "close enough."
            result[field] = value if value is not None else ""
    return result


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


def _write_dataset(records: list[dict], industry: str, metro: str) -> dict:
    """Shared manifest-file-writing logic behind both `publish_dataset`
    (from a CSV on disk) and `publish_records` (from in-memory records) --
    `records` must already be field-selected (see `select_public_fields`/
    `load_records` above), this just writes the JSON file and updates the
    manifest (adds/replaces this one dataset id, leaves every other entry
    untouched).
    """
    dataset_id = f"{slugify(industry)}--{slugify(metro)}"
    filename = f"{dataset_id}.json"

    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DATA_DIR / filename).write_text(
        json.dumps(records, indent=2, default=str), encoding="utf-8"
    )

    manifest = load_manifest()
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["datasets"] = [d for d in manifest["datasets"] if d["id"] != dataset_id]
    entry = {
        "id": dataset_id,
        "industry": industry,
        "metro": metro,
        "record_count": len(records),
        "file": filename,
    }
    manifest["datasets"].append(entry)
    manifest["datasets"].sort(key=lambda d: (d["metro"], d["industry"]))
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return entry


def publish_dataset(csv_path: Path, industry: str, metro: str) -> dict:
    """Publish one CSV as one industry+metro dataset -- the reusable core
    this module's CLI wraps, also imported directly by
    scripts/batch_scrape_metros.py so a batch run can publish each metro
    the moment it finishes, without shelling out to this file as a
    subprocess per metro.

    Returns the manifest entry that was written (id/industry/metro/
    record_count/file), so a caller can report on what just happened
    without re-reading the manifest itself.
    """
    return _write_dataset(load_records(csv_path), industry, metro)


def publish_records(records: list[dict], industry: str, metro: str) -> dict:
    """Like `publish_dataset`, but for records already in memory (e.g.
    straight out of a completed Streamlit search) -- no CSV round-trip
    needed. `records` should be the natively-typed dicts transform_summary/
    transform_detail produce, not yet flattened for CSV.
    """
    return _write_dataset([select_public_fields(r) for r in records], industry, metro)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", type=Path, help="A CSV produced by the pipeline (e.g. from a metro sweep)")
    parser.add_argument("--industry", required=True, help='e.g. "Car Dealers"')
    parser.add_argument("--metro", required=True, help='e.g. "Miami, FL"')
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"No such file: {args.csv_path}")
        return 1

    entry = publish_dataset(args.csv_path, args.industry, args.metro)
    manifest = load_manifest()
    print(f"Published {entry['record_count']} record(s) to site/data/{entry['file']}")
    print(f"Manifest now has {len(manifest['datasets'])} dataset(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
