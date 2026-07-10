"""Tier-aware model routing, reasoning gating, and per-model cost pricing.

Offline: exercises the pure resolver methods on ``OpenAICompatLLM`` and the
``TokenMeter`` estimator directly — no network, no key. (feature: account tiers)
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.services.llm.client import (
    SESSION_TIER,
    OpenAICompatLLM,
    TokenMeter,
    _prices_for,
)
from app.services.llm.router import Role


def _with_tier_settings(fn):
    """Run ``fn`` with free=DeepSeek / paid=Gemini wired and a reasoning cap set,
    restoring the global settings afterwards."""
    saved = {
        k: getattr(settings, k)
        for k in (
            "openai_model",
            "openai_model_paid",
            "openai_reasoning_max_tokens",
            "openai_reasoning_effort",
            "openai_price_in_per_mtok",
            "openai_price_out_per_mtok",
            "openai_price_in_per_mtok_paid",
            "openai_price_out_per_mtok_paid",
        )
    }
    settings.openai_model = "deepseek/deepseek-chat"
    settings.openai_model_paid = "google/gemini-3.5-flash"
    settings.openai_reasoning_max_tokens = 64
    settings.openai_reasoning_effort = ""
    settings.openai_price_in_per_mtok = 0.3
    settings.openai_price_out_per_mtok = 0.9
    settings.openai_price_in_per_mtok_paid = 1.5
    settings.openai_price_out_per_mtok_paid = 9.0
    tok = SESSION_TIER.set("free")
    try:
        return fn()
    finally:
        SESSION_TIER.reset(tok)
        for k, v in saved.items():
            setattr(settings, k, v)


def test_model_resolves_by_tier():
    def run():
        llm = OpenAICompatLLM(base_url="http://x/v1", api_key="k")
        free = llm._model_for(Role.NARRATOR)
        SESSION_TIER.set("paid")
        paid = llm._model_for(Role.NARRATOR)
        paid_scorer = llm._model_for(Role.SCORER)  # paid uses premium on EVERY role
        asyncio.run(llm._client.aclose())
        return free, paid, paid_scorer

    free, paid, paid_scorer = _with_tier_settings(run)
    assert free == "deepseek/deepseek-chat"
    assert paid == "google/gemini-3.5-flash"
    assert paid_scorer == "google/gemini-3.5-flash"


def test_reasoning_only_for_paid_when_tiers_on():
    def run():
        llm = OpenAICompatLLM(base_url="http://x/v1", api_key="k")
        free = llm._reasoning_for(Role.NARRATOR)  # DeepSeek: never send reasoning
        SESSION_TIER.set("paid")
        paid_narr = llm._reasoning_for(Role.NARRATOR)  # capped role
        paid_comp = llm._reasoning_for(Role.COMPANION)  # uncapped, no effort => None
        asyncio.run(llm._client.aclose())
        return free, paid_narr, paid_comp

    free, paid_narr, paid_comp = _with_tier_settings(run)
    assert free is None
    assert paid_narr == {"max_tokens": 64}
    assert paid_comp is None


def test_prices_and_estimate_are_per_model():
    def run():
        assert _prices_for("google/gemini-3.5-flash") == (1.5, 9.0)
        assert _prices_for("deepseek/deepseek-chat") == (0.3, 0.9)
        m = TokenMeter()
        # A costless (provider omitted cost) call on each model estimates per-model.
        one_mtok_in = {"prompt_tokens": 1_000_000, "completion_tokens": 0}
        m.record(Role.NARRATOR, "deepseek/deepseek-chat", one_mtok_in)
        m.record(Role.NARRATOR, "google/gemini-3.5-flash", one_mtok_in)
        return m.est_cost

    est = _with_tier_settings(run)
    assert abs(est - (0.3 + 1.5)) < 1e-9  # 1 Mtok DeepSeek + 1 Mtok Gemini input
