"""find_business -- phone-first matching, with the real "phone match has
zero reviews, a same-named sibling has them" case this project actually
hit live (see matcher.py's own module docstring)."""
from __future__ import annotations

import json
from pathlib import Path

from bbb_scraper.mapquest.client import _map_node
from bbb_scraper.mapquest.matcher import find_business
from bbb_scraper.mapquest.models import MapQuestMatch, MapQuestReview

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load_candidates(fixture_name: str) -> list[MapQuestMatch]:
    data = json.loads((FIXTURES / fixture_name).read_text(encoding="utf-8"))
    return [_map_node(n) for n in data["data"]["search"]["nodes"]]


# --- the real sibling-override case ----------------------------------------

def test_phone_match_with_zero_reviews_defers_to_a_review_carrying_sibling():
    """The actual real-world case this logic exists for: BBB's own listed
    phone for Walsh Crawlspace correctly resolves to MapQuest's
    "Walsh Crawlspace & Structural Repair LLC" node -- which has zero
    reviews. A near-identically-named sibling a few addresses over carries
    all 5 real reviews. The whole point of this module is harvesting
    review content, so that sibling should win."""
    candidates = _load_candidates("mapquest_search_walsh.json")

    match = find_business(candidates, name="Walsh Crawlspace & Structural Repair, LLC", phone="(704) 302-7715")

    assert match is not None
    assert match.mapquest_id == "423145406"
    assert len(match.reviews) == 5


def test_phone_match_with_reviews_is_returned_directly_no_sibling_check():
    """The common, simple case -- confirmed live on a second, unrelated
    real business (Platinum Exteriors): when the phone match itself
    already carries reviews, there's no reason to look any further."""
    candidates = _load_candidates("mapquest_search_platinum_exteriors.json")

    match = find_business(candidates, name="Platinum Exteriors Inc", phone="(928) 301-5529")

    assert match is not None
    assert match.mapquest_id == "424875185"
    assert match.review_count == 35


def test_sibling_search_prefers_the_review_carrying_candidate_over_a_closer_name_match(monkeypatch):
    """Real gap caught on re-review, not exercised by the 2-candidate Walsh
    fixture above: with 3+ candidates, the single closest name match to the
    phone-matched (reviewless) business might itself have zero reviews,
    while a less-close (but still above threshold) sibling is the one
    actually carrying them. The override must search among review-carrying
    candidates first, not just check whether the single best name match
    overall happens to have reviews. name_similarity is mocked here so the
    exact score gap is guaranteed rather than hoping two real strings land
    on the right side of the threshold by chance."""
    phone_match = MapQuestMatch(mapquest_id="1", name="Acme Plumbing LLC", phone="+15551234567", review_count=0)
    closer_name_no_reviews = MapQuestMatch(
        mapquest_id="2", name="Acme Plumbing LLC Exact", phone="+15550000000", review_count=0,
    )
    less_close_with_reviews = MapQuestMatch(
        mapquest_id="3", name="Acme Plumbing Co", phone="+15559999999",
        review_count=12, reviews=[MapQuestReview(text="Great service")],
    )
    scores = {"Acme Plumbing LLC Exact": 0.99, "Acme Plumbing Co": 0.92}
    monkeypatch.setattr("bbb_scraper.mapquest.matcher.name_similarity", lambda _target, cand: scores.get(cand, 0.0))

    match = find_business(
        [phone_match, closer_name_no_reviews, less_close_with_reviews],
        name="Acme Plumbing LLC", phone="(555) 123-4567",
    )

    assert match is less_close_with_reviews


def test_phone_match_with_zero_reviews_and_no_close_sibling_is_still_returned():
    """The override in the case above is conservative -- it only fires for
    a near-exact name match. A phone match with zero reviews and nothing
    else close enough in name is still the right answer to return (reviews
    or not), not None."""
    reviewless = MapQuestMatch(mapquest_id="1", name="Acme Plumbing LLC", phone="+15551234567", review_count=0)
    unrelated = MapQuestMatch(
        mapquest_id="2", name="Totally Different Business Name", phone="+15559999999",
        review_count=3, reviews=[MapQuestReview(text="hi")],
    )

    match = find_business([reviewless, unrelated], name="Acme Plumbing LLC", phone="(555) 123-4567")

    assert match is reviewless  # the unrelated candidate must never be picked just because it has reviews


def test_phone_with_no_match_at_all_returns_none_not_a_guess():
    candidates = _load_candidates("mapquest_search_walsh.json")

    match = find_business(candidates, name="Walsh Crawlspace & Structural Repair, LLC", phone="(555) 000-0000")

    assert match is None


def test_no_phone_falls_back_to_high_confidence_name_match():
    candidates = _load_candidates("mapquest_search_platinum_exteriors.json")

    match = find_business(candidates, name="Platinum Exteriors Inc")

    assert match is not None
    assert match.name in ("Platinum Exteriors", "Platinum Exteriors Inc")


def test_no_phone_and_no_close_name_match_returns_none():
    weak = MapQuestMatch(mapquest_id="1", name="Something Entirely Unrelated Co")
    match = find_business([weak], name="Acme Plumbing LLC")
    assert match is None


def test_no_candidates_returns_none():
    assert find_business([], name="Anything") is None
