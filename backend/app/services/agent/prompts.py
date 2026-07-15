"""Prompt assembly: CORE + role block + runtime context.

Loads the versionable templates from ``/prompts`` and builds the per-step user
message for each role from the typed inputs. Static prefix (CORE+ROLE) is kept
separate from the volatile RUNTIME_CONTEXT so it can be prompt-cached later.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from app.config import settings
from app.services.agent.languages import (
    clean_continuation,
    prompt_language,
    recent_openers,
)
from app.services.llm.router import Role
from app.shared.schemas import (
    AreaInput,
    CompanionInput,
    NarratorInput,
    PlannerInput,
    ScorerInput,
)

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

_ROLE_FILE = {
    Role.SCORER: "scorer",
    Role.NARRATOR: "narrator",
    Role.LANDMARK: "narrator",  # same role block, premium model
    Role.COMPANION: "companion",
}


@cache
def _load(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()


# Self-reference gender clause substituted into CORE ({self_reference}). Keeps the guide's
# grammatical gender consistent with the TTS voice (default "Ara" is female) so a female
# voice never says "я прошёл"/"рад" about itself. Written in Russian (like core.txt), but it
# governs the OUTPUT language's gendered forms; a no-op for genderless first person (en/zh).
_SELF_REFERENCE = {
    "feminine": (
        "О СЕБЕ — В ЖЕНСКОМ РОДЕ: твой голос женский, ты говоришь о себе как женщина. "
        "Все формы первого лица о себе — женского рода на любом языке, где род есть "
        "(«я прошла», «я рада», «сама видела», «была бы рада»). Никогда не говори о себе "
        "в мужском роде."
    ),
    "masculine": (
        "О СЕБЕ — В МУЖСКОМ РОДЕ: твой голос мужской, ты говоришь о себе как мужчина. "
        "Все формы первого лица о себе — мужского рода на любом языке, где род есть "
        "(«я прошёл», «я рад», «сам видел», «был бы рад»). Никогда не говори о себе "
        "в женском роде."
    ),
    "neutral": "",
}


def _core(language: str) -> str:
    """CORE with the language name and the self-reference gender clause substituted in."""
    core = _load("core").replace("{language}", prompt_language(language))
    clause = _SELF_REFERENCE.get(settings.assistant_gender, "")
    if clause:
        return core.replace("{self_reference}", clause)
    # Neutral: drop the placeholder and its surrounding blank paragraph cleanly.
    return core.replace("\n\n{self_reference}", "").replace("{self_reference}", "")


def system_for(role: Role, language: str) -> str:
    """CORE(language) + the role-specific block — the cacheable static prefix.

    ``language`` is an ISO-639-1 code (e.g. ``en``); it is mapped to a readable
    name so the model sees "English", not "en".
    """
    return f"{_core(language)}\n\n---\n\n{_load(_ROLE_FILE[role])}"


def system_for_area(language: str) -> str:
    """CORE(language) + the AREA block — for the gap-filling area monologue."""
    return f"{_core(language)}\n\n---\n\n{_load('area')}"


def system_for_planner(language: str) -> str:
    """CORE(language) + the PLANNER block — forms the area story arc."""
    return f"{_core(language)}\n\n---\n\n{_load('planner')}"


# Barge-in Companion, STREAMING variant: same behaviour as companion.txt but the model
# returns PLAIN SPOKEN PROSE (no JSON) so it can be streamed sentence-by-sentence to TTS.
# Tour steering (skip shops / shorter / mute) is derived heuristically from the question on
# this path — see companion.heuristic_patch — so the prompt drops the control_patch field.
_COMPANION_STREAM_BLOCK = (
    "Пользователь прервал экскурсию вопросом. Ответь как тот же гид — тот же голос, стиль,\n"
    "память; после ответа экскурсия продолжится сама.\n\n"
    "ВХОД: USER_MESSAGE, CONTEXT (окружение, LAST_NARRATION, ADDRESS), HISTORY, ALREADY_SAID.\n\n"
    "ПОВЕДЕНИЕ:\n"
    "• Сначала ответь на сам вопрос по существу, опираясь на CONTEXT (last_narration) и факты.\n"
    "  Ответ есть в контексте — дай его, не отделывайся «давай продолжим прогулку».\n"
    "• ALREADY_SAID — если не пусто, первое предложение ответа УЖЕ произнесено (быстрый ответ).\n"
    "  ПРОДОЛЖИ с него: добавь новые детали, НЕ повторяй и НЕ перефразируй его. Начни СРАЗУ с\n"
    "  нового (без «итак», без повтора темы). Добавить нечего — верни ровно [SILENCE].\n"
    "• Коротко и по делу, для аудио, на языке пользователя. Действуют все правила CORE:\n"
    "  только проверенные факты, без выдумок/клише; не знаешь — скажи честно, без «я ИИ».\n"
    "• Заканчивай ответ УТВЕРЖДЕНИЕМ, а не встречным вопросом или предложением действий.\n\n"
    "ВЫХОД: только сам текст ответа — обычной речью, без JSON, разметки и служебных полей."
)

# Tier-1 fast answer: ONE short sentence, instant. Kept minimal so a tiny fast model nails it.
_ANSWER_FAST_BLOCK = (
    "Пользователь прервал экскурсию вопросом. Ты — тот же гид. Дай МГНОВЕННЫЙ ответ — РОВНО\n"
    "ОДНО короткое предложение, по существу вопроса, опираясь на CONTEXT (LAST_NARRATION) и\n"
    "факты. Обычная речь, язык пользователя. Только правда: не знаешь — так и скажи одним\n"
    "предложением, без выдумок и без «я ИИ». Никаких вступлений, списков, разметки — одно\n"
    "предложение и всё (продолжение придёт следом отдельно)."
)


def system_for_answer_fast(language: str) -> str:
    """CORE(language) + the tier-1 fast one-sentence answer block."""
    return f"{_core(language)}\n\n---\n\n{_ANSWER_FAST_BLOCK}"


def system_for_companion_stream(language: str) -> str:
    """CORE(language) + the plain-text (streamable) Companion block."""
    return f"{_core(language)}\n\n---\n\n{_COMPANION_STREAM_BLOCK}"


def _json(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# user-message builders (volatile RUNTIME_CONTEXT)
# --------------------------------------------------------------------------- #
def build_scorer_user(inp: ScorerInput) -> str:
    candidates = [
        {
            "place_id": c.place.id,
            "name": c.place.name,
            "type": c.place.category,
            "type_weight": c.type_weight,
            "distance_m": c.distance_m,
            "in_gaze_cone": c.in_gaze_cone,
            "gaze_confidence": c.gaze_confidence.value,
            "facts_available": c.facts_available,
            "facts_snippet": c.facts_snippet,
        }
        for c in inp.candidates
    ]
    return _json(
        {
            "CANDIDATES": candidates,
            "ADDRESS": inp.address.model_dump(exclude_none=True),
            "SEEN": inp.seen,
            "PREFERENCES": inp.preferences.model_dump() if inp.preferences else None,
        }
    )


def build_narrator_user(inp: NarratorInput) -> str:
    return _json(
        {
            "PLACE": {"name": inp.place.name, "type": inp.place.category},
            "SIGNIFICANCE": inp.significance.value,
            "FACTS": inp.facts,
            "DISTANCE": inp.distance_m,
            "HEADING": {
                "direction_deg": inp.heading.direction_deg,
                "gaze_confidence": inp.heading.gaze_confidence.value,
                "side": inp.side,  # ahead|behind|left|right (left/right only at high gaze)
                "in_view": inp.in_view,  # true => visible now (in cone + close), else not in view
            },
            "PACE": inp.pace.value,
            "CONTEXT": inp.context.model_dump(exclude_none=True),
            "THEME": inp.theme,
            "TOLD": inp.told,
            "NEXT_HOOK": inp.next_hook,
            # An earlier object of the same kind already told this walk — reference it briefly
            # for a connected story (see CALLBACK in the role block), or ignore if it doesn't fit.
            "CALLBACK": (
                {"name": inp.callback.name, "type": inp.callback.category}
                if inp.callback else None
            ),
            # A notable object coming up ahead — you MAY tease it (see LOOKAHEAD), sparingly, and
            # say WHERE it is: side (left/right only when present, else ahead) + rounded distance.
            "LOOKAHEAD": (
                {
                    "name": inp.lookahead.name,
                    "type": inp.lookahead.category,
                    # left/right only when the facing is trustworthy; else None/ahead
                    "side": inp.lookahead.side,
                    "distance_m": (
                        round(inp.lookahead.distance_m / 10) * 10
                        if inp.lookahead.distance_m is not None else None
                    ),
                }
                if inp.lookahead else None
            ),
            # When elaborating, the facet to take THIS follow-up from (see ПРОДОЛЖЕНИЕ block) so
            # deeper details come from a different angle, not a reworded repeat. Null on first tell.
            "ELABORATE_ANGLE": inp.elaborate_angle if inp.flags.elaborate else None,
            # The last 1-2 SUBSTANTIVE paragraphs — CONTINUE this voice/thread (A1), a
            # POSITIVE continuity signal, distinct from HISTORY (the do-not-repeat ledger).
            # Terse bridges/floor lines are filtered so we don't seed on "Пройдём дальше."
            "CONTINUE_FROM": clean_continuation(inp.history, inp.language),
            # The openings you JUST used — do NOT start this line any of these ways (A1 variety).
            "AVOID_OPENERS": recent_openers(inp.history, inp.language),
            "HISTORY": inp.history,
            "FLAGS": {
                "switching": inp.flags.switching,
                "nothing_new": inp.flags.nothing_new,
                "elaborate": inp.flags.elaborate,
                "passing": inp.flags.passing,
                "passed": inp.flags.passed,  # already behind us -> past tense (see role block)
                "preferences": (
                    inp.flags.preferences.model_dump() if inp.flags.preferences else None
                ),
            },
        }
    )


def build_area_user(inp: AreaInput) -> str:
    return _json(
        {
            "ADDRESS": inp.address.model_dump(exclude_none=True),
            "FACTS": inp.facts,
            "THEME": inp.theme,
            "TOPIC": inp.topic,
            "TOLD": inp.told,
            "NEXT_HOOK": inp.next_hook,
            "LAST_PLACE": inp.last_place_name,
            "BEAT_MODE": inp.beat_mode,  # rotating rhetorical angle for variety (A1)
            # continue this voice (A1), filtering terse bridges/floor lines
            "CONTINUE_FROM": clean_continuation(inp.history, inp.language),
            # The openings you JUST used — do NOT start this paragraph any of these ways (A1).
            "AVOID_OPENERS": recent_openers(inp.history, inp.language),
            "HISTORY": inp.history,
            "PACE": inp.pace.value,
        }
    )


def build_planner_user(inp: PlannerInput) -> str:
    return _json(
        {
            "ADDRESS": inp.address.model_dump(exclude_none=True),
            "FACTS": inp.facts,
            "THEME_OVERRIDE": inp.theme_override,
        }
    )


def build_companion_user(inp: CompanionInput) -> str:
    return _json(
        {
            "USER_MESSAGE": inp.user_message,
            "CONTEXT": inp.context.model_dump(exclude_none=True),
            "LAST_NARRATION": inp.last_narration,
            "ADDRESS": inp.address.model_dump(exclude_none=True),
            "HISTORY": inp.history,
            # The fast tier already spoke this first sentence — CONTINUE from it, add NEW detail,
            # do NOT repeat/rephrase it; nothing to add -> [SILENCE]. Null on a single-tier answer.
            "ALREADY_SAID": inp.already_said,
        }
    )
