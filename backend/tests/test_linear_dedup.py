"""Linear-feature name dedup in build_candidates.

A river/canal/promenade is split across many OSM `way` segments sharing one name; id-based
dedup let the SAME river be narrated once per segment ("проходишь мимо речки Чуры" twice — the
lead's walk). build_candidates now also drops a linear candidate whose name was already narrated.
"""

from __future__ import annotations

from app.services.geo.ranking import build_candidates
from app.shared.schemas import GeoPoint, Heading, Place

HERE = GeoPoint(lat=55.70, lon=37.61)


def _place(pid: str, name: str, category: str) -> Place:
    return Place(id=pid, name=name, category=category, location=HERE)


def _ids(cands) -> set[str]:
    return {c.place.id for c in cands}


def test_second_river_segment_deduped_by_name() -> None:
    # Чура mapped as two ways (the actual bug): same name, distinct ids, both cat=river.
    places = [_place("way/112065482", "Чура", "river"), _place("way/137928470", "Чура", "river")]
    # Fresh walk: both segments are candidates (nothing narrated yet).
    assert _ids(build_candidates(HERE, Heading(), places, 300)) == {
        "way/112065482", "way/137928470",
    }
    # After the first segment is narrated (id seen + name recorded), the OTHER segment is dropped.
    out = build_candidates(
        HERE, Heading(), places, 300,
        seen=["way/112065482"], seen_linear_names=["Чура"],
    )
    assert _ids(out) == set()  # first seen by id, second dropped by name


def test_pedestrian_promenade_also_deduped() -> None:
    places = [_place("way/1", "Арбат", "pedestrian"), _place("way/2", "Арбат", "pedestrian")]
    out = build_candidates(HERE, Heading(), places, 300, seen_linear_names=["Арбат"])
    assert _ids(out) == set()


def test_area_water_pond_not_deduped_by_name() -> None:
    # A pond named "Чура" (cat=water = an AREA, a single object) must NOT be filtered by name.
    places = [_place("way/9", "Чура", "water")]
    out = build_candidates(HERE, Heading(), places, 300, seen_linear_names=["чура"])
    assert _ids(out) == {"way/9"}


def test_different_named_river_not_deduped() -> None:
    places = [_place("way/3", "Волга", "river")]
    out = build_candidates(HERE, Heading(), places, 300, seen_linear_names=["чура"])
    assert _ids(out) == {"way/3"}


def test_name_match_is_case_and_space_insensitive() -> None:
    places = [_place("way/4", "  ЧУРА ", "river")]
    out = build_candidates(HERE, Heading(), places, 300, seen_linear_names=["чура"])
    assert _ids(out) == set()
