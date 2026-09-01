from bbb_scraper.parsing.models import BusinessSummary
from bbb_scraper.parsing.search_parser import parse_search_results
from bbb_scraper.reference.models import Category, parse_location


def test_parse_search_results_against_real_fixture(load_json_fixture):
    """Fixture is a real, full (untrimmed) captured BBB /api/search response
    -- see its _fixture_note. This is checking the actual confirmed mapping
    against real data, not a placeholder guess.
    """
    data = load_json_fixture("search_listing_sample.json")
    category = Category(id="60004-000", name="CPA")
    location = parse_location("Saint Johns, FL")

    records = parse_search_results(data, category=category, location=location, page=1)

    assert len(records) == 15
    assert all(isinstance(r, BusinessSummary) for r in records)

    first = records[0]
    assert first.bbb_id == "0673_90048562_89699"
    assert first.business_id == "90048562"
    assert first.name == "Copper Advisors, LLC"
    # This row is a non-canonical branch (Greer) -- profile_url must come from
    # localReportUrl (which carries /addressId/89699), not the shared reportUrl
    # (which points at the canonical Greenville address instead).
    assert first.profile_url == (
        "https://www.bbb.org/us/sc/greer/profile/cpa/copper-advisors-llc-0673-90048562/addressId/89699"
    )
    assert first.phone == "(864) 877-9691"
    assert first.address == "1109 W Poinsett St Ste C"
    assert first.city == "Greer"
    assert first.state == "SC"
    assert first.postal_code == "29650-1318"
    assert first.lat == 34.94174575805664
    assert first.lon == -82.24696350097656
    assert first.rating == "A+"
    assert first.rating_score == 100.0
    assert first.accredited is True
    assert first.categories == ["CPA", "Financial Planning Consultants"]
    assert first.bbb_office_id == "0673"
    assert first.bbb_office_name == "BBB of Upstate South Carolina"
    assert first.search_category_id == "60004-000"
    assert first.search_category_name == "CPA"
    assert first.search_location == "Saint Johns, FL"
    assert first.source_page == 1

    # Unmapped fields survive in raw_extra rather than being dropped.
    assert first.raw_extra["tobText"] == "CPA"
    assert first.raw_extra["categories_full"] == [
        {"id": "60004-000", "name": "CPA"},
        {"id": "20012-000", "name": "Financial Planning Consultants"},
    ]


def test_multi_branch_business_keeps_each_listing_distinct(load_json_fixture):
    """Regression test: a business with several branch addresses must
    produce one record PER BRANCH with distinct bbb_id, not collapse into
    one. An earlier version of the mapping used "{bbbId}-{businessId}" as
    bbb_id, which silently merged branches together -- see bbb_id's
    docstring on BusinessSummary.
    """
    data = load_json_fixture("search_listing_sample.json")
    records = parse_search_results(data)

    barnes = [r for r in records if r.name.startswith("Barnes, Dennig")]
    assert len(barnes) == 3  # Cincinnati, Crestview Hills, Dayton branches

    bbb_ids = {r.bbb_id for r in barnes}
    assert len(bbb_ids) == 3, "each branch must have a distinct bbb_id"

    business_ids = {r.business_id for r in barnes}
    bbb_office_ids = {r.bbb_office_id for r in barnes}
    assert business_ids == {"3089"}
    assert bbb_office_ids == {"0292"}

    cities = {r.city for r in barnes}
    assert cities == {"Cincinnati", "Crestview Hills", "Dayton"}

    # Same trap, different field: reportUrl is identical across every branch
    # (points at the canonical one) -- profile_url must come from
    # localReportUrl when present, or fetching a non-canonical branch's
    # profile_url would silently return a different branch's page.
    by_city = {r.city: r for r in barnes}
    assert by_city["Cincinnati"].profile_url.endswith(
        "/us/oh/cincinnati/profile/cpa/barnes-dennig-company-ltd-0292-3089"
    )  # canonical branch: reportUrl has no addressId suffix, and that's correct here
    assert "/addressId/178405" in by_city["Crestview Hills"].profile_url
    assert "/addressId/53569" in by_city["Dayton"].profile_url


def test_multi_phone_business_keeps_first_phone_and_full_list_in_raw_extra(load_json_fixture):
    data = load_json_fixture("search_listing_sample.json")
    records = parse_search_results(data)

    grantham = next(r for r in records if r.name == "GranthamPoole PLLC")
    assert grantham.phone == "(601) 499-2400"
    assert grantham.raw_extra["phones"] == [
        "(601) 499-2400", "(601) 499-2401", "(662) 234-8892",
        "(601) 271-8921", "(662) 234-8130", "(601) 271-8860",
    ]


def test_handles_empty_address_and_null_service_area(load_json_fixture):
    data = load_json_fixture("search_listing_sample.json")
    records = parse_search_results(data)

    simpler_tax = next(r for r in records if r.name == "Simpler Tax Relief, LLC")
    assert simpler_tax.address == ""  # BBB sends "" (not null) when unknown

    cmms = next(r for r in records if r.address == "801 S Fillmore St Ste 600")
    assert cmms.raw_extra["serviceAreasSummary"] is None
    assert cmms.raw_extra["hasServiceArea"] is False


def test_parse_search_results_returns_empty_list_on_no_match():
    assert parse_search_results({}) == []
    assert parse_search_results({"results": []}) == []
