import math

from app.shared.geo_math import (
    angle_diff,
    bearing_deg,
    haversine_m,
    nearest_on_geometry,
    relative_bearing,
)
from app.shared.schemas import GeoPoint


def test_haversine_known_distance():
    # ~111.2 km per degree of latitude
    a = GeoPoint(lat=55.0, lon=37.0)
    b = GeoPoint(lat=56.0, lon=37.0)
    assert math.isclose(haversine_m(a, b), 111_195, rel_tol=0.01)


def test_nearest_on_geometry_inside_ring_is_zero():
    # B1: a closed ring (park/pond) the user stands INSIDE reads as distance 0 ("here"),
    # not the far boundary vertex — the fix for "silent inside the park" / "pond ahead".
    c = GeoPoint(lat=55.0, lon=37.0)
    d = 0.002  # ~200 m box half-size
    ring = [
        [c.lat - d, c.lon - d], [c.lat - d, c.lon + d],
        [c.lat + d, c.lon + d], [c.lat + d, c.lon - d],
        [c.lat - d, c.lon - d],  # closed
    ]
    dist, npt = nearest_on_geometry(c, ring)
    assert dist == 0.0 and npt.lat == c.lat  # inside -> here


def test_nearest_on_geometry_outside_measures_to_edge():
    # Outside the ring: distance is to the nearest EDGE, not a corner vertex.
    ring = [
        [55.0, 37.0], [55.0, 37.002], [55.002, 37.002], [55.002, 37.0], [55.0, 37.0],
    ]
    # a point due south of the bottom edge, mid-span -> distance ≈ perpendicular to edge
    p = GeoPoint(lat=54.999, lon=37.001)  # ~111 m south of the y=55.0 edge
    dist, npt = nearest_on_geometry(p, ring)
    assert math.isclose(dist, 111.2, rel_tol=0.05)
    assert math.isclose(npt.lat, 55.0, abs_tol=1e-6)  # nearest point sits on the edge


def test_nearest_on_geometry_open_line():
    # An open polyline (river) — nearest point along the line, no inside test.
    line = [[55.0, 37.0], [55.0, 37.004]]
    p = GeoPoint(lat=55.0009, lon=37.002)  # ~100 m north of the line mid-span
    dist, _ = nearest_on_geometry(p, line)
    assert math.isclose(dist, 100.0, rel_tol=0.05)


def test_haversine_zero():
    a = GeoPoint(lat=55.75, lon=37.62)
    assert haversine_m(a, a) == 0.0


def test_bearing_cardinal():
    origin = GeoPoint(lat=55.0, lon=37.0)
    north = GeoPoint(lat=55.5, lon=37.0)
    east = GeoPoint(lat=55.0, lon=37.5)
    assert math.isclose(bearing_deg(origin, north), 0.0, abs_tol=1.0)
    assert math.isclose(bearing_deg(origin, east), 90.0, abs_tol=1.0)


def test_angle_diff_wraps():
    assert angle_diff(10, 350) == 20
    assert angle_diff(0, 180) == 180
    assert angle_diff(90, 90) == 0


def test_relative_bearing_signs_left_and_right():
    # facing north (0): due east is +90 (right), due west is -90 (left)
    assert math.isclose(relative_bearing(0, 90), 90.0, abs_tol=0.01)
    assert math.isclose(relative_bearing(0, 270), -90.0, abs_tol=0.01)
    # straight ahead and directly behind
    assert math.isclose(relative_bearing(40, 40), 0.0, abs_tol=0.01)
    assert abs(relative_bearing(0, 180)) == 180.0
    # wrap-around: facing 350, target 10 is 20° to the right
    assert math.isclose(relative_bearing(350, 10), 20.0, abs_tol=0.01)
