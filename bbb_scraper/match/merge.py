"""
Turn a MatchOutcome into one wide "master" table:

  match_* meta  |  bbb_<field> ...  |  yelp_<field> ...  |  <derived BI> ...

Every row is one real-world business. `match_status` is one of
"matched" / "bbb_only" / "yelp_only". Matched rows carry both sides
horizontally; the *_only rows leave the other side's columns blank.

The derived-intelligence block (`_INTEL`) is a v1 starting point built from
what BBB + Yelp already give us. It's a plain name -> function(row) map so
adding a metric is one line -- expand it after the metrics conversation
with your mentor rather than guessing at more now.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any

from bbb_scraper.match.matcher import MatchOutcome
from bbb_scraper.match.normalize import letter_grade_to_num

# Curated columns carried from each source (keeps the master table readable
# vs. dumping all ~39 BBB columns). Add here if a field earns its place.
BBB_FIELDS = [
    "name", "phone", "city", "state", "postal_code", "rating", "rating_score",
    "accredited", "years_in_business", "website", "primary_category_name",
    "principal_contact", "profile_url", "bbb_id", "scraped_at",
]
YELP_FIELDS = [
    "name", "phone", "city", "state", "postal_code", "rating", "review_count",
    "price", "is_closed", "categories", "url", "id",
]


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes", "y", "t"}


# --- derived intelligence: name -> fn(row) -> value ----------------------

def _present_bbb(r):
    return int(r["match_status"] in ("matched", "bbb_only"))


def _present_yelp(r):
    return int(r["match_status"] in ("matched", "yelp_only"))


def _bbb_grade_num(r):
    return letter_grade_to_num(r.get("bbb_rating"))


# A Yelp rating only means something once a few reviews back it. Below
# this, treat the business as unrated (rating 0.0 / review_count 0 is
# Yelp's "no reviews yet", not a one-star business).
_MIN_REVIEWS_FOR_RATING = 5


def _yelp_rating(r):
    """Yelp star rating, but only if it's backed by >= _MIN_REVIEWS_FOR_RATING
    reviews -- otherwise None (unrated)."""
    y = _num(r.get("yelp_rating"))
    n = _num(r.get("yelp_review_count"))
    if y is None or n is None or n < _MIN_REVIEWS_FOR_RATING:
        return None
    return y


def _rating_gap(r):
    """BBB grade and Yelp stars, both put on 0-5, BBB minus Yelp.
    Positive = BBB looks kinder than Yelp does."""
    g = letter_grade_to_num(r.get("bbb_rating"))
    y = _yelp_rating(r)
    if g is None or y is None:
        return None
    return round(g / 4.33 * 5 - y, 2)


def _reputation_divergence_flag(r):
    """BBB grade A- or better but a *reviewed* Yelp rating under 3.0 --
    'clean on BBB, struggling on Yelp'. A prime reputation-work lead."""
    g = letter_grade_to_num(r.get("bbb_rating"))
    y = _yelp_rating(r)
    if g is None or y is None:
        return 0
    return int(g >= 3.67 and y < 3.0)


def _review_need_score(r):
    """0-100, higher = more likely to want review help. Yelp-side only;
    None if there's no Yelp match at all.

    - No reviews yet            -> 70  (real need, but they may not use Yelp)
    - Reviewed: 60% how low the rating is, 40% how thin the volume is
      (capped at 150 reviews).

    v1 weighting -- revisit after the metrics conversation.
    """
    if not r.get("present_yelp"):
        return None
    n = _num(r.get("yelp_review_count"))
    if n is None:
        return None
    if n < _MIN_REVIEWS_FOR_RATING:
        return 70.0
    y = _num(r.get("yelp_rating"))
    if y is None:
        return 70.0
    rating_gap = (5.0 - y) / 5.0
    volume_gap = 1.0 - min(n, 150.0) / 150.0
    return round((0.6 * rating_gap + 0.4 * volume_gap) * 100, 1)


def _low_review_volume_flag(r):
    n = _num(r.get("yelp_review_count"))
    return None if n is None else int(n < 25)


def _accredited_but_low_rated(r):
    y = _yelp_rating(r)
    if y is None:
        return 0
    return int(_truthy(r.get("bbb_accredited")) and y < 3.0)


def _lead_priority_score(r):
    """Rough composite for sorting outreach. Needs a Yelp match to mean
    anything (returns None otherwise)."""
    need = _review_need_score(r)
    if need is None:
        return None
    score = need
    if _reputation_divergence_flag(r):
        score += 15
    if _accredited_but_low_rated(r):
        score += 10
    if r.get("present_both"):
        score += 5  # both sources -> full picture, more actionable
    yrs = _num(r.get("bbb_years_in_business"))
    if yrs and yrs >= 10:
        score += 5  # established: more to protect, likelier to pay
    return round(min(score, 130), 1)


_INTEL: dict[str, Callable[[dict[str, Any]], Any]] = {
    "present_bbb": _present_bbb,
    "present_yelp": _present_yelp,
    "present_both": lambda r: int(r["match_status"] == "matched"),
    "bbb_grade_num": _bbb_grade_num,
    "rating_gap_bbb_minus_yelp": _rating_gap,
    "reputation_divergence_flag": _reputation_divergence_flag,
    "review_need_score": _review_need_score,
    "low_review_volume_flag": _low_review_volume_flag,
    "accredited_but_low_rated": _accredited_but_low_rated,
    "lead_priority_score": _lead_priority_score,
}


def _row(status: str, *, bbb: dict | None, yelp: dict | None,
         confidence: Any = "", band: str = "", signals: dict | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "match_status": status,
        "match_confidence": confidence,
        "match_band": band,
        "match_signals": json.dumps(signals, sort_keys=True) if signals else "",
    }
    for f in BBB_FIELDS:
        row[f"bbb_{f}"] = (bbb or {}).get(f, "")
    for f in YELP_FIELDS:
        v = (yelp or {}).get(f, "")
        row[f"yelp_{f}"] = json.dumps(v) if isinstance(v, (list, dict)) else v
    for name, fn in _INTEL.items():
        try:
            row[name] = fn(row)
        except Exception:  # noqa: BLE001 -- a derived column must never break the table
            row[name] = None
    return row


def build_master_table(
    outcome: MatchOutcome, *, include_yelp_only: bool = True
) -> list[dict[str, Any]]:
    """One wide row per business. `include_yelp_only=False` keeps it
    BBB-primary (matched + bbb_only rows only) -- what the batch scraper
    wants when Yelp is just supplementary enrichment."""
    rows: list[dict[str, Any]] = []
    for p in outcome.pairs:
        rows.append(
            _row("matched", bbb=p.bbb, yelp=p.yelp,
                 confidence=p.confidence, band=p.band, signals=p.signals)
        )
    for b in outcome.bbb_only:
        rows.append(_row("bbb_only", bbb=b, yelp=None))
    if include_yelp_only:
        for y in outcome.yelp_only:
            rows.append(_row("yelp_only", bbb=None, yelp=y))
    return rows
