"""
Great-circle geo math for scattering search anchor points around a center.

No geocoding library or step needed: BBB's own search response resolves a
place name to lat/lon as a side effect of a normal name-based search
(confirmed 2026-09-02 -- `location.latLng` in the raw `/api/search`
response, see parsing/search_parser.py's `parse_response_center`). This
module only does the "N points scattered around a known center" math.

Deliberately dependency-free: this is the standard spherical-trig
destination-point formula (given a start point, a bearing, and a distance,
find the resulting point). Accurate enough at city-metro scale (single
digits of miles of error at 10-50 mile radii) -- not worth a dependency
like geopy for this.
"""
from __future__ import annotations

import math

EARTH_RADIUS_MILES = 3958.8


def destination_point(
    lat: float, lon: float, distance_miles: float, bearing_degrees: float
) -> tuple[float, float]:
    """The point `distance_miles` from (lat, lon) along `bearing_degrees`
    (0=north, 90=east, 180=south, 270=west).
    """
    lat_r = math.radians(lat)
    lon_r = math.radians(lon)
    bearing_r = math.radians(bearing_degrees)
    ang_dist = distance_miles / EARTH_RADIUS_MILES

    new_lat_r = math.asin(
        math.sin(lat_r) * math.cos(ang_dist)
        + math.cos(lat_r) * math.sin(ang_dist) * math.cos(bearing_r)
    )
    new_lon_r = lon_r + math.atan2(
        math.sin(bearing_r) * math.sin(ang_dist) * math.cos(lat_r),
        math.cos(ang_dist) - math.sin(lat_r) * math.sin(new_lat_r),
    )
    new_lon = math.degrees(new_lon_r)
    new_lon = (new_lon + 540) % 360 - 180  # normalize to (-180, 180]
    return math.degrees(new_lat_r), new_lon


def generate_coverage_points(
    center_lat: float,
    center_lon: float,
    radius_miles: float,
    count: int = 16,
    *,
    include_center: bool = True,
    rings: list[tuple[float, int]] | None = None,
) -> list[tuple[float, float]]:
    """Scatter `count` points around (center_lat, center_lon) within
    `radius_miles`, meant as separate search anchors (each becomes its own
    request -- see Extractor.extract_search_coverage).

    Default layout: two rings (half radius, full radius), remaining count
    split evenly between them, plus the center itself as point 0 (so
    callers that already searched the center separately can skip index 0).
    Pass `rings` -- a list of (radius_miles, point_count) pairs -- to
    control the layout directly instead of the two-ring default.
    """
    points: list[tuple[float, float]] = []
    if include_center:
        points.append((center_lat, center_lon))

    if rings is None:
        remaining = count - len(points)
        if remaining <= 0:
            return points
        inner_n = remaining // 2
        outer_n = remaining - inner_n
        rings = [(radius_miles * 0.5, inner_n), (radius_miles, outer_n)]

    for ring_radius, ring_count in rings:
        if ring_count <= 0:
            continue
        for i in range(ring_count):
            bearing = (360 / ring_count) * i
            points.append(destination_point(center_lat, center_lon, ring_radius, bearing))

    return points
