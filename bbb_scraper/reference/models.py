"""
Structured search inputs: a BBB industry/category and a location.

Using these instead of a free-text query means the request we build maps
directly onto BBB's own filters (category id + location) rather than hoping
a text search happens to land on the right vertical.
"""
from __future__ import annotations

import re

from pydantic import BaseModel


class Category(BaseModel):
    """One entry from BBB's industry/category taxonomy.

    Confirmed 2026-08-31 against a real captured search request: BBB doesn't
    take a separate opaque category id in the search API -- `name` (e.g.
    "accredited cpa") is sent directly as the `find_text` param, alongside a
    static `find_type=Category`. So `id` here is purely an *internal* stable
    key (what CategoryDirectory.get()/search() match against, what shows up
    in data/reference/categories.json) -- it never goes in the request
    itself. `slug` is unused by the request too; kept in case it's useful
    for URLs later.
    """

    id: str
    name: str
    slug: str | None = None


class Location(BaseModel):
    """A parsed location: either a ZIP code or a city/state pair.

    Keeps `raw` around unconditionally so callers/URL builders always have
    something to fall back to even if parsing didn't recognize the shape.

    `lat`/`lon` are optional and not populated by `parse_location` (there's
    no geocoding step yet). `search.py` prefers `find_latlng` when they're
    present (more precise) and falls back to plain `find_loc` text
    otherwise -- both are live-confirmed working, so either is fine.
    """

    raw: str
    zip_code: str | None = None
    city: str | None = None
    state: str | None = None
    lat: float | None = None
    lon: float | None = None

    @property
    def display(self) -> str:
        if self.zip_code:
            return self.zip_code
        if self.city and self.state:
            return f"{self.city}, {self.state}"
        return self.raw


class City(BaseModel):
    """One real place (incorporated city or census-designated place) from
    data/reference/us_cities.csv -- see scripts/build_us_cities.py for how
    that file is built and cities.py for how it's queried.
    """

    name: str
    state: str
    lat: float
    lon: float
    population: int
    geoid: str

    @property
    def display(self) -> str:
        return f"{self.name}, {self.state}"

    def to_location(self) -> Location:
        """As a name-based Location for a find_loc search -- confirmed
        2026-09-02 this is what actually reaches a different local result
        pool, unlike a raw lat/lon Location (find_latlng) -- see
        etl/extract.py's extract_search_metro_coverage.
        """
        return Location(raw=self.display, city=self.name, state=self.state)


class Metro(BaseModel):
    """One entry in the curated metro-coverage dropdown list
    (data/reference/metros.json) -- deliberately a small, hand-picked set of
    major metros, not every place in us_cities.csv. `seed_location` is fed
    through `parse_location` and searched first to resolve the metro's
    center point (BBB's own geocoding, same free mechanism
    extract_search_coverage already relies on) -- it should be a real,
    unambiguous "City, ST" BBB can resolve, not necessarily the metro's
    official/full name.
    """

    id: str
    name: str
    seed_location: str


_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")
_CITY_STATE_RE = re.compile(r"^\s*([A-Za-z .'\-]+),\s*([A-Za-z]{2})\s*$")


def parse_location(text: str) -> Location:
    """Parse '78701', 'Austin, TX', or (as a fallback) anything else into a
    Location. Unrecognized shapes are kept as-is via `raw` -- they'll still
    work as a literal `find_loc`-style value, just without city/state/zip
    broken out individually.
    """
    text = text.strip()

    if _ZIP_RE.match(text):
        return Location(raw=text, zip_code=text)

    match = _CITY_STATE_RE.match(text)
    if match:
        return Location(raw=text, city=match.group(1).strip(), state=match.group(2).upper())

    return Location(raw=text)
