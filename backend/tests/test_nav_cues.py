"""Turn-by-turn navigator cues (guided mode): the deterministic cue engine over
NavState.steps — pre-announce once, fire once, min-gap, never in narration memory —
and the languages cue strings. Fully offline (no LLM, no network)."""

from __future__ import annotations

import asyncio

from app.services.agent import languages as lang
from app.services.agent.factory import build_orchestrator
from app.services.agent.orchestrator import Orchestrator
from app.services.geo.route_planner import RoutePlanner
from app.services.geo.routing import StraightLineRouting
from app.services.state.store import InMemoryStateStore
from app.shared.geo_math import offset_point
from app.shared.schemas import GeoPoint, Heading, NavManeuver, Pace

ORIGIN = GeoPoint(lat=55.7539, lon=37.6208)


def _orch(monkeypatch, **over):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_backend", "heuristic")
    monkeypatch.setattr(settings, "geo_source", "fixture")
    monkeypatch.setattr(settings, "enrichment_source", "mock")
    monkeypatch.setattr(settings, "session_greeting", True)
    monkeypatch.setattr(settings, "guided_script_enabled", False)
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)
    orch = build_orchestrator(store=InMemoryStateStore())
    orch.route_planner = RoutePlanner(StraightLineRouting(), orch.discovery.provider)
    return orch


def test_nav_cue_strings_ru():
    assert lang.nav_cue("ru", "turn", "right", "Парковая улица") == (
        "Поверни направо, дальше — Парковая улица."
    )
    assert lang.nav_cue("ru", "turn", "left") == "Поверни налево."
    assert lang.nav_cue("ru", "turn", "right", "", pre_dist_m=104.0) == (
        "Через сто метров поверни направо."
    )
    assert lang.nav_cue("ru", "fork", "slight left") == "На развилке держись левее."
    assert lang.nav_cue("en", "turn", "right", "Main Street") == (
        "Turn right, onto Main Street."
    )
    # unmapped maneuver -> "" (the engine skips it)
    assert lang.nav_cue("ru", "rotary", "weird") == ""


def _maneuver_at(dist_m: float, *, kind="turn", modifier="right", name="Парковая улица"):
    p = offset_point(ORIGIN, 90.0, dist_m)
    return NavManeuver(kind=kind, modifier=modifier, lat=p.lat, lon=p.lon, name=name)


def _guided_state_with_steps(orch, sid, steps):
    async def run():
        await orch.plan_route(sid, ORIGIN, mode="loop", budget_min=40)
        await orch.accept_route(sid)
        first = await orch.on_position(sid, ORIGIN, Heading(), Pace.SLOW)
        assert first.kind in ("narration", "silence")
        st = await orch.store.load(sid)
        st.nav.steps = steps
        st.nav.next_step_i = 0
        await orch.store.save(st)

    asyncio.run(run())


def test_cue_preannounce_then_fire_once(monkeypatch):
    orch = _orch(monkeypatch, nav_cue_min_gap_s=0.0)
    _guided_state_with_steps(orch, "n1", [_maneuver_at(90.0)])

    async def run():
        outs = []
        for _ in range(3):
            outs.append(await orch.on_position("n1", ORIGIN, Heading(), Pace.SLOW))
        near = offset_point(ORIGIN, 90.0, 70.0)  # ~20 m short of the turn
        for _ in range(2):
            outs.append(await orch.on_position("n1", near, Heading(), Pace.SLOW))
        st = await orch.store.load("n1")
        return outs, st

    outs, st = asyncio.run(run())
    texts = [o.text for o in outs if o.nav_cue]
    # exactly one pre-announce (inside 110 m) and one at-the-turn command (inside 35 m)
    assert len(texts) == 2
    assert texts[0].startswith("Через ")
    assert texts[1].startswith("Поверни направо")
    assert st.nav.steps[0].pre_said and st.nav.steps[0].said


def test_cue_min_gap_rate_limits(monkeypatch):
    orch = _orch(monkeypatch, nav_cue_min_gap_s=3600.0)
    _guided_state_with_steps(
        orch, "n2", [_maneuver_at(90.0), _maneuver_at(95.0, modifier="left")]
    )

    async def run():
        outs = []
        for _ in range(4):
            outs.append(await orch.on_position("n2", ORIGIN, Heading(), Pace.SLOW))
        return outs

    outs = asyncio.run(run())
    # the huge min-gap lets only the FIRST cue through
    assert sum(1 for o in outs if o.nav_cue) == 1


def test_cues_never_enter_narration_memory(monkeypatch):
    orch = _orch(monkeypatch, nav_cue_min_gap_s=0.0)
    _guided_state_with_steps(orch, "n3", [_maneuver_at(90.0)])

    async def run():
        out = await orch.on_position("n3", ORIGIN, Heading(), Pace.SLOW)
        st = await orch.store.load("n3")
        return out, st

    out, st = asyncio.run(run())
    assert out.nav_cue and out.text
    assert out.text not in st.narration_history
    assert all(out.text not in n for n in st.memory.narrations)


def test_no_steps_no_cues(monkeypatch):
    orch = _orch(monkeypatch, nav_cue_min_gap_s=0.0)
    _guided_state_with_steps(orch, "n4", [])

    async def run():
        outs = []
        for _ in range(3):
            outs.append(await orch.on_position("n4", ORIGIN, Heading(), Pace.SLOW))
        return outs

    outs = asyncio.run(run())
    assert not any(o.nav_cue for o in outs)  # straight-line route => chip-only leading


def test_unmapped_maneuver_is_skipped_not_blocking(monkeypatch):
    orch = _orch(monkeypatch, nav_cue_min_gap_s=0.0)
    _guided_state_with_steps(
        orch, "n5",
        [_maneuver_at(20.0, kind="rotary", modifier="weird", name=""),
         _maneuver_at(90.0)],
    )

    async def run():
        outs = []
        for _ in range(3):
            outs.append(await orch.on_position("n5", ORIGIN, Heading(), Pace.SLOW))
        st = await orch.store.load("n5")
        return outs, st

    outs, st = asyncio.run(run())
    cues = [o.text for o in outs if o.nav_cue]
    # the unmapped first maneuver is retired silently; the real turn still cues
    assert st.nav.steps[0].said
    assert cues and all("Поверни направо" in c or c.startswith("Через ") for c in cues)


def test_cue_state_survives_resume(monkeypatch):
    # Old persisted sessions (no steps field) parse; said-flags persist across loads.
    from app.shared.schemas import NavState, SessionState

    st = SessionState(session_id="x")
    payload = st.model_dump()
    payload["nav"].pop("steps", None)
    payload["nav"].pop("next_step_i", None)
    payload["nav"].pop("last_cue_at", None)
    revived = SessionState.model_validate(payload)
    assert revived.nav.steps == [] and revived.nav.next_step_i == 0
    nav = NavState(steps=[NavManeuver(kind="turn", modifier="left", said=True)])
    assert NavState.model_validate(nav.model_dump()).steps[0].said is True


def test_nav_cue_engine_walked_past_retires(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "nav_cue_min_gap_s", 0.0)
    from app.shared.schemas import SessionState

    st = SessionState(session_id="w")
    st.nav.steps = [_maneuver_at(0.0)]  # maneuver AT origin
    st.nav.steps[0].pre_said = True
    far = offset_point(ORIGIN, 90.0, 400.0)  # walked well past it
    cue, _urgent = Orchestrator._nav_cue_text(st, far)
    assert cue == ""
    assert st.nav.steps[0].said  # retired, never blocks the next maneuver
