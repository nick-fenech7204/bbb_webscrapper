from bbb_scraper.parsing.models import BusinessSummary
from bbb_scraper.parsing.search_parser import parse_search_results
from bbb_scraper.reference.models import Category, parse_location


def test_parse_search_results_against_real_fixture(load_json_fixture):
    """Fixture is a real (trimmed) captured BBB /api/search response --
    see its _fixture_note. This is checking the actual confirmed mapping,
    not a placeholder guess.
    """
    data = load_json_fixture("search_listing_sample.json")
    category = Category(id="accredited-cpa", name="accredited cpa")
    location = parse_location("Saint Johns, FL")

    records = parse_search_results(data, category=category, location=location, page=1)

    assert len(records) == 3
    assert all(isinstance(r, BusinessSummary) for r in records)

    first = records[0]
    assert first.bbb_id == "0292-10142"  # "{bbbId}-{businessId}", not BBB's raw composite "id"
    assert first.name == "Munninghoff, Lange & Company"
    assert first.profile_url == (
        "https://www.bbb.org/us/ky/covington/profile/cpa/munninghoff-lange-company-0292-10142"
    )
    assert first.phone == "(859) 655-2300"
    assert first.address == "231 Scott Street Suite 3"
    assert first.city == "Covington"
    assert first.state == "KY"
    assert first.postal_code == "41011-1573"
    assert first.lat == 39.07
    assert first.lon == -84.53
    assert first.rating == "A+"
    assert first.rating_score == 100.0
    assert first.accredited is True
    assert first.categories == ["CPA", "Financial Planning Consultants"]
    assert first.bbb_office_id == "0292"
    assert first.bbb_office_name == "BBB Cincinnati"
    assert first.search_category_id == "accredited-cpa"
    assert first.search_category_name == "accredited cpa"
    assert first.search_location == "Saint Johns, FL"
    assert first.source_page == 1

    # Unmapped fields survive in raw_extra rather than being dropped.
    assert first.raw_extra["search_result_id"] == "0292_10142_2238"
    assert first.raw_extra["tobText"] == "CPA"
    assert first.raw_extra["categories_full"] == [
        {"id": "60004-000", "name": "CPA"},
        {"id": "20012-000", "name": "Financial Planning Consultants"},
    ]

    third = records[2]
    assert third.name == "Simpler Tax Relief, LLC"
    assert third.address == ""  # BBB sends "" (not null) when address is unknown


def test_parse_search_results_returns_empty_list_on_no_match():
    assert parse_search_results({}) == []
    assert parse_search_results({"results": []}) == []
