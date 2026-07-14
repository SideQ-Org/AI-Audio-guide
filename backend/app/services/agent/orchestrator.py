"""Orchestrator: the single stateful brain. Owns the FSM and all state.

It is the only component that calls Geo, the pipeline (Scorer/Narrator),
Companion and the state store. Roles stay stateless and talk only through the
SessionState the orchestrator hands them.

FSM (states x events -> next), incl. degradation paths from the review:

    IDLE/EXPANDING/NARRATING/SWITCHING/ANSWERING ──TICK──▶ SCORING
    SCORING ──NARRATED──▶ NARRATING   ──SWITCH──▶ SWITCHING
    SCORING ──SILENCE──▶ IDLE         ──EXPANDED──▶ EXPANDING
    SCORING ──FAILURE──▶ ERROR        ERROR ──TICK──▶ RECOVERY ──TICK──▶ SCORING
    (any)   ──USER_SPEECH──▶ LISTENING ──ANSWERED──▶ ANSWERING
    (any)   ──GO_OFFLINE──▶ OFFLINE    OFFLINE ──GO_ONLINE──▶ RECOVERY
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum

from app.config import settings
from app.services.agent import languages as lang
from app.services.agent.companion import Companion
from app.services.agent.director import atomize_facts, find_lookahead, find_revisit
from app.services.agent.narrator import split_hook
from app.services.agent.pipeline import TextPipeline
from app.services.agent.significance import significance_from_weight, tags_have_wiki
from app.services.agent.walklog import (
    CURRENT_SID,
    clip,
    get_logger,
    tick_reset,
    tick_snapshot,
)
from app.services.geo.categories import LINEAR_CATEGORIES
from app.services.geo.discovery import Discovery
from app.services.geo.geocoder import Geocoder
from app.services.geo.ranking import Dedup, _norm_name, build_candidates
from app.services.llm.client import as_background
from app.services.metrics import GUIDE
from app.services.state.store import StateStore
from app.shared.geo_math import haversine_m
from app.shared.memory import ObjectMemo
from app.shared.schemas import (
    Candidate,
    CompanionInput,
    ControlPatch,
    GeoPoint,
    Heading,
    NarrativePlan,
    Pace,
    Place,
    SessionState,
    Significance,
)

# Recent narrations fed back to the roles as the no-repeat window. 18 (was 12): the
# anti-repeat check is "don't say anything already in HISTORY", so a too-short window
# let facts told ~12 beats ago resurface on a long single street. Wider window, fewer
# repeats — at a modest prompt-size cost.
_HISTORY_CAP = 18
_SEEN_CAP = 600  # cap the dedup list so a long walk can't grow session state unbounded
_TOLD_CAP = 80  # cap the arc's covered-topics ledger
_CONVO_CAP = 20
_PATH_STEP_M = 12.0  # min spacing between stored breadcrumb points (walk-history route)
_PATH_MAX_POINTS = 3000  # cap the stored path so a long walk stays bounded
# Follow-ups per place when nothing new is nearby. Kept low: a couple of extra
# details is enough — beyond that the guide starts mussing the same place, which
# is exactly the "цепляет одну тему и мусолит её" complaint.
_MAX_ELABORATE = 2
# The gap-filler monologue is a city -> district -> street CASCADE: at each level the
# guide tells atypical facts until that level runs out of NEW ones (the Narrator
# returns [SILENCE]), then descends a level; after the street is exhausted it goes
# quiet (a short "пройдём дальше" bridge) — by then the walker is usually onto a new
# street/district, which restarts the cascade. This replaces the old flat "connective
# angles", which rambled in circles ("мусолит одну тему") or bailed to silence too fast.
_BEATS_PER_LEVEL = 3  # soft cap of facts per level before descending (no-repeat trims it)
_LEVEL_ATTEMPTS_PER_TICK = 3  # one lull tick may descend city->district->street if dry
# Short spoken bridges for when the area material is exhausted and nothing is near:
# say one ("let's move on") and then go genuinely silent, rather than filler. These
# are spoken VERBATIM, so they live in languages.py and are picked by session language.
# Hard ceiling on the adaptive-radius discovery per tick. Discovery now makes at
# most two Overpass calls (tight, then one wide), each with its own mirror-failover
# timeout; this caps the pair so a tick can't stall for minutes in a sparse/foreign
# place, while leaving enough room for the wide query + one failover. On timeout we
# keep talking about the current place rather than going silent.
_DISCOVERY_DEADLINE_S = 20.0


class State(StrEnum):
    IDLE = "idle"
    EXPANDING = "expanding"
    SCORING = "scoring"
    NARRATING = "narrating"
    SWITCHING = "switching"
    LISTENING = "listening"
    ANSWERING = "answering"
    OFFLINE = "offline"
    ERROR = "error"
    RECOVERY = "recovery"


@dataclass
class OrchestratorOutput:
    state: str
    kind: str  # narration | silence | reply | error | offline
    text: str = ""
    place_id: str | None = None
    significance: str | None = None
    place_name: str | None = None
    lat: float | None = None
    lon: float | None = None
    card: str | None = None  # structured, re-readable facts for the object card (not spoken)
    image: str | None = None  # object photo URL (Wikipedia lead image) for the card, if any
    category: str | None = None  # OSM-derived category (card icon + label on the client)


def fingerprint(candidates: list[Candidate], cache=None, language: str = "ru") -> str:
    """A stable signature of the bubble set used to gate the LLM. When a fact `cache`
    is given it's FACTS-AWARE: each id is tagged with whether its facts are cached yet
    (in the session `language`). That keeps the gate stable for a genuinely factless
    object (no LLM re-call every tick), but RE-OPENS it the instant warm_ahead caches
    facts for a passing object whose facts were cold when it entered the bubble — so
    "walk up to a monument -> it gets narrated" is reliable instead of being burned
    forever by the first cold miss."""
    if cache is None:
        return ",".join(sorted(c.place.id for c in candidates))
    return ",".join(
        f"{c.place.id}:{int(cache.get(c.place.id, language) is not None)}"
        for c in sorted(candidates, key=lambda c: c.place.id)
    )


log = get_logger()  # shared walk logger (aiguide.agent); see walklog.py

# Anti-repeat now lives in SessionState.memory (WalkMemory, shared/memory.py): the corpus
# is the WHOLE walk, not a window, and callers use `st.memory.is_repeat(text)`.

def _local_hour(lon: float | None) -> int:
    """Rough local hour (0-23) from longitude — 15°/h — good enough for a morning/day/
    evening greeting without a timezone database."""
    from datetime import datetime

    utc = datetime.now(UTC)
    return int((utc.hour + utc.minute / 60.0 + (lon or 0.0) / 15.0) % 24)


def merge_patch(base: ControlPatch, patch: ControlPatch) -> ControlPatch:
    return ControlPatch(
        skip_categories=sorted(set(base.skip_categories) | set(patch.skip_categories)),
        focus_topics=sorted(set(base.focus_topics) | set(patch.focus_topics)),
        verbosity=patch.verbosity or base.verbosity,
        mute=patch.mute or base.mute,
    )


def _dedup(st) -> Dedup:
    """Cross-object anti-repeat set from session state — fed to build_candidates so a duplicate
    OSM object of an already-narrated real-world thing is dropped (wikidata / linear name /
    same name nearby). See ranking.Dedup."""
    return Dedup(
        linear_names=frozenset(_norm_name(n) for n in st.seen_linear_names),
        wikidata=frozenset(st.seen_wikidata),
        named=tuple((n, la, lo) for n, la, lo in st.seen_named),
    )


class Orchestrator:
    def __init__(
        self,
        discovery: Discovery,
        pipeline: TextPipeline,
        companion: Companion,
        store: StateStore,
        geocoder: Geocoder | None = None,
        summarizer=None,
    ) -> None:
        self.discovery = discovery
        self.pipeline = pipeline
        self.companion = companion
        self.store = store
        self.geocoder = geocoder
        self.summarizer = summarizer
        self._bg: set[asyncio.Task] = set()  # hold refs to fire-and-forget warm tasks

    # Ranking of in-bubble candidates: distance, with a bonus for objects in the gaze
    # cone (visible ahead). 0.6 => a 70 m object ahead ranks like ~42 m, so it beats a
    # far behind one but never a genuinely closer object (B2).
    _VISIBLE_BONUS = 0.6

    @classmethod
    def _visible_rank(cls, c: Candidate) -> float:
        return c.distance_m * (cls._VISIBLE_BONUS if c.in_gaze_cone else 1.0)

    # -- narration hot-path ------------------------------------------------- #
    @staticmethod
    def _append_path(st: SessionState, position: GeoPoint, *, paused: bool) -> None:
        """Breadcrumb the walk for the history route: keep points ~>= _PATH_STEP_M apart
        (distance-gated so standing still doesn't spam it), capped so a long walk stays
        bounded in memory and in the persisted payload. A point walked while the tour is
        paused carries a trailing `1.0` flag (`[lat, lon, 1.0]`) so the history map can
        draw that stretch differently; normal points stay 2-element `[lat, lon]`."""
        if not st.path or (seg := haversine_m(
            GeoPoint(lat=st.path[-1][0], lon=st.path[-1][1]), position
        )) >= _PATH_STEP_M:
            # Odometer for the revisit gate: only count real walking (not paused stretches).
            if st.path and not paused:
                st.route_len_m += seg
            point = [round(position.lat, 6), round(position.lon, 6)]
            if paused:
                point.append(1.0)
            st.path.append(point)
            if len(st.path) > _PATH_MAX_POINTS:
                del st.path[: len(st.path) - _PATH_MAX_POINTS]

    async def breadcrumb_paused(self, session_id: str, position: GeoPoint) -> None:
        """Record a GPS point while the tour is paused: flag it in the route AND refresh
        the walk's last-event clock so a long pause doesn't rotate the tour into a second
        walk (`history.record_object` gap-splits after `walk_gap_s`). No generation runs —
        this is the only session work done while paused."""
        st = await self.store.load(session_id)
        st.position = position
        self._append_path(st, position, paused=True)
        if st.walk_id is not None:
            st.walk_last_event_at = time.time()
        await self.store.save(st)

    async def peek_bubble(
        self, session_id: str, position: GeoPoint, heading: Heading
    ) -> tuple[str, Significance] | None:
        """CHEAP (no network): is there a fresh object right in the narrate bubble at this
        live position? Ranks the already-cached inventory disc. Returns `(id, significance)`
        for the nearest unseen object within `narrate_radius_m` that isn't the one we're
        already narrating — the signal to weave a place in, plus its significance for the
        priority decision. None when there's nothing new to jump to."""
        inv_store = getattr(self.discovery, "inventory", None)
        inv = inv_store.peek(session_id) if inv_store is not None else None
        if inv is None or not inv.places:
            return None
        st = await self.store.load(session_id)
        cands = build_candidates(
            position, heading, inv.places, settings.narrate_radius_m, st.seen_place_ids,
            _dedup(st),
        )
        if not cands:
            return None
        top = min(cands, key=self._visible_rank)
        if top.place.id == st.last_place_id:
            return None
        facts = self.pipeline.cache.has(top.place.id, st.language)
        return top.place.id, significance_from_weight(
            top.type_weight, facts, has_wiki=tags_have_wiki(top.place.tags)
        )

    def _warm_inventory(self, session_id: str, position: GeoPoint) -> None:
        """Non-blocking: fetch the Overpass disc for this session NOW (during the greeting),
        so the first real discovery is served from cache instead of a cold ~3 s network
        fetch — the 'long pause while it looked around' at startup."""
        inv = getattr(self.discovery, "inventory", None)
        prov = getattr(self.discovery, "provider", None)
        if inv is None or prov is None:
            return
        try:
            asyncio.ensure_future(inv.ensure(session_id, position, prov))
        except Exception:  # noqa: BLE001 — a warm failure must never disturb the greeting
            pass

    def _warm_area_intro(
        self, position: GeoPoint, language: str, theme_override: str | None
    ) -> None:
        """Non-blocking: geocode + pre-generate the area story arc during greeting delivery,
        cached by area_key, so _maybe_area_intro on the next tick serves it instantly instead
        of a cold planner LLM wait. Read-only — never touches session state; matches the live
        area_key (district|city) so the next tick's resolve finds it."""
        if self.geocoder is None:
            return

        async def _run() -> None:
            try:
                addr = await self.geocoder.reverse(position, language)
                key = addr.district or addr.city
                if key:
                    await self.pipeline.warm_plan(
                        key, addr, facts=None, theme_override=theme_override, language=language
                    )
            except Exception:  # noqa: BLE001 — a warm failure must never disturb the tour
                pass

        task = asyncio.ensure_future(as_background(_run()))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def narrate_object(
        self, session_id: str, place_id: str, *, passed: bool = False
    ) -> OrchestratorOutput:
        """Narrate a SPECIFIC object on demand — the scheduler's deferred mention of a
        newcomer that surfaced while a higher-priority object was being told. Ranks the
        object from the cached inventory, narrates it, and commits like a normal step. When
        `passed`, the object is already behind us: the Narrator frames it in the past tense
        (`NarratorFlags.passed`, not a canned prefix). Silence if it's already seen or no
        longer in the disc."""
        st = await self.store.load(session_id)
        if place_id in st.seen_place_ids or st.position is None:
            return await self._finish(st, State.IDLE, "silence")
        inv_store = getattr(self.discovery, "inventory", None)
        inv = inv_store.peek(session_id) if inv_store is not None else None
        place = next((p for p in (inv.places if inv else []) if p.id == place_id), None)
        if place is None:
            return await self._finish(st, State.IDLE, "silence")
        cands = build_candidates(
            st.position, st.heading, [place], settings.weave_radius_m, st.seen_place_ids,
            _dedup(st),
        )
        if not cands:
            return await self._finish(st, State.IDLE, "silence")
        plan = st.narrative_plan
        try:
            out = await self.pipeline.step(
                cands, seen=st.seen_place_ids, history=st.narration_history,
                address=st.address, heading=st.heading, pace=st.pace,
                preferences=st.control_patch, language=st.language,
                theme=plan.active_theme() or None, told=plan.told,
                next_hook=plan.next_hook, passing=True, passed=passed,
                recall=st.memory.objects,
            )
        except Exception:
            return await self._finish(st, State.ERROR, "error")
        if not (out.text and out.place):
            return await self._finish(st, State.IDLE, "silence")
        log.info("narrate deferred passed=%s place=%r | %s",
                 passed, out.place.name, clip(out.text))
        return await self._commit_step(st, out)

    async def on_position(
        self, session_id: str, position: GeoPoint, heading: Heading, pace: Pace
    ) -> OrchestratorOutput:
        # Debug envelope: stamp the session onto every log line of this tick, reset the
        # per-tick call counters, and emit a one-line summary (outcome + how many
        # external calls + wall time) when the tick returns — so the whole walk can be
        # reconstructed call-by-call. All best-effort; never disturbs narration.
        CURRENT_SID.set(session_id)
        tick_reset()
        t0 = time.perf_counter()
        llm0 = self._llm_calls(session_id)
        out = await self._on_position_impl(session_id, position, heading, pace)
        calls = tick_snapshot()
        enrich_n = calls.get("wiki", 0) + calls.get("web", 0) + calls.get("enrich_hit", 0)
        log.info(
            "tick -> %s | llm=+%d overpass=%d enrich=%d(hit=%d) t=%dms",
            out.kind, self._llm_calls(session_id) - llm0, calls.get("overpass", 0),
            enrich_n, calls.get("enrich_hit", 0), int((time.perf_counter() - t0) * 1000),
        )
        return out

    @staticmethod
    def _llm_calls(session_id: str) -> int:
        """Cumulative LLM call count for a session (from the token METER), for per-tick
        deltas. Lazy import keeps the llm layer off the module import path."""
        try:
            from app.services.llm.client import METER

            return METER.by_session.get(session_id, {}).get("calls", 0)
        except Exception:  # noqa: BLE001 — instrumentation must never break a tick
            return 0

    async def _on_position_impl(
        self, session_id: str, position: GeoPoint, heading: Heading, pace: Pace
    ) -> OrchestratorOutput:
        st = await self.store.load(session_id)
        st.position, st.heading, st.pace = position, heading, pace
        self._append_path(st, position, paused=False)

        # Raw per-tick position: the ground truth that discovery/gaze decisions derive
        # from (was missing — "stationary" had to be inferred from repeated geocodes).
        st.tick_seq += 1
        moved = haversine_m(position, st.last_log_pos) if st.last_log_pos else 0.0
        st.last_log_pos = position
        log.info(
            "pos #%d lat=%.5f lon=%.5f hdg=%s/%s pace=%s moved=%.0fm state=%s",
            st.tick_seq, position.lat, position.lon,
            round(heading.direction_deg) if heading.direction_deg is not None else "?",
            heading.gaze_confidence.value if heading.gaze_confidence else "?",
            pace.value if pace else "?", moved, st.state,
        )

        if st.state == State.OFFLINE:
            # server can't reach the cloud — degrade to silence (cached replay
            # is the client's job offline). Stay until GO_ONLINE.
            return await self._finish(st, State.OFFLINE, "offline")

        # Greet FIRST, INSTANTLY — before the (possibly slow) geocode, so a degraded
        # Overpass can't stall the opener for 15+ s. It's a varied, time-of-day opener;
        # the area intro (next tick) names the place. Kick off the disc fetch + geocode in
        # the background so discovery isn't cold-blocked later.
        if settings.session_greeting and not st.control_patch.mute and not st.greeted:
            st.greeted = True
            self._warm_inventory(session_id, position)
            # Pre-generate the area story arc while the greeting is being spoken, so the first
            # area intro (next tick) is instant instead of a cold planner LLM wait.
            self._warm_area_intro(position, st.language, st.narrative_plan.theme_override)
            text = lang.greeting(st.language, None, _local_hour(position.lon))
            log.info("greeting | %s", clip(text))
            return await self._finish(st, State.NARRATING, "narration", text)

        # resolve which city/district/street we're in (move-gated, off-cadence).
        await self._resolve_area(st, position)

        # general -> specific: when we first enter an area, open with it (a short
        # city/district intro) before descending to the concrete objects inside.
        if not st.control_patch.mute:
            intro = await self._maybe_area_intro(st, heading, pace)
            if intro is not None:
                return intro

        try:
            # Always start discovery tight (default radius) so the search never
            # stays inflated at 500 m; it still expands within this tick if nothing
            # is found, but the next tick starts close again. Bounded by an overall
            # deadline so a slow/blocked Overpass can't stall the tick for minutes.
            discover = (
                self.discovery.discover_inventory(
                    session_id, position, heading, st.seen_place_ids, _dedup(st)
                )
                if settings.inventory_enabled
                else self.discovery.discover_adaptive(
                    position, heading, st.seen_place_ids, settings.default_radius_m, _dedup(st)
                )
            )
            result = await asyncio.wait_for(discover, timeout=_DISCOVERY_DEADLINE_S)
        except Exception as e:  # includes asyncio.TimeoutError from the deadline
            # Don't go silent: keep elaborating on the current place (or a short area
            # line) until discovery succeeds on a later tick.
            log.info("discover FAILED (%s: %s) -> carry monologue",
                     type(e).__name__, clip(str(e), 80))
            return await self._continue_monologue(st, heading, pace)

        st.current_radius_m = result.radius_m

        if st.control_patch.mute:
            log.info("silent: muted (agent ticks, output suppressed)")
            return await self._finish(st, State.IDLE, "silence")

        # Warm facts for the whole live window (non-blocking) AND pre-generate the
        # narration for the object you're walking toward — so its blurb is spoken the
        # instant you reach it, not 5-20 s later. The arc context is passed so the
        # pre-generated line fits the running story.
        plan = st.narrative_plan
        # A notable object coming up ahead, to let narration lean forward ("впереди — усадьба").
        lookahead = find_lookahead(
            result.candidates, seen=st.seen_place_ids, min_ahead_m=settings.narrate_radius_m
        )
        self.pipeline.warm_ahead(
            result.candidates, address=st.address, language=st.language,
            seen=st.seen_place_ids, history=st.narration_history,
            theme=plan.active_theme() or None, told=plan.told, next_hook=plan.next_hook,
            heading=heading, pace=pace, preferences=st.control_patch,
            recall=st.memory.objects, lookahead=lookahead,
        )

        # Narrate an object ONLY when the user is passing close to it ("проходишь
        # мимо"): within the small narrate bubble, nearest first. Outside it the area
        # story spine (city/district/street) carries the tour — no far-object
        # fallback, so the guide talks about the district, not about objects across
        # the city.
        near = [c for c in result.candidates if c.distance_m <= settings.narrate_radius_m]
        # Prefer what the user can SEE ahead (in the gaze cone) over something beside/
        # behind them — "говори о том, что вижу и прохожу" (B2/P6). A soft bonus, not a
        # hard group, so a much closer object still wins (you don't skip the thing right
        # next to you for a far one merely ahead).
        near = sorted(near, key=self._visible_rank)[: settings.scorer_max_candidates]

        # Last-resort "reach" set: unseen objects the walker can SEE ahead (in the gaze
        # cone) but that are past the passing bubble. Used only when the area spine runs
        # dry, so the tour reaches a visible object instead of going silent — never
        # something beside/behind or out of view ("говори о том, что вижу"). Capped tight
        # (reach_radius_m) so it fires for what you're ABOUT to reach, not 150-200 m away.
        reach = sorted(
            (
                c for c in result.candidates
                if c.in_gaze_cone
                and c.distance_m <= settings.reach_radius_m
                and c.place.id not in st.seen_place_ids
                and c.place.id not in st.reach_exhausted_ids
            ),
            key=self._visible_rank,
        )[: settings.scorer_max_candidates]

        # Why is the bubble empty? Distinguish "found nothing at all" (Overpass/inventory
        # blank — a coverage or connectivity problem) from "found objects but none close
        # enough to narrate" (normal — the area spine carries) so silence is diagnosable.
        why = ""
        if not near:
            if not result.candidates:
                why = " | EMPTY: no objects in radius (overpass/inventory blank or all seen)"
            else:
                nearest = min(result.candidates, key=lambda c: c.distance_m)
                why = (
                    f" | none in {settings.narrate_radius_m:.0f}m bubble"
                    f" (nearest {nearest.place.name}@{round(nearest.distance_m)}m,"
                    f" reach={len(reach)})"
                )
        log.info(
            "discover r=%.0f cands=%d near=%d reach=%d expanded=%s%s",
            result.radius_m, len(result.candidates), len(near), len(reach), result.expanded,
            (" | " + ", ".join(
                f"{c.place.name}@{round(c.distance_m)}m"
                f"{'^cone' if c.in_gaze_cone else ''}{('/' + c.side) if c.side else ''}"
                for c in near[:4]
            )) if near else why,
        )

        # Gate on the BUBBLE set (not the wide window): skip the LLM when the same
        # object is still in the bubble (standing next to it) or the bubble is empty
        # -> advance the area spine instead. The fingerprint is FACTS-AWARE (see
        # `fingerprint`): it re-opens when warm_ahead caches facts for a passing object
        # whose facts were cold on arrival, so the object is reliably picked up instead
        # of being burned forever by the first cold-facts miss.
        fp = fingerprint(near, self.pipeline.cache, st.language)
        gated = fp == st.last_candidate_fingerprint and not result.expanded
        st.last_candidate_fingerprint = fp
        if not near or gated:
            log.info("-> monologue (%s)",
                     "bubble empty" if not near else "gated: same bubble set, unchanged")
            return await self._continue_monologue(
                st, heading, pace, expanded=result.expanded, reach=reach
            )

        switching = bool(
            st.last_place_id and near[0].place.id != st.last_place_id
        )
        plan = st.narrative_plan
        try:
            out = await self.pipeline.step(
                near,
                seen=st.seen_place_ids,
                history=st.narration_history,
                address=st.address,
                heading=heading,
                pace=pace,
                preferences=st.control_patch,
                switching=switching,
                language=st.language,
                theme=plan.active_theme() or None,
                told=plan.told,
                next_hook=plan.next_hook,
                passing=True,  # the user is right beside this object — introduce it, don't skip
                recall=st.memory.objects,  # callbacks: reference an earlier related object
                lookahead=lookahead,  # foreshadow: tease a notable object coming up ahead
            )
        except Exception:
            return await self._finish(st, State.ERROR, "error")

        # Code-level no-repeat net: if the model echoed something already said, drop it
        # to silence rather than emit a verbatim/near-verbatim paragraph again.
        if out.text and out.place and st.memory.is_repeat(out.text):
            log.info("suppress-repeat step place=%r", out.place.name)
            GUIDE.suppress_repeat()
            return await self._continue_monologue(
                st, heading, pace, expanded=result.expanded, reach=reach
            )

        if out.text and out.place:
            log.info(
                "narrate step place=%r sig=%s switching=%s | %s",
                out.place.name,
                out.significance.value if out.significance else None, switching,
                clip(out.text),
            )
            return await self._commit_step(st, out)

        # Passing object yielded silence (cold facts / nothing to say). The fp is
        # facts-aware, so once warm_ahead caches its facts the gate re-opens and the
        # next tick narrates it. Carry the area spine meanwhile.
        log.info("silence step place=%r (deterministic floor did not apply)",
                 out.place.name if out.place else None)
        GUIDE.silence()
        return await self._continue_monologue(
            st, heading, pace, expanded=result.expanded, reach=reach
        )

    # -- area resolution (general -> specific spine) ------------------------ #
    async def _resolve_area(self, st, position: GeoPoint) -> None:
        """Reverse-geocode the current city/district/street, move-gated so the
        extra request is rare. A change of area resets the area monologue state."""
        if self.geocoder is None:
            return
        moved = (
            st.last_geo_pos is None
            or haversine_m(position, st.last_geo_pos) >= settings.geocoder_min_move_m
        )
        if not moved:
            return
        try:
            addr = await self.geocoder.reverse(position, st.language)
        except Exception:
            return  # transient failure — retry next tick (don't advance last_geo_pos)
        if not any((addr.country, addr.city, addr.district, addr.street)):
            # Empty result (slow/uncovered geocoder): DON'T commit last_geo_pos, so the
            # next tick retries immediately instead of locking out for geocoder_min_move_m.
            # That was why early voice questions had no location until the user had walked
            # ~150 m ("ответы не учитывали геолокацию, со временем начали").
            return
        st.last_geo_pos = position
        st.address = addr
        log.info(
            "geocode country=%r city=%r district=%r street=%r",
            addr.country, addr.city, addr.district, addr.street,
        )
        new_key = addr.district or addr.city
        if new_key and new_key != st.area_key:
            st.area_key = new_key
            st.area_facts = None
            st.area_intro_done = False
            st.area_beats = 0
            st.area_bridge_said = False
            st.area_level = 0  # new area -> restart the city->district->street cascade
            st.area_level_beats = 0
            # fresh area => fresh story arc, but keep the user's chosen theme (if any)
            st.narrative_plan = NarrativePlan(theme_override=st.narrative_plan.theme_override)
            st.last_street = addr.street  # adopt silently; the area opener covers arrival
        elif addr.street and addr.street != st.last_street and st.area_intro_done:
            # Same district, but the user just stepped onto a NEW street. Don't reset
            # the arc — weave a smooth transition into the running monologue via the
            # next-paragraph baton ("свернув на …"), instead of a hard area intro.
            st.last_street = addr.street
            st.narrative_plan.next_hook = lang.street_hook(st.language, addr.street)
            # Re-arm the cascade so the fresh street gets its own facts (city/district
            # already in HISTORY -> the no-repeat rule silences them and it descends).
            st.area_level = 0
            st.area_level_beats = 0
            st.area_bridge_said = False

    def _has_area(self, st) -> bool:
        a = st.address
        return bool(a.district or a.city or a.street)

    async def _maybe_area_intro(
        self, st, heading: Heading, pace: Pace
    ) -> OrchestratorOutput | None:
        """On entering a new area, form the story arc (theme + outline) and speak
        its opener — before descending to the objects inside. None if not due."""
        if st.area_intro_done or not self._has_area(st):
            return None
        st.area_intro_done = True  # one opener per area, even if it comes back empty
        plan = st.narrative_plan
        # Prefer an arc pre-generated during the greeting (instant); else form it now (blocking).
        draft = self.pipeline.take_plan(st.area_key)
        if draft is None:
            try:
                # fast: the planner forms theme+outline+opener from general knowledge;
                # web area facts are fetched lazily for the later beats.
                draft = await self.pipeline.make_plan(
                    st.address,
                    facts=st.area_facts,
                    theme_override=plan.theme_override,
                    language=st.language,
                )
            except Exception:
                draft = None
        if draft is None:
            return None
        plan.area_key = st.area_key
        plan.theme = draft.theme or plan.theme
        plan.outline = draft.outline or plan.outline
        # Route the opener through the narration choke point too (defense in depth):
        # strips any stray HOOK label and applies the desolicit/attribution guards.
        opener, _ = split_hook((draft.opener or "").strip(), st.language)
        if not opener:
            return None
        plan.told = (plan.told + [lang.area_intro_told(st.language)])[-_TOLD_CAP:]
        st.narration_history = (st.narration_history + [opener])[-_HISTORY_CAP:]
        log.info("area intro key=%r theme=%r | %s", st.area_key, plan.theme, clip(opener))
        return await self._finish(st, State.NARRATING, "narration", opener)

    async def _commit_step(self, st, out) -> OrchestratorOutput:
        """Commit a narrated object — from the passing bubble OR a reach fallback.
        Advances the seen-list / history / last-place, resets the area-beat budget so
        the next lull opens fresh, and passes the arc baton. Shared by both paths so a
        reached object gets identical anti-repeat / arc-reset handling."""
        plan = st.narrative_plan
        switching = bool(st.last_place_id and out.place.id != st.last_place_id)
        st.narration_history = (st.narration_history + [out.text])[-_HISTORY_CAP:]
        st.seen_place_ids = (st.seen_place_ids + [out.place.id])[-_SEEN_CAP:]
        # Cross-object anti-repeat: remember the narrated ENTITY so a duplicate OSM object of the
        # same real-world thing isn't narrated again (see ranking.Dedup): a linear feature by name
        # (the "Чура twice" bug), its wikidata QID, and its name+location (same-named nearby dupes).
        nm = _norm_name(out.place.name)
        if out.place.category in LINEAR_CATEGORIES and nm:
            st.seen_linear_names = (st.seen_linear_names + [nm])[-_SEEN_CAP:]
        qid = (out.place.tags or {}).get("wikidata")
        if qid:
            st.seen_wikidata = (st.seen_wikidata + [qid])[-_SEEN_CAP:]
        if nm:
            st.seen_named = (
                st.seen_named + [(nm, out.place.location.lat, out.place.location.lon)]
            )[-_SEEN_CAP:]
        st.last_place_id = out.place.id
        st.last_place = out.place
        st.last_significance = out.significance
        st.elaboration_count = 0  # fresh place — allow follow-ups again
        st.area_beats = 0  # fresh budget of connective area beats for the next lull
        st.area_bridge_said = False  # let a future lull say "пройдём дальше" again
        plan.told = (plan.told + [out.place.name])[-_TOLD_CAP:]  # arc ledger (anti-repeat)
        plan.next_hook = out.next_hook  # baton: weave this into the next paragraph
        state = State.SWITCHING if switching else State.NARRATING
        GUIDE.narrate(
            significance=out.significance.value if out.significance else None,
            category=out.place.category,
            language=st.language,
            switching=switching,
        )
        self._record_history(st, out.place, out.significance, out.text)
        return await self._finish(
            st, state, "narration", out.text, out.place, out.significance,
            card=out.card, image=out.image,
        )

    # When nothing new is nearby, carry the story arc: advance the area outline by
    # one topic (or weave a topic the user asked about), then a couple of follow-ups
    # on the last object, then reach a visible object ahead, then a short "пройдём
    # дальше" bridge, and only then silence.
    async def _continue_monologue(
        self, st, heading: Heading, pace: Pace, *, expanded: bool = False,
        reach: list[Candidate] | None = None,
    ) -> OrchestratorOutput:
        # 1) advance the area story arc by one topic (outline, then briefly grounded
        #    connective beats — see _area_line; ungrounded filler is suppressed there)
        if self._has_area(st):
            text = await self._area_line(st, pace)
            if text:
                return await self._finish(st, State.NARRATING, "narration", text)

        # 1.5) revisit: the walker looped back to an object told earlier (and has since walked
        #      far enough along the route) -> acknowledge it and add ONE fresh detail.
        if settings.revisit_enabled and st.position is not None:
            memo = find_revisit(
                st.memory.objects, st.position, st.route_len_m,
                radius_m=settings.revisit_radius_m, min_route_m=settings.revisit_min_route_m,
            )
            if memo is not None:
                out = await self._revisit(st, memo, heading, pace)
                if out is not None:
                    return out

        # 2) fall back to telling MORE about the last object (bounded tightly)
        if st.last_place is not None and st.elaboration_count < _MAX_ELABORATE:
            try:
                text = await self.pipeline.elaborate(
                    st.last_place,
                    st.last_significance or Significance.MEDIUM,
                    history=st.narration_history,
                    address=st.address,
                    heading=heading,
                    pace=pace,
                    language=st.language,
                )
            except Exception:
                text = ""
            if text and st.memory.is_repeat(text):
                log.info("suppress-repeat elaborate place=%r", st.last_place.name)
                GUIDE.suppress_repeat()
                text = ""  # a re-phrased repeat — treat as nothing-to-add
            if text:
                st.elaboration_count += 1
                st.narration_history = (st.narration_history + [text])[-_HISTORY_CAP:]
                log.info("narrate elaborate place=%r n=%d | %s",
                         st.last_place.name, st.elaboration_count, clip(text))
                GUIDE.elaborate()
                return await self._finish(
                    st, State.NARRATING, "narration", text,
                    st.last_place, st.last_significance,
                )
            st.elaboration_count = _MAX_ELABORATE  # nothing more to add — stop trying

        # 3) reach: the area spine ran dry, but there's an object the walker can SEE
        #    ahead (in the gaze cone, past the passing bubble). Talk about it instead of
        #    going silent — last-resort only, so a walking user still gets bubble-first
        #    narration and never hears about things beside/behind or out of view.
        if reach:
            plan = st.narrative_plan
            try:
                out = await self.pipeline.step(
                    reach,
                    seen=st.seen_place_ids,
                    history=st.narration_history,
                    address=st.address,
                    heading=heading,
                    pace=pace,
                    preferences=st.control_patch,
                    switching=bool(st.last_place_id),
                    language=st.language,
                    theme=plan.active_theme() or None,
                    told=plan.told,
                    next_hook=plan.next_hook,
                    passing=False,  # not right beside it — it's visible up ahead
                    reach=True,  # frame as "виднеется впереди"; never dead air
                    recall=st.memory.objects,
                )
            except Exception:
                out = None
            if out and out.text and out.place:
                if st.memory.is_repeat(out.text):
                    log.info("suppress-repeat reach place=%r", out.place.name)
                    GUIDE.suppress_repeat()
                else:
                    log.info(
                        "narrate reach place=%r sig=%s dist=%.0f | %s",
                        out.place.name,
                        out.significance.value if out.significance else None,
                        reach[0].distance_m, clip(out.text),
                    )
                    return await self._commit_step(st, out)
            elif out is not None and out.place is not None:
                # Silence: a facts-less, non-notable object with nothing to say (a shop
                # etc. — notable/ambient objects get floored, never reach here). Retire
                # it from reach so we don't re-spend on it every tick and so the next
                # visible object behind it gets its turn.
                st.reach_exhausted_ids = (
                    st.reach_exhausted_ids + [out.place.id]
                )[-_SEEN_CAP:]
                log.info("reach exhausted place=%r", out.place.name)

        # 4) genuinely nothing to say: say one short bridge ("пройдём дальше") and then
        #    go quiet, instead of mussing the same topic in circles. One per lull.
        if self._has_area(st) and not st.area_bridge_said:
            st.area_bridge_said = True
            bridges = lang.bridges(st.language)
            bridge = bridges[st.area_beats % len(bridges)]
            st.narration_history = (st.narration_history + [bridge])[-_HISTORY_CAP:]
            log.info("bridge | %s", clip(bridge))
            return await self._finish(st, State.IDLE, "narration", bridge)

        # Everything is exhausted this lull — this is the real "went quiet" outcome.
        log.info(
            "silent: nothing to say (area=%s arc_topics=%d elaborate=%d/%d reach=%d expanded=%s)",
            self._has_area(st), len(st.narrative_plan.outline), st.elaboration_count,
            _MAX_ELABORATE, len(reach or []), expanded,
        )
        state = State.EXPANDING if expanded else State.IDLE
        return await self._finish(st, state, "silence")

    # One beat of the gap-filler monologue. Order: (1) a topic the user asked about,
    # (2) the next un-told outline topic from the plan, then (3) the city->district->
    # street cascade — atypical facts at one level until it's dry, then descend. The
    # no-repeat rule (CORE) makes a dry level return [SILENCE], which we read as
    # "go down a level". After the street is exhausted the caller bridges + goes quiet.
    async def _area_line(self, st, pace: Pace) -> str:
        plan = st.narrative_plan
        # Fetch verified area facts once, up front (used to ground every beat).
        if settings.area_enrich and st.area_facts is None:
            facts = await self.pipeline.enrich_area(
                st.address, st.position, timeout_s=settings.enrich_timeout_s,
                language=st.language,
            )
            st.area_facts = facts or ""  # cache "" so we don't refetch every beat
            log.info("area enrich key=%r -> %s", st.area_key, "facts" if facts else "empty")

        # (1)/(2) user focus, else the planned outline.
        focus = plan.pending_focus[0] if plan.pending_focus else None
        topic = focus or plan.next_topic()
        if topic is not None:
            return await self._emit_area_beat(st, topic, focus=focus, pace=pace)

        # (3) cascade: try the current level; if it has no NEW fact (silence), descend
        # and try the next — bounded per tick so a fully-dry area doesn't burn calls.
        # Anti-fabrication is LEVEL-AWARE: with no web-verified facts the model INVENTS
        # obscure street/district detail (the "метеоритный кратер" fabrication) — but it
        # reliably knows a *named city*. So keep talking about the CITY (grounded, [SILENCE]
        # if unsure) instead of going quiet, and just don't descend into the finer levels
        # it would make up. This is the "лучше бы про город дальше говорил" fix.
        if settings.area_cascade_requires_facts and not st.area_facts:
            city = st.address.city
            if not city:
                log.info("skip cascade: no verified facts and no city to fall back on")
                return ""
            city_l, _, _ = lang.level_labels(st.language)
            topic = lang.area_topic_grounded(st.language, city_l, city)
            # repeat-suppression in _emit_area_beat self-limits this: once the well-known
            # city facts are spent it returns "" and the caller bridges + goes quiet.
            return await self._emit_area_beat(st, topic, focus=None, pace=pace)
        levels = self._area_levels(st)
        attempts = 0
        while st.area_level < len(levels) and attempts < _LEVEL_ATTEMPTS_PER_TICK:
            if st.area_level_beats >= _BEATS_PER_LEVEL:
                st.area_level += 1
                st.area_level_beats = 0
                continue
            label, name = levels[st.area_level]
            topic = lang.area_topic(st.language, label, name)
            text = await self._emit_area_beat(st, topic, focus=None, pace=pace)
            attempts += 1
            if text:
                st.area_level_beats += 1
                return text
            st.area_level += 1  # this level is out of new facts -> go a level deeper
            st.area_level_beats = 0
        return ""

    async def _revisit(self, st, memo, heading: Heading, pace: Pace) -> OrchestratorOutput | None:
        """Narrate a short 'снова у X' + one fresh detail for a returned-to object (via elaborate,
        which reuses cached facts and avoids HISTORY). Re-arms the gate afterwards so it can't
        re-fire until the walker leaves and comes back again — even when nothing new was found."""
        place = Place(
            id=memo.id, name=memo.name, category=memo.category,
            location=GeoPoint(lat=memo.lat or 0.0, lon=memo.lon or 0.0), tags={},
        )
        sig = Significance(memo.significance) if memo.significance else Significance.MEDIUM
        try:
            text = await self.pipeline.elaborate(
                place, sig, history=st.narration_history, address=st.address,
                heading=heading, pace=pace, language=st.language, revisit=True,
            )
        except Exception:
            text = ""
        # Re-arm regardless of outcome: don't retry every tick while lingering near it.
        for o in st.memory.objects:
            if o.id == memo.id:
                o.said_route_m = st.route_len_m
                break
        if text and st.memory.is_repeat(text):
            text = ""
        if not text:
            return None
        st.narration_history = (st.narration_history + [text])[-_HISTORY_CAP:]
        log.info("revisit place=%r route=%.0f | %s", memo.name, st.route_len_m, clip(text))
        return await self._finish(st, State.NARRATING, "narration", text, place, sig)

    def _area_levels(self, st) -> list[tuple[str, str]]:
        """The (label, name) levels to descend through, broadest first. Labels are in
        the session language so the cascade topic reads naturally to the LLM."""
        a = st.address
        city_l, district_l, street_l = lang.level_labels(st.language)
        levels: list[tuple[str, str]] = []
        if a.city:
            levels.append((city_l, a.city))
        if a.district:
            levels.append((district_l, a.district))
        if a.street:
            levels.append((street_l, a.street))
        return levels

    async def _emit_area_beat(
        self, st, topic: str, *, focus: str | None, pace: Pace,
        pregen: tuple[str, str | None] | None = None,
    ) -> str:
        """Generate (or accept a pre-generated) area beat and commit it into session
        state. `pregen=(text, hook)` skips the LLM call — used by commit_area to land a
        beat that prefetch_area already produced in the background."""
        plan = st.narrative_plan
        # Fact-level dedup: feed the beat ONLY the area facts not yet told this walk (even if an
        # old one is reworded). Once they're exhausted -> None -> the beat has no verified facts,
        # so it descends the cascade / stays factual instead of re-telling ("опять про берёзы").
        new = st.memory.new_facts(atomize_facts(st.area_facts))
        if pregen is not None:
            text, hook = pregen
        else:
            try:
                text, hook = await self.pipeline.narrate_area(
                    st.address,
                    facts=" ".join(new) or None,
                    theme=plan.active_theme() or None,
                    topic=topic,
                    told=plan.told,
                    next_hook=plan.next_hook,
                    last_place_name=st.last_place.name if st.last_place else None,
                    history=st.narration_history,
                    pace=pace,
                    language=st.language,
                    beat_mode=lang.beat_mode(st.area_beats),  # rotate the rhetorical angle (A1)
                )
            except Exception:
                return ""
        if text and st.memory.is_repeat(text):
            # The street/district beat repeated an earlier one verbatim — the dominant
            # "повторял факты про улицы" symptom. Drop it; the cascade descends a level.
            log.info("suppress-repeat area topic=%r", topic)
            GUIDE.suppress_repeat()
            return ""
        if text:
            st.memory.mark_facts_told(new)  # these facts are now spoken — don't reuse them
            GUIDE.area_beat()
            st.area_beats += 1
            st.area_bridge_said = False  # real content flowed -> allow a later bridge
            if focus:
                plan.pending_focus.pop(0)  # answered/woven this user topic
            plan.told = (plan.told + [topic])[-_TOLD_CAP:]
            plan.next_hook = hook  # baton for the next paragraph
            st.narration_history = (st.narration_history + [text])[-_HISTORY_CAP:]
            log.info(
                "area beat level=%d topic=%r%s newfacts=%d | %s",
                st.area_level, topic, " focus" if focus else "", len(new), clip(text),
            )
        return text

    # -- background pre-generation (hide inter-beat LLM latency) -------------- #
    async def prefetch_area(
        self, session_id: str, pace: Pace
    ) -> tuple[str, str, str | None] | None:
        """READ-ONLY pre-generation of the NEXT planned (outline) area beat, run in the
        background while the current beat is still being spoken. Mutates NOTHING — it does
        not save the session, so it can run concurrently with delivery / a barge-in / a
        weave without corrupting the running narration. The producer lands the result via
        commit_area (single-threaded, freshness-rechecked). Returns (topic, text, hook),
        or None when there's nothing safe to pre-generate this beat.

        Scope is deliberately narrow: only the outline spine (where the field-walk gaps
        were). The area-facts fetch and the city/district/street cascade are stateful, so
        they're left to the live path — prefetch bails out for both."""
        st = await self.store.load(session_id)
        if not self._has_area(st):
            return None
        # Don't trigger the (state-mutating) area-facts fetch from a read-only prefetch:
        # if facts aren't resolved yet, let the live path fetch them first.
        if settings.area_enrich and st.area_facts is None:
            return None
        plan = st.narrative_plan
        topic = plan.next_topic()  # pure peek; the outline advances only on commit
        if topic is None:
            return None  # outline exhausted -> cascade (stateful), not prefetched
        try:
            # Read-only: filter to not-yet-told facts (same as the live path); the actual
            # told-marking happens single-threaded in _emit_area_beat when commit_area lands this.
            text, hook = await self.pipeline.narrate_area(
                st.address,
                facts=" ".join(st.memory.new_facts(atomize_facts(st.area_facts))) or None,
                theme=plan.active_theme() or None,
                topic=topic,
                told=plan.told,
                next_hook=plan.next_hook,
                last_place_name=st.last_place.name if st.last_place else None,
                history=st.narration_history,
                pace=pace,
                language=st.language,
                beat_mode=lang.beat_mode(st.area_beats),
            )
        except Exception:
            return None
        if not text:
            return None
        return topic, text, hook

    async def commit_area(
        self, session_id: str, topic: str, text: str, hook: str | None, pace: Pace
    ) -> OrchestratorOutput | None:
        """Land a pre-generated area beat as the next narration, re-checking freshness
        against the live state. Returns the narration output, or None if the beat went
        stale between prefetch and now (the topic was already covered, or it's a near-
        repeat) so the caller falls back to generating live."""
        st = await self.store.load(session_id)
        plan = st.narrative_plan
        # Freshness: the beat is only valid if it's STILL exactly the next outline topic
        # the live path would pick. If it was covered meanwhile, or a barge-in theme-switch
        # rebuilt the outline, next_topic() differs -> discard and let the live path run.
        if topic != plan.next_topic():
            return None
        emitted = await self._emit_area_beat(
            st, topic, focus=None, pace=pace, pregen=(text, hook)
        )
        if not emitted:
            return None  # is_repeat dropped it
        return await self._finish(st, State.NARRATING, "narration", emitted)

    async def summarize(self, session_id: str) -> str:
        """A short structured recap of the whole walk from everything narrated — for the Stop
        sheet. Off the hot path (fired once on end); '' when the summarizer is off / too little
        was said."""
        if self.summarizer is None:
            return ""
        st = await self.store.load(session_id)
        text = await self.summarizer.summarize(
            st.memory.narrations, address=st.address,
            theme=st.narrative_plan.theme or None, language=st.language,
        )
        # Persist onto the durable walk (best-effort) so it's readable later in the detail —
        # by the owner and by a friend it's shared with. Guest / no-DB is a no-op.
        if text and st.user_id and settings.database_url:
            try:
                from app.services.accounts import history

                history.record_summary(st, text)
            except Exception:  # noqa: BLE001 — persistence must never break the recap
                pass
        return text

    # -- barge-in ----------------------------------------------------------- #
    async def prepare_utterance(self, session_id: str, text: str):
        """Load state, enter LISTENING and build the CompanionInput for a barge-in. Shared
        by the non-streaming on_utterance and main.py's streaming path (which drives the
        companion itself, then calls finalize_utterance)."""
        CURRENT_SID.set(session_id)  # stamp the barge-in Q/A lines with the session
        st = await self.store.load(session_id)
        st.state = State.LISTENING
        last = st.narration_history[-1] if st.narration_history else None
        log.info("companion Q | %s", clip(text))
        cinp = CompanionInput(
            user_message=text,
            last_narration=last,
            address=st.address,
            history=st.conversation[-6:],
            language=st.language,
        )
        return st, cinp

    async def finalize_utterance(
        self, st, user_text: str, reply: str, control_patch=None
    ) -> OrchestratorOutput:
        """Apply steering, record the Q/A into conversation, persist, enter ANSWERING.
        The Companion's reply is the WHOLE answer — we deliberately do NOT re-queue it as an
        area-beat focus topic: on a field walk that produced a second, redundant/stale beat
        re-telling the same fact ("повторял по два раза"). Shared by both barge-in paths."""
        if control_patch is not None:
            st.control_patch = merge_patch(st.control_patch, control_patch)
        st.conversation = (st.conversation + [f"U: {user_text}", f"G: {reply}"])[-_CONVO_CAP:]
        log.info("companion A | %s", clip(reply))
        return await self._finish(st, State.ANSWERING, "reply", reply)

    async def on_utterance(self, session_id: str, text: str) -> OrchestratorOutput:
        st, cinp = await self.prepare_utterance(session_id, text)
        comp = await self.companion.respond(cinp)
        return await self.finalize_utterance(st, text, comp.reply, comp.control_patch)

    # -- theme switching (user picks/voices a topic to revolve around) ------- #
    async def set_theme(self, session_id: str, theme: str) -> None:
        st = await self.store.load(session_id)
        plan = st.narrative_plan
        plan.theme_override = theme.strip() or None
        # re-open the area so the arc is rebuilt around the chosen theme
        st.area_intro_done = False
        plan.outline = []
        await self.store.save(st)

    # -- connectivity ------------------------------------------------------- #
    async def set_online(self, session_id: str, online: bool) -> None:
        st = await self.store.load(session_id)
        st.state = State.RECOVERY if online else State.OFFLINE
        await self.store.save(st)

    # ---------------------------------------------------------------------- #
    def _record_history(self, st, place, significance, text: str) -> None:
        """Fire-and-forget walk-history write for a just-narrated object (phase 4).
        Guarded so guests / a disabled durable store cost nothing, and so the base
        install never imports the accounts (SQLAlchemy) layer. Never raises."""
        if not st.user_id or not settings.database_url:
            return
        try:
            from app.services.accounts import history

            history.record_object(st, place, significance, text)
        except Exception:  # noqa: BLE001 — history must never disturb narration
            pass

    async def _finish(
        self,
        st,
        state: State,
        kind: str,
        text: str = "",
        place=None,
        significance=None,
        card: str | None = None,
        image: str | None = None,
    ) -> OrchestratorOutput:
        prev = str(st.state)
        if prev != state.value:
            log.info("state %s -> %s (%s)", prev, state.value, kind)
        # Record into the walk memory at the single narration choke point: every spoken
        # paragraph (object step, elaborate, area beat, reach, intro) feeds the whole-walk
        # anti-repeat corpus, and each narrated object is remembered for callbacks.
        if kind == "narration" and text:
            st.memory.record_narration(text)
            if place is not None:
                st.memory.record_object_node(
                    ObjectMemo(
                        id=place.id,
                        name=place.name or "",
                        category=place.category or "",
                        wikidata=(place.tags or {}).get("wikidata"),
                        theme=st.narrative_plan.theme or None,
                        significance=significance.value if significance is not None else None,
                        lat=place.location.lat,
                        lon=place.location.lon,
                        said_route_m=st.route_len_m,
                    )
                )
        st.state = state
        await self.store.save(st)
        sig = significance.value if significance is not None else None
        return OrchestratorOutput(
            state.value,
            kind,
            text,
            place.id if place else None,
            sig,
            place.name if place else None,
            place.location.lat if place else None,
            place.location.lon if place else None,
            card=card,
            image=image,
            category=place.category if place else None,
        )
