"""Supabase JWT verification for WS auth (design §6/§9a).

Contract: ``verify_token(token) -> user_id | None``. It NEVER raises into the
connection path — any problem (bad signature, expired, wrong audience, misconfig,
``pyjwt`` not installed) returns ``None`` so the session simply degrades to a guest.

Two verification paths, tried in order:
  1. JWKS (asymmetric RS256/ES256) — the recommended path; the project's public keys
     are fetched once and cached by ``PyJWKClient``, then signatures verify locally.
  2. legacy HS256 — a shared project secret held on the backend.

Auth is OFF unless one of those is configured, in which case ``verify_token`` short-
circuits to ``None`` without importing ``pyjwt`` — so the base install (no ``accounts``
extra) is unaffected and everyone is a guest, exactly as today.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings

_log = logging.getLogger("aiguide.auth")


def auth_enabled() -> bool:
    """True when at least one JWT verification path is configured."""
    return bool(settings.supabase_jwks_url or settings.supabase_jwt_secret)


@lru_cache(maxsize=2)
def _jwks_client(url: str):
    import jwt  # local: only needed when JWKS is configured

    return jwt.PyJWKClient(url)


def verify_token(token: str) -> str | None:
    """Return the Supabase user id (JWT ``sub``) for a valid token, else ``None``.

    Synchronous (JWKS may do a one-time cached network fetch) — call it off the event
    loop, e.g. ``await asyncio.to_thread(verify_token, token)``.
    """
    if not token or not auth_enabled():
        return None
    try:
        import jwt
    except Exception:  # noqa: BLE001 — extra not installed; treat as guest
        _log.warning(
            "auth configured but pyjwt is missing — install the 'accounts' extra"
        )
        return None

    aud = settings.supabase_jwt_aud or None
    options = {"require": ["exp", "sub"]}

    if settings.supabase_jwks_url:
        try:
            signing_key = _jwks_client(
                settings.supabase_jwks_url
            ).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=aud,
                options=options,
            )
            return claims.get("sub")
        except Exception as e:  # noqa: BLE001 — fall through to HS256 / guest
            _log.info("JWKS verify failed: %s", e)

    if settings.supabase_jwt_secret:
        try:
            claims = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=aud,
                options=options,
            )
            return claims.get("sub")
        except Exception as e:  # noqa: BLE001 — invalid => guest
            _log.info("HS256 verify failed: %s", e)

    return None
