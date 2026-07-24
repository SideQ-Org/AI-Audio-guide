"""Этап 1 — bounded in-memory session store (no leak, evicts on disconnect/idle)."""

import asyncio
import time

from app.config import settings
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import SessionState


def test_delete_removes_session():
    async def run() -> bool:
        s = InMemoryStateStore()
        await s.save(SessionState(session_id="a"))
        assert "a" in s._data
        await s.delete("a")
        return "a" in s._data

    assert asyncio.run(run()) is False


def test_lru_cap_evicts_oldest():
    saved = settings.max_sessions
    settings.max_sessions = 2
    try:
        async def run() -> tuple[int, bool]:
            s = InMemoryStateStore()
            await s.save(SessionState(session_id="a"))
            await s.save(SessionState(session_id="b"))
            await s.save(SessionState(session_id="c"))  # over cap -> evict LRU ("a")
            return len(s._data), ("a" in s._data)

        n, has_a = asyncio.run(run())
        assert n == 2
        assert has_a is False
    finally:
        settings.max_sessions = saved


def test_ttl_evicts_idle():
    saved = settings.session_ttl_s
    settings.session_ttl_s = 0.05
    try:
        async def run() -> bool:
            s = InMemoryStateStore()
            await s.save(SessionState(session_id="a"))
            time.sleep(0.1)  # let "a" go idle past the TTL
            await s.load("b")  # any load triggers _evict_expired
            return "a" in s._data

        assert asyncio.run(run()) is False
    finally:
        settings.session_ttl_s = saved


def test_session_state_json_roundtrip_full():
    """The Redis store serializes the WHOLE SessionState as JSON — a populated state
    (guided nav, memory graph, paused-path breadcrumbs, cooldown ledger) must survive
    the round-trip intact, or prod sessions silently lose fields on reconnect."""
    from app.shared.memory import ObjectMemo
    from app.shared.schemas import (
        Address,
        GeoPoint,
        NavManeuver,
        NavStop,
        Place,
        RouteScript,
        StopBeat,
    )

    st = SessionState(session_id="rt1")
    st.address = Address(country="Россия", city="Москва", district="Останкинский")
    st.position = GeoPoint(lat=55.82, lon=37.64)
    st.path = [[55.82, 37.64], [55.821, 37.641, 1.0]]  # incl. a paused breadcrumb
    st.seen_place_ids = ["node/1", "way/2"]
    st.reach_exhausted_ids = ["node/3|0", "way/4|1"]
    st.last_cat_told = {"museum": 123.0}
    st.last_place = Place(
        id="node/1", name="Музей", category="museum", location=GeoPoint(lat=55.82, lon=37.64)
    )
    st.memory.record_narration("Справа от тебя музей с богатой историей.")
    st.memory.record_object_node(
        ObjectMemo(id="node/1", name="Музей", category="museum", lat=55.82, lon=37.64)
    )
    st.memory.mark_facts_told(["Музей открыли в тридцатых годах прошлого века."])
    st.nav.active = True
    st.nav.accepted = True
    st.nav.stops = [
        NavStop(place_id="node/9", name="Стоп", lat=55.83, lon=37.65, order=0, min_dist_m=88.0)
    ]
    st.nav.steps = [NavManeuver(kind="turn", modifier="left", name="Улица", lat=55.8, lon=37.6)]
    st.nav.script = RouteScript(theme="тема", intro="интро", beats=[StopBeat(order=0)])
    st.nav.last_passby_at = 456.0

    raw = st.model_dump_json()
    back = SessionState.model_validate_json(raw)
    assert back.session_id == "rt1"
    assert back.path == st.path
    assert back.reach_exhausted_ids == st.reach_exhausted_ids
    assert back.last_cat_told == st.last_cat_told
    assert back.memory.told_facts == st.memory.told_facts
    assert back.memory.objects[0].name == "Музей"
    assert back.nav.stops[0].min_dist_m == 88.0
    assert back.nav.script is not None and back.nav.script.theme == "тема"
    assert back.nav.last_passby_at == 456.0
