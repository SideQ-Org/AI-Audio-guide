"""Model router — maps an agent role to the configured Claude model id.

Stage 0: pure mapping (no API calls yet). Real Anthropic calls land in Stage 2,
but every caller goes through ``model_for`` so swapping a model = config change.
"""

from __future__ import annotations

from enum import StrEnum

from app.config import settings


class Role(StrEnum):
    SCORER = "scorer"
    NARRATOR = "narrator"
    COMPANION = "companion"
    LANDMARK = "landmark"  # high-end narrator for LANDMARK-significance places
    ENRICHER = "enricher"  # web-search fact gathering (off the hot-path)
    ANSWER_FAST = "answer_fast"  # tier-1 instant one-sentence answer (two-tier barge-in)
    JUDGE = "judge"  # interestingness evaluator (Block 4) — off-hot-path, other family
    OPTIMIZER = "optimizer"  # prompt-rewrite proposer (Block 4 loop) — strongest frontier model


def model_for(role: Role) -> str:
    return {
        Role.SCORER: settings.model_scorer,
        Role.NARRATOR: settings.model_narrator,
        Role.COMPANION: settings.model_companion,
        Role.LANDMARK: settings.model_landmark,
        Role.ENRICHER: settings.model_enricher,
        Role.ANSWER_FAST: settings.model_answer_fast or settings.model_companion,
        Role.JUDGE: settings.model_judge or settings.model_narrator,
        Role.OPTIMIZER: settings.model_optimizer or settings.model_landmark,
    }[role]
