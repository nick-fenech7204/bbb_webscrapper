import math

from bbb_scraper.reference.geo import destination_point, generate_coverage_points


def test_destination_point_due_north_from_equator():
    # 1 degree of latitude ~= 69 miles -- traveling ~69 miles due north from
    # the equator should land close to 1 degree north, same longitude.
    lat, lon = destination_point(0.0, 0.0, 69.0, bearing_degrees=0.0)
    assert math.isclose(lat, 1.0, abs_tol=0.05)
    assert math.isclose(lon, 0.0, abs_tol=1e-6)


def test_destination_point_due_east_from_equator():
    lat, lon = destination_point(0.0, 0.0, 69.0, bearing_degrees=90.0)
    assert math.isclose(lat, 0.0, abs_tol=1e-6)
    assert math.isclose(lon, 1.0, abs_tol=0.05)


def test_destination_point_zero_distance_returns_same_point():
    lat, lon = destination_point(30.0, -90.0, 0.0, bearing_degrees=45.0)
    assert math.isclose(lat, 30.0, abs_tol=1e-9)
    assert math.isclose(lon, -90.0, abs_tol=1e-9)


def test_destination_point_normalizes_longitude_crossing_antimeridian():
    # Starting near +179 longitude and heading east should wrap to negative,
    # not blow past 180.
    lat, lon = destination_point(0.0, 179.5, 100.0, bearing_degrees=90.0)
    assert -180.0 <= lon <= 180.0


def test_generate_coverage_points_includes_center_by_default():
    points = generate_coverage_points(25.77, -80.22, radius_miles=25, count=8)
    assert len(points) == 8
    assert points[0] == (25.77, -80.22)


def test_generate_coverage_points_excludes_center_when_asked():
    points = generate_coverage_points(25.77, -80.22, radius_miles=25, count=8, include_center=False)
    assert len(points) == 8
    assert (25.77, -80.22) not in points


def test_generate_coverage_points_respects_explicit_rings():
    points = generate_coverage_points(
        0.0, 0.0, radius_miles=10, rings=[(5, 4), (10, 6)], include_center=True
    )
    assert len(points) == 1 + 4 + 6


def test_generate_coverage_points_ring_points_are_actually_near_requested_radius():
    center_lat, center_lon = 25.77, -80.22
    points = generate_coverage_points(
        center_lat, center_lon, radius_miles=20, rings=[(20, 6)], include_center=False
    )
    for lat, lon in points:
        # rough distance check via the equirect approximation, generous
        # tolerance -- just confirming "roughly 20 miles out", not precision
        dlat_miles = (lat - center_lat) * 69.0
        dlon_miles = (lon - center_lon) * 69.0 * math.cos(math.radians(center_lat))
        dist = math.hypot(dlat_miles, dlon_miles)
        assert 18 <= dist <= 22
