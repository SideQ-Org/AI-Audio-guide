"""Companion role: answer a barge-in question and optionally steer the tour.

  * HeuristicCompanion — keyword-based control_patch + canned reply (offline)
  * LLMCompanion       — reply + control_patch via an LLMClient (production)
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Protocol

from app.services.llm.client import LLMClient
from app.services.llm.router import Role
from app.shared.schemas import CompanionInput, CompanionOutput, ControlPatch

from .prompts import (
    build_companion_user,
    system_for,
    system_for_companion_stream,
)


def heuristic_patch(user_message: str) -> ControlPatch | None:
    """Keyword-based tour steering from a voice/text command. Used by HeuristicCompanion
    (offline) AND the streaming path (which can't emit an LLM control_patch mid-stream).
    Covers the impactful commands — skip shops / shorter / mute; open-ended focus_topics is
    intentionally NOT re-queued from a barge-in anyway (see Orchestrator.on_utterance)."""
    msg = user_message.lower()
    if "магазин" in msg and ("пропуск" in msg or "не " in msg):
        return ControlPatch(skip_categories=["shop", "cafe", "restaurant"])
    if "короче" in msg or "покороче" in msg:
        return ControlPatch(verbosity="shorter")
    if "помолчи" in msg or "тише" in msg:
        return ControlPatch(mute=True)
    return None


# First sentence-ending punctuation followed by whitespace — so the streaming reply can be
# cut at clause boundaries and each sentence handed to TTS the moment it closes.
_SENT_END = re.compile(r"[.!?…](?=\s)")

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
        patch = heuristic_patch(inp.user_message)
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

    async def respond_stream(self, inp: CompanionInput) -> AsyncIterator[str]:
        """Yield the reply one sentence at a time as the LLM streams it, so the client can
        speak the first sentence within ~2 s rather than after the whole (~8 s) answer.

        Plain-text prompt (system_for_companion_stream); tour steering is derived by the
        caller via heuristic_patch. Requires an LLMClient with stream_text — callers that
        may hit a non-streaming backend must catch the AttributeError and fall back to
        respond(). Sentences are cut at punctuation; a trailing fragment is flushed at end."""
        system = system_for_companion_stream(inp.language)
        user = build_companion_user(inp)
        buf = ""
        async for piece in self._llm.stream_text(Role.COMPANION, system, user, max_tokens=400):
            buf += piece
            m = _SENT_END.search(buf)
            while m:
                sent, buf = buf[: m.end()].strip(), buf[m.end():].lstrip()
                if sent:
                    yield sent
                m = _SENT_END.search(buf)
        tail = buf.strip()
        if tail:
            yield tail
