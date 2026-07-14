"""Cross-object anti-repeat in build_candidates (ranking.Dedup): a candidate that is the SAME
real-world thing as one already narrated is dropped — by linear-feature name (river split across
OSM ways), by wikidata QID (a landmark mapped as node+way+relation), or by same name within
dedup_name_radius_m (a park's label node + polygon). Plain id dedup stays the separate `seen`.
"""

from __future__ import annotations

from app.config import settings
from app.services.geo.ranking import Dedup, build_candidates
from app.shared.schemas import GeoPoint, Heading, Place

HERE = GeoPoint(lat=55.70, lon=37.61)
FAR = GeoPoint(lat=55.71, lon=37.61)  # ~1.1 km north — beyond dedup_name_radius_m


def _place(pid: str, name: str, category: str, at: GeoPoint = HERE, **tags: str) -> Place:
    return Place(id=pid, name=name, category=category, location=at, tags=tags)


def _ids(cands) -> set[str]:
    return {c.place.id for c in cands}


# --- linear features: dedup by NAME (segments can be far apart) ---------------------------- #


def test_second_river_segment_deduped_by_name() -> None:
    places = [_place("way/1", "Чура", "river"), _place("way/2", "Чура", "river", at=FAR)]
    # Fresh walk: both segments are candidates (nothing narrated).
    assert _ids(build_candidates(HERE, Heading(), places, 3000)) == {"way/1", "way/2"}
    # First segment narrated -> the other is dropped by name (even 1 km away).
    out = build_candidates(
        HERE, Heading(), places, 3000, seen=["way/1"],
        dedup=Dedup(linear_names=frozenset({"чура"})),
    )
    assert _ids(out) == set()


def test_pedestrian_promenade_deduped_by_name() -> None:
    places = [_place("way/1", "Арбат", "pedestrian"), _place("way/2", "Арбат", "pedestrian")]
    out = build_candidates(
        HERE, Heading(), places, 300, dedup=Dedup(linear_names=frozenset({"арбат"}))
    )
    assert _ids(out) == set()


# --- wikidata QID: the same entity, whatever the name/category ----------------------------- #


def test_same_wikidata_qid_deduped() -> None:
    # A church mapped as a node AND a building way — same wikidata, different ids/categories.
    places = [
        _place("node/1", "Храм Ильи", "place_of_worship", wikidata="Q123"),
        _place("way/9", "Ильинская церковь", "building", wikidata="Q123"),
    ]
    out = build_candidates(HERE, Heading(), places, 300, dedup=Dedup(wikidata=frozenset({"Q123"})))
    assert _ids(out) == set()  # both are entity Q123 — already narrated


def test_different_wikidata_not_deduped() -> None:
    places = [_place("node/1", "Храм", "place_of_worship", wikidata="Q999")]
    out = build_candidates(HERE, Heading(), places, 300, dedup=Dedup(wikidata=frozenset({"Q123"})))
    assert _ids(out) == {"node/1"}


# --- name + proximity: a same-named object right by a narrated one -------------------------- #


def test_same_name_nearby_deduped() -> None:
    # A pond "Бекет" narrated at HERE; its label-node duplicate a few metres away is dropped.
    close = GeoPoint(lat=HERE.lat + 0.0002, lon=HERE.lon)  # ~22 m
    places = [_place("node/5", "Бекет", "water", at=close)]
    out = build_candidates(
        HERE, Heading(), places, 300, dedup=Dedup(named=(("бекет", HERE.lat, HERE.lon),))
    )
    assert _ids(out) == set()


def test_same_name_far_away_not_deduped() -> None:
    # Two genuinely different places that happen to share a name (far apart) both stay.
    assert settings.dedup_name_radius_m < 1000  # guard: FAR is well beyond the radius
    places = [_place("node/6", "Бекет", "water", at=FAR)]
    out = build_candidates(
        HERE, Heading(), places, 3000, dedup=Dedup(named=(("бекет", HERE.lat, HERE.lon),))
    )
    assert _ids(out) == {"node/6"}


def test_no_dedup_context_keeps_everything() -> None:
    places = [_place("way/1", "Чура", "river"), _place("node/1", "Храм", "place_of_worship")]
    assert _ids(build_candidates(HERE, Heading(), places, 300)) == {"way/1", "node/1"}
