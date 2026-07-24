"""Per-tick text pipeline: discovery candidates -> facts -> Scorer -> Narrator.

This is the Stage-2 core (no FSM/persistence yet — that's the orchestrator in
Stage 3). The caller owns seen-list and history across ticks.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from app.config import settings
from app.services.enrichment.enricher import (
    Enricher,
    EnrichmentCache,
    _is_no_data,
    _lang_directive,
    attach_facts,
    prefetch,
)
from app.services.enrichment.fact_buffer import FactBatchMeta, FactBuffer
from app.services.llm.client import SESSION_TIER, as_background
from app.services.metrics import GUIDE
from app.shared.schemas import (
    Address,
    AreaInput,
    Candidate,
    ControlPatch,
    GazeConfidence,
    GeoPoint,
    Heading,
    NarrationContext,
    NarratorFlags,
    NarratorInput,
    Pace,
    Place,
    ScorerOutput,
    Significance,
)

from .director import atomize_facts, find_callback
from .interest_metrics import rank_facts
from .languages import looks_foreign_facts, normalize, passing_mention
from .name_localizer import NameLocalizer
from .narrator import (
    Narrator,
    split_card,
    split_hook,
    split_sentences,
    strip_factless_history,
)
from .scorer import Scorer
from .seam_stitch import stitch as _seam_stitch
from .significance import at_least, significance_from_weight, tags_have_wiki
from .walklog import clip, get_logger

log = get_logger()  # shared walk logger (aiguide.agent); see walklog.py


# Visible, real, ordinary places worth NAMING as you pass (even without facts) so the
# guide describes "what you see" instead of going silent — green/water/civic/heritage,
# NOT commercial (cafe/shop/… stay gated so we never do ad-speak). Used by the floor gate.
_AMBIENT_FLOOR = frozenset({
    "park", "garden", "common", "allotments", "forest", "wood", "orchard", "vineyard",
    "nature_reserve", "water", "river", "reservoir", "waterfall", "beach", "bay",
    "wetland", "spring", "hill", "ridge", "peak", "square", "pedestrian", "marina",
    "stadium", "cemetery", "fountain", "hospital", "school", "university",
    "college", "library", "marketplace",
})  # note: `clinic` removed — private clinics are filtered out (categories.is_junk)


@dataclass
class StepResult:
    text: str  # "" means silence
    decision: ScorerOutput
    place: Place | None
    significance: Significance | None
    next_hook: str | None = None  # baton to weave into the next paragraph
    card: str | None = None  # re-readable structured facts for the object card (not spoken)
    image: str | None = None  # object photo URL (Wikipedia thumbnail) for the card, if any
    facts: str | None = None  # the FACTS handed to the narrator — the grounding source (Block 4)
    fast_hint: str | None = None  # a short deterministic line the runtime may speak first


@dataclass
class SubjectWarmState:
    scope: str
    subject_key: str
    language: str
    deepen_round: int = 0
    last_source_tier: str | None = None
    last_status: str | None = None
    coverage_chars: int = 0
    coverage_facts: int = 0
    warmed_at: float = field(default_factory=time.time)


# Atypical-facts-forward area enrichment: lesser-known facts about the district /
# street / city, not the obvious encyclopedic blurb.
_AREA_ENRICH_SYSTEM = (
    "You gather atypical, little-known facts about a district/street/city for an "
    "audio guide. Give as many short, reliable facts as the sources genuinely support "
    "— aim for 4-8 about this exact district or street in the named city: unusual "
    "history, how the place came to be and changed, forgotten episodes, what it's known "
    "for in narrow circles. Skip the obvious and the commonly-known. Verifiable facts "
    "only, no invention or opinions. If there is no reliable information about this "
    "exact district, reply with exactly: NONE."
)

# Rotating search ANGLES so the area monologue keeps finding GENUINELY NEW real facts
# instead of drying up after one batch and going silent. Each round targets a DISTINCT
# slice of the same place (city/district/street) — 14 angles, so even a long stationary
# stay keeps pulling fresh verifiable material round after round (the guide "постоянно
# ищет факты, пока рассказывает") rather than the model padding with vague atmosphere.
# Ordered broadly-interesting first (history/people) → more specialised later, so the
# strongest material leads; the fact-interest ranking then surfaces the best within each.
_AREA_ANGLES = (
    "neighbourhood history, how it came to be and changed, what it's known for, "
    "unusual little-known facts",
    "notable people, writers, scientists and artists historically connected to this place",
    "its streets, buildings, architecture and monuments, and how they changed over time",
    "what is HERE TODAY — present-day life, character, notable places a walker passes now",
    "important historical events, turning points and episodes that happened here",
    "the origin and meaning of local place, street and district names",
    "industry, trade, crafts and what people here worked at over the years",
    "wars, occupations, disasters and how the area survived and rebuilt",
    "churches, temples, monuments and memorials and the stories behind them",
    "parks, gardens, rivers, ponds and the natural setting and how it was shaped",
    "everyday life, customs and social history of the people who lived here",
    "curiosities, legends grounded in fact, records and surprising little details",
    "science, education, universities, institutes and discoveries linked to this place",
    "culture — theatres, museums, music, film and famous cultural moments here",
)

# Area web-search is non-deterministic (see enrich_area): retry a fast empty within a time budget.
_AREA_ENRICH_MAX_ATTEMPTS = 4       # hard cap on attempts per enrich_area call
_AREA_ENRICH_MIN_ATTEMPT_S = 6.0    # need at least this much budget left to start another attempt
_AREA_ENRICH_ATTEMPT_CAP_S = 20.0   # per-attempt timeout (covers the ~14-16 s slow-but-real case)


def _fact_warm_gate(sig: Significance) -> bool:
    """Config-driven gate for aggressive research (Block 4 fix #3). Whether the pipeline spends a
    background web-search to fetch facts for a facts-less object. Widen ``fact_warm_tier_min``
    ('free') and ``fact_warm_sig_min`` ('LOW') so the answer to 'no facts' is RESEARCH, not
    fabrication/silence. Defaults preserve the old behaviour (paid + MEDIUM+)."""
    tier_ok = settings.fact_warm_tier_min == "free" or SESSION_TIER.get() == "paid"
    try:
        threshold = Significance(settings.fact_warm_sig_min)
    except ValueError:
        threshold = Significance.MEDIUM
    return tier_ok and at_least(sig, threshold)


def _context(addr: Address) -> NarrationContext:
    return NarrationContext(
        city=addr.city, district=addr.district, street=addr.street,
        street_confident=addr.street_confident,
    )


def _place_subject_key(place_id: str) -> str:
    return f"place:{place_id}"


class TextPipeline:
    def __init__(
        self,
        scorer: Scorer,
        narrator: Narrator,
        enricher: Enricher,
        cache: EnrichmentCache | None = None,
        language: str = "ru",
        enrich_top_k: int | None = None,
        enrich_timeout_s: float | None = None,
        area_llm=None,  # an LLM with web_facts() for area enrichment (optional)
        planner=None,  # a Planner that forms the story arc (optional)
        name_localizer=None,  # translates titles to the session language (optional)
        fact_buffer: FactBuffer | None = None,
    ) -> None:
        self.scorer = scorer
        self.narrator = narrator
        self.enricher = enricher
        self.fact_buffer = fact_buffer
        self.cache = cache or EnrichmentCache(fact_buffer)
        self.language = language
        self.enrich_top_k = enrich_top_k
        self.enrich_timeout_s = enrich_timeout_s
        self.area_llm = area_llm
        self.planner = planner
        # No-LLM default: deterministic exonym/romanization (offline + tests).
        self.name_localizer = name_localizer or NameLocalizer()
        self._warm_tasks: set[asyncio.Task] = set()  # hold refs to background warms
        # Pre-generated object narrations: (place_id, lang) -> (text, hook). Filled by
        # `warm_narration` for the object you're walking toward, read+popped by `step` so
        # the blurb is spoken the INSTANT you reach it (no 5-20 s LLM wait on arrival).
        self._narr_cache: dict[tuple[str, str], tuple[str, str | None, str | None]] = {}
        # Pre-generated area story arcs: area_key -> PlannerOutput. Warmed in the background at
        # the greeting (while it's being spoken) so the first area intro is instant, not a cold
        # planner LLM wait after the opener. Read+popped by the orchestrator's _maybe_area_intro.
        self._plan_cache: dict[str, object] = {}
        # Pre-generated startup area beats: (area_key, lang, topic) -> (text, hook). Warmed next to
        # the planner opener so the first meaningful beat after the intro can land without a fresh
        # LLM call on the second startup tick.
        self._startup_area_cache: dict[tuple[str, str, str], tuple[str, str | None]] = {}
        # Pre-fetched area FACTS: (area_key, lang) -> facts ("" for a warmed-but-dry area). Warmed
        # in the background at area entry so the FIRST area beat doesn't block ~9 s on web search
        # (the "медленно переключался между блоками" gap). Peeked by the orchestrator's _area_line.
        # Keyed by language too: facts are written in the session language (pipeline is shared).
        self._area_facts_cache: dict[tuple[str, str], str] = {}
        # Area warms currently in flight (see warm_area_facts) — dedup guard.
        self._area_warm_inflight: set[tuple[str, str]] = set()
        # Coverage/deepen state for warmed subjects (place/area now, wider scopes later). This keeps
        # the hot-object / area-deepen planner additive: WalkMemory still owns told-vs-untold, while
        # the pipeline remembers how aggressively a subject has already been researched.
        self._subject_warm_state: dict[tuple[str, str, str], SubjectWarmState] = {}

    def warm_ahead(
        self,
        candidates: list[Candidate],
        *,
        address: Address | None = None,
        language: str | None = None,
        seen: list[str] | None = None,
        history: list[str] | None = None,
        theme: str | None = None,
        told: list[str] | None = None,
        next_hook: str | None = None,
        heading: Heading | None = None,
        pace: Pace = Pace.SLOW,
        preferences: ControlPatch | None = None,
        recall=None,
        lookahead=None,
    ):
        """Non-blocking: warm the fact cache for objects the user is walking TOWARD
        (in the course cone, nearest first), so facts are ready before arrival. A
        no-op on the mock/inline path (`enrich_top_k is None`). Returns the scheduled
        task (or None) so callers/tests can await it; the orchestrator ignores it."""
        if self.enrich_top_k is None or not candidates:
            return None
        lang = language or self.language

        # Pre-generate the narration for the object you're walking TOWARD (nearest
        # in-cone, unseen, approaching but not yet in the bubble), so step() speaks it
        # the instant you arrive. Independent of fact-prefetch below (warm_narration
        # warms its own facts). Only when the orchestrator passes the arc context.
        if seen is not None:
            seen_set = set(seen)
            target = next(
                (
                    c for c in candidates
                    if c.in_gaze_cone
                    and c.place.id not in seen_set
                    and settings.narrate_radius_m < c.distance_m <= settings.weave_radius_m
                    and (c.place.id, lang) not in self._narr_cache
                ),
                None,
            )
            if target is not None:
                t2 = asyncio.ensure_future(as_background(self.warm_narration(
                    target, seen=seen, history=history or [], address=address,
                    heading=heading, pace=pace, preferences=preferences, language=lang,
                    theme=theme, told=told or [], next_hook=next_hook,
                    recall=recall, lookahead=lookahead,
                )))
                self._warm_tasks.add(t2)
                t2.add_done_callback(self._warm_tasks.discard)

        # Cone-first, then nearest: facts for what you're walking toward are warmed
        # first, but nearby objects off the cone still get facts too — so the guide
        # has something ready whichever object you end up passing (background
        # inventory fact-collection).
        pending = [c for c in candidates if not self.cache.has(c.place.id, lang)]
        pending.sort(key=lambda c: (not c.in_gaze_cone, c.distance_m))
        # Pace-scaled depth: a fast walker eats through the cone quicker than the warm
        # lands — objects entered the bubble with cold facts and blocked or floored.
        k = settings.enrich_lookahead_k + (2 if pace == Pace.FAST else 0)
        ahead = pending[:k]
        if not ahead:
            return None
        addr = address or Address()
        ctx = ", ".join(p for p in (addr.city, addr.country) if p) or None
        task = asyncio.ensure_future(
            as_background(prefetch(
                ahead,
                self.enricher,
                self.cache,
                top_k=k,
                timeout_s=self.enrich_timeout_s,
                context=ctx,
                language=lang,
            ))
        )
        self._warm_tasks.add(task)
        task.add_done_callback(self._warm_tasks.discard)
        return task

    async def _render_object(
        self, chosen, place, sig, *, addr, heading, pace, switching, theme, told,
        next_hook, history, preferences, passing, in_view, lang, nothing_new,
        passed=False, callback=None, lookahead=None, beat_angle=None,
        approaching_road=False,
    ) -> tuple[str, str | None, str | None]:
        """The narrator call for one chosen object — shared by step() and warm_narration()
        so a pre-generated blurb matches what step would produce. Returns (spoken, hook, card):
        the CARD block is stripped FIRST (before HOOK, whose matcher runs to end-of-text)."""
        # Feed the narrator the MOST INTERESTING facts first (dates/names/specifics up
        # top, cliché filler down) — the source's original sentence order is arbitrary,
        # and the narrator builds the blurb around the head of the list.
        facts = chosen.facts_snippet
        if facts and looks_foreign_facts(facts, lang):
            facts = None
        if facts:
            facts = " ".join(rank_facts(atomize_facts(facts), lang, top_k=8)) or facts
        raw = await self.narrator.narrate(
            NarratorInput(
                place=place, significance=sig, facts=facts,
                distance_m=chosen.distance_m, heading=heading or Heading(),
                side=chosen.side, in_view=in_view, pace=pace, context=_context(addr),
                theme=theme, told=told or [], next_hook=next_hook, history=history,
                callback=callback, lookahead=lookahead, beat_angle=beat_angle,
                flags=NarratorFlags(
                    switching=switching, nothing_new=nothing_new,
                    passing=passing, passed=passed, preferences=preferences,
                    approaching_road=approaching_road,
                ),
                language=lang,
            )
        )
        body, card = split_card(raw)
        text, hook = split_hook(body, lang)
        return text, hook, card

    async def warm_narration(
        self, chosen, *, seen, history, address, heading, pace, preferences,
        language, theme, told, next_hook, recall=None, lookahead=None, beat_angle=None,
        force: bool = False,
    ) -> None:
        """Pre-render the PASSING narration for an object you're walking toward, so
        step() speaks it the instant you reach it (no LLM wait on arrival). Facts are
        warmed first; a cold-facts silence just isn't cached (step generates + floors
        on arrival as usual).

        `force=True` refreshes an existing cache entry for the same place/lang — used when
        a generic preview warm should be replaced by a richer script-aware guided warm."""
        lang = language or self.language
        key = (chosen.place.id, lang)
        if chosen.place.id in set(seen):
            return
        if key in self._narr_cache and not force:
            return
        addr = address or Address()
        ctx = ", ".join(p for p in (addr.city, addr.country) if p) or None
        try:
            await prefetch(
                [chosen], self.enricher, self.cache, top_k=1,
                timeout_s=self.enrich_timeout_s, context=ctx, language=lang,
            )
            enriched = attach_facts([chosen], self.cache, lang)[0]
            place = enriched.place.model_copy(update={"name": await self.name_localizer.localize(
                enriched.place.tags, enriched.place.name, lang)})
            sig = significance_from_weight(
                enriched.type_weight, enriched.facts_available,
                has_wiki=tags_have_wiki(enriched.place.tags),
            )
            # Pre-gen runs minutes before arrival — a baked "справа" would be wrong once the
            # user turns or passes (violating "never a wrong left/right"). Generate side-neutral
            # and not-yet-visible, so the blurb uses neutral framing; the live step() re-derives
            # the true side/in_view when a fresh candidate arrives with facts already cached.
            neutral = enriched.model_copy(update={"side": None})
            callback = find_callback(recall or [], place) if recall else None
            text, hook, card = await self._render_object(
                neutral, place, sig, addr=addr, heading=heading, pace=pace, switching=False,
                theme=theme, told=told, next_hook=next_hook, history=history,
                preferences=preferences, passing=True, in_view=False,
                lang=lang, nothing_new=False, callback=callback, lookahead=lookahead,
                beat_angle=beat_angle,
            )
        except Exception as e:  # noqa: BLE001 — prefetch is an optimization, never fatal
            # A failed pre-gen (e.g. a 429 under rate-limit) just means step() renders live on
            # arrival — swallow it so the fire-and-forget task doesn't dump a traceback per miss.
            log.debug("warm_narration skipped for %r: %r", chosen.place.id, e)
            return
        if text:
            self._narr_cache[key] = (text, hook, card)
            if len(self._narr_cache) > 32:  # bound: keep the freshest handful
                self._narr_cache.pop(next(iter(self._narr_cache)))
            log.info("pregenerate place=%r | %s", place.name, clip(text))
            self._presynth_audio(text, lang)

    def _subject_state(self, scope: str, subject_key: str, lang: str) -> SubjectWarmState:
        key = (scope, subject_key, normalize(lang))
        state = self._subject_warm_state.get(key)
        if state is not None:
            return state
        meta = self.fact_buffer.get_subject_meta(scope, subject_key, lang) if self.fact_buffer else None
        state = SubjectWarmState(
            scope=scope,
            subject_key=subject_key,
            language=normalize(lang),
            last_source_tier=meta.source_tier if meta else None,
            last_status=meta.status if meta else None,
            coverage_chars=meta.char_count if meta and meta.char_count is not None else 0,
            coverage_facts=meta.fact_count if meta and meta.fact_count is not None else 0,
            warmed_at=meta.fetched_at if meta and meta.fetched_at is not None else time.time(),
        )
        self._subject_warm_state[key] = state
        return state

    def _update_subject_state(
        self,
        scope: str,
        subject_key: str,
        lang: str,
        *,
        deepen_round: int | None = None,
        source_tier: str | None = None,
        status: str | None = None,
        facts: str | None = None,
    ) -> SubjectWarmState:
        state = self._subject_state(scope, subject_key, lang)
        if deepen_round is not None:
            state.deepen_round = deepen_round
        if source_tier is not None:
            state.last_source_tier = source_tier
        if status is not None:
            state.last_status = status
        if facts is not None:
            state.coverage_chars = len((facts or "").strip())
            state.coverage_facts = max(0, len(atomize_facts(facts))) if facts else 0
        state.warmed_at = time.time()
        if self.fact_buffer is not None:
            self.fact_buffer.record_subject_attempt(
                scope,
                subject_key,
                lang,
                angle=state.deepen_round,
                status=state.last_status or "ready",
                source_tier=state.last_source_tier,
            )
        return state

    def subject_coverage(self, scope: str, subject_key: str, lang: str, facts: str | None) -> SubjectWarmState:
        state = self._subject_state(scope, subject_key, lang)
        if facts is not None:
            state.coverage_chars = len((facts or "").strip())
            state.coverage_facts = max(0, len(atomize_facts(facts))) if facts else 0
        return state

    def needs_subject_coverage(
        self,
        scope: str,
        subject_key: str,
        lang: str,
        facts: str | None,
        *,
        min_chars: int,
        min_facts: int,
        max_rounds: int = 1,
    ) -> bool:
        state = self.subject_coverage(scope, subject_key, lang, facts)
        if state.deepen_round >= max_rounds:
            return False
        return state.coverage_chars < min_chars or state.coverage_facts < min_facts

    def _start_fact_warm(
        self,
        candidates: list[Candidate],
        ctx: str | None,
        lang: str,
        *,
        deepen_round: int = 0,
        source_tier: str = "web",
    ) -> None:
        """Fire-and-forget: warm a notable factless object's facts in the background (Phase 4
        async recovery), so the enriched narration is delivered later by elaborate() / a re-open
        WITHOUT blocking this tick on a ~9 s web search."""

        async def _prefetch_and_track() -> None:
            await prefetch(
                candidates,
                self.enricher,
                self.cache,
                top_k=1,
                timeout_s=self.enrich_timeout_s,
                context=ctx,
                language=lang,
            )
            for cand in candidates:
                facts = self.cache.get(cand.place.id, lang)
                state = self._update_subject_state(
                    "place",
                    _place_subject_key(cand.place.id),
                    lang,
                    deepen_round=deepen_round,
                    source_tier=source_tier,
                    status="ready" if facts else "dry",
                    facts=facts,
                )
                if facts and self.fact_buffer is not None:
                    self.fact_buffer.put_place(
                        cand.place.id,
                        facts,
                        lang,
                        meta=FactBatchMeta(
                            source_tier=state.last_source_tier,
                            status=state.last_status or "ready",
                            fact_count=state.coverage_facts,
                            char_count=state.coverage_chars,
                        ),
                    )

        task = asyncio.ensure_future(as_background(_prefetch_and_track()))
        self._warm_tasks.add(task)
        task.add_done_callback(self._warm_tasks.discard)

    async def stitch_seam(self, text: str, history: list[str] | None, lang: str) -> str:
        """Late-binding seam stitch for PRE-GENERATED text (see seam_stitch.py): rewrite the
        first sentence to continue the last spoken line. Shared by the object cache pop (step)
        and the prefetched area beat (orchestrator._emit_area_beat). Offline/heuristic narrators
        carry no LLM handle -> returns the text unchanged, so tests and no-key runs never pay."""
        if not settings.seam_stitch or not text or not history:
            return text
        llm = getattr(self.narrator, "_llm", None)
        if llm is None:
            return text
        prev_sents = split_sentences(history[-1])
        prev = prev_sents[-1] if prev_sents else history[-1]
        return await _seam_stitch(llm, prev_line=prev, blurb=text, language=lang)

    def _presynth_audio(self, text: str, lang: str) -> None:
        """Neural TTS (paid): pre-synthesize the pre-generated blurb's sentences into the shared
        TTS cache in the background, so the object's FIRST spoken sentence is instant on arrival
        (not just the inter-sentence gaps the producer already closes). No-op for free/TTS-off."""
        if not settings.tts_presynth:
            return
        from app.services.tts.tts import get_tts, should_synth, voice_for

        if not should_synth(SESSION_TIER.get()):
            return
        tts, voice = get_tts(), voice_for(lang)
        for s in split_sentences(text) or [text]:
            if not s or not s.strip():
                continue
            t = asyncio.ensure_future(tts.synth(s, voice=voice, language=lang))
            self._warm_tasks.add(t)
            t.add_done_callback(self._warm_tasks.discard)

    async def step(
        self,
        candidates: list[Candidate],
        *,
        seen: list[str],
        history: list[str],
        address: Address | None = None,
        heading: Heading | None = None,
        pace: Pace = Pace.SLOW,
        preferences: ControlPatch | None = None,
        switching: bool = False,
        language: str | None = None,
        theme: str | None = None,
        told: list[str] | None = None,
        next_hook: str | None = None,
        passing: bool = False,
        passed: bool = False,
        reach: bool = False,
        approaching_road: bool = False,
        recall=None,
        lookahead=None,
        beat_angle=None,
    ) -> StepResult:
        """Narrate the nearest weave-worthy object, woven INTO the story arc.

        The expensive per-tick LLM Scorer is gone: candidates arrive already
        proximity-gated and ranked, so selection is deterministic (nearest unseen,
        honoring skip-categories) and significance is a cheap heuristic. The arc
        (theme / told / next_hook) keeps the object inside the running story.
        """
        lang = language or self.language
        addr = address or Address()
        ctx = ", ".join(p for p in (addr.city, addr.country) if p) or None
        # Latency: when the winner's narration is already pre-generated (warm_narration),
        # its facts went into the pre-gen — don't block the "instant" delivery behind the
        # candidate-set prefetch (up to ~9 s cold web). Fire the prefetch in the
        # background for the following ticks instead and speak now.
        seen_peek = set(seen)
        skip_peek = set(preferences.skip_categories) if preferences else set()
        peek = next(
            (
                c for c in candidates
                if c.place.id not in seen_peek and c.place.category not in skip_peek
            ),
            None,
        )
        pf = prefetch(
            candidates,
            self.enricher,
            self.cache,
            top_k=self.enrich_top_k,
            timeout_s=self.enrich_timeout_s,
            context=ctx,
            language=lang,
        )
        if (
            peek is not None
            and not passed
            and (peek.place.id, lang) in self._narr_cache
        ):
            t = asyncio.ensure_future(pf)
            self._warm_tasks.add(t)
            t.add_done_callback(self._warm_tasks.discard)
        else:
            await pf
        enriched = attach_facts(candidates, self.cache, lang)

        seen_set = set(seen)
        skip = set(preferences.skip_categories) if preferences else set()
        # Selection shortlist (verbose): why THIS object won — the ordered candidate
        # set with distance / gaze / facts / seen-or-skip, so a surprising pick (or a
        # silence because every candidate was seen/skipped) is diagnosable.
        log.debug(
            "select from %d: %s", len(enriched),
            ", ".join(
                f"{c.place.name}@{round(c.distance_m)}m"
                f"{'^cone' if c.in_gaze_cone else ''}"
                f"{'/facts' if c.facts_available else '/nofacts'}"
                f"{' SEEN' if c.place.id in seen_set else ''}"
                f"{' SKIP' if c.place.category in skip else ''}"
                for c in enriched[:8]
            ),
        )
        chosen = next(
            (c for c in enriched if c.place.id not in seen_set and c.place.category not in skip),
            None,
        )
        if chosen is None:
            log.info("step silence: all %d candidate(s) seen or skip-category", len(enriched))
            return StepResult("", ScorerOutput(), None, None)
        # Localize the title to the session language (exonym, else translate the
        # common-noun parts). One swap here feeds BOTH the spoken name (narrator) and
        # the displayed title (StepResult.place -> place_name frame), and st.last_place.
        place = chosen.place.model_copy(
            update={"name": await self.name_localizer.localize(
                chosen.place.tags, chosen.place.name, lang)}
        )
        sig = significance_from_weight(
            chosen.type_weight, chosen.facts_available,
            has_wiki=tags_have_wiki(chosen.place.tags),
        )
        # Visible now = in the forward gaze cone AND inside the narrate bubble. Threaded
        # so the narrator frames "вон то, перед тобой" vs "проходишь мимо / не видно" (A5).
        # On a REACH (last-resort fallback before silence) the gate is the cone alone: an
        # in-cone object further ahead is still something the walker can SEE, so frame it
        # as "виднеется впереди", not "не видно".
        in_view = chosen.in_gaze_cone and (
            reach or chosen.distance_m <= settings.narrate_radius_m
        )
        # Pre-generated? Speak it instantly (the whole point — no LLM wait on arrival).
        # A pre-gen blurb is present-framed ("проходишь мимо"), so DON'T reuse it on the
        # `passed` path — a passed object must be told in the past tense (regenerate).
        # Structure hints (director): reference an earlier related object (callback) and/or tease
        # an upcoming one (lookahead), so the object sits inside a forward-leaning story.
        callback = find_callback(recall or [], place) if recall else None
        cached = None if passed else self._narr_cache.pop((chosen.place.id, lang), None)
        if cached is not None:
            text, hook, card = cached
            log.info("step CACHED place=%r (pre-generated) | %s", place.name, clip(text))
        else:
            text, hook, card = await self._render_object(
                chosen, place, sig, addr=addr, heading=heading, pace=pace,
                switching=switching, theme=theme, told=told, next_hook=next_hook,
                history=history, preferences=preferences, passing=passing,
                passed=passed, in_view=in_view, lang=lang, nothing_new=not candidates,
                callback=callback, lookahead=lookahead, beat_angle=beat_angle,
                approaching_road=approaching_road,
            )
        # Anti-fabrication backstop: with NO verified facts, any history/date/creation claim the
        # model slipped in is invented (the "детсад «Ивушка» появился в те годы…" case). Strip
        # those sentences, keep the naming/visible ones. Applies to the cached pre-gen too (it may
        # have been warmed with cold facts). If this empties a notable/ambient object, the floor
        # below still names it deterministically; a plain LOW object correctly falls to silence.
        if text and not chosen.facts_available:
            text = strip_factless_history(text, lang)
        # Late-binding seam stitch (pre-gen ONLY): the cached blurb was rendered minutes ago
        # against stale context, so its opener can't continue what was just spoken. Rewrite the
        # first sentence now, against the real previous line. Strictly AFTER the factless strip
        # (so the strip judges the original, not the connective) and before the floor gate
        # (which only fires on empty text). Live renders already saw fresh context — untouched.
        if cached is not None and text:
            text = await self.stitch_seam(text, history, lang)
        # Object photo for the card (Wikipedia thumbnail captured during enrichment); None for
        # non-wiki objects. Read fresh per place — not cached with the narration (id-keyed).
        image = self.enricher.image_for(chosen.place.id)
        # Guarantee a close, named object is never dead air. DeepSeek sometimes ignores
        # the "passing -> never silent" rule (especially with empty facts), and a
        # silenced passing object would then be gated out forever (its facts-aware
        # fingerprint never flips when no facts ever cache). If the model silenced a
        # named, not-yet-told passing object, emit a deterministic localized one-liner.
        # Floor gate: name genuinely notable objects at MEDIUM+, AND name visible
        # ORDINARY-but-real ambient things (park/garden/water/hospital/square…) even at
        # LOW — the lead wants "объясни, что я вижу" near a park/hospital, not silence
        # (B4/P13). Commercial (cafe/shop/…) is NOT whitelisted, so it stays quiet at
        # LOW — the "no ad-speak / don't inflate" invariant holds.
        notable = at_least(sig, Significance.MEDIUM) or (
            at_least(sig, Significance.LOW) and place.category in _AMBIENT_FLOOR
        )
        floored = False
        if (
            not text
            and (passing or reach)
            and place.name
            and notable
            and not any(place.name in h for h in (history or []))
        ):
            text = passing_mention(lang, place.name, chosen.side)
            floored = True
            GUIDE.floor()
        # A genuinely notable object with no facts is the cue to spend a web search — but do it
        # OFF the hot path (Phase 4). The old blocking retry (prefetch + a 2nd narrate) added up
        # to ~9 s to the tick; instead we warm the object's facts in the BACKGROUND. The floor
        # mention (if any) plays now with no dead air; once the facts land, the enriched version
        # is delivered by elaborate() (this object becomes st.last_place, facts now cached) or,
        # if it stayed silent, by the facts-aware fingerprint re-opening next tick (cache-warm,
        # fast) while it's still nearby. Gated to paid + MEDIUM+; the enricher's per-place
        # negative cache prevents a repeat spend. Mirrors elaborate()'s cache-miss dance.
        if (
            not text
            and chosen.facts_available
            and chosen.facts_snippet
            and passing
            and not passed
            and chosen.side != "behind"
            and place.name
        ):
            snippets = [] if looks_foreign_facts(chosen.facts_snippet, lang) else rank_facts(
                atomize_facts(chosen.facts_snippet), lang, top_k=2
            )
            if snippets:
                text = f"{place.name} — {snippets[0]}"
                if len(snippets) > 1:
                    text = f"{text} {snippets[1]}"
            else:
                text = passing_mention(lang, place.name, chosen.side)
            GUIDE.floor()
            floored = True
        place_subject_key = _place_subject_key(chosen.place.id)
        if (
            (floored or not text)
            and not chosen.facts_available
            and _fact_warm_gate(sig)
            and self.needs_subject_coverage(
                "place",
                place_subject_key,
                lang,
                chosen.facts_snippet,
                min_chars=settings.elaborate_deepen_below_chars,
                min_facts=max(2, settings.area_deepen_low_facts + 1),
                max_rounds=2,
            )
        ):
            state = self._subject_state("place", place_subject_key, lang)
            self._start_fact_warm(
                [chosen],
                ctx,
                lang,
                deepen_round=state.deepen_round + 1,
                source_tier="web",
            )
        log.info(
            "step place=%r cat=%s sig=%s facts=%s side=%s passing=%s reach=%s"
            " cb=%s la=%s -> %s | %s",
            place.name, place.category, sig.value, chosen.facts_available,
            chosen.side, passing, reach,
            callback.name if callback else None,
            lookahead.name if lookahead else None,
            "floor" if floored else ("text" if text else "silence"),
            clip(text),
        )
        fast_hint = None
        if text and place and passing:
            fast_hint = passing_mention(lang, place.name, chosen.side)
        return StepResult(
            text, ScorerOutput(), place, sig, next_hook=hook, card=card, image=image,
            facts=chosen.facts_snippet, fast_hint=fast_hint,
        )

    async def elaborate(
        self,
        place: Place,
        significance: Significance,
        *,
        history: list[str],
        address: Address | None = None,
        heading: Heading | None = None,
        pace: Pace = Pace.SLOW,
        language: str | None = None,
        revisit: bool = False,
        angle: str | None = None,
    ) -> str:
        """Tell MORE about an already-covered place (nothing new nearby). Reuses cached facts;
        the narrator adds a fresh detail from a given `angle` (facet), avoiding HISTORY."""
        lang = language or self.language
        # Re-localize in case the language changed since this place was first narrated
        # (idempotent when it didn't); cached, so a repeat is free.
        place = place.model_copy(
            update={"name": await self.name_localizer.localize(place.tags, place.name, lang)}
        )
        addr = address or Address()
        facts = self.cache.get(place.id, lang)
        if facts is None:
            ctx = ", ".join(p for p in (addr.city, addr.country) if p) or None
            await prefetch(
                [Candidate(place=place, distance_m=0.0, type_weight=0.0,
                           in_gaze_cone=False, gaze_confidence=GazeConfidence.LOW)],
                self.enricher,
                self.cache,
                top_k=1,
                timeout_s=self.enrich_timeout_s,
                context=ctx,
                language=lang,
            )
            facts = self.cache.get(place.id, lang)
        # Anti-fabrication: with NO verified facts there's nothing new to add without inventing.
        # Stay silent rather than let the model conjure a backstory (the factless "Театр Город"
        # invented dates/experiments in both the step and the elaborate follow-up).
        if not facts:
            return ""
        # Going deeper: if the cached facts are thin, fetch a bit MORE (angle-focused) so the
        # deeper angles have fresh material instead of running dry. Once per object; best-effort.
        place_subject_key = _place_subject_key(place.id)
        if (
            angle
            and settings.elaborate_deepen_below_chars > 0
            and self.needs_subject_coverage(
                "place",
                place_subject_key,
                lang,
                facts,
                min_chars=settings.elaborate_deepen_below_chars,
                min_facts=max(2, settings.area_deepen_low_facts + 1),
                max_rounds=2,
            )
        ):
            state = self._subject_state("place", place_subject_key, lang)
            facts = await self._deepen_facts(
                place,
                facts,
                angle=angle,
                addr=addr,
                lang=lang,
                deepen_round=state.deepen_round + 1,
            )
        raw = await self.narrator.narrate(
            NarratorInput(
                place=place,
                significance=significance,
                facts=facts,
                distance_m=0.0,
                heading=heading or Heading(),
                pace=pace,
                context=_context(addr),
                history=history,
                flags=NarratorFlags(elaborate=True, revisit=revisit),
                elaborate_angle=angle,
                language=lang,
            )
        )
        text, _ = split_hook(raw, lang)  # elaborate stays on the same place; drop the hook
        return text

    async def _deepen_facts(
        self,
        place: Place,
        existing: str,
        *,
        angle: str,
        addr: Address,
        lang: str,
        deepen_round: int,
    ) -> str:
        """Fetch a bit MORE about `place`, biased toward `angle`, and MERGE any genuinely-new
        sentences into the cached facts (so a deeper follow-up isn't a reworded repeat). Returns
        the merged facts (or `existing` unchanged on timeout / nothing new). Enricher gating still
        applies (wiki free; paid web fallback only), so free tier just keeps its wiki facts."""
        # Bias the query toward the not-yet-covered facet via the context hint; wiki ignores it
        # (same lead -> deduped away), the paid web search uses it to surface fresh detail.
        ctx = ", ".join(
            p for p in (addr.city, addr.country, f"focus: {angle}") if p
        ) or None
        try:
            more = await asyncio.wait_for(
                self.enricher.facts_for(place, ctx, lang), timeout=self.enrich_timeout_s
            )
        except Exception:  # noqa: BLE001 — deepen is best-effort (incl. timeout); keep existing
            self._update_subject_state(
                "place",
                _place_subject_key(place.id),
                lang,
                deepen_round=deepen_round,
                source_tier="web",
                status="transient_error",
                facts=existing,
            )
            return existing
        if not more:
            self._update_subject_state(
                "place",
                _place_subject_key(place.id),
                lang,
                deepen_round=deepen_round,
                source_tier="web",
                status="dry",
                facts=existing,
            )
            return existing
        have = existing.lower()
        fresh = [s for s in atomize_facts(more) if s.lower() not in have]
        if not fresh:
            self._update_subject_state(
                "place",
                _place_subject_key(place.id),
                lang,
                deepen_round=deepen_round,
                source_tier="web",
                status="ready",
                facts=existing,
            )
            return existing
        merged = (existing.rstrip() + " " + " ".join(fresh)).strip()
        self.cache.put(
            place.id,
            merged,
            lang,
            meta=FactBatchMeta(
                source_tier="web",
                status="ready",
                fact_count=len(atomize_facts(merged)),
                char_count=len(merged),
            ),
        )  # so later ticks reuse the richer facts
        self._update_subject_state(
            "place",
            _place_subject_key(place.id),
            lang,
            deepen_round=deepen_round,
            source_tier="web",
            status="ready",
            facts=merged,
        )
        return merged

    async def narrate_area(
        self,
        address: Address,
        *,
        facts: str | None,
        theme: str | None,
        topic: str | None,
        told: list[str],
        next_hook: str | None,
        last_place_name: str | None,
        history: list[str],
        pace: Pace = Pace.SLOW,
        language: str | None = None,
        beat_mode: str | None = None,
        visible: list[str] | None = None,
        on_street: bool = False,
    ) -> tuple[str, str | None]:
        """One beat of the area-level monologue — advance the story arc by one
        topic, staying inside the theme. Returns (spoken_text, next_hook); spoken
        text is "" for silence."""
        raw = await self.narrator.narrate_area(
            AreaInput(
                address=address,
                facts=facts,
                theme=theme,
                topic=topic,
                told=told,
                next_hook=next_hook,
                last_place_name=last_place_name,
                history=history,
                pace=pace,
                beat_mode=beat_mode,
                visible=visible or [],
                on_street=on_street,
                language=language or self.language,
            )
        )
        return split_hook(raw, language or self.language)

    async def make_plan(
        self, address: Address, *, facts: str | None, theme_override: str | None,
        language: str | None = None,
    ):
        """Form the story arc (theme + outline + opener) for a freshly entered area."""
        from app.shared.schemas import PlannerInput

        if self.planner is None:
            return None
        return await self.planner.plan(
            PlannerInput(
                address=address,
                facts=facts,
                theme_override=theme_override,
                language=language or self.language,
            )
        )

    async def warm_plan(
        self, area_key: str, address: Address, *, facts, theme_override, language
    ) -> None:
        """Background: pre-generate the story arc for `area_key` and cache it, so the first
        area intro is instant. Read-only w.r.t. session state; a no-op if already warmed or the
        planner is offline."""
        if not area_key or area_key in self._plan_cache:
            return
        draft = await self.make_plan(
            address, facts=facts, theme_override=theme_override, language=language
        )
        if draft is not None:
            self._plan_cache[area_key] = draft
            if len(self._plan_cache) > 8:  # bound: a handful of recent areas
                self._plan_cache.pop(next(iter(self._plan_cache)))

    def take_plan(self, area_key: str | None):
        """Pop a pre-generated arc for `area_key` (or None if not warmed)."""
        return self._plan_cache.pop(area_key, None) if area_key else None

    def warm_startup_area_beat(
        self,
        area_key: str,
        *,
        language: str,
        topic: str,
        text: str,
        hook: str | None,
    ) -> None:
        key = (area_key, normalize(language), topic)
        self._startup_area_cache[key] = (text, hook)
        if len(self._startup_area_cache) > 16:
            self._startup_area_cache.pop(next(iter(self._startup_area_cache)))

    def peek_startup_area_beat(
        self, area_key: str | None, *, language: str, topic: str | None
    ) -> tuple[str, str | None] | None:
        if not area_key or not topic:
            return None
        return self._startup_area_cache.get((area_key, normalize(language), topic))

    def take_startup_area_beat(
        self, area_key: str | None, *, language: str, topic: str | None
    ) -> tuple[str, str | None] | None:
        if not area_key or not topic:
            return None
        return self._startup_area_cache.pop((area_key, normalize(language), topic), None)

    async def enrich_area(
        self,
        address: Address,
        point: GeoPoint | None,
        *,
        timeout_s: float | None = None,
        language: str | None = None,
        angle: int = 0,
    ) -> str | None:
        """Fetch verified, atypical facts about the current district/street/city via
        web search. Slow-changing -> the orchestrator caches it once per area. `angle`
        rotates the search focus (history → people → streets → today) so a long stay in
        one area keeps finding GENUINELY NEW facts instead of drying up. The facts are
        written in the session language so the monologue doesn't leak the sources'
        (often Russian) language verbatim."""
        if self.area_llm is None:
            return None
        where = " ".join(p for p in (address.district, address.street, address.city) if p)
        if not where:
            return None
        coords = f"coordinates {point.lat:.4f}, {point.lon:.4f}" if point else ""
        focus = _AREA_ANGLES[angle % len(_AREA_ANGLES)]
        query = f"{where} {coords} {focus}".strip()
        system = _AREA_ENRICH_SYSTEM + _lang_directive(language or self.language)
        # The OpenRouter web plugin is NON-DETERMINISTIC: for the SAME area query it either does the
        # search (~14-16 s, rich facts) or returns empty/no-data FAST (~2-3 s) ~40% of the time
        # (measured on prod). A single call therefore left ~half of all areas factless -> silent.
        # Retry on a fast empty (cheap) within a time budget; a slow attempt that times out is not
        # retried (it wouldn't fit). This is what actually makes an area reliably factful.
        budget = timeout_s or 25.0
        deadline = time.monotonic() + budget
        for attempt in range(_AREA_ENRICH_MAX_ATTEMPTS):
            remaining = deadline - time.monotonic()
            # Always try at least ONCE; only gate RETRIES on the remaining budget.
            if attempt > 0 and remaining < _AREA_ENRICH_MIN_ATTEMPT_S:
                break
            per = min(max(remaining, 1.0), _AREA_ENRICH_ATTEMPT_CAP_S) if timeout_s else None
            try:
                coro = self.area_llm.web_facts(
                    system, query,
                    max_results=settings.web_search_max_results,
                    max_tokens=settings.web_search_max_tokens,
                )
                text = await (asyncio.wait_for(coro, timeout=per) if per else coro)
            except Exception:  # noqa: BLE001 — timeout/transient: a slow attempt won't fit a retry
                return None
            cleaned = (text or "").strip()
            if cleaned and not _is_no_data(cleaned):
                return cleaned
            # empty / no-data (usually a fast 2-3 s) -> loop and retry within the budget
        return None

    async def warm_area_facts(
        self, area_key: str | None, address: Address, point: GeoPoint | None,
        *, timeout_s: float | None = None, language: str | None = None, angle: int = 0,
    ) -> None:
        """Background: fetch the area facts and cache them by (`area_key`, lang, `angle`), so a
        beat serves them instantly instead of blocking on web search. `angle` is the deepen
        round (see enrich_area) — round 0 is the first batch, later rounds keep the monologue
        supplied. Best-effort — safe to call repeatedly / concurrently.

        Only NON-EMPTY facts are cached: a transient failure (timeout/429) must NOT poison the area
        for the whole session. A genuinely dry angle caches "" so the caller advances past it."""
        if not area_key:
            return
        key = (area_key, language or self.language, angle)
        if key in self._area_facts_cache or key in self._area_warm_inflight:
            # In-flight guard: _area_line now re-kicks the warm every not-yet-warmed tick
            # (the inline blocking fetch is gone) — without this, each tick would spawn a
            # duplicate concurrent web search for the same area.
            return
        self._area_warm_inflight.add(key)
        try:
            # Background + non-blocking, so give it a GENEROUS budget: more retries of the
            # flaky web plugin fit -> the area reliably lands facts before the beats need them.
            budget = (timeout_s or 25.0) * 2
            facts = await self.enrich_area(
                address, point, timeout_s=budget, language=language, angle=angle
            )
            # Cache the result — non-empty facts, OR "" for a dry angle (round > 0 only) so the
            # deepen loop can advance past a barren angle instead of re-fetching it forever. For
            # round 0 keep the old contract (only cache non-empty; None => inline retry).
            if facts:
                self._area_facts_cache.setdefault(key, facts)
                self._update_subject_state(
                    "area",
                    area_key,
                    language or self.language,
                    deepen_round=angle,
                    source_tier="web",
                    status="ready",
                    facts=facts,
                )
                if self.fact_buffer is not None:
                    self.fact_buffer.put_area(
                        area_key,
                        facts,
                        language or self.language,
                        angle=angle,
                        meta=FactBatchMeta(
                            source_tier="web",
                            status="ready",
                            fact_count=len(atomize_facts(facts)),
                            char_count=len(facts),
                        ),
                    )
            elif angle > 0:
                self._area_facts_cache.setdefault(key, "")
                self._update_subject_state(
                    "area",
                    area_key,
                    language or self.language,
                    deepen_round=angle,
                    source_tier="web",
                    status="dry",
                    facts="",
                )
        finally:
            self._area_warm_inflight.discard(key)

    def take_area_facts(
        self, area_key: str | None, language: str | None = None, *, angle: int = 0
    ) -> str | None:
        """Peek warmed area facts for (`area_key`, language, `angle`) — a string ("" = warmed-
        but-dry), or None when not warmed yet (the caller then fetches inline / kicks a warm).
        Peek, not pop: serves beats."""
        if not area_key:
            return None
        key = (area_key, language or self.language, angle)
        if key in self._area_facts_cache:
            return self._area_facts_cache[key]
        if self.fact_buffer is not None:
            facts = self.fact_buffer.get_area(area_key, language or self.language, angle=angle)
            if facts is not None:
                self._area_facts_cache[key] = facts
                self.subject_coverage("area", area_key, language or self.language, facts)
            return facts
        return None
