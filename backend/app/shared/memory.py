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
_FACTS_CAP = 300  # told atomic facts (anti-repeat at the fact level)


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


class ObjectMemo(BaseModel):
    """A narrated object node — the minimal graph node consumed today. Carries just enough
    to find a thematic callback later ('как та церковь, что мы видели раньше'): the name to
    reference, the category/wikidata/theme to judge relatedness. Phase 3 grows this into the
    full Object node (facts, edges); for now it's a flat record."""

    id: str
    name: str = ""
    category: str = ""
    wikidata: str | None = None
    theme: str | None = None
    significance: str | None = None
    # Position + the route odometer at narration time — the substrate for the revisit trigger
    # (near it again AND walked far enough along the route since). None lat => pre-revisit memo.
    lat: float | None = None
    lon: float | None = None
    said_route_m: float = 0.0


class WalkMemory(BaseModel):
    """What the guide has already said this walk. Persisted with SessionState, so it
    survives reconnects (resume) — the guide 'remembers' the whole walk, not 18 lines.

    Holds the narration corpus (whole-walk anti-repeat) and the narrated-object nodes
    (`objects`) that feed callbacks. Fact nodes / edges land in later phases
    (MEMORY_GRAPH_DESIGN.md §7)."""

    narrations: list[str] = Field(default_factory=list)  # every spoken paragraph
    objects: list[ObjectMemo] = Field(default_factory=list)  # narrated nodes (recall/callbacks)
    told_facts: list[str] = Field(default_factory=list)  # atomic facts already spoken (fact dedup)

    @property
    def object_ids(self) -> list[str]:
        """Narrated object ids, in order (dedup / recall)."""
        return [o.id for o in self.objects]

    # -- anti-repeat (now over the WHOLE walk, not a window) ----------------- #
    def is_repeat(self, text: str, *, threshold: float = 0.82) -> bool:
        return is_near_duplicate(text, self.narrations, threshold=threshold)

    def record_narration(self, text: str) -> None:
        if text:
            self.narrations = (self.narrations + [text])[-_NARRATIONS_CAP:]

    # -- objects (foundation for callbacks / long-term memory) --------------- #
    def record_object(self, place_id: str | None) -> None:
        """Bare-id record (back-compat / when no metadata is at hand)."""
        if place_id and place_id not in self.object_ids:
            self.objects = (self.objects + [ObjectMemo(id=place_id)])[-_OBJECTS_CAP:]

    def record_object_node(self, memo: ObjectMemo) -> None:
        """Rich record — the node the callback lookup reads."""
        if memo.id and memo.id not in self.object_ids:
            self.objects = (self.objects + [memo])[-_OBJECTS_CAP:]

    def recalled_object(self, place_id: str) -> bool:
        """True if this object was already narrated this walk (an earlier callback hook)."""
        return place_id in self.object_ids

    # -- facts (fact-level anti-repeat: kills the reworded "опять про берёзы") --- #
    def new_facts(self, facts: list[str], *, threshold: float = 0.62) -> list[str]:
        """From `facts`, keep only those NOT already told this walk (nor near-duplicated by an
        earlier fact in the same batch) — so a beat gets only genuinely new information, even if
        an old fact is reworded. A lower threshold than narration dedup: facts are short and a
        rephrasing shares most tokens."""
        out: list[str] = []
        seen = list(self.told_facts)
        for f in facts:
            f = (f or "").strip()
            if f and not is_near_duplicate(f, seen, threshold=threshold):
                out.append(f)
                seen.append(f)
        return out

    def mark_facts_told(self, facts: list[str]) -> None:
        for f in facts:
            f = (f or "").strip()
            if f:
                self.told_facts = (self.told_facts + [f])[-_FACTS_CAP:]
