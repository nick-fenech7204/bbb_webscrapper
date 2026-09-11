from bbb_scraper.match.dedupe import dedupe_by_phone


def test_records_sharing_a_phone_collapse_to_the_first_one():
    """The real case: 4 BBB branch listings for one company, one phone."""
    records = [
        {"name": "Goode Plumbing", "city": "Evanston", "phone": "(773) 930-3451"},
        {"name": "Goode Plumbing", "city": "Chicago", "phone": "(773) 930-3451"},
        {"name": "Goode Plumbing", "city": "Schiller Park", "phone": "(773) 930-3451"},
        {"name": "Goode Plumbing", "city": "Schiller Park", "phone": "(773) 930-3451"},
    ]
    out = dedupe_by_phone(records)
    assert len(out) == 1
    assert out[0]["city"] == "Evanston"  # first seen wins


def test_different_phone_formats_still_match():
    records = [
        {"name": "A", "phone": "(773) 930-3451"},
        {"name": "B", "phone": "+17739303451"},   # Yelp-style E.164
        {"name": "C", "phone": "773-930-3451"},
    ]
    out = dedupe_by_phone(records)
    assert len(out) == 1
    assert out[0]["name"] == "A"


def test_records_with_no_phone_are_never_merged_with_each_other():
    records = [
        {"name": "No Phone One", "phone": ""},
        {"name": "No Phone Two", "phone": None},
        {"name": "No Phone Three"},  # field missing entirely
    ]
    out = dedupe_by_phone(records)
    assert len(out) == 3


def test_distinct_phones_all_kept():
    records = [
        {"name": "A", "phone": "(305) 111-1111"},
        {"name": "B", "phone": "(305) 222-2222"},
    ]
    assert len(dedupe_by_phone(records)) == 2


def test_custom_phone_field_for_master_table_rows():
    rows = [
        {"bbb_name": "Goode Plumbing", "bbb_phone": "(773) 930-3451"},
        {"bbb_name": "Goode Plumbing", "bbb_phone": "(773) 930-3451"},
    ]
    out = dedupe_by_phone(rows, phone_field="bbb_phone")
    assert len(out) == 1


def test_empty_input():
    assert dedupe_by_phone([]) == []
