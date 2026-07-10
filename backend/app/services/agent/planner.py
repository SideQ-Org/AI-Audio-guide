"""Planner role: form the story arc for a freshly entered area.

The walk's narration is structured "от общего к частному" around a single
through-line per area. When the guide enters a new district, the Planner picks a
theme, a short ordered outline of topics to cover, and the spoken opener that
introduces the area + theme. Objects encountered along the route are then woven
INTO this arc (by the Narrator), and the outline is advanced between objects.

Two implementations behind a common ``Planner`` protocol:
  * HeuristicPlanner — deterministic, no LLM (offline sim / tests / fallback)
  * LLMPlanner       — structured JSON via an LLMClient (production)
"""

from __future__ import annotations

from typing import Protocol

from app.services.llm.client import LLMClient
from app.services.llm.router import Role
from app.shared.schemas import PlannerInput, PlannerOutput

from .prompts import build_planner_user, system_for_planner

PLANNER_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "theme": {"type": "string"},
        "outline": {"type": "array", "items": {"type": "string"}},
        "opener": {"type": "string"},
    },
    "required": ["theme", "outline", "opener"],
    "additionalProperties": False,
}


class Planner(Protocol):
    async def plan(self, inp: PlannerInput) -> PlannerOutput: ...


def _area_name(inp: PlannerInput) -> str:
    a = inp.address
    return a.district or a.city or a.street or ""


class HeuristicPlanner:
    """Deterministic arc — names the area and lays out generic topics. Used for
    offline sim/tests and as the fallback when no LLM is configured."""

    async def plan(self, inp: PlannerInput) -> PlannerOutput:
        area = _area_name(inp)
        theme = inp.theme_override or (f"{area}: чем живёт это место" if area else "")
        opener = f"Идём по {area}." if area else ""
        outline = ["как возникло это место", "облик улиц", "чем известно", "люди и истории"]
        return PlannerOutput(theme=theme, outline=outline, opener=opener)


class LLMPlanner:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def plan(self, inp: PlannerInput) -> PlannerOutput:
        # metered under the SCORER role (a cheap structured-JSON call; no router change)
        system = system_for_planner(inp.language)
        user = build_planner_user(inp)
        data = await self._llm.complete_json(Role.SCORER, system, user, PLANNER_SCHEMA)
        out = PlannerOutput.model_validate(data)
        if inp.theme_override:  # the user's chosen topic always wins
            out.theme = inp.theme_override
        return out
