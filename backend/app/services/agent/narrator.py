"""Narrator role: turn the chosen place + facts into short spoken text.

  * TemplateNarrator — deterministic, no LLM (offline sim / fallback)
  * LLMNarrator      — living prose via an LLMClient (production)

Both return "" for silence (the [SILENCE] sentinel is normalized away).
"""

from __future__ import annotations

import re
from typing import Protocol

from app.config import settings
from app.services.llm.client import USER_ADDRESS, LLMClient
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


# Structured facts for the object CARD (re-readable, NOT spoken): the narrator appends a
# trailing `CARD:` block with 2-3 dry facts (no "ты проходишь мимо" framing). Emitted in the
# SAME call as the narration (zero extra LLM cost), stripped before TTS like the HOOK baton.
_CARD_INSTR = (
    "\n\nПОСЛЕ строки HOOK (или в самом конце, если HOOK нет) добавь блок для КАРТОЧКИ: "
    "отдельной строкой `CARD:` (слово CARD и двоеточие — ЛАТИНИЦЕЙ ровно так, не переводи), "
    "а под ней 2–3 КОРОТКИХ факта об этом объекте на языке ответа — сухо, по делу, для "
    "чтения потом. КАЖДЫЙ факт с новой строки. БЕЗ обращений и рамок экскурсии: слова "
    "«ты», «вы», «проходишь мимо», «слева/справа», «сейчас», «перед тобой» — ЗАПРЕЩЕНЫ. "
    "НЕ включай справочные данные: почтовый адрес, номер телефона, часы работы, цены, "
    "сайт/e-mail — это не факт об объекте, а карточка не справочник. "
    "Только содержательные проверенные факты (как в озвучке; из FACTS). "
    "Нет фактов — блок CARD НЕ добавляй."
)
# Strip the CARD block off the end (before the HOOK strip, since HOOK's matcher is greedy to
# end-of-text and would otherwise swallow the card). `CARD:` to end, across newlines.
_CARD_RE = re.compile(r"(?is)\n?\s*\bCARD\s*[:：]\s*(.*)$")

# How the guide addresses the LISTENER grammatically — the user's optional choice. Appended to
# the narrator system prompt. Neutral (unset) is the default and ACTIVELY tells the model to
# sidestep gendered 2nd-person forms (Russian narration otherwise defaults to masculine "ты
# прошёл"). About ITSELF the guide still follows CORE (assistant-gender) — this is only the walker.
_USER_ADDRESS_INSTR = {
    "masculine": (
        "\n\nОБРАЩЕНИЕ К СЛУШАТЕЛЮ — В МУЖСКОМ РОДЕ: слушатель — мужчина. Формы, обращённые к "
        "нему, — мужского рода на любом языке, где род есть («ты прошёл», «ты сам видел», «ты "
        "бы удивился»). (О СЕБЕ правила CORE не меняются.)"
    ),
    "feminine": (
        "\n\nОБРАЩЕНИЕ К СЛУШАТЕЛЮ — В ЖЕНСКОМ РОДЕ: слушатель — женщина. Формы, обращённые к "
        "ней, — женского рода («ты прошла», «ты сама видела», «ты бы удивилась»). (О СЕБЕ "
        "правила CORE не меняются.)"
    ),
    "neutral": (
        "\n\nОБРАЩЕНИЕ К СЛУШАТЕЛЮ — НЕЙТРАЛЬНО: пол слушателя НЕИЗВЕСТЕН. НЕ угадывай его и "
        "ИЗБЕГАЙ форм, требующих рода при обращении к нему («ты прошёл/прошла», «ты сам/сама»): "
        "переформулируй безлично или в настоящем времени («вот и поворот», «мимо этого места», "
        "«здесь находится…»). Обращайся нейтрально."
    ),
}


def _address_instr(user_address: str) -> str:
    return _USER_ADDRESS_INSTR.get(user_address or "neutral", _USER_ADDRESS_INSTR["neutral"])


def split_card(text: str) -> tuple[str, str | None]:
    """Return (text_without_card, card_facts). The card is the trailing `CARD:` block —
    2-3 framing-free fact lines for the re-readable object card. None when absent."""
    if not text:
        return text, None
    m = _CARD_RE.search(text)
    if m is None:
        return text, None
    card = (m.group(1) or "").strip() or None
    return text[: m.start()], card


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

# Common Russian abbreviations that end in a period WITHOUT ending a sentence — so the
# fragment after them ("г. Москва", "ул. Тверская", "д. 5") must not be split off as its
# own "sentence" (which would speak a bare "Москва" alone and let the scheduler weave an
# object at a fake boundary). Single letters + "." (initials, "р. Волга") are caught too.
_ABBR = frozenset({
    "г", "ул", "д", "им", "пр", "наб", "пл", "р", "оз", "с", "пос", "просп", "бул",
    "ст", "стр", "корп", "к", "обл", "р-н", "мкр", "туп", "ш", "пер",
})
_ABBR_TAIL = re.compile(r"(?:^|\s)([А-Яа-яA-Za-z-]{1,5}|[А-Яа-яA-Za-z])\.$")


def _ends_with_abbrev(piece: str) -> bool:
    """True when `piece` ends with an abbreviation period (so the next piece continues it)."""
    m = _ABBR_TAIL.search(piece)
    if m is None:
        return False
    tok = m.group(1)
    return len(tok) == 1 or tok.lower() in _ABBR


def _sentences(text: str) -> list[str]:
    """Split narration into sentences (also on newlines), dropping empties. Rough but
    enough for the trailing-sentence guards below (narration is short, plain prose).
    Fragments after a known abbreviation ('г.', 'д.', initials) are re-merged so an
    abbreviation period isn't mistaken for a sentence end."""
    out: list[str] = []
    for line in text.split("\n"):
        for s in _SENT_BOUNDARY.split(line.strip()):
            s = s.strip()
            if not s:
                continue
            if out and _ends_with_abbrev(out[-1]):
                out[-1] = f"{out[-1]} {s}"  # continuation of an abbreviation, not a new sentence
            else:
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


# Fabrication tells for a FACT-LESS object: with no verified facts, any claim about history,
# creation, dates or events is invented (the "детсад «Ивушка» появился в те годы, когда город
# застраивался под нужды учёных" case — LOW, facts=False). We strip such sentences from object
# narration when facts are empty, keeping the safe naming/visible part. RU only for now (the
# prod language); the prompt ban covers the rest. A backstop over the prompt (cf. attributions).
_FACTLESS_HISTORY_MARKERS: dict[str, tuple[str, ...]] = {
    "ru": (
        "появил", "постро", "возвел", "возвед", "основа", "открыл", "открыт", "заложен",
        "воздвиг", "соорудил", "застраива", "застро", "в те годы", "в тот период",
        "в прошлом веке", "века", "веке", "столети", "в году", "советск", "довоенн",
        "послевоенн", "дореволюц", "испыт", "эксперимент", "изначально", "первоначально",
        "в старину", "в былые", "когда-то здесь", "тогда ещё",
    ),
}
_YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")


def strip_factless_history(text: str, language: str) -> str:
    """Drop sentences that assert history / creation / dates / events from an object narration
    that has NO verified facts — such claims are fabricated. Keeps the naming/visible sentences
    (which carry no such marker). Returns the trimmed text (possibly ''). RU only; other
    languages rely on the prompt ban."""
    markers = _FACTLESS_HISTORY_MARKERS.get((language or "").split("-")[0].lower())
    if not markers or not text:
        return text
    sents = _sentences(text)
    kept = [
        s for s in sents
        if not _YEAR_RE.search(s) and not any(m in s.lower() for m in markers)
    ]
    return " ".join(kept).strip() if len(kept) != len(sents) else text


# Empty "elemental/atemporal" poetic filler — what the guide reaches for when it has nothing
# factual to say ("время застыло", "здесь дышит история", abstract вода/воздух/огонь/камень as
# "стихии"). Not a fact and not about THIS place — a "nothing to say" tell. Backstop over the CORE
# prompt ban (cf. attributions / factless-history). RU only (prod language); other languages rely
# on the prompt. Phrase markers are unambiguous; single element words are deliberately NOT markers
# (a fountain / fire station / stone monument are legitimate) — an abstract elemental *cluster*
# (≥3 distinct elements in one fact-less sentence) is caught separately below.
_CLICHE_FILLER_MARKERS: dict[str, tuple[str, ...]] = {
    "ru": (
        "время застыл", "время останов", "время замер", "замерло время", "остановилось время",
        "здесь застыл", "будто застыл", "словно застыл", "как будто застыл",
        "дышит истори", "дышат истори", "дышит прошл", "дышит времен", "дыхание вечности",
        "пропитан атмосфер", "пропитана атмосфер", "пропитано атмосфер", "пропитаны атмосфер",
        "пропитан истори", "пропитана истори", "пропитано истори", "пропитаны истори",
        "энергетик мест", "энергетика мест", "энергетику мест", "энергетикой мест",
        "особая энергетик", "особую энергетик", "особой энергетик",
        "четыре стихи", "стихии природы", "первозданн", "дух места", "душа мест",
        "хранит память веков", "эхо прошлого", "здесь дышит",
    ),
}
_ELEMENTS = ("вода", "воздух", "огонь", "камень", "земля", "стихи")


def strip_cliche_filler(text: str, language: str) -> str:
    """Drop empty poetic-filler sentences ("время застыло", "дышит историей", abstract elemental
    metaphors) — a "nothing to say" tell. Keeps factual sentences (they carry no such marker).
    Returns the trimmed text (possibly ''). RU only; other languages rely on the prompt ban."""
    markers = _CLICHE_FILLER_MARKERS.get((language or "").split("-")[0].lower())
    if not markers or not text:
        return text
    sents = _sentences(text)
    kept = []
    for s in sents:
        low = s.lower()
        if any(m in low for m in markers):
            continue
        # abstract elemental listing ("вода, воздух, огонь, камень"): ≥3 distinct element words in
        # one sentence with no date — a real object almost never lists three; the poetry does.
        if not _YEAR_RE.search(s) and sum(1 for e in _ELEMENTS if e in low) >= 3:
            continue
        kept.append(s)
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
      4. desolicit — drop a trailing question/offer to the listener (A2);
      5. strip empty poetic/elemental filler ("время застыло", "дышит историей").
    These apply to narration/area ONLY; Companion replies never pass through here."""
    if not text:
        return text, None
    body, hook = _strip_hook(text)
    spoken = normalize(body.strip())
    if spoken:
        spoken = _desolicit(_strip_attributions(spoken, language), language)
        spoken = strip_cliche_filler(spoken, language)
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
        if settings.narrator_emit_card:
            system += _CARD_INSTR
        system += _address_instr(USER_ADDRESS.get())
        user = build_narrator_user(inp)
        text = await self._llm.complete_text(role, system, user)
        return normalize(text)

    async def narrate_area(self, inp: AreaInput) -> str:
        # the area monologue runs through the Narrator role/model (it's narration);
        # facts may be empty -> the prompt allows safe general knowledge of the city.
        system = system_for_area(inp.language)
        if settings.narrator_emit_hook:
            system += _HOOK_INSTR
        system += _address_instr(USER_ADDRESS.get())
        user = build_area_user(inp)
        text = await self._llm.complete_text(Role.NARRATOR, system, user)
        return normalize(text)


def _is_high(s: Significance) -> bool:
    return s in (Significance.HIGH, Significance.LANDMARK)
