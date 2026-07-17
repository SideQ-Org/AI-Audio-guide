"""Ф1: TourScripter — plans a whole guided route as one coherent tour.
HeuristicTourScripter is deterministic (offline); LLMTourScripter parses the structured
schema (exercised with FakeLLM). Fully offline."""

from __future__ import annotations

import asyncio

from app.services.agent.tour_scripter import (
    HeuristicTourScripter,
    LLMTourScripter,
)
from app.services.llm.client import FakeLLM
from app.shared.schemas import Address, RouteScriptInput, ScriptStop


def _inp(**kw) -> RouteScriptInput:
    stops = kw.pop("stops", [
        ScriptStop(name="Собор Василия", category="place_of_worship",
                   significance="LANDMARK", facts="X"),
        ScriptStop(name="ГУМ", category="attraction", significance="HIGH", facts="Y"),
        ScriptStop(name="Мавзолей", category="monument", significance="HIGH", facts=None),
    ])
    return RouteScriptInput(stops=stops, address=Address(city="Москва"), **kw)


def test_heuristic_produces_full_arc():
    s = asyncio.run(HeuristicTourScripter().script(_inp()))
    assert s.theme and s.intro and s.finale
    # one beat per stop, in order, each with a bridge.
    assert [b.order for b in s.beats] == [0, 1, 2]
    assert all(b.bridge for b in s.beats)
    # the intro names the first stops (overview).
    assert "Собор Василия" in s.intro


def test_heuristic_factless_stop_stays_grounded():
    s = asyncio.run(HeuristicTourScripter().script(_inp()))
    # Мавзолей has facts=None -> its beat must NOT invite invented history.
    mausoleum = s.beats[2]
    assert "без выдуманной истории" in mausoleum.angle


def test_heuristic_theme_override_wins():
    s = asyncio.run(HeuristicTourScripter().script(_inp(theme_override="архитектура авангарда")))
    assert s.theme == "архитектура авангарда"


def test_llm_scripter_parses_schema():
    payload = {
        "theme": "сердце старой Москвы",
        "intro": "Сегодня пройдём по Красной площади.",
        "beats": [
            {"order": 0, "angle": "главный храм", "bridge": "рядом — ГУМ", "callback": ""},
            {"order": 1, "angle": "торговые ряды", "bridge": "впереди мавзолей",
             "callback": "как у собора"},
            {"order": 2, "angle": "памятник эпохи", "bridge": "на этом всё", "callback": ""},
        ],
        "finale": "Вот и вся прогулка.",
    }
    scripter = LLMTourScripter(FakeLLM(json_response=payload))
    s = asyncio.run(scripter.script(_inp()))
    assert s.theme == "сердце старой Москвы"
    assert len(s.beats) == 3
    assert s.beats[1].callback == "как у собора"
    assert s.finale == "Вот и вся прогулка."


def test_llm_scripter_theme_override_wins():
    payload = {"theme": "модель-тема", "intro": "i", "beats": [], "finale": "f"}
    scripter = LLMTourScripter(FakeLLM(json_response=payload))
    s = asyncio.run(scripter.script(_inp(theme_override="моя тема")))
    assert s.theme == "моя тема"
