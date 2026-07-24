"""Late-binding seam stitch: connect a PRE-GENERATED blurb to the line just spoken.

Pre-generated narration (the object ``_narr_cache`` and prefetched area beats) is rendered
minutes before delivery against a frozen context snapshot, so its opening sentence cannot
continue whatever was actually said right before it — the root cause of the "каждая реплика —
отдельная карточка" seam. At delivery time this module asks a FAST model to rewrite ONLY the
first sentence so it flows from the previous line; the rest of the blurb is untouched.

Robustness first (the ``name_localizer`` pattern): it NEVER makes narration worse. No LLM,
a timeout, an empty/oversized/multi-sentence reply, or any error all return the original
blurb unchanged — the exact pre-fix behaviour. Uses ``Role.ANSWER_FAST`` (the provider-pinned
fast tier the two-tier companion answer already relies on), so no new model knobs.
"""

from __future__ import annotations

import asyncio
import re

from app.config import settings
from app.services.llm.router import Role

from .languages import prompt_language
from .narrator import split_sentences
from .walklog import clip, get_logger

log = get_logger()

_SYS = (
    "Ты — голос непрерывной аудио-экскурсии на прогулке. Слушателю только что прозвучала "
    "фраза PREV. Следующая реплика NEXT была заготовлена заранее и начинается «с нуля», "
    "как отдельная карточка. Перепиши ПЕРВОЕ предложение NEXT так, чтобы оно ПЕРЕТЕКАЛО из "
    "PREV: короткий оборот-мостик от мысли PREV (контраст, соседство, продолжение темы), "
    "затем суть первого предложения NEXT. ЖЁСТКИЕ ПРАВИЛА: не длиннее двадцати слов; НЕЛЬЗЯ "
    "добавлять ни одного факта, имени, названия или утверждения, которого нет в первом "
    "предложении NEXT (мостик — это связка, а не новая информация); не переноси факты из "
    "остальной части NEXT — они прозвучат следом сами; СОХРАНИ смысл первого предложения "
    "NEXT целиком — его название, сторону И его факт, ничего не выбрасывай; не повторяй "
    "формулировки PREV; без «как говорят», «возможно», «наверное»; без команд слушателю "
    "(«обрати внимание», «посмотри», «представь») и вопросов к нему; без дежурных зачинов "
    "(«Ты как раз проходишь…», «А вот…», «Кстати…»){avoid}. Ответ — ТОЛЬКО новое первое "
    "предложение на языке {language}, без кавычек и пояснений."
)

# A stitched opener may legitimately run a bit longer than the original (it carries the
# connective), but a reply beyond this ratio means the model narrated instead of stitching.
_MAX_LEN_RATIO = 2.0
_MIN_LEN = 8  # chars; anything shorter is a degenerate reply ("Да.")
_MAX_WORDS = 24  # the prompt demands <=20 words; small slack, then reject
# Fabrication/imperative tells observed live (llama invented a factory name + "как говорят",
# deepseek slipped "обрати внимание"): a connective must carry NO new claims and never command
# the listener, so any attribution/speculation/imperative marker = reject.
_BAD_MARKERS = ("как говорят", "говорят,", "по преданию", "легенда", "по слухам",
                "возможно", "наверное", "вероятно", "кажется",
                "обрати внимание", "обратите внимание", "посмотри", "представь")

_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)


def _content_words(text: str) -> set[str]:
    # Crude 4-char stems so Russian inflection still matches ("видели"/"видевшие" -> "виде").
    return {w.lower()[:4] for w in _WORD.findall(text)}


async def stitch(llm, *, prev_line: str, blurb: str, language: str) -> str:
    """Rewrite ``blurb``'s first sentence to continue ``prev_line``. Fallback = ``blurb``."""
    if llm is None or not settings.seam_stitch or not blurb or not prev_line:
        return blurb
    sents = split_sentences(blurb)
    if not sents:
        return blurb
    first = sents[0]
    # Recent connective variety: the fast model converges on one pivot word ("Рядом…" ×3 in the
    # live probe) exactly like the narrator once converged on "Ты как раз проходишь…". Ban the
    # previous stitched opener words for this session via a rotating module-level tail.
    avoid = ""
    if _recent_openers:
        avoid = "; не начинай со слов: " + ", ".join(f"«{o}…»" for o in _recent_openers)
    user = f"PREV: {prev_line.strip()}\nNEXT: {blurb.strip()}"
    try:
        raw = await asyncio.wait_for(
            llm.complete_text(
                Role.ANSWER_FAST,
                _SYS.format(language=prompt_language(language), avoid=avoid),
                user, max_tokens=120,
            ),
            settings.seam_stitch_timeout_s,
        )
    except Exception:
        return blurb  # transient (timeout/HTTP/budget) — speak the original, never stall
    new_first = (raw or "").strip().strip('"').strip("«»").strip()
    low = new_first.lower()
    # Guards: reject anything that isn't a single, sane, claim-free opening sentence.
    if (
        len(new_first) < _MIN_LEN
        or "\n" in new_first
        or len(split_sentences(new_first)) > 2
        or len(new_first.split()) > _MAX_WORDS
        or len(new_first) > max(len(first), 60) * _MAX_LEN_RATIO
        or any(m in low for m in _BAD_MARKERS)
    ):
        return blurb
    # Anti-duplication: the bridge must not pull the TAIL's facts forward (deepseek merged the
    # whole blurb into sentence 1 in the live probe — those facts would then play twice).
    tail_words = _content_words(" ".join(sents[1:]))
    new_words = _content_words(new_first)
    extra = new_words - _content_words(first)
    if tail_words and extra and len(extra & tail_words) / max(len(extra), 1) > 0.5:
        return blurb
    # Fact preservation: the rewrite must keep most of the original first sentence's content
    # (llama dropped "видели ещё первых дачников" — for a single-sentence blurb that loses the
    # only fact). Require half the original content words to survive.
    first_words = _content_words(first)
    if first_words and len(new_words & first_words) / len(first_words) < 0.5:
        return blurb
    _remember_opener(new_first)
    stitched = " ".join([new_first, *sents[1:]])
    log.info("seam stitch | %s -> %s", clip(first), clip(new_first))
    return stitched


# Rotating memory of recent stitched openers (first 2 words), process-wide. Deliberately tiny
# and approximate — it only needs to stop the fast model settling into one pivot word.
_recent_openers: list[str] = []


def _remember_opener(sentence: str) -> None:
    opener = " ".join(sentence.split()[:2])
    if not opener:
        return
    if opener in _recent_openers:
        _recent_openers.remove(opener)
    _recent_openers.append(opener)
    del _recent_openers[:-4]
