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


def test_straight_match_is_passthrough():
    pts = _pts()
    leg = asyncio.run(StraightLineRouting().match(pts))
    assert leg.polyline == [[p.lat, p.lon] for p in pts]


def test_match_track_preserves_paused_runs():
    from app.services.geo.track_match import match_track

    # A walking run, then a paused run (trailing 1.0), then walking again.
    path = [
        [55.7539, 37.6208], [55.7535, 37.6212], [55.7531, 37.6216],
        [55.7528, 37.6220, 1.0], [55.7526, 37.6222, 1.0],
        [55.7522, 37.6226], [55.7518, 37.6230],
    ]
    out = asyncio.run(match_track(path, StraightLineRouting()))
    # Straight-line match is a passthrough, so geometry is preserved...
    assert [p[:2] for p in out] == [p[:2] for p in path]
    # ...and the paused points keep their trailing 1.0 flag (grey-dashed styling).
    paused = [p for p in out if len(p) > 2 and p[2] == 1.0]
    assert len(paused) == 2


def test_match_track_short_path_unchanged():
    from app.services.geo.track_match import match_track

    assert asyncio.run(match_track([[55.75, 37.62]], StraightLineRouting())) == [[55.75, 37.62]]


def test_match_track_chunks_long_run(monkeypatch):
    from app.config import settings
    from app.services.geo.track_match import match_track

    monkeypatch.setattr(settings, "track_match_chunk", 5)  # force multiple chunks
    path = [[55.75 + i * 0.0002, 37.62 + i * 0.0002] for i in range(20)]
    out = asyncio.run(match_track(path, StraightLineRouting()))
    # Chunking with overlap-join must not drop or duplicate points (straight passthrough).
    assert [p[:2] for p in out] == [p[:2] for p in path]


# -- honesty guard: keep the real path when the snapped match is untrustworthy -------------- #
class _StubMatch:
    """Routing stub returning a fixed match leg — to exercise confidence/detour gating."""

    def __init__(self, snapped, dist, conf):
        from app.services.geo.routing import RouteLeg

        self._leg = RouteLeg(polyline=snapped, distance_m=dist, duration_s=0.0, confidence=conf)

    async def match(self, points):
        return self._leg


def test_match_track_low_confidence_keeps_raw():
    from app.services.geo.track_match import match_track

    path = [[55.75, 37.62], [55.751, 37.621], [55.752, 37.622]]
    stub = _StubMatch([[0.0, 0.0], [1.0, 1.0]], dist=250, conf=0.1)  # low confidence
    out = asyncio.run(match_track(path, stub))
    assert [p[:2] for p in out] == [p[:2] for p in path]  # raw kept, not the bogus snap


def test_match_track_detour_keeps_raw():
    from app.services.geo.track_match import match_track

    path = [[55.75, 37.62], [55.7505, 37.6205]]  # short real segment (~70 m)
    stub = _StubMatch([[55.75, 37.62], [55.7505, 37.6205]], dist=9999, conf=0.9)  # huge detour
    out = asyncio.run(match_track(path, stub))
    assert [p[:2] for p in out] == [p[:2] for p in path]  # real cut-through kept, no detour


def test_match_track_good_match_uses_snapped():
    from app.services.geo.track_match import match_track

    path = [[55.75, 37.62], [55.751, 37.621], [55.752, 37.622]]
    snapped = [[55.7501, 37.6201], [55.7511, 37.6211], [55.7521, 37.6221]]
    stub = _StubMatch(snapped, dist=250, conf=0.9)  # reasonable length + high confidence
    out = asyncio.run(match_track(path, stub))
    assert [p[:2] for p in out] == snapped  # snapped geometry used
