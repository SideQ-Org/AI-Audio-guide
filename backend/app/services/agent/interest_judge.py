"""LLM judge for interestingness — the G-Eval anchor (Block 4 Part A3).

Reference-free rubric scoring: chain-of-thought (``rationale``) → per-axis 0-4 scores →
hard-gates (``grounded`` / ``cliche``). Two modes, both bias-aware:

  * ``score``   — POINTWISE, for monitoring/CI (a stable per-blurb number).
  * ``compare`` — PAIRWISE with ORDER-SWAP (run both orders, average), for A/B selection of
    prompt/model variants — pairwise is far harder to game than an absolute 1-5 scale.

Bias mitigations baked in (LLM-judge literature):
  * self-preference → the JUDGE role routes to a DIFFERENT model family than the generator
    (see client._model_for / config.model_judge); never the paid generator.
  * position → ``compare`` swaps order and reconciles; disagreement ⇒ tie.
  * verbosity/length → the rubric tells the judge to ignore length (length-control).
  * score clustering → we ask for integer axes + gates and, in code, let the hard-gates
    dominate (an ungrounded/clichéd blurb can't score high no matter how "novel").

NOTE — logprob-weighting (G-Eval's sub-integer trick) is NOT implemented: it needs a new
logprob-returning path in the client, and the frontier judge it most helps is unreachable
under our regional geoblock. We compensate with pairwise selection + human calibration
(human_calib.py). This is a documented tradeoff, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.llm.client import LLMClient
from app.services.llm.router import Role

from .prompts import _json, system_for_judge

AXES = ("novelty", "specificity", "hook", "vividness", "in_place", "speakability")

# Strict structured output for the pointwise judge.
JUDGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        **{ax: {"type": "integer", "minimum": 0, "maximum": 4} for ax in AXES},
        "grounded": {"type": "boolean"},
        "cliche": {"type": "boolean"},
        "overall": {"type": "integer", "minimum": 0, "maximum": 4},
    },
    "required": ["rationale", *AXES, "grounded", "cliche", "overall"],
    "additionalProperties": False,
}

PAIRWISE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
    },
    "required": ["rationale", "winner"],
    "additionalProperties": False,
}

_PAIRWISE_TASK = (
    "\n\nРЕЖИМ СРАВНЕНИЯ: тебе даны ДВА фрагмента, A и B, про одно и то же место. Реши, какой "
    "ИНТЕРЕСНЕЕ для любопытного пешехода по тем же критериям и жёстким гейтам. Игнорируй длину и "
    "порядок предъявления. Верни JSON {rationale, winner: 'A'|'B'|'tie'}. 'tie' — если "
    "неотличимо или оба нарушают гейты."
)


@dataclass
class JudgeVerdict:
    rationale: str
    axes: dict[str, int]
    grounded: bool
    cliche: bool
    overall: int  # 0-4 as returned by the judge

    @property
    def gates_ok(self) -> bool:
        return self.grounded and not self.cliche

    @property
    def score(self) -> float:
        """0-1 anchor score. Hard-gates dominate: a blurb that fabricates or clichés is
        floored regardless of its axes (the 'facts only, no cliché' invariant as reward)."""
        base = self.overall / 4.0
        return min(base, 0.25) if not self.gates_ok else base


def _user(blurb: str, *, facts: str | None, context: dict | None, tier: str | None) -> str:
    return _json({
        "BLURB": blurb,
        "FACTS": (facts or None),
        "CONTEXT": (context or None),
        # free|paid — which model class wrote this. Hard-gates are tier-INDEPENDENT (facts-only
        # is universal); tier only calibrates the INTEREST expectation fairly for the model.
        "TIER": tier,
    })


class LLMJudge:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def score(
        self,
        blurb: str,
        *,
        facts: str | None = None,
        context: dict | None = None,
        language: str = "ru",
        tier: str | None = None,
    ) -> JudgeVerdict:
        """Pointwise rubric score for one blurb. ``tier`` (free|paid) tells the judge which model
        class wrote it, so interest is judged fairly for the tier — hard-gates are unaffected."""
        system = system_for_judge(language)
        data = await self._llm.complete_json(
            Role.JUDGE, system, _user(blurb, facts=facts, context=context, tier=tier), JUDGE_SCHEMA
        )
        return JudgeVerdict(
            rationale=str(data.get("rationale", "")),
            axes={ax: int(data.get(ax, 0)) for ax in AXES},
            grounded=bool(data.get("grounded", False)),
            cliche=bool(data.get("cliche", True)),
            overall=int(data.get("overall", 0)),
        )

    async def _compare_once(
        self, a: str, b: str, *, facts: str | None, context: dict | None, language: str
    ) -> str:
        system = system_for_judge(language) + _PAIRWISE_TASK
        user = _json({
            "A": a, "B": b, "FACTS": (facts or None), "CONTEXT": (context or None),
        })
        data = await self._llm.complete_json(Role.JUDGE, system, user, PAIRWISE_SCHEMA)
        return str(data.get("winner", "tie"))

    async def compare(
        self,
        a: str,
        b: str,
        *,
        facts: str | None = None,
        context: dict | None = None,
        language: str = "ru",
    ) -> str:
        """Pairwise A/B with order-swap. Runs both orders and reconciles; returns
        'A' | 'B' | 'tie'. A consistent winner across both orders wins; any disagreement
        (position bias) resolves to 'tie'."""
        w1 = await self._compare_once(a, b, facts=facts, context=context, language=language)
        # Swap the presentation order; a "A" here means the ORIGINAL b won.
        w2_raw = await self._compare_once(b, a, facts=facts, context=context, language=language)
        w2 = {"A": "B", "B": "A", "tie": "tie"}[w2_raw]
        if w1 == w2:
            return w1
        return "tie"
