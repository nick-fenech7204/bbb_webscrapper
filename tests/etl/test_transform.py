from bbb_scraper.etl.transform import normalize_phone, normalize_whitespace, transform_summary
from bbb_scraper.parsing.models import BusinessSummary


def test_normalize_phone_formats_10_digit_us_number():
    assert normalize_phone("512.555.0134") == "(512) 555-0134"
    assert normalize_phone("+1 (512) 555-0134") == "(512) 555-0134"


def test_normalize_phone_handles_missing_and_short_numbers():
    assert normalize_phone(None) is None
    assert normalize_phone("") is None
    assert normalize_phone("555") == "555"


def test_normalize_whitespace_collapses_and_strips():
    assert normalize_whitespace("  123   Main   St  ") == "123 Main St"
    assert normalize_whitespace(None) is None
    assert normalize_whitespace("   ") is None


def test_transform_summary_generates_stable_id_from_bbb_id():
    summary = BusinessSummary(bbb_id="0865-90012345", name="Acme Plumbing Co")
    record = transform_summary(summary)
    assert record["id"] == "bbb:0865-90012345"
    assert record["name"] == "Acme Plumbing Co"
    assert record["record_type"] == "summary"


def test_transform_summary_generates_deterministic_id_without_bbb_id():
    a = BusinessSummary(name="Acme Plumbing Co", address="123 Main St", phone="512-555-0134")
    b = BusinessSummary(name="Acme Plumbing Co", address="123 Main St", phone="512-555-0134")
    assert transform_summary(a)["id"] == transform_summary(b)["id"]
