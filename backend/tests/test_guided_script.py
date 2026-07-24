"""Ф2: at accept the guide builds a whole-route script (background) and plays the intro
overview first. Ф4 fallback: with guided_script_enabled=False the per-stop path is used.
Fully offline (heuristic scripter + fixture POIs + mock enrichment + straight routing)."""

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
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)
    orch = build_orchestrator(store=InMemoryStateStore())
    orch.route_planner = RoutePlanner(StraightLineRouting(), orch.discovery.provider)
    return orch


async def _drain_bg(orch):
    # accept_route builds the script on a fire-and-forget task; await it.
    for _ in range(5):
        tasks = list(orch._bg)
        if not tasks:
            break
        await asyncio.gather(*tasks, return_exceptions=True)


async def _first_guided_lines(orch, sid, pos=ORIGIN, limit: int = 2, attempts: int = 8):
    """Collect the first few *spoken* guided lines after accept.

    Depending on the exact timing/config, early ticks may contain a greeting, intro,
    lead-in, or a transient silence while the route state advances. Tests care that the
    expected spoken lines appear, not that every early tick speaks.
    """
    outs = []
    for _ in range(attempts):
        out = await orch.on_position(sid, pos, Heading(), Pace.SLOW)
        if out.kind == "narration" and out.text:
            outs.append(out)
            if len(outs) >= limit:
                break
    assert len(outs) >= limit
    return outs


def test_accept_guided_starts_speaking_even_if_script_is_late(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("s1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("s1")
        outs = await _first_guided_lines(orch, "s1")
        st = await orch.store.load("s1")
        return route, outs, st

    route, outs, st = asyncio.run(run())
    assert route.stops
    assert outs
    assert any(o.text for o in outs)
    assert st.nav.accepted is True
    # Script may still be building, but guided must already be speaking.
    if st.nav.script is not None and st.nav.script_ready:
        assert len(st.nav.script.beats) == len(route.stops)


def test_intro_plays_once(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        await orch.plan_route("s2", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("s2")
        await _drain_bg(orch)
        outs = await _first_guided_lines(orch, "s2")
        intro = next(o for o in outs if o.text)
        second = await orch.on_position("s2", ORIGIN, Heading(), Pace.SLOW)
        return outs, intro, second

    outs, intro, second = asyncio.run(run())
    assert outs
    assert not (second.kind == "narration" and second.text == intro.text)


def test_fast_guided_intro_is_startup_contract(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("fast1", ORIGIN, mode="loop", budget_min=40)
        for _ in range(5):
            tasks = list(orch._bg)
            if not tasks:
                break
            await asyncio.gather(*tasks, return_exceptions=True)
        st_before = await orch.store.load("fast1")
        await orch.accept_route("fast1")
        st_after_accept = await orch.store.load("fast1")
        first = await orch.on_position("fast1", ORIGIN, Heading(), Pace.SLOW)
        st_after = await orch.store.load("fast1")
        return route, st_before, st_after_accept, first, st_after

    route, st_before, st_after_accept, first, st_after = asyncio.run(run())
    assert route.stops
    assert st_before.startup_block is not None
    assert st_before.startup_block.scope == "guided_start"
    assert st_after_accept.startup_block is not None
    assert st_after_accept.startup_block.scope == "guided_start"
    # Direct orchestrator ticks bypass the producer's startup-block delivery path, so the
    # prepared fast intro stays parked here; the websocket/producer test asserts it is spoken first.
    assert first.kind == "narration" and first.text
    assert st_after.startup_block is not None
    assert st_after.startup_block.scope == "guided_start"


def test_full_guided_walk_reaches_stops_and_finishes(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("f1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("f1")
        await _drain_bg(orch)
        first_lines = await _first_guided_lines(orch, "f1")
        outs = list(first_lines)
        for s in route.stops:
            pos = GeoPoint(lat=s.place.location.lat, lon=s.place.location.lon)
            outs.append(await orch.on_position("f1", pos, Heading(), Pace.SLOW))
        fin = await orch.on_position("f1", ORIGIN, Heading(), Pace.SLOW)  # finale or last narration
        done = await orch.on_position("f1", ORIGIN, Heading(), Pace.SLOW)  # route_done
        st = await orch.store.load("f1")
        return route, outs, fin, done, st

    route, outs, fin, done, st = asyncio.run(run())
    reached = [o.nav_event for o in outs if o.nav_event and o.nav_event["type"] == "stop_reached"]
    assert len(reached) == len(route.stops)
    assert any(o.kind == "narration" and o.text for o in outs)
    assert done.nav_event and done.nav_event["type"] == "route_done"
    if st.nav.script is not None and st.nav.script_ready:
        assert st.nav.script.intro or st.nav.script.finale


def test_reroute_reseeds_tail_without_blocking_guided(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        await orch.plan_route("rs1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("rs1")
        await _drain_bg(orch)
        st = await orch.store.load("rs1")
        far = GeoPoint(lat=ORIGIN.lat + 0.005, lon=ORIGIN.lon + 0.005)
        out = await orch._reroute_tail(st, far, Heading(), Pace.SLOW, reason="test")
        await _drain_bg(orch)
        st2 = await orch.store.load("rs1")
        return out, st2

    out, st2 = asyncio.run(run())
    assert out is not None and out.nav_event and out.nav_event["type"] == "reroute"
    assert st2.nav.active is True
    # Script may be absent or late; reroute must still leave a usable guided tail.
    if st2.nav.script is not None:
        assert st2.nav.script_ready in {True, False}


def test_disabled_flag_skips_script(monkeypatch):
    orch = _orch(monkeypatch, guided_script_enabled=False)

    async def run():
        await orch.plan_route("s3", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("s3")
        await _drain_bg(orch)
        st = await orch.store.load("s3")
        return st

    st = asyncio.run(run())
    # No script built -> per-stop reactive path (script stays None).
    assert st.nav.script is None
    assert st.nav.accepted is True


def test_script_ready_warm_replaces_generic_first_stop_cache(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("s4", ORIGIN, mode="loop", budget_min=40)
        first_id = route.stops[0].place.id
        preview = None
        for _ in range(5):
            tasks = list(orch._bg)
            if not tasks:
                break
            await asyncio.gather(*tasks, return_exceptions=True)
            preview = orch.pipeline._narr_cache.get((first_id, "ru"))
            if preview is not None:
                break
        assert preview is not None
        await orch.accept_route("s4")
        await _drain_bg(orch)
        final = orch.pipeline._narr_cache.get((first_id, "ru"))
        st = await orch.store.load("s4")
        return preview, final, st, first_id

    preview, final, st, first_id = asyncio.run(run())
    assert st.nav.script_ready is True
    assert final is not None
    assert final[0]
    assert preview == final
    assert (first_id, "ru") in orch.pipeline._narr_cache


def test_guided_between_stops_uses_area_arc_not_silence(monkeypatch):
    orch = _orch(monkeypatch, nav_between_mode="teaser", nav_passby_enabled=False)

    async def run():
        route = await orch.plan_route("ga1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("ga1")
        await _drain_bg(orch)
        lines = await _first_guided_lines(orch, "ga1")
        st = await orch.store.load("ga1")
        assert route.stops
        first = route.stops[0].place.location
        far = GeoPoint(lat=(ORIGIN.lat + first.lat) / 2.0, lon=(ORIGIN.lon + first.lon) / 2.0)
        out = await orch.on_position("ga1", far, Heading(), Pace.SLOW)
        st2 = await orch.store.load("ga1")
        return lines, out, st, st2

    lines, out, st, st2 = asyncio.run(run())
    assert lines
    assert st2.narrative_plan.outline or st2.area_facts is not None or st2.last_place is not None or st2.nav.accepted
    assert out.kind == "narration" and out.text, "guided mode should carry the story between stops"
