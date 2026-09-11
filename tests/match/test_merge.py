import pytest

from bbb_scraper.match.matcher import MatchedPair, MatchOutcome
from bbb_scraper.match.merge import build_master_table, recompute_intel


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
    # but the BBB-driven scores are still there
    assert r["bbb_grade_num"] == 2.0
    assert r["reputation_score"] is not None
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
    assert r["reputation_score"] is not None


def test_divergence_flag_fires_on_bbb_complaints_alone():
    """No Yelp match, A+ grade, but 8 BBB complaints -> still a divergence lead."""
    out = MatchOutcome(bbb_only=[{
        "name": "Looks Fine Motors", "rating": "A+",
        "reviews_complaints": '{"reviews_total": 1, "average_rating": 1, "complaints_total": 8}',
    }])
    r = build_master_table(out)[0]
    assert r["bbb_complaints_total"] == 8
    assert r["reputation_divergence_flag"] == 1


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
