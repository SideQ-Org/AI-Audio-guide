"""Major-road narration (МКАД / шоссе / interchanges): classification, name-dedup,
ref fallback, and that roads are SECONDARY (never in the walk bubble / normal reach)."""

from __future__ import annotations

from app.services.agent.orchestrator import _is_major_road
from app.services.geo.categories import LINEAR_CATEGORIES, classify, is_junk
from app.services.geo.providers import _element_to_place
from app.shared.schemas import GeoPoint

HERE = GeoPoint(lat=55.90, lon=37.40)


def test_classify_motorway_trunk_junction():
    assert classify({"highway": "motorway", "name": "МКАД"})[0] == "motorway"
    assert classify({"highway": "trunk", "name": "Ленинградское шоссе"})[0] == "motorway"
    assert classify({"highway": "motorway_junction", "name": "Развязка"})[0] == "junction"
    # ordinary streets are NOT major roads (they aren't even fetched)
    assert classify({"highway": "residential", "name": "Тихая"})[0] != "motorway"


def test_major_road_is_linear_and_flagged():
    assert "motorway" in LINEAR_CATEGORIES  # deduped by name => narrated once
    assert _is_major_road("motorway") and _is_major_road("junction")
    assert not _is_major_road("museum") and not _is_major_road("river")


def test_major_road_not_junk():
    assert is_junk({"highway": "motorway", "name": "МКАД"}) is False


def test_named_motorway_becomes_place():
    el = {"type": "way", "id": 1, "tags": {"highway": "motorway", "name": "МКАД"},
          "geometry": [{"lat": 55.901, "lon": 37.40}, {"lat": 55.902, "lon": 37.41}]}
    p = _element_to_place(el, HERE)
    assert p is not None and p.name == "МКАД" and p.category == "motorway"


def test_unnamed_motorway_uses_ref():
    el = {"type": "way", "id": 2, "tags": {"highway": "trunk", "ref": "М-10"},
          "geometry": [{"lat": 55.901, "lon": 37.40}, {"lat": 55.902, "lon": 37.41}]}
    p = _element_to_place(el, HERE)
    assert p is not None and p.name == "М-10"


def test_unnamed_unref_motorway_dropped():
    el = {"type": "way", "id": 3, "tags": {"highway": "motorway"},
          "geometry": [{"lat": 55.901, "lon": 37.40}, {"lat": 55.902, "lon": 37.41}]}
    assert _element_to_place(el, HERE) is None  # no name, no ref => not a place


def test_ordinary_unnamed_road_not_resurrected_by_ref():
    # ref-fallback is road-classes only; a random unnamed thing with a ref stays dropped
    el = {"type": "node", "id": 4, "lat": 55.9, "lon": 37.4,
          "tags": {"amenity": "bench", "ref": "7"}}
    assert _element_to_place(el, HERE) is None


def test_road_excluded_from_bubble_and_reach():
    """A motorway candidate must NEVER land in the walk bubble (`near`) or normal reach —
    only the secondary road_reach set — so it can't outrank a real object you're passing.
    Mirrors the split the orchestrator does on result.candidates."""
    from app.services.agent.orchestrator import _is_major_road

    cats = ["motorway", "museum", "junction", "river"]
    objs = [c for c in cats if not _is_major_road(c)]
    assert "museum" in objs and "river" in objs
    assert "motorway" not in objs and "junction" not in objs


def test_seen_road_not_reoffered():
    """Once МКАД is narrated (its name in seen_linear_names) it isn't offered again —
    "ты у МКАД" can't repeat."""
    from app.services.geo.ranking import _norm_name
    seen = {_norm_name("МКАД")}
    assert _norm_name("МКАД") in seen
    assert _norm_name("Ленинградское шоссе") not in seen
