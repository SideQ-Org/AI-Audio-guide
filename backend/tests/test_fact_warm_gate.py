"""Fix #3 — aggressive-research knobs + config-patch mechanism (Block 4). Offline."""

from __future__ import annotations

from app.services.agent.pipeline import _fact_warm_gate
from app.services.llm.client import SESSION_TIER
from app.shared.schemas import Significance
from sim.prompt_optimize import apply_config_patch


def test_default_gating_is_paid_and_medium():
    # default knobs (paid + MEDIUM): a free-tier session gets NO research (old behaviour)
    tok = SESSION_TIER.set("free")
    try:
        assert _fact_warm_gate(Significance.MEDIUM) is False
        assert _fact_warm_gate(Significance.LOW) is False
    finally:
        SESSION_TIER.reset(tok)


def test_paid_session_researches_medium_by_default():
    tok = SESSION_TIER.set("paid")
    try:
        assert _fact_warm_gate(Significance.MEDIUM) is True    # paid + MEDIUM -> research
        # LOW is researched too since the «пусть ищет все» widening (fact_warm_sig_min
        # default LOW): ordinary-but-real objects get background facts; the paid-tier
        # gate still bounds the spend and rank_facts keeps only the best on top.
        assert _fact_warm_gate(Significance.LOW) is True
    finally:
        SESSION_TIER.reset(tok)


def test_config_patch_widens_research_to_free_and_low():
    # fix #3: broaden the knobs so the free tier researches facts-less LOW objects too
    tok = SESSION_TIER.set("free")
    try:
        with apply_config_patch({"fact_warm_tier_min": "free", "fact_warm_sig_min": "LOW"}):
            assert _fact_warm_gate(Significance.LOW) is True    # now research even LOW on free
        # reverted after the context
        assert _fact_warm_gate(Significance.LOW) is False
    finally:
        SESSION_TIER.reset(tok)


def test_apply_config_patch_reverts_and_ignores_unknown_keys():
    from app.config import settings
    before = settings.fact_warm_tier_min
    with apply_config_patch({"fact_warm_tier_min": "free", "no_such_setting": 123}):
        assert settings.fact_warm_tier_min == "free"
        assert not hasattr(settings, "no_such_setting")        # unknown key ignored, not set
    assert settings.fact_warm_tier_min == before               # restored
