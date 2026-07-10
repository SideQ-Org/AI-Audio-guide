"""Localize place TITLES to the session language.

`languages.display_name` resolves the deterministic part — an OSM exonym
(`name:<lang>` / `name:en` / `int_name`) when one exists, else a romanization. But
romanizing a Cyrillic name turns common nouns into nonsense ("Городской парк" ->
"Gorodskoy park" instead of "City park"). This service fills that gap with a cheap,
cached LLM translation that renders the generic/common-noun parts in the session
language while keeping proper names transliterated — exactly what the narrator
already does in its speech.

Robustness first: it NEVER blocks the tour. No LLM (offline/heuristic), a timeout,
the budget cap, or any error all fall back to the deterministic romanization, so a
title is always produced. Results are cached by (name, language); place names repeat
heavily across ticks/sessions, so steady-state cost is small and bounded by the
provider USD cap that `complete_text` already enforces.
"""

from __future__ import annotations

import asyncio
import re

from app.services.agent.languages import _has_cyrillic, normalize, prompt_language, transliterate
from app.services.llm.router import Role

_CACHE_CAP = 20000
_BATCH_CAP = 120  # at most this many uncached names per pin-frame LLM call

_SYS_ONE = (
    "You localize ONE place name into {language} for a short audio-guide title / map "
    "label. Translate the generic, common-noun parts (e.g. church, cathedral, city "
    "park, square, museum, bridge, monument, embankment) into {language}; keep proper "
    "names (people, unique toponyms) but render them in the {language} script "
    "(transliterate). Output ONLY the localized name — no quotes, no notes, no trailing "
    "period, keep it natural and short."
)
_SYS_MANY = (
    "You localize a numbered list of place names into {language} for short map labels. "
    "Translate the generic, common-noun parts (church, city park, square, museum, "
    "bridge, monument, ...) into {language}; keep proper names but render them in the "
    "{language} script (transliterate). Output the SAME numbers in the SAME order, one "
    "localized name per line as 'N. name' — nothing else, no quotes, no notes."
)

_LINE = re.compile(r"^\s*(\d+)[.)]\s*(.+?)\s*$")


def _clean(s: str) -> str:
    return s.strip().strip('"').strip("«»").strip().rstrip(".").strip()


class NameLocalizer:
    def __init__(self, llm=None, *, role: Role = Role.SCORER, timeout_s: float = 5.0) -> None:
        self._llm = llm
        self._role = role
        self._timeout = timeout_s
        self._cache: dict[tuple[str, str], str] = {}
        self._warm_tasks: set = set()  # hold refs to background pin-warm tasks

    # -- deterministic resolution shared by the single + batch paths ----------
    @staticmethod
    def _exonym(tags: dict[str, str], lang: str) -> str | None:
        return tags.get(f"name:{lang}") or tags.get("name:en") or tags.get("int_name")

    def _fallback(self, name: str) -> str:
        return transliterate(name) if _has_cyrillic(name) else name

    def _cache_put(self, key: tuple[str, str], value: str) -> None:
        if key not in self._cache and len(self._cache) >= _CACHE_CAP:
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[key] = value

    # -- single name (the narrated title) -------------------------------------
    async def localize(self, tags: dict[str, str], fallback: str, language: str) -> str:
        lang = normalize(language)
        nl = tags.get(f"name:{lang}")
        if nl:
            return nl
        if lang == "ru":
            return fallback  # authentic Russian name in the home region
        ex = tags.get("name:en") or tags.get("int_name")
        if ex:
            return ex
        if not _has_cyrillic(fallback):
            return fallback  # already Latin/target script — keep as-is
        key = (fallback, lang)
        if key in self._cache:
            return self._cache[key]
        if self._llm is None:
            return self._fallback(fallback)
        try:
            raw = await asyncio.wait_for(
                self._llm.complete_text(
                    self._role, _SYS_ONE.format(language=prompt_language(lang)),
                    fallback, max_tokens=40,
                ),
                self._timeout,
            )
        except Exception:
            return self._fallback(fallback)  # transient: don't cache, retry later
        out = _clean(raw) or self._fallback(fallback)
        self._cache_put(key, out)
        return out

    # -- batch (the nearby map pins) ------------------------------------------
    def localize_batch(
        self, items: list[tuple[dict[str, str], str]], language: str
    ) -> list[str]:
        """Resolve many (tags, name) pins FAST and synchronously: exonym, then the
        translation cache, else romanization. Never calls the LLM — so it can run on
        the pin-frame path without stalling narration. Pair it with ``warm_batch`` to
        fill the cache in the background, so a later frame shows the translations."""
        lang = normalize(language)
        out: list[str] = []
        for tags, name in items:
            nl = tags.get(f"name:{lang}")
            if nl:
                out.append(nl)
            elif lang == "ru":
                out.append(name)
            elif (ex := tags.get("name:en") or tags.get("int_name")):
                out.append(ex)
            elif not _has_cyrillic(name):
                out.append(name)
            else:
                out.append(self._cache.get((name, lang)) or self._fallback(name))
        return out

    def warm_batch(self, items: list[tuple[dict[str, str], str]], language: str) -> None:
        """Fire-and-forget: translate the uncached Cyrillic pin names into the cache
        (one batched LLM call) so the NEXT pin frame renders them. No-op without an LLM
        or when nothing is pending. Never awaited by the caller — can't stall the tour."""
        if self._llm is None:
            return
        lang = normalize(language)
        pending: list[str] = []
        seen: set[str] = set()
        for tags, name in items:
            if tags.get(f"name:{lang}") or tags.get("name:en") or tags.get("int_name"):
                continue
            if lang == "ru" or not _has_cyrillic(name):
                continue
            if (name, lang) in self._cache or name in seen:
                continue
            seen.add(name)
            pending.append(name)
        if not pending:
            return
        task = asyncio.ensure_future(self._warm(pending[:_BATCH_CAP], lang))
        self._warm_tasks.add(task)
        task.add_done_callback(self._warm_tasks.discard)

    async def _warm(self, names: list[str], lang: str) -> None:
        listing = "\n".join(f"{n + 1}. {name}" for n, name in enumerate(names))
        try:
            raw = await asyncio.wait_for(
                self._llm.complete_text(
                    self._role, _SYS_MANY.format(language=prompt_language(lang)),
                    listing, max_tokens=24 * len(names) + 64,
                ),
                self._timeout * 3,  # background: a generous window, blocks nothing
            )
        except Exception:
            return  # next frame will retry the still-uncached names
        for line in raw.splitlines():
            m = _LINE.match(line)
            if not m:
                continue
            idx = int(m.group(1)) - 1
            val = _clean(m.group(2))
            if 0 <= idx < len(names) and val:
                self._cache_put((names[idx], lang), val)
