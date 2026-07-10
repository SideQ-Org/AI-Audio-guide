"""REST surface for subscription purchases (feature: account tiers).

The client buys through the store, then POSTs the purchase token here; we verify it with
the store and, if active, flip the account to the paid tier. Import-safe for the base
install (durable-layer + store SDK imports are deferred into the handlers).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings

from ..accounts.api import MeOut, _accounts, build_me, current_user

router = APIRouter(tags=["billing"])

_log = logging.getLogger("aiguide.billing")


class GoogleVerifyIn(BaseModel):
    purchase_token: str
    product_id: str


@router.post("/billing/google/verify", response_model=MeOut)
async def google_verify(
    body: GoogleVerifyIn, user_id: str = Depends(current_user)
) -> MeOut:
    """Verify a Google Play purchase token and grant the paid tier on success. Returns
    the caller's fresh entitlements (same shape as GET /me)."""
    from . import verify

    if not verify.billing_enabled():
        raise HTTPException(status_code=503, detail="billing unavailable")
    products = {settings.billing_product_monthly, settings.billing_product_yearly}
    if body.product_id not in products:
        raise HTTPException(status_code=400, detail="unknown product")
    try:
        result = await verify.verify_google_subscription(
            body.product_id, body.purchase_token
        )
    except Exception as e:  # noqa: BLE001 — surface as a bad-gateway, don't 500
        _log.warning("google verify failed for %s: %r", user_id, e)
        raise HTTPException(status_code=502, detail="verification failed") from e

    repo, session_scope = _accounts()
    async with session_scope() as session:
        # Materialize the user row (id seeded from the JWT sub) before writing the sub.
        await repo.get_or_create_user(
            session, provider="supabase", provider_uid=user_id, user_id=user_id
        )
        if result.active:
            await repo.set_subscription(
                session,
                user_id=user_id,
                tier="paid",
                platform="google",
                product=body.product_id,
                expires_at=result.expires_at,
                token=body.purchase_token,
            )
        # An inactive/expired purchase leaves the tier untouched (effective_tier will
        # still read "free" once any prior sub lapses).
        return await build_me(repo, session, user_id)


@router.post("/billing/google/rtdn", status_code=204)
async def google_rtdn() -> None:
    """Play Real-Time Developer Notifications webhook (renew/cancel/refund) — STUB.
    Wire this to a Pub/Sub push subscription to keep tiers fresh without client pings;
    for now the app re-verifies on open (GET /me + purchase flow)."""
    _log.info("RTDN received (stub — not processed)")
