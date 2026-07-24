"""Guided-mode leg improvements: overshoot retire (a missed stop can't stall the tour),
pass-by narration between stops, skip -> tail reroute, and the cue urgency split
(pre-announce queues, only the imminent command interrupts)."""

from __future__ import annotations

import asyncio
import math

from app.services.agent.factory import build_orchestrator
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import GeoPoint, Heading, NavStopStatus, Pace

ORIGIN = GeoPoint(lat=55.7539, lon=37.6208)


def offset_point(origin: GeoPoint, bearing_deg: float, dist_m: float) -> GeoPoint:
    dlat = dist_m * math.cos(math.radians(bearing_deg)) / 111_320.0
    dlon = dist_m * math.sin(math.radians(bearing_deg)) / (
        111_320.0 * math.cos(math.radians(origin.lat))
    )
    return GeoPoint(lat=origin.lat + dlat, lon=origin.lon + dlon)


def _orch(monkeypatch, **over):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_backend", "heuristic")
    monkeypatch.setattr(settings, "geo_source", "fixture")
    monkeypatch.setattr(settings, "enrichment_source", "mock")
    monkeypatch.setattr(settings, "nav_between_mode", "teaser")
    monkeypatch.setattr(settings, "guided_script_enabled", False)
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)
    return build_orchestrator(store=InMemoryStateStore())


def test_overshot_stop_is_retired_not_stalling(monkeypatch):
    orch = _orch(monkeypatch, nav_overshoot_near_m=110.0, nav_overshoot_recede_m=60.0,
                 nav_passby_enabled=False)

    async def run():
        route = await orch.plan_route("ov1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("ov1")
        await orch.on_position("ov1", ORIGIN, Heading(), Pace.SLOW)  # greeting
        first = route.stops[0].place.location
        # Pass NEAR the stop (~80 m to the side) without entering the 35 m arrival radius…
        near = offset_point(first, 90.0, 80.0)
        await orch.on_position("ov1", near, Heading(), Pace.SLOW)
        # …then walk clearly away from it.
        away = offset_point(first, 90.0, 180.0)
        out = await orch.on_position("ov1", away, Heading(), Pace.SLOW)
        st = await orch.store.load("ov1")
        return route, out, st

    route, out, st = asyncio.run(run())
    stop0 = next(s for s in st.nav.stops if s.order == 0)
    assert stop0.status == NavStopStatus.REACHED, "overshot stop must retire, not stall"
    assert out.nav_event and out.nav_event["type"] == "stop_reached"
    assert st.nav.current_index >= 1  # the tour moved on to the next stop


def test_passby_narrates_non_stop_object_between_stops(monkeypatch):
    # Cap the route at 2 stops so the fixture's other objects stay NON-stop objects the
    # walker can pass between them.
    orch = _orch(monkeypatch, nav_passby_enabled=True, nav_passby_min_gap_s=0.0,
                 route_max_stops=2)

    async def run():
        await orch.plan_route("pb1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("pb1")
        await orch.on_position("pb1", ORIGIN, Heading(), Pace.SLOW)  # greeting (warms inv)
        await asyncio.sleep(0.05)  # let the fire-and-forget inventory warm land
        st = await orch.store.load("pb1")
        stop = next(s for s in st.nav.stops if s.status == NavStopStatus.PENDING)
        # A position far from the next stop (outside the teaser radius) but ON some
        # fixture object that is NOT a route stop: the guide should narrate it in passing.
        stop_ids = {s.place_id for s in st.nav.stops}
        inv = orch.discovery.inventory.peek("pb1")
        assert inv is not None and inv.places, "greeting must warm the inventory disc"
        from app.shared.geo_math import haversine_m

        target = next(
            (
                p for p in inv.places
                if p.id not in stop_ids
                and haversine_m(p.location, GeoPoint(lat=stop.lat, lon=stop.lon)) > 200
            ),
            None,
        )
        assert target is not None, "fixture must offer a non-stop object to pass by"
        out = await orch.on_position(
            "pb1", target.location, Heading(), Pace.SLOW
        )
        st = await orch.store.load("pb1")
        return out, st

    out, st = asyncio.run(run())
    assert out.kind == "narration" and out.text
    assert out.place_id is not None and out.place_id in st.seen_place_ids


def test_skip_stop_replans_tail_and_drops_skipped(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        route = await orch.plan_route("sk1", ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route("sk1")
        await orch.on_position("sk1", ORIGIN, Heading(), Pace.SLOW)  # greeting sets position
        out = await orch.skip_stop("sk1", 0)
        st = await orch.store.load("sk1")
        return route, out, st

    route, out, st = asyncio.run(run())
    # The tail was replanned around the refused stop and the client was told to redraw.
    assert out is not None and out.nav_event and out.nav_event["type"] == "reroute"
    # The refused stop is gone from the fresh plan (not offered back, not pending).
    pending_ids = [s.place_id for s in st.nav.stops if s.status == NavStopStatus.PENDING]
    assert route.stops[0].place.id not in pending_ids, "a skipped stop must not come back"
    assert st.nav.stops, "the tour keeps leading on the replanned tail"


def test_cue_urgency_split(monkeypatch):
    from app.config import settings
    from app.services.agent.orchestrator import Orchestrator
    from app.shared.schemas import NavManeuver, SessionState

    monkeypatch.setattr(settings, "nav_cue_min_gap_s", 0.0)
    st = SessionState(session_id="cu")
    st.nav.steps = [
        NavManeuver(kind="turn", modifier="right", name="Парковая улица",
                    lat=ORIGIN.lat, lon=ORIGIN.lon)
    ]
    far = offset_point(ORIGIN, 0.0, 90.0)  # inside pre-announce (110), outside fire (35)
    text, urgent = Orchestrator._nav_cue_text(st, far)
    assert text and urgent is False, "pre-announce must NOT interrupt"
    near = offset_point(ORIGIN, 0.0, 20.0)  # inside the fire radius
    text2, urgent2 = Orchestrator._nav_cue_text(st, near)
    assert text2 and urgent2 is True, "the imminent command interrupts"
