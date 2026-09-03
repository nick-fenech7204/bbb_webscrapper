"""
US city/place directory: loads the reference dataset of every US
incorporated place and CDP (with lat/lon + real 2020 census population) and
provides radius-based lookup over it.

The data lives at data/reference/us_cities.csv (see scripts/build_us_cities.py
for how it's built). This module just knows how to load and query it.

Why a directory of real place names, not just lat/lon math: confirmed
2026-09-02 that BBB's "local" result pool is tied to the specific *named*
place searched (`find_loc`), not just proximity to a center point -- a
lat/lon ring point 13 miles from Miami's own center (`find_latlng`) found a
completely different, non-overlapping set of real local businesses than a
plain "Kendall, FL" search did, even though Kendall is itself about 13 miles
from downtown Miami. `etl/extract.py`'s `extract_search_metro_coverage` uses
this module to find real nearby place names to sweep instead of scattering
coordinates (see `reference/geo.py`'s `generate_coverage_points`, used by
the earlier `extract_search_coverage` -- that approach still has its place
for a single unnamed center, just not for full metro coverage).
"""
from __future__ import annotations

import csv
from pathlib import Path

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import get_logger
from bbb_scraper.reference.geo import distance_miles
from bbb_scraper.reference.models import City

logger = get_logger(__name__)


class CityDirectory:
    def __init__(self, cities: list[City]):
        self._cities = cities

    @classmethod
    def load(cls, path: Path | str | None = None) -> "CityDirectory":
        path = Path(path) if path else settings.us_cities_file
        if not path.exists():
            logger.warning(
                "US cities reference file not found at %s -- run "
                "scripts/build_us_cities.py (needs a free Census API key) to "
                "enable metro coverage search. Falling back to an empty directory.",
                path,
            )
            return cls([])

        cities: list[City] = []
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    cities.append(
                        City(
                            name=row["name"],
                            state=row["state"],
                            lat=float(row["lat"]),
                            lon=float(row["lon"]),
                            population=int(row["population"]),
                            geoid=row["geoid"],
                        )
                    )
                except (ValueError, KeyError):
                    continue  # skip a malformed row rather than fail the whole load
        logger.info("Loaded %d cities from %s", len(cities), path)
        return cls(cities)

    def all(self) -> list[City]:
        return list(self._cities)

    def within_radius(
        self,
        center_lat: float,
        center_lon: float,
        radius_miles: float,
        *,
        min_population: int = 25_000,
    ) -> list[City]:
        """Real named places within `radius_miles` of a center point, with
        at least `min_population` residents -- sorted nearest first.

        `min_population` defaults to 25,000 to keep a big metro's sweep list
        to a few dozen substantial real cities instead of every tiny CDP a
        40-mile radius can catch (100+ for some metros) -- see
        scripts/build_us_cities.py's module docstring for the reasoning.
        Pass 0 to disable the floor entirely.
        """
        matches = [
            (distance_miles(center_lat, center_lon, city.lat, city.lon), city)
            for city in self._cities
            if city.population >= min_population
        ]
        matches = [(d, city) for d, city in matches if d <= radius_miles]
        matches.sort(key=lambda pair: pair[0])
        return [city for _, city in matches]
