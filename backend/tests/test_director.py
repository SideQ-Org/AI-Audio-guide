"""Unit tests for the narrative director's callback lookup (deterministic, no I/O)."""

from __future__ import annotations

from app.services.agent.director import (
    atomize_facts,
    find_callback,
    find_lookahead,
    find_revisit,
)
from app.shared.memory import ObjectMemo
from app.shared.schemas import Candidate, GazeConfidence, GeoPoint, Place


def _place(pid: str, category: str, name: str = "X") -> Place:
    return Place(id=pid, name=name, category=category, location=GeoPoint(lat=0.0, lon=0.0))


def _cand(pid, name, cat, dist, *, cone=True, weight=0.8) -> Candidate:
    return Candidate(
        place=_place(pid, cat, name), distance_m=dist, type_weight=weight,
        in_gaze_cone=cone, gaze_confidence=GazeConfidence.HIGH,
    )


def _memo(pid: str, category: str, name: str) -> ObjectMemo:
    return ObjectMemo(id=pid, name=name, category=category)


def test_callback_finds_earlier_same_category():
    objs = [
        _memo("a", "church", "Церковь Ильи"),
        _memo("b", "park", "Парк"),
        _memo("c", "building", "Дом"),  # +2 gap so the church is reachable at min_gap=2
        _memo("d", "cafe", "Кафе"),
    ]
    cb = find_callback(objs, _place("z", "church", "Храм Спаса"))
    assert cb is not None
    assert cb.name == "Церковь Ильи"
    assert cb.category == "church"


def test_callback_excludes_too_recent():
    # The only same-category object is within the last `min_gap` -> too close to be a callback.
    objs = [_memo("a", "park", "Парк"), _memo("b", "church", "Церковь")]
    assert find_callback(objs, _place("z", "church", "Храм"), min_gap=2) is None


def test_callback_none_when_unrelated():
    objs = [_memo("a", "park", "Парк"), _memo("b", "museum", "Музей"), _memo("c", "hall", "Дом")]
    assert find_callback(objs, _place("z", "church", "Храм")) is None


def test_callback_skips_dull_categories():
    # Two cafes: a callback ("as that cafe earlier") adds nothing -> skip commercial categories.
    objs = [_memo("a", "cafe", "Кафе"), _memo("b", "park", "Парк"), _memo("c", "park", "Сквер")]
    assert find_callback(objs, _place("z", "cafe", "Кафе два")) is None


def test_callback_ignores_unnamed_and_self():
    objs = [_memo("a", "church", ""), _memo("z", "church", "Тот же")]  # unnamed + the object itself
    assert find_callback(objs, _place("z", "church", "Храм"), min_gap=0) is None


def test_callback_empty_memory():
    assert find_callback([], _place("z", "church", "Храм")) is None


def test_atomize_facts_splits_sentences_and_drops_fragments():
    text = "Мост построили в тысяча девятьсот десятом году. Его длина двести метров. Да."
    facts = atomize_facts(text)
    assert len(facts) == 2  # the "Да." fragment (< 16 chars) is dropped
    assert facts[0].startswith("Мост построили")


def test_atomize_facts_empty():
    assert atomize_facts(None) == []
    assert atomize_facts("   ") == []


def test_lookahead_picks_nearest_notable_ahead():
    cands = [
        _cand("a", "Скамейка", "bench", 120, weight=0.2),  # not notable
        _cand("b", "Старая усадьба", "manor", 180),  # notable but farther
        _cand("c", "Церковь", "church", 90),  # notable, nearest ahead -> pick
        _cand("d", "Памятник", "monument", 200, cone=False),  # not in cone
    ]
    la = find_lookahead(cands, seen=[], min_ahead_m=55)
    assert la is not None
    assert la.name == "Церковь"


def test_lookahead_excludes_in_bubble_seen_and_dull():
    # In the bubble (<= min_ahead_m) -> it's "here", not ahead.
    assert find_lookahead([_cand("a", "Храм", "church", 40)], seen=[], min_ahead_m=55) is None
    # Already narrated.
    assert find_lookahead([_cand("a", "Храм", "church", 90)], seen=["a"], min_ahead_m=55) is None
    # Dull/commercial category is never worth teasing.
    assert find_lookahead([_cand("a", "Пятёрочка", "shop", 90)], seen=[], min_ahead_m=55) is None


def test_lookahead_none_when_nothing_ahead():
    assert find_lookahead([], seen=[], min_ahead_m=55) is None


def _memo_at(pid, lat, lon, said_route) -> ObjectMemo:
    return ObjectMemo(id=pid, name="Церковь", category="church", lat=lat, lon=lon,
                      said_route_m=said_route)


_HERE = GeoPoint(lat=55.75005, lon=37.61005)  # ~7 m from (55.7500, 37.6100)


def test_revisit_fires_when_returned_and_walked_away():
    o = _memo_at("a", 55.7500, 37.6100, 0.0)
    # Back near it AND walked 300 m along the route since telling it -> revisit.
    assert find_revisit([o], _HERE, 300.0, radius_m=60, min_route_m=250) is o


def test_revisit_gated_right_after_narration():
    o = _memo_at("a", 55.7500, 37.6100, 0.0)
    # Near it but only 100 m of route walked since -> too soon, stay silent (no "снова тут").
    assert find_revisit([o], _HERE, 100.0, radius_m=60, min_route_m=250) is None


def test_revisit_needs_proximity():
    o = _memo_at("a", 55.7500, 37.6100, 0.0)
    far = GeoPoint(lat=55.76, lon=37.62)  # ~1 km away
    assert find_revisit([o], far, 300.0, radius_m=60, min_route_m=250) is None


def test_revisit_skips_memos_without_position():
    o = ObjectMemo(id="a", name="X", category="church")  # pre-revisit memo, no lat/lon
    assert find_revisit([o], _HERE, 300.0, radius_m=60, min_route_m=250) is None
