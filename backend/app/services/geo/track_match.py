"""Map-match the walked GPS breadcrumb to the street/footpath network so the drawn track is
smooth and follows roads, not raw jitter. Wraps a RoutingProvider's ``match`` (OSRM
map-matching; straight-line is a passthrough).

The walk ``path`` is ``[[lat, lon], ...]`` with a trailing ``1.0`` on points walked while
paused. We split it into runs by the paused flag: WALKING runs are snapped to roads (chunked,
since OSRM /match caps ~100 coords/request), PAUSED runs are kept raw (with their flag) so the
renderer still draws them as a grey dashed stretch. Any OSRM error degrades to the input
geometry — never a crash, never silence.
"""

from __future__ import annotations

from app.config import settings
from app.shared.geo_math import haversine_m
from app.shared.schemas import GeoPoint

from .routing import RoutingProvider


def _is_paused(pt: list[float]) -> bool:
    return len(pt) > 2 and pt[2] == 1.0


def _raw_len_m(points: list[GeoPoint]) -> float:
    return sum(haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))


async def _safe_match(routing: RoutingProvider, points: list[GeoPoint]) -> list[list[float]]:
    """Snap one chunk to roads — but keep the REAL (unsnapped) geometry when the match is
    untrustworthy: an OSRM error, low confidence, or a snapped length that's much longer than
    the raw trace (a plausible-but-wrong detour around an unmapped alley/shortcut). This is the
    honesty guard: gladly straighten onto real streets, never invent a path that wasn't walked."""
    raw = [[p.lat, p.lon] for p in points]
    try:
        leg = await routing.match(points)
    except Exception:  # noqa: BLE001 — matching is cosmetic; degrade to the raw geometry
        return raw
    if not leg.polyline:
        return raw
    if leg.confidence < settings.track_match_min_confidence:
        return raw  # the trace didn't fit the road graph — trust the GPS, not the snap
    raw_len = _raw_len_m(points)
    if (
        raw_len > 0
        and leg.distance_m
        > raw_len * settings.track_match_detour_factor + settings.track_match_detour_floor_m
    ):
        return raw  # snapped path detours far around — the real cut-through isn't mapped
    return leg.polyline


async def _match_walking_run(
    routing: RoutingProvider, run: list[list[float]]
) -> list[list[float]]:
    """Snap a continuous walking run, chunked with a 1-point overlap for continuity."""
    if len(run) < 2:
        return [[r[0], r[1]] for r in run]
    chunk = max(2, settings.track_match_chunk)
    pts = [GeoPoint(lat=r[0], lon=r[1]) for r in run]
    out: list[list[float]] = []
    i = 0
    while i < len(pts):
        seg = pts[i : i + chunk]
        if len(seg) < 2:
            break
        snapped = await _safe_match(routing, seg)
        if out and snapped:
            snapped = snapped[1:]  # drop the overlap-join duplicate
        out.extend(snapped)
        if i + chunk >= len(pts):
            break
        i += chunk - 1  # overlap the last point into the next chunk
    return out


async def match_track(
    path: list[list[float]], routing: RoutingProvider
) -> list[list[float]]:
    """Return a road-snapped copy of the walk track. Walking runs are snapped; paused runs are
    kept raw (with their trailing 1.0) so paused stretches stay grey-dashed."""
    if len(path) < 2:
        return path
    result: list[list[float]] = []
    run: list[list[float]] = []
    run_paused = _is_paused(path[0])
    for pt in path:
        p = _is_paused(pt)
        if p != run_paused:
            result.extend(run if run_paused else await _match_walking_run(routing, run))
            run = []
            run_paused = p
        run.append(pt)
    result.extend(run if run_paused else await _match_walking_run(routing, run))
    return result
