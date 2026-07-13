"""Narrative director — deterministic decisions over the walk's memory graph.

Seed of the "content planner" from MEMORY_GRAPH_DESIGN.md: it reads what has already been
narrated (``WalkMemory.objects``) and decides *structure* — starting with callbacks — while
the Narrator stays the *realizer* (prose). Kept deterministic and O(objects) so it runs on the
hot path without an LLM round-trip; the LLM only renders the beat afterwards.

Phase 2 owns one decision: given the object about to be told, is there an earlier related object
worth referencing ("как та церковь, что мы видели раньше…")? Later phases add fact-level dedup,
ordering and look-ahead arc planning behind the same module.
"""

from __future__ import annotations

from app.shared.geo_math import haversine_m
from app.shared.memory import ObjectMemo
from app.shared.schemas import CallbackRef, Candidate, GeoPoint, LookaheadRef, Place

from .narrator import split_sentences

# Generic categories where a callback ("как тот магазин / дом ранее") adds nothing — skip them:
# commercial (no ad-speak) and featureless generic buildings (too common to be a meaningful link).
_DULL_CATEGORIES = frozenset({
    "shop", "supermarket", "convenience", "amenity", "food", "cafe", "restaurant",
    "building", "house", "residential", "apartments", "commercial", "retail", "office",
    "yes", "",
})

# Don't call back to something we mentioned only a beat or two ago — too close to feel like a
# callback (it'd read as a repeat). Reach further back into the walk than this.
_MIN_GAP = 2


def find_callback(
    objects: list[ObjectMemo], place: Place, *, min_gap: int = _MIN_GAP
) -> CallbackRef | None:
    """The most recent EARLIER-narrated object of the same category as `place`, excluding the
    last `min_gap` objects (too recent) and dull/commercial categories. None when nothing
    relates — the narrator then tells the object plainly. Deterministic, no I/O."""
    category = (place.category or "").strip().lower()
    if category in _DULL_CATEGORIES:
        return None
    # Everything except the most-recent `min_gap` (and never the object itself), newest-first.
    prior = objects[: len(objects) - min_gap] if len(objects) > min_gap else []
    for memo in reversed(prior):
        if memo.id == place.id or not memo.name:
            continue
        if (memo.category or "").strip().lower() == category:
            return CallbackRef(name=memo.name, category=memo.category)
    return None


# A candidate must be at least this "notable" (type_weight) to be worth foreshadowing — teasing
# a bench or a shop ahead adds nothing. Real landmarks/parks/monuments clear it.
_LOOKAHEAD_MIN_WEIGHT = 0.55


def find_lookahead(
    candidates: list[Candidate], *, seen: list[str], min_ahead_m: float,
    min_weight: float = _LOOKAHEAD_MIN_WEIGHT,
) -> LookaheadRef | None:
    """The nearest notable object coming up AHEAD (in the gaze cone, unseen, beyond the current
    'right here' bubble) — the one to tease so the tour leans forward. None when nothing ahead is
    worth announcing. Deterministic, no I/O; the narrator decides whether to actually use it."""
    seen_set = set(seen)
    best: Candidate | None = None
    for c in candidates:
        if (
            not c.in_gaze_cone  # only what's actually ahead in the walking direction
            or c.place.id in seen_set
            or c.distance_m <= min_ahead_m  # already here (in the bubble) — not "ahead"
            or c.type_weight < min_weight
            or (c.place.category or "").strip().lower() in _DULL_CATEGORIES
            or not c.place.name
        ):
            continue
        if best is None or c.distance_m < best.distance_m:  # soonest to reach
            best = c
    if best is None:
        return None
    return LookaheadRef(name=best.place.name, category=best.place.category)


def find_revisit(
    objects: list[ObjectMemo], position: GeoPoint, route_len_m: float, *,
    radius_m: float, min_route_m: float,
) -> ObjectMemo | None:
    """The nearest earlier-narrated object the walker has RETURNED to: within `radius_m` now AND
    at least `min_route_m` of route walked since it was told (so it never fires right after the
    main narration — only on a genuine loop back). None when no such object. No I/O."""
    best: ObjectMemo | None = None
    best_d = radius_m
    for o in objects:
        if o.lat is None or o.lon is None:
            continue
        if route_len_m - o.said_route_m < min_route_m:  # haven't walked far enough away yet
            continue
        d = haversine_m(position, GeoPoint(lat=o.lat, lon=o.lon))
        if d <= best_d:
            best_d, best = d, o
    return best


# Below this length a "fact" is a fragment, not a claim worth deduping — drop it.
_MIN_FACT_CHARS = 16


def atomize_facts(text: str | None) -> list[str]:
    """Split an enrichment blurb into atomic facts (one claim per sentence), heuristically —
    no LLM on the hot path. The fact-level ``told?`` layer (WalkMemory.new_facts) then filters
    these so a beat only ever gets NEW information."""
    if not text or not text.strip():
        return []
    out: list[str] = []
    for s in split_sentences(text):
        s = s.strip()
        if len(s) >= _MIN_FACT_CHARS:
            out.append(s)
    return out
