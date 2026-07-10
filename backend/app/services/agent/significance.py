"""Significance ordering helpers shared by the scorer roles."""

from __future__ import annotations

from app.services.llm.router import Role
from app.shared.schemas import Significance

_ORDER: dict[Significance, int] = {
    Significance.SKIP: 0,
    Significance.LOW: 1,
    Significance.MEDIUM: 2,
    Significance.HIGH: 3,
    Significance.LANDMARK: 4,
}


def rank(s: Significance) -> int:
    return _ORDER[s]


def at_least(s: Significance, threshold: Significance) -> bool:
    return _ORDER[s] >= _ORDER[threshold]


def significance_from_weight(weight: float, facts_available: bool) -> Significance:
    """Heuristic significance from type weight, softened when no facts back it."""
    if weight >= 0.85:
        s = Significance.LANDMARK
    elif weight >= 0.7:
        s = Significance.HIGH
    elif weight >= 0.5:
        s = Significance.MEDIUM
    elif weight >= 0.25:
        s = Significance.LOW
    else:
        s = Significance.SKIP
    # "only facts" invariant: don't claim a landmark we have nothing to say about.
    if not facts_available and _ORDER[s] > _ORDER[Significance.HIGH]:
        s = Significance.HIGH
    return s


def role_for_significance(s: Significance) -> Role:
    """LANDMARK gets the premium model; everything else the standard narrator."""
    return Role.LANDMARK if s is Significance.LANDMARK else Role.NARRATOR
