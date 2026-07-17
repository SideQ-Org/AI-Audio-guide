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

# Walk-level coherence axes (Block 4 coherence extension): judged over the WHOLE ordered sequence
# of blurbs, not one object — the per-blurb panel's blind spot (бесшовность / связность / арка).
WALK_AXES = ("seamlessness", "arc_coherence")

WALK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        **{ax: {"type": "integer", "minimum": 0, "maximum": 4} for ax in WALK_AXES},
    },
    "required": ["rationale", *WALK_AXES],
    "additionalProperties": False,
}

_WALK_TASK = (
    "\n\nРЕЖИМ ПРОГУЛКИ: тебе дан УПОРЯДОЧЕННЫЙ список фрагментов (BLURBS) одной прогулки — "
    "по порядку, как их слышал пешеход. Оцени прогулку КАК ЦЕЛОЕ по двум осям 0–4 и верни JSON "
    "{rationale, seamlessness, arc_coherence}:\n"
    "- seamlessness (бесшовность): плавно ли фрагменты перетекают друг в друга (пространственные/"
    "смысловые связки, «а вот…», «чуть дальше…»), или это набор оборванных карточек с резкими "
    "переходами. 4 — переходы естественны; 0 — каждый фрагмент начинается с нуля.\n"
    "- arc_coherence (связность/арка): читается ли прогулка как одна история с темой и отсылками "
    "(callback к ранее пройденному, единый мотив), или разрозненные факты без общей нити. 4 — есть "
    "тема и связки между объектами; 0 — просто перечень.\n"
    "Оценивай СВЯЗЬ между фрагментами, а не интересность каждого по отдельности. Пустые фрагменты/"
    "[SILENCE] не считаются связками — молчание не бесшовность.\n"
    "Ориентиры: seamlessness=4, arc=4 — «Слева храм XVIII века… // А чуть дальше, как и та "
    "церковь, стоит старая часовня того же прихода… // Впереди усадьба, к которой вела эта "
    "аллея.» (переходы и общая нить). seamlessness=0, arc=0 — «Тут кафе. // Памятник Пушкину. "
    "// Аптека работает с 8.» (три оборванные карточки без связок и темы)."
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


@dataclass
class WalkVerdict:
    """Walk-level coherence verdict (one per walk). Axes are 0-4 as returned by the judge;
    ``score`` normalises to 0-1 (mean of the two axes)."""

    rationale: str
    seamlessness: int
    arc_coherence: int

    @property
    def score(self) -> float:
        return max(0.0, min(1.0, (self.seamlessness + self.arc_coherence) / 8.0))


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
        # A verbose gold judge (e.g. glm-4.6) writes a long CoT rationale; too small a cap truncates
        # the JSON -> empty/invalid response. 2000 leaves room for rationale + the axes/gates.
        data = await self._llm.complete_json(
            Role.JUDGE, system, _user(blurb, facts=facts, context=context, tier=tier),
            JUDGE_SCHEMA, max_tokens=2000,
        )
        return JudgeVerdict(
            rationale=str(data.get("rationale", "")),
            axes={ax: int(data.get(ax, 0)) for ax in AXES},
            grounded=bool(data.get("grounded", False)),
            cliche=bool(data.get("cliche", True)),
            overall=int(data.get("overall", 0)),
        )

    async def score_walk(
        self,
        blurbs: list[str],
        *,
        language: str = "ru",
    ) -> WalkVerdict:
        """Walk-level coherence: judge the ORDERED sequence of blurbs as one narrative (seamlessness
        + arc). Pass only non-silent blurbs (silence isn't smoothness). Blurbs are clipped so a long
        walk fits the judge context. Neutral (0/0) for <2 blurbs — coherence is undefined."""
        clean = [(b or "").strip() for b in blurbs if (b or "").strip()]
        if len(clean) < 2:
            return WalkVerdict(rationale="too few blurbs", seamlessness=0, arc_coherence=0)
        # Clip each blurb + cap the count so a huge walk can't blow the judge context window.
        seq = [b[:280] for b in clean[:40]]
        system = system_for_judge(language) + _WALK_TASK
        data = await self._llm.complete_json(
            Role.JUDGE, system, _json({"BLURBS": seq}), WALK_SCHEMA, max_tokens=1200,
        )
        return WalkVerdict(
            rationale=str(data.get("rationale", "")),
            seamlessness=int(data.get("seamlessness", 0)),
            arc_coherence=int(data.get("arc_coherence", 0)),
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
