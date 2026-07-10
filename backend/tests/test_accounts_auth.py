"""Phase 3 — WS auth: Supabase JWT verification + sid↔user_id binding.

The unit tests need ``pyjwt`` (the ``accounts`` extra) and are skipped without it, so
the base offline gate stays green. The guest path (no/invalid token) is exercised too —
that is the invariant we must never break.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.services.accounts.auth import verify_token
from app.services.agent.factory import build_orchestrator
from app.services.stt.stt import build_stt

jwt = pytest.importorskip("jwt")  # pyjwt, from the `accounts` extra

_SECRET = "test-hs256-secret-at-least-32-bytes-long"


@contextlib.contextmanager
def _hs256_auth():
    """Enable HS256 verification for the duration of a test, then restore."""
    prev_secret = settings.supabase_jwt_secret
    prev_jwks = settings.supabase_jwks_url
    settings.supabase_jwt_secret = _SECRET
    settings.supabase_jwks_url = ""
    try:
        yield
    finally:
        settings.supabase_jwt_secret = prev_secret
        settings.supabase_jwks_url = prev_jwks


def _token(sub="user-abc", *, aud="authenticated", exp_delta=timedelta(hours=1),
           secret=_SECRET, drop_sub=False):
    claims = {"aud": aud, "exp": datetime.now(UTC) + exp_delta}
    if not drop_sub:
        claims["sub"] = sub
    return jwt.encode(claims, secret, algorithm="HS256")


# -- verify_token unit tests ---------------------------------------------- #


def test_disabled_returns_none():
    # no secret/jwks configured => auth off => guest, even with a "valid" token.
    # Explicitly clear both so the test is hermetic regardless of the ambient .env.
    prev_j, prev_s = settings.supabase_jwks_url, settings.supabase_jwt_secret
    settings.supabase_jwks_url = ""
    settings.supabase_jwt_secret = ""
    try:
        assert verify_token(_token()) is None
    finally:
        settings.supabase_jwks_url = prev_j
        settings.supabase_jwt_secret = prev_s


def test_valid_token_returns_sub():
    with _hs256_auth():
        assert verify_token(_token(sub="abc-123")) == "abc-123"


def test_expired_token_rejected():
    with _hs256_auth():
        assert verify_token(_token(exp_delta=timedelta(hours=-1))) is None


def test_wrong_secret_rejected():
    with _hs256_auth():
        assert verify_token(_token(secret="a-different-secret-also-32-bytes-long!!")) is None


def test_wrong_audience_rejected():
    with _hs256_auth():
        assert verify_token(_token(aud="some-other-service")) is None


def test_missing_sub_rejected():
    with _hs256_auth():
        assert verify_token(_token(drop_sub=True)) is None


def test_empty_token_returns_none():
    with _hs256_auth():
        assert verify_token("") is None


def test_es256_jwks_token_accepted(monkeypatch):
    """The asymmetric path a real Supabase project uses (ES256 + JWKS). Signs a token
    with a local EC key and stubs the JWKS client to return its public key."""
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives.asymmetric import ec

    import app.services.accounts.auth as authmod

    priv = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode(
        {"sub": "uid-es256", "aud": "authenticated", "exp": datetime.now(UTC) + timedelta(hours=1)},
        priv,
        algorithm="ES256",
    )

    class _Key:
        key = priv.public_key()

    class _FakeJwksClient:
        def get_signing_key_from_jwt(self, _token):
            return _Key()

    monkeypatch.setattr(authmod, "_jwks_client", lambda _url: _FakeJwksClient())
    monkeypatch.setattr(settings, "supabase_jwks_url", "https://example/jwks")
    monkeypatch.setattr(settings, "supabase_jwt_secret", "")
    assert authmod.verify_token(token) == "uid-es256"


# -- WS auth flow (sid ↔ user_id) ----------------------------------------- #


def _heuristic_app():
    settings.agent_backend = "heuristic"
    settings.geo_source = "fixture"
    settings.stt_backend = "mock"
    main_module._orchestrator = build_orchestrator()
    main_module._stt = build_stt()
    return TestClient(main_module.app)


def test_ws_valid_token_binds_user_id():
    client = _heuristic_app()
    orch = main_module._orchestrator
    sid = "authsession12345"
    with _hs256_auth():
        with client.websocket_connect(f"/ws?sid={sid}") as ws:
            ws.send_json({"type": "auth", "token": _token(sub="supabase-uid-1")})
            reply = ws.receive_json()
            # Reply now carries entitlements (feature: account tiers); DB off in this
            # test => free tier defaults.
            assert reply["type"] == "auth"
            assert reply["authenticated"] is True
            assert reply["tier"] == "free"
    state = asyncio.run(orch.store.load(sid))
    assert state.user_id == "supabase-uid-1"  # bound into the resumable session


def test_ws_invalid_token_degrades_to_guest():
    client = _heuristic_app()
    orch = main_module._orchestrator
    sid = "guestsession1234"
    with _hs256_auth():
        with client.websocket_connect(f"/ws?sid={sid}") as ws:
            ws.send_json({"type": "auth", "token": "garbage.not.a.jwt"})
            reply = ws.receive_json()
            assert reply["type"] == "auth"
            assert reply["authenticated"] is False
            assert reply["tier"] == "free"  # invalid token => guest, free tier
    state = asyncio.run(orch.store.load(sid))
    assert state.user_id is None  # invalid token => guest, socket not refused


def test_ws_no_auth_message_stays_guest():
    # the current MVP path: a client that never sends `auth` is a guest and still works
    client = _heuristic_app()
    orch = main_module._orchestrator
    sid = "noauthsession123"
    with client.websocket_connect(f"/ws?sid={sid}") as ws:
        ws.send_json(
            {"type": "position", "lat": 55.7525, "lon": 37.6231, "gaze_confidence": "low"}
        )
        # narration still flows without any auth
        got_narration = False
        for _ in range(4):
            msg = ws.receive_json()
            if msg["type"] == "narration":
                got_narration = True
                break
        assert got_narration
    state = asyncio.run(orch.store.load(sid))
    assert state.user_id is None
