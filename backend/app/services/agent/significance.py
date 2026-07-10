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

# One tier up, saturating at LANDMARK — used to lift wiki-linked objects with facts.
_LIFTED: dict[Significance, Significance] = {
    Significance.SKIP: Significance.SKIP,
    Significance.LOW: Significance.MEDIUM,
    Significance.MEDIUM: Significance.HIGH,
    Significance.HIGH: Significance.LANDMARK,
    Significance.LANDMARK: Significance.LANDMARK,
}


def rank(s: Significance) -> int:
    return _ORDER[s]


def at_least(s: Significance, threshold: Significance) -> bool:
    return _ORDER[s] >= _ORDER[threshold]


def tags_have_wiki(tags: dict[str, str] | None) -> bool:
    """True when OSM tags carry a Wikipedia/Wikidata link — a strong 'notable, and we have
    real facts to tell' signal that the generic type weight often misses (e.g. a historic
    building tagged only `building`)."""
    if not tags:
        return False
    return "wikipedia" in tags or "wikidata" in tags


def significance_from_weight(
    weight: float, facts_available: bool, *, has_wiki: bool = False
) -> Significance:
    """Heuristic significance from type weight, softened when no facts back it and lifted
    one tier for wiki-linked objects we actually have facts for (so a richly-documented but
    generically-tagged place isn't under-narrated)."""
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
    # Fact-richness lifts depth by one tier — but only when facts back it (never conjures a
    # factless landmark) and never for a SKIP (a bench with a stray wikidata stays skipped).
    if has_wiki and facts_available and Significance.SKIP is not s:
        s = _LIFTED[s]
    return s


def role_for_significance(s: Significance) -> Role:
    """LANDMARK gets the premium model; everything else the standard narrator."""
    return Role.LANDMARK if s is Significance.LANDMARK else Role.NARRATOR
