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
import contextlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC
from enum import StrEnum

from app.config import settings
from app.services.agent import languages as lang
from app.services.agent.companion import Companion
from app.services.agent.director import atomize_facts, find_lookahead, find_revisit
from app.services.agent.interest_metrics import rank_facts
from app.services.agent.narrator import split_hook
from app.services.agent.pipeline import TextPipeline
from app.services.agent.significance import (
    at_least,
    significance_from_weight,
    tags_have_wiki,
)
from app.services.agent.walklog import (
    CURRENT_SID,
    clip,
    get_logger,
    tick_reset,
    tick_snapshot,
)
from app.services.enrichment.enricher import attach_facts, prefetch
from app.services.geo.categories import LINEAR_CATEGORIES
from app.services.geo.discovery import Discovery
from app.services.geo.geocoder import Geocoder
from app.services.geo.ranking import Dedup, _norm_name, build_candidates
from app.services.geo.route_planner import PlannedRoute, RoutePlanner
from app.services.geo.track_match import match_track
from app.services.llm.client import SESSION_ID, SESSION_TIER, USER_ADDRESS, as_background
from app.services.metrics import GUIDE
from app.services.state.store import StateStore
from app.shared.geo_math import haversine_m, nearest_on_geometry
from app.shared.memory import ObjectMemo, is_fact_duplicate
from app.shared.schemas import (
    Address,
    AreaInput,
    Candidate,
    CompanionInput,
    ControlPatch,
    FactReserveItem,
    GeoPoint,
    Heading,
    NarrativePlan,
    NavState,
    NavStop,
    NavStopStatus,
    Pace,
    Place,
    RouteScript,
    RouteScriptInput,
    ScriptStop,
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
# Follow-ups per place when nothing new is nearby. Each takes a DIFFERENT angle
# (lang.elaborate_angle: history/people/function/detail/context) so the guide goes DEEPER
# from a fresh side instead of mussing the same fact — the walker keeps learning about the
# place until a new object arrives. Still bounded, and the Narrator returns [SILENCE] the
# moment FACTS have nothing genuinely new, so a facts-thin place still stops quickly.
_MAX_ELABORATE = 4

_MAJOR_ROAD_CATEGORIES = frozenset({"motorway", "junction"})
# LLM route-script filler we never want to speak on a leg — it's worse than silence,
# because it sounds like a broken thought instead of a route-wide arc.
_GUIDED_EMPTY_MARKERS = (
    "вот и всё", "на этом всё", "больше тут сказать нечего", "нечего добавить",
    "идём дальше", "that's all", "nothing more to say",
)


def _is_major_road(category: str | None) -> bool:
    """A big road / interchange (МКАД, шоссе, развязка) — narrated by the secondary
    road-reach path, never the walk bubble (you can't walk it)."""
    return category in _MAJOR_ROAD_CATEGORIES


def _guided_leg_empty(text: str | None) -> bool:
    """A scripted lead-in/leg filler that adds no content and should be treated as
    empty. Better to fall through to a real pass-by/area beat than speak a dead-end
    line like «вот и всё, что о нём можно сказать»."""
    t = (text or "").strip().lower()
    return not t or any(m in t for m in _GUIDED_EMPTY_MARKERS)


def _guided_intro_where(st) -> str:
    """Deterministic location phrase for the first guided sentence."""
    if st.address.district:
        return f"в районе {st.address.district}"
    if st.address.city:
        return f"в городе {st.address.city}"
    if st.address.street:
        return f"на улице {st.address.street}"
    return "в этих местах"


def _fallback_guided_intro(st, stops) -> str:
    """A deterministic opening when the rich route script is still building. The route
    already knows the area, so the guide can still start as one coherent tour instead of
    cue->silence while the full arc finishes."""
    where = _guided_intro_where(st)
    return (
        f"Начинаем прогулку {where}: сначала разберёмся, как старые сюжеты этого места "
        f"переходят в сегодняшний городской ритм, а дальше пойдём по самым заметным точкам вокруг."
    )


def _guided_intro_topic(st, stops) -> str:
    """Best current guided-route focus for the first fast intro sentence."""
    if st.nav.script is not None:
        if st.nav.script.theme:
            return st.nav.script.theme
        if st.nav.script.lead_in:
            return st.nav.script.lead_in
    if st.narrative_plan.theme:
        return st.narrative_plan.theme
    if st.narrative_plan.outline:
        return st.narrative_plan.outline[0]
    if stops:
        first = stops[0].name
        return f"чем примечательно место вокруг {first}"
    return "как история этих мест переходит в сегодняшнюю жизнь"


def _fallback_startup_area_sentence(st) -> str:
    """A deterministic area-led startup sentence when a warmed startup beat is absent."""
    if st.address.district:
        return (
            f"Сейчас мы в районе {st.address.district}: начнём с того, как здесь старые городские "
            f"сюжеты переходят в сегодняшнюю жизнь."
        )
    if st.address.city:
        return (
            f"Сейчас мы в городе {st.address.city}: начнём с того, как здесь история города "
            f"собирается в сегодняшнем ритме улиц."
        )
    if st.address.street:
        return (
            f"Сейчас мы на улице {st.address.street}: сначала посмотрим, как она связывает "
            f"прошлое этого места с его сегодняшней жизнью."
        )
    return ""


def _startup_contract_key(position: GeoPoint, language: str) -> tuple[int, int, str]:
    """A coarse shared key for startup prewarm handoff (~110 m cells)."""
    return round(position.lat * 1000), round(position.lon * 1000), language


def _route_street_names(nav: NavState) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for step in nav.steps:
        name = (step.name or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
        if len(names) >= 5:
            break
    return names
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
    # proactive guided mode (advisory labels for WSStateUpdate; the real logic lives in nav)
    PLANNING = "planning"
    PROPOSED = "proposed"
    EN_ROUTE = "en_route"
    AT_STOP = "at_stop"
    REPLANNING = "replanning"


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
    # Guided mode side event to forward to the client alongside this output (a dict ready to
    # send: stop_reached / route_done / reroute). None for the reactive path.
    nav_event: dict | None = None
    # A turn-by-turn navigator cue («через сто метров поверни направо») — spoken with
    # interrupt priority on the client and EXCLUDED from narration memory/history so the
    # anti-repeat corpus, seam stitch, and quality metrics never see it.
    nav_cue: bool = False
    # Only the imminent turn COMMAND interrupts the current sentence; the far
    # pre-announce queues behind it (cue-vs-narration seamlessness).
    nav_urgent: bool = False


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


def _nav_from_route(
    route: PlannedRoute, budget_m: float | None, budget_min: float | None
) -> NavState:
    """Turn a freshly planned route into the persisted NavState (active, not yet accepted)."""
    stops: list[NavStop] = []
    prev = 0.0
    for s in route.stops:
        stops.append(
            NavStop(
                place_id=s.place.id,
                name=s.place.name,
                category=s.place.category,
                lat=s.place.location.lat,
                lon=s.place.location.lon,
                significance=s.significance,
                order=s.order,
                leg_distance_m=max(0.0, s.cum_distance_m - prev),
                place=s.place,
            )
        )
        prev = s.cum_distance_m
    return NavState(
        active=bool(stops),
        accepted=False,
        mode=route.mode,
        origin=route.origin,
        destination=route.destination,
        budget_m=budget_m or 0.0,
        budget_min=budget_min or 0.0,
        stops=stops,
        polyline=route.polyline,
        total_distance_m=route.total_distance_m,
        total_duration_s=route.total_duration_s,
        current_index=0,
        steps=list(route.nav_steps or []),  # turn-by-turn maneuvers ([] on straight-line)
    )


def _navstop_ws(s: NavStop) -> dict:
    """Serialize a NavStop into the client's WSRouteStop shape (for a reroute frame)."""
    return {
        "index": s.order,
        "name": s.name,
        "category": s.category,
        "lat": s.lat,
        "lon": s.lon,
        "significance": str(s.significance),
        "leg_distance_m": s.leg_distance_m,
        "status": str(s.status),
    }


def _reroute_event(nav: NavState, reason: str) -> dict:
    """The `reroute` frame the producer forwards after the route tail is replanned."""
    return {
        "type": "reroute",
        "stops": [_navstop_ws(s) for s in nav.stops],
        "polyline": nav.polyline,
        "reason": reason,
        # Fresh maneuvers for the replanned tail (next-turn chip); old clients ignore it.
        "steps": [m.model_dump() for m in nav.steps] if nav.steps else None,
    }


class Orchestrator:
    def __init__(
        self,
        discovery: Discovery,
        pipeline: TextPipeline,
        companion: Companion,
        store: StateStore,
        geocoder: Geocoder | None = None,
        summarizer=None,
        route_planner: RoutePlanner | None = None,
        tour_scripter=None,
        routing=None,
    ) -> None:
        self.discovery = discovery
        self.pipeline = pipeline
        self.companion = companion
        self.store = store
        self.geocoder = geocoder
        self.summarizer = summarizer
        self.route_planner = route_planner  # proactive guided mode; None => guided disabled
        self.tour_scripter = tour_scripter  # whole-route narration arc for guided mode
        self.routing = routing  # map-matches the walked track (geo/track_match.py)
        self._bg: set[asyncio.Task] = set()  # hold refs to fire-and-forget warm tasks
        # One-shot prewarm runs on a short-lived WS sid, while the real free-walk session starts on
        # a fresh sid. Keep the prepared startup contract in a tiny shared cache keyed by coarse geo
        # cell + language so the live session can adopt it after greeting without reusing the socket.
        self._prewarmed_startup_contracts: dict[tuple[int, int, str], FactReserveItem] = {}

    async def matched_track(self, session_id: str) -> list[list[float]] | None:
        """The walked track snapped to streets (OSRM /match), or None when matching is off /
        the track is too short / no routing. Cosmetic + read-only — never touches narration."""
        if not settings.track_match_enabled or self.routing is None:
            return None
        st = await self.store.load(session_id)
        if len(st.path) < settings.track_match_min_points:
            return None
        return await match_track(st.path, self.routing)

    # -- proactive guided mode: plan / accept / cancel / skip --------------- #
    async def plan_route(
        self,
        session_id: str,
        origin: GeoPoint,
        *,
        mode: str,
        budget_min: float | None = None,
        budget_km: float | None = None,
        destination: GeoPoint | None = None,
        pick_landmark: bool = False,
        theme: str = "",
    ) -> PlannedRoute:
        """Build a guided route from `origin`, store it as this session's nav state (active,
        not yet accepted) and return it so the caller can propose it. Reuses the session
        seen-list + dedup so a resumed/second route doesn't repeat earlier stops."""
        assert self.route_planner is not None, "guided mode requires a route_planner"
        st = await self.store.load(session_id)
        budget_m = budget_km * 1000.0 if budget_km else None
        route = await self.route_planner.build(
            origin,
            mode=mode,
            budget_m=budget_m,
            budget_min=budget_min,
            destination=destination,
            pick_landmark=pick_landmark,
            seen=st.seen_place_ids,
            dedup=_dedup(st),
            language=st.language,
        )
        st.guide_mode = "guided"
        st.nav = _nav_from_route(route, budget_m, budget_min)
        st.state = State.PROPOSED
        if theme.strip():
            st.narrative_plan.theme_override = theme.strip()
        scripting = (
            self.tour_scripter is not None
            and settings.guided_script_enabled
            and bool(st.nav.stops)
        )
        if scripting:
            # Start building the whole-route arc NOW, while the user is still looking at
            # the route sheet — by accept it's usually ready, so the intro is instant
            # instead of ~30 s of post-greeting silence. A reject wastes one background
            # call; the freshness re-check in _build_route_script discards a stale commit.
            st.nav.script = RouteScript()  # placeholder; script_ready gates leading
            st.nav.script_ready = False
        await self.store.save(st)
        self._warm_guided_preview(st, origin)
        if scripting:
            self._build_script_bg(session_id)
        log.info(
            "guided route planned mode=%s stops=%d dist=%.0fm dur=%.0fs",
            route.mode, len(route.stops), route.total_distance_m, route.total_duration_s,
        )
        return route

    async def accept_route(self, session_id: str) -> None:
        """The user accepted the proposed route — the guide may start leading. The
        whole-route narration arc was kicked off at plan time (see plan_route); if the
        scripter is off, warm the FIRST stop's blurb now instead (the script path warms
        it with its beat angle when the script commits)."""
        st = await self.store.load(session_id)
        if not st.nav.active:
            return
        st.nav.accepted = True
        st.state = State.EN_ROUTE
        scripting = (
            self.tour_scripter is not None
            and settings.guided_script_enabled
            and bool(st.nav.stops)
        )
        if scripting and st.nav.script is None:
            # Defensive: plan_route didn't start the build (e.g. scripter toggled on
            # between plan and accept) — start it now, as before.
            st.nav.script = RouteScript()
            st.nav.script_ready = False
            await self.store.save(st)
            self._build_script_bg(session_id)
            return
        await self.store.save(st)
        if not scripting:
            self._warm_next_stop(st, st.heading, st.pace, reason="accept")

    def _build_script_bg(self, session_id: str) -> None:
        """Fire-and-forget: build the whole-route script; on any failure clear it so the
        guide falls back to the per-stop reactive path instead of staying silent forever.
        Tier/address context is reloaded from session state so guided prep never silently runs as
        the default free tier just because the spawning task had no explicit paid context."""
        async def _run() -> None:
            st = await self.store.load(session_id)
            SESSION_ID.set(session_id)
            SESSION_TIER.set(st.tier)
            USER_ADDRESS.set(st.user_address)
            try:
                await self._build_route_script(session_id)
            except Exception as e:  # noqa: BLE001 — never strand the walk on a script failure
                log.info("guided script build failed (%s) -> per-stop fallback", type(e).__name__)
                st = await self.store.load(session_id)
                st.nav.script = None
                st.nav.script_ready = True  # unblock the guided tick (per-stop path)
                await self.store.save(st)

        task = asyncio.ensure_future(as_background(_run()))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def _warm_guided_intro(self, st: SessionState) -> None:
        """Fire-and-forget: prepare the first real guided sentence during proposal warmup so the
        accept path can speak a substantive opener immediately, not a filler or greeting-only line."""
        stops = [s for s in st.nav.stops if s.place is not None]
        area = _guided_intro_where(st)
        topic = _guided_intro_topic(st, stops)

        async def _run() -> None:
            fresh = await self.store.load(st.session_id)
            SESSION_ID.set(st.session_id)
            SESSION_TIER.set(fresh.tier)
            USER_ADDRESS.set(fresh.user_address)
            addr = fresh.address
            if not (addr.district or addr.city or addr.street) and self.geocoder is not None:
                anchor = fresh.nav.origin or fresh.position
                if anchor is not None:
                    with contextlib.suppress(Exception):
                        addr = await self.geocoder.reverse(anchor, fresh.language)
            guided_area = _guided_intro_where(fresh) if (fresh.address.city or fresh.address.district or fresh.address.street) else area
            try:
                text = await self.pipeline.narrator.narrate_guided_intro_fast(
                    AreaInput(
                        address=addr,
                        facts=fresh.area_facts,
                        theme=fresh.nav.script.theme if fresh.nav.script is not None else fresh.narrative_plan.theme,
                        topic=topic,
                        told=fresh.narrative_plan.told,
                        next_hook=fresh.narrative_plan.next_hook,
                        last_place_name=stops[0].name if stops else None,
                        history=fresh.narration_history,
                        pace=Pace.SLOW,
                        beat_mode="guided_intro",
                        visible=fresh.visible_now,
                        on_street=addr.street_confident,
                        language=fresh.language,
                    )
                )
            except Exception:
                text = ""
            if not text:
                names = [s.name for s in stops[:2] if s.name]
                text = _fallback_guided_intro(fresh, stops) if names or guided_area else ""
            if not text:
                return
            refreshed = await self.store.load(st.session_id)
            existing = refreshed.startup_block
            if refreshed.nav.accepted:
                return
            if existing is not None and existing.scope != "guided_start":
                return
            refreshed.startup_block = FactReserveItem(
                id=self._reserve_id("area", "guided_start", f"{guided_area}:{topic}", text, refreshed.language),
                kind="area",
                scope="guided_start",
                subject_key=f"{guided_area}:{topic}",
                language=refreshed.language,
                text=text,
                estimated_seconds=self._reserve_seconds(text),
                area_key=refreshed.area_key or guided_area,
                startup_contract=True,
            )
            if not (refreshed.address.district or refreshed.address.city or refreshed.address.street):
                refreshed.address = addr
                refreshed.area_key = refreshed.area_key or guided_area
            await self.store.save(refreshed)
            log.info("guided fast intro prepared area=%r topic=%r | %s", guided_area, topic, clip(text))

        task = asyncio.ensure_future(as_background(_run()))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _build_route_script(self, session_id: str) -> None:
        """Pre-load facts for every stop, then ask the scripter for the whole-route arc.
        Commits single-threaded with a freshness re-check (route unchanged since accept)."""
        assert self.tour_scripter is not None
        st = await self.store.load(session_id)
        nav = st.nav
        anchor = nav.origin or st.position
        stops = [s for s in nav.stops if s.place is not None][: settings.guided_script_max_stops]
        if not stops or anchor is None:
            raise ValueError("no scriptable stops / no anchor")
        # Candidates from each stop's Place (huge radius so none are distance-filtered out;
        # distance is irrelevant here — we only need enrichment + significance per object).
        cands = build_candidates(
            anchor, st.heading, [s.place for s in stops], 1e7, st.seen_place_ids, _dedup(st)
        )
        lang = st.language
        ctx = ", ".join(p for p in (st.address.city, st.address.country) if p) or None
        timeout = self.pipeline.enrich_timeout_s or settings.enrich_timeout_s
        await prefetch(
            cands, self.pipeline.enricher, self.pipeline.cache,
            top_k=len(cands), timeout_s=timeout, context=ctx, language=lang,
        )
        enriched = attach_facts(cands, self.pipeline.cache, lang)
        script_stops = [
            ScriptStop(
                name=c.place.name,
                category=c.place.category,
                significance=str(
                    significance_from_weight(
                        c.type_weight, c.facts_available, has_wiki=tags_have_wiki(c.place.tags)
                    )
                ),
                # Ranked best-first: the scripter builds each stop's angle around the
                # head of the list (the prompt says facts are pre-sorted by interest).
                facts=" ".join(
                    rank_facts(atomize_facts(c.facts_snippet), lang, top_k=6)
                ) or None
                if c.facts_available and c.facts_snippet
                else None,
            )
            for c in enriched
        ]
        route_key = st.address.district or st.address.city
        route_facts = st.area_facts
        if route_facts is None and route_key:
            route_facts = self.pipeline.take_area_facts(route_key, lang)
        route_plan = None
        if route_key:
            route_plan = self.pipeline.take_plan(route_key)
            if route_plan is None:
                try:
                    route_plan = await self.pipeline.make_plan(
                        st.address,
                        facts=route_facts,
                        theme_override=st.narrative_plan.theme_override,
                        language=lang,
                    )
                except Exception:
                    route_plan = None
        inp = RouteScriptInput(
            stops=script_stops,
            theme_override=st.narrative_plan.theme_override,
            address=st.address,
            route_facts=route_facts,
            route_outline=(route_plan.outline if route_plan is not None else []),
            route_streets=_route_street_names(nav),
            language=lang,
        )
        script = await self.tour_scripter.script(inp)
        # Freshness: reload and only commit if the route is unchanged (no reroute/skip since).
        st2 = await self.store.load(session_id)
        if [s.place_id for s in st2.nav.stops] != [s.place_id for s in nav.stops]:
            log.info("guided script stale (route changed) -> discard")
            return
        st2.nav.script = script
        st2.nav.script_ready = True
        st2.narrative_plan.theme = script.theme or st2.narrative_plan.theme
        if route_plan is not None:
            st2.narrative_plan.area_key = route_key
            st2.narrative_plan.outline = route_plan.outline or st2.narrative_plan.outline
        if route_facts is not None and st2.area_facts is None:
            st2.area_facts = route_facts
        await self.store.save(st2)
        self._warm_guided_intro(st2)
        log.info("guided script ready: theme=%r beats=%d", script.theme, len(script.beats))
        # Warm the FIRST stop's blurb inside the fresh arc (its beat angle + theme) so the
        # first arrival speaks instantly — stop[0] was the one arrival nothing pre-warmed.
        self._warm_next_stop(st2, st2.heading, st2.pace, force=True, reason="script-ready")

    async def cancel_route(self, session_id: str) -> None:
        """Drop the guided route and return to the free (reactive) guide."""
        st = await self.store.load(session_id)
        st.guide_mode = "free"
        st.nav = NavState()
        await self.store.save(st)

    async def skip_stop(self, session_id: str, stop_index: int) -> OrchestratorOutput | None:
        """Mark a pending stop as skipped and replan the tail around it — otherwise the
        polyline/maneuvers still lead THROUGH the skipped stop (wrong cues, false
        off-route, chip pointing at a place the user refused). Returns the reroute
        output (nav_event for the client to redraw), or None when no replan happened."""
        st = await self.store.load(session_id)
        skipped = False
        for s in st.nav.stops:
            if s.order == stop_index and s.status == NavStopStatus.PENDING:
                s.status = NavStopStatus.SKIPPED
                skipped = True
        await self.store.save(st)
        if not (skipped and st.nav.accepted and st.position is not None
                and self.route_planner is not None):
            return None
        try:
            return await self._reroute_tail(
                st, st.position, st.heading, st.pace, reason="skip"
            )
        except Exception:  # noqa: BLE001 — a failed replan must not break the skip itself
            log.info("skip reroute failed -> keep leading the current line")
            return None

    # -- guided-mode per-tick leading ------------------------------------- #
    async def _guided_tick(
        self, st: SessionState, position: GeoPoint, heading: Heading, pace: Pace
    ) -> OrchestratorOutput:
        """One tick of leading the walker along an accepted route: skip past handled stops,
        narrate the current one on arrival, otherwise carry a route-wide story between stops."""
        nav = st.nav
        # Greet FIRST (once, instant, canned) — the planning flow suppresses the reactive
        # greeting until the route is accepted ("приветствие только после принятия"), so
        # the accepted tour opens with it while the route script builds in the background.
        # st.greeted persists in SessionState → a resume never double-greets.
        if settings.session_greeting and not st.control_patch.mute and not st.greeted:
            st.greeted = True
            self._warm_inventory(st.session_id, position)
        # Guided mode still needs live area/address state after the greeting: the between-stop
        # arc reuses the same area spine as the free walk, and without resolve_area the route has
        # no fresh district / street facts to speak from once the intro is spent.
        await self._resolve_area(st, position)
        stops = nav.stops
        idx = nav.current_index
        while idx < len(stops) and stops[idx].status != NavStopStatus.PENDING:
            idx += 1
        nav.current_index = idx
        if idx >= len(stops):
            # Route finished; the finale needs the script — while it's (re)building, hold.
            if nav.script is not None and not nav.script_ready:
                return await self._finish(st, State.EN_ROUTE, "silence")
            return await self._finish_guided_route(st)

        stop = stops[idx]
        dist = haversine_m(position, GeoPoint(lat=stop.lat, lon=stop.lon))
        stop.min_dist_m = min(stop.min_dist_m, dist)
        # Arrival + turn cues run BEFORE the script gate: while the arc (re)builds in the
        # background — precisely the window right after accept/reroute — the walker still
        # gets turn-by-turn leading and a reached stop still gets narrated (per-stop path,
        # beat=None) instead of 15-30 s of dead navigation.
        if dist <= settings.nav_arrival_radius_m:
            return await self._arrive_stop(st, stop, position, heading, pace)
        # Overshoot: the walker came near this stop but is now clearly receding — GPS
        # jump, a tight arrival radius, or they just didn't stop. Retire it as passed
        # (narrated in the past tense) instead of stalling the whole tour on it forever.
        if (
            stop.min_dist_m <= settings.nav_overshoot_near_m
            and dist - stop.min_dist_m >= settings.nav_overshoot_recede_m
        ):
            log.info(
                "guided overshoot stop #%d %r (min=%.0fm now=%.0fm)",
                stop.order, stop.name, stop.min_dist_m, dist,
            )
            return await self._arrive_stop(
                st, stop, position, heading, pace, passed=True
            )
        # Turn-by-turn cue split: the IMMINENT command outranks all narration (missed turn
        # is worse than a delayed anecdote), but the far pre-announce should NOT steal the
        # only leg-beat slot and turn the route into cue/cue/silence. So: urgent cue now;
        # non-urgent cue is deferred until after `_guided_between` if that produced silence.
        cue, urgent = self._nav_cue_text(st, position)
        if cue and urgent:
            log.info("nav cue (urgent) | %s", clip(cue))
            return await self._finish(
                st, State.EN_ROUTE, "narration", cue, nav_cue=True, nav_urgent=True
            )
        # Route-wide intro: if the rich script is ready, use its true intro; otherwise
        # fall back to a deterministic opening built from the already-known area + first
        # stop names. This guarantees the guided route STARTS as a story immediately,
        # instead of cue->silence while the script catches up in background.
        if not nav.intro_done and nav.script is not None:
            intro = ""
            if nav.script_ready and nav.script.intro:
                st.narrative_plan.theme = nav.script.theme or st.narrative_plan.theme
                if not st.narrative_plan.outline and nav.script.lead_in:
                    st.narrative_plan.outline = [nav.script.lead_in]
                intro = nav.script.intro
            else:
                intro = _fallback_guided_intro(st, stops)
            if intro:
                nav.intro_done = True
                st.narration_history = (st.narration_history + [intro])[-_HISTORY_CAP:]
                st.memory.mark_facts_told(atomize_facts(intro))
                log.info("guided intro | %s", clip(intro))
                return await self._finish(st, State.NARRATING, "narration", intro)
        rerouted = await self._maybe_reroute(st, position, heading, pace)
        if rerouted is not None:
            return rerouted
        out = await self._guided_between(st, stop, dist, position, heading, pace)
        # A far pre-announce cue is SOFT priority: only speak it if the leg produced no
        # content this tick. This lets the scripted leg beat / pass-by / area layer carry
        # the route narrative, instead of the user hearing cue -> cue -> stop.
        if cue and not urgent and out.kind == "silence":
            log.info("nav cue | %s", clip(cue))
            return await self._finish(
                st, State.EN_ROUTE, "narration", cue, nav_cue=True, nav_urgent=False
            )
        return out

    @staticmethod
    def _beat_for(nav: NavState, order: int):
        """The scripted StopBeat for a stop order (None if no script / not found)."""
        if nav.script is None:
            return None
        return next((b for b in nav.script.beats if b.order == order), None)

    @staticmethod
    def _nav_cue_text(st: SessionState, position: GeoPoint) -> tuple[str, bool]:
        """The turn-by-turn cue due at this position, as ``(text, urgent)`` — ("", False)
        when nothing is due. Deterministic (no LLM): each maneuver speaks at most twice —
        a heads-up inside nav_cue_preannounce_m (urgent=False: it queues behind the
        sentence being spoken instead of cutting it) and the command inside
        nav_cue_fire_m (urgent=True: interrupt-delivered — a missed turn costs more than
        a clipped sentence) — with a global min-gap. Spoken-once flags live in NavState,
        so a reconnect never re-announces a passed turn. Maneuvers with no mapped phrase
        (or walked past unspoken) are skipped, never block progression."""
        nav = st.nav
        if not settings.nav_cues_enabled or not nav.steps:
            return "", False
        now = time.time()
        if nav.last_cue_at is not None and (now - nav.last_cue_at) < settings.nav_cue_min_gap_s:
            return "", False
        i = nav.next_step_i
        while i < len(nav.steps):
            m = nav.steps[i]
            if m.said or m.kind == "arrive":
                i += 1
                continue
            d = haversine_m(position, GeoPoint(lat=m.lat, lon=m.lon))
            # Walked past without the command firing (sparse GPS): once it was близко
            # (pre_said) and is now clearly receding, retire it and move on.
            if m.pre_said and d > settings.nav_cue_preannounce_m * 1.5:
                m.said = True
                i += 1
                continue
            if d <= settings.nav_cue_fire_m:
                text = lang.nav_cue(st.language, m.kind, m.modifier, m.name)
                m.said = True  # retire even when unmapped ("") so it can't block
                if not text:
                    i += 1
                    continue
                nav.next_step_i = i
                nav.last_cue_at = now
                return text, True
            if d <= settings.nav_cue_preannounce_m and not m.pre_said:
                text = lang.nav_cue(st.language, m.kind, m.modifier, m.name, pre_dist_m=d)
                m.pre_said = True
                if not text:
                    m.said = True  # unmapped — retire it entirely
                    i += 1
                    continue
                nav.next_step_i = i
                nav.last_cue_at = now
                return text, False
            break  # next un-said maneuver is still far ahead — nothing due
        nav.next_step_i = i
        return "", False

    async def _finish_guided_route(self, st: SessionState) -> OrchestratorOutput:
        """The last stop is done — play the scripted finale (once, canned fallback when the
        script path was unavailable), then end the route on the next tick (client shows the
        summary on Stop)."""
        nav = st.nav
        finale = (nav.script.finale if nav.script else "") or (
            # Per-stop fallback route still deserves a closing word, not an abrupt stop.
            lang.route_finale(st.language) if not nav.finale_done and nav.active else ""
        )
        if finale and not nav.finale_done:
            nav.finale_done = True
            st.narration_history = (st.narration_history + [finale])[-_HISTORY_CAP:]
            log.info("guided finale | %s", clip(finale))
            return await self._finish(st, State.NARRATING, "narration", finale)
        was_active = nav.active
        nav.active = False
        out = await self._finish(st, State.IDLE, "silence")
        if was_active:
            out.nav_event = {"type": "route_done"}
            log.info("guided route done (%d stops)", len(nav.stops))
        return out

    async def _arrive_stop(
        self, st: SessionState, stop: NavStop, position: GeoPoint, heading: Heading,
        pace: Pace, *, passed: bool = False,
    ) -> OrchestratorOutput:
        """Walker reached a stop (or overshot it — `passed=True`, told in the past tense):
        mark it reached, warm the next one, and narrate this one with the SAME pipeline the
        reactive path uses (choice dictated by the plan, not the nearest-object bubble)."""
        stop.status = NavStopStatus.REACHED
        st.nav.current_index += 1
        self._warm_next_stop(st, heading, pace)
        nav_event = {"type": "stop_reached", "stop_index": stop.order, "place_id": stop.place_id}
        log.info("guided arrive stop #%d %r%s", stop.order, stop.name,
                 " (overshoot)" if passed else "")

        place = stop.place
        cands = (
            build_candidates(
                position, heading, [place],
                # An overshot stop can be past weave_radius_m already — widen so the
                # candidate isn't distance-filtered out of its own past-tense mention.
                max(settings.weave_radius_m, stop.min_dist_m + settings.nav_overshoot_recede_m * 2)
                if passed else settings.weave_radius_m,
                st.seen_place_ids, _dedup(st),
            )
            if place is not None
            else []
        )
        if not cands:
            out = await self._finish(st, State.AT_STOP, "silence")
            out.nav_event = nav_event
            return out
        plan = st.narrative_plan
        # Scripted beat: tell THIS stop from its pre-planned angle, inside the whole-route
        # theme, with the transition (bridge) as the baton to the next stop.
        beat = self._beat_for(st.nav, stop.order)
        theme = (st.nav.script.theme if st.nav.script else "") or plan.active_theme() or None
        angle = beat.angle if beat else None
        if beat and beat.callback:
            angle = (angle or "") + f" Уместна отсылка назад: {beat.callback}."
        next_hook = (beat.bridge if beat and beat.bridge else None) or plan.next_hook
        try:
            step = await self.pipeline.step(
                cands, seen=st.seen_place_ids, history=st.narration_history,
                address=st.address, heading=heading, pace=pace,
                preferences=st.control_patch, language=st.language,
                theme=theme, told=plan.told,
                next_hook=next_hook, passing=not passed, passed=passed,
                recall=st.memory.objects,
                beat_angle=angle,
            )
        except Exception as e:  # noqa: BLE001 — degrade to an error tick, but say WHY
            log.info("arrive step failed: %r", e)
            out = await self._finish(st, State.ERROR, "error")
            out.nav_event = nav_event
            return out
        if step.text and step.place:
            out = await self._commit_step(st, step)
        else:
            out = await self._finish(st, State.AT_STOP, "silence")
        out.nav_event = nav_event
        return out

    async def _guided_between(
        self, st: SessionState, stop: NavStop, dist: float, position: GeoPoint,
        heading: Heading, pace: Pace,
    ) -> OrchestratorOutput:
        """Guided between-stop narration, but driven by the same fallback architecture as the
        ordinary walk: live objects, area spine, revisit/elaborate/reach, and only then silence.
        The route contributes priorities and transitions; it does not become a sparse emission FSM."""
        prev_beat = None
        for order in range(stop.order - 1, -1, -1):
            prev = next((s for s in st.nav.stops if s.order == order), None)
            if prev is not None and prev.status == NavStopStatus.SKIPPED:
                continue
            prev_beat = self._beat_for(st.nav, order)
            break
        if (
            settings.nav_between_mode != "silent"
            and not st.nav.lead_in_done
            and st.nav.script is not None
        ):
            st.nav.lead_in_done = True
            text = (
                st.nav.script.lead_in
                if st.nav.script.lead_in
                else lang.guided_lead_in(
                    st.language,
                    st.address.district or st.address.city or st.address.street or "этом месте",
                    stop.name,
                )
            )
            if not _guided_leg_empty(text):
                st.narration_history = (st.narration_history + [text])[-_HISTORY_CAP:]
                st.memory.mark_facts_told(atomize_facts(text))
                st.narrative_plan.told = (
                    st.narrative_plan.told + [lang.area_intro_told(st.language)]
                )[-_TOLD_CAP:]
                log.info("guided lead-in->#%d %r @%.0fm", stop.order, stop.name, dist)
                return await self._finish(st, State.EN_ROUTE, "narration", text)
        if (
            settings.nav_between_mode != "silent"
            and not stop.leg_said
            and prev_beat is not None and prev_beat.leg
            and dist > settings.nav_teaser_radius_m
        ):
            stop.leg_said = True
            text = prev_beat.leg
            if not _guided_leg_empty(text):
                st.narration_history = (st.narration_history + [text])[-_HISTORY_CAP:]
                st.memory.mark_facts_told(atomize_facts(text))
                log.info("guided leg->#%d %r @%.0fm", stop.order, stop.name, dist)
                return await self._finish(st, State.EN_ROUTE, "narration", text)
        if (
            settings.nav_between_mode != "silent"
            and not stop.teased
            and dist <= settings.nav_teaser_radius_m
        ):
            stop.teased = True
            text = (prev_beat.bridge if prev_beat and prev_beat.bridge else "") \
                or lang.nav_teaser(st.language, stop.name, dist)
            if text:
                st.narration_history = (st.narration_history + [text])[-_HISTORY_CAP:]
                st.memory.mark_facts_told(atomize_facts(text))
                log.info("guided bridge->#%d %r @%.0fm", stop.order, stop.name, dist)
                return await self._finish(st, State.EN_ROUTE, "narration", text)

        # Free-walk-style live emitters on the route corridor.
        near, reach, road_reach = await self._guided_live_candidates(st, position, heading, pace)
        log.info(
            "guided emitters stop=%r dist=%.0f near=%d reach=%d road=%d teased=%s leg_said=%s",
            stop.name, dist, len(near), len(reach), len(road_reach), stop.teased, stop.leg_said,
        )
        if near:
            plan = st.narrative_plan
            theme = (st.nav.script.theme if st.nav.script else "") or plan.active_theme() or None
            lookahead = find_lookahead(
                near + reach, seen=st.seen_place_ids, min_ahead_m=settings.narrate_radius_m
            )
            try:
                out = await self.pipeline.step(
                    near,
                    seen=st.seen_place_ids,
                    history=st.narration_history,
                    address=st.address,
                    heading=heading,
                    pace=pace,
                    preferences=st.control_patch,
                    language=st.language,
                    theme=theme,
                    told=plan.told,
                    next_hook=plan.next_hook,
                    passing=True,
                    recall=st.memory.objects,
                    lookahead=lookahead,
                    beat_angle=self._same_cat_angle(st, near[0]),
                )
            except Exception as e:
                log.info("guided live emitter failed: %s", type(e).__name__)
                out = None
            if out and out.text and out.place:
                if not st.memory.is_repeat(out.text):
                    log.info("guided live place=%r | %s", out.place.name, clip(out.text))
                    return await self._commit_step(st, out)
                log.info("guided live emitter repeat-suppressed place=%r", out.place.name)
            else:
                log.info("guided live emitter empty")

        # Then reuse the ordinary monologue ladder before admitting silence.
        if settings.nav_between_mode != "silent":
            log.info("guided fallback -> continue_monologue")
            return await self._continue_monologue(st, heading, pace, reach=reach, road_reach=road_reach)
        log.info("guided fallback -> hard silence (nav_between_mode=silent)")
        return await self._finish(st, State.EN_ROUTE, "silence")

    async def _guided_live_candidates(
        self, st: SessionState, position: GeoPoint, heading: Heading, pace: Pace
    ) -> tuple[list[Candidate], list[Candidate], list[Candidate]]:
        """Free-walk-style live candidate windows for guided mode: nearby bubble objects,
        visible reach candidates, and major-road reach candidates. Route stops are excluded
        from the ordinary bubble/reach sets because stop narration is handled by arrival /
        approach logic; everything else should behave like a routed free walk, not a sparse
        pass-by side-channel."""
        inv_store = getattr(self.discovery, "inventory", None)
        prov = getattr(self.discovery, "provider", None)
        inv = None
        if inv_store is not None and prov is not None:
            try:
                inv = await inv_store.ensure(st.session_id, position, prov)
            except Exception:
                inv = inv_store.peek(st.session_id)
        elif inv_store is not None:
            inv = inv_store.peek(st.session_id)
        if inv is None or not inv.places:
            return [], [], []
        stop_ids = {s.place_id for s in st.nav.stops}
        result = build_candidates(
            position, heading, inv.places, settings.narrate_radius_m, st.seen_place_ids, _dedup(st)
        )
        objs = [
            c for c in result
            if c.place.id not in stop_ids and not _is_major_road(c.place.category)
        ]
        near = [c for c in objs if c.distance_m <= self._narrate_reach_m(c)]
        near = sorted(
            near, key=lambda c: self._visible_rank(c) * self._cat_cooldown_factor(st, c)
        )[: settings.scorer_max_candidates]
        reach = sorted(
            (
                c for c in objs
                if c.in_gaze_cone
                and c.distance_m <= self._reach_limit_m(c)
                and c.place.id not in st.seen_place_ids
                and not self._reach_retired(st, c)
            ),
            key=self._visible_rank,
        )[: settings.scorer_max_candidates]
        road_reach: list[Candidate] = []
        if settings.narrate_major_roads:
            seen_roads = {_norm_name(n) for n in st.seen_linear_names}
            road_reach = sorted(
                (
                    c for c in result
                    if _is_major_road(c.place.category)
                    and c.distance_m <= settings.road_reach_radius_m
                    and c.place.id not in st.seen_place_ids
                    and _norm_name(c.place.name) not in seen_roads
                ),
                key=lambda c: c.distance_m,
            )[:3]
        # Guided should warm future live emitters the same way free walk does, not only the next stop.
        plan = st.narrative_plan
        lookahead = find_lookahead(
            near + reach, seen=st.seen_place_ids, min_ahead_m=settings.narrate_radius_m
        )
        self.pipeline.warm_ahead(
            near + reach,
            address=st.address,
            language=st.language,
            seen=st.seen_place_ids,
            history=st.narration_history,
            theme=plan.active_theme() or None,
            told=plan.told,
            next_hook=plan.next_hook,
            heading=heading,
            pace=pace,
            preferences=st.control_patch,
            recall=st.memory.objects,
            lookahead=lookahead,
        )
        return near, reach, road_reach

    async def _guided_passby(
        self, st: SessionState, position: GeoPoint, heading: Heading, pace: Pace
    ) -> OrchestratorOutput | None:
        """Narrate a live route-corridor object the same way free walk narrates a passing
        object, but deduped against route stops and rate-limited so cues/stops still fit."""
        nav = st.nav
        now = time.time()
        if (
            nav.last_passby_at is not None
            and (now - nav.last_passby_at) < settings.nav_passby_min_gap_s
        ):
            return None
        near, _, _ = await self._guided_live_candidates(st, position, heading, pace)
        if not near:
            return None
        plan = st.narrative_plan
        theme = (nav.script.theme if nav.script else "") or plan.active_theme() or None
        try:
            out = await self.pipeline.step(
                near,
                seen=st.seen_place_ids, history=st.narration_history,
                address=st.address, heading=heading, pace=pace,
                preferences=st.control_patch, language=st.language,
                theme=theme, told=plan.told, next_hook=plan.next_hook,
                passing=True, recall=st.memory.objects,
            )
        except Exception:
            return None
        nav.last_passby_at = now
        if not (out and out.text and out.place):
            return None
        if st.memory.is_repeat(out.text):
            log.info("suppress-repeat passby place=%r", out.place.name)
            GUIDE.suppress_repeat()
            return None
        log.info("guided passby place=%r | %s", out.place.name, clip(out.text))
        return await self._commit_step(st, out)

    @staticmethod
    def _offroute_distance(nav: NavState, position: GeoPoint) -> float:
        """How far the walker is from the planned route line (0 if on it / no line)."""
        if len(nav.polyline) < 2:
            return 0.0
        d, _ = nearest_on_geometry(position, nav.polyline)
        return d

    async def _maybe_reroute(
        self, st: SessionState, position: GeoPoint, heading: Heading, pace: Pace
    ) -> OrchestratorOutput | None:
        """Soft reroute: if the walker strays off the route line for longer than the
        debounce (and not too often), replan the pending tail from where they are. Returns
        an output carrying a `reroute` event, or None when no reroute is due."""
        nav = st.nav
        if self.route_planner is None or nav.reroute_count >= settings.nav_reroute_max:
            return None
        now = time.monotonic()
        if self._offroute_distance(nav, position) <= settings.nav_offroute_m:
            nav.off_route_since = None
            return None
        if nav.off_route_since is None:
            nav.off_route_since = now
            return None
        if now - nav.off_route_since < settings.nav_offroute_debounce_s:
            return None
        if (
            nav.last_reroute_at is not None
            and now - nav.last_reroute_at < settings.nav_reroute_min_interval_s
        ):
            return None
        return await self._reroute_tail(st, position, heading, pace, reason="off_route")

    async def _reroute_tail(
        self, st: SessionState, position: GeoPoint, heading: Heading, pace: Pace, *, reason: str
    ) -> OrchestratorOutput | None:
        """Replan the remaining (PENDING) stops from the current position, keeping the ones
        already REACHED. Renumbers the fresh tail after the reached ones and re-anchors the
        route line. Emits a `reroute` frame for the client to redraw."""
        assert self.route_planner is not None
        nav = st.nav
        reached = [s for s in nav.stops if s.status == NavStopStatus.REACHED]
        # Don't re-offer already-covered places (reached stops + the session seen-list)
        # NOR a just-SKIPPED stop — the user refused it; replanning it back in is worse
        # than a shorter route.
        seen = st.seen_place_ids + [
            s.place_id for s in nav.stops if s.status != NavStopStatus.PENDING
        ]
        route = await self.route_planner.build(
            position, mode=nav.mode,
            budget_m=nav.budget_m or None, budget_min=nav.budget_min or None,
            destination=nav.destination, pick_landmark=False,
            seen=seen, dedup=_dedup(st), language=st.language,
        )
        if not route.stops:
            # Nothing to replan onto — keep leading the current plan (avoid churn).
            nav.off_route_since = None
            nav.last_reroute_at = time.monotonic()
            return None
        fresh = _nav_from_route(route, nav.budget_m or None, nav.budget_min or None)
        base = len(reached)
        for i, s in enumerate(fresh.stops):
            s.order = base + i
        nav.stops = reached + fresh.stops
        nav.polyline = fresh.polyline
        nav.total_distance_m = fresh.total_distance_m
        nav.total_duration_s = fresh.total_duration_s
        nav.current_index = base
        # Fresh tail => fresh maneuvers; spoken-once flags reset by construction.
        nav.steps = fresh.steps
        nav.next_step_i = 0
        nav.off_route_since = None
        nav.last_reroute_at = time.monotonic()
        nav.reroute_count += 1
        # Re-script the (new) tail: the arc must cover the fresh stops. Gate leading on
        # script_ready while it rebuilds in the background (intro/reached stops are kept).
        rescript = self.tour_scripter is not None and settings.guided_script_enabled
        if rescript:
            nav.script_ready = False
            nav.finale_done = False
        log.info("guided reroute (%s) -> %d fresh stops (%d kept)", reason, len(fresh.stops), base)
        out = await self._finish(st, State.REPLANNING, "silence")
        out.nav_event = _reroute_event(nav, reason)
        if rescript:
            self._build_script_bg(st.session_id)
        return out

    def _warm_guided_preview(self, st: SessionState, origin: GeoPoint) -> None:
        """Bounded preview-stage warmup for a proposed guided route. Runs while the user is
        still looking at the route sheet, so accept-time startup can reuse ready inventory,
        area context, a first guided intro phrase, and a first-stop narration without adding
        any new protocol/state."""
        self._warm_inventory(st.session_id, origin)
        self._warm_area_intro(
            origin,
            st.language,
            st.narrative_plan.theme_override,
            warm_first_beat=True,
        )
        self._warm_startup_candidates(st.session_id, origin, st.language)
        self._warm_next_stop(
            st,
            st.heading,
            st.pace,
            force=not st.nav.script_ready,
            reason="preview" if not st.nav.accepted else "accepted",
        )
        self._warm_guided_intro(st)
        log.info(
            "guided preview warm sid=%s inventory=1 area=1 startup=1 intro=1 first_stop=%s script=%s",
            st.session_id,
            bool(st.nav.stops),
            "ready" if st.nav.script_ready else "building" if st.nav.script is not None else "off",
        )

    def _warm_startup_candidates(
        self, session_id: str, position: GeoPoint, language: str
    ) -> None:
        """Fire-and-forget: prewarm the likely startup candidates' facts and draft narration.

        Uses only cached/provider data and the pipeline's own warm caches — never session live
        position/history/greet state — so the startup gets richer object readiness without
        turning prewarm into a real tour."""
        inv_store = getattr(self.discovery, "inventory", None)
        prov = getattr(self.discovery, "provider", None)
        if inv_store is None or prov is None:
            return

        async def _run() -> None:
            try:
                await inv_store.ensure(session_id, position, prov)
                inv = inv_store.peek(session_id)
                if inv is None or not inv.places:
                    return
                st = await self.store.load(session_id)
                cands = build_candidates(
                    position,
                    Heading(),
                    inv.places,
                    settings.weave_radius_m,
                    [],
                    _dedup(st),
                )
                if not cands:
                    return
                self.pipeline.warm_ahead(
                    cands,
                    address=st.address,
                    language=language,
                    seen=[],
                    history=[],
                    theme=st.narrative_plan.theme_override or None,
                    told=[],
                    next_hook=None,
                    heading=Heading(),
                    pace=Pace.SLOW,
                    preferences=ControlPatch(),
                )
            except Exception:
                pass

        task = asyncio.ensure_future(as_background(_run()))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def _warm_next_stop(
        self,
        st: SessionState,
        heading: Heading,
        pace: Pace,
        *,
        force: bool = False,
        reason: str = "next-stop",
    ) -> None:
        """Fire-and-forget pre-generation of the NEXT stop's blurb (like warm_ahead on the
        reactive path), so its narration is instant on arrival instead of a cold LLM wait.

        Before the route script is ready this warms a generic stop narration; once the script
        lands, `force=True` lets the richer beat-aware version replace that cache entry."""
        nav = st.nav
        nxt = next(
            (
                s for s in nav.stops[nav.current_index:]
                if s.status == NavStopStatus.PENDING and s.place is not None
            ),
            None,
        )
        if nxt is None:
            return
        cands = build_candidates(
            GeoPoint(lat=nxt.lat, lon=nxt.lon), heading, [nxt.place],
            settings.weave_radius_m, st.seen_place_ids, _dedup(st),
        )
        if not cands:
            return
        plan = st.narrative_plan
        # Pre-generate inside the arc: before the script is ready we still warm a generic first
        # stop blurb from the active theme/hook; the script-ready pass refreshes it with beat data.
        beat = self._beat_for(nav, nxt.order) if nav.script_ready else None
        theme = (nav.script.theme if nav.script_ready and nav.script else "") or plan.active_theme() or None
        angle = beat.angle if beat else None
        next_hook = (beat.bridge if beat and beat.bridge else None) or plan.next_hook

        async def _run() -> None:
            fresh = await self.store.load(st.session_id)
            SESSION_ID.set(st.session_id)
            SESSION_TIER.set(fresh.tier)
            USER_ADDRESS.set(fresh.user_address)
            await self.pipeline.warm_narration(
                cands[0], seen=fresh.seen_place_ids, history=fresh.narration_history,
                address=fresh.address, heading=heading, pace=pace,
                preferences=fresh.control_patch, language=fresh.language,
                theme=theme, told=fresh.narrative_plan.told,
                next_hook=next_hook, recall=fresh.memory.objects, beat_angle=angle,
                force=force,
            )
            log.info(
                "guided warm %s stop=%r force=%s beat=%s script_ready=%s",
                reason,
                nxt.name,
                force,
                bool(angle),
                fresh.nav.script_ready,
            )

        task = asyncio.ensure_future(as_background(_run()))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # Ranking of in-bubble candidates: distance, with a bonus for objects in the gaze
    # cone (visible ahead). 0.6 => a 70 m object ahead ranks like ~42 m, so it beats a
    # far behind one but never a genuinely closer object (B2).
    _VISIBLE_BONUS = 0.6

    @classmethod
    def _visible_rank(cls, c: Candidate) -> float:
        # Significance-aware distance: a high-value category (museum 0.9) shrinks the
        # effective distance by up to ~1.5x vs a low one, so the Tretyakov pavilion at
        # 90 m outranks a gym at 65 m — while a genuinely adjacent ordinary object still
        # wins (the factor is bounded, never a hard grouping). Field-found at ВДНХ:
        # distance-only ranking narrated entrance arches thrice and the museums never.
        w = 1.15 - 0.5 * max(0.0, min(1.0, c.type_weight))
        return c.distance_m * (cls._VISIBLE_BONUS if c.in_gaze_cone else 1.0) * w

    @staticmethod
    def _reach_notable(c: Candidate) -> bool:
        """A candidate worth reaching for ahead of generic area filler: museum-grade
        category weight, or HIGH+ significance once facts/wiki are in the picture."""
        if c.type_weight >= 0.8:
            return True
        sig = significance_from_weight(
            c.type_weight, c.facts_available, has_wiki=tags_have_wiki(c.place.tags)
        )
        return at_least(sig, Significance.HIGH)

    @classmethod
    def _reach_limit_m(cls, c: Candidate) -> float:
        """Reach trigger distance for a candidate: notable objects are worth reaching
        further out (a museum 150 m ahead), ordinary ones keep the tight cap."""
        return (
            settings.reach_radius_notable_m
            if cls._reach_notable(c)
            else settings.reach_radius_m
        )

    @staticmethod
    def _reach_retired(st, c: Candidate) -> bool:
        """Reach-retire check, FACTS-AWARE: an object retired while its facts were cold
        gets one more chance when facts arrive (mirrors the bubble fingerprint). Retired
        WITH facts (or a legacy bare id) stays retired."""
        ids = st.reach_exhausted_ids
        return (
            c.place.id in ids  # legacy bare-id entries block unconditionally
            or f"{c.place.id}|1" in ids  # retired with facts — nothing more will appear
            or (not c.facts_available and f"{c.place.id}|0" in ids)
        )

    @staticmethod
    def _cat_in_cooldown(st, c: Candidate) -> bool:
        """A same-category object was narrated recently AND this one is ordinary (no
        facts, below HIGH) — telling it right away reads as a repeat ("вторая
        библиотека подряд"). Notable or fact-bearing objects are never demoted."""
        told_at = st.last_cat_told.get(c.place.category)
        if told_at is None or (time.time() - told_at) >= settings.narrate_category_cooldown_s:
            return False
        if c.facts_available:
            return False
        sig = significance_from_weight(
            c.type_weight, c.facts_available, has_wiki=tags_have_wiki(c.place.tags)
        )
        return not at_least(sig, Significance.HIGH)

    @classmethod
    def _cat_cooldown_factor(cls, st, c: Candidate) -> float:
        return settings.narrate_category_penalty if cls._cat_in_cooldown(st, c) else 1.0

    @staticmethod
    def _same_cat_angle(st, c: Candidate) -> str | None:
        """Director's note for a cooled-category winner: frame it as the second of a
        kind nearby, not a cold re-introduction. None in the common case."""
        if not Orchestrator._cat_in_cooldown(st, c):
            return None
        return lang.same_category_callback(st.language)

    @staticmethod
    def _narrate_reach_m(c: Candidate) -> float:
        """Effective passing-bubble radius for a candidate. A LOW-significance, fact-less
        object (a plain kindergarten / shop) only counts as "passing" when you're genuinely
        beside it (narrate_radius_low_m) — not 48 m away, which reads as "over there" and, with
        no facts, tempts the model to invent history. Notable (MEDIUM+) or fact-bearing objects
        keep the full narrate_radius_m."""
        sig = significance_from_weight(
            c.type_weight, c.facts_available, has_wiki=tags_have_wiki(c.place.tags)
        )
        if not c.facts_available and not at_least(sig, Significance.MEDIUM):
            return settings.narrate_radius_low_m
        return settings.narrate_radius_m

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
            task = asyncio.ensure_future(inv.ensure(session_id, position, prov))
            self._bg.add(task)
            task.add_done_callback(self._bg.discard)
        except Exception:  # noqa: BLE001 — a warm failure must never disturb the greeting
            pass

    def _warm_area_intro(
        self, position: GeoPoint, language: str, theme_override: str | None,
        *, warm_first_beat: bool = True,
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
                    # Also warm the area FACTS so the first beat after the intro doesn't block.
                    await self.pipeline.warm_area_facts(
                        key, addr, position,
                        timeout_s=settings.enrich_timeout_s, language=language,
                    )
                    if warm_first_beat:
                        draft = self.pipeline.take_plan(key)
                        if draft is not None:
                            # `take_plan()` pops; restore it immediately so the first live tick can
                            # still consume the warmed plan instantly.
                            self.pipeline._plan_cache[key] = draft
                            first_topic = draft.next_topic()
                            warmed = self.pipeline.take_area_facts(key, language)
                            if warmed is not None:
                                self.pipeline._area_facts_cache[(key, language, 0)] = warmed
                            if first_topic is not None:
                                try:
                                    text, hook = await self.pipeline.narrate_area(
                                        addr,
                                        facts=warmed or None,
                                        theme=draft.theme,
                                        topic=first_topic,
                                        told=[],
                                        next_hook=None,
                                        last_place_name=None,
                                        history=[],
                                        pace=Pace.SLOW,
                                        language=language,
                                        beat_mode=lang.beat_mode(0),
                                        visible=[],
                                        on_street=addr.street_confident,
                                    )
                                except Exception:
                                    text = ""
                                    hook = None
                                if text:
                                    self.pipeline.warm_startup_area_beat(
                                        key,
                                        language=language,
                                        topic=first_topic,
                                        text=text,
                                        hook=hook,
                                    )
                            _walk.info(
                                "startup warm key=%r topic=%r facts=%s beat=%s",
                                key,
                                first_topic,
                                bool(warmed),
                                bool(first_topic and self.pipeline.peek_startup_area_beat(key, language=language, topic=first_topic) is not None),
                            )
            except Exception:  # noqa: BLE001 — a warm failure must never disturb the tour
                pass

        task = asyncio.ensure_future(as_background(_run()))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def _warm_area_facts_bg(
        self, area_key: str | None, address: Address, point: GeoPoint | None, language: str,
        *, angle: int = 0,
    ) -> None:
        """Non-blocking: warm the area's facts for deepen round `angle` (cached in the pipeline
        by area_key+angle) so the next beat serves them instantly instead of blocking on web
        search. Read-only wrt session state — writes only the pipeline's area-facts cache."""
        if not area_key:
            return

        async def _run() -> None:
            try:
                await self.pipeline.warm_area_facts(
                    area_key, address, point,
                    timeout_s=settings.enrich_timeout_s, language=language, angle=angle,
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
        except Exception as e:  # noqa: BLE001 — degrade to an error tick, but say WHY
            log.info("deferred step failed: %r", e)
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

        if st.startup_block is None and st.guide_mode == "free":
            key = _startup_contract_key(position, st.language)
            warmed = self._prewarmed_startup_contracts.pop(key, None)
            if warmed is not None:
                st.startup_block = warmed.model_copy(deep=True)
                log.info(
                    "startup contract adopted key=%r source=%s scope=%s | %s",
                    key,
                    warmed.kind,
                    warmed.scope,
                    clip(warmed.text),
                )

        # Proactive guided mode: once the user accepted a planned route, a dedicated tick
        # leads them along it (arrival + narrate the stop) instead of the reactive
        # nearest-object logic below. The reactive path stays entirely untouched.
        if st.guide_mode == "guided" and st.nav.active and st.nav.accepted:
            return await self._guided_tick(st, position, heading, pace)

        # Route PROPOSED but not yet accepted: the user is looking at the route sheet.
        # Stay completely silent — no greeting, no area intro, no discovery. The tour
        # (and its greeting) starts only on route_accept; route_reject drops back to the
        # free walk, which then greets reactively as before.
        if st.guide_mode == "guided" and st.nav.active and not st.nav.accepted:
            return await self._finish(st, State.PROPOSED, "silence")

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
        # Refresh what the walker can actually SEE (in-cone, near) — the area monologue
        # may spatially anchor only to these names (visible-or-abstract rule).
        st.visible_now = [
            c.place.name
            for c in sorted(result.candidates, key=lambda c: c.distance_m)
            if c.in_gaze_cone and c.distance_m <= 250 and c.place.name
        ][:6]

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
        # Major roads (МКАД / шоссе / interchanges) are handled by a SEPARATE, wider,
        # secondary trigger (road_reach below) — never the walk bubble or normal reach:
        # you can't "проходишь мимо" a motorway. Split them out first.
        objs = [c for c in result.candidates if not _is_major_road(c.place.category)]
        near = [c for c in objs if c.distance_m <= self._narrate_reach_m(c)]
        # Prefer what the user can SEE ahead (in the gaze cone) over something beside/
        # behind them — "говори о том, что вижу и прохожу" (B2/P6). A soft bonus, not a
        # hard group, so a much closer object still wins (you don't skip the thing right
        # next to you for a far one merely ahead). The category-cooldown factor softly
        # demotes a SECOND same-category ordinary object right after the first ("вторая
        # библиотека подряд" reads as a repeat) — a lone candidate still wins.
        near = sorted(
            near, key=lambda c: self._visible_rank(c) * self._cat_cooldown_factor(st, c)
        )[: settings.scorer_max_candidates]

        # Last-resort "reach" set: unseen objects the walker can SEE ahead (in the gaze
        # cone) but that are past the passing bubble. Used only when the area spine runs
        # dry, so the tour reaches a visible object instead of going silent — never
        # something beside/behind or out of view ("говори о том, что вижу"). Capped tight
        # (reach_radius_m) so it fires for what you're ABOUT to reach, not 150-200 m away.
        reach = sorted(
            (
                c for c in objs
                if c.in_gaze_cone
                and c.distance_m <= self._reach_limit_m(c)
                and c.place.id not in st.seen_place_ids
                and not self._reach_retired(st, c)
            ),
            key=self._visible_rank,
        )[: settings.scorer_max_candidates]

        # Major-road set: a big NAMED road/interchange the walker is coming near (wider
        # radius, in-cone) that hasn't been mentioned yet. SECONDARY — consumed only in
        # the empty-bubble monologue, AFTER the notable-object reach, and once per road
        # (its name enters seen_linear_names on commit, so it can't repeat).
        # NOTE: no in_gaze_cone gate here (unlike object reach) — a motorway is huge and
        # you sense it whether it's ahead or alongside; requiring it dead-ahead meant a
        # road you walk PARALLEL to (the common case) never fired. Distance + once-per-road
        # is enough; the framing is "рядом …", never a left/right side.
        road_reach: list[Candidate] = []
        if settings.narrate_major_roads:
            seen_roads = {_norm_name(n) for n in st.seen_linear_names}
            road_reach = sorted(
                (
                    c for c in result.candidates
                    if _is_major_road(c.place.category)
                    and c.distance_m <= settings.road_reach_radius_m
                    and c.place.id not in st.seen_place_ids
                    and _norm_name(c.place.name) not in seen_roads
                ),
                key=lambda c: c.distance_m,
            )[:3]

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
                st, heading, pace, expanded=result.expanded, reach=reach, road_reach=road_reach
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
                # A cooled-down same-category winner is framed as "ещё одна/второй рядом"
                # instead of a cold re-introduction (the "две библиотеки" complaint).
                beat_angle=self._same_cat_angle(st, near[0]),
            )
        except Exception as e:  # noqa: BLE001 — degrade to an error tick, but say WHY
            log.info("bubble step failed: %r", e)
            return await self._finish(st, State.ERROR, "error")

        # Code-level no-repeat net: if the model echoed something already said, drop it
        # to silence rather than emit a verbatim/near-verbatim paragraph again.
        if out.text and out.place and st.memory.is_repeat(out.text):
            log.info("suppress-repeat step place=%r", out.place.name)
            GUIDE.suppress_repeat()
            return await self._continue_monologue(
                st, heading, pace, expanded=result.expanded, reach=reach, road_reach=road_reach
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
            st, heading, pace, expanded=result.expanded, reach=reach, road_reach=road_reach
        )

    def _warm_reverse_bg(self, position: GeoPoint, language: str) -> None:
        """Fire-and-forget reverse geocode purely to POPULATE the geocoder's grid cache
        (the result is discarded) — the next tick's `cached()` then hits instantly."""
        if self.geocoder is None:
            return

        async def _run() -> None:
            try:
                await self.geocoder.reverse(position, language)
            except Exception:  # noqa: BLE001 — a failed warm just retries next tick
                pass

        task = asyncio.ensure_future(as_background(_run()))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

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
        # Grid-cache hit is instant (a warmed cell — a standing/slow walker). On a MISS:
        # carry the last address ONLY while still near the last committed fix; once the
        # walker has moved farther than geocoder_carry_max_m the old street is stale, so
        # resolve NOW (bounded-blocking). The pure background-carry FROZE the street while
        # walking — every fresh 11 m cell missed, the warm always landed a cell too late,
        # so the guide narrated the street left minutes behind ("про Парковую, что сзади").
        cached_fn = getattr(self.geocoder, "cached", None)
        addr: Address | None = cached_fn(position, st.language) if cached_fn else None
        if addr is None:
            has_old = any(
                (st.address.country, st.address.city, st.address.district, st.address.street)
            )
            near_last = (
                st.last_geo_pos is not None
                and haversine_m(position, st.last_geo_pos) <= settings.geocoder_carry_max_m
            )
            if has_old and near_last:
                # Barely past the move-gate — the old address is still roughly right;
                # carry it this tick and warm the cell for the next.
                self._warm_reverse_bg(position, st.language)
                return
            try:
                # Moved far (or the first fix): resolve fresh, bounded so a slow geocode
                # can't freeze the tick — fast via the self-hosted Overpass.
                addr = await asyncio.wait_for(
                    self.geocoder.reverse(position, st.language),
                    timeout=settings.geocoder_block_timeout_s,
                )
            except Exception:
                if has_old:
                    self._warm_reverse_bg(position, st.language)  # slow — carry as fallback
                return  # transient/timeout — retry next tick (don't advance last_geo_pos)
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
            st.area_cityless_beats = 0  # new city => a fresh grounded-line budget
            st.area_silent_streak = 0  # new area => the dry gate re-opens
            st.area_warm_skips = 0  # fresh warm-skip budget for the new area's facts
            st.area_fetch_round = 0  # new area => start the deepen angle rotation over
            # fresh area => fresh story arc, but keep the user's chosen theme (if any)
            st.narrative_plan = NarrativePlan(theme_override=st.narrative_plan.theme_override)
            st.last_street = addr.street  # adopt silently; the area opener covers arrival
            # Warm this new area's facts in the background so its first beat doesn't block ~9 s.
            self._warm_area_facts_bg(new_key, addr, st.position, st.language)
        elif addr.street and addr.street != st.last_street and st.area_intro_done:
            # Same district, but the user just stepped onto a NEW street. Don't reset
            # the arc — weave a smooth transition into the running monologue via the
            # next-paragraph baton ("свернув на …"), instead of a hard area intro.
            st.last_street = addr.street
            st.narrative_plan.next_hook = lang.street_hook(st.language, addr.street)
            # Re-arm the cascade at the STREET level only. Jumping back to level 0 here
            # re-armed the whole city cascade on EVERY street change (or geocoder street
            # oscillation), and each re-arm burned a 9-18 s narrate_area LLM call that
            # returned [SILENCE] — the "minutes of quiet" loop seen on the 17.07 walk.
            # City/district are already covered; a fresh street only needs its own level.
            st.area_level = max(0, len(self._area_levels(st)) - 1)
            st.area_level_beats = 0
            st.area_bridge_said = False
            st.area_silent_streak = 0  # a genuinely new street may have something to say

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
        await self._prepare_startup_block(st)
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
        # Tier-1 fast opener: speak one short natural area sentence first, then let the full
        # planner opener follow as the rich continuation. If the fast tier fails, fall back to
        # the normal single-shot opener.
        opener, _ = split_hook((draft.opener or "").strip(), st.language)
        if not opener:
            return None
        fast_opener = ""
        try:
            if hasattr(self.pipeline.narrator, "narrate_area_fast"):
                fast_opener = await self.pipeline.narrator.narrate_area_fast(
                    AreaInput(
                        address=st.address,
                        facts=st.area_facts,
                        theme=plan.active_theme() or None,
                        topic=plan.next_topic(),
                        told=plan.told,
                        next_hook=plan.next_hook,
                        last_place_name=st.last_place.name if st.last_place else None,
                        history=st.narration_history,
                        pace=pace,
                        beat_mode=lang.beat_mode(st.area_beats),
                        visible=st.visible_now,
                        on_street=st.address.street_confident,
                        language=st.language,
                    )
                )
        except Exception:
            fast_opener = ""
        if fast_opener and not lang.opener_repeats(fast_opener, st.memory.narrations, st.language):
            opener = f"{fast_opener} {opener}".strip()
        plan.told = (plan.told + [lang.area_intro_told(st.language)])[-_TOLD_CAP:]
        st.narration_history = (st.narration_history + [opener])[-_HISTORY_CAP:]
        log.info("area intro key=%r theme=%r | %s", st.area_key, plan.theme, clip(opener))
        return await self._finish(st, State.NARRATING, "narration", opener)

    def _prewarm_startup_contract(
        self,
        session_id: str,
        position: GeoPoint,
        language: str,
        theme_override: str | None,
    ) -> None:
        """Fire-and-forget: build the startup contract itself during prewarm.

        This may fill address/area_key/startup_block, but never touches live_position, greeted,
        history, or any producer-facing pacing state. The goal is that the first substantive block
        after the greeting is already parked on the session before the user presses start."""

        async def _run() -> None:
            try:
                st = await self.store.load(session_id)
                if self.geocoder is not None and not self._has_area(st):
                    try:
                        addr = await self.geocoder.reverse(position, language)
                    except Exception:
                        addr = None
                    if addr is not None and any((addr.country, addr.city, addr.district, addr.street)):
                        st.address = addr
                        st.area_key = addr.district or addr.city
                        if st.area_key:
                            self._warm_area_intro(
                                position,
                                language,
                                theme_override,
                                warm_first_beat=True,
                            )
                if st.startup_block is None:
                    fallback_area = _fallback_startup_area_sentence(st)
                    if fallback_area:
                        st.startup_block = FactReserveItem(
                            id=self._reserve_id("area", "startup", st.area_key or "area", fallback_area, st.language),
                            kind="area",
                            scope="startup",
                            subject_key=st.area_key or "area",
                            language=st.language,
                            text=fallback_area,
                            estimated_seconds=self._reserve_seconds(fallback_area),
                            area_key=st.area_key,
                            startup_contract=True,
                        )
                await self._prepare_startup_block(st)
                if st.startup_block is not None:
                    self._prewarmed_startup_contracts[_startup_contract_key(position, language)] = (
                        st.startup_block.model_copy(deep=True)
                    )
                await self.store.save(st)
            except Exception:
                pass

        task = asyncio.ensure_future(as_background(_run()))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _prepare_startup_block(self, st: SessionState) -> None:
        """Build a guaranteed first meaningful block for startup and park it on the session.

        Priority:
        1. warmed startup area beat
        2. deterministic nearby object from cached inventory
        3. factual fallback from area/city facts
        Best-effort and side-effect free wrt told/seen ledgers until actually spoken."""
        if st.startup_block is not None:
            return
        area_key = st.area_key
        topic = st.narrative_plan.next_topic()
        warmed = self.pipeline.peek_startup_area_beat(area_key, language=st.language, topic=topic)
        if warmed is not None and topic:
            text, hook = warmed
            st.startup_block = FactReserveItem(
                id=self._reserve_id("area", "startup", f"{area_key}:{topic}", text, st.language),
                kind="area",
                scope="startup",
                subject_key=f"{area_key}:{topic}",
                language=st.language,
                text=text,
                estimated_seconds=self._reserve_seconds(text),
                area_key=area_key,
                startup_contract=True,
            )
            st.narrative_plan.next_hook = hook
            log.info("startup contract prepared area key=%r topic=%r | %s", area_key, topic, clip(text))
            return

        fallback_area = _fallback_startup_area_sentence(st)
        if fallback_area:
            st.startup_block = FactReserveItem(
                id=self._reserve_id("area", "startup", area_key or "area", fallback_area, st.language),
                kind="area",
                scope="startup",
                subject_key=area_key or "area",
                language=st.language,
                text=fallback_area,
                estimated_seconds=self._reserve_seconds(fallback_area),
                area_key=area_key,
                startup_contract=True,
            )
            log.info("startup contract prepared area fallback key=%r | %s", area_key, clip(fallback_area))
            return

        inv_store = getattr(self.discovery, "inventory", None)
        inv = inv_store.peek(st.session_id) if inv_store is not None else None
        if inv is not None and st.position is not None:
            cands = build_candidates(
                st.position, st.heading, inv.places, settings.narrate_radius_m,
                st.seen_place_ids, _dedup(st),
            )
            if cands:
                top = min(cands, key=self._visible_rank)
                text = lang.passing_mention(st.language, top.place.name, top.side)
                st.startup_block = FactReserveItem(
                    id=self._reserve_id("object", "startup", top.place.id, text, st.language),
                    kind="object",
                    scope="startup",
                    subject_key=top.place.id,
                    language=st.language,
                    text=text,
                    place_id=top.place.id,
                    place_name=top.place.name,
                    category=top.place.category,
                    estimated_seconds=self._reserve_seconds(text),
                    area_key=area_key,
                    startup_contract=True,
                )
                log.info("startup contract prepared object place=%r | %s", top.place.name, clip(text))
                return

        if st.address.city:
            city_label = lang.level_labels(st.language)[0]
            text = lang.area_topic_grounded(st.language, city_label, st.address.city)
            st.startup_block = FactReserveItem(
                id=self._reserve_id("fallback", "startup", st.address.city, text, st.language),
                kind="fallback",
                scope="startup",
                subject_key=st.address.city,
                language=st.language,
                text=text,
                estimated_seconds=self._reserve_seconds(text),
                area_key=area_key,
                startup_contract=True,
            )
            log.info("startup contract prepared fallback city=%r | %s", st.address.city, clip(text))

    async def _commit_step(self, st, out) -> OrchestratorOutput:
        """Commit a narrated object — from the passing bubble OR a reach fallback.
        Advances the seen-list / history / last-place, resets the area-beat budget so
        the next lull opens fresh, and passes the arc baton. Shared by both paths so a
        reached object gets identical anti-repeat / arc-reset handling."""
        plan = st.narrative_plan
        switching = bool(st.last_place_id and out.place.id != st.last_place_id)
        st.narration_history = (st.narration_history + [out.text])[-_HISTORY_CAP:]
        # Fact-level ledger for OBJECT narrations too (was area-beats-only): an area
        # beat must not re-tell an object's fact in different words, and vice versa —
        # the «Тракторист» blurb re-telling the ВДНХ-arch story is this direction.
        st.memory.mark_facts_told(atomize_facts(out.text))
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
        st.area_cityless_beats = 0  # real object flowed -> re-arm the grounded city filler
        st.area_silent_streak = 0  # real object flowed -> the dry gate re-opens
        st.area_bridge_said = False  # let a future lull say "пройдём дальше" again
        # Category cooldown ledger (soft anti-"вторая библиотека подряд" rank penalty).
        if out.place.category:
            st.last_cat_told[out.place.category] = time.time()
            if len(st.last_cat_told) > 32:
                oldest = min(st.last_cat_told, key=st.last_cat_told.get)
                st.last_cat_told.pop(oldest, None)
        plan.told = (plan.told + [out.place.name])[-_TOLD_CAP:]  # arc ledger (anti-repeat)
        plan.next_hook = out.next_hook  # baton: weave this into the next paragraph
        state = State.SWITCHING if switching else State.NARRATING
        GUIDE.narrate(
            significance=out.significance.value if out.significance else None,
            category=out.place.category,
            language=st.language,
            switching=switching,
        )
        # Compact context for the Block 4 corpus (built only when capture is on). FACTS is
        # the grounding source; the rest is the salient decision context the model saw.
        sample_ctx = None
        if settings.capture_narration_samples:
            sample_ctx = {
                "place": {"name": out.place.name, "type": out.place.category},
                "significance": out.significance.value if out.significance else None,
                "theme": plan.theme or None,
                "next_hook": out.next_hook,
                "switching": switching,
                "city": st.address.city or None,
                "district": st.address.district or None,
            }
        self._record_history(
            st, out.place, out.significance, out.text,
            facts=out.facts, input_json=sample_ctx,
        )
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
        road_reach: list[Candidate] | None = None,
    ) -> OrchestratorOutput:
        # 0) a NOTABLE object the walker can see ahead (museum-grade weight or HIGH+
        #    significance) outranks generic area filler: reach it FIRST, before another
        #    beat about the district. Field-found at ВДНХ: the area spine kept talking
        #    while the Tretyakov pavilion drifted past unnarrated. Ordinary reach
        #    objects still wait until the spine runs dry (step 3).
        reach_tried = False
        if reach and self._reach_notable(reach[0]):
            reach_tried = True
            out = await self._reach_step(st, heading, pace, reach)
            if out is not None:
                return out

        # 0.5) a big NAMED road/interchange nearby (МКАД, шоссе): a one-time "рядом
        #     МКАД…" when you first come near it. SECONDARY — after real notable objects,
        #     before generic area filler; once per road (its name enters seen_linear_names
        #     on commit via LINEAR dedup, so it never repeats). Framed "пешком не пройти".
        if road_reach:
            out = await self._reach_step(st, heading, pace, road_reach, road=True)
            if out is not None:
                return out

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

        # 2) fall back to telling MORE about the last object (bounded tightly).
        #    ONLY while the walker is still near it: an elaborate about an object left
        #    hundreds of metres behind reads as obsession («я уже ушёл от суда, а он
        #    мне опять про суд»). Walking away closes the topic for good — a genuine
        #    return is the revisit path's job (1.5), which is distance+route gated.
        if st.last_place is not None and st.elaboration_count < _MAX_ELABORATE:
            if (
                st.position is not None
                and haversine_m(st.position, st.last_place.location)
                > settings.elaborate_max_distance_m
            ):
                log.info(
                    "elaborate closed (walked away from %r)", st.last_place.name
                )
                st.elaboration_count = _MAX_ELABORATE
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
                    angle=lang.elaborate_angle(st.elaboration_count),
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
                # Ledger the spoken facts so a later area beat can't re-tell them in
                # different words (the object→area repeat direction was unguarded).
                st.memory.mark_facts_told(atomize_facts(text))
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
        #    (A NOTABLE reach candidate was already tried at step 0 — don't re-spend.)
        if reach and not reach_tried:
            out = await self._reach_step(st, heading, pace, reach)
            if out is not None:
                return out

        # 4) shared factual arc fallback: if both modes ran out of live emitters, but the
        #    buffer still holds unseen area/place facts, keep the walk alive with those facts
        #    before we ever admit real silence.
        if (factual := self._factual_arc_fallback(st)) is not None:
            return await self._finish(st, State.NARRATING, "narration", factual)

        # 5) genuinely nothing to say: say one short bridge ("пройдём дальше") and then
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

    def _factual_arc_fallback(self, st: SessionState) -> str | None:
        """Last factual safety net for both modes: if the live emitters are exhausted but the
        session still has untold factual atoms in the current area or the last object, emit one
        short continuity line from those facts instead of going silent. Uses the whole-walk fact
        ledger, so it prefers genuinely unseen material and should not repeat what was already told."""
        if st.area_facts:
            new_area = rank_facts(
                st.memory.new_facts(atomize_facts(st.area_facts)), st.language, top_k=2
            )
            if new_area:
                text = " ".join(new_area)
                st.memory.mark_facts_told(new_area)
                st.narration_history = (st.narration_history + [text])[-_HISTORY_CAP:]
                log.info("factual fallback area | %s", clip(text))
                return text
        if st.last_place is not None:
            facts = self.pipeline.cache.get(st.last_place.id, st.language)
            if facts:
                new_obj = rank_facts(
                    st.memory.new_facts(atomize_facts(facts)), st.language, top_k=2
                )
                if new_obj:
                    text = f"{st.last_place.name}. " + " ".join(new_obj)
                    st.memory.mark_facts_told(new_obj)
                    st.narration_history = (st.narration_history + [text])[-_HISTORY_CAP:]
                    log.info("factual fallback place=%r | %s", st.last_place.name, clip(text))
                    return text
        return None

    async def _reach_step(
        self, st, heading: Heading, pace: Pace, reach: list[Candidate], *, road: bool = False
    ) -> OrchestratorOutput | None:
        """One attempt to narrate a visible-ahead (reach) object via the shared pipeline.
        Returns the committed narration, or None (silence/repeat — caller falls through).
        On silence the object is retired FACTS-AWARE: `id|0` (no facts yet — re-armed if
        facts arrive later) or `id|1` (facts were there — nothing more will appear).
        `road=True` frames it as a nearby major road you can't walk ("пешком не пройти")."""
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
                approaching_road=road,  # a big road you can't walk — frame accordingly
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
            # Silence: nothing to say about it right now. Retire it from reach so we
            # don't re-spend on it every tick — but remember whether facts were cold,
            # so a later fact-warm re-opens exactly one retry (see _reach_retired).
            had_facts = any(
                c.place.id == out.place.id and c.facts_available for c in reach
            )
            st.reach_exhausted_ids = (
                st.reach_exhausted_ids + [f"{out.place.id}|{1 if had_facts else 0}"]
            )[-_SEEN_CAP:]
            log.info("reach exhausted place=%r facts=%s", out.place.name, had_facts)
        return None

    # One beat of the gap-filler monologue. Order: (1) a topic the user asked about,
    # (2) the next un-told outline topic from the plan, then (3) the city->district->
    # street cascade — atypical facts at one level until it's dry, then descend. The
    # no-repeat rule (CORE) makes a dry level return [SILENCE], which we read as
    # "go down a level". After the street is exhausted the caller bridges + goes quiet.
    def _maybe_deepen_area(self, st) -> None:
        """Keep the area monologue supplied with REAL facts: when the current batch is
        nearly all told, pull the NEXT rotated search angle (history → people → streets →
        today) and APPEND its fresh facts to st.area_facts. When new facts land it RE-ARMS
        the monologue (resets the dry streak / cascade level / cityless cap) so a walker
        lingering in one area keeps hearing new material instead of staying silent.
        Read-only wrt the LLM (the fetch is a background warm); state edits are local."""
        if not settings.area_enrich or settings.area_deepen_max <= 0:
            return
        if not st.area_facts or st.area_fetch_round >= settings.area_deepen_max:
            return
        # Still have untold material? Don't deepen yet.
        untold = st.memory.new_facts(atomize_facts(st.area_facts))
        state = self.pipeline.subject_coverage("area", st.area_key, st.language, st.area_facts)
        if (
            len(untold) > settings.area_deepen_low_facts
            and state.coverage_chars >= settings.elaborate_deepen_below_chars
            and state.coverage_facts >= max(2, settings.area_deepen_low_facts + 1)
        ):
            return
        nxt = st.area_fetch_round + 1
        warmed = self.pipeline.take_area_facts(st.area_key, st.language, angle=nxt)
        if warmed is None:
            # Not fetched yet — kick the next angle(s) in the background so the pipeline
            # stays a step ahead of delivery (prefetch_ahead rounds in flight at once);
            # this tick still leans on the remaining facts / cascade while they land.
            for a in range(nxt, min(nxt + settings.area_deepen_prefetch_ahead,
                                    settings.area_deepen_max + 1)):
                self._warm_area_facts_bg(
                    st.area_key, st.address, st.position, st.language, angle=a
                )
            return
        st.area_fetch_round = nxt  # advance past this angle regardless (dry or not)
        self.pipeline._update_subject_state(
            "area",
            st.area_key,
            st.language,
            deepen_round=nxt,
            source_tier="web",
            status="ready" if warmed else "dry",
            facts=warmed or "",
        )
        if not warmed:
            return  # this angle was barren; the next deepen tries the one after
        # Fresh real facts arrived: append (bounded) and RE-ARM the monologue.
        combined = (st.area_facts + " " + warmed).strip()
        st.area_facts = combined[-4000:]
        st.area_silent_streak = 0
        st.area_cityless_beats = 0
        st.area_level = 0
        st.area_level_beats = 0
        log.info("area deepen round=%d key=%r -> +%d chars (re-armed)",
                 nxt, st.area_key, len(warmed))

    async def _area_line(self, st, pace: Pace) -> str:
        plan = st.narrative_plan
        # Deepen: top up the area facts with the next search angle before the dry-gate,
        # so a long stay in one spot keeps finding real new facts (and re-arms the gate).
        self._maybe_deepen_area(st)
        # Dry-area gate: after several consecutive silent beats the area has nothing
        # left to say — stop burning a 9-18 s narrate_area LLM call per tick on
        # [SILENCE]. Re-armed by a real object (_commit_step), a new area, a new street,
        # or a deepen fetch landing fresh facts (_maybe_deepen_area).
        if st.area_silent_streak >= settings.area_dry_max:
            return ""
        # Fetch verified area facts once, up front (used to ground every beat). Prefer the
        # background-warmed facts (from area entry). When the warm hasn't landed yet, kick
        # it in the background and SKIP this beat — the old inline fetch blocked the tick
        # for up to enrich_timeout_s (25 s of silence measured live). area_enrich_inline
        # restores the blocking fetch if the skip proves worse in the field.
        if settings.area_enrich and st.area_facts is None:
            warmed = self.pipeline.take_area_facts(st.area_key, st.language)
            if warmed is not None:
                st.area_facts = warmed
                log.info("area enrich key=%r -> %s (warmed)",
                         st.area_key, "facts" if warmed else "empty")
            elif self.pipeline.fact_buffer is not None:
                warmed = self.pipeline.fact_buffer.get_area(st.area_key, st.language)
                if warmed is not None:
                    st.area_facts = warmed
                    log.info("area enrich key=%r -> facts (buffer)", st.area_key)
                elif not settings.area_enrich_inline and st.area_warm_skips < 2:
                    # Bounded skip: give the background warm two ticks to land. It caches only
                    # NON-EMPTY facts (a transient failure must not poison the area), so a
                    # genuinely dry/failed warm would skip forever — after two skips fall
                    # through to ONE inline fetch that settles st.area_facts ("" included).
                    # Startup exception: before the first area intro lands, don't burn the whole
                    # opener just because web facts are still warming — but ONLY when the startup
                    # prewarm has already staged a planner draft for this area, i.e. we have real
                    # warmed structure to speak from. Otherwise keep the old skip semantics.
                    startup_plan_ready = bool(st.area_key) and st.area_key in self.pipeline._plan_cache
                    if not st.area_intro_done and startup_plan_ready:
                        self._warm_area_facts_bg(st.area_key, st.address, st.position, st.language)
                        log.info("area enrich key=%r startup path -> no-block first beat", st.area_key)
                    else:
                        st.area_warm_skips += 1
                        self._warm_area_facts_bg(st.area_key, st.address, st.position, st.language)
                        log.info("area enrich key=%r not warm yet -> skip beat (bg warm %d/2)",
                                 st.area_key, st.area_warm_skips)
                        return ""
                else:
                    facts = await self.pipeline.enrich_area(
                        st.address, st.position, timeout_s=settings.enrich_timeout_s,
                        language=st.language,
                    )
                    st.area_facts = facts or ""  # cache "" so we don't refetch every beat
                    if facts and self.pipeline.fact_buffer is not None:
                        self.pipeline.fact_buffer.put_area(st.area_key, facts, st.language)
                    log.info("area enrich key=%r -> %s", st.area_key, "facts" if facts else "empty")

        # (1)/(2) user focus, else the planned outline.
        focus = plan.pending_focus[0] if plan.pending_focus else None
        topic = focus or plan.next_topic()
        if topic is not None:
            pregen = None
            if focus is None:
                pregen = self.pipeline.take_startup_area_beat(
                    st.area_key,
                    language=st.language,
                    topic=topic,
                )
            return await self._emit_area_beat(st, topic, focus=focus, pace=pace, pregen=pregen)

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
            # Hard cap: once the real well-known city facts are spent, the model FABRICATES
            # fresh specifics every tick, and because each invention differs textually,
            # is_repeat can't stop the loop (8 invented monologues down 1-я Советская). After
            # a couple of grounded lines, go quiet; a real object / new area re-arms it.
            if st.area_cityless_beats >= settings.area_cityless_max:
                log.info("cityless cap reached (%d) -> quiet", st.area_cityless_beats)
                return ""
            city_l, _, _ = lang.level_labels(st.language)
            topic = lang.area_topic_grounded(st.language, city_l, city)
            # Count the ATTEMPT, not the success: a [SILENCE] burns the same 9-18 s LLM
            # call, and counting only successes let the cap never trip — the grounded
            # city beat re-fired every tick forever (the 17.07 silent-loop walk).
            st.area_cityless_beats += 1
            return await self._emit_area_beat(
                st, topic, focus=None, pace=pace, allow_factless=True
            )
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
        st.memory.mark_facts_told(atomize_facts(text))  # symmetric fact ledger
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
        # Street level ONLY when the walker is PHYSICALLY on the street (street_confident:
        # within ~20 m of it). Walking through courtyards / between streets => no street
        # level, the tour stays on district/city — never "talk about the nearest street".
        if a.street and a.street_confident:
            levels.append((street_l, a.street))
        return levels

    async def _emit_area_beat(
        self, st, topic: str, *, focus: str | None, pace: Pace,
        pregen: tuple[str, str | None] | None = None,
        allow_factless: bool = False,
    ) -> str:
        """Generate (or accept a pre-generated) area beat and commit it into session
        state. `pregen=(text, hook)` skips the LLM call — used by commit_area to land a
        beat that prefetch_area already produced in the background."""
        plan = st.narrative_plan
        # Fact-level dedup: feed the beat ONLY the area facts not yet told this walk (even if an
        # old one is reworded). Once they're exhausted -> None -> the beat has no verified facts,
        # so it descends the cascade / stays factual instead of re-telling ("опять про берёзы").
        # Ranked by interestingness (dates/names/specifics first) and capped — the beat
        # builds around the best material; the rest stays un-told and gets its turn later.
        new = rank_facts(
            st.memory.new_facts(atomize_facts(st.area_facts)), st.language, top_k=6
        )
        if pregen is not None:
            text, hook = pregen
        else:
            # Anti-fabrication: a beat with ZERO not-yet-told facts has nothing real to
            # stand on — the model then invents plausible "observed" specifics (bollards
            # with rings, the thud of a ball — real prod fabrications). Skip it; the
            # cascade descends / the reach path gets the airtime. The capped fact-less
            # CITY fallback and an explicit user focus stay allowed (allow_factless).
            # Applies only once the facts pipeline has SETTLED (area_enrich on and the
            # fetch resolved) — with enrichment off (offline/heuristic stack) beats are
            # canned/planner-driven and there is nothing to ground them with by design.
            if (
                settings.area_beat_requires_new_facts
                and settings.area_enrich
                and st.area_facts is not None
                and not new
                and not (allow_factless or focus)
            ):
                log.info("skip area beat (no new facts) topic=%r", topic)
                st.area_silent_streak += 1
                return ""
            # Dry shortcut: the area's facts are all told AND the last beat already came
            # back silent — a fresh 9-18 s narrate_area call will [SILENCE] again (the
            # no-repeat rule guarantees it). Skip the spend; a real object or a new
            # street/area resets the streak and re-opens the tap.
            if not new and st.area_silent_streak >= 1:
                log.info("skip area beat (facts exhausted, silent streak=%d) topic=%r",
                         st.area_silent_streak, topic)
                st.area_silent_streak += 1
                return ""
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
                    visible=st.visible_now,  # spatial anchoring only to what's actually seen
                    on_street=st.address.street_confident,
                )
            except Exception:
                return ""
        if text and st.memory.is_repeat(text):
            # The street/district beat repeated an earlier one verbatim — the dominant
            # "повторял факты про улицы" symptom. Drop it; the cascade descends a level.
            log.info("suppress-repeat area topic=%r", topic)
            GUIDE.suppress_repeat()
            st.area_silent_streak += 1  # a dropped beat is a silent outcome (dry gate)
            return ""
        if text and lang.opener_repeats(text, st.memory.narrations, st.language):
            # Same OPENING as a recent line ("Вот и сейчас, если присмотреться…" ×2). Common
            # with a pre-generated beat built before the colliding line committed, so its
            # AVOID_OPENERS couldn't have seen it. Drop it; the cascade/live path re-forms.
            log.info("suppress-opener-repeat area topic=%r", topic)
            GUIDE.suppress_repeat()
            st.area_silent_streak += 1  # a dropped beat is a silent outcome (dry gate)
            return ""
        # Late-binding seam stitch (pre-generated beats ONLY): a prefetched beat was rendered
        # against a stale snapshot, so its opener can't continue what was actually just spoken.
        # Rewrite its first sentence against the real last line — strictly AFTER is_repeat /
        # opener_repeats (formulaic connectives must not false-drop the whole beat, and those
        # checks were tuned for the raw pre-gen text), so the stitched text is exactly what
        # commits to narration_history below. Live beats saw fresh context — never stitched.
        # (The pre-warmed planner opener (warm_plan/take_plan) is a known accepted gap: it is
        # usually the first line after the canned greeting, where a cold start is natural.)
        if pregen is not None and text:
            text = await self.pipeline.stitch_seam(text, st.narration_history, st.language)
        # Dry-area streak: consecutive empty beats arm the _area_line gate (stop burning
        # LLM calls on a talked-out area); any real content re-arms the monologue.
        st.area_silent_streak = 0 if text else st.area_silent_streak + 1
        if text:
            st.memory.mark_facts_told(new)  # these facts are now spoken — don't reuse them
            # ALSO ledger the narrator's OWN wording of the beat: the next re-fetch of the
            # same real-world fact (new area scope, new distiller run) is often closer to
            # what was SPOKEN than to the raw atom — doubling the paraphrase-match surface
            # (the «ниши на первых этажах» street→district repeat).
            st.memory.mark_facts_told(atomize_facts(text))
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
    def _reserve_id(self, kind: str, scope: str, subject_key: str, text: str, language: str) -> str:
        seed = f"{kind}|{scope}|{subject_key}|{language}|{text.strip()}"
        return uuid.uuid5(uuid.NAMESPACE_URL, seed).hex

    @staticmethod
    def _reserve_seconds(text: str) -> float:
        text = (text or "").strip()
        if not text:
            return 0.0
        return min(max(len(text) / 14.0, 4.0), 28.0)

    @staticmethod
    def _reserve_line_text(text: str | None, language: str) -> str:
        """Reserve lines may be spoken verbatim by the client, so keep only session-language-safe
        factual text here. Non-matching foreign-language fact blobs are dropped rather than leaked."""
        cleaned = (text or "").strip()
        if not cleaned or lang.looks_foreign_facts(cleaned, language):
            return ""
        return cleaned

    async def refresh_fact_reserve(self, session_id: str) -> list[FactReserveItem]:
        """Rebuild the session-scoped degraded/offline reserve from the latest state.

        Reserve items are buffered from real facts, but they are NOT treated as told until the
        client explicitly acks playback (`reserve_played`)."""
        st = await self.store.load(session_id)
        candidates: list[tuple[int, FactReserveItem, list[str]]] = []
        batch_id = uuid.uuid4().hex
        route_version = ""
        if st.guide_mode == "guided" and st.nav.accepted:
            route_version = uuid.uuid5(
                uuid.NAMESPACE_URL,
                "|".join([str(s.place_id) for s in st.nav.stops]),
            ).hex
        log.info(
            "reserve build sid=%s last_place=%s area=%s guided=%s accepted=%s city=%s batch=%s routev=%s",
            session_id,
            st.last_place.id if st.last_place else None,
            st.area_key,
            st.guide_mode,
            st.nav.accepted,
            st.address.city,
            batch_id,
            route_version,
        )

        def _item(
            *,
            kind: str,
            scope: str,
            subject_key: str,
            text: str,
            priority: int,
            facts_seed: list[str],
            place_id: str | None = None,
            place_name: str | None = None,
            category: str | None = None,
            stop_order: int | None = None,
        ) -> None:
            text = self._reserve_line_text(text, st.language)
            if not text:
                return
            item = FactReserveItem(
                id=self._reserve_id(kind, scope, subject_key, text, st.language),
                kind=kind,
                scope=scope,
                subject_key=subject_key,
                language=st.language,
                text=text,
                place_id=place_id,
                place_name=place_name,
                category=category,
                estimated_seconds=self._reserve_seconds(text),
                batch_id=batch_id,
                guide_mode=st.guide_mode,
                area_key=st.area_key,
                route_version=route_version,
                stop_order=stop_order,
            )
            log.info(
                "reserve candidate sid=%s prio=%d sec=%.1f kind=%s scope=%s subject=%s stop=%s | %s",
                session_id,
                priority,
                item.estimated_seconds,
                item.kind,
                item.scope,
                item.subject_key,
                item.stop_order,
                clip(item.text),
            )
            candidates.append((priority, item, facts_seed))

        if st.last_place is not None:
            facts = self.pipeline.cache.get(st.last_place.id, st.language)
            ranked = rank_facts(st.memory.new_facts(atomize_facts(facts or "")), st.language, top_k=2)
            if ranked:
                text = self._reserve_line_text(f"{st.last_place.name} — {' '.join(ranked)}", st.language)
                _item(
                    kind="object",
                    scope="place",
                    subject_key=st.last_place.id,
                    text=text,
                    priority=100,
                    facts_seed=atomize_facts(text),
                    place_id=st.last_place.id,
                    place_name=st.last_place.name,
                    category=st.last_place.category,
                )
            elif st.narration_history:
                text = st.narration_history[-1].strip()
                _item(
                    kind="object",
                    scope="place",
                    subject_key=st.last_place.id,
                    text=text,
                    priority=90,
                    facts_seed=atomize_facts(text),
                    place_id=st.last_place.id,
                    place_name=st.last_place.name,
                    category=st.last_place.category,
                )

        area_new = rank_facts(
            st.memory.new_facts(atomize_facts(st.area_facts or ""), threshold=0.9), st.language, top_k=4
        )
        if area_new and st.area_key:
            for i in range(0, len(area_new), 2):
                chunk = area_new[i:i + 2]
                text = self._reserve_line_text(" ".join(chunk), st.language)
                _item(
                    kind="area",
                    scope="area",
                    subject_key=st.area_key,
                    text=text,
                    priority=80 - i,
                    facts_seed=chunk,
                )

        if st.guide_mode == "guided" and st.nav.accepted and st.nav.script:
            if st.nav.script.lead_in:
                _item(
                    kind="bridge",
                    scope="route",
                    subject_key=st.area_key or "route",
                    text=st.nav.script.lead_in.strip(),
                    priority=70,
                    facts_seed=atomize_facts(st.nav.script.lead_in),
                )
            for beat in st.nav.script.beats[:6]:
                if beat.leg:
                    _item(
                        kind="bridge",
                        scope="route",
                        subject_key=f"route-leg:{beat.order}",
                        text=beat.leg.strip(),
                        priority=68 - beat.order,
                        facts_seed=atomize_facts(beat.leg),
                        stop_order=beat.order,
                    )
                if beat.bridge:
                    _item(
                        kind="bridge",
                        scope="route",
                        subject_key=f"route-bridge:{beat.order}",
                        text=beat.bridge.strip(),
                        priority=66 - beat.order,
                        facts_seed=atomize_facts(beat.bridge),
                        stop_order=beat.order,
                    )
            if st.nav.script.finale:
                _item(
                    kind="bridge",
                    scope="route",
                    subject_key="route-finale",
                    text=st.nav.script.finale.strip(),
                    priority=20,
                    facts_seed=atomize_facts(st.nav.script.finale),
                    stop_order=len(st.nav.stops),
                )

        inv_store = getattr(self.discovery, "inventory", None)
        inv = inv_store.peek(session_id) if inv_store is not None else None
        if inv is not None and st.position is not None:
            candidates_ahead = build_candidates(
                st.position,
                st.heading,
                inv.places,
                settings.weave_radius_m,
                st.seen_place_ids,
                _dedup(st),
            )
            for cand in candidates_ahead[:8]:
                if st.last_place is not None and cand.place.id == st.last_place.id:
                    continue
                facts = self.pipeline.cache.get(cand.place.id, st.language)
                ranked = rank_facts(st.memory.new_facts(atomize_facts(facts or "")), st.language, top_k=2)
                if not ranked:
                    continue
                text = self._reserve_line_text(f"{cand.place.name} — {' '.join(ranked[:1])}", st.language)
                prio = 60 if cand.in_gaze_cone else 50
                _item(
                    kind="object",
                    scope="place",
                    subject_key=cand.place.id,
                    text=text,
                    priority=prio,
                    facts_seed=ranked[:1],
                    place_id=cand.place.id,
                    place_name=cand.place.name,
                    category=cand.place.category,
                )

        for scope, subject_key in (
            ("street", st.address.street or ""),
            ("district", st.address.district or ""),
            ("city", st.address.city or ""),
        ):
            if not subject_key or self.pipeline.fact_buffer is None:
                continue
            facts = self.pipeline.fact_buffer.get_subject(scope, subject_key, st.language)
            if not facts:
                continue
            ranked = rank_facts(st.memory.new_facts(atomize_facts(facts), threshold=0.9), st.language, top_k=4)
            for i in range(0, len(ranked), 2):
                chunk = ranked[i:i + 2]
                if not chunk:
                    continue
                _item(
                    kind="area" if scope != "city" else "fallback",
                    scope=scope,
                    subject_key=subject_key,
                    text=self._reserve_line_text(" ".join(chunk), st.language),
                    priority=40 - i if scope != "city" else 20 - i,
                    facts_seed=chunk,
                )

        if st.address.city:
            text = lang.area_topic_grounded(st.language, lang.level_labels(st.language)[0], st.address.city)
            _item(
                kind="fallback",
                scope="city",
                subject_key=st.address.city,
                text=text,
                priority=10,
                facts_seed=atomize_facts(text),
            )

        target_s = settings.reserve_target_guided_s if st.guide_mode == "guided" and st.nav.accepted else settings.reserve_target_s
        hard_cap_s = settings.reserve_hard_cap_s
        seen_ids: set[str] = set(st.played_reserve_ids)
        virtual_told = list(st.memory.told_facts)
        items: list[FactReserveItem] = []
        total_s = 0.0
        for _, item, fact_seed in sorted(candidates, key=lambda it: (-it[0], it[1].id)):
            if item.id in seen_ids:
                continue
            # Keep one current-object reserve line even when its facts were just spoken live: the
            # offline/degraded path needs a continuity bridge back into the same place, and dropping
            # it as a duplicate leaves the reserve empty right after a normal narration.
            continuity_item = item.kind == "object" and item.place_id == st.last_place_id
            if not continuity_item and any(is_fact_duplicate(f, virtual_told) for f in fact_seed if f.strip()):
                continue
            seen_ids.add(item.id)
            items.append(item)
            if not continuity_item:
                virtual_told.extend([f for f in fact_seed if f.strip()])
            total_s += item.estimated_seconds
            if len(items) >= settings.reserve_max_items:
                break
            if total_s >= hard_cap_s:
                break
            if total_s >= target_s and len(items) >= 6:
                break

        st.fact_reserve = items
        log.info(
            "reserve built sid=%s items=%d sec=%.1f target=%.1f hard=%.1f order=%s",
            session_id,
            len(items),
            total_s,
            target_s,
            hard_cap_s,
            [f"{it.kind}:{it.subject_key}" for it in items],
        )
        await self.store.save(st)
        return st.fact_reserve

    async def ack_fact_reserve(self, session_id: str, reserve_id: str) -> None:
        """Reserve playback acknowledgment from the client. This is the only point where a
        buffered reserve line becomes part of told-state / anti-repeat memory."""
        st = await self.store.load(session_id)
        item = next((it for it in st.fact_reserve if it.id == reserve_id), None)
        if item is None:
            log.info("reserve ack sid=%s id=%s -> missing", session_id, reserve_id)
            return
        if reserve_id in st.played_reserve_ids:
            log.info("reserve ack sid=%s id=%s -> duplicate", session_id, reserve_id)
            return
        st.played_reserve_ids = (st.played_reserve_ids + [reserve_id])[-_SEEN_CAP:]
        st.memory.record_narration(item.text)
        st.memory.mark_facts_told(atomize_facts(item.text))
        if item.place_id:
            st.memory.record_object_node(
                ObjectMemo(id=item.place_id, name=item.place_name or "", category=item.category or "")
            )
        st.fact_reserve = [it for it in st.fact_reserve if it.id != reserve_id]
        log.info(
            "reserve ack sid=%s id=%s kind=%s scope=%s subject=%s | %s",
            session_id,
            reserve_id,
            item.kind,
            item.scope,
            item.subject_key,
            clip(item.text),
        )
        await self.store.save(st)

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
        new = rank_facts(
            st.memory.new_facts(atomize_facts(st.area_facts)), st.language, top_k=6
        )
        if (
            settings.area_beat_requires_new_facts
            and settings.area_enrich
            and st.area_facts is not None
            and not new
        ):
            # Same anti-fabrication gate as the live path: a beat with zero not-yet-told
            # facts would freewheel — don't pre-generate one either.
            return None
        try:
            # Read-only: filter to not-yet-told facts (same as the live path); the actual
            # told-marking happens single-threaded in _emit_area_beat when commit_area lands this.
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
                beat_mode=lang.beat_mode(st.area_beats),
                visible=st.visible_now,  # spatial anchoring only to what's actually seen
                on_street=st.address.street_confident,
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
        CURRENT_SID.set(session_id)  # walklog attribution (seam stitch logs from here)
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
    def _record_history(
        self, st, place, significance, text: str,
        *, facts: str | None = None, input_json: dict | None = None,
    ) -> None:
        """Fire-and-forget walk-history write for a just-narrated object (phase 4).
        Guarded so guests / a disabled durable store cost nothing, and so the base
        install never imports the accounts (SQLAlchemy) layer. Never raises.

        ``facts``/``input_json`` feed the Block 4 corpus (persisted only when
        ``capture_narration_samples`` is on — the writer decides)."""
        if not st.user_id or not settings.database_url:
            return
        try:
            from app.services.accounts import history

            history.record_object(
                st, place, significance, text, facts=facts, input_json=input_json
            )
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
        nav_cue: bool = False,
        nav_urgent: bool = False,
    ) -> OrchestratorOutput:
        prev = str(st.state)
        if prev != state.value:
            log.info("state %s -> %s (%s)", prev, state.value, kind)
        # Record into the walk memory at the single narration choke point: every spoken
        # paragraph (object step, elaborate, area beat, reach, intro) feeds the whole-walk
        # anti-repeat corpus, and each narrated object is remembered for callbacks.
        # Navigator cues are deliberately EXCLUDED — canned strings would pollute the
        # anti-repeat corpus / metrics and legitimately repeat («поверни направо» ×N).
        if kind == "narration" and text and not nav_cue:
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
            nav_cue=nav_cue,
            nav_urgent=nav_urgent,
        )
