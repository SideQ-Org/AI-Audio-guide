from app.services.llm.router import Role, model_for
from app.shared.schemas import (
    GeoPoint,
    Heading,
    NarratorInput,
    Place,
    ScoredPlace,
    ScorerOutput,
    Significance,
)


def test_scorer_output_roundtrip():
    out = ScorerOutput(
        scored=[ScoredPlace(place_id="p1", significance=Significance.HIGH, reason="x")],
        next="p1",
        expand_radius=False,
    )
    again = ScorerOutput.model_validate_json(out.model_dump_json())
    assert again.next == "p1"
    assert again.scored[0].significance is Significance.HIGH


def test_narrator_input_defaults():
    ni = NarratorInput(
        place=Place(id="p1", name="Парк", category="park", location=GeoPoint(lat=1, lon=2)),
        significance=Significance.MEDIUM,
        distance_m=42.0,
        heading=Heading(),
    )
    assert ni.language == "ru"
    assert ni.flags.switching is False
    assert ni.pace.value == "slow"


def test_model_router_maps_all_roles():
    for role in Role:
        assert isinstance(model_for(role), str)
        assert model_for(role)
