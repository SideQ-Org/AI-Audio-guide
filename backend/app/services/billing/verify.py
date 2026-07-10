"""Google Play (and, stubbed, Apple) subscription receipt verification.

Verifies a client-supplied purchase token against the Play Developer API using a
service-account key, returning whether the subscription is active and when it expires.
``google-auth`` is imported lazily (optional dep in the ``accounts`` extra) so the base
install still starts; if it's missing / unconfigured, ``billing_enabled()`` is False and
the API answers 503.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.config import settings

_log = logging.getLogger("aiguide.billing")

_ANDROID_SCOPE = "https://www.googleapis.com/auth/androidpublisher"


def billing_enabled() -> bool:
    """True when Google Play verification is configured (package + service-account key)."""
    return bool(settings.google_play_package and settings.google_service_account_json)


@dataclass
class SubResult:
    active: bool
    expires_at: datetime | None
    raw: dict


def _google_access_token() -> str:
    """Blocking: mint an OAuth2 access token from the service-account key. google-auth
    is synchronous, so callers run this in a thread."""
    from google.auth.transport.requests import Request  # lazy: optional dep
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(
        settings.google_service_account_json, scopes=[_ANDROID_SCOPE]
    )
    creds.refresh(Request())
    return creds.token


async def verify_google_subscription(product_id: str, purchase_token: str) -> SubResult:
    """Verify a Play subscription purchase token: is it active, and when does it expire?

    Raises on transport/credential/HTTP errors (the caller maps that to a 502). A
    well-formed-but-inactive purchase (expired / not paid) returns ``active=False``.
    """
    token = await asyncio.to_thread(_google_access_token)
    url = (
        "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/"
        f"{settings.google_play_package}/purchases/subscriptions/{product_id}"
        f"/tokens/{purchase_token}"
    )
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    data = r.json()
    expiry_ms = int(data.get("expiryTimeMillis") or 0)
    expires_at = datetime.fromtimestamp(expiry_ms / 1000, tz=UTC) if expiry_ms else None
    # paymentState: 0 pending, 1 received, 2 free trial, 3 deferred. Active = paid/trial
    # AND not yet expired.
    payment_state = data.get("paymentState")
    active = (
        expires_at is not None
        and expires_at > datetime.now(UTC)
        and payment_state in (1, 2)
    )
    return SubResult(active=active, expires_at=expires_at, raw=data)


async def verify_apple_subscription(receipt: str, product_id: str) -> SubResult:
    """Apple App Store verification — STUB (markup only for now; StoreKit is wired on
    the client but server verification is a later milestone)."""
    raise NotImplementedError("Apple receipt verification not implemented yet")
