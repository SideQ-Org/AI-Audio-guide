import asyncio

from app.services.geo.inventory import InventoryStore, SessionInventory
from app.shared.schemas import GeoPoint, Place

HERE = GeoPoint(lat=55.7537, lon=37.6205)


def _place(pid="x", lat=55.7537, lon=37.6205) -> Place:
    return Place(id=pid, name=pid, category="monument", location=GeoPoint(lat=lat, lon=lon))


class CountingProvider:
    """Counts fetch_places calls; returns a fixed list regardless of radius."""

    def __init__(self, places):
        self.places = places
        self.calls = 0

    async def fetch_places(self, center, radius_m):
        self.calls += 1
        return list(self.places)


def test_inventory_skips_overpass_until_anchor_left():
    """The wide disc is fetched once and reused for nearby ticks; Overpass is
    re-hit only after the user walks past half the disc radius from the anchor."""

    async def run():
        prov = CountingProvider([_place()])
        store = InventoryStore()
        sid = "s"
        await store.ensure(sid, HERE, prov)
        assert prov.calls == 1
        # small move (~55 m, well inside the 400 m re-fetch edge) -> served from cache
        near = GeoPoint(lat=HERE.lat + 0.0005, lon=HERE.lon)
        await store.ensure(sid, near, prov)
        assert prov.calls == 1
        # big move (~555 m, past the 400 m edge) -> one fresh fetch, re-anchored
        far = GeoPoint(lat=HERE.lat + 0.005, lon=HERE.lon)
        inv = await store.ensure(sid, far, prov)
        assert prov.calls == 2
        assert inv.anchor == far  # re-centred where we last looked

    asyncio.run(run())


def test_inventory_keeps_stale_disc_on_empty_fetch():
    """A transient empty Overpass result must not blank a usable inventory."""

    async def run():
        prov = CountingProvider([_place()])
        store = InventoryStore()
        sid = "s"
        await store.ensure(sid, HERE, prov)
        prov.places = []  # next fetch comes back empty (transient miss / sparse)
        far = GeoPoint(lat=HERE.lat + 0.005, lon=HERE.lon)
        inv = await store.ensure(sid, far, prov)
        assert prov.calls == 2
        assert [p.id for p in inv.places] == ["x"]  # kept the last good disc
        assert inv.anchor == far  # but re-anchored, so it won't hammer next tick

    asyncio.run(run())


def test_take_places_update_pushes_once_per_change():
    """The map-pin push fires once when the disc is built and again only when it
    changes — never re-pushing an unchanged disc."""

    async def run():
        prov = CountingProvider([_place("a")])
        store = InventoryStore()
        sid = "s"
        await store.ensure(sid, HERE, prov)
        first = store.take_places_update(sid)
        assert first is not None and [p.id for p in first] == ["a"]
        assert store.take_places_update(sid) is None  # unchanged -> no re-push
        prov.places = [_place("a"), _place("b")]  # disc changes on the next refetch
        far = GeoPoint(lat=HERE.lat + 0.005, lon=HERE.lon)
        await store.ensure(sid, far, prov)
        upd = store.take_places_update(sid)
        assert upd is not None and {p.id for p in upd} == {"a", "b"}

    asyncio.run(run())


def test_approach_marks_passed_after_closest_approach():
    """An object the user walks toward and then past is flagged `passed`, so the
    guide can prefer what's ahead over what's behind."""
    store = InventoryStore()
    p = _place()  # at HERE
    inv = SessionInventory(anchor=HERE, places=[p], last_fetch_at=0.0)
    far = GeoPoint(lat=HERE.lat + 0.005, lon=HERE.lon)  # ~555 m
    near = GeoPoint(lat=HERE.lat + 0.0008, lon=HERE.lon)  # ~89 m (inside weave)
    away = GeoPoint(lat=HERE.lat + 0.004, lon=HERE.lon)  # ~445 m (receding past min)
    store.update_approach(inv, far)
    assert inv.approach["x"].passed is False
    store.update_approach(inv, near)  # closest approach
    assert inv.approach["x"].passed is False
    store.update_approach(inv, away)  # now clearly receding
    assert inv.approach["x"].passed is True
    assert store.passed_ids(inv) == {"x"}
