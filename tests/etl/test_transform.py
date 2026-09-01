from bbb_scraper.etl.transform import normalize_phone, normalize_whitespace, transform_detail, transform_summary
from bbb_scraper.parsing.models import BusinessDetail, BusinessSummary


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


def test_transform_summary_keeps_different_branches_of_same_company_distinct():
    """Same company, same business_id/bbb_office_id, different branch
    address -> must produce different dedupe keys. This is the actual
    end-to-end check for the bug described in BusinessSummary.bbb_id's
    docstring: two branches must never collapse to one record downstream.
    """
    branch_a = BusinessSummary(
        bbb_id="0292_3089_1134", business_id="3089", bbb_office_id="0292",
        name="Barnes, Dennig & Company, LTD.", address="150 E 4th St Ste 300", city="Cincinnati",
    )
    branch_b = BusinessSummary(
        bbb_id="0292_3089_178405", business_id="3089", bbb_office_id="0292",
        name="Barnes, Dennig & Company, LTD.", address="2617 Legends Way", city="Crestview Hills",
    )
    record_a = transform_summary(branch_a)
    record_b = transform_summary(branch_b)
    assert record_a["id"] != record_b["id"]
    assert record_a["business_id"] == record_b["business_id"] == "3089"


def test_transform_detail_keeps_nested_fields_native_not_prestringified():
    """transform.py's job is one-dict-per-record, not all-scalar-values --
    contacts/socials/reviews_complaints stay as real list/dict objects here.
    Serializing them for a flat destination (CSV/SQL cell) is each sink's
    own job, done right before writing -- see pipeline/sinks/csv_sink.py.
    """
    detail = BusinessDetail(
        name="Acme Plumbing Co",
        contacts=[{"name": "Jane Doe", "title": "Owner", "is_principal": True}],
        socials=[{"platform": "facebook", "url": "https://facebook.com/acme"}],
        reviews_complaints={"reviews_total": 3, "complaints_total": 1},
        categories=["Plumbers"],
    )
    record = transform_detail(detail)

    assert record["contacts"] == [{"name": "Jane Doe", "title": "Owner", "is_principal": True}]
    assert isinstance(record["contacts"], list)
    assert record["socials"] == [{"platform": "facebook", "url": "https://facebook.com/acme"}]
    assert record["reviews_complaints"] == {"reviews_total": 3, "complaints_total": 1}
    assert isinstance(record["reviews_complaints"], dict)
