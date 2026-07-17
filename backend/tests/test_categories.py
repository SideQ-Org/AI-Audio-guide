"""Junk filter: private service/commerce (clinics/dentists/kindergartens/…) is dropped before
becoming a Place, while landmarks (hospital/school) and cultural/nature objects are kept.
Weights/thresholds are NOT touched — this is a tag blacklist only."""

from __future__ import annotations

from app.services.geo.categories import is_junk
from app.services.geo.providers import _element_to_place
from app.shared.schemas import GeoPoint

_ORIGIN = GeoPoint(lat=55.75, lon=37.62)


def test_is_junk_true_for_service_commerce():
    for tags in [
        {"amenity": "clinic"}, {"amenity": "doctors"}, {"amenity": "dentist"},
        {"amenity": "veterinary"}, {"amenity": "childcare"}, {"amenity": "kindergarten"},
        {"amenity": "pharmacy"}, {"amenity": "social_facility"}, {"amenity": "nursing_home"},
        {"healthcare": "dentist"}, {"healthcare": "physiotherapist"}, {"healthcare": "laboratory"},
    ]:
        assert is_junk(tags) is True, tags


def test_is_junk_false_for_landmarks_and_culture():
    for tags in [
        {"amenity": "hospital"}, {"healthcare": "hospital"}, {"amenity": "school"},
        {"tourism": "museum"}, {"historic": "monument"}, {"leisure": "park"},
        {"amenity": "community_centre"}, {"man_made": "tower"}, {},
    ]:
        assert is_junk(tags) is False, tags


def test_cultural_anchor_beats_junk():
    # A museum in a former clinic (both tags) stays — sightseeing value wins.
    assert is_junk({"amenity": "clinic", "tourism": "museum"}) is False
    assert is_junk({"amenity": "dentist", "historic": "yes"}) is False


def _el(tags):
    return {"type": "node", "id": 1, "lat": 55.751, "lon": 37.621, "tags": tags}


def test_element_to_place_drops_junk():
    for tags in [
        {"amenity": "clinic", "name": "Стоматология Улыбка"},
        {"amenity": "kindergarten", "name": "Ивушка"},
        {"healthcare": "dentist", "name": "Дент"},
    ]:
        assert _element_to_place(_el(tags), _ORIGIN) is None, tags


def test_element_to_place_keeps_real_objects():
    museum = _element_to_place(_el({"tourism": "museum", "name": "Музей"}), _ORIGIN)
    assert museum is not None and museum.name == "Музей"
    school = _element_to_place(_el({"amenity": "school", "name": "Школа №1"}), _ORIGIN)
    assert school is not None  # kept as a landmark


def test_toggle_off_keeps_junk(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "filter_junk_objects", False)
    clinic = _element_to_place(_el({"amenity": "clinic", "name": "Клиника"}), _ORIGIN)
    assert clinic is not None  # filter disabled -> the object is created again
