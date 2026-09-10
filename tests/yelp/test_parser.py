from datetime import datetime

from bbb_scraper.yelp.models import YelpBusiness
from bbb_scraper.yelp.parser import parse_yelp_business, parse_yelp_search_response


def test_parse_search_response_against_real_fixture(load_json_fixture):
    """Fixture is a real, untrimmed businesses/search response
    (car dealers / Jacksonville, FL), captured 2026-09-10."""
    data = load_json_fixture("yelp_search_car_dealers_jacksonville.json")

    records = parse_yelp_search_response(
        data, search_term="car dealers", search_location="Jacksonville, FL", source_page=0
    )

    assert len(records) == 50
    assert all(isinstance(r, YelpBusiness) for r in records)

    first = records[0]
    assert first.yelp_id == "OkQQ0-P2gyO4FamOKZLUcg"
    assert first.yelp_alias == "arlington-toyota-jacksonville-2"
    assert first.name == "Arlington Toyota"
    assert first.phone == "+19043029611"
    assert first.display_phone == "(904) 302-9611"
    assert first.address == "10939 Atlantic Blvd"
    assert first.city == "Jacksonville"
    assert first.state == "FL"
    assert first.postal_code == "32225"
    assert first.lat == 30.32563
    assert first.lon == -81.51713
    assert first.rating == 2.3
    assert first.review_count == 620
    assert first.price is None  # absent for every row in this fixture
    assert first.is_closed is False
    assert first.categories == ["Car Dealers", "Car Rental", "Auto Repair"]
    assert first.distance_meters is not None
    assert first.search_term == "car dealers"
    assert first.search_location == "Jacksonville, FL"
    assert first.source_page == 0
    assert isinstance(first.scraped_at, datetime)


def test_full_category_objects_and_suite_lines_kept_in_raw_extra(load_json_fixture):
    data = load_json_fixture("yelp_search_car_dealers_jacksonville.json")
    first = parse_yelp_search_response(data)[0]
    # aliases preserved for later category-narrowing work
    assert first.raw_extra["categories"][0] == {"alias": "car_dealers", "title": "Car Dealers"}
    # business_hours wasn't hoisted -> must survive in raw_extra
    assert "business_hours" in first.raw_extra


def test_parse_business_handles_missing_optional_blocks():
    """A sparse record (no location, no coords, no categories) must still
    map without raising."""
    biz = parse_yelp_business({"id": "abc123", "name": "Nowhere LLC"})
    assert biz.yelp_id == "abc123"
    assert biz.name == "Nowhere LLC"
    assert biz.address is None
    assert biz.lat is None
    assert biz.categories == []
    assert biz.review_count is None
