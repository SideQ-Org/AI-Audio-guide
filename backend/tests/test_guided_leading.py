"""Phase 2 guided leading: _guided_tick reaches each planned stop (stop_reached +
narration), advances current_index, and fires route_done at the end. Fully offline
(heuristic backend + fixture POIs + mock enrichment + straight-line routing)."""

from __future__ import annotations

import asyncio

from app.services.agent.factory import build_orchestrator
from app.services.geo.route_planner import RoutePlanner
from app.services.geo.routing import StraightLineRouting
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import GeoPoint, Heading, NavStopStatus, Pace

ORIGIN = GeoPoint(lat=55.7539, lon=37.6208)


def _orch(monkeypatch):
    from app.config import settings

    # Force a fully offline stack regardless of the developer's .env.
    monkeypatch.setattr(settings, "agent_backend", "heuristic")
    monkeypatch.setattr(settings, "geo_source", "fixture")
    monkeypatch.setattr(settings, "enrichment_source", "mock")
    monkeypatch.setattr(settings, "nav_between_mode", "teaser")
    orch = build_orchestrator(store=InMemoryStateStore())
    orch.route_planner = RoutePlanner(StraightLineRouting(), orch.discovery.provider)
    return orch


def test_guided_reaches_every_stop_then_done(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("g1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("g1")
        outs = []
        for s in route.stops:
            pos = GeoPoint(lat=s.place.location.lat, lon=s.place.location.lon)
            outs.append(await orch.on_position("g1", pos, Heading(), Pace.SLOW))
        final = await orch.on_position("g1", ORIGIN, Heading(), Pace.SLOW)
        st = await orch.store.load("g1")
        return route, outs, final, st

    route, outs, final, st = asyncio.run(run())
    assert route.stops, "fixture should produce a route"
    reached = [o.nav_event for o in outs if o.nav_event and o.nav_event["type"] == "stop_reached"]
    assert len(reached) == len(route.stops)
    # stop_reached indices are the stop orders, in order.
    assert [e["stop_index"] for e in reached] == list(range(len(route.stops)))
    # every stop ends REACHED, and the route is finished.
    assert all(s.status == NavStopStatus.REACHED for s in st.nav.stops)
    assert final.nav_event and final.nav_event["type"] == "route_done"
    assert st.nav.active is False


def test_between_stops_teases_then_silent(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("g2", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("g2")
        first = route.stops[0].place.location
        # A point ~100 m short of the first stop (inside the teaser radius, outside arrival).
        near = GeoPoint(lat=first.lat + 0.0009, lon=first.lon)
        out1 = await orch.on_position("g2", near, Heading(), Pace.SLOW)
        out2 = await orch.on_position("g2", near, Heading(), Pace.SLOW)
        return out1, out2

    out1, out2 = asyncio.run(run())
    # First approach in range teases (a narration); the immediate repeat stays silent.
    assert out1.kind == "narration" and out1.text
    assert out2.kind == "silence"


def test_reactive_mode_untouched_when_not_guided(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        # No start_guided -> guide_mode stays "free"; a normal tick greets (reactive path).
        out = await orch.on_position("g3", ORIGIN, Heading(), Pace.SLOW)
        st = await orch.store.load("g3")
        return out, st

    out, st = asyncio.run(run())
    assert st.guide_mode == "free"
    assert st.nav.active is False
    # The very first reactive tick is the instant greeting, never a nav event.
    assert out.nav_event is None
