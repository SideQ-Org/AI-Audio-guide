"""Pedestrian routing providers: a self-hosted OSRM client and a straight-line
fallback. Both satisfy the ``RoutingProvider`` protocol so the route planner is
agnostic to the source (real foot routing vs. haversine approximation).

Why self-hosted OSRM (not a public API): prod runs in a geo-blocked region where
OpenAI/Anthropic/Google (and their TTS) 403 — a public routing API (Google/Mapbox/
ORS) would very likely block the same way. An OSRM container on the internal docker
network never sends traffic outside, so there's no block to hit. When OSRM is
unreachable (or off), ``StraightLineRouting`` degrades to "as the crow flies"
distances — a straight line, not silence (the same spirit as the Overpass mirrors
and the xAI-TTS fallback: the guide never goes dark).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import httpx

from app.config import settings
from app.shared.geo_math import haversine_m
from app.shared.schemas import GeoPoint

# Cap a stored route polyline so a long walk can't bloat session state, mirroring
# providers._GEOM_MAX_PTS for object outlines. Route lines are longer than a single
# object's outline, so the cap is higher.
_POLYLINE_MAX_PTS = 256


def _downsample(pts: list[list[float]]) -> list[list[float]]:
    """Even-stride downsample of a ``[[lat, lon], ...]`` polyline, keeping the last
    vertex so the geometry still reaches its endpoint. Short lines pass through."""
    if len(pts) <= _POLYLINE_MAX_PTS:
        return pts
    step = len(pts) / _POLYLINE_MAX_PTS
    out = [pts[int(i * step)] for i in range(_POLYLINE_MAX_PTS)]
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out


@dataclass
class RouteLeg:
    """A walked path between an ordered list of points: the real (or approximate)
    geometry plus its length. For a multi-point request the geometry spans the whole
    chain; distance/duration are the totals."""

    polyline: list[list[float]]  # [[lat, lon], ...] along walkable ways
    distance_m: float
    duration_s: float


@dataclass
class DistanceMatrix:
    """Pairwise walking distances/durations for N points (row i -> col j). Used by the
    planner's greedy insertion so it optimises on real path length, not crow-flies."""

    distances_m: list[list[float]]
    durations_s: list[list[float]]


@runtime_checkable
class RoutingProvider(Protocol):
    async def route(self, points: list[GeoPoint]) -> RouteLeg: ...
    async def table(self, points: list[GeoPoint]) -> DistanceMatrix: ...


# --------------------------------------------------------------------------- #
# OSRM (self-hosted, foot profile)
# --------------------------------------------------------------------------- #
def _coords(points: list[GeoPoint]) -> str:
    """OSRM wants ``lon,lat;lon,lat;...`` (x,y order) in the URL path."""
    return ";".join(f"{p.lon},{p.lat}" for p in points)


class OSRMRouting:
    """Talks to a self-hosted ``osrm-routed`` with the foot profile over the internal
    docker network. Any transport/HTTP/JSON error raises — the caller (route planner)
    catches and falls back to straight-line so the walk still gets a route."""

    def __init__(self, base_url: str | None = None, timeout_s: float | None = None) -> None:
        self.base_url = (base_url or settings.osrm_url).rstrip("/")
        self.timeout_s = timeout_s if timeout_s is not None else settings.routing_timeout_s

    async def route(self, points: list[GeoPoint]) -> RouteLeg:
        if len(points) < 2:
            pts = [[p.lat, p.lon] for p in points]
            return RouteLeg(polyline=pts, distance_m=0.0, duration_s=0.0)
        url = f"{self.base_url}/route/v1/foot/{_coords(points)}"
        params = {"overview": "full", "geometries": "geojson", "steps": "false"}
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        routes = data.get("routes") or []
        if not routes:
            raise ValueError(f"OSRM route: no route between {len(points)} points")
        r = routes[0]
        # GeoJSON coordinates are [lon, lat]; we store [lat, lon] like every other polyline.
        coords = r.get("geometry", {}).get("coordinates") or []
        polyline = _downsample([[c[1], c[0]] for c in coords])
        return RouteLeg(
            polyline=polyline,
            distance_m=float(r.get("distance", 0.0)),
            duration_s=float(r.get("duration", 0.0)),
        )

    async def table(self, points: list[GeoPoint]) -> DistanceMatrix:
        if len(points) < 2:
            n = len(points)
            zero = [[0.0] * n for _ in range(n)]
            return DistanceMatrix(distances_m=zero, durations_s=zero)
        url = f"{self.base_url}/table/v1/foot/{_coords(points)}"
        params = {"annotations": "distance,duration"}
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        distances = data.get("distances")
        durations = data.get("durations")
        if distances is None or durations is None:
            raise ValueError("OSRM table: missing distances/durations")
        # OSRM sends null for unreachable pairs; treat those as a large finite cost so the
        # planner just avoids them rather than crashing on None arithmetic.
        big = 1e9
        distances = [[big if v is None else float(v) for v in row] for row in distances]
        durations = [[big if v is None else float(v) for v in row] for row in durations]
        return DistanceMatrix(distances_m=distances, durations_s=durations)


# --------------------------------------------------------------------------- #
# Straight-line (no network — always available)
# --------------------------------------------------------------------------- #
class StraightLineRouting:
    """Crow-flies fallback: distances via haversine, duration = distance / walk speed,
    geometry = the requested points themselves. Never raises, needs no network."""

    def __init__(self, walk_speed_mps: float | None = None) -> None:
        self.speed = walk_speed_mps if walk_speed_mps is not None else settings.walk_speed_mps

    async def route(self, points: list[GeoPoint]) -> RouteLeg:
        polyline = [[p.lat, p.lon] for p in points]
        dist = sum(
            haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1)
        )
        return RouteLeg(polyline=polyline, distance_m=dist, duration_s=dist / self.speed)

    async def table(self, points: list[GeoPoint]) -> DistanceMatrix:
        n = len(points)
        distances = [[0.0] * n for _ in range(n)]
        durations = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = haversine_m(points[i], points[j])
                distances[i][j] = distances[j][i] = d
                durations[i][j] = durations[j][i] = d / self.speed
        return DistanceMatrix(distances_m=distances, durations_s=durations)


def make_routing() -> RoutingProvider:
    """Pick the routing provider from settings. ``osrm`` -> self-hosted OSRM (with a
    straight-line fallback happening at call sites on error); anything else -> straight."""
    if settings.routing_source == "osrm":
        return OSRMRouting()
    return StraightLineRouting()
