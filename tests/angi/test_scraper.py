"""business_detail_to_row -- the flattening logic scripts/scrape_angi_category.py's
CSV writer and scripts/batch_scrape_metros.py's in-batch Angi scrape both
now share (previously duplicated between the two -- see the function's own
docstring). No test existed for this before it was a real library function
sitting only inline in the CLI script."""
from __future__ import annotations

import json

from bbb_scraper.angi.models import BusinessDetail, RatingBreakdown, Review
from bbb_scraper.angi.scraper import ANGI_CSV_FIELDS, business_detail_to_row


def _detail(**overrides) -> BusinessDetail:
    defaults = {
        "profile_url": "https://www.angi.com/companylist/us/il/chicago/acme-reviews-123.htm",
        "name": "Acme Plumbing",
        "phone": "(312) 555-0100",
        "overall_rating": 4.933734939759036,
        "review_count": 83,
        "rating_breakdown": [RatingBreakdown(star=5, count=80, percentage=96.385), RatingBreakdown(star=1, count=1, percentage=1.205)],
        "categories": ["Plumbing for a Remodel or Addition - Install", "Faucets, Fixtures and Pipes - Repair or Replace"],
        "licenses": ["IL-12345"],
        "highlights": ["typeId:5=20"],
        "bonded": True, "insured": True,
        "searched_category": "Plumbers", "searched_metro": "Chicago, IL",
    }
    defaults.update(overrides)
    return BusinessDetail(**defaults)


def test_row_has_every_declared_csv_field():
    row = business_detail_to_row(_detail())
    assert set(row.keys()) == set(ANGI_CSV_FIELDS)


def test_rounds_the_raw_overall_rating_for_display():
    row = business_detail_to_row(_detail())
    assert row["overall_rating"] == 4.93  # not the raw 4.933734939759036


def test_rating_breakdown_expands_to_one_column_per_star():
    row = business_detail_to_row(_detail())
    assert row["rating_5_star_pct"] == 96.4  # rounded to 1 digit
    assert row["rating_1_star_pct"] == 1.2
    assert row["rating_4_star_pct"] is None  # no 4-star entry in the fixture -- None, not 0 or missing


def test_list_fields_join_on_semicolon_not_comma():
    # A real Angi category name can contain a literal comma of its own
    # ("Faucets, Fixtures and Pipes - Repair or Replace") -- joining on ", "
    # would make the boundary between items ambiguous.
    row = business_detail_to_row(_detail())
    assert row["categories"] == (
        "Plumbing for a Remodel or Addition - Install; "
        "Faucets, Fixtures and Pipes - Repair or Replace"
    )
    assert row["num_categories"] == 2
    assert row["licenses"] == "IL-12345"


def test_empty_list_fields_join_to_empty_string_not_none():
    row = business_detail_to_row(_detail(categories=[], licenses=[], highlights=[]))
    assert row["categories"] == ""
    assert row["num_categories"] == 0


def test_none_rating_stays_none_not_rounded_to_a_number():
    row = business_detail_to_row(_detail(overall_rating=None, rating_breakdown=[]))
    assert row["overall_rating"] is None
    assert row["rating_5_star_pct"] is None


# --- reviews (2026-09-15, real text captured for the planned local-sentiment pass) --

def test_reviews_serialize_to_a_real_json_string_cell():
    reviews = [
        Review(text="Great work!", rating=5, reviewer_name="Carl S.", date_label="April 2026", is_verified=True),
        Review(text="Not thrilled, took too long.", rating=2, reviewer_name="Katie J.", date_label="March 2026"),
    ]
    row = business_detail_to_row(_detail(reviews=reviews))
    parsed = json.loads(row["reviews"])  # must round-trip -- a downstream sentiment pass reads this directly
    assert len(parsed) == 2
    assert parsed[0]["text"] == "Great work!"
    assert parsed[0]["rating"] == 5
    assert parsed[0]["date_label"] == "April 2026"
    assert row["num_reviews_captured"] == 2


def test_no_reviews_serializes_to_an_empty_json_array_not_blank():
    row = business_detail_to_row(_detail())  # no reviews override -- default_factory=list
    assert row["reviews"] == "[]"  # always parseable, never "" or None
    assert json.loads(row["reviews"]) == []
    assert row["num_reviews_captured"] == 0


def test_review_text_with_embedded_delimiters_survives_the_json_round_trip():
    """The whole reason this is JSON and not "; "-joined like the plain
    list fields above -- a review's own text can contain a semicolon, a
    comma, even a quote, and none of that should corrupt the record."""
    tricky_text = 'Said "call back Tuesday"; never did, and the price - $1,200 - was a surprise.'
    row = business_detail_to_row(_detail(reviews=[Review(text=tricky_text, rating=1)]))
    parsed = json.loads(row["reviews"])
    assert parsed[0]["text"] == tricky_text
