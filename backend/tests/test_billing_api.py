"""Billing: POST /billing/google/verify grants the paid tier (feature: account tiers).

The store call is mocked (no network/credentials): we patch verify_google_subscription
and assert the endpoint flips the account to paid and that GET /me then reflects it.
Skipped without the ``accounts`` extra.
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
from app.services.accounts.models import Base  # noqa: E402
from app.services.billing import verify as billing_verify  # noqa: E402

_SECRET = "test-hs256-secret-at-least-32-bytes-long"


def _auth(sub: str) -> dict:
    tok = jwt.encode(
        {"sub": sub, "aud": "authenticated", "exp": datetime.now(UTC) + timedelta(hours=1)},
        _SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


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


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=main.app), base_url="http://t")


def _run(scenario):
    async def wrapped():
        engine = _make_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        db._set_engine_for_tests(engine)
        settings.database_url = "sqlite+aiosqlite://"
        settings.supabase_jwt_secret = _SECRET
        settings.supabase_jwks_url = ""
        settings.google_play_package = "com.example.aiguide"
        settings.google_service_account_json = "/does/not/matter/mocked.json"
        try:
            return await scenario()
        finally:
            await db.dispose_engine()
            settings.database_url = ""
            settings.supabase_jwt_secret = ""
            settings.google_play_package = ""
            settings.google_service_account_json = ""

    return asyncio.run(wrapped())


def _fake_active(**_):
    async def _f(product_id, purchase_token):
        return billing_verify.SubResult(
            active=True,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            raw={"paymentState": 1},
        )
    return _f


def _fake_inactive(**_):
    async def _f(product_id, purchase_token):
        return billing_verify.SubResult(active=False, expires_at=None, raw={})
    return _f


def test_verify_grants_paid_tier(monkeypatch):
    monkeypatch.setattr(billing_verify, "verify_google_subscription", _fake_active())

    async def scenario():
        uid = str(uuid.uuid4())
        async with _client() as c:
            r = await c.post(
                "/billing/google/verify",
                headers=_auth(uid),
                json={"purchase_token": "tok-123", "product_id": "premium_monthly"},
            )
            me = await c.get("/me", headers=_auth(uid))
        return r.status_code, r.json(), me.json()

    status, body, me = _run(scenario)
    assert status == 200
    assert body["tier"] == "paid"
    assert body["daily_tour_limit"] is None and body["walk_limit"] is None  # unlimited
    assert me["tier"] == "paid"  # persisted — GET /me agrees


def test_verify_rejects_unknown_product(monkeypatch):
    monkeypatch.setattr(billing_verify, "verify_google_subscription", _fake_active())

    async def scenario():
        async with _client() as c:
            r = await c.post(
                "/billing/google/verify",
                headers=_auth(str(uuid.uuid4())),
                json={"purchase_token": "t", "product_id": "not_a_product"},
            )
        return r.status_code

    assert _run(scenario) == 400


def test_inactive_purchase_stays_free(monkeypatch):
    monkeypatch.setattr(billing_verify, "verify_google_subscription", _fake_inactive())

    async def scenario():
        uid = str(uuid.uuid4())
        async with _client() as c:
            r = await c.post(
                "/billing/google/verify",
                headers=_auth(uid),
                json={"purchase_token": "t", "product_id": "premium_yearly"},
            )
        return r.status_code, r.json()

    status, body = _run(scenario)
    assert status == 200
    assert body["tier"] == "free"  # inactive receipt does not grant paid
