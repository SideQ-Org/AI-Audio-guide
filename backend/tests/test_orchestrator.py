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
            if out.kind == "narration":
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
    p = _place("p", "Музей", "museum", lat=HERE.lat + 0.0013)  # ~145 m due north
    facts = {"p": "Большой музей."}

    async def run_facing_it():
        orch = _orch([p], facts=facts)
        facing = Heading(direction_deg=0.0)  # north — the object is in the cone
        out = await orch.on_position("s", HERE, facing, Pace.SLOW)
        assert out.kind == "narration"
        assert out.place_id == "p"

    async def run_facing_away():
        orch = _orch([p], facts=facts)
        away = Heading(direction_deg=180.0)  # south — object NOT in view -> stay silent
        out = await orch.on_position("s", HERE, away, Pace.SLOW)
        assert out.kind != "narration"

    asyncio.run(run_facing_it())
    asyncio.run(run_facing_away())


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


def test_cascade_skipped_without_facts_to_avoid_fabrication():
    """Anti-fabrication: with no verified area facts and the planned arc exhausted, the
    'atypical fact про <area>' cascade is NOT run (it would invent) — _area_line returns
    "" so the caller reaches a real object / bridges / goes quiet instead of fabricating."""
    orch = _orch([])

    called = False

    async def fake_narrate_area(address, **kw):
        nonlocal called
        called = True
        return f"fabricated: {kw['topic'][:20]}", None  # the model WOULD produce prose

    orch.pipeline.narrate_area = fake_narrate_area

    async def run():
        st = await orch.store.load("nofacts")
        st.address = Address(city="Долгопрудный", street="Парковая")
        st.area_facts = ""  # enrichment came back empty
        st.narrative_plan.outline = []  # planned arc exhausted -> only the cascade is left
        out = await orch._area_line(st, Pace.SLOW)
        assert out == "" and not called  # cascade gated, model never asked to invent

    asyncio.run(run())


def test_passing_notable_object_floored_when_facts_cold_not_left_silent():
    """A passing, notable (MEDIUM+) object whose facts are cold/empty must still be
    NAMED on first contact via the deterministic floor mention — never left silent and
    never burned out by the gate (the "10 минут вокруг памятника, так и не рассказал"
    bug). The model can silence it; the code floor guarantees a one-liner anyway."""
    p = _place("mon", "Памятник Пушкину", "monument")  # weight 0.9 -> HIGH (cold)

    async def run():
        orch = _orch([p], facts={})  # cold: no facts on the first approach
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
