"""Prompt assembly: CORE + role block + runtime context.

Loads the versionable templates from ``/prompts`` and builds the per-step user
message for each role from the typed inputs. Static prefix (CORE+ROLE) is kept
separate from the volatile RUNTIME_CONTEXT so it can be prompt-cached later.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from app.services.agent.languages import prompt_language
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


def system_for(role: Role, language: str) -> str:
    """CORE(language) + the role-specific block — the cacheable static prefix.

    ``language`` is an ISO-639-1 code (e.g. ``en``); it is mapped to a readable
    name so the model sees "English", not "en".
    """
    core = _load("core").replace("{language}", prompt_language(language))
    return f"{core}\n\n---\n\n{_load(_ROLE_FILE[role])}"


def system_for_area(language: str) -> str:
    """CORE(language) + the AREA block — for the gap-filling area monologue."""
    core = _load("core").replace("{language}", prompt_language(language))
    return f"{core}\n\n---\n\n{_load('area')}"


def system_for_planner(language: str) -> str:
    """CORE(language) + the PLANNER block — forms the area story arc."""
    core = _load("core").replace("{language}", prompt_language(language))
    return f"{core}\n\n---\n\n{_load('planner')}"


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
            # The last 1-2 spoken paragraphs, verbatim — CONTINUE this voice/thread (A1),
            # a POSITIVE continuity signal, distinct from HISTORY (the do-not-repeat ledger).
            "CONTINUE_FROM": inp.history[-2:],
            "HISTORY": inp.history,
            "FLAGS": {
                "switching": inp.flags.switching,
                "nothing_new": inp.flags.nothing_new,
                "elaborate": inp.flags.elaborate,
                "passing": inp.flags.passing,
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
            "CONTINUE_FROM": inp.history[-2:],  # continue this voice (A1)
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
        }
    )
