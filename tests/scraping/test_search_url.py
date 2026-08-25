from urllib.parse import parse_qs, urlparse

from bbb_scraper.reference.models import Category, parse_location
from bbb_scraper.scraping.search import build_search_url


def test_build_search_url_encodes_category_and_location():
    category = Category(id="plumbers", name="Plumbers", slug="plumbers")
    location = parse_location("Austin, TX")

    url = build_search_url(category, location, page=2)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["find_category"] == ["plumbers"]
    assert params["find_text"] == ["Plumbers"]
    assert params["find_loc"] == ["Austin, TX"]
    assert params["page"] == ["2"]


def test_build_search_url_uses_zip_when_location_is_zip():
    category = Category(id="plumbers", name="Plumbers")
    location = parse_location("78701")

    url = build_search_url(category, location)
    params = parse_qs(urlparse(url).query)
    assert params["find_loc"] == ["78701"]
