"""publish_site_data record-shaping (pure functions only -- no file I/O)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from publish_site_data import (
    _null_intel,
    select_public_fields,
    select_public_fields_from_master,
)


def test_bbb_only_record_gets_last_updated_and_null_intel():
    rec = select_public_fields({
        "name": "Ramirez Motors", "phone": "(305) 642-4409", "rating": "A+",
        "categories": ["Used Car Dealers"], "scraped_at": "2026-09-03T15:43:00+00:00",
    })
    assert rec["name"] == "Ramirez Motors"
    assert rec["last_updated"] == "2026-09-03"
    assert rec["on_yelp"] is False
    assert rec["yelp_rating"] is None
    assert rec["review_need_score"] is None
    assert rec["categories"] == ["Used Car Dealers"]


def test_matched_master_row_carries_yelp_and_intel():
    row = {
        "match_status": "matched",
        "bbb_name": "Italy Blue Auto Sales LLC", "bbb_rating": "B-",
        "bbb_scraped_at": "2026-09-03T12:00:00+00:00",
        "bbb_categories": '["Used Car Dealers"]',
        "yelp_name": "Italy Blue Auto Sales", "yelp_rating": "1.5",
        "yelp_review_count": "8", "yelp_url": "https://www.yelp.com/biz/italy-blue",
        "review_need_score": "79.9", "lead_priority_score": "84.9",
        "reputation_divergence_flag": "0", "rating_gap_bbb_minus_yelp": "1.58",
    }
    rec = select_public_fields_from_master(row)
    assert rec["name"] == "Italy Blue Auto Sales LLC"
    assert rec["last_updated"] == "2026-09-03"
    assert rec["on_yelp"] is True
    assert rec["yelp_name"] == "Italy Blue Auto Sales"
    assert rec["yelp_rating"] == 1.5
    assert rec["yelp_review_count"] == 8  # int, not 8.0
    assert rec["yelp_url"].endswith("italy-blue")
    assert rec["review_need_score"] == 79.9
    assert rec["rating_gap_bbb_minus_yelp"] == 1.58
    assert rec["categories"] == ["Used Car Dealers"]
    # no raw yelp phone/id/price leak through
    assert "yelp_phone" not in rec and "yelp_id" not in rec


def test_bbb_only_master_row_has_blank_yelp():
    row = {
        "match_status": "bbb_only", "bbb_name": "Nowhere Motors", "bbb_rating": "A",
        "bbb_scraped_at": "2026-09-04T00:00:00+00:00",
        "yelp_name": "", "yelp_rating": "", "review_need_score": "",
    }
    rec = select_public_fields_from_master(row)
    assert rec["on_yelp"] is False
    assert rec["yelp_rating"] is None
    assert rec["yelp_name"] == ""
    assert rec["review_need_score"] is None
    assert rec["last_updated"] == "2026-09-04"


def test_null_intel_covers_every_site_field():
    ni = _null_intel()
    assert ni["on_yelp"] is False
    assert set(ni) >= {"yelp_name", "yelp_rating", "yelp_review_count", "yelp_url",
                       "review_need_score", "lead_priority_score"}
