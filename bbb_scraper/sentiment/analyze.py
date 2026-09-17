"""Rolls up a master row's already-captured reviews (mapquest_reviews/
angi_reviews/bbb_reviews -- whichever are present) into per-review
ReviewSentiment results plus business-level aggregate signals for lead
scoring.

Aggregates are deliberately stored as stable facts (dates, counts, a
historical average gap), never as a pre-computed "days since" or ratio --
anything relative to *today* drifts stale the moment the clock moves past
when this ran. bbb_scraper.match.merge computes "days since" dynamically
at scoring time instead (see its own _days_since/_review_gap_signal),
the same way recompute_intel already re-derives every other signal on
demand rather than trusting a frozen snapshot.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any

from bbb_scraper.sentiment.client import OllamaClient
from bbb_scraper.sentiment.models import ReviewSentiment

_HASH_LEN = 16


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_HASH_LEN]

_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]+)\s+(\d{4})$")

# column -> (text key, rating key, date key, date is "YYYY-MM-DD" already?)
# Angi's own date_label is month+year only ("April 2026") -- see
# bbb_scraper.angi.models.Review's own docstring -- everything else here
# (bbb, mapquest) captures an exact day.
_SOURCE_COLUMNS: dict[str, str] = {
    "mapquest_reviews": "mapquest",
    "bbb_reviews": "bbb",
    "angi_reviews": "angi",
}


def _parse_date(raw: str | None) -> date | None:
    """Best-effort: an exact "YYYY-MM-DD" (bbb/mapquest), or "Month YYYY"
    (angi's date_label -- approximated to the 1st of that month, never a
    guessed day). None for anything else rather than a wrong guess."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        # .date() immediately discards time-of-day -- there's no real
        # timezone concept left to be naive about for a bare calendar date.
        return datetime.strptime(raw, "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        pass
    m = _MONTH_YEAR_RE.match(raw)
    if not m:
        return None
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _normalize(source: str, raw: dict) -> dict[str, Any]:
    date_field = "date_label" if source == "angi" else "date"
    return {
        "source": source,
        "text": raw.get("text"),
        "rating": raw.get("rating"),
        "date": _parse_date(raw.get(date_field)),
    }


def _collect_raw_reviews(row: dict) -> list[dict]:
    """Every review already captured for this business, across whichever
    of mapquest_reviews/bbb_reviews/angi_reviews are present on the row --
    normalized to a common {source, text, rating, date} shape. Malformed
    JSON in a column is skipped, not fatal (matches this project's usual
    "a bad cell shouldn't take down the whole row" posture)."""
    collected: list[dict] = []
    for column, source in _SOURCE_COLUMNS.items():
        raw_json = row.get(column)
        if not raw_json or raw_json == "[]":
            continue
        try:
            items = json.loads(raw_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(items, list):
            continue
        collected.extend(_normalize(source, item) for item in items if isinstance(item, dict))
    return collected


def _existing_cache(row: dict) -> dict[tuple[str, str], ReviewSentiment]:
    """Whatever this row's own review_sentiment column already has, keyed
    by (source, text_hash) -- lets a re-run (--in-place over a checkpoint
    that already has some sentiment data, or the batch re-processing a
    metro) skip paying Ollama's ~2-4s/call again for a review it's already
    analyzed. Malformed/missing column -> empty cache, never fatal."""
    raw_json = row.get("review_sentiment")
    if not raw_json or raw_json == "[]":
        return {}
    try:
        items = json.loads(raw_json)
    except (TypeError, ValueError):
        return {}
    cache: dict[tuple[str, str], ReviewSentiment] = {}
    for item in items:
        if not isinstance(item, dict) or "text_hash" not in item:
            continue
        try:
            rs = ReviewSentiment(**item)
        except TypeError:
            continue  # a column written by an older/different schema -- skip, don't crash
        cache[(rs.source, rs.text_hash)] = rs
    return cache


def analyze_business_reviews(row: dict, client: OllamaClient) -> tuple[list[ReviewSentiment], dict]:
    """Every already-captured review for this business -> (per-review
    ReviewSentiment list, aggregate dict of stable facts for the master
    row). Best-effort throughout -- a single review's analysis failing
    (Ollama down, a malformed response) just excludes it from the
    results; this function itself never raises.

    Reuses any already-cached result for a review it's seen before (see
    _existing_cache) rather than re-calling Ollama -- a review's text
    never changes once captured, so its analysis doesn't need refreshing.
    """
    cache = _existing_cache(row)
    results: list[ReviewSentiment] = []
    for raw in _collect_raw_reviews(row):
        if not raw.get("text"):
            continue
        text_hash = _text_hash(raw["text"])
        cached = cache.get((raw["source"], text_hash))
        if cached is not None:
            results.append(cached)
            continue

        analysis = client.analyze_review(raw["text"], rating=raw.get("rating"))
        if analysis is None:
            continue
        results.append(ReviewSentiment(
            source=raw["source"],
            text_hash=text_hash,
            date=raw["date"].isoformat() if raw["date"] else None,
            rating=raw.get("rating"),
            **analysis,
        ))
    return results, _aggregate(results)


_NEGATIVE_SENTIMENTS = ("negative", "mixed")


def _top_complaint(results: list[ReviewSentiment]) -> dict:
    """The single most representative negative/mixed review, for a
    business-level "here's the actual problem" column (2026-09-16, Nick's
    ask) -- not a second LLM call, just picking the best already-analyzed
    one: a rep needs ONE concrete thing to open a pitch with, not a raw
    count.

    Ranked, in order: actionable_for_pitch (the model's own call on
    whether this complaint makes a usable pitch) first, then most severe,
    then most recent (a live problem opens a pitch better than an old
    one), then -- Nick's explicit call -- MapQuest-sourced preferred on
    any remaining tie. Real reason, not an arbitrary preference: in the
    first real batch run under this pipeline (Electricians/Dallas,
    2026-09-16), MapQuest was 528/528 of all analyzed review content --
    BBB review fetching isn't wired into the batch pipeline at all yet
    (only available via the separate scripts/fetch_bbb_reviews.py), and
    Angi was off for that whole run (no confident category match). So
    MapQuest isn't being artificially up-weighted here against real
    competing content -- it's already the dominant real source, and this
    tiebreak just keeps it that way if/when BBB/Angi review capture joins
    the integrated batch pipeline later.
    """
    candidates = [r for r in results if r.sentiment in _NEGATIVE_SENTIMENTS and r.summary]
    if not candidates:
        return {"top_complaint_theme": None, "top_complaint_summary": None}

    def sort_key(r: ReviewSentiment):
        parsed_date = date.fromisoformat(r.date) if r.date else date.min
        return (r.actionable_for_pitch is True, r.severity or 0, parsed_date, r.source == "mapquest")

    best = max(candidates, key=sort_key)
    return {"top_complaint_theme": best.theme, "top_complaint_summary": best.summary}


def _aggregate(results: list[ReviewSentiment]) -> dict:
    # most_recent_review_date / most_recent_negative_review_date are meant to
    # read as genuine 3rd-party review-platform activity -- Yelp (via
    # MapQuest's own review-text surface, source="mapquest"; see
    # _SOURCE_COLUMNS' own comment for why) and Angi -- not BBB's own
    # review/complaint system, which is a structurally different thing (a
    # formal complaint process, not a casual star review). 2026-09-17,
    # Nick's call: both date fields exclude source="bbb" from here on.
    # review_sentiment_analyzed_count/negative_count and top_complaint below
    # are NOT filtered this way -- a BBB-sourced review still counts as
    # analyzed and can still be the top complaint, only the two DATE fields
    # change. bbb_reviews is essentially never populated by the integrated
    # batch pipeline anyway (only scripts/fetch_bbb_reviews.py backfills it
    # by hand), so this changes nothing for almost every real record today --
    # it's here so a future bbb_reviews backfill can't silently start
    # influencing a signal/column meant to read as Yelp/Angi activity.
    third_party = [r for r in results if r.source != "bbb"]
    # Paired with source (raw "mapquest"/"angi", same vocabulary as
    # ReviewSentiment.source -- not display-formatted here; see
    # publish_site_data.py for the "mapquest" -> "Yelp" label) so the site
    # can show where its most-recent-(negative)-review date actually came
    # from. Tuples sort by date first; a same-day tie falls back to
    # comparing the source string, which is harmless -- just a stable,
    # arbitrary tiebreak.
    all_entries = sorted((date.fromisoformat(r.date), r.source) for r in third_party if r.date)
    negative_entries = sorted(
        (date.fromisoformat(r.date), r.source) for r in third_party
        if r.date and r.sentiment in _NEGATIVE_SENTIMENTS
    )

    avg_gap_days = None
    if len(all_entries) >= 2:
        all_dates = [d for d, _ in all_entries]
        gaps = [(all_dates[i] - all_dates[i - 1]).days for i in range(1, len(all_dates))]
        avg_gap_days = round(sum(gaps) / len(gaps), 1)

    return {
        "review_sentiment_analyzed_count": len(results),
        "review_sentiment_negative_count": sum(1 for r in results if r.sentiment in _NEGATIVE_SENTIMENTS),
        "most_recent_review_date": all_entries[-1][0].isoformat() if all_entries else None,
        "most_recent_review_source": all_entries[-1][1] if all_entries else None,
        "most_recent_negative_review_date": negative_entries[-1][0].isoformat() if negative_entries else None,
        "most_recent_negative_review_source": negative_entries[-1][1] if negative_entries else None,
        "avg_review_gap_days": avg_gap_days,
        **_top_complaint(results),
    }
