"""Reverse geocoding parse + the area-level monologue flow (general -> specific)."""

import asyncio

from app.services.agent.companion import HeuristicCompanion
from app.services.agent.narrator import Narrator
from app.services.agent.orchestrator import Orchestrator
from app.services.agent.pipeline import TextPipeline
from app.services.agent.scorer import HeuristicScorer
from app.services.enrichment.enricher import MockEnricher
from app.services.geo.discovery import Discovery
from app.services.geo.geocoder import MockGeocoder, parse_address
from app.services.geo.providers import StaticPlaceProvider
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import (
    Address,
    AreaInput,
    GeoPoint,
    Heading,
    NarratorInput,
    Pace,
    Place,
    PlannerInput,
    PlannerOutput,
)


# --------------------------------------------------------------------------- #
# parse_address — the locale heuristic
# --------------------------------------------------------------------------- #
def _admin(level, name):
    return {"type": "relation", "tags": {"admin_level": str(level), "name": name}}


def test_parse_federal_city_moscow():
    # Moscow: city is admin_level 4, district 8; macro levels 3/5 are ignored.
    els = [
        _admin(2, "Россия"),
        _admin(3, "Центральный федеральный округ"),
        _admin(4, "Москва"),
        _admin(5, "Центральный административный округ"),
        _admin(8, "Таганский район"),
        {"type": "way", "tags": {"highway": "residential", "name": "Воронцовская улица"}},
    ]
    addr = parse_address(els, "ru")
    assert addr.country == "Россия"
    assert addr.city == "Москва"
    assert addr.district == "Таганский район"
    assert addr.street == "Воронцовская улица"


def test_parse_ordinary_city_prefers_level6():
    # Ordinary city: level 4 is the oblast, level 6 is the city itself.
    els = [
        _admin(2, "Россия"),
        _admin(4, "Тульская область"),
        _admin(6, "Тула"),
        _admin(8, "Центральный район"),
    ]
    addr = parse_address(els, "ru")
    assert addr.city == "Тула"  # not the oblast
    assert addr.district == "Центральный район"


def test_parse_prefers_localized_name_and_empty():
    els = [{"type": "relation", "tags": {"admin_level": "4", "name": "Moscow", "name:ru": "Москва"}}]
    assert parse_address(els, "ru").city == "Москва"
    assert parse_address([], "ru") == Address()


def test_parse_street_prefers_highest_road_class():
    """On a wide avenue several named ways fall in range; the resolved street must be
    the main road (проспект), not whichever side street Overpass listed first — that
    stability is what stops the street name (and the area story) from flickering."""
    els = [
        # a side street is returned FIRST, the avenue second — class must win, not order
        {"type": "way", "tags": {"highway": "residential", "name": "Тупиковый переулок"}},
        {"type": "way", "tags": {"highway": "primary", "name": "проспект Пацаева"}},
        {"type": "way", "tags": {"highway": "service", "name": "Дублёр"}},
    ]
    assert parse_address(els, "ru").street == "проспект Пацаева"
    # a single named street is still picked as-is
    one = [{"type": "way", "tags": {"highway": "residential", "name": "Воронцовская улица"}}]
    assert parse_address(one, "ru").street == "Воронцовская улица"


def test_parse_street_picks_nearest_when_geometry_given():
    """With a point + way geometry, the NEAREST street wins even if a bigger road is
    also in range — so a generous search radius never mislabels you onto a far avenue
    you can merely see. Class only breaks ties between equally-near streets."""
    here = GeoPoint(lat=55.7000, lon=37.6000)

    def way(name, hw, dlat):  # a short E-W segment offset dlat degrees north of `here`
        lat = here.lat + dlat
        return {
            "type": "way", "tags": {"highway": hw, "name": name},
            "geometry": [
                {"lat": lat, "lon": here.lon - 0.001},
                {"lat": lat, "lon": here.lon + 0.001},
            ],
        }

    els = [
        way("Большой проспект", "primary", 0.0009),   # a primary, but ~100 m away
        way("Тихая улица", "residential", 0.00015),   # residential, but ~17 m away (here)
    ]
    assert parse_address(els, "ru", here).street == "Тихая улица"  # nearest wins
    # far primary out of the picture -> the street you're on is chosen, not the avenue


def test_parse_street_confidence_by_distance():
    """B5/wish c: assert the street only when the user is close to it. A nearest named
    way beyond ~20 m => street_confident=False, so the guide won't claim "идёшь по улице
    X" while mid-block / between streets."""
    from app.shared.schemas import GeoPoint

    here = GeoPoint(lat=55.7000, lon=37.6000)

    def way(name, dlat):
        lat = here.lat + dlat
        return {
            "type": "way", "tags": {"highway": "residential", "name": name},
            "geometry": [
                {"lat": lat, "lon": here.lon - 0.001},
                {"lat": lat, "lon": here.lon + 0.001},
            ],
        }

    near = parse_address([way("Тихая улица", 0.00015)], "ru", here)  # ~17 m
    assert near.street == "Тихая улица" and near.street_confident is True
    far = parse_address([way("Дальняя улица", 0.0005)], "ru", here)  # ~56 m
    assert far.street == "Дальняя улица" and far.street_confident is False


# --------------------------------------------------------------------------- #
# Story-arc flow — opener -> woven object -> outline-advancing area beats
# --------------------------------------------------------------------------- #
class ScriptNarrator:
    """Narrator that returns tagged text so the flow is observable."""

    async def narrate(self, inp: NarratorInput) -> str:
        return f"OBJECT:{inp.place.name}"

    async def narrate_area(self, inp: AreaInput) -> str:
        return f"AREA:{inp.topic}"


class ScriptPlanner:
    """Planner returning a fixed arc so the flow is deterministic."""

    async def plan(self, inp: PlannerInput) -> PlannerOutput:
        return PlannerOutput(
            theme="ремёсла Таганки",
            outline=["слобода", "улицы", "люди"],
            opener=f"OPENER:{inp.address.district}",
        )


def _orch(places) -> Orchestrator:
    pipeline = TextPipeline(
        HeuristicScorer(), ScriptNarrator(), MockEnricher({}), planner=ScriptPlanner()
    )
    geocoder = MockGeocoder(Address(country="Россия", city="Москва", district="Таганский район"))
    return Orchestrator(
        Discovery(StaticPlaceProvider(places)),
        pipeline,
        HeuristicCompanion(),
        InMemoryStateStore(),
        geocoder=geocoder,
    )


def test_arc_opener_then_woven_object_then_outline_beats_no_repeats():
    place = Place(
        id="node/1", name="Дом Музы", category="historic",
        location=GeoPoint(lat=55.7415, lon=37.6539),
    )

    async def run():
        orch = _orch([place])
        st = await orch.store.load("s1")  # skip the one-time opener greeting for this arc test
        st.greeted = True
        await orch.store.save(st)
        here = GeoPoint(lat=55.7415, lon=37.6539)
        outs = []
        for _ in range(5):
            out = await orch.on_position("s1", here, Heading(), Pace.SLOW)
            outs.append(out.text)
        return outs

    outs = asyncio.run(run())
    # 1st line is the arc opener (formed by the planner), before any object.
    assert outs[0] == "OPENER:Таганский район"
    # the nearby object is woven in once reached.
    assert "OBJECT:Дом Музы" in outs
    # gaps are filled by advancing the outline, each topic once (no repeats).
    beats = [o for o in outs if o.startswith("AREA:")]
    assert "AREA:слобода" in beats
    assert len(beats) == len(set(beats))  # outline topics never repeat


def test_user_question_is_queued_to_weave_into_narration():
    async def run():
        orch = _orch([])
        # establish an area first
        await orch.on_position("s2", GeoPoint(lat=55.7415, lon=37.6539), Heading(), Pace.SLOW)
        await orch.on_utterance("s2", "А кто такой Высоцкий?")
        st = await orch.store.load("s2")
        return st.narrative_plan.pending_focus

    pending = asyncio.run(run())
    assert "А кто такой Высоцкий?" in pending  # queued as a focus topic to weave next
