"""Offline tests for the pedestrian routing layer (StraightLineRouting + make_routing).
OSRM is exercised separately in test_routing_live.py (needs a running osrm-foot)."""

from __future__ import annotations

import asyncio

from app.services.geo.routing import (
    OSRMRouting,
    StraightLineRouting,
    make_routing,
)
from app.shared.geo_math import haversine_m
from app.shared.schemas import GeoPoint


def _pts() -> list[GeoPoint]:
    # Three points on Red Square, roughly a right-ish path.
    return [
        GeoPoint(lat=55.7539, lon=37.6208),
        GeoPoint(lat=55.7525, lon=37.6231),
        GeoPoint(lat=55.7510, lon=37.6210),
    ]


def test_straight_route_distance_matches_haversine_chain():
    pts = _pts()
    leg = asyncio.run(StraightLineRouting(walk_speed_mps=1.3).route(pts))
    expected = sum(haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    assert abs(leg.distance_m - expected) < 1e-6
    assert abs(leg.duration_s - expected / 1.3) < 1e-6
    # Geometry is the requested points themselves, in [lat, lon].
    assert leg.polyline == [[p.lat, p.lon] for p in pts]


def test_straight_route_single_point_is_zero():
    leg = asyncio.run(StraightLineRouting().route([GeoPoint(lat=55.75, lon=37.62)]))
    assert leg.distance_m == 0.0
    assert leg.duration_s == 0.0


def test_straight_table_is_symmetric_with_zero_diagonal():
    pts = _pts()
    m = asyncio.run(StraightLineRouting(walk_speed_mps=1.3).table(pts))
    n = len(pts)
    assert len(m.distances_m) == n and all(len(r) == n for r in m.distances_m)
    for i in range(n):
        assert m.distances_m[i][i] == 0.0
        for j in range(n):
            assert abs(m.distances_m[i][j] - m.distances_m[j][i]) < 1e-9
    # Off-diagonal matches haversine, duration = distance / speed.
    d01 = haversine_m(pts[0], pts[1])
    assert abs(m.distances_m[0][1] - d01) < 1e-6
    assert abs(m.durations_s[0][1] - d01 / 1.3) < 1e-6


def test_make_routing_selects_by_source(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "routing_source", "straight")
    assert isinstance(make_routing(), StraightLineRouting)
    monkeypatch.setattr(settings, "routing_source", "osrm")
    assert isinstance(make_routing(), OSRMRouting)
