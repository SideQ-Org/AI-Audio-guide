"""Live check for the interestingness judge (Block 4 Phase 2).

Skipped automatically when no OpenAI-compatible model is reachable, so the default suite
stays offline-green. Run against a reachable judge model (config.openai_model_judge, a
NON-generator family) to sanity-check the rubric end-to-end: a rich factual blurb should
out-score an empty clichéd one, and an ungrounded blurb should trip the groundedness gate.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import settings
from app.services.agent.interest_judge import LLMJudge
from app.services.llm.client import OpenAICompatLLM


def _reachable() -> bool:
    try:
        httpx.get(settings.openai_base_url.rstrip("/") + "/models", timeout=2.0)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (settings.openai_model and _reachable()),
    reason="no OpenAI-compatible judge model reachable",
)


def _judge() -> LLMJudge:
    return LLMJudge(OpenAICompatLLM())


def test_judge_prefers_rich_over_cliche():
    rich = (
        "Тело Ленина забальзамировали в 1924-м, а в начале шестидесятых "
        "тайно перезахоронили тут же."
    )
    dull = "Здесь время будто застыло, и всё вокруг дышит историей этого места."
    facts = "Ленин, бальзамирование 1924, перезахоронение в 1960-е."
    vr = asyncio.run(_judge().score(rich, facts=facts))
    vd = asyncio.run(_judge().score(dull))
    assert vr.score > vd.score


def test_judge_flags_ungrounded_when_no_facts():
    fabricated = "Этот детский сад построили в тридцатые годы специально для рабочих завода."
    v = asyncio.run(_judge().score(fabricated, facts=None))
    assert not v.grounded or v.score <= 0.25
