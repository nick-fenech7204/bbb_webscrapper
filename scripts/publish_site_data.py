#!/usr/bin/env python
"""
Publish scraped data into site/data/ for the static site to read -- the
"how does new data get onto the public site" step, deliberately separate
from scraping. The site is genuinely static (no live backend) and only
ever reads pre-generated files like the ones this script writes.

Three ways in, all producing the same per-record shape:
  - publish_dataset(csv_path, ...)      -- a BBB CSV on disk
  - publish_records(records, ...)       -- in-memory BBB records
  - publish_master_rows(rows, ...)      -- rows from match.merge.build_master_table
                                          (BBB fields + derived-intelligence
                                          columns; raw yelp_* columns dropped)
  - publish_master_csv(csv_path, ...)   -- a master-table CSV on disk

Every record carries the BBB public fields, a `last_updated` date, the
matched Yelp fields (name/rating/review_count/url -- null when unmatched),
and our derived-intelligence columns. See _YELP_SITE_FIELDS /
_INTEL_SITE_FIELDS. The site footer credits Yelp and links each matched
record back to its Yelp page.

Usage:
    python scripts/publish_site_data.py data/processed/miami_car_dealers_full.csv \\
        --industry "Car Dealers" --metro "Miami, FL"
    python scripts/publish_site_data.py --master \\
        data/processed/bbb_yelp_master__car-dealers-miami-fl.csv \\
        --industry "Car Dealers" --metro "Miami, FL"
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

# Ordered -- also the column order in the site's CSV export.
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

# Matched Yelp fields carried onto the site (from the `yelp_` prefix of a
# master-table row). Yelp's API terms want attribution + a link back --
# the site footer does that and every matched record links to yelp_url.
_YELP_SITE_FIELDS = ["yelp_name", "yelp_rating", "yelp_review_count", "yelp_url"]

# Our own derived-intelligence columns (read straight through from a
# master-table row).
_INTEL_SITE_FIELDS = [
    "review_need_score",
    "lead_priority_score",
    "reputation_divergence_flag",
    "low_review_volume_flag",
    "accredited_but_low_rated",
    "rating_gap_bbb_minus_yelp",
]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "dataset"


def _date_only(value) -> str:
    """'2026-09-03T15:43:00+00:00' or a datetime -> '2026-09-03'."""
    if not value:
        return ""
    return str(value)[:10]


def _null_intel() -> dict:
    """Yelp/intelligence fields for a record with no Yelp match (a BBB-only
    dataset, or an unmatched row)."""
    d = {f: None for f in (*_YELP_SITE_FIELDS, *_INTEL_SITE_FIELDS)}
    d["on_yelp"] = False
    return d


def _decode_json_field(value, field: str):
    empty = {} if field == "reviews_complaints" else []
    if value in (None, ""):
        return empty
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return empty


def select_public_fields(record: dict) -> dict:
    """From an in-memory, natively-typed BBB record (straight from
    transform_summary/transform_detail). No Yelp -> null intelligence."""
    result = {}
    for field in _PUBLIC_FIELDS:
        value = record.get(field)
        if field in _JSON_FIELDS:
            result[field] = _decode_json_field(value, field)
        else:
            result[field] = value if value is not None else ""
    result["last_updated"] = _date_only(record.get("scraped_at"))
    result.update(_null_intel())
    return result


def load_records(csv_path: Path) -> list[dict]:
    """From a BBB CSV on disk. No Yelp -> null intelligence."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    records = []
    for row in rows:
        record = {}
        for field in _PUBLIC_FIELDS:
            value = row.get(field, "")
            if field in _JSON_FIELDS:
                record[field] = _decode_json_field(value, field)
            else:
                record[field] = value
        record["last_updated"] = _date_only(row.get("scraped_at"))
        record.update(_null_intel())
        records.append(record)
    return records


def _num_or_none(v):
    if v in ("", None):
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


def select_public_fields_from_master(row: dict) -> dict:
    """From a match.merge.build_master_table row: BBB fields from the `bbb_`
    prefix, matched Yelp fields from the `yelp_` prefix, our derived
    columns read straight through. Only these Yelp fields cross over --
    everything else in `yelp_*` stays local."""
    result = {}
    for field in _PUBLIC_FIELDS:
        value = row.get(f"bbb_{field}", "")
        if field in _JSON_FIELDS:
            result[field] = _decode_json_field(value, field)
        else:
            result[field] = value if value not in (None,) else ""
    result["last_updated"] = _date_only(row.get("bbb_scraped_at"))

    matched = str(row.get("match_status") or "") == "matched"
    result["on_yelp"] = matched
    result["yelp_name"] = (row.get("yelp_name") or "") if matched else ""
    result["yelp_rating"] = _num_or_none(row.get("yelp_rating")) if matched else None
    result["yelp_review_count"] = _num_or_none(row.get("yelp_review_count")) if matched else None
    result["yelp_url"] = (row.get("yelp_url") or "") if matched else ""
    for field in _INTEL_SITE_FIELDS:
        result[field] = _num_or_none(row.get(field))
    return result


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"generated_at": None, "datasets": []}


def _write_dataset(records: list[dict], industry: str, metro: str) -> dict:
    dataset_id = f"{slugify(industry)}--{slugify(metro)}"
    filename = f"{dataset_id}.json"

    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DATA_DIR / filename).write_text(
        json.dumps(records, indent=2, default=str), encoding="utf-8"
    )

    has_intel = any(r.get("review_need_score") is not None for r in records)
    manifest = load_manifest()
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["datasets"] = [d for d in manifest["datasets"] if d["id"] != dataset_id]
    entry = {
        "id": dataset_id,
        "industry": industry,
        "metro": metro,
        "record_count": len(records),
        "has_intel": has_intel,
        "file": filename,
    }
    manifest["datasets"].append(entry)
    manifest["datasets"].sort(key=lambda d: (d["metro"], d["industry"]))
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return entry


def publish_dataset(csv_path: Path, industry: str, metro: str) -> dict:
    """Publish one BBB CSV as one industry+metro dataset."""
    return _write_dataset(load_records(csv_path), industry, metro)


def publish_records(records: list[dict], industry: str, metro: str) -> dict:
    """Publish in-memory BBB records (e.g. straight out of a scrape)."""
    return _write_dataset([select_public_fields(r) for r in records], industry, metro)


def publish_master_rows(rows: list[dict], industry: str, metro: str) -> dict:
    """Publish match.merge.build_master_table rows. BBB-primary: `yelp_only`
    rows (a Yelp business with no BBB match) are dropped -- the site is a
    BBB directory enriched with Yelp, not a Yelp directory."""
    bbb_primary = [r for r in rows if str(r.get("match_status") or "") != "yelp_only"]
    return _write_dataset(
        [select_public_fields_from_master(r) for r in bbb_primary], industry, metro
    )


def publish_master_csv(csv_path: Path, industry: str, metro: str) -> dict:
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return publish_master_rows(rows, industry, metro)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv_path", type=Path, help="A BBB CSV, or a master-table CSV with --master")
    parser.add_argument("--master", action="store_true",
                        help="csv_path is a build_master_table CSV (bbb_*/yelp_* columns)")
    parser.add_argument("--industry", required=True, help='e.g. "Car Dealers"')
    parser.add_argument("--metro", required=True, help='e.g. "Miami, FL"')
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"No such file: {args.csv_path}")
        return 1

    entry = (publish_master_csv if args.master else publish_dataset)(
        args.csv_path, args.industry, args.metro
    )
    manifest = load_manifest()
    print(f"Published {entry['record_count']} record(s) to site/data/{entry['file']} "
          f"(intelligence: {'yes' if entry['has_intel'] else 'no'})")
    print(f"Manifest now has {len(manifest['datasets'])} dataset(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
