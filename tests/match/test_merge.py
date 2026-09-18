from datetime import date, timedelta

import pytest

from bbb_scraper.match.matcher import MatchedPair, MatchOutcome
from bbb_scraper.match.merge import _SOURCE_WEIGHTS, build_master_table, recompute_intel


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()  # noqa: DTZ011 -- calendar-date test fixture, no timezone concept applies


def _pair(bbb, yelp, conf=0.95, band="confident"):
    return MatchedPair(bbb=bbb, yelp=yelp, confidence=conf, band=band,
                       signals={"phone": 1.0, "name": 1.0})


def test_matched_row_has_both_sides_horizontally():
    out = MatchOutcome(pairs=[_pair(
        {"name": "Machado Auto Sales", "rating": "A+", "accredited": "False", "phone": "(305) 642-4409"},
        {"name": "Machado Auto Sales", "rating": 4.5, "review_count": 40, "id": "abc", "url": "u"},
    )])
    rows = build_master_table(out)
    assert len(rows) == 1
    r = rows[0]
    assert r["match_status"] == "matched"
    assert r["bbb_name"] == "Machado Auto Sales"
    assert r["yelp_name"] == "Machado Auto Sales"
    assert r["bbb_rating"] == "A+"
    assert r["yelp_rating"] == 4.5
    assert r["present_both"] == 1


def test_bbb_only_row_still_gets_bbb_side_scoring():
    out = MatchOutcome(bbb_only=[{"name": "Lonely BBB Co", "rating": "C", "years_in_business": "15"}])
    r = build_master_table(out)[0]
    assert r["match_status"] == "bbb_only"
    assert r["bbb_name"] == "Lonely BBB Co"
    assert r["yelp_name"] == ""
    assert r["present_bbb"] == 1 and r["present_yelp"] == 0
    assert r["review_need_score"] is None  # Yelp-specific -> None without a match
    # but the BBB-driven score is still there
    assert r["bbb_grade_num"] == 2.0
    assert r["lead_priority_score"] is not None  # a C-grade, 15yo business is a real lead


def test_unrated_yelp_listing_is_not_treated_as_one_star():
    """rating 0.0 + review_count 0 is Yelp's 'no reviews yet'."""
    out = MatchOutcome(pairs=[_pair(
        {"name": "Better Auto of Miami", "rating": "A+", "accredited": "True"},
        {"name": "Better Auto of Miami", "rating": 0.0, "review_count": 0, "id": "x", "url": "u"},
    )])
    r = build_master_table(out)[0]
    assert r["reputation_divergence_flag"] == 0
    assert r["accredited_but_low_rated"] == 0
    assert r["rating_gap_bbb_minus_yelp"] is None
    assert r["review_need_score"] == 70.0  # "no reviews" need, not 100


def test_include_yelp_only_false_drops_the_yelp_only_tail():
    out = MatchOutcome(
        pairs=[_pair({"name": "A"}, {"name": "A", "id": "1", "url": "u"})],
        bbb_only=[{"name": "B"}],
        yelp_only=[{"name": "C", "id": "2", "url": "u"}],
    )
    full = build_master_table(out)
    assert {r["match_status"] for r in full} == {"matched", "bbb_only", "yelp_only"}

    primary = build_master_table(out, include_yelp_only=False)
    assert {r["match_status"] for r in primary} == {"matched", "bbb_only"}
    assert len(primary) == 2


def test_divergence_flag_fires_on_clean_grade_but_bad_yelp():
    out = MatchOutcome(pairs=[_pair(
        {"name": "Cars Business LLC", "rating": "A+", "years_in_business": "12"},
        {"name": "Cars Business", "rating": 2.5, "review_count": 30, "id": "x", "url": "u"},
    )])
    r = build_master_table(out)[0]
    assert r["reputation_divergence_flag"] == 1
    assert r["review_need_score"] > 40
    assert r["lead_priority_score"] is not None


def test_divergence_flag_fires_on_bbb_complaints_alone():
    """No Yelp match, A+ grade, but 8 BBB complaints -> still a divergence lead."""
    out = MatchOutcome(bbb_only=[{
        "name": "Looks Fine Motors", "rating": "A+",
        "reviews_complaints": '{"reviews_total": 1, "average_rating": 1, "complaints_total": 8}',
    }])
    r = build_master_table(out)[0]
    assert r["bbb_complaints_total"] == 8
    assert r["reputation_divergence_flag"] == 1


# --- _accredited_but_low_rated (2026-09-16: no dedicated test existed at
# all before this -- only an incidental accredited_but_low_rated == 0
# assertion elsewhere) -------------------------------------------------------

def test_accredited_but_low_rated_fires_on_low_yelp():
    out = MatchOutcome(pairs=[_pair(
        {"name": "X", "rating": "A+", "accredited": "True"},
        {"name": "X", "rating": 2.0, "review_count": 30, "id": "i", "url": "u"},
    )])
    r = build_master_table(out)[0]
    assert r["accredited_but_low_rated"] == 1


def test_accredited_but_low_rated_fires_on_low_angi():
    """2026-09-16 regression: this predated Angi's integration and never
    checked it, even though _reputation_divergence_flag right above it
    already treats Angi as an equal-standing rating source -- a BBB-
    accredited business with a bad Angi rating (no Yelp match at all)
    silently missed both the flag and its +8 bonus."""
    out = MatchOutcome(bbb_only=[{
        "name": "X", "rating": "A+", "accredited": "True", "phone": "3055550100",
    }])
    row = build_master_table(out)[0]
    assert row["accredited_but_low_rated"] == 0  # no Angi match yet -- nothing to flag

    row["angi_phone"] = "3055550100"
    row["angi_overall_rating"] = "2.0"
    row["angi_review_count"] = "20"
    row = recompute_intel(row)
    assert row["accredited_but_low_rated"] == 1


def test_accredited_but_low_rated_fires_on_low_bbb_average():
    out = MatchOutcome(bbb_only=[{
        "name": "X", "rating": "A+", "accredited": "True",
        "reviews_complaints": '{"reviews_total": 5, "average_rating": 2.0}',
    }])
    r = build_master_table(out)[0]
    assert r["accredited_but_low_rated"] == 1


def test_accredited_but_low_rated_off_when_not_accredited():
    """Every rating here is low -- but accreditation is the gate, checked first."""
    out = MatchOutcome(pairs=[_pair(
        {"name": "X", "rating": "A+", "accredited": "False"},
        {"name": "X", "rating": 1.5, "review_count": 30, "id": "i", "url": "u"},
    )])
    r = build_master_table(out)[0]
    assert r["accredited_but_low_rated"] == 0


def test_accredited_but_low_rated_off_when_ratings_are_all_fine():
    out = MatchOutcome(pairs=[_pair(
        {"name": "X", "rating": "A+", "accredited": "True"},
        {"name": "X", "rating": 4.8, "review_count": 30, "id": "i", "url": "u"},
    )])
    r = build_master_table(out)[0]
    assert r["accredited_but_low_rated"] == 0


def test_contact_readiness_labels_and_scores():
    def readiness(phone="", contact="", email=""):
        out = MatchOutcome(bbb_only=[{
            "name": "X", "rating": "B", "phone": phone,
            "principal_contact": contact, "email": email,
        }])
        r = build_master_table(out)[0]
        return r["contact_readiness"], r["contact_readiness_score"], r["has_phone"], \
            r["has_named_contact"], r["has_email"]

    label, score, has_phone, has_contact, has_email = readiness(
        phone="(773) 930-3451", contact="Glenn Wright, Manager", email="a@b.com")
    assert label == "Phone + named contact" and score == 100
    assert has_phone == 1 and has_contact == 1 and has_email == 1

    label, score, has_phone, has_contact, has_email = readiness(phone="(773) 930-3451")
    assert label == "Phone only" and score == 75
    assert has_phone == 1 and has_contact == 0 and has_email == 0

    label, score, *_ = readiness(contact="Glenn Wright, Manager")
    assert label == "Contact/email only, no phone" and score == 30

    label, score, *_ = readiness(email="a@b.com")
    assert label == "Contact/email only, no phone" and score == 30

    label, score, has_phone, has_contact, has_email = readiness()
    assert label == "No direct contact info" and score == 0
    assert has_phone == 0 and has_contact == 0 and has_email == 0


def test_lead_priority_score_penalizes_missing_phone_and_rewards_named_contact():
    """The scoring model's finalized reachability signal: a great-fit lead
    with no phone is meaningfully less useful than the same business with
    one, and a named contact on top of a phone is a modest extra bump."""
    def score(phone, contact=""):
        out = MatchOutcome(pairs=[_pair(
            {"name": "X", "rating": "B", "years_in_business": "12",
             "phone": phone, "principal_contact": contact},
            {"name": "X", "rating": 2.9, "review_count": 25, "id": "i", "url": "u"},
        )])
        return build_master_table(out)[0]["lead_priority_score"]

    no_phone = score(phone="")
    phone_only = score(phone="(773) 930-3451")
    phone_and_contact = score(phone="(773) 930-3451", contact="Glenn Wright, Manager")

    assert no_phone < phone_only, "no phone should pull the score down, not just leave it be"
    assert phone_only == pytest.approx(no_phone * 2, abs=0.2), "the cut for no phone is a straight halving"
    assert phone_and_contact > phone_only, "a named contact on top of a phone should still add a little"


def test_reachability_falls_back_to_angi_phone_when_there_is_no_bbb_phone():
    """2026-09-17, Nick's call: an angi_only row (no BBB match at all, see
    bbb_scraper/angi/enrich.py) still has a real, dialable phone number --
    penalizing it the same way a business with NO phone anywhere gets
    penalized would be wrong, not conservative."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "A+"}])  # no bbb_phone at all
    row = build_master_table(out)[0]
    no_phone_anywhere = row["lead_priority_score"]
    assert row["has_phone"] == 0
    assert row["contact_readiness"] == "No direct contact info"

    row["angi_phone"] = "7044914939"
    row = recompute_intel(row)
    assert row["has_phone"] == 1
    assert row["contact_readiness"] == "Phone only"
    # Same 2x relationship as the BBB-phone case above -- reachability
    # doesn't care which source the phone came from.
    assert row["lead_priority_score"] == pytest.approx(no_phone_anywhere * 2, abs=0.2)


def test_recompute_intel_fills_in_columns_a_stale_master_row_never_had():
    """Regression: a master-table CSV written before a scoring change has
    no idea the new _INTEL columns exist -- recompute_intel (used by
    publish_master_rows) has to add them, not just refresh the ones already
    there. 2026-09-11: has_phone/contact_readiness/etc. were added to
    _INTEL but a previously-published dataset came from a master CSV
    written before that change, so every new column read back as None."""
    stale_row = {
        "match_status": "bbb_only", "bbb_name": "Goode Plumbing", "bbb_rating": "NR",
        "bbb_phone": "(773) 930-3451", "bbb_principal_contact": "",
        # an old, pre-formula-change value that must NOT survive untouched
        "lead_priority_score": 999,
    }
    fresh = recompute_intel(stale_row)
    assert fresh["has_phone"] == 1
    assert fresh["contact_readiness"] == "Phone only"
    assert fresh["lead_priority_score"] != 999
    assert stale_row["lead_priority_score"] == 999  # input untouched


def test_lead_priority_peaks_in_the_salvageable_middle():
    """A firm selling review help wants fixable problems at payable
    businesses -- not the already-fine, not the beyond-help."""
    def score(bbb_rating, yelp_rating, yelp_reviews, years="12"):
        out = MatchOutcome(pairs=[_pair(
            {"name": "X", "rating": bbb_rating, "years_in_business": years, "accredited": "True"},
            {"name": "X", "rating": yelp_rating, "review_count": yelp_reviews, "id": "i", "url": "u"},
        )])
        return build_master_table(out)[0]["lead_priority_score"]

    great = score("A+", 4.6, 220)      # already fine
    sweet = score("B", 2.9, 25)        # struggling, fixable, established
    dumpster = score("F", 1.0, 400)    # beyond help, probably has an agency already

    assert sweet > great
    assert sweet > dumpster


# --- BBB complaints as a base lead_priority_score signal (2026-09-15) ------
# Real-data audit finding: 31.3% of every business scraped so far has an
# NR (Not Rated) BBB grade and no Yelp/Angi match -- and lead_priority_score
# returned None for every single one of them, even though 96.9% of those
# rows DO have a real, independently-tracked bbb_complaints_total sitting
# right there (it just wasn't wired in as a real base signal, only a
# conditional bonus on top of an already-nonzero score). See
# _bbb_complaints_signal's own docstring for the full finding.

def test_nr_graded_business_with_complaint_history_gets_a_real_score_not_none():
    """The actual bug being fixed: before this, an NR-graded, Yelp/Angi-
    unmatched row with a real complaint history still scored None -- exactly
    as unscoreable as one with zero data at all, which is wrong."""
    out = MatchOutcome(bbb_only=[{
        "name": "Never Rated Plumbing", "rating": "NR", "phone": "3055550100",
        "reviews_complaints": '{"complaints_total": 4}',
    }])
    row = build_master_table(out)[0]
    assert row["lead_priority_score"] is not None


def test_still_none_when_truly_no_signal_at_all():
    """The safety property this fix must not break: a row with NR grade,
    no complaints data, and no Yelp/Angi match still correctly has nothing
    to say -- None, not a fabricated score."""
    out = MatchOutcome(bbb_only=[{"name": "Total Mystery Co", "rating": "NR"}])
    row = build_master_table(out)[0]
    assert row["lead_priority_score"] is None


def test_bbb_complaints_signal_peaks_for_a_moderate_real_count():
    """Same "salvageable middle" shape as every other signal -- a handful of
    real complaints outscores both zero (nothing to fix) and a heavy volume
    (likely beyond a reputation nudge), all else being equal (no grade, no
    Yelp/Angi, so this signal is the only thing driving the score)."""
    def score(complaints_total):
        out = MatchOutcome(bbb_only=[{
            "name": "X", "rating": "NR", "phone": "3055550100",
            "reviews_complaints": f'{{"complaints_total": {complaints_total}}}',
        }])
        return build_master_table(out)[0]["lead_priority_score"]

    zero = score(0)
    moderate = score(5)
    heavy = score(50)
    assert moderate > zero
    assert moderate > heavy


def test_complaints_are_not_double_counted_once_another_signal_is_present():
    """2026-09-16 regression: _bbb_complaints_signal feeds `signals` (the
    averaged base score) -- there must not ALSO be a separate flat bonus
    for the same complaint count once a grade (or any other signal) is
    present too, or this one input counts twice while every other signal
    here only ever counts once. Real bug, found live: a grade=B row jumped
    47.5 -> 74.5 (+27) going from 0 to 5 complaints, only 19 of which was
    the legitimate base-signal band shift (30 -> 68); the other 8 was a
    leftover bonus from before complaints were a base signal at all."""
    def score(complaints_total):
        out = MatchOutcome(bbb_only=[{
            "name": "X", "rating": "B", "phone": "3055550100",
            "reviews_complaints": f'{{"complaints_total": {complaints_total}}}',
        }])
        return build_master_table(out)[0]["lead_priority_score"]

    zero = score(0)
    moderate = score(5)
    # The only two signals present are the BBB grade band (65, fixed) and
    # _bbb_complaints_signal (30 for zero complaints, 68 for a moderate
    # count) -- so the jump between them must equal exactly what averaging
    # those two produces, not that plus a leftover flat bonus on top.
    assert moderate - zero == round((68 - 30) / 2, 1)


def test_website_dead_flag_reads_the_webcheck_column():
    """website_dead_flag surfaces whatever bbb_scraper.webcheck already
    decided (see its own tests for the actual liveness logic) -- this only
    checks the plumbing: the flag reads bbb_website_dead correctly."""
    out = MatchOutcome(bbb_only=[
        {"name": "Dead Site Co", "website_dead": True, "website_status": "dead_404"},
    ])
    row = build_master_table(out)[0]
    assert row["bbb_website_dead"] == True
    assert row["bbb_website_status"] == "dead_404"
    assert row["website_dead_flag"] == 1


def test_website_dead_flag_defaults_to_0_when_never_checked():
    """A record that never went through check_dead_websites.py has no
    website_dead field at all -- must read as "not flagged", never crash
    and never default to asserting a problem it has no evidence for."""
    out = MatchOutcome(bbb_only=[{"name": "Never Checked Co"}])
    row = build_master_table(out)[0]
    assert row["website_dead_flag"] == 0


def test_website_dead_flag_off_when_site_is_fine():
    out = MatchOutcome(bbb_only=[
        {"name": "Fine Co", "website_dead": False, "website_status": "ok"},
    ])
    row = build_master_table(out)[0]
    assert row["website_dead_flag"] == 0


# --- Angi (bolt-on enrichment, see bbb_scraper/angi/enrich.py) --------------
# These inject angi_* fields directly and call recompute_intel, the same way
# enrich_with_angi does internally -- see tests/angi/test_enrich.py for the
# phone-matching step itself.

def test_angi_corporate_account_reads_the_flag_and_defaults_off():
    """Angi's own explicit franchise/corporate-account flag, wired into
    ANGI_FIELDS 2026-09-15 (bbb_scraper/curate.py's chain/large-firm
    filtering reads this column) -- checked against real data first: 23/874
    real Angi-matched businesses across every batch scraped so far carry
    this flag, and every sampled name is a recognizable national brand
    (Groundworks, Erie Home, American Standard, Power Home Remodeling)."""
    out = MatchOutcome(bbb_only=[{"name": "Independent Co", "rating": "B"}])
    row = build_master_table(out)[0]
    assert row["angi_corporate_account"] == 0  # nothing angi_* set yet, defaults off

    row["angi_is_corporate_account"] = "True"
    row = recompute_intel(row)
    assert row["angi_corporate_account"] == 1


def test_on_angi_reflects_whether_an_angi_name_is_present():
    """2026-09-17, real bug: this used to check angi_phone specifically,
    which undercounted real angi_only rows whose source Angi listing had
    no phone captured (confirmed live: 86/557 in one real batch) --
    angi_name is set on every genuine match, phone or not, so that's the
    real signal now."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    assert row["on_angi"] == 0  # nothing angi_* set yet

    row["angi_name"] = "Co on Angi"
    row = recompute_intel(row)
    assert row["on_angi"] == 1

    # A real angi_only row with no captured phone must still read as on_angi.
    row["angi_phone"] = ""
    row = recompute_intel(row)
    assert row["on_angi"] == 1


def test_unmatched_row_lead_priority_score_is_unaffected_by_angi_existing():
    """The whole point of averaging over *present* signals only: adding
    Angi as a signal source must not move any row that isn't matched to
    Angi. (2026-09-16: rewritten against lead_priority_score now that it's
    the one score -- reputation_score used to carry this same property.)"""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B"}])
    before = build_master_table(out)[0]["lead_priority_score"]
    row = recompute_intel(build_master_table(out)[0])  # angi_* still blank
    assert row["lead_priority_score"] == before


def test_angi_rating_in_sweet_spot_pulls_lead_priority_score_up():
    """A+ alone bands low (22 -- "already fine", no pitch needed). A
    genuinely fixable Angi rating (2.5 stars, inside the salvageable-
    middle band) should pull the score up toward that band's peak (82),
    not down -- unlike the old reputation_score, "worse" isn't simply
    "better lead" here, so this checks the real shape instead of raw
    monotonic weakness."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "A+", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    clean_score = row["lead_priority_score"]  # A+ alone -> 22, the "too good" band

    row["angi_phone"] = "3055550100"
    row["angi_overall_rating"] = "2.5"
    row["angi_review_count"] = "40"
    row = recompute_intel(row)
    assert row["lead_priority_score"] > clean_score


def test_source_weighting_gives_bbb_one_vote_not_one_per_submetric():
    """2026-09-17, Nick's call after a real sample batch made the old
    imbalance concrete (see _SOURCE_WEIGHTS' own comment): BBB used to get
    3 votes in a flat average (grade/review-average/complaints) against
    Yelp's or Angi's 2 (rating/volume) or sentiment's 1, just because of
    how many sub-metrics BBB happens to expose -- not by design. Now every
    source blends to ONE value first, then _SOURCE_WEIGHTS decides the mix
    (Yelp/Angi/sentiment weighted above BBB). This business's own 3 BBB
    sub-metrics (grade=22, review-avg=82, complaints=68) would have
    averaged to a high-ish 46.0 combined with one low Yelp signal (12)
    under the old flat-average formula ((22+82+68+12)/4) -- pinning that
    the real, current result is meaningfully different (BBB's 3 numbers
    collapse to one 57.3 vote, then Yelp's single 12 actually outweighs it
    per-source, 0.30 vs 0.20), not a coincidence of similar-looking inputs.
    """
    out = MatchOutcome(bbb_only=[{
        # complaints_total=3, not 5+ -- stays in the same 1-10 signal band
        # (68) without also tripping reputation_divergence_flag's own
        # separate >=5 threshold, which would add a +12 bonus this test
        # isn't trying to exercise.
        "name": "Co", "rating": "A+", "phone": "3055550100",
        "reviews_complaints": '{"reviews_total": 5, "average_rating": 3.0, "complaints_total": 3}',
    }])
    row = build_master_table(out)[0]
    bbb_blended = (22 + 82 + 68) / 3  # grade + review-avg + complaints, one BBB vote
    assert row["lead_priority_score"] == round(bbb_blended, 1)  # BBB-only: its own blended value, unscaled

    row["match_status"] = "matched"
    row["yelp_review_count"] = "200"  # volume-only signal (151+ reviews -> 12); no yelp_rating set, so no rating signal
    row = recompute_intel(row)
    expected = (bbb_blended * _SOURCE_WEIGHTS["bbb"] + 12 * _SOURCE_WEIGHTS["yelp"]) / (
        _SOURCE_WEIGHTS["bbb"] + _SOURCE_WEIGHTS["yelp"]
    )
    assert row["lead_priority_score"] == round(expected, 1)
    old_flat_average = (22 + 82 + 68 + 12) / 4  # what this would have been pre-2026-09-17
    assert row["lead_priority_score"] < old_flat_average


def test_yelp_rating_falls_back_to_mapquest_when_no_official_match():
    """2026-09-17, Nick's call: MapQuest surfaces real Yelp-sourced rating
    data through its own separate name+phone-in-city match, a second real
    door into the same Yelp data the official Fusion API match can miss --
    confirmed real and common (24 MapQuest-only Yelp matches vs. 18
    official ones in one real Charlotte test), not a rare edge case."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "A+", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    assert row["lead_priority_score"] == 22.0  # BBB grade alone -- no Yelp signal yet
    assert row["present_yelp"] == 0

    row["mapquest_rating_provider"] = "YELP"
    row["mapquest_rating_value"] = "1.0"  # rating band 45 (<=1.5)
    row["mapquest_review_count"] = "40"  # volume band 72 (1-60)
    row = recompute_intel(row)
    assert row["present_yelp"] == 1
    yelp_blended = (45 + 72) / 2
    bbb_blended = 22.0
    expected = (bbb_blended * _SOURCE_WEIGHTS["bbb"] + yelp_blended * _SOURCE_WEIGHTS["yelp"]) / (
        _SOURCE_WEIGHTS["bbb"] + _SOURCE_WEIGHTS["yelp"]
    )
    # + reputation_divergence_flag's own +12: a clean A+ BBB grade but a
    # real, MapQuest-confirmed 1.0-star Yelp rating is exactly the "looks
    # fine on paper, isn't" case that flag exists for -- and it correctly
    # fires here BECAUSE _yelp_rating now sees the MapQuest fallback too.
    assert row["reputation_divergence_flag"] == 1
    assert row["lead_priority_score"] == round(expected, 1) + 12


def test_official_yelp_match_wins_over_mapquest_when_both_present():
    """MapQuest is a fallback for when the official match is MISSING, not
    an override -- a real official Fusion API match's own numbers should
    never be replaced by MapQuest's independently-matched ones, even if
    they'd disagree."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "A+", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    row["match_status"] = "matched"
    row["yelp_rating"] = "4.5"  # official: high rating, low volume band (8)
    row["yelp_review_count"] = "200"
    row["mapquest_rating_provider"] = "YELP"
    row["mapquest_rating_value"] = "1.0"  # would band to 45 if it won instead
    row["mapquest_review_count"] = "40"
    row = recompute_intel(row)
    yelp_blended = (8 + 12) / 2  # official rating (4.5 -> 8) + official volume (200 -> 12), NOT MapQuest's
    bbb_blended = 22.0
    expected = (bbb_blended * _SOURCE_WEIGHTS["bbb"] + yelp_blended * _SOURCE_WEIGHTS["yelp"]) / (
        _SOURCE_WEIGHTS["bbb"] + _SOURCE_WEIGHTS["yelp"]
    )
    assert row["lead_priority_score"] == round(expected, 1)


def test_mapquest_rating_only_counts_when_provider_is_confirmed_yelp():
    """A non-Yelp (or blank) mapquest_rating_provider must never be treated
    as Yelp data -- _has_mapquest_yelp_data checks the real field, doesn't
    assume every MapQuest match is Yelp-sourced."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "A+", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    row["mapquest_rating_provider"] = "TRIPADVISOR"  # a real, just non-Yelp value the field can hold
    row["mapquest_rating_value"] = "1.0"
    row["mapquest_review_count"] = "40"
    row = recompute_intel(row)
    assert row["present_yelp"] == 0
    assert row["lead_priority_score"] == 22.0  # unchanged -- BBB grade alone


def test_mapquest_yelp_rating_still_needs_enough_reviews_to_count():
    """Same _MIN_REVIEWS_FOR_RATING gate as the official Yelp/Angi ratings
    -- a MapQuest-confirmed Yelp rating backed by too few reviews doesn't
    count as a rating signal (though the volume signal still applies)."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "A+", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    row["mapquest_rating_provider"] = "YELP"
    row["mapquest_rating_value"] = "1.0"  # would band to 45 if it counted
    row["mapquest_review_count"] = "2"  # too few for the rating gate, but volume band still applies (72)
    row = recompute_intel(row)
    expected = (22 * _SOURCE_WEIGHTS["bbb"] + 72 * _SOURCE_WEIGHTS["yelp"]) / (
        _SOURCE_WEIGHTS["bbb"] + _SOURCE_WEIGHTS["yelp"]
    )
    assert row["lead_priority_score"] == round(expected, 1)


def test_angi_rating_signal_still_needs_enough_reviews_to_count():
    """Same _MIN_REVIEWS_FOR_RATING gate as Yelp for the RATING signal
    specifically -- but unlike the old reputation_score, Angi's volume
    signal only needs a phone match (see _lead_priority_score), so it
    still contributes even at 1 review; only the rating band must be
    excluded. Pinned with exact arithmetic so a broken gate (the 45 band
    sneaking in) would change the result, not just possibly move it."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "A+", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    assert row["lead_priority_score"] == 22.0  # BBB grade signal alone

    row["angi_phone"] = "3055550100"
    row["angi_overall_rating"] = "1.0"  # would band to 45 if it counted
    row["angi_review_count"] = "1"  # too few for the rating gate
    row = recompute_intel(row)
    # Only the volume signal (band 72 for 1 review) joins the BBB grade
    # signal (22) -- the rating band (45) is excluded by the gate. Combined
    # via _SOURCE_WEIGHTS (2026-09-17, Nick's call: Yelp/Angi/sentiment
    # weighted above BBB), renormalized over the 2 sources present here.
    expected = (22 * _SOURCE_WEIGHTS["bbb"] + 72 * _SOURCE_WEIGHTS["angi"]) / (
        _SOURCE_WEIGHTS["bbb"] + _SOURCE_WEIGHTS["angi"]
    )
    assert row["lead_priority_score"] == round(expected, 1)


def test_reputation_divergence_flag_fires_on_low_angi_rating_alone():
    """Clean BBB grade + a bad Angi rating -- looks fine on paper, isn't."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "A", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    assert row["reputation_divergence_flag"] == 0

    row["angi_phone"] = "3055550100"
    row["angi_overall_rating"] = "2.0"
    row["angi_review_count"] = "30"
    row = recompute_intel(row)
    assert row["reputation_divergence_flag"] == 1


def test_lead_priority_score_responds_to_angi_signal():
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    baseline = row["lead_priority_score"]

    row["angi_phone"] = "3055550100"
    row["angi_overall_rating"] = "2.5"  # in the "82" band -- a strong lead signal
    row["angi_review_count"] = "20"
    row = recompute_intel(row)
    assert row["lead_priority_score"] != baseline


# --- Facebook enrichment (bbb_scraper.facebook, 2026-09-18) -----------------
# Same post-hoc-then-recompute pattern as Angi above -- facebook_* columns
# land via bbb_scraper.facebook.enrich.enrich_with_facebook, then
# recompute_intel picks them up, same as every other bolt-on source.

def test_lead_priority_score_ignores_facebook_data_when_status_is_not_ok():
    """A "no_facebook_link"/"unavailable"/"check_failed" row must not be
    scored as if it had a real 0% rating -- the explicit status=="ok" guard
    in _lead_priority_score is what prevents that, not just recommend_
    percentage/review_count happening to be None."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    baseline = row["lead_priority_score"]

    row["facebook_status"] = "no_facebook_link"
    row["facebook_recommend_percentage"] = 0  # must never be read while status isn't "ok"
    row["facebook_review_count"] = 0
    row = recompute_intel(row)
    assert row["lead_priority_score"] == baseline


def test_lead_priority_score_responds_to_facebook_signal():
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    baseline = row["lead_priority_score"]

    row["facebook_status"] = "ok"
    row["facebook_recommend_percentage"] = 65  # 65/20=3.25 -> the "82" salvageable-middle band
    row["facebook_review_count"] = 20
    row = recompute_intel(row)
    assert row["lead_priority_score"] != baseline


def test_facebook_signal_is_weighted_lower_than_yelp_for_an_identical_band():
    """Nick's explicit call: Facebook "wont hold a ton of weight." Confirm
    the same salvageable-middle band moves the score less coming from
    Facebook than from Yelp, on an otherwise-identical row."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B", "phone": "3055550100"}])
    base_row = build_master_table(out)[0]
    baseline = base_row["lead_priority_score"]

    facebook_row = dict(base_row)
    facebook_row["facebook_status"] = "ok"
    facebook_row["facebook_recommend_percentage"] = 65  # /20 = 3.25 -> the "82" band, same as Yelp case below
    facebook_row["facebook_review_count"] = 20  # same volume band (<=60 -> 72) as the Yelp case below
    facebook_row = recompute_intel(facebook_row)

    yelp_row = dict(base_row)
    yelp_row["yelp_rating"] = 3.25  # -> the same "82" band via _rating_band, same numeric input as facebook above
    yelp_row["yelp_review_count"] = 20  # required for _yelp_rating to trust the rating at all (>= _MIN_REVIEWS_FOR_RATING)
    yelp_row = recompute_intel(yelp_row)

    facebook_delta = facebook_row["lead_priority_score"] - baseline
    yelp_delta = yelp_row["lead_priority_score"] - baseline
    assert 0 < facebook_delta < yelp_delta


# --- Local review-sentiment analysis (bbb_scraper.sentiment, 2026-09-15) ----
# These inject review_sentiment_*/most_recent_*/avg_review_gap_days fields
# directly and call recompute_intel, same post-hoc-then-recompute pattern
# as Angi above -- see bbb_scraper/sentiment/analyze.py for how those
# columns actually get produced, and _review_sentiment_signal's own
# docstring for the real-data audit these thresholds were tuned against.

def _row_with(**sentiment_fields) -> dict:
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B", "phone": "3055550100"}])
    row = build_master_table(out)[0]
    row.update(sentiment_fields)
    return recompute_intel(row)


def test_review_sentiment_signal_none_until_anything_has_been_analyzed():
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B"}])
    row = build_master_table(out)[0]
    assert row["review_sentiment_signal"] is None


def test_review_sentiment_signal_low_but_not_zero_with_no_negative_found():
    row = _row_with(review_sentiment_analyzed_count=5, review_sentiment_negative_count=0)
    assert row["review_sentiment_signal"] == 18


def test_review_sentiment_signal_peaks_for_the_salvageable_middle():
    """Same "salvageable middle" shape as every other signal here: some
    real negative sentiment outscores both none and all-negative."""
    none_negative = _row_with(review_sentiment_analyzed_count=5, review_sentiment_negative_count=0)
    some_negative = _row_with(review_sentiment_analyzed_count=5, review_sentiment_negative_count=2,
                               most_recent_negative_review_date="2020-01-01")  # old, so recency doesn't inflate this
    all_negative = _row_with(review_sentiment_analyzed_count=5, review_sentiment_negative_count=5,
                              most_recent_negative_review_date="2020-01-01")

    assert some_negative["review_sentiment_signal"] > none_negative["review_sentiment_signal"]
    assert some_negative["review_sentiment_signal"] > all_negative["review_sentiment_signal"]
    assert all_negative["review_sentiment_signal"] > none_negative["review_sentiment_signal"]  # still real signal, not erased


def test_review_sentiment_signal_recent_negative_scores_higher_than_old():
    """Recency is a smooth multiplier, not a hard gate -- real data audit
    (see the function's own docstring) found most real negative sentiment
    is actually 1-2+ years old, so old-but-real friction still scores
    meaningfully, just lower than an active, current problem."""
    recent = _row_with(review_sentiment_analyzed_count=5, review_sentiment_negative_count=1,
                        most_recent_negative_review_date=date.today().isoformat())  # noqa: DTZ011
    old = _row_with(review_sentiment_analyzed_count=5, review_sentiment_negative_count=1,
                     most_recent_negative_review_date="2015-01-01")

    assert recent["review_sentiment_signal"] > old["review_sentiment_signal"]
    assert old["review_sentiment_signal"] > 0  # still real signal, not erased by age


def test_lead_priority_score_responds_to_sentiment_signal():
    out = MatchOutcome(bbb_only=[{"name": "Co", "rating": "B"}])
    row = build_master_table(out)[0]
    baseline = row["lead_priority_score"]

    row["review_sentiment_analyzed_count"] = 5
    row["review_sentiment_negative_count"] = 2
    row["most_recent_negative_review_date"] = date.today().isoformat()  # noqa: DTZ011
    row = recompute_intel(row)

    assert row["lead_priority_score"] != baseline


def test_review_gap_flag_off_without_a_computable_gap():
    row = _row_with(most_recent_review_date="2024-01-01")  # no avg_review_gap_days at all
    assert row["review_gap_flag"] == 0


def test_review_gap_flag_fires_only_for_a_real_outlier_vs_its_own_history():
    """Relative to the business's OWN average gap, not a fixed day count --
    real data spans 3-3890 days between reviews (median 384), so a
    universal threshold wouldn't mean anything (see the function's own
    docstring)."""
    normal = _row_with(avg_review_gap_days=60, most_recent_review_date=_days_ago(90))
    quiet = _row_with(avg_review_gap_days=60, most_recent_review_date=_days_ago(400))  # ~6.5x its own average

    assert normal["review_gap_flag"] == 0
    assert quiet["review_gap_flag"] == 1


# --- Regression: every captured field must actually reach the master table --

def test_bbb_fields_carries_lat_lon_through_to_the_master_row():
    """2026-09-16, found in a full-project audit: bbb_scraper.etl.transform
    has always captured real lat/lon, and publish_site_data.py's
    _PUBLIC_FIELDS has always tried to publish them (reading bbb_lat/
    bbb_lon), but BBB_FIELDS never actually carried them onto the master
    row -- so every published record's lat/lon silently came out blank no
    matter what was really scraped. Locks in the fix rather than just the
    docstring explaining it."""
    out = MatchOutcome(bbb_only=[{"name": "Co", "lat": 25.77, "lon": -80.19}])
    row = build_master_table(out)[0]
    assert row["bbb_lat"] == 25.77
    assert row["bbb_lon"] == -80.19


def test_angi_fields_stays_in_sync_with_the_real_angi_csv_shape():
    """ANGI_FIELDS is a hand-maintained subset of what
    bbb_scraper.angi.scraper.ANGI_CSV_FIELDS actually captures (which is
    itself guarded, see tests/angi/test_scraper.py::
    test_row_has_every_declared_csv_field) -- 2026-09-16's audit found 11
    real fields (street, all five rating_N_star_pct columns, is_paid_pro,
    licenses, highlights, searched_category, searched_metro) that Angi's
    scraper had captured all along but ANGI_FIELDS silently never carried
    into the master table. This doesn't demand every CSV field end up here
    forever (a future deliberate exclusion is fine) -- it demands that
    dropping one be a conscious edit to this test, not silent drift."""
    from bbb_scraper.angi.scraper import ANGI_CSV_FIELDS
    from bbb_scraper.match.merge import ANGI_FIELDS
    assert set(ANGI_FIELDS) == set(ANGI_CSV_FIELDS)
