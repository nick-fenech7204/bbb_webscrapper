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

    `id` is whatever BBB uses to filter by category in the search request
    (a numeric id, a slug, whatever their `find_category`-equivalent param
    expects -- confirm against a real captured search request). `slug` is
    optional and only useful if BBB's URLs/params use a human-readable slug
    instead of / in addition to the id.
    """

    id: str
    name: str
    slug: str | None = None


class Location(BaseModel):
    """A parsed location: either a ZIP code or a city/state pair.

    Keeps `raw` around unconditionally so callers/URL builders always have
    something to fall back to even if parsing didn't recognize the shape.
    """

    raw: str
    zip_code: str | None = None
    city: str | None = None
    state: str | None = None

    @property
    def display(self) -> str:
        if self.zip_code:
            return self.zip_code
        if self.city and self.state:
            return f"{self.city}, {self.state}"
        return self.raw


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
