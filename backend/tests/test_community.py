"""Community API — handles, friendships, feed, challenges (design/COMMUNITY.md).

Drives the ASGI app with httpx over an injected in-memory SQLite engine, same harness as
test_accounts_api.py. Skipped without the ``accounts`` extra.
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


def _token(sub: str) -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "exp": datetime.now(UTC) + timedelta(hours=1)},
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


async def _seed_walk(uid: str, *, distance_m=1000, city="Москва", district="Центр", path=None):
    async with db.session_scope() as s:
        await repo.get_or_create_user(s, provider="supabase", provider_uid=uid, user_id=uid)
        w = await repo.start_walk(
            s, walk_id=uuid.uuid4(), user_id=uid, sid="s" * 20, language="ru",
            city=city, district=district, title="Прогулка",
        )
        w.distance_m = distance_m
        w.object_count = 5
        if path is not None:
            w.path = path


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=main.app), base_url="http://t")


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


def test_handle_and_level():
    async def scenario():
        uid = str(uuid.uuid4())
        await _seed_walk(uid)  # 1 walk → level 1
        async with _client() as c:
            r1 = await c.post("/community/profile", json={"handle": "@Anna"}, headers=_auth(uid))
            r2 = await c.get("/community/me", headers=_auth(uid))
        return r1.json(), r2.json()

    prof, me = _run(scenario)
    assert prof["handle"] == "anna"  # normalized (lowercased, @ stripped)
    assert me["level"] == 1 and me["walk_count"] == 1


def test_handle_conflict():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        async with _client() as c:
            await c.post("/community/profile", json={"handle": "shared"}, headers=_auth(a))
            r = await c.post("/community/profile", json={"handle": "shared"}, headers=_auth(b))
        return r.status_code

    assert _run(scenario) == 409


def test_friend_request_accept_and_list():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        async with _client() as c:
            await c.post("/community/profile", json={"handle": "anna"}, headers=_auth(a))
            await c.post("/community/profile", json={"handle": "boris"}, headers=_auth(b))
            # A requests B by handle
            req = await c.post(
                "/community/friends/request", json={"handle": "boris"}, headers=_auth(a)
            )
            # B sees an incoming request
            reqs = await c.get("/community/friends/requests", headers=_auth(b))
            # B accepts A
            acc = await c.post(f"/community/friends/{a}/accept", headers=_auth(b))
            # both now list each other
            fa = await c.get("/community/friends", headers=_auth(a))
            fb = await c.get("/community/friends", headers=_auth(b))
        return req.json(), reqs.json(), acc.status_code, fa.json(), fb.json()

    req, reqs, acc, fa, fb = _run(scenario)
    assert req["status"] == "pending"
    assert len(reqs["incoming"]) == 1 and reqs["incoming"][0]["handle"] == "anna"
    assert acc == 200
    assert [f["handle"] for f in fa["friends"]] == ["boris"]
    assert [f["handle"] for f in fb["friends"]] == ["anna"]


def test_reciprocal_request_auto_accepts():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        async with _client() as c:
            await c.post("/community/profile", json={"handle": "anna"}, headers=_auth(a))
            await c.post("/community/profile", json={"handle": "boris"}, headers=_auth(b))
            await c.post(
                "/community/friends/request", json={"handle": "boris"}, headers=_auth(a)
            )
            # B requests A back → should auto-accept
            r = await c.post(
                "/community/friends/request", json={"handle": "anna"}, headers=_auth(b)
            )
            fa = await c.get("/community/friends", headers=_auth(a))
        return r.json(), fa.json()

    r, fa = _run(scenario)
    assert r["status"] == "accepted"
    assert len(fa["friends"]) == 1


def test_challenge_leaderboard_orders_by_progress():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        async with _client() as c:
            await c.post("/community/profile", json={"handle": "anna"}, headers=_auth(a))
            await c.post("/community/profile", json={"handle": "boris"}, headers=_auth(b))
            made = await c.post(
                "/community/challenges",
                json={"title": "10к", "metric": "distance", "goal": 10000, "days": 7},
                headers=_auth(a),
            )
            cid = made.json()["id"]
            await c.post(f"/community/challenges/{cid}/join", headers=_auth(b))
            # Walks must fall inside the challenge window → seed AFTER it starts.
            await _seed_walk(a, distance_m=2000)
            await _seed_walk(b, distance_m=8000)
            det = await c.get(f"/community/challenges/{cid}", headers=_auth(a))
        return det.json()

    det = _run(scenario)
    board = det["leaderboard"]
    assert [e["user"]["handle"] for e in board] == ["boris", "anna"]  # 8000 > 2000
    assert board[0]["progress"] == 8000 and board[1]["progress"] == 2000


def test_feed_shows_friend_activity():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        async with _client() as c:
            await c.post("/community/profile", json={"handle": "anna"}, headers=_auth(a))
            await c.post("/community/profile", json={"handle": "boris"}, headers=_auth(b))
            await c.post("/community/friends/request", json={"handle": "boris"}, headers=_auth(a))
            await c.post(f"/community/friends/{a}/accept", headers=_auth(b))
            # B creates a challenge → records a challenge_join activity
            await c.post(
                "/community/challenges",
                json={"title": "прогулка", "metric": "places", "goal": 10},
                headers=_auth(b),
            )
            feed = await c.get("/community/feed", headers=_auth(a))
        return feed.json()

    feed = _run(scenario)
    kinds = [(i["kind"], i["user"]["handle"]) for i in feed["items"]]
    assert ("challenge_join", "boris") in kinds


def test_weekly_challenge_autocreated():
    async def scenario():
        uid = str(uuid.uuid4())
        async with _client() as c:
            r = await c.get("/community/challenges", headers=_auth(uid))
        return r.json()

    data = _run(scenario)
    globals_ = [c for c in data["challenges"] if c["scope"] == "global"]
    assert globals_ and globals_[0]["creator_id"] is None


def test_my_walks_includes_path():
    async def scenario():
        uid = str(uuid.uuid4())
        await _seed_walk(uid, path=[[55.75, 37.62], [55.76, 37.63]])
        async with _client() as c:
            r = await c.get("/community/my/walks", headers=_auth(uid))
        return r.json()

    data = _run(scenario)
    assert len(data["walks"]) == 1
    assert data["walks"][0]["path"] == [[55.75, 37.62], [55.76, 37.63]]
    assert data["walks"][0]["city"] == "Москва"


def test_group_streak_created_and_valued():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        # both walked today → common day → streak >= 1
        await _seed_walk(a)
        await _seed_walk(b)
        async with _client() as c:
            await c.post("/community/profile", json={"handle": "anna"}, headers=_auth(a))
            await c.post("/community/profile", json={"handle": "boris"}, headers=_auth(b))
            # must be friends first
            await c.post("/community/friends/request", json={"handle": "boris"}, headers=_auth(a))
            await c.post(f"/community/friends/{a}/accept", headers=_auth(b))
            made = await c.post(
                "/community/streaks",
                json={"handles": ["boris"], "title": "Вместе"},
                headers=_auth(a),
            )
            listed = await c.get("/community/streaks", headers=_auth(b))
        return made.json(), listed.json()

    made, listed = _run(scenario)
    assert made["days"] >= 1
    assert sorted(m["handle"] for m in made["members"]) == ["anna", "boris"]
    # b (the friend) also sees the shared streak
    assert len(listed["streaks"]) == 1 and listed["streaks"][0]["days"] >= 1


def test_group_streak_only_adds_friends():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        async with _client() as c:
            await c.post("/community/profile", json={"handle": "anna"}, headers=_auth(a))
            await c.post("/community/profile", json={"handle": "boris"}, headers=_auth(b))
            # NOT friends → boris must be ignored, streak has only the creator
            made = await c.post(
                "/community/streaks", json={"handles": ["boris"]}, headers=_auth(a)
            )
        return made.json()

    made = _run(scenario)
    assert [m["handle"] for m in made["members"]] == ["anna"]


async def _seed_walk_with_event(uid: str) -> str:
    async with db.session_scope() as s:
        await repo.get_or_create_user(s, provider="supabase", provider_uid=uid, user_id=uid)
        wid = uuid.uuid4()
        w = await repo.start_walk(
            s, walk_id=wid, user_id=uid, sid="s" * 20, language="ru",
            city="Москва", title="Прогулка",
        )
        w.path = [[55.75, 37.62], [55.76, 37.63]]
        await repo.append_event(
            s, walk_id=wid, place_id="p1", name="Кремль", category="landmark",
            lat=55.75, lon=37.62, significance="LANDMARK", narration="Это Кремль.",
        )
        return str(wid)


def test_share_walk_and_friend_can_view():
    async def scenario():
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        wid = await _seed_walk_with_event(a)
        async with _client() as c:
            await c.post("/community/profile", json={"handle": "anna"}, headers=_auth(a))
            await c.post("/community/profile", json={"handle": "boris"}, headers=_auth(b))
            await c.post("/community/friends/request", json={"handle": "boris"}, headers=_auth(a))
            await c.post(f"/community/friends/{a}/accept", headers=_auth(b))
            fw_before = await c.get("/community/friends/walks", headers=_auth(b))
            view_before = await c.get(f"/community/walks/{wid}", headers=_auth(b))
            sh = await c.post(f"/community/walks/{wid}/share", headers=_auth(a))
            fw_after = await c.get("/community/friends/walks", headers=_auth(b))
            view_after = await c.get(f"/community/walks/{wid}", headers=_auth(b))
        return (fw_before.json(), view_before.status_code, sh.json(),
                fw_after.json(), view_after.status_code, view_after.json())

    fw_before, vb, sh, fw_after, va, detail = _run(scenario)
    assert fw_before["walks"] == []          # not shared → invisible
    assert vb == 404                          # friend can't view an unshared walk
    assert sh["shared"] is True
    assert len(fw_after["walks"]) == 1        # now visible
    assert va == 200
    assert detail["path"] == [[55.75, 37.62], [55.76, 37.63]]
    assert detail["events"][0]["name"] == "Кремль"
    assert detail["events"][0]["narration"] == "Это Кремль."


def test_owner_views_own_walk():
    async def scenario():
        a = str(uuid.uuid4())
        wid = await _seed_walk_with_event(a)
        async with _client() as c:
            r = await c.get(f"/community/walks/{wid}", headers=_auth(a))
        return r.status_code, r.json()

    code, d = _run(scenario)
    assert code == 200 and len(d["events"]) == 1
