"""Walk memory — the working-memory substrate for a single walk.

Phase 1 of the narrative memory graph (see MEMORY_GRAPH_DESIGN.md): a structured,
whole-walk record of what the guide has already said — narrated objects, covered
topics, and the narration corpus — so anti-repeat spans the ENTIRE walk (not a
18-line window) and future phases can add fact/theme nodes, callbacks, and durable
long-term memory on top of it.

Lives in `shared/` (not `services/agent/`) because it is persisted as part of
`SessionState` and must not pull in the service layer. It depends on nothing but
pydantic + the stdlib, so `shared` stays a leaf.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Caps so a long walk can't grow session state unbounded (mirrors _SEEN_CAP).
_NARRATIONS_CAP = 400
_OBJECTS_CAP = 600


def _norm_tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def is_near_duplicate(text: str, history: list[str], *, threshold: float = 0.82) -> bool:
    """True if `text` essentially repeats one of `history`. A code-level safety net over
    the model's imperfect obedience to the no-repeat rule: catches verbatim and
    near-verbatim paragraphs (token-set Jaccard >= threshold, or near-full containment
    of the shorter in the longer). Short lines (bridges, one-line floor mentions) are
    never flagged — too small to judge, deduped by name elsewhere."""
    toks = _norm_tokens(text)
    if len(toks) < 6:
        return False
    for h in history:
        ht = _norm_tokens(h)
        if not ht:
            continue
        inter = len(toks & ht)
        union = len(toks | ht)
        if union and inter / union >= threshold:
            return True
        smaller = min(len(toks), len(ht))
        if smaller and inter / smaller >= 0.92:
            return True
    return False


class WalkMemory(BaseModel):
    """What the guide has already said this walk. Persisted with SessionState, so it
    survives reconnects (resume) — the guide 'remembers' the whole walk, not 18 lines.

    Phase 1 holds the two nodes that are actually consumed today: the narration corpus
    (whole-walk anti-repeat) and the set of narrated objects (recorded now so the
    callback / long-term-memory phase has the history ready). Theme/fact nodes and
    edges land in later phases (MEMORY_GRAPH_DESIGN.md §7)."""

    narrations: list[str] = Field(default_factory=list)  # every spoken paragraph
    object_ids: list[str] = Field(default_factory=list)  # narrated object ids (recall/dedup)

    # -- anti-repeat (now over the WHOLE walk, not a window) ----------------- #
    def is_repeat(self, text: str, *, threshold: float = 0.82) -> bool:
        return is_near_duplicate(text, self.narrations, threshold=threshold)

    def record_narration(self, text: str) -> None:
        if text:
            self.narrations = (self.narrations + [text])[-_NARRATIONS_CAP:]

    # -- objects (foundation for callbacks / long-term memory) --------------- #
    def record_object(self, place_id: str | None) -> None:
        if place_id and place_id not in self.object_ids:
            self.object_ids = (self.object_ids + [place_id])[-_OBJECTS_CAP:]

    def recalled_object(self, place_id: str) -> bool:
        """True if this object was already narrated this walk (an earlier callback hook)."""
        return place_id in self.object_ids
