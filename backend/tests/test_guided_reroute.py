"""Phase 3 guided reroute: straying off the route line for longer than the debounce
replans the pending tail (keeping reached stops) and emits a reroute event. Off-route
below the threshold, or within the debounce window, does NOT reroute."""

from __future__ import annotations

import asyncio

from app.services.agent.factory import build_orchestrator
from app.services.geo.route_planner import RoutePlanner
from app.services.geo.routing import StraightLineRouting
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import GeoPoint, Heading, Pace

ORIGIN = GeoPoint(lat=55.7539, lon=37.6208)


def _orch(monkeypatch, **over):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_backend", "heuristic")
    monkeypatch.setattr(settings, "geo_source", "fixture")
    monkeypatch.setattr(settings, "enrichment_source", "mock")
    monkeypatch.setattr(settings, "nav_offroute_m", 50.0)
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)
    orch = build_orchestrator(store=InMemoryStateStore())
    orch.route_planner = RoutePlanner(StraightLineRouting(), orch.discovery.provider)
    return orch


def test_offroute_past_debounce_reroutes(monkeypatch):
    # Zero debounce + zero min-interval so a single far tick triggers immediately.
    orch = _orch(monkeypatch, nav_offroute_debounce_s=0.0, nav_reroute_min_interval_s=0.0)

    async def run():
        await orch.plan_route("r1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("r1")
        # Jump ~1 km away from the whole route line -> off-route. The first tick arms the
        # debounce timer; the second (past the zero debounce) reroutes.
        far = GeoPoint(lat=ORIGIN.lat + 0.01, lon=ORIGIN.lon + 0.01)
        await orch.on_position("r1", far, Heading(), Pace.SLOW)
        out = await orch.on_position("r1", far, Heading(), Pace.SLOW)
        st = await orch.store.load("r1")
        return out, st

    out, st = asyncio.run(run())
    assert out.nav_event and out.nav_event["type"] == "reroute"
    assert st.nav.reroute_count == 1
    assert st.nav.stops, "reroute should yield a fresh tail"


def test_offroute_within_debounce_does_not_reroute(monkeypatch):
    # Long debounce: the first off-route tick only arms the timer, never reroutes.
    orch = _orch(monkeypatch, nav_offroute_debounce_s=60.0)

    async def run():
        await orch.plan_route("r2", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("r2")
        far = GeoPoint(lat=ORIGIN.lat + 0.01, lon=ORIGIN.lon + 0.01)
        out = await orch.on_position("r2", far, Heading(), Pace.SLOW)
        st = await orch.store.load("r2")
        return out, st

    out, st = asyncio.run(run())
    assert not (out.nav_event and out.nav_event.get("type") == "reroute")
    assert st.nav.reroute_count == 0
    assert st.nav.off_route_since is not None  # timer armed


def test_on_route_does_not_reroute(monkeypatch):
    orch = _orch(monkeypatch, nav_offroute_debounce_s=0.0, nav_reroute_min_interval_s=0.0)

    async def run():
        route = await orch.plan_route("r3", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("r3")
        # A point right on the route polyline (its first vertex) -> on-route, no reroute.
        on = GeoPoint(lat=route.polyline[1][0], lon=route.polyline[1][1])
        out = await orch.on_position("r3", on, Heading(), Pace.SLOW)
        st = await orch.store.load("r3")
        return out, st

    out, st = asyncio.run(run())
    assert not (out.nav_event and out.nav_event.get("type") == "reroute")
    assert st.nav.reroute_count == 0
