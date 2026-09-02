from bbb_scraper.parsing.search_parser import parse_response_center


def test_parse_response_center_extracts_latlng_from_real_response(load_json_fixture):
    """search_miami_distance_sorted.json is a real captured response (see
    its _fixture_note) from a name-based find_loc search -- confirms
    location.latLng is really there and really parses.
    """
    data = load_json_fixture("search_miami_distance_sorted.json")
    center = parse_response_center(data)
    assert center == (25.77084, -80.215542)


def test_parse_response_center_returns_none_for_findlatlng_based_response(load_json_fixture):
    """search_listing_sample.json is real too, but from a find_latlng-based
    search -- confirms location genuinely stays null there, this isn't a
    parsing gap.
    """
    data = load_json_fixture("search_listing_sample.json")
    assert parse_response_center(data) is None


def test_parse_response_center_handles_missing_and_malformed_location():
    assert parse_response_center({}) is None
    assert parse_response_center({"location": None}) is None
    assert parse_response_center({"location": {}}) is None
    assert parse_response_center({"location": {"latLng": "not-a-latlng"}}) is None
    assert parse_response_center({"location": {"latLng": "25.5,not-a-number"}}) is None
