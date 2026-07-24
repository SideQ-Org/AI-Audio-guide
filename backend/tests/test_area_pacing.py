"""Block-C pacing fixes: the dry-area gate, the attempts-counted cityless cap, the
street-change level re-arm, the background-only area fetch, and the category cooldown.
These kill the "minutes of [SILENCE]-burning LLM calls" loop from the 17.07 walk logs."""

from __future__ import annotations

import asyncio
import time

from app.services.agent.factory import build_orchestrator
from app.services.agent.orchestrator import Orchestrator
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import (
    Address,
    Candidate,
    GazeConfidence,
    GeoPoint,
    Pace,
    Place,
    SessionState,
)

ORIGIN = GeoPoint(lat=55.7539, lon=37.6208)


def _orch(monkeypatch, **over):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_backend", "heuristic")
    monkeypatch.setattr(settings, "geo_source", "fixture")
    monkeypatch.setattr(settings, "enrichment_source", "mock")
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)
    return build_orchestrator(store=InMemoryStateStore())


def _state(sid="a1", city="Долгопрудный"):
    st = SessionState(session_id=sid)
    st.address = Address(city=city)
    st.area_key = city
    st.position = ORIGIN
    return st


class _CountingNarrate:
    """Fake pipeline.narrate_area that always returns [SILENCE] (empty)."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, *a, **kw):
        self.calls += 1
        return "", None


def test_cityless_cap_counts_silent_attempts(monkeypatch):
    orch = _orch(monkeypatch, area_cityless_max=2, area_dry_max=99)
    fake = _CountingNarrate()
    monkeypatch.setattr(orch.pipeline, "narrate_area", fake)
    st = _state()
    st.area_facts = ""  # factless area -> the grounded-city fallback branch

    async def run():
        outs = [await orch._area_line(st, Pace.SLOW) for _ in range(5)]
        return outs

    outs = asyncio.run(run())
    assert all(o == "" for o in outs)
    # Attempts are counted, and the dry shortcut makes it even cheaper: the FIRST silent
    # try burns the one LLM call; the second is short-circuited (no facts + streak>=1)
    # before the narrator; the cap (2 attempts) then keeps the branch quiet forever.
    assert fake.calls == 1
    assert st.area_cityless_beats == 2


def test_dry_streak_short_circuits_before_llm(monkeypatch):
    orch = _orch(monkeypatch, area_dry_max=3)
    fake = _CountingNarrate()
    monkeypatch.setattr(orch.pipeline, "narrate_area", fake)
    st = _state()
    st.area_facts = "какой-то факт"
    st.area_silent_streak = 3  # at the gate

    assert asyncio.run(orch._area_line(st, Pace.SLOW)) == ""
    assert fake.calls == 0  # no LLM spend on a talked-out area


def test_exhausted_facts_skip_after_first_silence(monkeypatch):
    orch = _orch(monkeypatch, area_dry_max=99)
    fake = _CountingNarrate()
    monkeypatch.setattr(orch.pipeline, "narrate_area", fake)
    st = _state()
    st.area_facts = "факт про липы"
    st.memory.mark_facts_told(["факт про липы"])  # all told -> new == []
    st.area_silent_streak = 1  # the last beat already came back silent

    async def run():
        return await orch._emit_area_beat(st, "тема", focus=None, pace=Pace.SLOW)

    assert asyncio.run(run()) == ""
    assert fake.calls == 0  # dry shortcut fired before the narrate call
    assert st.area_silent_streak == 2


def test_street_change_rearms_street_level_only(monkeypatch):
    orch = _orch(monkeypatch)

    class _Geo:
        async def reverse(self, position, language):
            return Address(city="Долгопрудный", district="Центр", street="Новая улица")

    orch.geocoder = _Geo()
    st = _state()
    st.address = Address(city="Долгопрудный", district="Центр", street="Старая улица")
    st.area_key = "Центр"
    st.last_street = "Старая улица"
    st.area_intro_done = True
    st.area_level = 0
    st.area_silent_streak = 5

    asyncio.run(orch._resolve_area(st, ORIGIN))
    # Same district, new street: jump straight to the STREET level (city/district are
    # covered) instead of re-arming the whole cascade, and re-open the dry gate.
    assert st.area_level == len(orch._area_levels(st)) - 1
    assert st.area_silent_streak == 0


def test_cold_area_facts_skips_beat_and_warms_bg(monkeypatch):
    orch = _orch(monkeypatch, area_enrich=True, area_enrich_inline=False)
    fake = _CountingNarrate()
    monkeypatch.setattr(orch.pipeline, "narrate_area", fake)

    async def _boom(*a, **kw):
        raise AssertionError("inline enrich_area must not be called")

    monkeypatch.setattr(orch.pipeline, "enrich_area", _boom)
    st = _state()
    st.area_facts = None  # not warmed yet

    out = asyncio.run(orch._area_line(st, Pace.SLOW))
    assert out == ""  # beat skipped this tick (no 25 s inline block)
    assert fake.calls == 0


def _cand(cat="library", weight=0.5, facts=False):
    return Candidate(
        place=Place(id="p", name="Библиотека №2", category=cat,
                    location=GeoPoint(lat=1, lon=2)),
        distance_m=30.0, type_weight=weight, in_gaze_cone=False,
        gaze_confidence=GazeConfidence.LOW, facts_available=facts,
    )


def test_category_cooldown_demotes_second_ordinary(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "narrate_category_cooldown_s", 480.0)
    monkeypatch.setattr(settings, "narrate_category_penalty", 2.5)
    st = _state()
    st.last_cat_told = {"library": time.time() - 60.0}  # told a minute ago
    c = _cand()
    assert Orchestrator._cat_in_cooldown(st, c) is True
    assert Orchestrator._cat_cooldown_factor(st, c) == 2.5
    assert Orchestrator._same_cat_angle(st, c)  # the "вторая рядом" director's note
    # facts or a stale ledger lift the demotion
    assert Orchestrator._cat_in_cooldown(st, _cand(facts=True)) is False
    st.last_cat_told = {"library": time.time() - 1000.0}
    assert Orchestrator._cat_in_cooldown(st, c) is False
    assert Orchestrator._same_cat_angle(st, c) is None


def test_deepen_appends_next_angle_and_rearms(monkeypatch):
    """When the current area facts are all told, _maybe_deepen_area pulls the next
    search angle's warmed facts, appends them, and RE-ARMS the monologue (resets the
    dry streak / cascade level) so a lingering walker keeps hearing new material."""
    orch = _orch(monkeypatch, area_enrich=True, area_deepen_max=4, area_deepen_low_facts=1)
    st = _state()
    st.area_facts = "Первый факт про район уже рассказан."
    st.memory.mark_facts_told(["Первый факт про район уже рассказан."])  # untold == []
    st.area_silent_streak = 5  # dry-gate tripped
    st.area_level = 2

    # Round 1 facts are already warmed in the pipeline cache.
    orch.pipeline._area_facts_cache[(st.area_key, st.language, 1)] = (
        "Совершенно новый факт про людей района. И ещё один про события."
    )
    orch._maybe_deepen_area(st)

    assert st.area_fetch_round == 1
    assert "Совершенно новый факт" in st.area_facts
    assert st.area_silent_streak == 0  # re-armed
    assert st.area_level == 0
    state = orch.pipeline.subject_coverage("area", st.area_key, st.language, st.area_facts)
    assert state.deepen_round == 1
    assert state.coverage_facts >= 2
    # the freshly-appended facts are now the untold material
    from app.services.agent.director import atomize_facts
    assert st.memory.new_facts(atomize_facts(st.area_facts))


def test_deepen_kicks_bg_warm_when_next_angle_cold(monkeypatch):
    """If the next angle isn't warmed yet, deepen kicks a background fetch and leaves
    state untouched this tick (the cascade/cityless fallback carries meanwhile)."""
    orch = _orch(monkeypatch, area_enrich=True, area_deepen_max=4, area_deepen_low_facts=1)
    kicked_angles = []

    def _fake_warm(area_key, address, point, language, *, angle=0):
        kicked_angles.append(angle)

    monkeypatch.setattr(orch, "_warm_area_facts_bg", _fake_warm)
    st = _state()
    st.area_facts = "Единственный факт, уже рассказанный."
    st.memory.mark_facts_told(["Единственный факт, уже рассказанный."])
    orch._maybe_deepen_area(st)
    # Prefetch-ahead warms the next angle AND one beyond it (angles 1 and 2).
    assert 1 in kicked_angles  # the immediately-next angle is requested
    assert st.area_fetch_round == 0  # not advanced until facts actually land


def test_deepen_advances_past_a_dry_angle(monkeypatch):
    """A warmed-but-empty ('' = dry) angle advances the round without re-arming, so the
    loop tries the NEXT angle instead of re-fetching the barren one forever."""
    orch = _orch(monkeypatch, area_enrich=True, area_deepen_max=4, area_deepen_low_facts=1)
    st = _state()
    st.area_facts = "Факт рассказан."
    st.memory.mark_facts_told(["Факт рассказан."])
    st.area_silent_streak = 5
    orch.pipeline._area_facts_cache[(st.area_key, st.language, 1)] = ""  # dry angle
    orch._maybe_deepen_area(st)
    assert st.area_fetch_round == 1  # advanced past the dry angle
    assert st.area_silent_streak == 5  # NOT re-armed (nothing new to say)
