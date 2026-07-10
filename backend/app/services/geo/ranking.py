"""Turn raw places into ranked Candidates for a given position & heading.

Score combines three signals from the business logic:
  * proximity      — closer is better
  * type weight    — museum/monument > shop (categories.py)
  * gaze cone      — objects ahead get a boost; muted when gaze_confidence=low
"""

from __future__ import annotations

from collections.abc import Iterable

from app.shared.geo_math import (
    angle_diff,
    bearing_deg,
    haversine_m,
    nearest_on_geometry,
    relative_bearing,
)
from app.shared.schemas import Candidate, GazeConfidence, GeoPoint, Heading, Place

from .categories import weight_for

GAZE_CONE_DEG = 45.0
_GAZE_BOOST_HIGH = 1.5
_GAZE_BOOST_LOW = 1.2


def _side(rel_bearing: float, confidence: GazeConfidence) -> str:
    """Map a signed relative bearing to a spoken side. ahead/behind are safe from
    the GPS course; left/right require a real facing (gaze_confidence=high)."""
    a = abs(rel_bearing)
    if a <= GAZE_CONE_DEG:
        return "ahead"
    if a >= 180.0 - GAZE_CONE_DEG:
        return "behind"
    if confidence is GazeConfidence.HIGH:
        return "left" if rel_bearing < 0 else "right"
    return ""  # lateral, but confidence too low to call left/right


def _score(candidate: Candidate, radius_m: float) -> float:
    proximity = max(0.0, 1.0 - candidate.distance_m / radius_m) if radius_m else 0.0
    gaze = 1.0
    if candidate.in_gaze_cone:
        gaze = (
            _GAZE_BOOST_HIGH
            if candidate.gaze_confidence is GazeConfidence.HIGH
            else _GAZE_BOOST_LOW
        )
    return candidate.type_weight * (0.5 + 0.5 * proximity) * gaze


def build_candidates(
    position: GeoPoint,
    heading: Heading,
    places: Iterable[Place],
    radius_m: float,
    seen: Iterable[str] = (),
) -> list[Candidate]:
    seen_ids = set(seen)
    candidates: list[Candidate] = []
    for place in places:
        if place.id in seen_ids:
            continue
        # For a polygon/line, measure to the whole shape from the LIVE position (0 when
        # inside) and take direction from the true nearest edge point — not a stale
        # snapped vertex (B1). A point object stays a plain haversine to its location.
        if place.geometry:
            distance, npt = nearest_on_geometry(position, place.geometry)
        else:
            distance, npt = haversine_m(position, place.location), place.location
        if distance > radius_m:
            continue
        in_cone = False
        rel_bearing: float | None = None
        side: str | None = None
        # Inside/at the shape (distance ~0): "here" — no meaningful direction to give.
        if heading.direction_deg is not None and distance >= 1.0:
            bearing = bearing_deg(position, npt)
            in_cone = angle_diff(bearing, heading.direction_deg) <= GAZE_CONE_DEG
            rel_bearing = round(relative_bearing(heading.direction_deg, bearing), 1)
            side = _side(rel_bearing, heading.gaze_confidence) or None
        candidates.append(
            Candidate(
                place=place,
                distance_m=round(distance, 1),
                type_weight=weight_for(place.category),
                in_gaze_cone=in_cone,
                gaze_confidence=heading.gaze_confidence,
                relative_bearing_deg=rel_bearing,
                side=side,
            )
        )
    candidates.sort(key=lambda c: _score(c, radius_m), reverse=True)
    return candidates
