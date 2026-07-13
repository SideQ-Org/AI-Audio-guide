"""Streaming barge-in Companion: sentence-cutting + heuristic steering.

Exercises LLMCompanion.respond_stream against a fake streaming LLM (no network) so the
sentence-boundary emission and the heuristic control_patch stay covered by the offline gate.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.services.agent.companion import LLMCompanion, heuristic_patch
from app.shared.schemas import CompanionInput


class _FakeStreamLLM:
    """Yields a reply in small deltas that straddle sentence boundaries mid-chunk."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def stream_text(self, role, system, user, *, max_tokens=400) -> AsyncIterator[str]:
        for c in self._chunks:
            yield c


def _collect(chunks: list[str]) -> list[str]:
    comp = LLMCompanion(_FakeStreamLLM(chunks))

    async def run() -> list[str]:
        return [s async for s in comp.respond_stream(CompanionInput(user_message="?"))]

    return asyncio.run(run())


def test_respond_stream_emits_whole_sentences() -> None:
    # Two sentences delivered in fragments that split a sentence across chunks.
    out = _collect(["Улица наз", "вана в честь ", "инженера. Тут ", "жил учёный."])
    assert out == ["Улица названа в честь инженера.", "Тут жил учёный."]


def test_respond_stream_flushes_unterminated_tail() -> None:
    # A reply with no trailing punctuation must still be flushed as the final fragment.
    out = _collect(["Готово", ", идём дальше"])
    assert out == ["Готово, идём дальше"]


def test_heuristic_patch_commands() -> None:
    assert heuristic_patch("пропускай магазины").skip_categories == ["shop", "cafe", "restaurant"]
    assert heuristic_patch("давай покороче").verbosity == "shorter"
    assert heuristic_patch("помолчи немного").mute is True
    assert heuristic_patch("расскажи про эту церковь") is None


# --- the WS send path: reply frames must actually reach the client ------------------------ #
# Regression guard: _answer_streaming once built OrchestratorOutput(kind="reply", ...) WITHOUT
# the required `state` field -> TypeError on every sentence -> the answer was generated but
# NEVER sent (the guide "не говорил в ответ"). This drives the real send loop with a fake ws.


class _FakeStreamCompanion:
    async def respond_stream(self, inp) -> AsyncIterator[str]:
        for s in ["Первое предложение.", "Второе."]:
            yield s


class _FakeOrch:
    def __init__(self) -> None:
        self.companion = _FakeStreamCompanion()
        self.finalized: tuple | None = None

    async def prepare_utterance(self, session_id: str, text: str):
        return object(), CompanionInput(user_message=text)

    async def finalize_utterance(self, st, user_text, reply, control_patch=None):
        self.finalized = (reply, control_patch)


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_json(self, obj) -> None:
        self.sent.append(obj)


def test_answer_streaming_actually_sends_reply_frames() -> None:
    import app.main as m

    async def run():
        orch = _FakeOrch()
        ws = _FakeWS()
        rt = m._SessionRuntime(ws, orch, "sid")  # tier defaults to "free" -> no neural synth
        handled = await rt._answer_streaming("кто это?")
        return handled, ws.sent, orch.finalized

    handled, sent, finalized = asyncio.run(run())
    replies = [o for o in sent if o.get("type") == "reply"]
    assert handled is True
    # one reply frame per streamed sentence — the bug sent ZERO
    assert [r["text"] for r in replies] == ["Первое предложение.", "Второе."]
    # every reply is preceded by a valid state frame (the field that was missing)
    assert any(o.get("type") == "state" and o.get("state") == "answering" for o in sent)
    # the whole answer is finalized into session state
    assert finalized is not None and finalized[0] == "Первое предложение. Второе."
