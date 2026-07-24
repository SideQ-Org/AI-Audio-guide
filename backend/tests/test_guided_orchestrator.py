"""Phase 1 guided mode: plan_route writes a proposed NavState onto the session, and
accept/reject/skip mutate it correctly. Uses the fixture orchestrator (offline)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.agent.factory import build_orchestrator
from app.services.geo.providers import StaticPlaceProvider
from app.services.geo.route_planner import RoutePlanner
from app.services.geo.routing import StraightLineRouting
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import GeoPoint, NavStopStatus

_FIX = Path(__file__).resolve().parent / "fixtures" / "places_red_square.json"


def _orch():
    from app.config import settings

    settings.agent_backend = "heuristic"
    settings.geo_source = "fixture"
    settings.enrichment_source = "mock"
    orch = build_orchestrator(store=InMemoryStateStore())
    # Force straight-line routing + the offline fixture POIs so this test never hits the
    # network (the .env may set GEO_SOURCE=overpass, which would make discovery.provider live).
    provider = StaticPlaceProvider.from_json(_FIX)
    orch.route_planner = RoutePlanner(StraightLineRouting(), provider)
    return orch


# Red Square fixture centre — the fixture provider returns notable places around here.
ORIGIN = GeoPoint(lat=55.7539, lon=37.6208)


def test_plan_route_writes_nav_state():
    orch = _orch()

    async def run():
        route = await orch.plan_route("s1", ORIGIN, mode="loop", budget_min=40)
        st = await orch.store.load("s1")
        return route, st

    route, st = asyncio.run(run())
    assert st.guide_mode == "guided"
    assert st.nav.active is True and st.nav.accepted is False
    assert st.nav.mode == "loop"
    assert len(st.nav.stops) == len(route.stops)
    assert st.nav.current_index == 0
    # Every stop has an order and a leg distance recorded.
    assert [s.order for s in st.nav.stops] == list(range(len(st.nav.stops)))


def test_accept_route_sets_accepted():
    orch = _orch()

    async def run():
        await orch.plan_route("s2", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("s2")
        return await orch.store.load("s2")

    st = asyncio.run(run())
    assert st.nav.accepted is True


def test_plan_route_warms_guided_preview_artifacts():
    orch = _orch()

    async def run():
        route = await orch.plan_route("sp", ORIGIN, mode="loop", budget_min=40)
        for _ in range(5):
            tasks = list(orch._bg)
            if not tasks:
                break
            await asyncio.gather(*tasks, return_exceptions=True)
        st = await orch.store.load("sp")
        cache = dict(orch.pipeline._narr_cache)
        return route, st, cache, orch.discovery.inventory.peek("sp")

    route, st, cache, inv = asyncio.run(run())
    assert route.stops
    assert st.nav.accepted is False
    assert inv is not None
    first_stop_id = route.stops[0].place.id
    assert (first_stop_id, st.language) in cache
    assert st.startup_block is not None
    assert st.startup_block.scope == "guided_start"
    assert st.startup_block.text


def test_reject_route_returns_to_free():
    orch = _orch()

    async def run():
        await orch.plan_route("s3", ORIGIN, mode="loop", budget_min=40)
        await orch.cancel_route("s3")
        return await orch.store.load("s3")

    st = asyncio.run(run())
    assert st.guide_mode == "free"
    assert st.nav.active is False
    assert st.nav.stops == []


def test_skip_stop_marks_skipped():
    orch = _orch()

    async def run():
        route = await orch.plan_route("s4", ORIGIN, mode="loop", budget_min=40)
        assert route.stops, "fixture should yield at least one stop"
        await orch.skip_stop("s4", 0)
        return await orch.store.load("s4")

    st = asyncio.run(run())
    assert st.nav.stops[0].status == NavStopStatus.SKIPPED
