"""Prompt assembly: CORE + role block + runtime context.

Loads the versionable templates from ``/prompts`` and builds the per-step user
message for each role from the typed inputs. Static prefix (CORE+ROLE) is kept
separate from the volatile RUNTIME_CONTEXT so it can be prompt-cached later.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
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
    RouteScriptInput,
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
def _load_file(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").strip()


# In-process prompt overrides (Block 4 Phase 5/6). The self-improvement loop swaps a CANDIDATE
# prompt text in here to evaluate it WITHOUT touching the file (and it's the seed of the Phase 6
# canary hot-swap). EMPTY by default ⇒ `_load` behaves exactly as the cached file read, so live
# behaviour is unchanged until something sets an override. Not thread-safe by design: set/clear
# around a single evaluation, or per-session at the canary boundary.
_overrides: dict[str, str] = {}

# PER-SESSION overrides (Block 4 Phase 6 canary). A ContextVar so a fraction of live sessions can
# use a CANARY prompt while everyone else uses the file — set at the session boundary, auto-scoped
# to that request's context, never leaking across sessions. Empty ⇒ no effect (dormant by default).
_session_overrides: ContextVar[dict[str, str] | None] = ContextVar(
    "prompt_session_overrides", default=None
)


def _load(name: str) -> str:
    so = _session_overrides.get()
    if so and name in so:
        return so[name]
    ov = _overrides.get(name)
    return ov if ov is not None else _load_file(name)


def set_session_prompt_override(mapping: dict[str, str] | None) -> None:
    """Set the per-session prompt overrides for the current context (Phase 6 canary). Pass a
    ``{name: text}`` map for this session, or None to clear. Auto-scoped to the ContextVar."""
    _session_overrides.set(mapping or None)


def clear_session_prompt_override() -> None:
    _session_overrides.set(None)


def set_prompt_override(name: str, text: str | None) -> None:
    """Override (or, with ``text=None``, clear) the named prompt for this process. ``name`` is a
    template stem — ``"narrator"``, ``"area"``, ``"core"``, ``"judge"``, etc."""
    if text is None:
        _overrides.pop(name, None)
    else:
        _overrides[name] = text


def clear_prompt_overrides() -> None:
    _overrides.clear()


def active_overrides() -> dict[str, str]:
    """A copy of the currently-active overrides (for diagnostics / the canary controller)."""
    return dict(_overrides)


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


def system_for_scripter(language: str) -> str:
    """CORE(language) + the SCRIPTER block — plans the whole guided route as one tour."""
    return f"{_core(language)}\n\n---\n\n{_load('scripter')}"


def system_for_judge(language: str) -> str:
    """The interestingness-judge rubric (Block 4). Deliberately STANDALONE — it does NOT
    prepend CORE: the judge is an evaluator, not the guide, and must not inherit the guide
    persona/voice rules. ``{language}`` names the language the blurbs are written in."""
    return _load("judge").replace("{language}", prompt_language(language))


def system_for_optimizer() -> str:
    """The prompt-rewrite proposer meta-prompt (Block 4 loop). Standalone (no CORE) — it is a
    prompt engineer rewriting the guide's prompt, not the guide itself."""
    return _load("optimizer")


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
            # Guided tour: the scripted role of THIS stop inside the whole-route arc (see BEAT
            # block). A director's note — the angle to tell this object from so it fits the tour.
            "BEAT": inp.beat_angle,
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


def build_scripter_user(inp: RouteScriptInput) -> str:
    return _json(
        {
            "ADDRESS": inp.address.model_dump(exclude_none=True),
            "THEME_OVERRIDE": inp.theme_override,
            "STOPS": [
                {
                    "order": i,
                    "name": s.name,
                    "category": s.category,
                    "significance": s.significance,
                    "facts": s.facts,
                }
                for i, s in enumerate(inp.stops)
            ],
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
