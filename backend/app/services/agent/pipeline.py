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
from app.services.llm.client import SESSION_TIER
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

from .languages import passing_mention
from .name_localizer import NameLocalizer
from .narrator import Narrator, split_hook
from .scorer import Scorer
from .significance import at_least, significance_from_weight
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

    def warm_ahead(
        self,
        candidates: list[Candidate],
        *,
        address: Address | None = None,
        language: str | None = None,
    ):
        """Non-blocking: warm the fact cache for objects the user is walking TOWARD
        (in the course cone, nearest first), so facts are ready before arrival. A
        no-op on the mock/inline path (`enrich_top_k is None`). Returns the scheduled
        task (or None) so callers/tests can await it; the orchestrator ignores it."""
        if self.enrich_top_k is None or not candidates:
            return None
        lang = language or self.language
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
            prefetch(
                ahead,
                self.enricher,
                self.cache,
                top_k=settings.enrich_lookahead_k,
                timeout_s=self.enrich_timeout_s,
                context=ctx,
                language=lang,
            )
        )
        self._warm_tasks.add(task)
        task.add_done_callback(self._warm_tasks.discard)
        return task

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
        reach: bool = False,
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
        sig = significance_from_weight(chosen.type_weight, chosen.facts_available)
        # Visible now = in the forward gaze cone AND inside the narrate bubble. Threaded
        # so the narrator frames "вон то, перед тобой" vs "проходишь мимо / не видно" (A5).
        # On a REACH (last-resort fallback before silence) the gate is the cone alone: an
        # in-cone object further ahead is still something the walker can SEE, so frame it
        # as "виднеется впереди", not "не видно".
        in_view = chosen.in_gaze_cone and (
            reach or chosen.distance_m <= settings.narrate_radius_m
        )
        raw = await self.narrator.narrate(
            NarratorInput(
                place=place,
                significance=sig,
                facts=chosen.facts_snippet,
                distance_m=chosen.distance_m,
                heading=heading or Heading(),
                side=chosen.side,
                in_view=in_view,
                pace=pace,
                context=_context(addr),
                theme=theme,
                told=told or [],
                next_hook=next_hook,
                history=history,
                flags=NarratorFlags(
                    switching=switching,
                    nothing_new=not candidates,
                    passing=passing,
                    preferences=preferences,
                ),
                language=lang,
            )
        )
        text, hook = split_hook(raw, lang)
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
        # Silence on a genuinely notable object with no facts is the cue to spend a web
        # search and try again. The usual cause is `chosen` sitting outside the
        # enrich_top_k prefetch window, so its facts never warmed; an on-demand
        # single-object enrich (wiki -> paid web, self-gated in CompositeEnricher) fills
        # that gap. Gated to paid sessions (cost) and MEDIUM+ (don't chase shops/benches),
        # and the enricher's per-place negative cache prevents a second spend on the same
        # object. Mirrors elaborate()'s cache-miss dance.
        if (
            not text
            and not chosen.facts_available
            and at_least(sig, Significance.MEDIUM)
            and SESSION_TIER.get() == "paid"
        ):
            await prefetch(
                [chosen], self.enricher, self.cache,
                top_k=1, timeout_s=self.enrich_timeout_s, context=ctx, language=lang,
            )
            facts = self.cache.get(place.id, lang)
            if facts:
                retry_raw = await self.narrator.narrate(
                    NarratorInput(
                        place=place, significance=sig, facts=facts,
                        distance_m=chosen.distance_m, heading=heading or Heading(),
                        side=chosen.side, in_view=in_view, pace=pace,
                        context=_context(addr), theme=theme,
                        told=told or [], next_hook=next_hook, history=history,
                        flags=NarratorFlags(
                            switching=switching, nothing_new=not candidates,
                            passing=passing, preferences=preferences,
                        ),
                        language=lang,
                    )
                )
                retry_text, retry_hook = split_hook(retry_raw, lang)
                if retry_text:
                    text, hook = retry_text, retry_hook
                    log.info("step websearch-retry place=%r -> recovered facts", place.name)
        log.info(
            "step place=%r cat=%s sig=%s facts=%s side=%s passing=%s reach=%s -> %s | %s",
            place.name, place.category, sig.value, chosen.facts_available,
            chosen.side, passing, reach,
            "floor" if floored else ("text" if text else "silence"),
            clip(text),
        )
        return StepResult(text, ScorerOutput(), place, sig, next_hook=hook)

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
    ) -> str:
        """Tell MORE about an already-covered place (nothing new nearby). Reuses
        cached facts; the narrator adds a fresh detail, avoiding HISTORY."""
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
                flags=NarratorFlags(elaborate=True),
                language=lang,
            )
        )
        text, _ = split_hook(raw, lang)  # elaborate stays on the same place; drop the hook
        return text

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
