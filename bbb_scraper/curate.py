"""
Publish-time curation: trims a build_master_table row list down to a
genuinely sales-ready set before it reaches the public site -- dropping
detected chains/large corporate accounts and cutting off leads whose
lead_priority_score is too low to be worth a rep's time.

Deliberately applied only at publish time (scripts/publish_site_data.py),
never to the underlying checkpoint CSVs in data/processed/batch/ -- those
stay the full, uncurated master data (useful for re-analysis, re-tuning
these thresholds, or an export that wants everything), same "raw log vs.
curated view" split this project already has between businesses.csv and
the published site.

Added 2026-09-15, after a real-data audit across every batch scraped so far
(13,291 rows, 8 industries, 27 metro lists) -- not guessed at:

- **Chain detection**: the same normalized business name repeated 3+ times
  within one metro+industry list. Chosen threshold, not an arbitrary round
  number -- the real repeat-count distribution is a sharp cliff (11,091
  names appeared exactly once, 918 appeared exactly twice -- plausible
  coincidence, e.g. two unrelated small shops both named "ABC Electric" --
  then a thin tail of just 61 groups at 3+ that reads, on inspection, as a
  genuine multi-location chain almost every time: Comfort Dental x32 in
  Denver, The Home Depot x15 in Detroit, Roto-Rooter Plumbing x11 in
  Chicago, Keller Williams Realty x7 in Atlanta, a single named realtor at
  Sibcy Cline across 24 different branch-office addresses in Cincinnati/
  Northern Kentucky with no phone number to have ever let phone-based
  dedup catch it). 2.7% of every real row scraped so far falls in this
  bucket.
- **Corporate accounts**: Angi's own explicit is_corporate_account flag
  (bbb_scraper/match/merge.py's angi_corporate_account column) -- a direct
  signal, not a proxy. 2.6% of every real Angi-matched business carries it,
  and every sampled name (Groundworks, Erie Home, American Standard, Power
  Home Remodeling, Jacuzzi Bath Remodel, ...) is a recognizable national
  home-services brand -- independently corroborating the same kind of
  business the chain-name detector above catches, from a completely
  different signal (Angi's own account metadata, not a name count).
- **Low score cutoff**: conservative on purpose. Every row sampled just
  below LOW_SCORE_CUTOFF had an objectively excellent public reputation
  already -- a BBB A+ grade with zero recorded complaints, and whenever
  matched to a review platform, a 4.2-4.9 star rating. The scoring model is
  correctly identifying these as the worst possible fit for a "let us fix
  your reputation" pitch; this isn't noise being trimmed; it's leads that
  score `lead_priority_score < 30` are genuinely the wrong fit under the
  model's own stated intent. Rows with no score at all (no BBB grade, no
  complaint history, no Yelp/Angi match -- 1.0% of the real dataset once
  bbb_scraper.match.merge's complaints-based signal fix landed) are cut on
  the same basis: zero evidence either way isn't more useful than a
  confirmed-bad fit.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

CHAIN_NAME_REPEAT_THRESHOLD = 3
LOW_SCORE_CUTOFF = 30.0


def _normalized_name(row: dict[str, Any]) -> str:
    return str(row.get("bbb_name") or "").strip().lower()


def _is_corporate_account(row: dict[str, Any]) -> bool:
    v = row.get("angi_corporate_account")
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"1", "true", "yes", "y", "t"}


def _score_or_none(row: dict[str, Any]) -> float | None:
    v = row.get("lead_priority_score")
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def detect_chain_names(rows: list[dict[str, Any]], *, threshold: int = CHAIN_NAME_REPEAT_THRESHOLD) -> set[str]:
    """Normalized (stripped, lowercased) bbb_name values that appear
    `threshold` or more times in `rows` -- see the module docstring for why
    3 is the real-data-justified default. Blank names never count (an empty
    string repeating isn't a business)."""
    counts = Counter(name for r in rows if (name := _normalized_name(r)))
    return {name for name, count in counts.items() if count >= threshold}


def curate_for_publish(
    rows: list[dict[str, Any]], *,
    chain_threshold: int = CHAIN_NAME_REPEAT_THRESHOLD,
    low_score_cutoff: float | None = LOW_SCORE_CUTOFF,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One master-row list in, (kept_rows, removed_counts) out. Doesn't
    mutate `rows`; every kept row is the same object/dict it was, not a
    copy -- this only ever decides keep-or-drop, never edits a field.

    `removed_counts` breaks down *why* each row was dropped -- checked in
    this order (chain name, then corporate account, then score), so a row
    is counted exactly once even if it would have matched more than one
    reason -- so a publish step can report what actually changed instead of
    just a single before/after total. Keys always present (0 when nothing
    was removed for that reason), so a caller can print them unconditionally.

    Pass `low_score_cutoff=None` to skip that check entirely (chain/
    corporate-account filtering still applies) -- useful for a caller that
    wants the full reachable set regardless of score, e.g. a future export
    mode, without needing a second code path.
    """
    chain_names = detect_chain_names(rows, threshold=chain_threshold)
    kept: list[dict[str, Any]] = []
    removed = {"chain_name": 0, "corporate_account": 0, "low_score": 0}

    for row in rows:
        if _normalized_name(row) in chain_names:
            removed["chain_name"] += 1
            continue
        if _is_corporate_account(row):
            removed["corporate_account"] += 1
            continue
        if low_score_cutoff is not None:
            score = _score_or_none(row)
            if score is None or score < low_score_cutoff:
                removed["low_score"] += 1
                continue
        kept.append(row)

    return kept, removed
