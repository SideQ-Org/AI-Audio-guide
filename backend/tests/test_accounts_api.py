"""Phase 5 — REST /me + /walks under auth (design §7).

Drives the ASGI app with httpx in a single event loop (so the seeding and the request
handlers share the injected in-memory engine). Skipped without the ``accounts`` extra.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")
jwt = pytest.importorskip("jwt")

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import app.main as main  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.accounts import db  # noqa: E402
from app.services.accounts import repository as repo  # noqa: E402
from app.services.accounts.models import Base  # noqa: E402

_SECRET = "test-hs256-secret-at-least-32-bytes-long"


def _token(sub: str, *, aud="authenticated", exp=timedelta(hours=1)) -> str:
    return jwt.encode(
        {"sub": sub, "aud": aud, "exp": datetime.now(UTC) + exp},
        _SECRET,
        algorithm="HS256",
    )


def _auth(sub: str) -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


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


async def _seed_walk(uid: str, *, city="Москва", n_events=2, email=None) -> str:
    async with db.session_scope() as s:
        await repo.get_or_create_user(
            s, provider="supabase", provider_uid=uid, user_id=uid, email=email
        )
        w = await repo.start_walk(
            s, walk_id=uuid.uuid4(), user_id=uid, sid="s" * 20,
            language="ru", city=city, title="Прогулка",
        )
        for i in range(n_events):
            await repo.append_event(
                s, walk_id=w.id, place_id=f"p{i}", name=f"N{i}", category="museum",
                lat=1.0 + i, lon=2.0, significance="HIGH", narration=f"t{i}",
            )
        return str(w.id)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=main.app), base_url="http://t"
    )


def _run(scenario):
    async def wrapped():
        await _init_db()
        settings.database_url = "sqlite+aiosqlite://"
        settings.supabase_jwt_secret = _SECRET
        settings.supabase_jwks_url = ""
        try:
            return await scenario()
        finally:
            await db.dispose_engine()
            settings.database_url = ""
            settings.supabase_jwt_secret = ""

    return asyncio.run(wrapped())


def test_me_returns_profile():
    async def scenario():
        uid = str(uuid.uuid4())
        await _seed_walk(uid, email="me@example.com")
        async with _client() as c:
            r = await c.get("/me", headers=_auth(uid))
        return r.status_code, r.json()

    status, body = _run(scenario)
    assert status == 200
    assert body["id"] and body["email"] == "me@example.com"


def test_walks_list_is_isolated_per_user():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        await _seed_walk(a, city="Москва")
        await _seed_walk(a, city="Казань")
        await _seed_walk(b, city="Сочи")
        async with _client() as c:
            r = await c.get("/walks", headers=_auth(a))
        return r.status_code, r.json()

    status, body = _run(scenario)
    assert status == 200
    assert len(body["walks"]) == 2  # only A's walks, not B's
    assert {w["city"] for w in body["walks"]} == {"Москва", "Казань"}


def test_walk_detail_has_events_and_enforces_ownership():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        wid = await _seed_walk(a, n_events=3)
        async with _client() as c:
            mine = await c.get(f"/walks/{wid}", headers=_auth(a))
            others = await c.get(f"/walks/{wid}", headers=_auth(b))
        return mine.status_code, mine.json(), others.status_code

    status, body, other_status = _run(scenario)
    assert status == 200
    assert len(body["events"]) == 3
    assert [e["seq"] for e in body["events"]] == [0, 1, 2]  # ordered
    assert other_status == 404  # B cannot read A's walk


def test_delete_walk_removes_and_blocks_non_owner():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        wid = await _seed_walk(a)
        async with _client() as c:
            not_owner = await c.delete(f"/walks/{wid}", headers=_auth(b))
            owner = await c.delete(f"/walks/{wid}", headers=_auth(a))
            after = await c.get(f"/walks/{wid}", headers=_auth(a))
        return not_owner.status_code, owner.status_code, after.status_code

    non_owner, owner, after = _run(scenario)
    assert non_owner == 404  # B can't delete A's walk
    assert owner == 204  # A deletes it
    assert after == 404  # gone


def test_delete_me_wipes_account_data():
    async def scenario():
        uid = str(uuid.uuid4())
        await _seed_walk(uid, n_events=2)
        await _seed_walk(uid, n_events=1)
        async with _client() as c:
            before = await c.get("/walks", headers=_auth(uid))
            deleted = await c.delete("/me", headers=_auth(uid))
            after = await c.get("/walks", headers=_auth(uid))
            me = await c.get("/me", headers=_auth(uid))
        return (
            len(before.json()["walks"]), deleted.status_code,
            after.status_code, after.json()["walks"], me.status_code, me.json(),
        )

    n_before, del_status, after_status, after_walks, me_status, me_body = _run(scenario)
    assert n_before == 2
    assert del_status == 204
    assert after_status == 200 and after_walks == []  # all walks gone
    # token still valid -> /me falls back to the JWT identity (user row is gone)
    assert me_status == 200 and me_body["id"] and me_body["email"] is None


def test_unauthorized_without_or_with_bad_token():
    async def scenario():
        async with _client() as c:
            none = await c.get("/walks")
            bad = await c.get("/walks", headers={"Authorization": "Bearer garbage"})
        return none.status_code, bad.status_code

    no_token, bad_token = _run(scenario)
    assert no_token == 401
    assert bad_token == 401
