#!/usr/bin/env python
"""
Run local sentiment analysis (Ollama, zero-shot -- no training/fine-tuning,
see bbb_scraper/sentiment/client.py's own module docstring) over every
review already captured in a checkpoint CSV -- mapquest_reviews/
bbb_reviews/angi_reviews, whichever are present -- writing review_sentiment
(JSON-in-cell, one entry per analyzed review) plus the aggregate columns
lead scoring (and the published site) reads: review_sentiment_analyzed_count,
review_sentiment_negative_count, most_recent_review_date,
most_recent_negative_review_date, avg_review_gap_days, top_complaint_theme,
top_complaint_summary.

Unlike scripts/fetch_bbb_reviews.py / fetch_mapquest_reviews.py, this
doesn't hit any external site -- it only reads data already sitting in
the CSV, so there's no per-request politeness/cost concern the way there
is fetching from a real third party. Defaults to every row with review
text, not a --top N curated subset; --top/--max-businesses remain
available for a quick test run on a subset. The real cost here is your
own machine's time (Ollama, ~2-4s/review once warm, ~44s cold-start for
the first call) -- --in-place re-runs skip any review already analyzed
(see bbb_scraper.sentiment.analyze's own cache), so a re-run after
capturing more reviews only pays for the new ones.

Usage:
    python scripts/analyze_review_sentiment.py data/processed/batch/plumbers--chicago-il.csv \\
        --in-place
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
from bbb_scraper.match.merge import recompute_intel
from bbb_scraper.sentiment.analyze import analyze_business_reviews
from bbb_scraper.sentiment.client import OllamaClient, is_available

configure_logging()
logger = get_logger(__name__)

_REVIEW_COLUMNS = ("mapquest_reviews", "bbb_reviews", "angi_reviews")

_NEW_COLUMNS = (
    "review_sentiment", "review_sentiment_analyzed_count", "review_sentiment_negative_count",
    "most_recent_review_date", "most_recent_negative_review_date", "avg_review_gap_days",
    # 2026-09-16: added alongside bbb_scraper.sentiment.analyze._top_complaint
    # -- real incident, caught live: these two were correctly computed and
    # applied to `row` via the generic aggregate.items() loop below, but
    # csv.DictWriter's extrasaction="ignore" silently drops any row key not
    # listed here, so they never actually reached the written CSV until
    # this list was updated to match. batch_scrape_metros.py's own inline
    # enrichment was unaffected -- it writes a JSON file, not a fixed-
    # fieldname CSV, so it never had this failure mode.
    "top_complaint_theme", "top_complaint_summary",
)


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("-inf")


def _has_any_review_text(row: dict) -> bool:
    return any(row.get(c) not in (None, "", "[]") for c in _REVIEW_COLUMNS)


def _select_rows(rows: list[dict], *, top: int | None, max_businesses: int | None,
                  score_field: str) -> list[dict]:
    candidates = [r for r in rows if _has_any_review_text(r)]
    if top is not None:
        candidates = sorted(candidates, key=lambda r: _num(r.get(score_field)), reverse=True)[:top]
    if max_businesses is not None:
        candidates = candidates[:max_businesses]
    return candidates


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", type=Path, help="A master-table/checkpoint CSV (data/processed/batch/*.csv)")
    ap.add_argument("--score-field", default="lead_priority_score",
                     help="Column --top sorts by, descending (default: lead_priority_score)")
    ap.add_argument("--top", type=int, default=None,
                     help="Only analyze the top N rows (by --score-field) that have review text -- "
                          "default is every row with review text, since this reads data already "
                          "captured rather than hitting an external site")
    ap.add_argument("--max-businesses", type=int, default=None, help="Hard cap regardless of --top")
    ap.add_argument("--output", type=Path, default=None,
                     help="Where to write the result (default: <path stem>--sentiment<suffix>)")
    ap.add_argument("--in-place", action="store_true",
                     help="Overwrite --path directly instead of writing a new file "
                          "(a timestamped backup is written to data/processed/archive/ first, "
                          "same as fetch_bbb_reviews.py/fetch_mapquest_reviews.py)")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"No such file: {args.path}")
        return 1

    if not is_available():
        print("Ollama isn't reachable -- is it running? (checked http://localhost:11434/api/tags)")
        return 1

    with args.path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        all_rows = list(reader)
    print(f"Loaded {len(all_rows)} rows from {args.path}")

    with_reviews = sum(1 for r in all_rows if _has_any_review_text(r))
    if with_reviews == 0:
        print("No rows have review text yet (mapquest_reviews/bbb_reviews/angi_reviews are all empty) -- "
              "nothing to analyze. Run fetch_mapquest_reviews.py/fetch_bbb_reviews.py first, or re-scrape "
              "with the batch's MapQuest/Angi steps on.")
        return 0

    selected = _select_rows(all_rows, top=args.top, max_businesses=args.max_businesses,
                             score_field=args.score_field)
    selected_ids = {id(r) for r in selected}
    print(f"{with_reviews} row(s) have review text; analyzing {len(selected)} of them...")

    started = time.time()
    businesses_done = 0
    reviews_total = 0
    negative_total = 0

    with OllamaClient() as client:
        for i, row in enumerate(selected, 1):
            try:
                results, aggregate = analyze_business_reviews(row, client)
            except Exception:  # one business's analysis failing must not lose the rest of the run
                logger.exception("Sentiment analysis failed for row %d", i)
                continue

            businesses_done += 1
            reviews_total += len(results)
            negative_total += aggregate["review_sentiment_negative_count"]

            row["review_sentiment"] = json.dumps([asdict(r) for r in results], ensure_ascii=False)
            for key, value in aggregate.items():
                row[key] = value if value is not None else ""
            # This row's lead_priority_score/review_sentiment_signal/
            # review_gap_flag/etc. were computed by build_master_table
            # before this sentiment data existed -- refresh them in place
            # now that it does, same as batch_scrape_metros.py's own
            # inline sentiment step (_enrich_metro_with_sentiment) and
            # bbb_scraper.angi.enrich.enrich_with_angi both already do
            # right after adding a signal that feeds scoring. Mutates
            # `row` in place (via .update, not reassignment) rather than
            # rebinding it to recompute_intel's return value -- `row` is
            # the same object sitting in `all_rows`, and rebinding the
            # local name here wouldn't update that shared reference.
            row.update(recompute_intel(row))

            if i % 20 == 0 or i == len(selected):
                elapsed = time.time() - started
                print(f"  {i}/{len(selected)} businesses ({reviews_total} reviews seen, "
                      f"{client.calls_made} real Ollama calls, {negative_total} negative/mixed, "
                      f"{elapsed / 60:.1f}min)")

    for row in all_rows:
        if id(row) not in selected_ids:
            for col in _NEW_COLUMNS:
                row.setdefault(col, "")

    if args.in_place:
        archive_dir = REPO_ROOT / "data" / "processed" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = archive_dir / f"{args.path.stem}.pre-sentiment-{stamp}{args.path.suffix}"
        shutil.copy2(args.path, backup)
        print(f"Backed up original to {backup}")
        output_path = args.path
    else:
        output_path = args.output or args.path.with_name(f"{args.path.stem}--sentiment{args.path.suffix}")

    # A real union of every key actually on any row, not just the original
    # CSV's own fieldnames + _NEW_COLUMNS -- the same bug this file's own
    # _NEW_COLUMNS comment above describes (extrasaction="ignore" silently
    # dropping a key missing from the writer's fieldnames) also applies to
    # row.update(recompute_intel(row)) above: a checkpoint written before a
    # newer _INTEL column existed (e.g. review_sentiment_signal/
    # review_gap_flag, added 2026-09-15/16) won't have that column in
    # `fieldnames`, so recompute_intel setting it in memory wouldn't be
    # enough on its own -- it needs to actually reach the writer's fieldname
    # list too. Matches batch_scrape_metros.py's own _write_partial_checkpoint/
    # rebuild_all_metros_file, which build fieldnames the same dynamic way
    # for the identical reason.
    out_fieldnames = list(fieldnames) + sorted(
        {key for row in all_rows for key in row} - set(fieldnames)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    elapsed = time.time() - started
    print(f"\nWrote {output_path}")
    print(f"{businesses_done} business(es) processed, {reviews_total} review(s) seen "
          f"({client.calls_made} real Ollama calls, {reviews_total - client.calls_made} from cache, "
          f"{negative_total} negative/mixed), {elapsed / 60:.1f} min.")
    if not args.in_place:
        print(f"This wrote a new file, not {args.path} -- pass --in-place to overwrite it directly "
              f"(a backup is made first).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
