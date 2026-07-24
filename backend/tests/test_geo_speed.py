"""Geo speed upgrades: geocoder grid cache, Overpass disk L2 cache, snapped query
center, lifecycle junk filter, and the slimmed selector set."""

from __future__ import annotations

import asyncio

from app.services.geo import providers
from app.services.geo.categories import is_junk
from app.services.geo.geocoder import OverpassGeocoder
from app.services.geo.providers import build_query
from app.shared.schemas import Address, GeoPoint

HERE = GeoPoint(lat=55.7539, lon=37.6208)


# --- geocoder grid cache ---------------------------------------------------------- #


def test_geocoder_grid_cache_hits_same_cell(monkeypatch):
    calls = {"n": 0}

    async def fake_fetch(query, **kw):
        calls["n"] += 1
        return [{"type": "relation", "tags": {"admin_level": "4", "name": "Москва"}}]

    monkeypatch.setattr("app.services.geo.geocoder.fetch_overpass_elements", fake_fetch)
    geo = OverpassGeocoder()

    async def run():
        a1 = await geo.reverse(HERE, "ru")
        # ~5 m away — the SAME ~11 m grid cell => served from cache, no network.
        near = GeoPoint(lat=HERE.lat + 0.00004, lon=HERE.lon)
        a2 = await geo.reverse(near, "ru")
        assert geo.cached(near, "ru") is not None
        return a1, a2

    a1, a2 = asyncio.run(run())
    assert a1.city == a2.city == "Москва"
    assert calls["n"] == 1


def test_geocoder_parallel_street_is_a_cache_miss(monkeypatch):
    """A point ~50 m away (a PARALLEL street's distance) must NOT reuse the cached cell —
    the coarse 110 m cell shared one address across parallel streets and named the wrong
    one after a turn. At ~11 m cells each street resolves for where you actually are."""
    calls = {"n": 0}

    async def fake_fetch(query, **kw):
        calls["n"] += 1
        # Street name derived from the query's lat so parallel streets differ.
        name = "Улица А" if "55.7539" in query else "Улица Б"
        return [
            {"type": "relation", "tags": {"admin_level": "4", "name": "Москва"}},
            {"type": "way", "tags": {"highway": "residential", "name": name},
             "geometry": [{"lat": 55.7539, "lon": 37.6208}]},
        ]

    monkeypatch.setattr("app.services.geo.geocoder.fetch_overpass_elements", fake_fetch)
    geo = OverpassGeocoder()

    async def run():
        await geo.reverse(HERE, "ru")
        # ~55 m north — a parallel street; must be a cache MISS (fresh resolve).
        parallel = GeoPoint(lat=HERE.lat + 0.0005, lon=HERE.lon)
        assert geo.cached(parallel, "ru") is None
        await geo.reverse(parallel, "ru")
        return calls["n"]

    assert asyncio.run(run()) == 2  # two distinct resolves, not one shared cell


def test_geocoder_cached_returns_none_cold():
    geo = OverpassGeocoder()
    assert geo.cached(HERE, "ru") is None


def test_geocoder_cache_expires(monkeypatch):
    from app.config import settings

    geo = OverpassGeocoder()
    geo._grid[geo._key(HERE, "ru")] = (-1e9, Address(city="Старый"))
    monkeypatch.setattr(settings, "geocoder_cache_ttl_s", 1.0)
    assert geo.cached(HERE, "ru") is None  # stale entry does not serve


# --- overpass disk L2 cache -------------------------------------------------------- #


def test_disk_cache_roundtrip(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "overpass_disk_cache", str(tmp_path))
    q = build_query(HERE, 800)
    elements = [{"type": "node", "id": 1, "lat": 55.75, "lon": 37.62, "tags": {"name": "x"}}]
    providers._disk_cache_write(q, elements)
    assert providers._disk_cache_read(q) == elements
    # TTL expiry
    monkeypatch.setattr(settings, "overpass_disk_ttl_s", 0.0)
    assert providers._disk_cache_read(q) is None


def test_disk_cache_off_by_default(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "overpass_disk_cache", "")
    assert providers._disk_cache_read("q") is None
    providers._disk_cache_write("q", [])  # no-op, must not raise


# --- snapped query center ----------------------------------------------------------- #


def test_query_center_snap_shares_cache_key():
    a = build_query(GeoPoint(lat=round(55.75391, 3), lon=round(37.62083, 3)), 800)
    b = build_query(GeoPoint(lat=round(55.75416, 3), lon=round(37.62058, 3)), 800)
    assert a == b  # ~30 m apart -> the same snapped query text (and disk-cache key)


# --- lifecycle junk + selector slimming ---------------------------------------------- #


def test_disused_and_abandoned_are_junk():
    assert is_junk({"amenity": "cinema", "disused": "yes"}) is True
    assert is_junk({"building": "yes", "abandoned": "yes"}) is True
    assert is_junk({"amenity": "cinema"}) is False
    # deliberate sightseeing ruins stay
    assert is_junk({"ruins": "yes", "abandoned": "yes"}) is False
    assert is_junk({"historic": "ruins", "disused": "yes"}) is False


def test_selectors_exclude_hotel_noise():
    q = build_query(HERE, 800)
    assert '"tourism"~"' in q  # positive list, not the bare key
    assert "hotel" not in q and "hostel" not in q
    assert '"water"];' not in q  # the bare water selector is gone
    assert '"waterway"~' in q  # rivers/canals still fetched


# --- empty answers are non-final in the mirror race ---------------------------------- #


def test_race_empty_primary_falls_through_to_data(monkeypatch):
    """A region-clipped self-hosted primary answers instantly-but-EMPTY outside its
    clip — the race must not accept that as the win when another mirror has data."""
    from app.config import settings

    monkeypatch.setattr(settings, "overpass_url", "http://primary")
    monkeypatch.setattr(settings, "overpass_mirrors", "http://second")
    monkeypatch.setattr(settings, "overpass_race", 2)
    monkeypatch.setattr(settings, "overpass_race_stagger_s", 0.0)
    monkeypatch.setattr(settings, "overpass_disk_cache", "")

    data = [{"type": "node", "id": 1, "lat": 1.0, "lon": 2.0, "tags": {"name": "x"}}]

    async def fake_fetch_one(client, url, query):
        return [] if "primary" in url else data

    monkeypatch.setattr(providers, "_fetch_one", fake_fetch_one)
    out = asyncio.run(providers.fetch_overpass_elements("q1"))
    assert out == data


def test_race_all_empty_is_a_real_empty(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "overpass_url", "http://primary")
    monkeypatch.setattr(settings, "overpass_mirrors", "http://second")
    monkeypatch.setattr(settings, "overpass_race", 2)
    monkeypatch.setattr(settings, "overpass_race_stagger_s", 0.0)
    monkeypatch.setattr(settings, "overpass_disk_cache", "")

    async def fake_fetch_one(client, url, query):
        return []

    monkeypatch.setattr(providers, "_fetch_one", fake_fetch_one)
    assert asyncio.run(providers.fetch_overpass_elements("q2")) == []
