"""End-of-walk summary — one post-walk LLM call that turns everything the guide said into a
short, STRUCTURED recap (theme · key places · curious facts), shown on the Stop sheet.

Grounded strictly in what was narrated (``WalkMemory.narrations``) — it recaps, never invents.
Off the hot path: fired once when the walk ends, delivered async over the WS.
"""

from __future__ import annotations

from typing import Protocol

from app.services.agent.languages import prompt_language
from app.services.llm.client import LLMClient, Role
from app.shared.schemas import Address

# One standalone instruction (not the per-tick CORE): the summary is read, not spoken, so light
# structure is welcome. The {language} rule mirrors CORE — always answer in the session language.
_SYSTEM = (
    "You write a SHORT, structured recap of a walking audio-tour, for the user to read on the "
    "end-of-walk screen. You are given the fragments the guide actually narrated during the walk. "
    "Produce a tight recap in {language}:\n"
    "- 2–4 short thematic parts (e.g. the through-line of the area, the key places, the most "
    "curious facts).\n"
    "- Use short bold-free headers or bullet dashes so it scans well; plain text, no markdown "
    "symbols beyond a leading '— '.\n"
    "- Lively and concise, no filler, no repetition.\n"
    "- STRICTLY only what appears in the fragments — never invent facts, dates or places.\n"
    "- 4–8 lines total. Answer ONLY in {language}."
)

# Cap the fragments fed in AND truncate each — a long walk's full corpus makes the prompt huge and
# the (premium) model slow, so the recap can miss the client's socket window. The tail is most
# relevant; the opening of each paragraph carries its point.
_CAP = 24
_FRAG_CHARS = 200


class Summarizer(Protocol):
    async def summarize(
        self, narrations: list[str], *, address: Address, theme: str | None, language: str
    ) -> str: ...


class NullSummarizer:
    """No summary (offline / heuristic backend)."""

    async def summarize(
        self, narrations: list[str], *, address: Address, theme: str | None, language: str
    ) -> str:
        return ""


class LLMSummarizer:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def summarize(
        self, narrations: list[str], *, address: Address, theme: str | None, language: str
    ) -> str:
        frags = [n.strip()[:_FRAG_CHARS] for n in narrations if n and n.strip()][-_CAP:]
        if len(frags) < 2:
            return ""  # too little was said to be worth a recap
        system = _SYSTEM.format(language=prompt_language(language))
        where = ", ".join(p for p in (address.city, address.district) if p)
        user = (
            (f"AREA: {where}\n" if where else "")
            + (f"THEME: {theme}\n" if theme else "")
            + "FRAGMENTS (what the guide said, in order):\n"
            + "\n".join(f"- {f}" for f in frags)
        )
        try:
            text = await self._llm.complete_text(Role.NARRATOR, system, user, max_tokens=500)
        except Exception:  # noqa: BLE001 — a failed summary must never break the end-of-walk flow
            return ""
        return (text or "").strip()
