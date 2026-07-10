import asyncio
from pathlib import Path

from app.services.geo.discovery import Discovery
from app.services.geo.providers import StaticPlaceProvider, _element_to_place
from app.shared.schemas import GazeConfidence, GeoPoint, Heading, Pace, Place
from sim.routes import RED_SQUARE
from sim.walk import walk

FIXTURE = Path(__file__).parent / "fixtures" / "places_red_square.json"


def test_unnamed_sports_pitch_gets_a_synthesized_name():
    """A neighbourhood football field (leisure=pitch sport=soccer) is usually unnamed —
    it must survive discovery with a synthesized name, while a tiny table-tennis court
    stays nameless and is dropped (no flooding the tour)."""
    o = GeoPoint(lat=55.0, lon=37.0)
    soccer = {"type": "way", "id": 1, "tags": {"leisure": "pitch", "sport": "soccer"},
              "geometry": [{"lat": 55.0, "lon": 37.0}], "center": {"lat": 55.0, "lon": 37.0}}
    p = _element_to_place(soccer, o)
    assert p is not None and p.name == "футбольное поле" and p.category == "pitch"
    tt = {"type": "node", "id": 2, "tags": {"leisure": "pitch", "sport": "table_tennis"},
          "lat": 55.0, "lon": 37.0}
    assert _element_to_place(tt, o) is None  # not a notable sport -> dropped


def test_walk_yields_monotonic_steps_with_heading():
    steps = list(walk(RED_SQUARE, speed_mps=1.3, step_s=8.0))
    assert len(steps) > 3
    times = [s.t for s in steps]
    assert times == sorted(times)
    assert all(s.heading.direction_deg is not None for s in steps)
    assert all(s.heading.gaze_confidence is GazeConfidence.LOW for s in steps)
    assert steps[0].pace is Pace.SLOW


def test_fixture_provider_loads_places():
    provider = StaticPlaceProvider.from_json(FIXTURE)
    places = asyncio.run(provider.fetch_places(RED_SQUARE[0], 100.0))
    assert len(places) == 6
    assert any(p.name == "ГУМ" for p in places)


def test_discovery_ranks_candidates_along_walk():
    async def run() -> set[str]:
        provider = StaticPlaceProvider.from_json(FIXTURE)
        discovery = Discovery(provider)
        seen: list[str] = []
        distinct: set[str] = set()
        for step in walk(RED_SQUARE, speed_mps=1.3, step_s=8.0):
            result = await discovery.discover_adaptive(step.position, step.heading, seen, 80.0)
            if result.candidates:
                top = result.candidates[0]
                distinct.add(top.place.id)
                if top.place.id not in seen:
                    seen.append(top.place.id)
        return distinct

    distinct = asyncio.run(run())
    # over the Red Square walk we should surface several distinct landmarks
    assert len(distinct) >= 3


def test_adaptive_radius_expands_when_empty():
    async def run() -> object:
        far = Place(
            id="p1",
            name="Дальний музей",
            category="museum",
            location=GeoPoint(lat=55.7560, lon=37.6205),
        )
        provider = StaticPlaceProvider([far])
        discovery = Discovery(provider, max_radius_m=500.0)
        origin = GeoPoint(lat=55.7537, lon=37.6205)
        return await discovery.discover_adaptive(origin, Heading(), [], radius_m=80.0)

    result = asyncio.run(run())
    assert result.expanded is True
    assert result.radius_m > 80.0
    assert result.candidates and result.candidates[0].place.id == "p1"
