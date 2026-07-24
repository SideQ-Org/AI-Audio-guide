"""Narrative-quality fixes from the 17.07 field feedback:

- elaborate is distance-gated («я уже ушёл от суда, а он мне опять про суд»);
- significance-aware ranking + notable-reach-first («про арки, а не про музей»);
- facts-aware reach retire (a museum retired with cold facts gets one retry);
- area beats require NEW facts (no more invented "observed" specifics);
- the fact ledger is symmetric (object narrations feed told_facts too).
"""

from __future__ import annotations

import asyncio

from app.services.agent.factory import build_orchestrator
from app.services.agent.orchestrator import _MAX_ELABORATE, Orchestrator
from app.services.agent.pipeline import StepResult
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import (
    Address,
    Candidate,
    GazeConfidence,
    GeoPoint,
    Pace,
    Place,
    ScorerOutput,
    SessionState,
    Significance,
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


def _place(pid="node/1", name="Объект", cat="museum", lat=55.7539, lon=37.6208) -> Place:
    return Place(id=pid, name=name, category=cat, location=GeoPoint(lat=lat, lon=lon))


def _cand(place, dist, weight, *, cone=True, facts=False) -> Candidate:
    return Candidate(
        place=place, distance_m=dist, type_weight=weight, in_gaze_cone=cone,
        gaze_confidence=GazeConfidence.HIGH, facts_available=facts,
    )


class _CountingElaborate:
    def __init__(self, text="Ещё деталь про это место."):
        self.calls = 0
        self.text = text

    async def __call__(self, *a, **kw):
        self.calls += 1
        return self.text


# --- elaborate distance gate ------------------------------------------------- #


def test_elaborate_closed_after_walking_away(monkeypatch):
    orch = _orch(monkeypatch, elaborate_max_distance_m=90.0, revisit_enabled=False)
    fake = _CountingElaborate()
    monkeypatch.setattr(orch.pipeline, "elaborate", fake)
    st = SessionState(session_id="e1")
    st.last_place = _place(name="Зюзинский суд", cat="courthouse")
    st.last_significance = Significance.MEDIUM
    st.elaboration_count = 0
    # ~200 m north of the object — the walker has left it behind.
    st.position = GeoPoint(lat=ORIGIN.lat + 0.0018, lon=ORIGIN.lon)

    from app.shared.schemas import Heading

    out = asyncio.run(orch._continue_monologue(st, Heading(), Pace.SLOW))
    assert fake.calls == 0, "no more talk about an object the walker left behind"
    assert st.elaboration_count == _MAX_ELABORATE  # topic closed for good
    assert out.kind == "silence"


def test_elaborate_still_runs_beside_the_object(monkeypatch):
    orch = _orch(monkeypatch, elaborate_max_distance_m=90.0, revisit_enabled=False)
    fake = _CountingElaborate()
    monkeypatch.setattr(orch.pipeline, "elaborate", fake)
    st = SessionState(session_id="e2")
    st.last_place = _place()
    st.last_significance = Significance.MEDIUM
    st.elaboration_count = 0
    st.position = GeoPoint(lat=ORIGIN.lat + 0.0003, lon=ORIGIN.lon)  # ~33 m — still beside

    from app.shared.schemas import Heading

    out = asyncio.run(orch._continue_monologue(st, Heading(), Pace.SLOW))
    assert fake.calls == 1
    assert out.kind == "narration" and out.text


# --- significance-aware ranking ---------------------------------------------- #


def test_visible_rank_prefers_museum_over_closer_gym():
    museum = _cand(_place("m", "Музей", "museum"), dist=90, weight=0.9)
    gym = _cand(_place("g", "Спортзал", "sports_centre"), dist=65, weight=0.35)
    assert Orchestrator._visible_rank(museum) < Orchestrator._visible_rank(gym)


def test_visible_rank_adjacent_ordinary_still_wins():
    museum = _cand(_place("m", "Музей", "museum"), dist=90, weight=0.9)
    kiosk = _cand(_place("k", "Киоск", "shop"), dist=20, weight=0.2)
    assert Orchestrator._visible_rank(kiosk) < Orchestrator._visible_rank(museum)


# --- notable reach before area filler ----------------------------------------- #


def test_notable_reach_outranks_area_beat(monkeypatch):
    orch = _orch(monkeypatch)
    st = SessionState(session_id="r1")
    st.address = Address(city="Москва", district="Останкинский")
    st.area_key = "Останкинский"
    st.position = ORIGIN
    st.area_facts = "Факт про район, ещё не рассказанный."

    area_calls = {"n": 0}

    async def _area(*a, **kw):
        area_calls["n"] += 1
        return "AREA BEAT", None

    monkeypatch.setattr(orch.pipeline, "narrate_area", _area)

    museum = _place("m1", "Павильон №1 (Третьяковка)", "museum")

    async def _step(*a, **kw):
        return StepResult(
            "Впереди — павильон с экспозицией Третьяковской галереи.",
            ScorerOutput(), museum, Significance.HIGH,
        )

    monkeypatch.setattr(orch.pipeline, "step", _step)

    from app.shared.schemas import Heading

    reach = [_cand(museum, dist=120, weight=0.9, facts=True)]
    out = asyncio.run(orch._continue_monologue(st, Heading(), Pace.SLOW, reach=reach))
    assert out.kind == "narration" and "Третьяков" in out.text
    assert area_calls["n"] == 0, "the museum must not wait behind district filler"
    assert museum.id in st.seen_place_ids


def test_ordinary_reach_still_waits_for_area(monkeypatch):
    orch = _orch(monkeypatch)
    st = SessionState(session_id="r2")
    st.address = Address(city="Москва", district="Останкинский")
    st.area_key = "Останкинский"
    st.position = ORIGIN
    st.area_facts = "Факт про район, ещё не рассказанный."

    async def _area(*a, **kw):
        return "AREA BEAT", None

    monkeypatch.setattr(orch.pipeline, "narrate_area", _area)

    async def _step(*a, **kw):  # pragma: no cover — must not be reached
        raise AssertionError("ordinary reach must not preempt the area spine")

    monkeypatch.setattr(orch.pipeline, "step", _step)

    from app.shared.schemas import Heading

    shop = _place("s1", "Магазин", "shop")
    reach = [_cand(shop, dist=100, weight=0.3)]
    out = asyncio.run(orch._continue_monologue(st, Heading(), Pace.SLOW, reach=reach))
    assert out.kind == "narration" and out.text == "AREA BEAT"


# --- facts-aware reach retire -------------------------------------------------- #


def test_reach_retired_rearms_when_facts_arrive():
    st = SessionState(session_id="rr")
    st.reach_exhausted_ids = ["m1|0"]  # retired while facts were cold
    cold = _cand(_place("m1", "Музей", "museum"), dist=100, weight=0.9, facts=False)
    warm = _cand(_place("m1", "Музей", "museum"), dist=100, weight=0.9, facts=True)
    assert Orchestrator._reach_retired(st, cold) is True
    assert Orchestrator._reach_retired(st, warm) is False  # facts landed -> one more try

    st.reach_exhausted_ids = ["m1|1"]  # retired WITH facts — nothing more will appear
    assert Orchestrator._reach_retired(st, warm) is True
    st.reach_exhausted_ids = ["m1"]  # legacy bare id blocks unconditionally
    assert Orchestrator._reach_retired(st, warm) is True


def test_reach_limit_wider_for_notable():
    from app.config import settings

    museum = _cand(_place("m", "Музей", "museum"), dist=160, weight=0.9)
    shop = _cand(_place("s", "Магазин", "shop"), dist=160, weight=0.3)
    assert Orchestrator._reach_limit_m(museum) == settings.reach_radius_notable_m
    assert Orchestrator._reach_limit_m(shop) == settings.reach_radius_m


# --- area beats require NEW facts ---------------------------------------------- #


def test_area_beat_skipped_without_new_facts(monkeypatch):
    orch = _orch(monkeypatch, area_enrich=True, area_beat_requires_new_facts=True)
    calls = {"n": 0}

    async def _area(*a, **kw):
        calls["n"] += 1
        return "ВЫДУМАННЫЕ СТОЛБИКИ", None

    monkeypatch.setattr(orch.pipeline, "narrate_area", _area)
    st = SessionState(session_id="a1")
    st.address = Address(city="Долгопрудный", street="Дирижабельная улица")
    st.area_key = "Долгопрудный"
    fact = "На Дирижабельной улице в тридцатые годы строили дирижабли на большом комбинате."
    st.area_facts = fact
    st.memory.mark_facts_told([fact])  # -> new == []

    out = asyncio.run(orch._emit_area_beat(st, "тема улицы", focus=None, pace=Pace.SLOW))
    assert out == ""
    assert calls["n"] == 0, "a factless beat must not reach the LLM (it invents)"
    assert st.area_silent_streak == 1


def test_area_beat_flows_with_new_facts(monkeypatch):
    orch = _orch(monkeypatch, area_enrich=True, area_beat_requires_new_facts=True)

    async def _area(*a, **kw):
        return "Грамотный бит на свежем факте.", None

    monkeypatch.setattr(orch.pipeline, "narrate_area", _area)
    st = SessionState(session_id="a2")
    st.address = Address(city="Долгопрудный")
    st.area_key = "Долгопрудный"
    st.area_facts = "Свежий, ещё не рассказанный факт про дирижабли."

    out = asyncio.run(orch._emit_area_beat(st, "тема", focus=None, pace=Pace.SLOW))
    assert out == "Грамотный бит на свежем факте."


# --- symmetric fact ledger ------------------------------------------------------ #


def test_object_narration_feeds_fact_ledger(monkeypatch):
    orch = _orch(monkeypatch)
    st = SessionState(session_id="l1")
    st.position = ORIGIN
    place = _place("o1", "Главный вход ВДНХ", "attraction")
    out = StepResult(
        "Арку Главного входа ВДНХ построили в пятидесятых, её венчает скульптура.",
        ScorerOutput(), place, Significance.HIGH,
    )
    asyncio.run(orch._commit_step(st, out))
    assert st.memory.told_facts, "the object's spoken facts must enter the ledger"
    # A later area beat re-telling the same fact in different words gets nothing new.
    dup = ["Арка Главного входа ВДНХ, построенная в пятидесятых, увенчана скульптурой."]
    assert st.memory.new_facts(dup) == []
