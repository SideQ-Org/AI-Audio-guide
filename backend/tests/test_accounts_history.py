"""Phase 4 — fire-and-forget walk-history writes (design §5).

Direct tests of the history module against in-memory SQLite, plus one integration test
that drives the real orchestrator (fixture geo) to prove the narrate-point hook fires.
Skipped without the ``accounts`` extra, so the base offline gate stays green.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import event, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.accounts import db, history  # noqa: E402
from app.services.accounts.models import Base, Walk, WalkEvent  # noqa: E402
from app.shared.schemas import (  # noqa: E402
    Address,
    GeoPoint,
    Heading,
    Pace,
    Place,
    SessionState,
    Significance,
)


def _make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


async def _init_db():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db._set_engine_for_tests(engine)
    return engine


async def _drain():
    """Await the detached history write tasks so assertions see committed rows."""
    if history._tasks:
        await asyncio.gather(*list(history._tasks))


def _state(user_id: str | None) -> SessionState:
    return SessionState(
        session_id="s" * 20,
        user_id=user_id,
        language="ru",
        address=Address(city="Долгопрудный", district="Центр"),
    )


def _place(pid: str = "p1") -> Place:
    return Place(id=pid, name="Место", category="museum", location=GeoPoint(lat=55.9, lon=37.5))


async def _count(model) -> int:
    async with db.get_sessionmaker()() as s:
        return await s.scalar(select(func.count()).select_from(model))


def test_logged_in_writes_walk_and_events():
    async def run():
        await _init_db()
        settings.database_url = "sqlite+aiosqlite://"
        try:
            uid = str(uuid.uuid4())
            st = _state(uid)
            history.record_object(st, _place("p1"), Significance.HIGH, "текст один")
            await _drain()
            history.record_object(st, _place("p2"), Significance.MEDIUM, "текст два")
            await _drain()
            async with db.get_sessionmaker()() as s:
                walk = (await s.scalars(select(Walk))).one()
                nev = await s.scalar(select(func.count()).select_from(WalkEvent))
                data = (
                    nev, walk.object_count, walk.city, walk.title,
                    str(st.walk_id) == str(walk.id), walk.ended_at is not None,
                )
        finally:
            await db.dispose_engine()
            settings.database_url = ""
        return data

    nev, obj_count, city, title, same_id, ended = asyncio.run(run())
    assert nev == 2  # both objects appended to ONE walk
    assert obj_count == 2
    assert city == "Долгопрудный"
    assert title == "Прогулка по Долгопрудный"
    assert same_id  # SessionState.walk_id points at the persisted walk
    assert ended  # ended_at trails the last event


def test_walk_path_is_persisted_and_survives_events():
    async def run():
        await _init_db()
        settings.database_url = "sqlite+aiosqlite://"
        try:
            st = _state(str(uuid.uuid4()))
            st.path = [[55.90, 37.50], [55.901, 37.501]]
            history.record_object(st, _place("p1"), Significance.HIGH, "a")
            await _drain()
            st.path.append([55.902, 37.502])  # more walking before the next object
            history.record_object(st, _place("p2"), Significance.HIGH, "b")
            await _drain()
            async with db.get_sessionmaker()() as s:
                walk = (await s.scalars(select(Walk))).one()
                return walk.path
        finally:
            await db.dispose_engine()
            settings.database_url = ""

    path = asyncio.run(run())
    # first walk keeps the FULL trail (no reset on the first walk), latest snapshot wins
    assert path == [[55.90, 37.50], [55.901, 37.501], [55.902, 37.502]]


def test_guest_writes_nothing():
    async def run():
        await _init_db()
        settings.database_url = "sqlite+aiosqlite://"
        try:
            st = _state(None)  # guest
            history.record_object(st, _place(), Significance.HIGH, "x")
            await _drain()
            n = await _count(Walk)
            return n, st.walk_id
        finally:
            await db.dispose_engine()
            settings.database_url = ""

    n_walks, walk_id = asyncio.run(run())
    assert n_walks == 0  # nothing written for a guest
    assert walk_id is None


def test_disabled_store_is_noop():
    async def run():
        await _init_db()
        settings.database_url = ""  # durable store OFF even though a user is present
        try:
            st = _state(str(uuid.uuid4()))
            history.record_object(st, _place(), Significance.HIGH, "x")
            await _drain()
            return st.walk_id
        finally:
            await db.dispose_engine()

    assert asyncio.run(run()) is None  # no walk_id stamped, no task scheduled


def test_long_gap_starts_a_new_walk():
    async def run():
        await _init_db()
        settings.database_url = "sqlite+aiosqlite://"
        try:
            st = _state(str(uuid.uuid4()))
            history.record_object(st, _place("p1"), Significance.HIGH, "a")
            await _drain()
            first = st.walk_id
            # simulate a pause longer than walk_gap_s -> the next object opens a new walk
            st.walk_last_event_at = time.time() - (settings.walk_gap_s + 60)
            history.record_object(st, _place("p2"), Significance.HIGH, "b")
            await _drain()
            second = st.walk_id
            n = await _count(Walk)
            return first, second, n
        finally:
            await db.dispose_engine()
            settings.database_url = ""

    first, second, n_walks = asyncio.run(run())
    assert first != second  # gap rotated the walk
    assert n_walks == 2


async def _make_n_walks(uid: str, tier: str, n: int):
    """Force ``n`` separate walks for one user by rotating past the gap each time,
    draining after each so the detached writes commit serially (shared SQLite conn)."""
    st = _state(uid)
    st.tier = tier
    for i in range(n):
        st.walk_last_event_at = time.time() - (settings.walk_gap_s + 60)  # force a new walk
        history.record_object(st, _place(f"p{i}"), Significance.HIGH, f"t{i}")
        await _drain()


def test_free_tier_ring_buffers_saved_walks():
    async def run():
        await _init_db()
        settings.database_url = "sqlite+aiosqlite://"
        prev = settings.free_tier_walk_limit
        settings.free_tier_walk_limit = 3
        try:
            await _make_n_walks(str(uuid.uuid4()), "free", 5)
            return await _count(Walk)
        finally:
            settings.free_tier_walk_limit = prev
            await db.dispose_engine()
            settings.database_url = ""

    assert asyncio.run(run()) == 3  # oldest evicted; only the newest 3 kept


def test_paid_tier_keeps_all_walks():
    async def run():
        await _init_db()
        settings.database_url = "sqlite+aiosqlite://"
        prev = settings.free_tier_walk_limit
        settings.free_tier_walk_limit = 3
        try:
            await _make_n_walks(str(uuid.uuid4()), "paid", 5)
            return await _count(Walk)
        finally:
            settings.free_tier_walk_limit = prev
            await db.dispose_engine()
            settings.database_url = ""

    assert asyncio.run(run()) == 5  # paid = unlimited, no eviction


def test_orchestrator_hook_persists_narrated_object():
    """Integration: a logged-in session narrating a fixture object writes history via
    the orchestrator's narrate-point hook (guest path is covered by the WS tests)."""

    async def run():
        await _init_db()
        settings.database_url = "sqlite+aiosqlite://"
        settings.agent_backend = "heuristic"
        settings.geo_source = "fixture"
        try:
            from app.services.agent.factory import build_orchestrator

            orch = build_orchestrator()
            sid = "histsession1234"
            st = await orch.store.load(sid)
            st.user_id = str(uuid.uuid4())
            await orch.store.save(st)
            nev = 0
            for _ in range(6):  # a couple of ticks to reach the object narration
                await orch.on_position(
                    sid, GeoPoint(lat=55.7525, lon=37.6231), Heading(), Pace.SLOW
                )
                await _drain()
                nev = await _count(WalkEvent)
                if nev >= 1:
                    break
            return nev
        finally:
            await db.dispose_engine()
            settings.database_url = ""

    assert asyncio.run(run()) >= 1  # the narrated object was persisted
