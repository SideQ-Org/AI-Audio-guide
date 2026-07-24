"""REST surface for accounts + walk history (design §7), phase 5.

Read-only history under auth (Supabase does login itself; the backend just verifies the
JWT and serves the user's own rows) plus a delete for the right to be forgotten.

Import-safe for the base install: the top of this module pulls in only FastAPI/pydantic
and the (SQLAlchemy-free) ``verify_token``. The durable-layer imports (``db`` /
``repository``) are deferred into the handlers via ``_accounts()``, so a build without
the ``accounts`` extra can still start the app — the endpoints just answer 503.
"""

from __future__ import annotations

import asyncio
import uuid as _uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from app.config import settings

from .auth import verify_token

router = APIRouter(tags=["accounts"])


# -- response models ------------------------------------------------------- #


class MeOut(BaseModel):
    id: str
    email: str | None = None
    display_name: str | None = None
    # Entitlements (feature: account tiers). The client mirrors these for UX
    # (upgrade prompts); the backend enforces them authoritatively regardless.
    tier: str = "free"  # "free" | "paid" (effective, honouring expiry)
    tours_today: int = 0  # new walks started in the last rolling 24h
    daily_tour_limit: int | None = None  # None => unlimited (paid)
    walk_count: int = 0  # total saved walks
    walk_limit: int | None = None  # None => unlimited (paid)
    subscription_expires_at: datetime | None = None


class WalkOut(BaseModel):
    id: str
    started_at: datetime
    ended_at: datetime | None = None
    language: str
    city: str | None = None
    district: str | None = None
    distance_m: int | None = None
    object_count: int
    title: str | None = None
    # Downsampled GPS route [[lat, lon(, paused)], ...] for the history-list track preview;
    # null/empty for walks recorded before the route feature. The detail carries the full path.
    path: list | None = None


class WalkEventOut(BaseModel):
    seq: int
    place_id: str
    name: str
    category: str
    lat: float
    lon: float
    significance: str
    narration: str | None = None
    said_at: datetime


class WalkDetailOut(WalkOut):
    events: list[WalkEventOut]
    # Inherits `path` from WalkOut, but the detail overrides it with the FULL route (the list
    # carries only a downsampled preview).
    # Structured end-of-walk recap (readable by the owner and a shared-with friend).
    summary: str | None = None


class WalksPage(BaseModel):
    walks: list[WalkOut]
    next_cursor: str | None = None


# -- dependencies / helpers ------------------------------------------------ #


async def current_user(authorization: str | None = Header(default=None)) -> str:
    """Resolve the caller's Supabase user id from a ``Bearer`` JWT, else 401.

    Verification runs off the event loop (JWKS may do a one-time cached fetch) and
    degrades to 401 on any invalid/missing/expired token — never a 500."""
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    user_id = await asyncio.to_thread(verify_token, token)
    if not user_id:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user_id


def _accounts():
    """Lazily import the durable layer; 503 if it isn't available/configured."""
    try:
        from . import repository as repo
        from .db import accounts_enabled, session_scope
    except Exception as e:  # noqa: BLE001 — extra not installed
        raise HTTPException(status_code=503, detail="accounts unavailable") from e
    if not accounts_enabled():
        raise HTTPException(status_code=503, detail="accounts disabled")
    return repo, session_scope


def _downsample(path: list | None, max_points: int = 48) -> list | None:
    """Thin a GPS route down to ~max_points for a light list payload — even sampling that keeps
    the first and last point and each point's pause flag. None/[] passes through unchanged."""
    if not path or len(path) <= max_points:
        return path or None
    step = len(path) / max_points
    idx = sorted({int(i * step) for i in range(max_points)} | {0, len(path) - 1})
    return [path[i] for i in idx]


def _walk_out(walk) -> WalkOut:
    return WalkOut(
        id=str(walk.id),
        started_at=walk.started_at,
        ended_at=walk.ended_at,
        language=walk.language,
        city=walk.city,
        district=walk.district,
        distance_m=walk.distance_m,
        object_count=walk.object_count,
        title=walk.title,
        path=_downsample(walk.path),
    )


# -- endpoints ------------------------------------------------------------- #


async def build_me(repo, session, user_id: str) -> MeOut:
    """Compute the caller's profile + entitlements from the durable store. Shared by
    GET /me and the billing verify endpoint (so both return the identical shape)."""
    user = await repo.get_user(session, user_id=user_id)
    tier = repo.effective_tier(user)
    since = datetime.now(UTC) - timedelta(hours=24)
    # ONE walks sweep instead of two COUNT round-trips: the pooler is ~200 ms away, so
    # every saved statement is real wall-clock off the profile's first paint.
    from .community import walks_ts_by_user

    ts = (await walks_ts_by_user(session, [user_id])).get(_uuid.UUID(user_id), [])
    tours_today = sum(
        1 for t in ts if (t.replace(tzinfo=UTC) if t.tzinfo is None else t) >= since
    )
    walk_count = len(ts)
    paid = tier == "paid"
    return MeOut(
        id=str(user.id) if user is not None else user_id,
        email=user.email if user is not None else None,
        display_name=user.display_name if user is not None else None,
        tier=tier,
        tours_today=tours_today,
        daily_tour_limit=None if paid else settings.free_tier_daily_tours,
        walk_count=walk_count,
        walk_limit=None if paid else settings.free_tier_walk_limit,
        subscription_expires_at=user.subscription_expires_at if user is not None else None,
    )


@router.get("/me", response_model=MeOut)
async def me(user_id: str = Depends(current_user)) -> MeOut:
    """The caller's profile + entitlements. Falls back to a guest-shaped free profile
    if the user row hasn't been materialized yet (they haven't recorded a walk)."""
    repo, session_scope = _accounts()
    async with session_scope() as session:
        return await build_me(repo, session, user_id)


@router.delete("/me", status_code=204)
async def delete_me(user_id: str = Depends(current_user)) -> None:
    """Delete the caller's account data — profile, identities, walks, events (cascade).
    Right-to-be-forgotten. The Supabase *auth* user is separate (delete it from the
    Supabase dashboard or via the admin/service key); this wipes everything we store."""
    repo, session_scope = _accounts()
    async with session_scope() as session:
        await repo.delete_user(session, user_id=user_id)  # idempotent: no row => no-op


@router.get("/walks", response_model=WalksPage)
async def list_walks(
    user_id: str = Depends(current_user),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, description="started_at of the last row seen"),
) -> WalksPage:
    """Most-recent-first page of the caller's walks (keyset pagination via ``cursor``)."""
    repo, session_scope = _accounts()
    before: datetime | None = None
    if cursor:
        try:
            before = datetime.fromisoformat(cursor)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid cursor") from e
    async with session_scope() as session:
        walks = await repo.list_walks(
            session, user_id=user_id, limit=limit, before=before
        )
        # Build inside the scope: `walk.path` (like other attrs) expires once the session commits.
        out = [_walk_out(w) for w in walks]
    next_cursor = out[-1].started_at.isoformat() if len(out) == limit else None
    return WalksPage(walks=out, next_cursor=next_cursor)


@router.get("/walks/{walk_id}", response_model=WalkDetailOut)
async def get_walk(walk_id: str, user_id: str = Depends(current_user)) -> WalkDetailOut:
    """A single walk with its narrated objects — only if it belongs to the caller."""
    repo, session_scope = _accounts()
    async with session_scope() as session:
        walk = await repo.get_walk(session, walk_id=walk_id, user_id=user_id)
        if walk is None:
            raise HTTPException(status_code=404, detail="walk not found")
        events = [
            WalkEventOut(
                seq=e.seq, place_id=e.place_id, name=e.name, category=e.category,
                lat=e.lat, lon=e.lon, significance=e.significance,
                narration=e.narration, said_at=e.said_at,
            )
            for e in walk.events
        ]
        detail = _walk_out(walk).model_dump()
        detail.pop("path", None)  # replace the downsampled preview with the FULL route below
        path = walk.path  # read inside the session scope (attrs expire on commit)
        summary = walk.summary
    return WalkDetailOut(**detail, events=events, path=path, summary=summary)


@router.delete("/walks/{walk_id}", status_code=204)
async def delete_walk(walk_id: str, user_id: str = Depends(current_user)) -> None:
    """Delete a walk (cascades to its events) — the right-to-be-forgotten hook."""
    repo, session_scope = _accounts()
    async with session_scope() as session:
        ok = await repo.delete_walk(session, walk_id=walk_id, user_id=user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="walk not found")
