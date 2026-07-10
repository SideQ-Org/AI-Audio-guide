"""Narrator role: turn the chosen place + facts into short spoken text.

  * TemplateNarrator — deterministic, no LLM (offline sim / fallback)
  * LLMNarrator      — living prose via an LLMClient (production)

Both return "" for silence (the [SILENCE] sentinel is normalized away).
"""

from __future__ import annotations

import re
from typing import Protocol

from app.config import settings
from app.services.llm.client import LLMClient
from app.services.llm.router import Role
from app.shared.schemas import AreaInput, NarratorInput, Significance

from .languages import attribution_markers, solicit_markers
from .prompts import build_area_user, build_narrator_user, system_for, system_for_area
from .significance import role_for_significance

SILENCE = "[SILENCE]"

# Cross-paragraph baton: the Narrator appends an internal "HOOK: ..." line that we
# strip from the spoken text and hand to the next paragraph as `next_hook`, so the
# tour reads as one woven monologue instead of independently-improvised paragraphs.
_HOOK_INSTR = (
    "\n\nВ САМОМ КОНЦЕ ответа добавь служебную строку строго в формате `HOOK: <2-6 слов>` "
    "— слово HOOK и двоеточие пиши ЛАТИНИЦЕЙ ровно так (не переводи ярлык), сам крючок — на "
    "языке рассказа. Это ВНУТРЕННЯЯ пометка-связка к следующему фрагменту: она НЕ "
    "произносится и НЕ входит в рассказ, а в следующем фрагменте её нужно ОБЫГРАТЬ своими "
    "словами, не повторяя дословно. Если связки нет — строку HOOK не добавляй."
)
# Strip the HOOK baton so it never reaches TTS. Two matchers:
#  * ASCII `HOOK:` anywhere to end — the mandated format; DeepSeek often tacks it onto
#    the last sentence on the SAME line ("…прошлого. HOOK: дальше"), so match anywhere.
#  * a LOCALIZED/renamed label on its OWN trailing line (Крючок:/Связка:/BRIDGE — …) —
#    the model sometimes translates or renames the label, which the ASCII matcher misses;
#    anchored to a line start so a mid-sentence word can't swallow half the narration.
_HOOK_RE = re.compile(r"(?is)\bHOOK\s*[:：]\s*(.*)$")
_HOOK_RE_LABELLED = re.compile(
    r"(?im)(?:^|\n)\s*(?:HOOK|КРЮЧОК|СВЯЗКА|ПЕРЕХОД|BRIDGE|NEXT)\s*[:：—–-]\s*(.+?)\s*$"
)


def _strip_hook(text: str) -> tuple[str, str | None]:
    """Return (text_without_baton, hook). Tries the ASCII form first, then a localized
    label on its own trailing line."""
    m = _HOOK_RE.search(text)
    if m is not None:
        return text[: m.start()], (m.group(1) or "").strip() or None
    labelled = list(_HOOK_RE_LABELLED.finditer(text))
    if labelled:  # keep the LAST labelled line (the trailing baton)
        last = labelled[-1]
        return text[: last.start()], (last.group(1) or "").strip() or None
    return text, None


_SENT_BOUNDARY = re.compile(r"(?<=[.!?…。！？])\s+")


def _sentences(text: str) -> list[str]:
    """Split narration into sentences (also on newlines), dropping empties. Rough but
    enough for the trailing-sentence guards below (narration is short, plain prose)."""
    out: list[str] = []
    for line in text.split("\n"):
        for s in _SENT_BOUNDARY.split(line.strip()):
            s = s.strip()
            if s:
                out.append(s)
    return out


# Public alias: the narration scheduler (main.py) delivers narration one sentence at a
# time so an object can be woven in at a boundary instead of cutting a line mid-word.
def split_sentences(text: str) -> list[str]:
    return _sentences(text)


def _desolicit(text: str, language: str) -> str:
    """Drop trailing listener-directed sentences — a question to the listener or an
    offer ("если хотите, расскажу подробнее"). Narration is a monologue and must never
    solicit (CORE); the model still slips, so strip it here. Only the TRAILING run is
    removed, so real content is preserved; if nothing but a solicit remains -> ''. """
    if not text:
        return text
    markers = solicit_markers(language)
    sents = _sentences(text)
    n = len(sents)
    while sents:
        s = sents[-1]
        low = s.lower()
        if s.endswith("?") or s.endswith("？") or any(m in low for m in markers):
            sents.pop()
        else:
            break
    return " ".join(sents).strip() if len(sents) != n else text


def _strip_attributions(text: str, language: str) -> str:
    """Drop sentences that lean on an unverifiable folk attribution ("старожилы
    рассказывали", "легенда гласит") — a fabrication tell. Backstop over the prompt ban."""
    markers = attribution_markers(language)
    if not markers or not text:
        return text
    sents = _sentences(text)
    kept = [s for s in sents if not any(m in s.lower() for m in markers)]
    return " ".join(kept).strip() if len(kept) != len(sents) else text


def split_hook(text: str, language: str = "ru") -> tuple[str, str | None]:
    """Split the trailing `HOOK: ...` baton off the narration and clean the spoken part.
    Returns (spoken, hook). No HOOK -> (cleaned_text, None).

    The single choke point every narration path goes through, so no caller can forget
    the guards applied here (in order):
      1. strip the HOOK baton (ASCII or a localized trailing label);
      2. normalize() — blanks the [SILENCE] sentinel (the model often appends `HOOK:` to
         a bare `[SILENCE]`, so normalizing AFTER the split is what actually blanks it);
      3. strip unverifiable folk attributions (fabrication backstop, A3);
      4. desolicit — drop a trailing question/offer to the listener (A2).
    These apply to narration/area ONLY; Companion replies never pass through here."""
    if not text:
        return text, None
    body, hook = _strip_hook(text)
    spoken = normalize(body.strip())
    if spoken:
        spoken = _desolicit(_strip_attributions(spoken, language), language)
    return spoken, hook

# very rough per-category openers for the template fallback (no facts case)
_GENERIC = {
    "park": "Тут небольшой сквер — обычное место, чтобы перевести дух.",
    "garden": "Тут рядом садик, ничего особенного, но приятно.",
    "shop": "",  # commercial without facts → stay silent
    "cafe": "",
    "building": "",
}


def normalize(text: str) -> str:
    text = text.strip()
    return "" if text == SILENCE or not text else text


class Narrator(Protocol):
    async def narrate(self, inp: NarratorInput) -> str: ...

    async def narrate_area(self, inp: AreaInput) -> str: ...


class TemplateNarrator:
    async def narrate(self, inp: NarratorInput) -> str:
        if inp.flags.nothing_new:
            return ""  # idle: silence (the living-companion line is the LLM's job)

        name = inp.place.name
        if any(name in h for h in inp.history):
            return ""  # already covered this place — don't repeat

        prefix = "А вот это уже интереснее — " if inp.flags.switching else ""

        if inp.facts:
            body = inp.facts.strip()
            if len(body) > 220 and not _is_high(inp.significance):
                body = body[:220].rsplit(" ", 1)[0] + "…"
            return normalize(f"{prefix}{name}. {body}")

        # no facts: only speak for genuinely notable types, else silence
        generic = _GENERIC.get(inp.place.category, "")
        return normalize(f"{prefix}{generic}" if generic else "")

    async def narrate_area(self, inp: AreaInput) -> str:
        # deterministic fallback: name the area / use facts, else silence
        where = inp.address.district or inp.address.city or inp.address.street
        if not where:
            return ""
        if inp.facts:
            return normalize(inp.facts.strip()[:220])
        if inp.topic:
            return normalize(f"А сам {where} — это про {inp.topic}.")
        return ""


class LLMNarrator:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def narrate(self, inp: NarratorInput) -> str:
        # Deterministic silence — decide in code, don't spend an LLM call (or rely
        # on the model's reasoning) to stay quiet. Elaborate mode is the exception:
        # it deliberately revisits an already-covered place to add new detail.
        if not inp.flags.elaborate:
            if inp.flags.nothing_new:
                return ""
            if any(inp.place.name in h for h in inp.history):
                return ""  # already covered this place — never repeat
        role = role_for_significance(inp.significance)
        system = system_for(role, inp.language)
        if settings.narrator_emit_hook:
            system += _HOOK_INSTR
        user = build_narrator_user(inp)
        text = await self._llm.complete_text(role, system, user)
        return normalize(text)

    async def narrate_area(self, inp: AreaInput) -> str:
        # the area monologue runs through the Narrator role/model (it's narration);
        # facts may be empty -> the prompt allows safe general knowledge of the city.
        system = system_for_area(inp.language)
        if settings.narrator_emit_hook:
            system += _HOOK_INSTR
        user = build_area_user(inp)
        text = await self._llm.complete_text(Role.NARRATOR, system, user)
        return normalize(text)


def _is_high(s: Significance) -> bool:
    return s in (Significance.HIGH, Significance.LANDMARK)
