#!/usr/bin/env python
"""
Snapshot everything currently published in site/data/*.json into one flat
historical CSV -- run before a curation/scoring change that will alter what
gets published, so the "before" state isn't lost once new batches overwrite
these files. Read-only with respect to site/data/: this only ever archives,
never modifies or deletes a published dataset.

Each site/data/<id>.json is a list of public-field rows (see
scripts/publish_site_data.py's select_public_fields_from_master) with no
industry/metro of its own -- that only lives in manifest.json, keyed by the
same id. This stitches the two back together so the historical CSV is
self-contained: every row carries which industry+metro it came from, not
just what dataset_id happens to encode in its filename.

Usage:
    python scripts/archive_published_data.py
    python scripts/archive_published_data.py --output data/processed/archive/my_snapshot.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SITE_DATA_DIR = REPO_ROOT / "site" / "data"
MANIFEST_PATH = SITE_DATA_DIR / "manifest.json"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "processed" / "archive" / f"site_snapshot_{date.today().isoformat()}.csv"  # noqa: DTZ011 -- calendar-date filename stamp, no real timezone concept applies

# Same JSON-in-cell convention as every other CSV in this project.
_JSON_FIELDS = {"categories", "contacts", "socials", "reviews_complaints"}

_LEAD_COLUMNS = ["industry", "metro", "dataset_id", "archived_at"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                     help=f"Where to write the historical CSV (default: {DEFAULT_OUTPUT})")
    args = ap.parse_args()

    if not MANIFEST_PATH.exists():
        print(f"No manifest at {MANIFEST_PATH} -- nothing published yet?")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    today = date.today().isoformat()  # noqa: DTZ011 -- calendar-date stamp, no real timezone concept applies
    all_rows: list[dict] = []
    fieldnames: list[str] = list(_LEAD_COLUMNS)
    seen_fields = set(fieldnames)

    for entry in manifest["datasets"]:
        path = SITE_DATA_DIR / entry["file"]
        if not path.exists():
            print(f"  skipping {entry['id']} -- {entry['file']} listed in manifest but missing on disk")
            continue
        records = json.loads(path.read_text(encoding="utf-8"))
        for r in records:
            row = {
                "industry": entry["industry"],
                "metro": entry["metro"],
                "dataset_id": entry["id"],
                "archived_at": today,
            }
            for k, v in r.items():
                if k in _JSON_FIELDS:
                    v = json.dumps(v, ensure_ascii=False)
                row[k] = v
                if k not in seen_fields:
                    seen_fields.add(k)
                    fieldnames.append(k)
            all_rows.append(row)
        print(f"  {entry['id']}: {len(records)} row(s)")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nWrote {len(all_rows)} row(s) across {len(manifest['datasets'])} dataset(s) -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
