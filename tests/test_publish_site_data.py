"""publish_site_data record-shaping (pure functions only -- no file I/O)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from publish_site_data import (
    _bbb_only_master,
    select_public_fields_from_master,
)


def _publish_one(bbb_record: dict) -> dict:
    return select_public_fields_from_master(_bbb_only_master([bbb_record])[0])


def test_bbb_only_record_gets_last_updated_and_bbb_scoring_but_no_yelp():
    rec = _publish_one({
        "name": "Ramirez Motors", "phone": "(305) 642-4409", "rating": "C",
        "years_in_business": "15", "accredited": "True",
        "categories": ["Used Car Dealers"], "scraped_at": "2026-09-03T15:43:00+00:00",
    })
    assert rec["name"] == "Ramirez Motors"
    assert rec["phone"] == "(305) 642-4409"
    assert rec["last_updated"] == "2026-09-03"
    assert rec["on_yelp"] is False
    assert rec["yelp_rating"] is None
    assert rec["categories"] == ["Used Car Dealers"]  # list fields survive the master round-trip
    # BBB-side intelligence is still computed
    assert rec["bbb_grade_num"] == 2.0
    assert rec["reputation_score"] is not None
    assert rec["lead_priority_score"] is not None
    assert rec["review_need_score"] is None  # Yelp-specific


def test_matched_master_row_carries_yelp_and_intel():
    from publish_site_data import publish_master_rows

    row = {
        "match_status": "matched",
        "bbb_name": "Italy Blue Auto Sales LLC", "bbb_rating": "B-",
        "bbb_scraped_at": "2026-09-03T12:00:00+00:00",
        "bbb_categories": '["Used Car Dealers"]',
        "yelp_name": "Italy Blue Auto Sales", "yelp_rating": "1.9",
        "yelp_review_count": "9", "yelp_url": "https://www.yelp.com/biz/italy-blue",
        "reputation_score": "50.1", "lead_priority_score": "81.0",
        "reputation_divergence_flag": "0", "low_review_volume_flag": "1",
    }
    rec = select_public_fields_from_master(row)
    assert rec["name"] == "Italy Blue Auto Sales LLC"
    assert rec["last_updated"] == "2026-09-03"
    assert rec["on_yelp"] is True
    assert rec["yelp_name"] == "Italy Blue Auto Sales"
    assert rec["yelp_rating"] == 1.9
    assert rec["yelp_review_count"] == 9  # int, not 9.0
    assert rec["reputation_score"] == 50.1
    assert rec["low_review_volume_flag"] == 1  # int flag, not 1.0
    assert rec["categories"] == ["Used Car Dealers"]
    assert "yelp_phone" not in rec and "yelp_id" not in rec
    assert callable(publish_master_rows)


def test_bbb_complaints_surface_from_reviews_complaints_json():
    rec = _publish_one({
        "name": "Looks Fine Motors", "rating": "A+", "scraped_at": "2026-09-04T00:00:00+00:00",
        "reviews_complaints": '{"reviews_total": 1, "average_rating": 1, "complaints_total": 8}',
    })
    assert rec["bbb_complaints_total"] == 8
    assert rec["reputation_divergence_flag"] == 1  # clean grade, 8 complaints
