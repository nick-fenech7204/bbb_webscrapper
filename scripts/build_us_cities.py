#!/usr/bin/env python
"""
One-time (or occasional-refresh) reference-data build: produces
data/reference/us_cities.csv -- every incorporated place AND census-designated
place (CDP) in the US, with lat/lon and real population, used by the metro
coverage-sweep feature to find real named places to search near a metro
center (see bbb_scraper/reference/geo.py's cities_within_radius and
etl/extract.py's extract_search_metro_coverage).

Not run by the app itself -- run this by hand when you want to refresh the
data (Census updates the Gazetteer and population estimates roughly yearly):

    python scripts/build_us_cities.py

Two Census Bureau sources, joined by (state FIPS, place FIPS):

  1. Gazetteer Files (geography: name, lat/lon for every place, CDP included)
     https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
     Free, no key needed, no population column.

  2. 2020 Decennial Census P1 (total population, for every place, CDP
     included) via the Census API -- requires a free API key (CENSUS_API_KEY
     in .env): https://api.census.gov/data/key_signup.html

Why not the annual Population Estimates (SUB-EST) CSV instead of the 2020
decennial count for population? Confirmed 2026-09-02: SUB-EST only covers
incorporated places, not CDPs -- Kendall CDP and Olympia Heights CDP (both
real, both explicitly wanted for this project) are silently absent from it.
The decennial count is older (2020, not annually updated) but is the only
free source that actually covers every place the Gazetteer does.

Why not SimpleMaps' free cities database as an alternative? Checked and
ruled out for the same reason -- their free tier explicitly excludes
CDPs/unincorporated places (upsell to their paid Pro/Comprehensive tier).
"""
from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bbb_scraper.config import settings
from bbb_scraper.logging_setup import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

GAZETTEER_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip"
CENSUS_API_URL = "https://api.census.gov/data/2020/dec/pl"

# Longest-match-first so e.g. "zona urbana" doesn't get skipped by a rule
# that only strips "urbana". Covers every suffix word actually observed in
# the 2025 Gazetteer's place NAME column.
_NAME_SUFFIX_RE = re.compile(
    r"\s+(CDP|city|town|village|borough|municipality|government|comunidad|zona urbana|corporation)$"
)


def clean_city_name(raw_name: str) -> str:
    """"Kendall CDP" -> "Kendall", "Abbeville city" -> "Abbeville". Falls
    back to the raw name unchanged if no known suffix matches (confirmed
    2026-09-02: a small number of Gazetteer rows don't follow the pattern --
    better to keep an odd suffix than mangle a name we don't recognize)."""
    cleaned = _NAME_SUFFIX_RE.sub("", raw_name).strip()
    return cleaned or raw_name


def fetch_gazetteer() -> list[dict[str, str]]:
    logger.info("Downloading Gazetteer places file...")
    resp = requests.get(GAZETTEER_URL, timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        inner_name = next(n for n in zf.namelist() if n.endswith(".txt"))
        text = zf.read(inner_name).decode("latin-1")

    rows = []
    lines = text.splitlines()
    header = lines[0].split("|")
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = dict(zip(header, line.split("|")))
        rows.append(fields)
    logger.info("Gazetteer: %d places", len(rows))
    return rows


def fetch_population_by_state(state_fips: set[str], api_key: str) -> dict[str, int]:
    """{state_fips+place_fips (7 chars): population} across all states, one
    API call per state (get=...&for=place:*&in=state:XX pulls every place in
    that state at once -- far fewer calls than one per place)."""
    population: dict[str, int] = {}
    for i, fips in enumerate(sorted(state_fips), 1):
        resp = requests.get(
            CENSUS_API_URL,
            params={"get": "NAME,P1_001N", "for": "place:*", "in": f"state:{fips}", "key": api_key},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning("Census API state=%s returned %s -- skipping", fips, resp.status_code)
            continue
        data = resp.json()
        header, *data_rows = data
        idx = {name: i for i, name in enumerate(header)}
        for row in data_rows:
            key = row[idx["state"]] + row[idx["place"]]
            try:
                population[key] = int(row[idx["P1_001N"]])
            except (ValueError, TypeError):
                continue
        logger.info("Population fetched for state %s (%d/%d)", fips, i, len(state_fips))
    return population


def main() -> int:
    if not settings.census_api_key:
        print(
            "CENSUS_API_KEY not set in .env -- get a free key at "
            "https://api.census.gov/data/key_signup.html and add it, then re-run."
        )
        return 1

    gaz_rows = fetch_gazetteer()
    state_fips = {row["GEOID"][:2] for row in gaz_rows}
    population = fetch_population_by_state(state_fips, settings.census_api_key)

    out_rows = []
    missing_population = 0
    for row in gaz_rows:
        geoid = row["GEOID"]
        pop = population.get(geoid)
        if pop is None:
            missing_population += 1
            continue
        out_rows.append(
            {
                "name": clean_city_name(row["NAME"]),
                "state": row["USPS"],
                "lat": row["INTPTLAT"],
                "lon": row["INTPTLONG"],
                "population": pop,
                "geoid": geoid,
            }
        )

    out_path = settings.us_cities_file
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "state", "lat", "lon", "population", "geoid"])
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"\nWrote {len(out_rows)} places to {out_path}")
    print(f"({missing_population} Gazetteer places had no population match -- skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
