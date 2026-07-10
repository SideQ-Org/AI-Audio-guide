"""Live checks against a local OpenAI-compatible model (LM Studio).

Skipped automatically when the server at OPENAI_BASE_URL is unreachable, so the
default suite stays offline-green. Run LM Studio to exercise these.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import settings
from app.services.agent.companion import LLMCompanion
from app.services.agent.narrator import LLMNarrator
from app.services.agent.scorer import LLMScorer
from app.services.llm.client import OpenAICompatLLM
from app.shared.schemas import (
    Candidate,
    CompanionInput,
    GazeConfidence,
    GeoPoint,
    Heading,
    NarratorFlags,
    NarratorInput,
    Place,
    ScorerInput,
    Significance,
)


def _reachable() -> bool:
    try:
        httpx.get(settings.openai_base_url.rstrip("/") + "/models", timeout=2.0)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (settings.openai_model and _reachable()),
    reason="LM Studio / OpenAI-compatible model not reachable",
)


def _llm() -> OpenAICompatLLM:
    return OpenAICompatLLM()


def _cand(pid, name, category, weight, facts=None) -> Candidate:
    return Candidate(
        place=Place(id=pid, name=name, category=category, location=GeoPoint(lat=55.75, lon=37.62)),
        distance_m=20.0,
        type_weight=weight,
        in_gaze_cone=True,
        gaze_confidence=GazeConfidence.LOW,
        facts_available=facts is not None,
        facts_snippet=facts,
    )


def _narr(**kw) -> NarratorInput:
    base = dict(
        place=Place(id="p", name="Музей", category="museum", location=GeoPoint(lat=1, lon=2)),
        significance=Significance.HIGH,
        facts="Краснокирпичный музей конца девятнадцатого века.",
        distance_m=30.0,
        heading=Heading(direction_deg=90.0, gaze_confidence=GazeConfidence.LOW),
    )
    base.update(kw)
    return NarratorInput(**base)


def test_scorer_returns_valid_choice():
    out = asyncio.run(
        LLMScorer(_llm()).score(
            ScorerInput(
                candidates=[
                    _cand("shop", "ГУМ", "shop", 0.25),
                    _cand("mus", "Музей", "museum", 0.9, facts="Большой музей."),
                ]
            )
        )
    )
    ids = {s.place_id for s in out.scored}
    assert ids == {"shop", "mus"}
    assert out.next in (None, "shop", "mus")


def test_narrator_silent_when_nothing_new():
    text = asyncio.run(
        LLMNarrator(_llm()).narrate(
            _narr(facts=None, significance=Significance.LOW, flags=NarratorFlags(nothing_new=True))
        )
    )
    assert text == ""  # [SILENCE] normalized to empty


def test_companion_extracts_skip_shops():
    out = asyncio.run(
        LLMCompanion(_llm()).respond(CompanionInput(user_message="пожалуйста, пропускай магазины"))
    )
    assert out.reply
    if out.control_patch:
        assert out.control_patch.skip_categories or out.control_patch.mute is False
