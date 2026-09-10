"""
Scrape Yelp for one industry+location, match it to a BBB CSV, and write the
wide BBB|Yelp master table (with the v1 derived-intelligence columns).

    python scripts/match_bbb_yelp.py \
        --bbb-csv data/processed/miami_car_dealers_full.csv \
        --industry "car dealers" --location "Miami, FL"       # ~5 API calls

Yelp calls are disk-cached (data/raw/yelp/), so re-running is free.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bbb_scraper.logging_setup import get_logger
from bbb_scraper.match.matcher import match_datasets
from bbb_scraper.match.merge import build_master_table
from bbb_scraper.yelp.client import YelpClient
from bbb_scraper.yelp.extract import YelpExtractor

logger = get_logger("match_bbb_yelp")


def load_bbb_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbb-csv", required=True, type=Path)
    ap.add_argument("--industry", required=True, help="Yelp search term")
    ap.add_argument("--location", required=True, help="place string Yelp geocodes")
    ap.add_argument("--yelp-term", default=None, help="override Yelp term (default: --industry)")
    ap.add_argument("--yelp-sort", default=None,
                    choices=["best_match", "rating", "review_count", "distance"],
                    help="Yelp sort (default: Yelp's best_match). 'distance' pulls the "
                         "240 closest to center -- better overlap with a tight local pool.")
    ap.add_argument("--max-yelp", type=int, default=240, help="Yelp results ceiling (<=240)")
    ap.add_argument("--name-drop", default="", help="comma-sep extra name tokens to ignore in matching")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--no-cache", action="store_true", help="force live Yelp calls")
    args = ap.parse_args()

    bbb = load_bbb_csv(args.bbb_csv)
    logger.info("loaded %d BBB rows from %s", len(bbb), args.bbb_csv)

    extractor = YelpExtractor(YelpClient(use_cache=not args.no_cache))
    term = args.yelp_term or args.industry
    yb = extractor.search_area(
        term, args.location, max_results=args.max_yelp, sort_by=args.yelp_sort,
    )
    logger.info("yelp: %d businesses (%d live API calls)", len(yb), extractor.client.calls_made)
    if extractor.client.last_rate_limit:
        logger.info("yelp quota now: %s", extractor.client.last_rate_limit)
    yelp = [b.to_match_dict() for b in yb]

    name_drop = {t.strip().lower() for t in args.name_drop.split(",") if t.strip()}
    outcome = match_datasets(bbb, yelp, name_extra_drop=name_drop or None)
    rows = build_master_table(outcome)

    slug = f"{args.industry}--{args.location}".lower()
    slug = "".join(c if c.isalnum() else "-" for c in slug).strip("-")
    slug = "-".join(filter(None, slug.split("-")))
    out = args.out or Path("data/processed") / f"bbb_yelp_master__{slug}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    s = outcome.summary
    print("\n" + "=" * 66)
    print(f"  BBB rows:   {s['bbb_total']}")
    print(f"  Yelp rows:  {s['yelp_total']}")
    print(f"  Matched:    {s['matched']}   (confident {s['confident']} / review {s['review']})")
    print(f"  BBB only:   {s['bbb_only']}")
    print(f"  Yelp only:  {s['yelp_only']}")
    print("=" * 66)

    print("\n  sample matches (bbb name  <->  yelp name  |  conf  signals):")
    for p in sorted(outcome.pairs, key=lambda p: p.confidence, reverse=True)[:12]:
        sg = " ".join(f"{k}={v:.2f}" for k, v in p.signals.items())
        print(f"   {p.bbb.get('name','?')[:30]:30} <-> {p.yelp.get('name','?')[:30]:30} "
              f"{p.confidence:.2f} [{p.band[:4]}]  {sg}")

    leads = [r for r in rows if r.get("lead_priority_score") is not None]
    leads.sort(key=lambda r: r["lead_priority_score"], reverse=True)
    print("\n  top 10 leads by lead_priority_score:")
    for r in leads[:10]:
        print(f"   {str(r.get('bbb_name') or r.get('yelp_name'))[:32]:32} "
              f"prio={r['lead_priority_score']:>5}  need={r['review_need_score']:>5}  "
              f"bbb={r.get('bbb_rating') or '-':>2} yelp={r.get('yelp_rating') or '-'}"
              f" ({r.get('yelp_review_count') or 0} rev)  "
              f"{'DIVERGENCE' if r.get('reputation_divergence_flag') else ''}")

    print(f"\n  wrote {len(rows)} rows x {len(fieldnames)} cols -> {out}\n")
    print("  intel columns:", ", ".join(
        k for k in fieldnames if k not in ("match_status", "match_confidence", "match_band", "match_signals")
        and not k.startswith(("bbb_", "yelp_"))))


if __name__ == "__main__":
    main()
