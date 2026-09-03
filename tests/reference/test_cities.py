from pathlib import Path

from bbb_scraper.reference.cities import CityDirectory
from bbb_scraper.reference.models import City


def _city(name, state, lat, lon, population, geoid="0000000"):
    return City(name=name, state=state, lat=lat, lon=lon, population=population, geoid=geoid)


def test_load_missing_file_returns_empty_directory(tmp_path):
    directory = CityDirectory.load(tmp_path / "does_not_exist.csv")
    assert directory.all() == []
    assert directory.within_radius(25.77, -80.22, 40) == []


def test_load_from_real_us_cities_file():
    """Confirms the actual built reference file (data/reference/
    us_cities.csv) has real, correct data -- not a placeholder. Kendall and
    Olympia Heights are both real CDPs (not incorporated cities) near Miami
    -- confirming they're present is exactly the gap this file was built to
    close (annual Census population estimates don't cover CDPs at all).
    """
    directory = CityDirectory.load(Path("data/reference/us_cities.csv"))
    all_cities = directory.all()
    assert len(all_cities) > 10_000  # every US place, not a trimmed sample

    kendall = next(c for c in all_cities if c.name == "Kendall" and c.state == "FL")
    assert kendall.population > 50_000  # real 2020 count is 80,241
    olympia_heights = next(c for c in all_cities if c.name == "Olympia Heights" and c.state == "FL")
    assert olympia_heights.population > 5_000  # real 2020 count is 12,873


def test_within_radius_filters_by_distance_and_sorts_nearest_first(tmp_path):
    center = _city("Center", "FL", 25.77, -80.22, population=100_000)
    near = _city("Near", "FL", 25.80, -80.22, population=100_000)  # a couple miles
    far = _city("Far", "FL", 30.27, -97.74, population=100_000)  # ~1000+ miles (Austin)
    directory = CityDirectory([far, near, center])

    results = directory.within_radius(25.77, -80.22, radius_miles=50, min_population=0)

    assert [c.name for c in results] == ["Center", "Near"]  # far excluded, nearest first


def test_within_radius_filters_by_population_floor(tmp_path):
    small = _city("Small Town", "FL", 25.78, -80.22, population=5_000)
    big = _city("Big City", "FL", 25.79, -80.22, population=500_000)
    directory = CityDirectory([small, big])

    results = directory.within_radius(25.77, -80.22, radius_miles=50, min_population=25_000)

    assert [c.name for c in results] == ["Big City"]


def test_within_radius_zero_population_floor_disables_filter():
    small = _city("Small Town", "FL", 25.78, -80.22, population=5_000)
    directory = CityDirectory([small])

    results = directory.within_radius(25.77, -80.22, radius_miles=50, min_population=0)

    assert [c.name for c in results] == ["Small Town"]


def test_city_to_location_is_name_based_not_latlng():
    city = _city("Kendall", "FL", 25.669538, -80.354741, population=80_241)
    location = city.to_location()
    assert location.city == "Kendall"
    assert location.state == "FL"
    assert location.lat is None and location.lon is None  # find_loc, not find_latlng -- deliberate
