"""Offline tests for the guided-mode route planner: selection by significance,
budget adherence, dedup, ordering, and the empty-pool signal. Uses StaticPlaceProvider
+ StraightLineRouting so it's deterministic and needs no network."""

from __future__ import annotations

import asyncio

from app.services.geo.providers import StaticPlaceProvider
from app.services.geo.route_planner import RoutePlanner
from app.services.geo.routing import StraightLineRouting
from app.shared.schemas import GeoPoint, Place


def _place(pid: str, name: str, cat: str, lat: float, lon: float, tags=None) -> Place:
    return Place(
        id=pid, name=name, category=cat,
        location=GeoPoint(lat=lat, lon=lon), tags=tags or {},
    )


# A cluster of places around Red Square. museum/monument are high-weight (>=0.85/0.9),
# cafe/shop are low (below the MEDIUM floor), so only the notable ones enter a route.
def _provider() -> StaticPlaceProvider:
    return StaticPlaceProvider([
        _place("n/1", "Музей", "museum", 55.7550, 37.6180),
        _place("n/2", "Памятник", "monument", 55.7530, 37.6220),
        _place("n/3", "Собор", "place_of_worship", 55.7510, 37.6205),
        _place("n/4", "Кофейня", "cafe", 55.7540, 37.6200),
        _place("n/5", "Магазин", "shop", 55.7520, 37.6210),
    ])


ORIGIN = GeoPoint(lat=55.7539, lon=37.6208)


def _build(**kw):
    planner = RoutePlanner(StraightLineRouting(), _provider())
    return asyncio.run(planner.build(ORIGIN, **kw))


def test_loop_selects_only_notable_places():
    route = _build(mode="loop", budget_min=40)
    assert route.enough
    cats = {s.place.category for s in route.stops}
    # cafe/shop are below the MEDIUM significance floor — never routed.
    assert "cafe" not in cats and "shop" not in cats
    assert cats <= {"museum", "monument", "place_of_worship"}
    # Loop geometry returns toward the origin.
    assert route.polyline[0] == [ORIGIN.lat, ORIGIN.lon]
    assert route.polyline[-1] == [ORIGIN.lat, ORIGIN.lon]


def test_stops_are_ordered_and_indexed():
    route = _build(mode="loop", budget_min=40)
    orders = [s.order for s in route.stops]
    assert orders == list(range(len(route.stops)))
    # Cumulative distance is monotonically non-decreasing along the route.
    cums = [s.cum_distance_m for s in route.stops]
    assert cums == sorted(cums)


def test_tight_budget_limits_stops():
    big = _build(mode="loop", budget_min=60)
    tiny = _build(mode="loop", budget_min=6)
    assert len(tiny.stops) <= len(big.stops)
    # A tiny loop must still respect the budget on total distance.
    assert tiny.total_distance_m <= 6 * 60 * 1.3 + 1.0


def test_max_stops_cap(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "route_max_stops", 2)
    route = _build(mode="loop", budget_min=120)
    assert len(route.stops) <= 2


def test_dedup_drops_same_wikidata():
    dupes = StaticPlaceProvider([
        _place("n/1", "Собор", "place_of_worship", 55.7550, 37.6180, {"wikidata": "Q100"}),
        _place("w/1", "Собор (контур)", "place_of_worship", 55.7551, 37.6181, {"wikidata": "Q100"}),
        _place("n/2", "Музей", "museum", 55.7530, 37.6220),
    ])
    planner = RoutePlanner(StraightLineRouting(), dupes)
    route = asyncio.run(planner.build(ORIGIN, mode="loop", budget_min=40))
    ids = [s.place.id for s in route.stops]
    assert not ("n/1" in ids and "w/1" in ids)  # same entity routed at most once


def test_empty_pool_is_not_enough():
    barren = StaticPlaceProvider([
        _place("n/1", "Кофейня", "cafe", 55.7540, 37.6200),
        _place("n/2", "Магазин", "shop", 55.7520, 37.6210),
    ])
    planner = RoutePlanner(StraightLineRouting(), barren)
    route = asyncio.run(planner.build(ORIGIN, mode="loop", budget_min=40))
    assert not route.enough
    assert route.stops == []


def test_seen_places_excluded():
    route = _build(mode="loop", budget_min=40, seen=["n/1", "n/2"])
    ids = {s.place.id for s in route.stops}
    assert "n/1" not in ids and "n/2" not in ids


def test_pick_landmark_ends_at_top_interest():
    route = _build(mode="destination", budget_min=60, pick_landmark=True)
    assert route.destination is not None
    assert route.stops, "landmark destination should itself be a stop"
    # The last stop is the chosen landmark (highest-weight: museum 0.9 / monument).
    assert route.stops[-1].place.category in {"museum", "monument"}


def test_destination_prefers_forward_progress_over_big_detour():
    destination = GeoPoint(lat=55.7539, lon=37.6408)
    provider = StaticPlaceProvider([
        _place("fwd", "Впереди", "museum", 55.7539, 37.6308),
        _place("detour", "В стороне", "museum", 55.7839, 37.6158),
    ])
    planner = RoutePlanner(StraightLineRouting(), provider)
    route = asyncio.run(
        planner.build(ORIGIN, mode="destination", destination=destination, budget_min=45)
    )
    ids = [s.place.id for s in route.stops]
    assert "fwd" in ids
    assert not (ids and ids[0] == "detour"), "destination route should not start with a big side detour"
