"""Companion role: answer a barge-in question and optionally steer the tour.

  * HeuristicCompanion — keyword-based control_patch + canned reply (offline)
  * LLMCompanion       — reply + control_patch via an LLMClient (production)
"""

from __future__ import annotations

from typing import Protocol

from app.services.llm.client import LLMClient
from app.services.llm.router import Role
from app.shared.schemas import CompanionInput, CompanionOutput, ControlPatch

from .prompts import build_companion_user, system_for

COMPANION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "control_patch": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "skip_categories": {"type": "array", "items": {"type": "string"}},
                        "focus_topics": {"type": "array", "items": {"type": "string"}},
                        "verbosity": {
                            "anyOf": [
                                {"type": "null"},
                                {"type": "string", "enum": ["shorter", "normal", "longer"]},
                            ]
                        },
                        "mute": {"type": "boolean"},
                    },
                    "required": ["skip_categories", "focus_topics", "verbosity", "mute"],
                    "additionalProperties": False,
                },
            ]
        },
    },
    "required": ["reply", "control_patch"],
    "additionalProperties": False,
}


class Companion(Protocol):
    async def respond(self, inp: CompanionInput) -> CompanionOutput: ...


class HeuristicCompanion:
    """Tiny RU keyword steering — enough to exercise barge-in offline."""

    async def respond(self, inp: CompanionInput) -> CompanionOutput:
        msg = inp.user_message.lower()
        patch: ControlPatch | None = None
        if "магазин" in msg and ("пропуск" in msg or "не " in msg):
            patch = ControlPatch(skip_categories=["shop", "cafe", "restaurant"])
        elif "короче" in msg or "покороче" in msg:
            patch = ControlPatch(verbosity="shorter")
        elif "помолчи" in msg or "тише" in msg:
            patch = ControlPatch(mute=True)

        # End on a statement, never a soliciting offer ("сейчас расскажу подробнее?") —
        # the companion answers and the tour resumes on its own (A2).
        reply = "Понял, дальше так и сделаю." if patch else "Понял."
        return CompanionOutput(reply=reply, control_patch=patch)


class LLMCompanion:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def respond(self, inp: CompanionInput) -> CompanionOutput:
        system = system_for(Role.COMPANION, inp.language)
        user = build_companion_user(inp)
        data = await self._llm.complete_json(Role.COMPANION, system, user, COMPANION_SCHEMA)
        return CompanionOutput.model_validate(data)
