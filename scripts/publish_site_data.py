#!/usr/bin/env python
"""
Publish scraped data into site/data/ for the static site to read -- the
"how does new data get onto the public site" step, deliberately separate
from scraping. The site is genuinely static (no live backend) and only
ever reads pre-generated files like the ones this script writes.

Four ways in, all funneling through match.merge.build_master_table so every
record gets the same shape and the same BBB-side intelligence columns:
  - publish_dataset(csv_path, ...)      -- a BBB CSV on disk
  - publish_records(records, ...)       -- in-memory BBB records
  - publish_master_rows(rows, ...)      -- rows from build_master_table (BBB
                                          + Yelp matched); raw yelp_* beyond
                                          the four site fields are dropped
  - publish_master_csv(csv_path, ...)   -- a master-table CSV on disk

Every record carries the BBB public fields, a `last_updated` date, the
matched Yelp fields (name/rating/review_count/url -- null when unmatched),
and our derived-intelligence columns (lead_priority_score, the flags --
computed for every BBB record; a Yelp match just adds signal). See
_YELP_SITE_FIELDS / _INTEL_SITE_FIELDS. The site footer credits Yelp and
links each matched record back to its Yelp page.

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bbb_scraper.curate import curate_for_publish
from bbb_scraper.match.matcher import MatchOutcome
from bbb_scraper.match.merge import _has_mapquest_yelp_data, build_master_table, recompute_intel

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
    "website_status",
]

_JSON_FIELDS = {"categories", "contacts", "socials", "reviews_complaints"}

# Fields that must come out as a real JSON bool/number, not whatever string
# type a CSV round-trip left them as -- publish_master_csv/publish_dataset
# both read rows via csv.DictReader, which stringifies *everything*
# (a Python `False` becomes the literal text "False"). The in-memory paths
# (publish_records/publish_master_rows called straight out of a scrape)
# never had this problem since nothing touched a CSV; --master/--csv-path
# reads off disk did, silently, until this was caught in a real diff review
# (accredited: false -> "False", years_in_business: 13 -> "13") -- same
# TRUEISH semantics as site/js/app.js's isTrue() so pipeline and client
# agree on what counts as true.
_BOOL_FIELDS = {"accredited"}
_INT_FIELDS = {"years_in_business"}
_TRUEISH = {"true", "1", "yes", "y", "t"}


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in _TRUEISH


# Matched Yelp fields carried onto the site (from the `yelp_` prefix of a
# master-table row). Yelp's API terms want attribution + a link back --
# the site footer does that and every matched record links to yelp_url.
_YELP_SITE_FIELDS = ["yelp_name", "yelp_rating", "yelp_review_count", "yelp_url"]

# Matched Angi fields (bbb_scraper/angi/enrich.py's phone-matched angi_*
# columns), 2026-09-14: angi_name/angi_rating/angi_review_count/angi_url
# (mirroring the Yelp fields above) plus specialties (angi_categories,
# under the label it's actually shown as on the site -- Angi's services-
# offered list reads as a specialties column, not a generic "categories"
# one; BBB already has its own bbb_categories/primary_category_name, a
# different taxonomy) and angi_super_service_award (a trust badge worth
# surfacing on its own, not just folded into the rating number).
_ANGI_SITE_FIELDS = [
    "angi_name", "angi_rating", "angi_review_count", "angi_url",
    "specialties", "angi_super_service_award",
]

# 2026-09-17, Nick's call: Angi is now a real discovery source in its own
# right (bbb_scraper/angi/enrich.py can emit a genuinely new angi_only row,
# not just enrich an existing BBB one -- see that module's docstring), so
# these core identity fields fall back to their Angi equivalent when
# there's no BBB value -- an angi_only row would otherwise publish with a
# blank name/phone/city, which isn't "less data", it's a broken-looking
# row. Deliberately scoped to fields with a genuine Angi equivalent:
# BBB-specific concepts (grade, accreditation, complaints,
# principal_contact, years_in_business, lat/lon -- Angi profiles carry no
# lat/lon at all) are NOT faked from Angi data and stay blank, the same
# gap a yelp_only-style row already has today for BBB-only fields.
_ANGI_IDENTITY_FALLBACK = {
    "name": "angi_name", "phone": "angi_phone", "website": "angi_website",
    "city": "angi_city", "state": "angi_state", "postal_code": "angi_zip_code",
    "profile_url": "angi_profile_url",
}

# Our own derived-intelligence columns (read straight through from a
# master-table row). These are available for every BBB record -- a Yelp
# match just adds more signal, it isn't required.
_INTEL_SITE_FIELDS = [
    "bbb_grade_num",
    "bbb_review_avg",
    "bbb_reviews_total",
    "bbb_complaints_total",
    "rating_gap_bbb_minus_yelp",
    "reputation_divergence_flag",
    "review_need_score",
    "low_review_volume_flag",
    "accredited_but_low_rated",
    # Sentiment feeds lead_priority_score itself (bbb_scraper.match.merge's
    # _review_sentiment_signal/_review_gap_flag) but wasn't reaching the
    # published site at all until now (2026-09-16) -- a rep looking at the
    # table had no way to see WHY a score moved for sentiment reasons, only
    # THAT it did. Surfaced the same way every other score-driving signal
    # already is.
    "review_sentiment_signal",
    "review_gap_flag",
    "lead_priority_score",
    "has_phone",
    "has_named_contact",
    "has_email",
    "contact_readiness",
    "contact_readiness_score",
    "website_dead_flag",
    "on_angi",
]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "dataset"


def _date_only(value) -> str:
    """'2026-09-03T15:43:00+00:00' or a datetime -> '2026-09-03'."""
    if not value:
        return ""
    return str(value)[:10]


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


def _bbb_only_master(bbb_records: list[dict]) -> list[dict]:
    """Run BBB-only records through build_master_table so they get the same
    BBB-side intelligence columns (lead_priority_score, the flags) as a
    Yelp-matched dataset -- the Yelp match just adds signal, it isn't
    required for scoring."""
    outcome = MatchOutcome(bbb_only=list(bbb_records))
    return build_master_table(outcome, include_yelp_only=False)


def _num_or_none(v):
    if v in ("", None):
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


# bbb_scraper.sentiment.analyze stores the raw ReviewSentiment.source
# vocabulary ("mapquest"/"angi"/"bbb") -- mapquest_reviews is genuinely
# Yelp review text (see that module's own _aggregate docstring), so it
# displays as "Yelp" here, the same treatment the rest of the site already
# gives a MapQuest-sourced rating. "bbb" is included (2026-09-17, once
# _aggregate stopped excluding BBB from the date fields) so a BBB-sourced
# latest-review date still shows a real source on hover, not a blank one.
_REVIEW_SOURCE_LABEL = {"mapquest": "Yelp", "angi": "Angi", "bbb": "BBB"}


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
        elif field in _BOOL_FIELDS:
            result[field] = _to_bool(value)
        elif field in _INT_FIELDS:
            result[field] = _num_or_none(value)
        else:
            result[field] = value if value not in (None,) else ""
    for public_field, angi_field in _ANGI_IDENTITY_FALLBACK.items():
        if not result.get(public_field):
            result[public_field] = row.get(angi_field) or ""
    # No Angi-side fallback here -- bbb_scraper.angi doesn't capture a scrape
    # timestamp at all today, so an angi_only row's last_updated is blank
    # (known, minor gap; adding a real Angi timestamp is a separate,
    # small piece of work, not faked here with today's publish time).
    result["last_updated"] = _date_only(row.get("bbb_scraped_at"))

    # 2026-09-17, Nick's call: MapQuest surfaces real Yelp-sourced rating
    # data through its own separate name+phone-in-city match, a second real
    # door into the same Yelp data the official Fusion API match can miss
    # (its 240-result cap, or a business the confidence-scored matcher
    # didn't clear) -- confirmed real and common (24 MapQuest-only Yelp
    # matches vs. 18 official ones in one Charlotte test), not a rare edge
    # case worth ignoring. Official match wins when both exist; MapQuest's
    # own fields fill in when it's the only place this business's Yelp
    # data showed up. yelp_url uses mapquest_rating_url specifically (a
    # real yelp.com/biz/... link, confirmed live 2026-09-17 -- see
    # mapquest/client.py's module docstring), NOT mapquest_url (MapQuest's
    # own page for the business) -- Nick's explicit call: a Yelp rating
    # should link to the actual Yelp page, not a MapQuest page. A
    # checkpoint scraped before this field existed just has no yelp_url
    # here until it's re-scraped (renders as plain, non-linked text --
    # see app.js's oneRatingLink -- not a crash).
    matched = str(row.get("match_status") or "") == "matched"
    mapquest_yelp = _has_mapquest_yelp_data(row)
    result["on_yelp"] = matched or mapquest_yelp
    result["yelp_name"] = (row.get("yelp_name") or "") if matched else ""
    result["yelp_rating"] = (
        _num_or_none(row.get("yelp_rating")) if matched
        else _num_or_none(row.get("mapquest_rating_value")) if mapquest_yelp
        else None
    )
    result["yelp_review_count"] = (
        _num_or_none(row.get("yelp_review_count")) if matched
        else _num_or_none(row.get("mapquest_review_count")) if mapquest_yelp
        else None
    )
    result["yelp_url"] = (
        (row.get("yelp_url") or "") if matched
        else (row.get("mapquest_rating_url") or "") if mapquest_yelp
        else ""
    )
    result["yelp_via_mapquest"] = mapquest_yelp and not matched

    # on_angi itself is set below via _INTEL_SITE_FIELDS (merge.py's own
    # _on_angi computes it from angi_phone) -- read the same way here just
    # to gate these other fields consistently with whatever that column
    # actually says, rather than recomputing the same truthiness twice.
    on_angi = _to_bool(row.get("on_angi"))
    result["angi_name"] = (row.get("angi_name") or "") if on_angi else ""
    result["angi_rating"] = _num_or_none(row.get("angi_overall_rating")) if on_angi else None
    result["angi_review_count"] = _num_or_none(row.get("angi_review_count")) if on_angi else None
    result["angi_url"] = (row.get("angi_profile_url") or "") if on_angi else ""
    result["specialties"] = (row.get("angi_categories") or "") if on_angi else ""
    result["angi_super_service_award"] = _to_bool(row.get("angi_is_super_service_award_winner")) if on_angi else False

    # The single most representative negative/mixed review (bbb_scraper.
    # sentiment.analyze._top_complaint) -- a raw fact written directly onto
    # the row by the batch's sentiment step, not an _INTEL-computed column,
    # so it needs its own pass-through here rather than going through the
    # generic loop below.
    result["top_complaint_theme"] = row.get("top_complaint_theme") or ""
    result["top_complaint_summary"] = row.get("top_complaint_summary") or ""

    # Same raw-fact pass-through as top_complaint above -- both dates
    # already drive lead_priority_score (most_recent_negative_review_date
    # feeds _review_sentiment_signal's recency multiplier;
    # most_recent_review_date feeds _review_gap_flag) but were never
    # actually surfaced to the site itself (2026-09-16 gap, caught when
    # Nick asked "are we taking it into account" -- the formula was, the
    # published record wasn't).
    result["most_recent_review_date"] = row.get("most_recent_review_date") or ""
    result["most_recent_review_source"] = _REVIEW_SOURCE_LABEL.get(row.get("most_recent_review_source"), "")
    result["most_recent_negative_review_date"] = row.get("most_recent_negative_review_date") or ""
    result["most_recent_negative_review_source"] = _REVIEW_SOURCE_LABEL.get(
        row.get("most_recent_negative_review_source"), "")
    result["review_sentiment_analyzed_count"] = _num_or_none(row.get("review_sentiment_analyzed_count"))
    result["review_sentiment_negative_count"] = _num_or_none(row.get("review_sentiment_negative_count"))

    for field in _INTEL_SITE_FIELDS:
        result[field] = _num_or_none(row.get(field))
    # integer flags stay ints, not 1.0/0.0
    for flag in ("reputation_divergence_flag", "low_review_volume_flag", "accredited_but_low_rated",
                 "has_phone", "has_named_contact", "has_email", "website_dead_flag", "on_angi",
                 "review_gap_flag"):
        if result.get(flag) is not None:
            result[flag] = int(result[flag])
    return result


def load_manifest() -> dict:
    if MANIFEST_PATH.exists():
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return {"generated_at": None, "datasets": []}


def _curate_and_select(master_rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """One shared choke point for every publish_* entry point below --
    curate_for_publish (bbb_scraper/curate.py: drops detected chains/
    corporate accounts and leads scoring under LOW_SCORE_CUTOFF) runs on
    the full master row (bbb_/yelp_/angi_-prefixed, every intel column)
    BEFORE select_public_fields_from_master narrows it down to the site's
    public shape -- curation needs bbb_name/angi_corporate_account/
    lead_priority_score exactly as build_master_table produces them, not
    the renamed/subset public fields. Only ever applied at publish time;
    the underlying checkpoint CSV a caller read from stays untouched."""
    kept, removed = curate_for_publish(master_rows)
    return [select_public_fields_from_master(r) for r in kept], removed


def _write_dataset(
    records: list[dict], industry: str, metro: str, *, curated_out: dict[str, int] | None = None
) -> dict:
    dataset_id = f"{slugify(industry)}--{slugify(metro)}"
    filename = f"{dataset_id}.json"

    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (SITE_DATA_DIR / filename).write_text(
        json.dumps(records, indent=2, default=str), encoding="utf-8"
    )

    def _lead(r: dict) -> float:
        v = r.get("lead_priority_score")
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    manifest = load_manifest()
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    manifest["datasets"] = [d for d in manifest["datasets"] if d["id"] != dataset_id]
    entry = {
        "id": dataset_id,
        "industry": industry,
        "metro": metro,
        # 2026-09-17, Nick's ask: lets the site's own lists-view sort by
        # "most recent" -- the manifest's own array order was never a
        # usable proxy for that (it's re-sorted (metro, industry) every
        # single publish, see below, so array position reflects
        # alphabetical order, not recency). Every publish of THIS dataset
        # (first time or a republish) sets this to right now.
        "published_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "has_yelp": any(r.get("on_yelp") for r in records),
        "yelp_matched": sum(1 for r in records if r.get("on_yelp")),
        "top_lead_score": round(max((_lead(r) for r in records), default=0.0), 1),
        "file": filename,
        # Curation transparency (2026-09-15, see bbb_scraper/curate.py) --
        # every dataset publish reports exactly how many rows were dropped
        # and why, rather than the reduction being invisible. Always
        # present (zeros when nothing was curated out, e.g. an old dataset
        # republished before this existed) so a UI/caller never needs a
        # None-check.
        "curated_out": curated_out or {"chain_name": 0, "corporate_account": 0, "low_score": 0},
    }
    manifest["datasets"].append(entry)
    manifest["datasets"].sort(key=lambda d: (d["metro"], d["industry"]))
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return entry


def publish_dataset(csv_path: Path, industry: str, metro: str) -> dict:
    """Publish one BBB CSV as one industry+metro dataset (BBB-side
    intelligence columns included; no Yelp)."""
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    master = _bbb_only_master(rows)
    public_rows, removed = _curate_and_select(master)
    return _write_dataset(public_rows, industry, metro, curated_out=removed)


def publish_records(records: list[dict], industry: str, metro: str) -> dict:
    """Publish in-memory BBB records (e.g. straight out of a scrape)."""
    master = _bbb_only_master(records)
    public_rows, removed = _curate_and_select(master)
    return _write_dataset(public_rows, industry, metro, curated_out=removed)


def publish_master_rows(rows: list[dict], industry: str, metro: str) -> dict:
    """Publish match.merge.build_master_table rows. `yelp_only` rows (a
    Yelp business with no BBB or Angi match) are dropped -- the site is a
    BBB/Angi directory enriched with Yelp, not a Yelp directory. `angi_only`
    rows (2026-09-17: Angi businesses with no BBB match -- see
    bbb_scraper/angi/enrich.py) are kept, same as bbb_only ones -- Angi is
    now a real discovery source in its own right, not just enrichment.

    `rows` may be a previously-written master CSV read back off disk, whose
    intel columns (lead_priority_score, ...) were computed whenever that
    file was written -- recompute_intel refreshes them against today's
    _INTEL formulas rather than trusting a possibly-stale snapshot.
    """
    keep = [r for r in rows if str(r.get("match_status") or "") != "yelp_only"]
    keep = [recompute_intel(r) for r in keep]
    public_rows, removed = _curate_and_select(keep)
    return _write_dataset(public_rows, industry, metro, curated_out=removed)


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
          f"({entry['yelp_matched']} matched to Yelp, top lead {entry['top_lead_score']})")
    print(f"Manifest now has {len(manifest['datasets'])} dataset(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
