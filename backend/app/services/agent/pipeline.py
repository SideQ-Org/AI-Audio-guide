"""Per-tick text pipeline: discovery candidates -> facts -> Scorer -> Narrator.

This is the Stage-2 core (no FSM/persistence yet — that's the orchestrator in
Stage 3). The caller owns seen-list and history across ticks.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import settings
from app.services.enrichment.enricher import (
    Enricher,
    EnrichmentCache,
    _is_no_data,
    _lang_directive,
    attach_facts,
    prefetch,
)
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
from .languages import passing_mention
from .name_localizer import NameLocalizer
from .narrator import (
    Narrator,
    split_card,
    split_hook,
    split_sentences,
    strip_factless_history,
)
from .scorer import Scorer
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
    "stadium", "cemetery", "fountain", "hospital", "clinic", "school", "university",
    "college", "library", "marketplace",
})


@dataclass
class StepResult:
    text: str  # "" means silence
    decision: ScorerOutput
    place: Place | None
    significance: Significance | None
    next_hook: str | None = None  # baton to weave into the next paragraph
    card: str | None = None  # re-readable structured facts for the object card (not spoken)
    image: str | None = None  # object photo URL (Wikipedia thumbnail) for the card, if any


# Atypical-facts-forward area enrichment: lesser-known facts about the district /
# street / city, not the obvious encyclopedic blurb.
_AREA_ENRICH_SYSTEM = (
    "You gather atypical, little-known facts about a district/street/city for an "
    "audio guide. Give 2-4 short, reliable facts about this exact district or street "
    "in the named city: unusual history, how the place came to be and changed, "
    "forgotten episodes, what it's known for in narrow circles. Skip the obvious and "
    "the commonly-known. Verifiable facts only, no invention or opinions. If there is "
    "no reliable information about this exact district, reply with exactly: NONE."
)


def _context(addr: Address) -> NarrationContext:
    return NarrationContext(
        city=addr.city, district=addr.district, street=addr.street,
        street_confident=addr.street_confident,
    )


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
    ) -> None:
        self.scorer = scorer
        self.narrator = narrator
        self.enricher = enricher
        self.cache = cache or EnrichmentCache()
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
        # Pre-fetched area FACTS: (area_key, lang) -> facts ("" for a warmed-but-dry area). Warmed
        # in the background at area entry so the FIRST area beat doesn't block ~9 s on web search
        # (the "медленно переключался между блоками" gap). Peeked by the orchestrator's _area_line.
        # Keyed by language too: facts are written in the session language (pipeline is shared).
        self._area_facts_cache: dict[tuple[str, str], str] = {}
        # Objects we've already run the elaborate "deepen" fetch for (one extra web search per
        # object, keyed (place_id, lang)), so going deeper doesn't re-hit the web every follow-up.
        self._deepened: set[tuple[str, str]] = set()

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
        ahead = pending[: settings.enrich_lookahead_k]
        if not ahead:
            return None
        addr = address or Address()
        ctx = ", ".join(p for p in (addr.city, addr.country) if p) or None
        task = asyncio.ensure_future(
            as_background(prefetch(
                ahead,
                self.enricher,
                self.cache,
                top_k=settings.enrich_lookahead_k,
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
        passed=False, callback=None, lookahead=None,
    ) -> tuple[str, str | None, str | None]:
        """The narrator call for one chosen object — shared by step() and warm_narration()
        so a pre-generated blurb matches what step would produce. Returns (spoken, hook, card):
        the CARD block is stripped FIRST (before HOOK, whose matcher runs to end-of-text)."""
        raw = await self.narrator.narrate(
            NarratorInput(
                place=place, significance=sig, facts=chosen.facts_snippet,
                distance_m=chosen.distance_m, heading=heading or Heading(),
                side=chosen.side, in_view=in_view, pace=pace, context=_context(addr),
                theme=theme, told=told or [], next_hook=next_hook, history=history,
                callback=callback, lookahead=lookahead,
                flags=NarratorFlags(
                    switching=switching, nothing_new=nothing_new,
                    passing=passing, passed=passed, preferences=preferences,
                ),
                language=lang,
            )
        )
        body, card = split_card(raw)
        text, hook = split_hook(body, lang)
        return text, hook, card

    async def warm_narration(
        self, chosen, *, seen, history, address, heading, pace, preferences,
        language, theme, told, next_hook, recall=None, lookahead=None,
    ) -> None:
        """Pre-render the PASSING narration for an object you're walking toward, so
        step() speaks it the instant you reach it (no LLM wait on arrival). Facts are
        warmed first; a cold-facts silence just isn't cached (step generates + floors
        on arrival as usual)."""
        lang = language or self.language
        key = (chosen.place.id, lang)
        if chosen.place.id in set(seen) or key in self._narr_cache:
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

    def _start_fact_warm(self, candidates: list[Candidate], ctx: str | None, lang: str) -> None:
        """Fire-and-forget: warm a notable factless object's facts in the background (Phase 4
        async recovery), so the enriched narration is delivered later by elaborate() / a re-open
        WITHOUT blocking this tick on a ~9 s web search."""
        task = asyncio.ensure_future(
            as_background(prefetch(
                candidates, self.enricher, self.cache,
                top_k=1, timeout_s=self.enrich_timeout_s, context=ctx, language=lang,
            ))
        )
        self._warm_tasks.add(task)
        task.add_done_callback(self._warm_tasks.discard)

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
        recall=None,
        lookahead=None,
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
        await prefetch(
            candidates,
            self.enricher,
            self.cache,
            top_k=self.enrich_top_k,
            timeout_s=self.enrich_timeout_s,
            context=ctx,
            language=lang,
        )
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
                callback=callback, lookahead=lookahead,
            )
        # Anti-fabrication backstop: with NO verified facts, any history/date/creation claim the
        # model slipped in is invented (the "детсад «Ивушка» появился в те годы…" case). Strip
        # those sentences, keep the naming/visible ones. Applies to the cached pre-gen too (it may
        # have been warmed with cold facts). If this empties a notable/ambient object, the floor
        # below still names it deterministically; a plain LOW object correctly falls to silence.
        if text and not chosen.facts_available:
            text = strip_factless_history(text, lang)
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
            (floored or not text)
            and not chosen.facts_available
            and at_least(sig, Significance.MEDIUM)
            and SESSION_TIER.get() == "paid"
        ):
            self._start_fact_warm([chosen], ctx, lang)
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
        return StepResult(
            text, ScorerOutput(), place, sig, next_hook=hook, card=card, image=image
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
        if (
            angle
            and settings.elaborate_deepen_below_chars > 0
            and len(facts) < settings.elaborate_deepen_below_chars
            and (place.id, lang) not in self._deepened
        ):
            self._deepened.add((place.id, lang))
            facts = await self._deepen_facts(place, facts, angle=angle, addr=addr, lang=lang)
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
        self, place: Place, existing: str, *, angle: str, addr: Address, lang: str
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
            return existing
        if not more:
            return existing
        have = existing.lower()
        fresh = [s for s in atomize_facts(more) if s.lower() not in have]
        if not fresh:
            return existing
        merged = (existing.rstrip() + " " + " ".join(fresh)).strip()
        self.cache.put(place.id, merged, lang)  # so later ticks reuse the richer facts
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
                language=language or self.language,
            )
        )
        return split_hook(raw, language or self.language)

    async def make_plan(
        self, address: Address, *, facts: str | None, theme_override: str | None, language: str | None = None
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

    async def enrich_area(
        self,
        address: Address,
        point: GeoPoint | None,
        *,
        timeout_s: float | None = None,
        language: str | None = None,
    ) -> str | None:
        """Fetch verified, atypical facts about the current district/street/city via
        web search. Slow-changing -> the orchestrator caches it once per area. The
        facts are written in the session language so the area monologue doesn't leak
        the sources' (often Russian) language verbatim."""
        if self.area_llm is None:
            return None
        where = " ".join(p for p in (address.district, address.street, address.city) if p)
        if not where:
            return None
        coords = f"coordinates {point.lat:.4f}, {point.lon:.4f}" if point else ""
        query = f"{where} {coords} neighbourhood history what it's known for unusual facts".strip()
        system = _AREA_ENRICH_SYSTEM + _lang_directive(language or self.language)
        try:
            coro = self.area_llm.web_facts(
                system, query, max_results=3, max_tokens=400
            )
            text = (await asyncio.wait_for(coro, timeout=timeout_s) if timeout_s else await coro)
        except (Exception, asyncio.TimeoutError):
            return None
        cleaned = (text or "").strip()
        if not cleaned or _is_no_data(cleaned):
            return None
        return cleaned

    async def warm_area_facts(
        self, area_key: str | None, address: Address, point: GeoPoint | None,
        *, timeout_s: float | None = None, language: str | None = None,
    ) -> None:
        """Background: fetch the area facts once and cache them by `area_key`, so the FIRST area
        beat serves them instantly instead of blocking ~9 s on web search. Caches "" for a dry
        area too (so it isn't refetched). Best-effort — safe to call repeatedly / concurrently."""
        if not area_key:
            return
        key = (area_key, language or self.language)
        if key in self._area_facts_cache:
            return
        facts = await self.enrich_area(address, point, timeout_s=timeout_s, language=language)
        self._area_facts_cache.setdefault(key, facts or "")

    def take_area_facts(self, area_key: str | None, language: str | None = None) -> str | None:
        """Peek warmed area facts for (`area_key`, language) — a string ("" = warmed-but-dry), or
        None when not warmed yet (the caller then fetches inline). Peek, not pop: serves beats."""
        if not area_key:
            return None
        return self._area_facts_cache.get((area_key, language or self.language))
