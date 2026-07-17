"""Tour scripter: plan the narration arc for a WHOLE guided route at once.

Unlike the reactive guide (which meets objects one at a time and can't see ahead), a
guided walk knows its entire route up front — the ordered stops with their significance
and (pre-loaded) facts. So the guide can plan one coherent tour: a through-line theme, an
opening overview, the role each stop plays in the story, the transitions/anticipations
between them, and a closing word — instead of narrating each stop in isolation.

This is the "one LLM call over the whole route" pass (mirrors summarizer.py, but takes the
STOP LIST in and emits a structured per-stop scenario out). The per-stop spoken text is
still generated per stop with this scenario as context (see orchestrator warm/step) — this
only sets the vector.

Two implementations behind a common ``TourScripter`` protocol:
  * HeuristicTourScripter — deterministic, no LLM (offline sim / tests / fallback)
  * LLMTourScripter       — structured JSON via an LLMClient (production)
"""

from __future__ import annotations

from typing import Protocol

from app.services.llm.client import LLMClient
from app.services.llm.router import Role
from app.shared.schemas import RouteScript, RouteScriptInput, StopBeat

from .prompts import build_scripter_user, system_for_scripter

ROUTE_SCRIPT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "theme": {"type": "string"},
        "intro": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "order": {"type": "integer"},
                    "angle": {"type": "string"},
                    "bridge": {"type": "string"},
                    "callback": {"type": "string"},
                },
                "required": ["order", "angle", "bridge", "callback"],
                "additionalProperties": False,
            },
        },
        "finale": {"type": "string"},
    },
    "required": ["theme", "intro", "beats", "finale"],
    "additionalProperties": False,
}


class TourScripter(Protocol):
    async def script(self, inp: RouteScriptInput) -> RouteScript: ...


def _area_name(inp: RouteScriptInput) -> str:
    a = inp.address
    return a.district or a.city or a.street or ""


class HeuristicTourScripter:
    """Deterministic arc — a plain intro naming the stops, one generic beat per stop, a plain
    finale. No facts are invented (a factless stop's beat only names it). Offline / fallback."""

    async def script(self, inp: RouteScriptInput) -> RouteScript:
        area = _area_name(inp)
        names = [s.name for s in inp.stops]
        theme = inp.theme_override or (
            f"{area}: прогулка по интересным местам" if area else "Прогулка по интересным местам"
        )
        head = "Пройдём небольшой маршрут" + (f" по {area}" if area else "") + "."
        tail = (" Нас ждут: " + ", ".join(names[:4]) + ".") if names else ""
        intro = (head + tail).strip()
        beats: list[StopBeat] = []
        for i, s in enumerate(inp.stops):
            nxt = inp.stops[i + 1].name if i + 1 < len(inp.stops) else ""
            angle = f"расскажи о «{s.name}»"
            if not s.facts:
                angle += " (только назови и опиши, что видно, без выдуманной истории)"
            beats.append(
                StopBeat(
                    order=i,
                    angle=angle,
                    bridge=(f"Дальше идём к: {nxt}." if nxt else "Это последняя точка маршрута."),
                    callback="",
                )
            )
        finale = "На этом наша прогулка завершается. Спасибо, что были рядом!"
        return RouteScript(theme=theme, intro=intro, beats=beats, finale=finale)


class LLMTourScripter:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def script(self, inp: RouteScriptInput) -> RouteScript:
        # Metered under the NARRATOR role (it's the guide's own voice planning the tour; no
        # router change, like LLMPlanner reusing SCORER). A generous token budget: the whole
        # route's arc + per-stop beats in one structured reply.
        system = system_for_scripter(inp.language)
        user = build_scripter_user(inp)
        data = await self._llm.complete_json(
            Role.NARRATOR, system, user, ROUTE_SCRIPT_SCHEMA, max_tokens=1600
        )
        out = RouteScript.model_validate(data)
        if inp.theme_override:  # the user's chosen topic always wins
            out.theme = inp.theme_override
        return out
