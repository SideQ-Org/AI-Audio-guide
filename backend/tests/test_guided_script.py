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


def test_accept_builds_script_and_plays_intro(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("s1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("s1")
        await _drain_bg(orch)
        st = await orch.store.load("s1")
        out = await orch.on_position("s1", ORIGIN, Heading(), Pace.SLOW)
        st2 = await orch.store.load("s1")
        return route, st, out, st2

    route, st, out, st2 = asyncio.run(run())
    assert st.nav.script_ready is True
    assert st.nav.script is not None and st.nav.script.intro
    assert len(st.nav.script.beats) == len(route.stops)
    # first guided tick speaks the intro overview.
    assert out.kind == "narration" and out.text == st.nav.script.intro
    assert st2.nav.intro_done is True


def test_intro_plays_once(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        await orch.plan_route("s2", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("s2")
        await _drain_bg(orch)
        first = await orch.on_position("s2", ORIGIN, Heading(), Pace.SLOW)
        # a second tick at the same spot must NOT repeat the intro.
        second = await orch.on_position("s2", ORIGIN, Heading(), Pace.SLOW)
        return first, second

    first, second = asyncio.run(run())
    assert first.kind == "narration"
    assert not (second.kind == "narration" and second.text == first.text)


def test_full_scripted_walk_intro_stops_finale(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("f1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("f1")
        await _drain_bg(orch)
        st0 = await orch.store.load("f1")
        outs = [await orch.on_position("f1", ORIGIN, Heading(), Pace.SLOW)]  # intro tick
        for s in route.stops:
            pos = GeoPoint(lat=s.place.location.lat, lon=s.place.location.lon)
            outs.append(await orch.on_position("f1", pos, Heading(), Pace.SLOW))
        fin = await orch.on_position("f1", ORIGIN, Heading(), Pace.SLOW)  # finale
        done = await orch.on_position("f1", ORIGIN, Heading(), Pace.SLOW)  # route_done
        return route, st0.nav.script, outs, fin, done

    route, script, outs, fin, done = asyncio.run(run())
    # intro first
    assert outs[0].kind == "narration" and outs[0].text == script.intro
    # every stop reached
    reached = [o.nav_event for o in outs if o.nav_event and o.nav_event["type"] == "stop_reached"]
    assert len(reached) == len(route.stops)
    # finale spoken, then route_done
    assert fin.kind == "narration" and fin.text == script.finale
    assert done.nav_event and done.nav_event["type"] == "route_done"


def test_reroute_rescripts_tail(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        await orch.plan_route("rs1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("rs1")
        await _drain_bg(orch)
        st = await orch.store.load("rs1")
        assert st.nav.script_ready is True
        # A tail reroute from an off-route point re-scripts the fresh tail (script_ready flips
        # false, then the background rebuild sets it true again).
        far = GeoPoint(lat=ORIGIN.lat + 0.005, lon=ORIGIN.lon + 0.005)
        out = await orch._reroute_tail(st, far, Heading(), Pace.SLOW, reason="test")
        await _drain_bg(orch)
        st2 = await orch.store.load("rs1")
        return out, st2

    out, st2 = asyncio.run(run())
    assert out is not None and out.nav_event and out.nav_event["type"] == "reroute"
    assert st2.nav.script_ready is True
    assert st2.nav.script is not None


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
