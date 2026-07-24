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
    """The wide disc is fetched once and reused for nearby ticks. Walking toward the
    edge now triggers a BACKGROUND re-centre (stale-while-revalidate) — ensure()
    itself returns the current disc immediately and never blocks the tick."""

    async def run():
        prov = CountingProvider([_place()])
        store = InventoryStore()
        sid = "s"
        await store.ensure(sid, HERE, prov)
        assert prov.calls == 1
        # small move (~55 m, inside the predict fraction) -> served from cache, no bg
        near = GeoPoint(lat=HERE.lat + 0.0005, lon=HERE.lon)
        await store.ensure(sid, near, prov)
        assert prov.calls == 1 and not store._refreshing
        # big move (~555 m, past the predict edge, still inside the 800 m disc):
        # the STALE disc is returned instantly and a background refresh starts.
        far = GeoPoint(lat=HERE.lat + 0.005, lon=HERE.lon)
        inv = await store.ensure(sid, far, prov)
        assert prov.calls == 1  # not blocked on the fetch
        assert inv.anchor == HERE  # still the stale disc this tick
        task = store._refreshing.get(sid)
        assert task is not None
        await task  # drain the background refresh
        inv2 = await store.ensure(sid, far, prov)
        assert prov.calls == 2
        assert inv2.anchor == far  # re-centred where we last looked

    asyncio.run(run())


def test_inventory_teleport_outside_disc_blocks_fresh():
    """A resume/teleport OUTSIDE the whole disc can't serve stale data from another
    part of town — that one case still fetches in the foreground."""

    async def run():
        prov = CountingProvider([_place()])
        store = InventoryStore()
        sid = "s"
        await store.ensure(sid, HERE, prov)
        away = GeoPoint(lat=HERE.lat + 0.01, lon=HERE.lon)  # ~1.1 km, outside r=800
        inv = await store.ensure(sid, away, prov)
        assert prov.calls == 2  # blocking fresh fetch
        assert inv.anchor == away

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
        await store.ensure(sid, far, prov)  # stale served, bg refresh kicked
        task = store._refreshing.get(sid)
        assert task is not None
        await task
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
        await store.ensure(sid, far, prov)  # stale served, bg refresh kicked
        task = store._refreshing.get(sid)
        assert task is not None
        await task
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
