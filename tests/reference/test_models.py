from bbb_scraper.reference.models import parse_location


def test_parse_location_recognizes_zip_code():
    loc = parse_location("78701")
    assert loc.zip_code == "78701"
    assert loc.city is None
    assert loc.display == "78701"


def test_parse_location_recognizes_zip_plus4():
    loc = parse_location("78701-1234")
    assert loc.zip_code == "78701-1234"


def test_parse_location_recognizes_city_state():
    loc = parse_location("Austin, TX")
    assert loc.city == "Austin"
    assert loc.state == "TX"
    assert loc.zip_code is None
    assert loc.display == "Austin, TX"


def test_parse_location_lowercase_state_is_normalized():
    loc = parse_location("Austin, tx")
    assert loc.state == "TX"


def test_parse_location_falls_back_to_raw_for_unrecognized_shape():
    loc = parse_location("somewhere weird")
    assert loc.raw == "somewhere weird"
    assert loc.zip_code is None
    assert loc.city is None
    assert loc.display == "somewhere weird"
