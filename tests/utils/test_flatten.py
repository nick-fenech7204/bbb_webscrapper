import json

from bbb_scraper.utils.flatten import flatten_record


def test_flatten_record_json_encodes_lists_and_dicts():
    record = {
        "id": "1",
        "categories": ["Plumbers", "HVAC"],
        "reviews_complaints": {"reviews_total": 3, "complaints_total": None},
    }
    flat = flatten_record(record)

    assert flat["id"] == "1"  # scalars pass through unchanged
    assert json.loads(flat["categories"]) == ["Plumbers", "HVAC"]
    assert json.loads(flat["reviews_complaints"]) == {"reviews_total": 3, "complaints_total": None}


def test_flatten_record_leaves_scalars_and_none_untouched():
    record = {"name": "Acme", "count": 3, "rating": None, "accredited": True}
    assert flatten_record(record) == record
