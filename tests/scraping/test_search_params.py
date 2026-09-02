from bbb_scraper.config import Settings
from bbb_scraper.reference.models import Category, Location, parse_location
from bbb_scraper.scraping.search import build_referer, build_search_params


def _cfg(**overrides) -> Settings:
    base = dict(bbb_find_country="USA")
    base.update(overrides)
    return Settings(**base)


def test_build_search_params_sends_category_name_as_find_text():
    category = Category(id="plumbers", name="Plumbers")
    location = parse_location("Austin, TX")

    params = build_search_params(category, location, page=2, cfg=_cfg())

    assert params["find_text"] == "Plumbers"
    assert params["find_type"] == "Category"
    assert params["find_country"] == "USA"
    assert params["page"] == 2
    # no lat/lon on this Location -> falls back to find_loc
    assert params["find_loc"] == "Austin, TX"
    assert "find_latlng" not in params


def test_build_search_params_prefers_latlng_when_present():
    category = Category(id="plumbers", name="Plumbers")
    location = Location(raw="Austin, TX", city="Austin", state="TX", lat=30.2672, lon=-97.7431)

    params = build_search_params(category, location, cfg=_cfg())

    assert params["find_latlng"] == "30.2672,-97.7431"
    assert "find_loc" not in params


def test_build_search_params_uses_zip_display_when_zip():
    category = Category(id="plumbers", name="Plumbers")
    location = parse_location("78701")

    params = build_search_params(category, location, cfg=_cfg())
    assert params["find_loc"] == "78701"


def test_build_search_params_omits_sort_by_default():
    category = Category(id="plumbers", name="Plumbers")
    location = parse_location("Austin, TX")

    params = build_search_params(category, location, cfg=_cfg())
    assert "sort" not in params


def test_build_search_params_includes_sort_when_given():
    category = Category(id="plumbers", name="Plumbers")
    location = parse_location("Austin, TX")

    params = build_search_params(category, location, cfg=_cfg(), sort="Distance")
    assert params["sort"] == "Distance"


def test_build_referer_is_a_bbb_search_url():
    category = Category(id="plumbers", name="Plumbers")
    location = parse_location("Austin, TX")

    referer = build_referer(category, location, page=3)
    assert referer.startswith("https://www.bbb.org/search?")
    assert "find_text=Plumbers" in referer
    assert "page=3" in referer
