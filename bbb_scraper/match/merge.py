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
from datetime import date
from typing import Any

from bbb_scraper.match.matcher import MatchOutcome
from bbb_scraper.match.normalize import letter_grade_to_num

# BBB columns carried into the master table. Broad enough that the master
# table + the published site record are the full BBB picture (the site's
# _PUBLIC_FIELDS all map to a `bbb_<field>` here). List/dict fields
# (categories/contacts/socials/reviews_complaints) ride through as-is
# in-memory, JSON-encoded once written to CSV.
#
# lat/lon added 2026-09-16 -- a real gap, caught in a full-project audit:
# bbb_scraper.etl.transform has captured real lat/lon on every BBB record
# since the original scaffold, and publish_site_data.py's _PUBLIC_FIELDS
# has always tried to publish them (reading `bbb_lat`/`bbb_lon` off this
# exact list), but they were never actually in BBB_FIELDS -- so every
# published record's lat/lon silently came out "" no matter what was
# really scraped, with nothing downstream (no test, no site JS) ever
# reading them to notice.
BBB_FIELDS = [
    "name", "phone", "email", "website",
    "address", "city", "state", "postal_code", "lat", "lon",
    "rating", "rating_score", "accredited", "accreditation_status",
    "years_in_business", "business_started",
    "primary_category_name", "categories",
    "principal_contact", "contacts", "socials",
    "reviews_complaints", "organization_description", "entity_type",
    "profile_url", "bbb_id", "scraped_at",
    # From bbb_scraper.webcheck (scripts/check_dead_websites.py) -- absent
    # (empty string) on a row that was never run through it, which every
    # _INTEL reader below already treats as "not dead" / "not checked",
    # never as a crash.
    "website_dead", "website_status", "website_checked_at",
]
YELP_FIELDS = [
    "name", "phone", "city", "state", "postal_code", "rating", "review_count",
    "price", "is_closed", "categories", "url", "id",
]
# Angi enrichment (bbb_scraper/angi/enrich.py) -- bolt-on, not part of
# match_datasets/build_master_table's own BBB<->Yelp matching. Angi records
# are matched to an already-built master row by exact phone number only
# (Nick's call: phone is decisive enough here on its own, no need for
# Yelp's fuzzy name/geo scoring -- and Angi profiles don't carry lat/lon
# anyway, so that signal wouldn't be available even if wanted). Field names
# here match bbb_scraper.angi.models.BusinessDetail's own attribute names
# (fed in via the flattened CSV scripts/scrape_angi_category.py writes).
ANGI_FIELDS = [
    "name", "phone", "website", "address", "street", "city", "state", "zip_code",
    "overall_rating", "review_count",
    # 2026-09-16, found in a full-project audit: these five plus street/
    # is_paid_pro/licenses/highlights/searched_category/searched_metro were
    # in bbb_scraper.angi.scraper.ANGI_CSV_FIELDS (so real, already-scraped
    # data) but never made it into ANGI_FIELDS -- silently dropped before
    # ever reaching the master table, the exact "captured but never used"
    # gap this file's own BBB_FIELDS lat/lon fix (right above) also
    # addressed. Same "broad checkpoint, curate for the site separately"
    # split as everywhere else here -- none of these are in
    # publish_site_data.py's _ANGI_SITE_FIELDS, that's still a separate
    # decision.
    "rating_5_star_pct", "rating_4_star_pct", "rating_3_star_pct",
    "rating_2_star_pct", "rating_1_star_pct",
    "categories", "num_categories",
    "about_us", "highlights", "is_paid_pro",
    "is_super_service_award_winner", "is_corporate_account", "bonded", "insured", "licenses",
    # Real written reviews (2026-09-15, see bbb_scraper/angi/scraper.py's
    # business_detail_to_row) -- a JSON-string column (up to ~25 reviews,
    # newest first), the same shape the planned local-sentiment pass (BBB's
    # own scripts/fetch_bbb_reviews.py already produces) will read. Carried
    # into the checkpoint/master table for that purpose -- deliberately
    # NOT added to publish_site_data.py's _ANGI_SITE_FIELDS, that's a
    # separate decision (a raw review dump isn't public-site-ready) from
    # just getting the data flowing.
    "reviews", "num_reviews_captured",
    "searched_category", "searched_metro", "profile_url",
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


def _has_value(v: Any) -> bool:
    """Non-empty after stripping -- "is there a real value here", as opposed
    to _truthy's "does this look like a boolean yes". Used for contact
    fields (phone/email/a name): a blank string or None means we don't have
    it, regardless of what it would mean if it were 'true'/'false'."""
    return v is not None and str(v).strip() != ""


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


def _rating_band(rating: float) -> float:
    """Salvageable-middle banding for any 0-5 opinion of the business --
    Yelp stars, Angi stars, and (as of 2026-09-16) BBB's own review
    average all share this shape now: too low reads as probably-beyond-
    help, too high needs no pitch at all, the peak sits in the fixable
    middle. BBB's review average used to get a cruder binary split (just
    "under 3.5 or not") -- unified here as part of folding reputation_score
    (which DID treat BBB's average as a real, continuous signal) into this
    one score instead of running two scores side by side."""
    return 45 if rating <= 1.5 else 82 if rating < 3.7 else 34 if rating < 4.2 else 8


# --- derived intelligence: name -> fn(row) -> value ----------------------

def _present_bbb(r):
    return int(r["match_status"] in ("matched", "bbb_only"))


def _has_mapquest_yelp_data(r) -> bool:
    """True when MapQuest's own aggregate-rating block genuinely confirms
    Yelp as its source (checked against the real field, not assumed --
    see mapquest/models.py's own rating_provider comment). MapQuest's
    GraphQL surfaces real Yelp-sourced rating/review data through a
    completely separate matching pass (its own name+phone-in-city search,
    not the BBB<->Yelp Fusion matcher's confidence-scored one) -- so it's
    a second real door into the same underlying Yelp data, not a
    different platform. 2026-09-17, Nick's call, after a real batch
    (Plumbers, Charlotte NC) showed 24 real businesses with MapQuest-
    confirmed Yelp ratings that the official Fusion match missed entirely
    -- more than the 18 the official matcher found for that same metro."""
    return (r.get("mapquest_rating_provider") or "").strip().upper() == "YELP"


def _present_yelp(r):
    return int(r["match_status"] in ("matched", "yelp_only") or _has_mapquest_yelp_data(r))


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
    reviews -- otherwise None (unrated). Official Fusion API match first;
    falls back to MapQuest's own Yelp-sourced rating (_has_mapquest_yelp_data)
    when that's the only place this business's Yelp data showed up."""
    y = _num(r.get("yelp_rating"))
    n = _num(r.get("yelp_review_count"))
    if y is not None and n is not None and n >= _MIN_REVIEWS_FOR_RATING:
        return y
    if _has_mapquest_yelp_data(r):
        y = _num(r.get("mapquest_rating_value"))
        n = _num(r.get("mapquest_review_count"))
        if y is not None and n is not None and n >= _MIN_REVIEWS_FOR_RATING:
            return y
    return None


def _effective_yelp_review_count(r):
    """Yelp review count for scoring -- the official Fusion API match's own
    count when that happened, else MapQuest's Yelp-sourced count as a
    fallback. Same reasoning as _yelp_rating/_has_mapquest_yelp_data above;
    kept separate from _yelp_rating since the volume signal in
    _lead_priority_score needs the count even when there aren't enough
    reviews yet for a rating band (0 reviews is itself a real signal)."""
    if r.get("match_status") == "matched":
        n = _num(r.get("yelp_review_count"))
        if n is not None:
            return n
    if _has_mapquest_yelp_data(r):
        return _num(r.get("mapquest_review_count"))
    return None


def _angi_rating(r):
    """Angi star rating, same "needs enough reviews behind it" gate as
    _yelp_rating -- a business with 1 five-star review isn't rated, it's
    lucky. None if unmatched to Angi or not rated yet."""
    a = _num(r.get("angi_overall_rating"))
    n = _num(r.get("angi_review_count"))
    if a is None or n is None or n < _MIN_REVIEWS_FOR_RATING:
        return None
    return a


def _on_angi(r):
    return int(_has_value(r.get("angi_phone")))


def _angi_corporate_account(r):
    """Angi's own explicit franchise/corporate-account flag -- a direct
    "this isn't an independent local operator" signal, not a proxy. Added
    2026-09-15 alongside the curation pass (see bbb_scraper/curate.py):
    checked against real data first -- 23/874 real Angi-matched businesses
    across every batch so far carry this flag, and every sampled name
    (Groundworks, Erie Home, American Standard, Power Home Remodeling, ...)
    is a recognizable national home-services brand, not a false positive.
    0/blank when unmatched to Angi, same as every other angi_* signal."""
    return int(_truthy(r.get("angi_is_corporate_account")))


def _rating_gap(r):
    """BBB grade and Yelp stars, both put on 0-5, BBB minus Yelp.
    Positive = BBB looks kinder than Yelp does."""
    g = letter_grade_to_num(r.get("bbb_rating"))
    y = _yelp_rating(r)
    if g is None or y is None:
        return None
    return round(g / 4.33 * 5 - y, 2)


def _reputation_divergence_flag(r):
    """BBB grade looks clean (A- or better) but the actual feedback doesn't:
    a reviewed Yelp or Angi rating under 3, OR BBB's own review average
    under 2.5, OR 5+ BBB complaints, OR most of its analyzed reviews read
    negative/mixed. A prime reputation-work lead -- the clearest version of
    "your BBB page looks great but here's the real story" this model can
    make, which is exactly why it's the biggest single bonus in
    _lead_priority_score.

    Sentiment check added 2026-09-16, alongside folding reputation_score
    into this one score: without it, a business could look clean on grade
    AND on Yelp/Angi/BBB-avg (all unrated or all fine) while its actual
    review TEXT -- the thing a rep would read before calling -- skews
    negative, and this flag would never catch it. Requires a real analyzed
    majority (more negative/mixed than not), not a single bad review.
    """
    g = letter_grade_to_num(r.get("bbb_rating"))
    if g is None or g < 3.67:
        return 0
    yr = _yelp_rating(r)
    if yr is not None and yr < 3.0:
        return 1
    ar = _angi_rating(r)
    if ar is not None and ar < 3.0:
        return 1
    bavg = _bbb_review_avg(r)
    if bavg is not None and bavg < 2.5:
        return 1
    bcomp = _bbb_complaints_total(r)
    if bcomp is not None and bcomp >= 5:
        return 1
    analyzed = _int_or_none(r.get("review_sentiment_analyzed_count"))
    negative = _int_or_none(r.get("review_sentiment_negative_count"))
    if analyzed and negative is not None and negative / analyzed > 0.5:
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
    """2026-09-16: added the Angi check -- this predated Angi's 2026-09-14
    integration and never got updated, so a BBB-accredited business with a
    bad ANGI rating (and no Yelp match, or a fine one) silently missed this
    flag/bonus even though _reputation_divergence_flag right above already
    treats Angi as an equal-standing rating source. Same bar (< 3.0) as
    the other two checks, for the same reason."""
    if not _truthy(r.get("bbb_accredited")):
        return 0
    yr = _yelp_rating(r)
    if yr is not None and yr < 3.0:
        return 1
    ar = _angi_rating(r)
    if ar is not None and ar < 3.0:
        return 1
    bavg = _bbb_review_avg(r)
    if bavg is not None and bavg < 3.0:
        return 1
    return 0


# Effective-identity accessors: bbb_<field> when present, else angi_<field>.
# 2026-09-17, Nick's call: Angi is now a real discovery source in its own
# right (bbb_scraper/angi/enrich.py can create a genuinely new "angi_only"
# row, not just enrich an existing BBB one), so anything that used to
# assume "the business's real name/phone/city is always in bbb_*" needs a
# fallback for a row that was never on BBB at all. Kept as small, explicitly-
# named functions (not a generic "try every bbb_/angi_ pair" loop) so it's
# obvious at each call site which identity fact is being read, and so a
# BBB-specific field with no real Angi equivalent (grade, accreditation,
# complaints, principal_contact, years_in_business, lat/lon) is never
# silently given a fallback that doesn't actually make sense for it. Only
# the 4 fields an internal enrichment step (MapQuest search) actually needs
# live here -- website/postal_code/profile_url get the same fallback
# treatment, but as a small field-name mapping in publish_site_data.py's
# own select_public_fields_from_master, since that's the only place they're
# needed and a 7-entry dict there is simpler than 3 more single-field
# functions here for callers that don't exist yet.
def _effective_name(r):
    return r.get("bbb_name") or r.get("angi_name") or ""


def _effective_phone(r):
    return r.get("bbb_phone") or r.get("angi_phone") or ""


def _effective_city(r):
    return r.get("bbb_city") or r.get("angi_city") or ""


def _effective_state(r):
    return r.get("bbb_state") or r.get("angi_state") or ""


def _has_phone(r):
    return int(_has_value(_effective_phone(r)))


def _has_named_contact(r):
    return int(_has_value(r.get("bbb_principal_contact")))


def _has_email(r):
    return int(_has_value(r.get("bbb_email")))


def _contact_readiness(r):
    """Plain-language read on whether there's enough here to actually call
    or email this business today -- distinct from whether they're a good
    *fit* (lead_priority_score already covers that). A
    great lead nobody can reach isn't a working lead yet. Phone is what lets
    a rep pick up and dial; a named contact (BBB's principal_contact, e.g.
    "Glenn Wright, Manager") makes that call land on a real person instead
    of a front desk; email is a fallback channel when there's no number.
    Phone falls back to Angi's when there's no BBB one (2026-09-17) -- a
    real Angi-sourced number is just as dialable as a BBB-sourced one, and
    penalizing an angi_only row for lacking a phone it structurally can't
    have (no BBB listing to carry one) would be wrong, not conservative.
    Named contact stays BBB-only -- Angi doesn't capture an equivalent."""
    phone = _has_value(_effective_phone(r))
    contact = _has_value(r.get("bbb_principal_contact"))
    email = _has_value(r.get("bbb_email"))
    if phone and contact:
        return "Phone + named contact"
    if phone:
        return "Phone only"
    if contact or email:
        return "Contact/email only, no phone"
    return "No direct contact info"


def _contact_readiness_score(r):
    """0-100 version of _contact_readiness, for sorting the table by it.
    Phone is the hard requirement to act on a lead today; a named contact
    is a meaningful bonus on top of a phone (not a substitute for one)."""
    phone = _has_value(_effective_phone(r))
    contact = _has_value(r.get("bbb_principal_contact"))
    email = _has_value(r.get("bbb_email"))
    if phone and contact:
        return 100
    if phone:
        return 75
    if contact or email:
        return 30
    return 0


def _website_dead_flag(r):
    """1 if bbb_scraper.webcheck confirmed this business's own website is
    dead/404/parked (see bbb_scraper/webcheck -- deliberately conservative,
    only high-confidence signals count). 0 if it checked out fine OR was
    never checked at all -- this only ever asserts a problem it's actually
    confident about, never "unknown" as "dead"."""
    return int(_truthy(r.get("bbb_website_dead")))


def _bbb_complaints_signal(r):
    """How much BBB's complaint count alone points to a good lead -- same
    "salvageable middle" shape as every other signal here (peaks for a
    real, fixable amount of visible friction; dampens for zero and for a
    heavy volume), but usable even when BBB never assigned a letter grade
    at all.

    Added 2026-09-15 after a real-data audit across every batch scraped so
    far: BBB rates a business "NR" (Not Rated, no usable letter grade) for
    31.9% of every real business scraped -- and for businesses with an NR
    grade AND no Yelp/Angi match (31.3% of the whole dataset, ~4,158 real
    rows), _lead_priority_score returned None outright, every time, with
    zero exceptions -- not a low score, no score at all, silently dropped
    from every list sorted or filtered by lead priority. The data to do
    better was sitting right there unused: bbb_complaints_total is a real,
    independently-tracked BBB field that doesn't require a letter grade or
    a minimum review count -- present for 96.9% of exactly the rows that
    had no other usable signal (4,027 of 4,158). It was already feeding
    the reputation_score metric this project had at the time (folded into
    this one score on 2026-09-16 -- see _lead_priority_score) and an
    existing +8 bonus here -- but a bonus
    only ever adjusts an already-nonzero score, so it never rescued a row
    that had nothing else to start from. This makes it a real base signal
    instead, averaged in alongside whichever of grade/Yelp/Angi are
    present, and available entirely on its own when none of them are.
    """
    bcomp = _bbb_complaints_total(r)
    if bcomp is None:
        return None
    if bcomp == 0:
        return 30  # no visible complaint history -- not itself a strong pull either way
    if bcomp <= 10:
        return 68  # a handful of real, fixable complaints at a business BBB still tracks
    if bcomp <= 25:
        return 50
    return 25  # heavy complaint volume -- likely beyond what a reputation nudge fixes


def _days_since(iso_date: str | None) -> int | None:
    """iso_date must already be "YYYY-MM-DD" -- bbb_scraper.sentiment.analyze
    stores stable facts (a date, a count, a historical average), never a
    pre-computed "days since", specifically so this can compute it fresh
    against *today* rather than trusting a snapshot that goes stale the
    moment the clock moves past when the row was written -- same reason
    recompute_intel exists at all rather than trusting frozen columns."""
    if not iso_date:
        return None
    try:
        return (date.today() - date.fromisoformat(iso_date)).days  # noqa: DTZ011 -- calendar-date arithmetic, no real timezone concept applies
    except ValueError:
        return None


def _review_sentiment_signal(r):
    """0-100, "how much recent, real negative review sentiment points to a
    good lead" -- local Ollama analysis of mapquest_reviews/bbb_reviews/
    angi_reviews text (bbb_scraper.sentiment), not a fixed-vocabulary
    keyword match. None when nothing's been analyzed yet (most rows,
    until scripts/analyze_review_sentiment.py or the batch's --sentiment
    step runs on them).

    v1 thresholds -- hand-tuned against a real audit, 2026-09-15, of every
    review analyzed so far (134 businesses, 699 reviews, 3 real metros):
      - 44% of analyzed businesses (59/134) had ZERO negative/mixed
        sentiment -- a real, common case, not an edge case; scored low
        but not zero, same as _bbb_complaints_signal's bcomp==0 case.
      - Negative ratio bands follow the same "salvageable middle" shape
        as every other signal here: some real friction (<=60% negative)
        is the clearest pitch, not none and not total -- but 100%
        negative (12/134, 9%) is real signal too (often a thin sample,
        1-2 reviews, but still a real documented complaint) and stays
        meaningfully high, just not the peak.
      - Recency is a smooth multiplier, deliberately NOT a hard gate: of
        the 75 businesses with a dated negative review, only 13 (17%)
        were within 6 months -- the real majority (44, 59%) were 2+
        years old. Gating hard on recency would have thrown out most of
        this project's real complaint signal; old-but-real friction
        still says something, just less urgently than a live problem.
      - severity is deliberately NOT weighted here: real data shows the
        model calls 109/143 (76%) of negative/mixed reviews severity 5 --
        it's tracking sentiment more than it's discriminating severity in
        practice with the current prompt, so treating it as an
        independent driver would just double-count the sentiment label
        under a different name. Revisit if the prompt is tuned to spread
        severity out more.
    """
    analyzed = _int_or_none(r.get("review_sentiment_analyzed_count"))
    if not analyzed:
        return None
    negative = _int_or_none(r.get("review_sentiment_negative_count")) or 0

    if negative == 0:
        return 18  # real reviews analyzed, no negative/mixed sentiment found -- not a pull either way

    ratio = negative / analyzed
    if ratio <= 0.6:
        base = 78  # the salvageable middle -- real, fixable-looking friction, not the whole story
    elif ratio < 1.0:
        base = 60
    else:
        base = 45  # every analyzed review negative -- often a thin sample; real, but a harder pitch to frame as "a quick fix"

    days_since = _days_since(r.get("most_recent_negative_review_date"))
    if days_since is None:
        recency_mult = 0.85  # negative sentiment exists but its date didn't parse
    elif days_since <= 180:
        recency_mult = 1.15  # an active, current problem
    elif days_since <= 365:
        recency_mult = 1.0
    elif days_since <= 730:
        recency_mult = 0.85
    else:
        recency_mult = 0.7  # old history -- real, but not urgent

    return round(min(base * recency_mult, 100), 1)


def _review_gap_flag(r):
    """1 if this business has gone notably quiet relative to its OWN
    historical review cadence. Deliberately relative, not a fixed day
    count: real data (2026-09-15, 107 businesses with a computable gap)
    spans 3 to 3890 days between reviews (median 384) -- how often a
    business normally gets reviewed varies far too much across
    businesses for one universal threshold to mean anything, so this
    only fires for a multi-x outlier against that same business's own
    baseline.

    Deliberately ambiguous on its own -- a real quiet period could mean
    the problem got fixed and reviews naturally slowed, or it could mean
    a reputation bad enough that people stopped engaging at all -- so
    this is a small bonus in _lead_priority_score, not an independent
    averaged signal the way _review_sentiment_signal is.
    """
    avg_gap = _num(r.get("avg_review_gap_days"))
    days_since = _days_since(r.get("most_recent_review_date"))
    if avg_gap is None or avg_gap <= 0 or days_since is None:
        return 0
    return int(days_since > avg_gap * 3)


# Per-source weights for _lead_priority_score's base signal. Each of the 4
# sources below is blended into exactly ONE 0-100 value first (see the
# function itself), then combined via these weights -- replacing the old
# design where BBB, having 3 sub-metrics (grade/review-average/complaints)
# against Yelp's and Angi's 2 each (rating/volume), silently got 3 votes in
# a flat average instead of 1 -- up to 37.5% of an 8-signal average just
# because of how many columns BBB happens to expose, not by any deliberate
# choice. Nick's call, 2026-09-17, after a real sample batch (Electricians,
# Nashville, TN) made the old imbalance concrete: BBB stays the required
# discovery source (a business still has to be a BBB record to exist in
# this pipeline at all -- that part's intentional and unchanged), but once
# a business IS in the table, he wants Yelp/Angi/review-sentiment weighted
# well above BBB, not the other way around. Weights are renormalized over
# whichever sources are actually present for a given row (see the
# total_weight division below), so e.g. a BBB-only record still scores
# sensibly off just its own blended value rather than getting silently
# capped near 20. v1, hand-tuned -- revisit after the metrics conversation,
# same as every other weight in this file.
_SOURCE_WEIGHTS = {"yelp": 0.30, "angi": 0.25, "sentiment": 0.25, "bbb": 0.20}


def _lead_priority_score(r):
    """How good a sales lead this business is *for a firm that sells review
    / reputation-management services* -- the one score this project scores
    a lead by (2026-09-16: folded in what used to be a separate
    reputation_score number; see this function's own recent history for
    why). Not raw reputation weakness -- it favors the salvageable middle:
    a visible, fixable problem at a business mature enough to pay. Both
    extremes (already fine / beyond help) score lower. Reachability (phone
    / named contact) then scales the result -- the best-fit lead in the
    world is dead weight this week if there's no number to call. 0-130.
    Available for any BBB record with *any* usable signal -- including BBB
    complaint history alone now, see _bbb_complaints_signal's docstring for
    why that matters.

    Every base signal is either a salvageable-middle band (rating/volume/
    grade/complaints/sentiment: too little or too much both score lower
    than a real, fixable, visible problem) or a genuinely distinct bonus on
    top (divergence, tenure, accreditation, reachability) -- never the same
    underlying fact counted twice. See _rating_band for the shared 0-5-
    rating curve (Yelp, Angi, and BBB's own review average all use it).

    2026-09-17: each of the 4 sources (Yelp, Angi, BBB, sentiment) blends
    its own sub-metrics into one 0-100 value, THEN those 4 values combine
    via _SOURCE_WEIGHTS -- see that constant's own comment for why (source-
    count fairness, not just sub-metric-count fairness). v1, hand-tuned --
    revisit after the metrics conversation.
    """
    source_scores: dict[str, float] = {}

    yelp_parts: list[float] = []
    yr = _yelp_rating(r)
    if yr is not None:
        yelp_parts.append(_rating_band(yr))
    yn = _effective_yelp_review_count(r)
    if yn is not None:
        yelp_parts.append(40 if yn == 0 else 72 if yn <= 60 else 40 if yn <= 150 else 12)
    if yelp_parts:
        source_scores["yelp"] = sum(yelp_parts) / len(yelp_parts)

    # Angi: same banding as Yelp above -- both are 5-star homeowner-review
    # platforms, and there isn't yet enough Angi-specific data in this
    # project to justify a different curve.
    angi_parts: list[float] = []
    ar = _angi_rating(r)
    if ar is not None:
        angi_parts.append(_rating_band(ar))
    an = _num(r.get("angi_review_count"))
    if an is not None and _has_value(r.get("angi_phone")):
        angi_parts.append(40 if an == 0 else 72 if an <= 60 else 40 if an <= 150 else 12)
    if angi_parts:
        source_scores["angi"] = sum(angi_parts) / len(angi_parts)

    # BBB: grade + its own review average + complaint history, blended into
    # one value the same way Yelp/Angi's rating+volume are above -- this is
    # the actual fix for the old imbalance, see _SOURCE_WEIGHTS' comment.
    bbb_parts: list[float] = []
    g = letter_grade_to_num(r.get("bbb_rating"))
    if g is not None:
        bbb_parts.append(65 if 1.67 <= g <= 3.33 else 32 if g < 1.67 else 22)
    # BBB's own review average -- same _rating_band curve as Yelp/Angi above
    # (2026-09-16: previously a cruder binary 72/15 split; unified as part
    # of folding reputation_score, which treated this as a real continuous
    # signal, into this one score).
    bavg = _bbb_review_avg(r)
    if bavg is not None:
        bbb_parts.append(_rating_band(bavg))
    bcomp_signal = _bbb_complaints_signal(r)
    if bcomp_signal is not None:
        bbb_parts.append(bcomp_signal)
    if bbb_parts:
        source_scores["bbb"] = sum(bbb_parts) / len(bbb_parts)

    # Local review-sentiment analysis, added 2026-09-15 (bbb_scraper.sentiment)
    # -- None for the vast majority of rows until analyze_review_sentiment.py
    # or the batch's --sentiment step has actually run on them, same
    # "additive, never subtracts a signal that isn't there yet" shape as
    # every other optional signal here. Already a single blended value (no
    # per-source sub-metrics of its own to average together first).
    sentiment_signal = _review_sentiment_signal(r)
    if sentiment_signal is not None:
        source_scores["sentiment"] = sentiment_signal

    if not source_scores:
        return None
    total_weight = sum(_SOURCE_WEIGHTS[s] for s in source_scores)
    score = sum(source_scores[s] * _SOURCE_WEIGHTS[s] for s in source_scores) / total_weight

    if _reputation_divergence_flag(r):
        score += 12  # looks fine on paper, isn't -- a wake-up-call pitch
    if _accredited_but_low_rated(r):
        score += 8
    if _review_gap_flag(r):
        score += 6  # gone notably quiet vs. its own history -- ambiguous alone, so a small nudge, not a driver
    # No separate bcomp bonus here (removed 2026-09-16): _bbb_complaints_signal
    # above already averages this exact bbb_complaints_total value into
    # `bbb_parts` -- an extra flat +8 for the same 1-25 range was double-
    # counting the one input, on top of what every other signal here gets
    # (counted once, via the average). Verified live: a grade=B row jumped
    # 47.5 -> 74.5 (+27) going from 0 to 5 complaints, only 19 of which came
    # from the base-signal band shift -- the rest was this redundant bonus.
    yrs = _num(r.get("bbb_years_in_business"))
    if yrs and yrs >= 10:
        score += 6  # revenue + something to protect
    if _truthy(r.get("bbb_accredited")):
        score += 4  # already pays for a reputation/credibility service

    # Reachability. A phone number is the hard requirement for a rep to
    # act on this today; no phone means real extra work (hunting down a
    # number elsewhere) before the lead is usable at all, so it's a real
    # cut -- not a disqualifier, since the business is still findable, just
    # not a "call it this afternoon" lead. A named contact on top of a
    # phone is a smaller bonus: the call lands on a real person instead of
    # a front desk, but it was already dialable without one. Falls back to
    # Angi's phone when there's no BBB one (2026-09-17, see
    # _effective_phone's own comment) -- an angi_only business with a real
    # phone number is exactly as reachable as a BBB one with the same.
    if _has_value(_effective_phone(r)):
        if _has_value(r.get("bbb_principal_contact")):
            score += 5
    else:
        score *= 0.5

    return round(min(score, 130), 1)


_INTEL: dict[str, Callable[[dict[str, Any]], Any]] = {
    "present_bbb": _present_bbb,
    "present_yelp": _present_yelp,
    "present_both": lambda r: int(r["match_status"] == "matched"),
    "on_angi": _on_angi,
    "angi_corporate_account": _angi_corporate_account,
    "bbb_grade_num": _bbb_grade_num,
    "bbb_review_avg": _bbb_review_avg,
    "bbb_reviews_total": _bbb_reviews_total,
    "bbb_complaints_total": _bbb_complaints_total,
    "rating_gap_bbb_minus_yelp": _rating_gap,
    "reputation_divergence_flag": _reputation_divergence_flag,
    "review_need_score": _review_need_score,
    "low_review_volume_flag": _low_review_volume_flag,
    "accredited_but_low_rated": _accredited_but_low_rated,
    "review_sentiment_signal": _review_sentiment_signal,
    "review_gap_flag": _review_gap_flag,
    "lead_priority_score": _lead_priority_score,
    "has_phone": _has_phone,
    "has_named_contact": _has_named_contact,
    "has_email": _has_email,
    "contact_readiness": _contact_readiness,
    "contact_readiness_score": _contact_readiness_score,
    "website_dead_flag": _website_dead_flag,
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
        v = (bbb or {}).get(f, "")
        # JSON-encode list/dict values same as YELP_FIELDS below -- BBB has
        # more of these (categories/contacts/socials/reviews_complaints) than
        # Yelp does, so this asymmetry was more latent risk than Yelp's own
        # version of the same line: a caller that writes build_master_table's
        # output straight to csv.DictWriter without CSVSink's flatten_record
        # first (match_bbb_yelp.py's BBB input happens to already be
        # pre-stringified via a CSV round-trip today, so this was dormant,
        # not live) would otherwise get invalid-JSON Python repr() strings
        # for these fields instead of real JSON (2026-09-17 audit).
        row[f"bbb_{f}"] = json.dumps(v) if isinstance(v, (list, dict)) else v
    for f in YELP_FIELDS:
        v = (yelp or {}).get(f, "")
        row[f"yelp_{f}"] = json.dumps(v) if isinstance(v, (list, dict)) else v
    for name, fn in _INTEL.items():
        try:
            row[name] = fn(row)
        except Exception:  # noqa: BLE001 -- a derived column must never break the table
            row[name] = None
    return row


def recompute_intel(row: dict[str, Any]) -> dict[str, Any]:
    """Recompute every _INTEL column on a row that already has its
    bbb_*/yelp_*/match_status columns -- e.g. one read back off a
    previously-written master-table CSV. build_master_table computes intel
    fresh every time it builds a row, so this is only needed when you have
    a row that *didn't* just come out of build_master_table: publishing an
    old master CSV trusts whatever intel columns are already sitting in
    it, which goes stale the moment the _INTEL formulas change. Returns a
    new dict; doesn't mutate the one you pass in."""
    row = dict(row)
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
