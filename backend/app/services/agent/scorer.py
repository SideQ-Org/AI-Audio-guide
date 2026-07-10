"""Scorer role: rank candidates and pick the next place to narrate.

Two implementations behind a common ``Scorer`` protocol:
  * HeuristicScorer — deterministic, no LLM (offline sim / the cheap gate)
  * LLMScorer       — structured JSON via an LLMClient (production hot-path)
"""

from __future__ import annotations

from typing import Protocol

from app.services.llm.client import LLMClient
from app.services.llm.router import Role
from app.shared.schemas import ScoredPlace, ScorerInput, ScorerOutput, Significance

from .prompts import build_scorer_user, system_for
from .significance import at_least, significance_from_weight

# JSON schema for the structured Scorer output (strict: additionalProperties=false).
SCORER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "scored": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "place_id": {"type": "string"},
                    "significance": {
                        "type": "string",
                        "enum": ["SKIP", "LOW", "MEDIUM", "HIGH", "LANDMARK"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["place_id", "significance", "reason"],
                "additionalProperties": False,
            },
        },
        "next": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "expand_radius": {"type": "boolean"},
    },
    "required": ["scored", "next", "expand_radius"],
    "additionalProperties": False,
}


class Scorer(Protocol):
    async def score(self, inp: ScorerInput) -> ScorerOutput: ...


class HeuristicScorer:
    """Deterministic ranking — also the cheap gate that decides whether the
    LLM scorer is even worth calling."""

    async def score(self, inp: ScorerInput) -> ScorerOutput:
        seen = set(inp.seen)
        skip = set(inp.preferences.skip_categories) if inp.preferences else set()

        scored: list[ScoredPlace] = []
        best: ScoredPlace | None = None
        for c in inp.candidates:
            if c.place.category in skip:
                sig = Significance.SKIP
            else:
                sig = significance_from_weight(c.type_weight, c.facts_available)
            scored.append(ScoredPlace(place_id=c.place.id, significance=sig, reason=""))
            # candidates arrive pre-sorted by score; first acceptable unseen wins
            if best is None and c.place.id not in seen and at_least(sig, Significance.LOW):
                best = scored[-1]

        next_id = best.place_id if best and at_least(best.significance, Significance.LOW) else None
        return ScorerOutput(
            scored=scored,
            next=next_id,
            expand_radius=not inp.candidates,
        )


class LLMScorer:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def score(self, inp: ScorerInput) -> ScorerOutput:
        system = system_for(Role.SCORER, inp.language)
        user = build_scorer_user(inp)
        data = await self._llm.complete_json(Role.SCORER, system, user, SCORER_SCHEMA)
        return ScorerOutput.model_validate(data)
