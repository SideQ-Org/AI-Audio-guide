"""Durable accounts/history repository — offline tests on in-memory SQLite.

Skipped entirely unless the ``accounts`` extra is installed (SQLAlchemy + aiosqlite),
so the base offline gate (`.[dev,stt]`) stays green either way. When the extra IS
present these run and cover the repository CRUD + cross-user isolation — the same
authz path Postgres RLS enforces in prod.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import event, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.services.accounts import repository as repo  # noqa: E402
from app.services.accounts.models import Base, WalkEvent  # noqa: E402


def _make_engine():
    """One shared in-memory SQLite DB (StaticPool) with FK enforcement on, so the
    ON DELETE CASCADE behaves like Postgres."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # pragma: no cover - trivial pragma hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


async def _setup():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_get_or_create_user_is_idempotent():
    async def run():
        engine, Session = await _setup()
        async with Session() as s:
            u1 = await repo.get_or_create_user(
                s, provider="google", provider_uid="sub-123", email="a@x.com"
            )
            await s.commit()
        async with Session() as s:
            u2 = await repo.get_or_create_user(
                s, provider="google", provider_uid="sub-123"
            )
            await s.commit()
        await engine.dispose()
        return u1.id, u2.id

    a, b = asyncio.run(run())
    assert a == b  # same identity -> same user, no duplicate


def test_user_id_seeded_from_provider():
    async def run():
        engine, Session = await _setup()
        seed = "11111111-1111-1111-1111-111111111111"
        async with Session() as s:
            u = await repo.get_or_create_user(
                s, provider="supabase", provider_uid="sub-9", user_id=seed
            )
            await s.commit()
        await engine.dispose()
        return str(u.id)

    assert asyncio.run(run()) == "11111111-1111-1111-1111-111111111111"


def test_walk_lifecycle_events_and_counts():
    async def run():
        engine, Session = await _setup()
        async with Session() as s:
            u = await repo.get_or_create_user(s, provider="email", provider_uid="e1")
            walk = await repo.start_walk(
                s, user_id=u.id, sid="s" * 20, language="ru", city="Долгопрудный"
            )
            for i in range(3):
                await repo.append_event(
                    s, walk_id=walk.id, place_id=f"p{i}", name=f"Место {i}",
                    category="museum", lat=55.9 + i, lon=37.5, significance="HIGH",
                    narration=f"текст {i}",
                )
            await repo.end_walk(
                s, walk_id=walk.id, distance_m=1200, title="Прогулка"
            )
            await s.commit()
            fetched = await repo.get_walk(s, walk_id=walk.id, user_id=u.id)
            seqs = [e.seq for e in fetched.events]
            data = (
                fetched.object_count, seqs, fetched.distance_m, fetched.title,
                fetched.ended_at is not None,
            )
        await engine.dispose()
        return data

    object_count, seqs, dist, title, ended = asyncio.run(run())
    assert object_count == 3
    assert seqs == [0, 1, 2]  # strictly ordered
    assert dist == 1200 and title == "Прогулка" and ended is True


def test_list_walks_newest_first_and_isolated():
    async def run():
        engine, Session = await _setup()
        base = datetime(2026, 7, 1, tzinfo=UTC)
        async with Session() as s:
            ua = await repo.get_or_create_user(s, provider="email", provider_uid="A")
            ub = await repo.get_or_create_user(s, provider="email", provider_uid="B")
            # three walks for A at increasing times (index i is the newest at i=2)
            created = []
            for i in range(3):
                w = await repo.start_walk(s, user_id=ua.id, sid="a" * 20, language="ru")
                w.started_at = base + timedelta(hours=i)
                created.append(str(w.id))
            wb = await repo.start_walk(s, user_id=ub.id, sid="b" * 20, language="en")
            wb.started_at = base + timedelta(hours=99)
            await s.commit()
            a_walks = await repo.list_walks(s, user_id=ua.id)
            ids = [str(w.id) for w in a_walks]
        await engine.dispose()
        return ids, list(reversed(created))

    ids, expected_newest_first = asyncio.run(run())
    assert len(ids) == 3  # B's walk is not visible to A
    assert ids == expected_newest_first  # newest (i=2) first, oldest (i=0) last


def test_effective_tier_honours_expiry():
    async def run():
        engine, Session = await _setup()
        now = datetime.now(UTC)
        async with Session() as s:
            u = await repo.get_or_create_user(s, provider="email", provider_uid="T")
            free = repo.effective_tier(u)  # default
            await repo.set_subscription(
                s, user_id=u.id, tier="paid", platform="google",
                product="premium_monthly", expires_at=now + timedelta(days=30),
                token="tok",
            )
            paid = repo.effective_tier(u)
            # lapsed subscription silently reverts to free
            await repo.set_subscription(
                s, user_id=u.id, tier="paid", expires_at=now - timedelta(days=1),
            )
            lapsed = repo.effective_tier(u)
            await s.commit()
        await engine.dispose()
        return free, paid, lapsed, repo.effective_tier(None)

    free, paid, lapsed, guest = asyncio.run(run())
    assert free == "free" and paid == "paid" and lapsed == "free" and guest == "free"


def test_walk_counts_and_oldest_eviction():
    async def run():
        engine, Session = await _setup()
        base = datetime(2026, 7, 1, tzinfo=UTC)
        async with Session() as s:
            u = await repo.get_or_create_user(s, provider="email", provider_uid="C")
            for i in range(3):
                w = await repo.start_walk(s, user_id=u.id, sid="c" * 20, language="ru")
                w.started_at = base + timedelta(days=i)  # i=0 oldest
            await s.commit()
            total = await repo.count_walks(s, user_id=u.id)
            # only walks in the last 24h (relative to base+2d) count toward the quota
            recent = await repo.count_walks_since(
                s, user_id=u.id, since=base + timedelta(days=2)
            )
            evicted = await repo.delete_oldest_walk(s, user_id=u.id)
            await s.commit()
            remaining = [str(w.id) for w in await repo.list_walks(s, user_id=u.id)]
            after = await repo.count_walks(s, user_id=u.id)
        await engine.dispose()
        return total, recent, evicted, after, remaining

    total, recent, evicted, after, remaining = asyncio.run(run())
    assert total == 3
    assert recent == 1  # only the newest started within the window
    assert evicted is True and after == 2  # oldest dropped, ring buffer holds


def test_get_and_delete_enforce_ownership_and_cascade():
    async def run():
        engine, Session = await _setup()
        async with Session() as s:
            ua = await repo.get_or_create_user(s, provider="email", provider_uid="A")
            ub = await repo.get_or_create_user(s, provider="email", provider_uid="B")
            walk = await repo.start_walk(s, user_id=ua.id, sid="a" * 20, language="ru")
            await repo.append_event(
                s, walk_id=walk.id, place_id="p", name="X", category="park",
                lat=1.0, lon=2.0, significance="LOW",
            )
            await s.commit()

            # B cannot read or delete A's walk
            not_mine = await repo.get_walk(s, walk_id=walk.id, user_id=ub.id)
            b_del = await repo.delete_walk(s, walk_id=walk.id, user_id=ub.id)
            await s.commit()

            # A deletes it -> walk + its events gone (cascade)
            a_del = await repo.delete_walk(s, walk_id=walk.id, user_id=ua.id)
            await s.commit()
            remaining_events = list(await s.scalars(select(WalkEvent)))
        await engine.dispose()
        return not_mine, b_del, a_del, len(remaining_events)

    not_mine, b_del, a_del, remaining = asyncio.run(run())
    assert not_mine is None  # ownership enforced on read
    assert b_del is False  # non-owner delete is a no-op
    assert a_del is True
    assert remaining == 0  # cascade removed the events
