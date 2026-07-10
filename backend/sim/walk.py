"""Virtual walk: turn a polyline of waypoints into a stream of position steps.

Used as the development & regression harness (business logic mentions a virtual
walk by incrementing coordinates over time). Heading is the segment bearing;
gaze_confidence is configurable to emulate "phone in pocket" (low).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from app.shared.geo_math import bearing_deg, haversine_m
from app.shared.schemas import GazeConfidence, GeoPoint, Heading, Pace


@dataclass
class SimStep:
    t: float  # seconds since start
    position: GeoPoint
    heading: Heading
    pace: Pace


def pace_for(speed_mps: float) -> Pace:
    if speed_mps < 0.2:
        return Pace.STILL
    if speed_mps <= 1.8:
        return Pace.SLOW
    return Pace.FAST


def walk(
    waypoints: list[GeoPoint],
    speed_mps: float = 1.3,
    step_s: float = 5.0,
    gaze_confidence: GazeConfidence = GazeConfidence.LOW,
) -> Iterator[SimStep]:
    """Yield a SimStep every ``step_s`` seconds along the polyline."""
    if len(waypoints) < 2:
        raise ValueError("walk() needs at least two waypoints")

    pace = pace_for(speed_mps)
    step_dist = speed_mps * step_s
    t = 0.0
    last_bearing = bearing_deg(waypoints[0], waypoints[1])

    for a, b in zip(waypoints, waypoints[1:], strict=False):
        seg_len = haversine_m(a, b)
        bearing = bearing_deg(a, b) if seg_len > 0 else last_bearing
        last_bearing = bearing
        traveled = 0.0
        while traveled < seg_len:
            frac = traveled / seg_len if seg_len > 0 else 1.0
            pos = GeoPoint(
                lat=a.lat + (b.lat - a.lat) * frac,
                lon=a.lon + (b.lon - a.lon) * frac,
            )
            yield SimStep(
                t=t,
                position=pos,
                heading=Heading(direction_deg=bearing, gaze_confidence=gaze_confidence),
                pace=pace,
            )
            traveled += step_dist
            t += step_s

    yield SimStep(
        t=t,
        position=waypoints[-1],
        heading=Heading(direction_deg=last_bearing, gaze_confidence=gaze_confidence),
        pace=pace,
    )
