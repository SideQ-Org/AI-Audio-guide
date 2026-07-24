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

from dataclasses import dataclass, field
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
class RouteStep:
    """One turn-by-turn maneuver along a route (OSRM legs[].steps[].maneuver): where it
    happens, what to do, and the way's name. The base of the spoken navigator cues."""

    kind: str  # OSRM maneuver type: turn / fork / end of road / roundabout / arrive / ...
    modifier: str  # left / right / slight left / straight / uturn / "" when absent
    lat: float
    lon: float
    name: str  # the way turned ONTO ("" for unnamed paths)
    distance_m: float  # length of the step's own way segment (to the NEXT maneuver)


# Maneuvers that carry no spoken value: "continue straight" and silent name changes.
_STEP_SKIP_KINDS = {"depart", "new name", "continue"}
_STEPS_CAP = 150  # runaway guard: a walking route never legitimately needs more


@dataclass
class RouteLeg:
    """A walked path between an ordered list of points: the real (or approximate)
    geometry plus its length. For a multi-point request the geometry spans the whole
    chain; distance/duration are the totals."""

    polyline: list[list[float]]  # [[lat, lon], ...] along walkable ways
    distance_m: float
    duration_s: float
    # OSRM map-matching confidence (0..1); 1.0 for a route/straight-line (not a match). Low
    # confidence means the trace didn't fit the road graph (e.g. an unmapped alley/shortcut).
    confidence: float = 1.0
    # Turn-by-turn maneuvers (OSRM steps=true); [] for straight-line/match legs, where
    # the guided leading falls back to the direction chip alone.
    steps: list[RouteStep] = field(default_factory=list)


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
    # Snap a noisy GPS trace to the road/footpath network (OSRM map-matching), for a clean
    # drawn track. Returns the snapped polyline; on no-match falls back to the input geometry.
    async def match(self, points: list[GeoPoint]) -> RouteLeg: ...


# --------------------------------------------------------------------------- #
# OSRM (self-hosted, foot profile)
# --------------------------------------------------------------------------- #
def _coords(points: list[GeoPoint]) -> str:
    """OSRM wants ``lon,lat;lon,lat;...`` (x,y order) in the URL path."""
    return ";".join(f"{p.lon},{p.lat}" for p in points)


def _parse_steps(route: dict) -> list[RouteStep]:
    """Flatten OSRM ``legs[].steps[]`` into spoken-cue-worthy maneuvers. Drops depart /
    continue / silent name changes and intermediate arrives (per-waypoint), keeping only
    the FINAL arrive so the navigator can announce the destination."""
    steps: list[RouteStep] = []
    legs = route.get("legs") or []
    for li, leg in enumerate(legs):
        last_leg = li == len(legs) - 1
        for s in leg.get("steps") or []:
            man = s.get("maneuver") or {}
            kind = str(man.get("type") or "")
            if kind in _STEP_SKIP_KINDS:
                continue
            if kind == "arrive" and not last_leg:
                continue  # intermediate waypoint arrives are stop-logic, not turns
            loc = man.get("location") or [0.0, 0.0]  # [lon, lat]
            steps.append(RouteStep(
                kind=kind,
                modifier=str(man.get("modifier") or ""),
                lat=float(loc[1]),
                lon=float(loc[0]),
                name=str(s.get("name") or ""),
                distance_m=float(s.get("distance", 0.0)),
            ))
            if len(steps) >= _STEPS_CAP:
                return steps
    return steps


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
        params = {"overview": "full", "geometries": "geojson", "steps": "true"}
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
            steps=_parse_steps(r),
        )

    async def match(self, points: list[GeoPoint]) -> RouteLeg:
        if len(points) < 2:
            pts = [[p.lat, p.lon] for p in points]
            return RouteLeg(polyline=pts, distance_m=0.0, duration_s=0.0)
        url = f"{self.base_url}/match/v1/foot/{_coords(points)}"
        # tidy=true drops near-duplicate/noisy inputs; gaps=ignore keeps one matching across
        # small gaps; radiuses gives OSRM the GPS accuracy so it snaps sensibly.
        r = settings.track_match_radius_m
        params = {
            "geometries": "geojson", "overview": "full", "tidy": "true", "gaps": "ignore",
            "radiuses": ";".join(str(r) for _ in points),
        }
        async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        matchings = data.get("matchings") or []
        if not matchings:
            raise ValueError(f"OSRM match: no matching for {len(points)} points")
        m = matchings[0]
        coords = m.get("geometry", {}).get("coordinates") or []
        polyline = _downsample([[c[1], c[0]] for c in coords])
        return RouteLeg(
            polyline=polyline,
            distance_m=float(m.get("distance", 0.0)),
            duration_s=float(m.get("duration", 0.0)),
            confidence=float(m.get("confidence", 1.0)),
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

    async def match(self, points: list[GeoPoint]) -> RouteLeg:
        # No map to snap to — return the input geometry unchanged (still cleaned upstream).
        return await self.route(points)

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
