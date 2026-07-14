import asyncio
from pathlib import Path

from app.config import settings
from app.services.agent.companion import HeuristicCompanion
from app.services.agent.narrator import TemplateNarrator
from app.services.agent.orchestrator import (
    _BEATS_PER_LEVEL,
    Orchestrator,
    State,
    merge_patch,
)
from app.services.agent.pipeline import TextPipeline
from app.services.agent.scorer import HeuristicScorer
from app.services.enrichment.enricher import MockEnricher
from app.services.geo.discovery import Discovery
from app.services.geo.providers import StaticPlaceProvider
from app.services.state.store import InMemoryStateStore
from app.shared.memory import is_near_duplicate
from app.shared.schemas import Address, ControlPatch, GeoPoint, Heading, Pace, Place
from sim.routes import RED_SQUARE
from sim.walk import walk

FIX = Path(__file__).parent / "fixtures"
HERE = GeoPoint(lat=55.7537, lon=37.6205)


def _place(pid, name, category, lat=55.7537, lon=37.6205) -> Place:
    return Place(id=pid, name=name, category=category, location=GeoPoint(lat=lat, lon=lon))


def _orch(places, facts=None, companion=None) -> Orchestrator:
    discovery = Discovery(StaticPlaceProvider(places))
    pipeline = TextPipeline(HeuristicScorer(), TemplateNarrator(), MockEnricher(facts or {}))
    return Orchestrator(
        discovery, pipeline, companion or HeuristicCompanion(), InMemoryStateStore()
    )


async def _skip_greeting(orch, sid):
    """Mark the session greeted so the one-time session-opener greeting doesn't shift a
    test that asserts the FIRST on_position outcome (discovery/silence)."""
    st = await orch.store.load(sid)
    st.greeted = True
    await orch.store.save(st)


class CountingPipeline(TextPipeline):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = 0

    async def step(self, *a, **k):
        self.calls += 1
        return await super().step(*a, **k)


def test_walk_narrates_and_persists_memory():
    async def run():
        provider = StaticPlaceProvider.from_json(FIX / "places_red_square.json")
        enricher = MockEnricher.from_json(FIX / "facts_red_square.json")
        pipeline = TextPipeline(HeuristicScorer(), TemplateNarrator(), enricher)
        orch = Orchestrator(
            Discovery(provider), pipeline, HeuristicCompanion(), InMemoryStateStore()
        )
        narrated = []
        for step in walk(RED_SQUARE, speed_mps=1.3, step_s=8.0):
            out = await orch.on_position("s1", step.position, step.heading, step.pace)
            if out.kind == "narration" and out.place_id:  # skip greeting/area lines (no place)
                narrated.append(out.place_id)
        state = await orch.store.load("s1")
        return narrated, state

    narrated, state = asyncio.run(run())
    assert len(narrated) >= 3
    assert len(narrated) == len(set(narrated))  # dedup
    assert state.seen_place_ids  # memory persisted
    assert set(narrated) <= set(state.seen_place_ids)


def test_breadcrumb_paused_flags_point_and_holds_walk():
    # A point walked while the tour is paused is flagged ([lat, lon, 1.0]) so history can
    # style it, and the walk's last-event clock is refreshed so a long pause doesn't
    # rotate the tour into a second walk. No narration/generation happens.
    async def run():
        orch = _orch([_place("p1", "Памятник", "monument")])
        await orch.on_position("s", HERE, Heading(), Pace.SLOW)  # normal point starts path
        st = await orch.store.load("s")
        st.walk_id = "w1"
        st.walk_last_event_at = 1000.0  # stale clock -> would rotate without a refresh
        await orch.store.save(st)
        far = GeoPoint(lat=55.7550, lon=37.6205)  # ~140 m north -> past the 12 m gate
        await orch.breadcrumb_paused("s", far)
        return await orch.store.load("s")

    st = asyncio.run(run())
    assert len(st.path[0]) == 2, "the unpaused point stays a bare [lat, lon] pair"
    assert st.path[-1][2] == 1.0 and len(st.path[-1]) == 3, "paused point carries the flag"
    assert st.walk_last_event_at > 1000.0, "pause refreshes the clock (one walk)"


def test_heuristic_gate_skips_llm_on_unchanged_set():
    async def run():
        discovery = Discovery(StaticPlaceProvider([_place("shop1", "ГУМ", "shop")]))
        pipeline = CountingPipeline(HeuristicScorer(), TemplateNarrator(), MockEnricher({}))
        orch = Orchestrator(discovery, pipeline, HeuristicCompanion(), InMemoryStateStore())
        await _skip_greeting(orch, "s")
        o1 = await orch.on_position("s", HERE, Heading(), Pace.SLOW)
        o2 = await orch.on_position("s", HERE, Heading(), Pace.SLOW)
        return o1, o2, pipeline.calls

    o1, o2, calls = asyncio.run(run())
    assert o1.kind == "silence" and o2.kind == "silence"
    assert calls == 1  # second identical tick was gated, pipeline not called again


def test_barge_in_applies_control_patch():
    async def run():
        orch = _orch([_place("m", "Музей", "museum")], facts={"m": "факт"})
        await orch.on_utterance("s", "пропускай магазины")
        return await orch.store.load("s")

    state = asyncio.run(run())
    assert state.state == State.ANSWERING
    assert "shop" in state.control_patch.skip_categories
    assert state.conversation  # dialog remembered


def test_mute_silences_narration():
    async def run():
        orch = _orch([_place("m", "Музей", "museum")], facts={"m": "Большой музей."})
        await orch.on_utterance("s", "помолчи")
        out = await orch.on_position("s", HERE, Heading(), Pace.SLOW)
        state = await orch.store.load("s")
        return out, state

    out, state = asyncio.run(run())
    assert out.kind == "silence"
    assert state.control_patch.mute is True
    assert not state.seen_place_ids  # nothing narrated while muted


def test_offline_degrades_to_silence():
    async def run():
        orch = _orch([_place("m", "Музей", "museum")], facts={"m": "факт"})
        await orch.set_online("s", False)
        off = await orch.on_position("s", HERE, Heading(), Pace.SLOW)
        await orch.set_online("s", True)
        return off

    off = asyncio.run(run())
    assert off.kind == "offline" and off.state == State.OFFLINE


def test_merge_patch_unions_and_overrides():
    base = ControlPatch(skip_categories=["shop"], verbosity="normal")
    patch = ControlPatch(skip_categories=["cafe"], verbosity="shorter", mute=True)
    merged = merge_patch(base, patch)
    assert set(merged.skip_categories) == {"shop", "cafe"}
    assert merged.verbosity == "shorter"
    assert merged.mute is True


def test_geocoder_retries_after_empty_then_resolves():
    """#2: an empty/failed first geocode must NOT lock last_geo_pos — otherwise the
    address (and the companion's location-awareness) only resolves after walking
    geocoder_min_move_m. The next tick at the SAME spot should retry and resolve."""

    class FlakyGeo:
        def __init__(self):
            self.calls = 0

        async def reverse(self, point, language="ru"):
            self.calls += 1
            return Address() if self.calls == 1 else Address(city="Москва", district="Тверской")

    orch = _orch([])
    geo = FlakyGeo()
    orch.geocoder = geo

    async def run():
        st = await orch.store.load("geo-retry")
        await orch._resolve_area(st, HERE)  # empty -> must not commit / not lock out
        assert st.last_geo_pos is None
        assert not any(
            (st.address.country, st.address.city, st.address.district, st.address.street)
        )
        await orch._resolve_area(st, HERE)  # SAME spot: retries (not move-gated) -> resolves
        assert geo.calls == 2
        assert st.address.city == "Москва"
        assert st.last_geo_pos is not None

    asyncio.run(run())


def test_object_narrated_only_within_passing_bubble():
    """Step 1: an object outside the small narrate bubble is NOT narrated (the guide
    stays on the area/silence spine); the SAME object narrates once the user is
    passing close to it."""
    p = _place("p", "Музей", "museum")  # at HERE
    orch = _orch([p], facts={"p": "Большой музей."})

    async def run():
        await _skip_greeting(orch, "s")
        far = GeoPoint(lat=HERE.lat + 0.0018, lon=HERE.lon)  # ~200 m: in window, not in bubble
        o_far = await orch.on_position("s", far, Heading(), Pace.SLOW)
        assert o_far.kind != "narration"
        near = GeoPoint(lat=HERE.lat + 0.00035, lon=HERE.lon)  # ~39 m: passing by
        o_near = await orch.on_position("s", near, Heading(), Pace.SLOW)
        assert o_near.kind == "narration"
        assert o_near.place_id == "p"

    asyncio.run(run())


def test_reach_narrates_in_cone_object_instead_of_silence():
    """Last-resort reach: when the passing bubble is empty and the area spine is dry
    (here: no area at all), a visible object AHEAD (in the gaze cone, past the bubble)
    is narrated instead of dead air — but only when it's actually in view."""
    p = _place("p", "Музей", "museum", lat=HERE.lat + 0.0007)  # ~78 m due north (within reach)
    facts = {"p": "Большой музей."}

    async def run_facing_it():
        orch = _orch([p], facts=facts)
        await _skip_greeting(orch, "s")
        facing = Heading(direction_deg=0.0)  # north — the object is in the cone
        out = await orch.on_position("s", HERE, facing, Pace.SLOW)
        assert out.kind == "narration"
        assert out.place_id == "p"

    async def run_facing_away():
        orch = _orch([p], facts=facts)
        await _skip_greeting(orch, "s")
        away = Heading(direction_deg=180.0)  # south — object NOT in view -> stay silent
        out = await orch.on_position("s", HERE, away, Pace.SLOW)
        assert out.kind != "narration"

    asyncio.run(run_facing_it())
    asyncio.run(run_facing_away())


def test_greeting_fires_once_at_session_start():
    """The instant session-opener greeting is the first thing spoken (no place), exactly
    once, so the tour starts immediately while the rest loads."""
    orch = _orch([])

    async def run():
        settings.session_greeting = True
        o1 = await orch.on_position("g", HERE, Heading(), Pace.SLOW)
        st = await orch.store.load("g")
        o2 = await orch.on_position("g", HERE, Heading(), Pace.SLOW)
        return o1, o2, st.greeted

    o1, o2, greeted = asyncio.run(run())
    assert o1.kind == "narration" and o1.place_id is None and o1.text  # the greeting
    assert greeted is True
    assert o2.text != o1.text  # not greeted again on the next tick


def test_peek_bubble_flags_fresh_in_bubble_object():
    """The cheap per-frame check flags a fresh object sitting in the narrate bubble (the
    signal to preempt), and stops flagging it once it's been narrated."""
    p = _place("p", "Музей", "museum")  # at HERE
    orch = _orch([p])

    async def run():
        await _skip_greeting(orch, "s")
        far = GeoPoint(lat=HERE.lat + 0.0018, lon=HERE.lon)  # ~200 m: builds inventory
        await orch.on_position("s", far, Heading(), Pace.SLOW)
        hit = await orch.peek_bubble("s", HERE, Heading())  # p is right here now
        await orch.on_position("s", HERE, Heading(), Pace.SLOW)  # narrate it
        hit2 = await orch.peek_bubble("s", HERE, Heading())
        return hit, hit2

    hit, hit2 = asyncio.run(run())
    assert hit is not None and hit[0] == "p"  # (id, significance) for the bubble object
    assert hit2 is None  # already narrated -> nothing fresh to jump to


def test_narrate_object_passed_narrates_without_canned_prefix():
    """narrate_object(passed=True) narrates a specific object we've walked past — the
    scheduler's deferred-mention path. Past-tense framing now rides on the `passed` flag in
    the prompt, NOT a canned 'кстати, мы прошли' prefix, so no stock lead-in is prepended
    (which also avoids the old present/past tense clash with the body)."""
    p = _place("p", "Музей", "museum")  # at HERE
    orch = _orch([p], facts={"p": "Большой музей девятнадцатого века."})

    async def run():
        await _skip_greeting(orch, "s")
        far = GeoPoint(lat=HERE.lat + 0.0018, lon=HERE.lon)  # ~200 m: builds inventory, p unseen
        await orch.on_position("s", far, Heading(), Pace.SLOW)
        return await orch.narrate_object("s", "p", passed=True)

    out = asyncio.run(run())
    assert out.kind == "narration" and out.place_id == "p"
    assert out.text  # the object was narrated
    assert not out.text.startswith("Кстати,")  # no canned passed-object prefix anymore


def test_street_change_weaves_transition_without_resetting_arc():
    """E: stepping onto a new street within the SAME district sets a transition
    baton (next_hook) and keeps the running arc, instead of a hard reset + opener.
    A district change still resets the arc."""
    orch = _orch([])

    class Geo:
        def __init__(self):
            self.addr = Address(city="Москва", district="Тверской", street="Тверская")

        async def reverse(self, point, language="ru"):
            return self.addr

    geo = Geo()
    orch.geocoder = geo

    async def run():
        st = await orch.store.load("street")
        await orch._resolve_area(st, HERE)  # first resolve -> establishes the area
        assert st.last_street == "Тверская"
        st.area_intro_done = True  # pretend the area opener has played
        st.narrative_plan.outline = ["t1"]
        arc = st.narrative_plan
        # move >150 m; new street, SAME district -> woven transition, no reset
        geo.addr = Address(city="Москва", district="Тверской", street="Камергерский")
        far = GeoPoint(lat=HERE.lat + 0.002, lon=HERE.lon)
        await orch._resolve_area(st, far)
        assert st.narrative_plan is arc  # arc NOT reset
        assert st.narrative_plan.outline == ["t1"]
        assert st.last_street == "Камергерский"
        assert "Камергерский" in (st.narrative_plan.next_hook or "")
        # now a DISTRICT change -> fresh arc
        geo.addr = Address(city="Москва", district="Арбат", street="Арбат")
        farther = GeoPoint(lat=HERE.lat + 0.004, lon=HERE.lon)
        await orch._resolve_area(st, farther)
        assert st.narrative_plan is not arc  # reset on new district
        assert st.area_intro_done is False

    asyncio.run(run())


def test_warm_ahead_caches_cone_first_then_nearby_nonblocking():
    """B/step4: facts are warmed cone-first (what you walk toward), then nearby
    off-cone objects too (background inventory fact-collection)."""
    from app.services.enrichment.enricher import EnrichmentCache
    from app.shared.schemas import Candidate, GazeConfidence

    class FakeEnricher:
        async def facts_for(self, place, context=None, language="ru"):
            return f"facts:{place.id}"

    def cand(pid, dist, cone):
        return Candidate(
            place=_place(pid, pid, "monument"),
            distance_m=dist,
            type_weight=0.9,
            in_gaze_cone=cone,
            gaze_confidence=GazeConfidence.LOW,
        )

    async def run():
        cands = [cand("a", 50, True), cand("b", 120, True), cand("c", 80, False)]
        # budget=2 -> only the two cone objects warmed (cone has priority over the
        # nearer off-cone "c")
        cache = EnrichmentCache()
        pipe = TextPipeline(
            HeuristicScorer(), TemplateNarrator(), FakeEnricher(),
            cache=cache, enrich_top_k=2, enrich_timeout_s=5.0,
        )
        pipe_settings_k = settings.enrich_lookahead_k
        settings.enrich_lookahead_k = 2
        try:
            await pipe.warm_ahead(cands)
        finally:
            settings.enrich_lookahead_k = pipe_settings_k
        assert cache.get("a") == "facts:a" and cache.get("b") == "facts:b"
        assert cache.get("c") is None  # cone-first: off-cone bumped past the budget

        # budget=3 -> the nearby off-cone object is warmed too (background facts)
        cache2 = EnrichmentCache()
        pipe2 = TextPipeline(
            HeuristicScorer(), TemplateNarrator(), FakeEnricher(),
            cache=cache2, enrich_top_k=2, enrich_timeout_s=5.0,
        )
        settings.enrich_lookahead_k = 3
        try:
            await pipe2.warm_ahead(cands)
        finally:
            settings.enrich_lookahead_k = pipe_settings_k
        assert cache2.get("c") == "facts:c"

        # mock/inline path (enrich_top_k=None) must be a no-op (no background work)
        inline = TextPipeline(HeuristicScorer(), TemplateNarrator(), FakeEnricher())
        assert inline.warm_ahead([cand("a", 50, True)]) is None

    asyncio.run(run())


def test_area_cascade_descends_city_to_district_to_street():
    """Once the outline is exhausted the gap-filler cascades city -> district ->
    street: a level with no NEW fact (the Narrator returns [SILENCE]) is skipped and
    the next, deeper level is tried — within a single lull tick — so the guide keeps
    talking about where you actually are instead of going quiet after one line."""
    orch = _orch([])

    async def fake_narrate_area(address, **kw):
        topic = kw["topic"]
        if "про город" in topic or "про район" in topic:
            return "", None  # city + district are dry (no new facts)
        return f"улица: {topic[:18]}", None  # the street still has something to say

    orch.pipeline.narrate_area = fake_narrate_area

    async def run():
        st = await orch.store.load("casc")
        st.address = Address(city="Москва", district="Тверской", street="Тверская")
        st.area_facts = "Проверенные факты о районе."  # cascade requires facts now
        out = await orch._area_line(st, Pace.SLOW)
        assert out.startswith("улица:")  # descended past the dry city/district
        assert st.area_level == 2  # landed on the street level

    asyncio.run(run())


def test_area_cascade_bounded_per_level_then_silent():
    """Each level yields at most a few facts (per-level soft budget); once the only
    level is spent and there's nowhere deeper to go, the beat returns "" so the caller
    bridges with 'пройдём дальше' and goes quiet — no endless rambling."""
    orch = _orch([])

    async def fake_narrate_area(address, **kw):
        return f"beat: {kw['topic'][:20]}", None  # this level always has another fact

    orch.pipeline.narrate_area = fake_narrate_area

    async def run():
        st = await orch.store.load("casc2")
        st.address = Address(city="Москва")  # a single level (city)
        st.area_facts = "Проверенные факты о районе."  # cascade requires facts now
        produced = [await orch._area_line(st, Pace.SLOW) for _ in range(_BEATS_PER_LEVEL + 2)]
        nonempty = [t for t in produced if t]
        assert len(nonempty) == _BEATS_PER_LEVEL  # filled the per-level budget...
        assert produced[_BEATS_PER_LEVEL] == ""  # ...then quiet (nowhere deeper)

    asyncio.run(run())


def test_cascade_city_allowed_without_facts_but_never_street():
    """Anti-fabrication is LEVEL-AWARE. With no verified facts the model invents obscure
    street/district detail — but it reliably knows a NAMED CITY. So with the planned arc
    exhausted the cascade keeps talking about the *city* (grounded, [SILENCE] if unsure),
    the "лучше бы про город дальше говорил" fix — and never descends to the street it
    would make up."""
    orch = _orch([])

    topics: list[str] = []

    async def fake_narrate_area(address, **kw):
        topics.append(kw["topic"])
        return f"city beat: {kw['topic'][:20]}", None

    orch.pipeline.narrate_area = fake_narrate_area

    async def run():
        st = await orch.store.load("nofacts")
        st.address = Address(city="Долгопрудный", street="Парковая")
        st.area_facts = ""  # enrichment came back empty
        st.narrative_plan.outline = []  # planned arc exhausted -> only the cascade is left
        out = await orch._area_line(st, Pace.SLOW)
        assert out.startswith("city beat:")  # kept talking instead of going quiet...
        assert any("Долгопрудный" in t for t in topics)  # ...about the city
        assert not any("Парковая" in t for t in topics)  # never the ungrounded street

    asyncio.run(run())


def test_narrate_reach_tight_for_low_factless_full_for_notable():
    """The Ивушка distance fix: a LOW-significance, fact-less object only counts as 'passing'
    within the tight bubble; a notable or fact-bearing one keeps the full narrate bubble."""
    from app.services.agent.orchestrator import Orchestrator
    from app.shared.schemas import Candidate, GazeConfidence

    def cand(weight, facts):
        return Candidate(
            place=_place("x", "X", "kindergarten"), distance_m=40.0, type_weight=weight,
            in_gaze_cone=False, gaze_confidence=GazeConfidence.LOW, facts_available=facts,
        )

    # LOW weight + no facts -> tight bubble (48 m Ивушка would no longer fire)
    assert Orchestrator._narrate_reach_m(cand(0.3, False)) == settings.narrate_radius_low_m
    # same LOW object but WITH facts -> full bubble (worth a passing mention)
    assert Orchestrator._narrate_reach_m(cand(0.3, True)) == settings.narrate_radius_m
    # MEDIUM+ object -> full bubble even without facts
    assert Orchestrator._narrate_reach_m(cand(0.6, False)) == settings.narrate_radius_m


def test_cityless_fallback_capped_then_rearmed_by_object():
    """The fact-less city fallback fabricates fresh (non-repeating) specifics every tick, so
    is_repeat can't stop it (8 invented monologues down 1-я Советская). It is hard-capped at
    area_cityless_max grounded lines per dry stretch, then goes quiet; a real object re-arms it."""
    orch = _orch([])

    n = 0

    async def fake_narrate_area(address, **kw):
        nonlocal n
        n += 1
        # DISTINCT text each call, so is_repeat can't stop it — only the cap can.
        return f"выдуманный факт номер {n} про город", None

    orch.pipeline.narrate_area = fake_narrate_area

    async def run():
        st = await orch.store.load("cityless")
        st.address = Address(city="Долгопрудный", street="1-я Советская")
        st.area_facts = ""  # enrichment empty -> the fabrication-prone fallback
        st.narrative_plan.outline = []  # only the cascade is left
        cap = settings.area_cityless_max
        produced = [await orch._area_line(st, Pace.SLOW) for _ in range(cap + 4)]
        nonempty = [t for t in produced if t]
        assert len(nonempty) == cap  # capped despite every beat being textually distinct
        assert produced[cap] == ""  # went quiet instead of inventing another street "fact"

        # A real object narrated -> the filler re-arms (matches _commit_step's reset).
        st.area_cityless_beats = 0
        assert await orch._area_line(st, Pace.SLOW)  # talks again after real content

    asyncio.run(run())


def test_prefetch_area_is_read_only():
    """The background beat pre-generation warms the NEXT outline beat WITHOUT touching
    session state (no store.save, no told/history/counter mutation) — that read-only
    property is what makes it safe to run concurrently with delivery / barge-in / weave."""
    orch = _orch([])

    async def fake_narrate_area(address, **kw):
        return f"warmed: {kw['topic']}", "next-hook"

    orch.pipeline.narrate_area = fake_narrate_area

    async def run():
        st = await orch.store.load("pf")
        st.address = Address(city="Москва")
        st.area_facts = "Проверенные факты."  # facts resolved -> prefetch is eligible
        st.narrative_plan.outline = ["arc-1", "arc-2"]
        await orch.store.save(st)
        pre = await orch.prefetch_area("pf", Pace.SLOW)
        assert pre == ("arc-1", "warmed: arc-1", "next-hook")  # next outline topic, generated
        # nothing was committed — state is exactly as it was
        st2 = await orch.store.load("pf")
        assert st2.narrative_plan.told == []
        assert st2.area_beats == 0
        assert st2.narration_history == []
        assert st2.narrative_plan.next_hook is None

    asyncio.run(run())


def test_prefetch_area_bails_without_resolved_facts():
    """Read-only means it must NOT trigger the (state-mutating) area-facts fetch: if facts
    aren't resolved yet, prefetch bails so the live path fetches them first."""
    orch = _orch([])

    async def boom(*a, **k):  # must never be called
        raise AssertionError("narrate_area called before facts resolved")

    orch.pipeline.narrate_area = boom

    async def run():
        st = await orch.store.load("pf2")
        st.address = Address(city="Москва")
        st.area_facts = None  # not fetched yet
        st.narrative_plan.outline = ["arc-1"]
        await orch.store.save(st)
        assert await orch.prefetch_area("pf2", Pace.SLOW) is None

    asyncio.run(run())


def test_commit_area_lands_fresh_beat_and_discards_stale():
    """commit_area lands a warmed beat only while it's STILL the next outline topic; once
    that topic has been covered (or a theme-switch rebuilt the outline) it discards the
    warmed beat so the tour never re-tells or drifts to a stale arc."""
    orch = _orch([])
    orch.pipeline.narrate_area = None  # commit uses the pregen text, never the LLM

    async def run():
        st = await orch.store.load("cm")
        st.address = Address(city="Москва")
        st.area_facts = "Факты."
        st.narrative_plan.outline = ["arc-1", "arc-2"]
        await orch.store.save(st)

        # fresh: arc-1 is still next -> committed, state advances
        out = await orch.commit_area("cm", "arc-1", "warmed one.", "hook", Pace.SLOW)
        assert out is not None and out.text == "warmed one."
        st = await orch.store.load("cm")
        assert "arc-1" in st.narrative_plan.told
        assert st.area_beats == 1
        assert st.narrative_plan.next_hook == "hook"

        # stale: arc-1 already told, so a second warmed arc-1 is dropped (no double-tell)
        assert await orch.commit_area("cm", "arc-1", "warmed again.", "h2", Pace.SLOW) is None
        st = await orch.store.load("cm")
        assert st.area_beats == 1  # unchanged

    asyncio.run(run())


def test_passing_notable_object_floored_when_facts_cold_not_left_silent():
    """A passing, notable (MEDIUM+) object whose facts are cold/empty must still be
    NAMED on first contact via the deterministic floor mention — never left silent and
    never burned out by the gate (the "10 минут вокруг памятника, так и не рассказал"
    bug). The model can silence it; the code floor guarantees a one-liner anyway."""
    p = _place("mon", "Памятник Пушкину", "monument")  # weight 0.9 -> HIGH (cold)

    async def run():
        orch = _orch([p], facts={})  # cold: no facts on the first approach
        await _skip_greeting(orch, "s")
        near = GeoPoint(lat=HERE.lat + 0.00035, lon=HERE.lon)  # ~39 m: in the bubble
        o1 = await orch.on_position("s", near, Heading(), Pace.SLOW)
        # named immediately via the floor mention, even with no facts and a silent model
        assert o1.kind == "narration" and o1.place_id == "mon"
        assert "Памятник Пушкину" in o1.text

    asyncio.run(run())


def test_is_near_duplicate_catches_verbatim_and_near_repeats():
    hist = ["Этот старый маяк построили в девятнадцатом веке для входа кораблей в порт."]
    # verbatim repeat
    assert is_near_duplicate(hist[0], hist)
    # near-verbatim: a single word swapped is still a repeat
    assert is_near_duplicate(
        "Этот старый маяк построили в девятнадцатом веке для входа кораблей в гавань.", hist
    )
    # containment: a shorter line fully inside an earlier longer one
    assert is_near_duplicate("Этот старый маяк построили в девятнадцатом веке.", hist)
    # genuinely new content is NOT a duplicate
    assert not is_near_duplicate("Совсем другая история про реку, мост и старый рынок рядом.", hist)
    # short lines (floor mentions, bridges) are never flagged
    assert not is_near_duplicate("Тут рядом — Маяк.", hist)
    assert not is_near_duplicate("Любой текст здесь.", [])  # empty history
