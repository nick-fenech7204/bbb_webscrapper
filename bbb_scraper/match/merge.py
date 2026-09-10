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

# BBB columns carried into the master table. Broad enough that the master
# table + the published site record are the full BBB picture (the site's
# _PUBLIC_FIELDS all map to a `bbb_<field>` here). List/dict fields
# (categories/contacts/socials/reviews_complaints) ride through as-is
# in-memory, JSON-encoded once written to CSV.
BBB_FIELDS = [
    "name", "phone", "email", "website",
    "address", "city", "state", "postal_code",
    "rating", "rating_score", "accredited", "accreditation_status",
    "years_in_business", "business_started",
    "primary_category_name", "categories",
    "principal_contact", "contacts", "socials",
    "reviews_complaints", "organization_description", "entity_type",
    "profile_url", "bbb_id", "scraped_at",
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


def _int_or_none(v: Any) -> int | None:
    f = _num(v)
    return None if f is None else int(f)


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"true", "1", "yes", "y", "t"}


def _bbb_rc(r: dict) -> dict:
    """The BBB reviews/complaints block as a dict -- it's a JSON string off
    a CSV, a real dict in-memory, or absent on a no-details run."""
    v = r.get("bbb_reviews_complaints")
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return {}
    return {}


# A rating only means something once a few reviews back it (rating 0.0 /
# count 0 is "no reviews yet", not a one-star business).
_MIN_REVIEWS_FOR_RATING = 5


# --- derived intelligence: name -> fn(row) -> value ----------------------

def _present_bbb(r):
    return int(r["match_status"] in ("matched", "bbb_only"))


def _present_yelp(r):
    return int(r["match_status"] in ("matched", "yelp_only"))


def _bbb_grade_num(r):
    return letter_grade_to_num(r.get("bbb_rating"))


def _bbb_review_avg(r):
    """BBB's own customer-review average (0-5), distinct from the letter
    grade. None unless there are >= 3 BBB reviews behind it."""
    rc = _bbb_rc(r)
    avg = _num(rc.get("average_rating"))
    total = _num(rc.get("reviews_total"))
    if avg is None or not total or total < 3:
        return None
    return round(avg, 2)


def _bbb_reviews_total(r):
    rc = _bbb_rc(r)
    return _int_or_none(rc.get("reviews_total")) if rc else None


def _bbb_complaints_total(r):
    rc = _bbb_rc(r)
    return _int_or_none(rc.get("complaints_total")) if rc else None


def _yelp_rating(r):
    """Yelp star rating, but only if backed by >= _MIN_REVIEWS_FOR_RATING
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


def _reputation_score(r):
    """0-100 blended "how weak is this business's public reputation" --
    higher = weaker = better lead. Weighted mean over whichever signals are
    present: BBB letter grade (always), BBB's own review average + complaint
    count (detail runs), Yelp rating + review volume (matched). None only if
    there's no BBB grade and no Yelp rating at all.

    v1 weighting -- revisit after the metrics conversation.
    """
    parts: list[tuple[float, float]] = []  # (weight, 0..1 weakness)

    g = letter_grade_to_num(r.get("bbb_rating"))
    if g is not None:
        parts.append((0.30, (4.33 - g) / 4.33))

    bavg = _bbb_review_avg(r)
    if bavg is not None:
        parts.append((0.15, (5.0 - bavg) / 5.0))
    bcomp = _bbb_complaints_total(r)
    if bcomp is not None:
        parts.append((0.15, min(bcomp / 10.0, 1.0)))

    yr = _yelp_rating(r)
    if yr is not None:
        parts.append((0.25, (5.0 - yr) / 5.0))
    yn = _num(r.get("yelp_review_count"))
    if yn is not None and r.get("match_status") == "matched":
        parts.append((0.15, 1.0 - min(yn, 150.0) / 150.0))

    if not parts:
        return None
    wsum = sum(w for w, _ in parts)
    return round(sum(w * v for w, v in parts) / wsum * 100, 1)


def _reputation_divergence_flag(r):
    """BBB grade looks clean (A- or better) but the actual feedback doesn't:
    a reviewed Yelp rating under 3, OR BBB's own review average under 2.5,
    OR 5+ BBB complaints. A prime reputation-work lead."""
    g = letter_grade_to_num(r.get("bbb_rating"))
    if g is None or g < 3.67:
        return 0
    yr = _yelp_rating(r)
    if yr is not None and yr < 3.0:
        return 1
    bavg = _bbb_review_avg(r)
    if bavg is not None and bavg < 2.5:
        return 1
    bcomp = _bbb_complaints_total(r)
    if bcomp is not None and bcomp >= 5:
        return 1
    return 0


def _review_need_score(r):
    """0-100, specifically "needs help getting Yelp reviews": weak or
    missing Yelp presence. Yelp-side only -- None if there's no Yelp match.

    - No reviews yet -> 70
    - Reviewed: 60% how low the rating is, 40% how thin the volume is.
    """
    if r.get("match_status") != "matched":
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
    if n is None or r.get("match_status") != "matched":
        return None
    return int(n < 25)


def _accredited_but_low_rated(r):
    if not _truthy(r.get("bbb_accredited")):
        return 0
    yr = _yelp_rating(r)
    if yr is not None and yr < 3.0:
        return 1
    bavg = _bbb_review_avg(r)
    if bavg is not None and bavg < 3.0:
        return 1
    return 0


def _lead_priority_score(r):
    """How good a sales lead this business is *for a firm that sells review
    / reputation-management services*. Not raw reputation weakness -- it
    favors the salvageable middle: a visible, fixable problem at a business
    mature enough to pay. Both extremes (already fine / beyond help) score
    lower. 0-130. Available for any BBB record.

    v1, hand-tuned -- revisit after the metrics conversation.
    """
    signals: list[float] = []  # each 0-100, "how much this points to a good lead"

    yr = _yelp_rating(r)
    if yr is not None:
        signals.append(45 if yr <= 1.5 else 82 if yr < 3.7 else 34 if yr < 4.2 else 8)

    yn = _num(r.get("yelp_review_count"))
    if yn is not None and r.get("match_status") == "matched":
        signals.append(40 if yn == 0 else 72 if yn <= 60 else 40 if yn <= 150 else 12)

    g = letter_grade_to_num(r.get("bbb_rating"))
    if g is not None:
        signals.append(65 if 1.67 <= g <= 3.33 else 32 if g < 1.67 else 22)

    bavg = _bbb_review_avg(r)
    if bavg is not None:
        signals.append(72 if bavg < 3.5 else 15)

    if not signals:
        return None
    score = sum(signals) / len(signals)

    if _reputation_divergence_flag(r):
        score += 12  # looks fine on paper, isn't -- a wake-up-call pitch
    if _accredited_but_low_rated(r):
        score += 8
    bcomp = _bbb_complaints_total(r)
    if bcomp is not None and 1 <= bcomp <= 25:
        score += 8  # active pain, not a lost cause
    yrs = _num(r.get("bbb_years_in_business"))
    if yrs and yrs >= 10:
        score += 6  # revenue + something to protect
    if _truthy(r.get("bbb_accredited")):
        score += 4  # already pays for a reputation/credibility service

    return round(min(score, 130), 1)


_INTEL: dict[str, Callable[[dict[str, Any]], Any]] = {
    "present_bbb": _present_bbb,
    "present_yelp": _present_yelp,
    "present_both": lambda r: int(r["match_status"] == "matched"),
    "bbb_grade_num": _bbb_grade_num,
    "bbb_review_avg": _bbb_review_avg,
    "bbb_reviews_total": _bbb_reviews_total,
    "bbb_complaints_total": _bbb_complaints_total,
    "rating_gap_bbb_minus_yelp": _rating_gap,
    "reputation_score": _reputation_score,
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
