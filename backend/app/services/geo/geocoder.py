"""Reverse geocoding: GPS point -> Address(country, city, district, street).

This is what lets the guide narrate "от общего к частному" — it has to know the
city / district / street it is standing in before it can descend to the objects
inside them. Public Nominatim is often blocked/limited, so the default provider
reuses the same reachable Overpass endpoint as place discovery: ``is_in`` returns
the administrative areas enclosing the point, and a small ``around`` query finds
the street. Off the hot-path and move-gated by the orchestrator (areas change
slowly), so the extra request is rare.
"""

from __future__ import annotations

import math
from typing import Protocol

from app.shared.schemas import Address, GeoPoint

from .providers import fetch_overpass_elements

# Named streets within this distance of each other (relative to the user) count as
# "equally near"; among those we prefer the higher road class (the avenue over a cross
# street / its дублёр). 10 m (was 25): 25 m let a bigger PARALLEL road a lane away win
# over the quiet переулок you're actually on ("иду не по этому переулку") — the class
# tie is now only for a true corner/overlap, so distance decides. A street clearly
# closer than this always wins outright.
_STREET_NEAR_TIE_M = 10.0
# The chosen street is "confident" only when you're within this of it — then the guide
# may assert "идёшь по улице X". Beyond it (a wide search radius picked the nearest named
# way but you may be mid-block / between streets) the narrator speaks of "здесь / в этом
# районе" without naming the street, instead of the false "вы идёте по улице X" (P3/c).
_STREET_CONFIDENT_M = 20.0


class Geocoder(Protocol):
    async def reverse(self, point: GeoPoint, language: str = "ru") -> Address: ...


# OSM admin_level is locale-dependent; these heuristics target the common cases
# (federal cities like Moscow/SPb where the city is level 4, ordinary cities at
# level 6, districts at level 8+). The ``place`` tag, when present, overrides.
_CITY_PLACES = {"city", "town", "village"}
_DISTRICT_PLACES = {"suburb", "borough", "quarter", "neighbourhood", "city_district", "district"}

# Road-class priority — used only to break ties between streets the user is EQUALLY
# near (e.g. an avenue and its дублёр): prefer the higher class. The primary criterion
# is distance (the street you're actually closest to), so a far parallel road never
# wins just for being bigger.
_HIGHWAY_RANK = {
    "motorway": 9, "trunk": 8, "primary": 7, "secondary": 6, "tertiary": 5,
    "unclassified": 4, "residential": 4, "living_street": 3, "pedestrian": 2,
    "footway": 1, "path": 1, "track": 1, "service": 1, "cycleway": 1, "steps": 0,
}


def _highway_rank(hw: str) -> int:
    return _HIGHWAY_RANK.get(hw, 4)  # unknown class ~ an ordinary residential street


def _point_to_segment_m(p: GeoPoint, a: dict, b: dict) -> float:
    """Distance (m) from p to the segment a-b, via a local equirectangular projection
    centred on p (accurate to well under a metre at street scale)."""
    latr = math.radians(p.lat)
    mx = math.cos(latr) * 111_320.0  # metres per degree of longitude at this latitude
    my = 111_320.0  # metres per degree of latitude
    ax, ay = (a["lon"] - p.lon) * mx, (a["lat"] - p.lat) * my
    bx, by = (b["lon"] - p.lon) * mx, (b["lat"] - p.lat) * my
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 == 0.0:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, -(ax * dx + ay * dy) / seg2))  # project p (origin) onto a-b
    return math.hypot(ax + t * dx, ay + t * dy)


def _way_distance_m(p: GeoPoint, geometry: list[dict] | None) -> float:
    """Nearest distance (m) from p to a way's polyline; +inf if no usable geometry."""
    if not geometry:
        return math.inf
    if len(geometry) == 1:
        g = geometry[0]
        return _point_to_segment_m(p, g, g)
    return min(
        _point_to_segment_m(p, geometry[i], geometry[i + 1])
        for i in range(len(geometry) - 1)
    )


def _pick_street(streets: list[tuple[float, int, str]]) -> tuple[str, float] | None:
    """From (distance_m, -rank, name) candidates pick the nearest; among streets within
    _STREET_NEAR_TIE_M of the nearest, prefer the higher road class, then the closer,
    then the name — fully deterministic, so the chosen street can't flicker tick-to-tick.
    Returns (name, distance_m) so the caller can judge confidence."""
    if not streets:
        return None
    nearest = min(s[0] for s in streets)
    near = [s for s in streets if s[0] <= nearest + _STREET_NEAR_TIE_M]
    near.sort(key=lambda s: (s[1], s[0], s[2]))  # -rank (higher class first), distance, name
    chosen = near[0]
    return chosen[2], chosen[0]


def _name(tags: dict, language: str) -> str | None:
    for key in (f"name:{language}", "name:ru", "name:en", "name", "int_name"):
        v = tags.get(key)
        if v:
            return v
    return None


def parse_address(
    elements: list[dict], language: str = "ru", point: GeoPoint | None = None
) -> Address:
    """Pure parse of an Overpass ``is_in`` + nearby-street response into an Address.

    Street selection: the way the user is actually NEAREST to (computed from each way's
    geometry against ``point``), with road class breaking ties between equally-near
    streets (the avenue over its дублёр). Without ``point``/geometry it falls back to
    "highest road class wins". Distance-first means a wide search radius can reliably
    find a big avenue without ever mislabelling you onto a far parallel road, and the
    result is deterministic — no flicker, so the area story stops restarting/repeating."""
    admins: dict[int, str] = {}  # admin_level -> name
    city = district = country = None
    # (distance_m, -rank, name) per named highway; distance 0 when geometry is absent.
    streets: list[tuple[float, int, str]] = []
    for el in elements:
        tags = el.get("tags") or {}
        name = _name(tags, language)
        if not name:
            continue
        place = tags.get("place")
        if place in _CITY_PLACES and not city:
            city = name
        if place in _DISTRICT_PLACES and not district:
            district = name
        hw = tags.get("highway")
        if hw:
            dist = _way_distance_m(point, el.get("geometry")) if point is not None else 0.0
            streets.append((dist, -_highway_rank(hw), name))
            continue
        lvl = tags.get("admin_level")
        if lvl and lvl.isdigit():
            admins.setdefault(int(lvl), name)
    picked = _pick_street(streets)
    if picked is not None:
        street, street_dist = picked
        # No geometry (point is None) => distances are 0 => confident (fixture/tests).
        street_confident = street_dist <= _STREET_CONFIDENT_M
    else:
        street, street_confident = None, False

    if 2 in admins:
        country = admins[2]
    # city: an ordinary city sits at level 6; a federal city (Moscow) at level 4.
    # Skip the macro levels 3 (federal district) and 5 (admin okrug).
    if not city:
        city = admins.get(6) or admins.get(4)
    # district: the most specific local area (largest admin_level >= 7).
    if not district:
        deep = [lvl for lvl in admins if lvl >= 7]
        if deep:
            district = admins[max(deep)]
    return Address(
        country=country, city=city, district=district,
        street=street, street_confident=street_confident,
    )


class OverpassGeocoder:
    @staticmethod
    def _query(point: GeoPoint) -> str:
        lat, lon = point.lat, point.lon
        return (
            "[out:json][timeout:12];"
            f"is_in({lat},{lon})->.a;"
            "area.a[admin_level];out tags;"
            # 80 m (was 35): wide enough to actually find a big проспект from the far
            # sidewalk / through GPS drift (at 35-50 m a wide avenue often returned no
            # street at all — the "долго находит улицу" lag). `out geom` gives each way's
            # polyline so parse_address can pick the one you're truly NEAREST to, so the
            # generous radius never mislabels you onto a far parallel road.
            f"way(around:80,{lat},{lon})[highway][name];out geom 16;"
        )

    async def reverse(self, point: GeoPoint, language: str = "ru") -> Address:
        # Same multi-mirror failover as place discovery — geocoding shares the
        # outage risk, so it must share the resilience (one slow mirror used to
        # block the area intro and leave the guide silent).
        elements = await fetch_overpass_elements(self._query(point))
        return parse_address(elements, language, point)


class MockGeocoder:
    """Returns a fixed address — for tests and offline/dev runs."""

    def __init__(self, address: Address) -> None:
        self._address = address

    async def reverse(self, point: GeoPoint, language: str = "ru") -> Address:
        return self._address


class NullGeocoder:
    """No reverse geocoding — empty address (the guide just won't name the area)."""

    async def reverse(self, point: GeoPoint, language: str = "ru") -> Address:
        return Address()
