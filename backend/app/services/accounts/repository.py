"""Async CRUD for the durable layer. Pure data-access — no orchestrator wiring yet
(that is phase 4). Every function takes an ``AsyncSession`` so the caller owns the
transaction (``session_scope`` in prod, a test-bound session in tests).

Ownership is enforced in-app as well as by Postgres RLS (design §9a): reads/deletes
filter on ``user_id`` so one user can never touch another's walk by guessing an id.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    Identity,
    InterestSignal,
    NarrationSample,
    User,
    Walk,
    WalkEvent,
    WalkQuality,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _grant_premium_default() -> bool:
    """Whether new signups are minted as lifetime paid (beta early-access flag)."""
    from app.config import settings

    return settings.grant_premium_to_new_users


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# -- users / identities ---------------------------------------------------- #


async def get_or_create_user(
    session: AsyncSession,
    *,
    provider: str,
    provider_uid: str,
    email: str | None = None,
    display_name: str | None = None,
    user_id: uuid.UUID | str | None = None,
) -> User:
    """Resolve the user behind an external identity, creating both on first login.

    ``(provider, provider_uid)`` is unique, so a returning login always maps back to
    the same user; a first login mints a ``User`` + ``Identity`` pair.

    ``user_id`` seeds the user's primary key from the identity provider (for Supabase,
    the JWT ``sub`` == ``auth.uid()``). Setting it makes ``walks.user_id`` equal
    ``auth.uid()`` so the Postgres RLS policies (db/rls.sql) match directly.
    """
    existing = await _resolve_identity(session, provider, provider_uid)
    if existing is not None:
        return existing.user

    user = User(email=email, display_name=display_name)
    if user_id is not None:
        user.id = _as_uuid(user_id)
    # Beta / early-access: mint new signups as lifetime "paid" (design: config flag, off by
    # default — flip OFF when real store subscriptions launch). Only the create path, so
    # existing users are never re-tiered. expires_at stays None → effective_tier == "paid".
    if _grant_premium_default():
        user.tier = "paid"
        user.subscription_platform = "grant"
        user.subscription_product = "beta_grant"
    try:
        # SAVEPOINT so that if we LOSE a create race the rollback undoes only THIS insert (not the
        # caller's whole transaction) and leaves the session usable to re-read the winner's row.
        # The Community tab fires ~10 requests at once, all calling get-or-create for the same
        # Supabase sub — without this the losers hit a duplicate pk_users and 500 the whole tab.
        async with session.begin_nested():
            session.add(user)
            await session.flush()  # assign user.id before linking the identity
            session.add(Identity(user_id=user.id, provider=provider, provider_uid=provider_uid))
            await session.flush()
        return user
    except IntegrityError:
        # A concurrent request created the same user/identity first — re-read it.
        existing = await _resolve_identity(session, provider, provider_uid)
        if existing is not None:
            return existing.user
        if user_id is not None:
            got = await session.get(User, _as_uuid(user_id))
            if got is not None:
                return got
        raise


async def _resolve_identity(
    session: AsyncSession, provider: str, provider_uid: str
) -> Identity | None:
    return await session.scalar(
        select(Identity)
        .where(Identity.provider == provider, Identity.provider_uid == provider_uid)
        .options(selectinload(Identity.user))
    )


async def get_user(session: AsyncSession, *, user_id: uuid.UUID | str) -> User | None:
    return await session.get(User, _as_uuid(user_id))


def effective_tier(user: User | None, *, now: datetime | None = None) -> str:
    """The tier to actually grant. ``"paid"`` only while the subscription is current;
    a lapsed sub (``subscription_expires_at`` in the past) silently reverts to
    ``"free"`` even if the stored ``tier`` still reads ``"paid"`` (renewal not yet
    re-verified). A missing user row is a guest → ``"free"``."""
    if user is None or user.tier != "paid":
        return "free"
    exp = user.subscription_expires_at
    if exp is None:
        return "paid"  # non-expiring / lifetime grant
    now = now or _now()
    if exp.tzinfo is None:  # stored naive (sqlite) — assume UTC
        exp = exp.replace(tzinfo=UTC)
    return "paid" if exp > now else "free"


async def set_subscription(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    tier: str,
    platform: str | None = None,
    product: str | None = None,
    expires_at: datetime | None = None,
    token: str | None = None,
) -> User | None:
    """Write the verified subscription onto the user row (billing path). The user must
    already exist (the billing handler materializes it via ``get_or_create_user`` first).
    Returns the updated user, or None if the row is missing."""
    user = await session.get(User, _as_uuid(user_id))
    if user is None:
        return None
    user.tier = tier
    user.subscription_platform = platform
    user.subscription_product = product
    user.subscription_expires_at = expires_at
    if token is not None:
        user.subscription_token = token
    await session.flush()
    return user


async def delete_user(session: AsyncSession, *, user_id: uuid.UUID | str) -> bool:
    """Delete a user and everything they own (identities, walks, walk_events cascade
    via the FK ON DELETE CASCADE) — the account-deletion / right-to-be-forgotten hook.
    Returns False if the user row didn't exist (e.g. never recorded a walk)."""
    result = await session.execute(delete(User).where(User.id == _as_uuid(user_id)))
    return (result.rowcount or 0) > 0


# -- walks ----------------------------------------------------------------- #


async def start_walk(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    sid: str,
    language: str,
    city: str | None = None,
    district: str | None = None,
    title: str | None = None,
    walk_id: uuid.UUID | str | None = None,
) -> Walk:
    """Create a walk. ``walk_id`` may be supplied so the caller can reference the walk
    before the DB round-trip (the orchestrator pre-generates it, then writes async)."""
    walk = Walk(
        user_id=_as_uuid(user_id),
        sid=sid,
        language=language,
        city=city,
        district=district,
        title=title,
    )
    if walk_id is not None:
        walk.id = _as_uuid(walk_id)
    session.add(walk)
    await session.flush()
    return walk


async def append_event(
    session: AsyncSession,
    *,
    walk_id: uuid.UUID | str,
    place_id: str,
    name: str,
    category: str,
    lat: float,
    lon: float,
    significance: str,
    narration: str | None = None,
    said_at: datetime | None = None,
) -> WalkEvent:
    """Append the next narrated object to a walk. ``seq`` is derived from the current
    max so events stay strictly ordered even across reconnects; ``object_count`` is
    kept in sync so the list view needs no COUNT."""
    wid = _as_uuid(walk_id)
    max_seq = await session.scalar(
        select(func.max(WalkEvent.seq)).where(WalkEvent.walk_id == wid)
    )
    seq = 0 if max_seq is None else max_seq + 1
    event = WalkEvent(
        walk_id=wid,
        seq=seq,
        place_id=place_id,
        name=name,
        category=category,
        lat=lat,
        lon=lon,
        significance=significance,
        narration=narration,
        said_at=said_at or _now(),
    )
    session.add(event)
    walk = await session.get(Walk, wid)
    if walk is not None:
        walk.object_count = seq + 1
    await session.flush()
    return event


async def append_narration_sample(
    session: AsyncSession,
    *,
    walk_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
    seq: int,
    kind: str,
    language: str,
    narration: str,
    tier: str = "free",
    place_id: str | None = None,
    category: str | None = None,
    significance: str | None = None,
    facts: str | None = None,
    input_json: dict | None = None,
) -> NarrationSample:
    """Persist one narrated blurb + the full context that produced it (Block 4 corpus).
    Kept in the same transaction as the event write so the walk FK is satisfied and a
    walk deletion cascades to its samples."""
    sample = NarrationSample(
        walk_id=_as_uuid(walk_id),
        user_id=_as_uuid(user_id),
        seq=seq,
        kind=kind,
        language=language,
        tier=tier,
        place_id=place_id,
        category=category,
        significance=significance,
        facts=facts,
        input_json=input_json,
        narration=narration,
    )
    session.add(sample)
    await session.flush()
    return sample


async def append_interest_signal(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    kind: str,
    weight: float,
    walk_id: uuid.UUID | str | None = None,
    place_id: str | None = None,
    category: str | None = None,
    significance: str | None = None,
    language: str | None = None,
    meta: dict | None = None,
) -> InterestSignal:
    """Append a real interest signal (Block 4 Part C future ground-truth)."""
    signal = InterestSignal(
        user_id=_as_uuid(user_id),
        kind=kind,
        weight=weight,
        walk_id=_as_uuid(walk_id) if walk_id is not None else None,
        place_id=place_id,
        category=category,
        significance=significance,
        language=language,
        meta=meta,
    )
    session.add(signal)
    await session.flush()
    return signal


# -- quality worker (Block 4 Phase 4) -------------------------------------- #


async def list_unscored_walks(session: AsyncSession, *, limit: int = 50) -> list[Walk]:
    """Finished walks that have no ``walk_quality`` row yet — the sweep work-list. A walk
    is 'finished' once ``ended_at`` is set (the history writer stamps it on each event).
    Events are eager-loaded so the caller can score without an extra round-trip."""
    rows = await session.scalars(
        select(Walk)
        .outerjoin(WalkQuality, WalkQuality.walk_id == Walk.id)
        .where(WalkQuality.id.is_(None), Walk.ended_at.is_not(None))
        .order_by(Walk.ended_at.asc())
        .options(selectinload(Walk.events))
        .limit(limit)
    )
    return list(rows)


async def get_narration_samples(
    session: AsyncSession, *, walk_id: uuid.UUID | str
) -> list[NarrationSample]:
    """The captured (FACTS + context → narration) samples for a walk, in tour order."""
    rows = await session.scalars(
        select(NarrationSample)
        .where(NarrationSample.walk_id == _as_uuid(walk_id))
        .order_by(NarrationSample.seq.asc())
    )
    return list(rows)


async def append_walk_quality(
    session: AsyncSession, *, walk_id: uuid.UUID | str, user_id: uuid.UUID | str, **fields
) -> WalkQuality:
    """Write the per-walk quality row (idempotent via the unique walk_id)."""
    row = WalkQuality(walk_id=_as_uuid(walk_id), user_id=_as_uuid(user_id), **fields)
    session.add(row)
    await session.flush()
    return row


async def end_walk(
    session: AsyncSession,
    *,
    walk_id: uuid.UUID | str,
    distance_m: int | None = None,
    title: str | None = None,
    ended_at: datetime | None = None,
) -> Walk | None:
    walk = await session.get(Walk, _as_uuid(walk_id))
    if walk is None:
        return None
    walk.ended_at = ended_at or _now()
    if distance_m is not None:
        walk.distance_m = distance_m
    if title is not None:
        walk.title = title
    await session.flush()
    return walk


async def update_walk_path(
    session: AsyncSession, *, walk_id: uuid.UUID | str, path: list | None
) -> None:
    """Overwrite the walk's stored GPS breadcrumb (idempotent; snapshotted on each event)."""
    if not path:
        return
    walk = await session.get(Walk, _as_uuid(walk_id))
    if walk is not None:
        walk.path = path
        await session.flush()


async def set_walk_summary(
    session: AsyncSession, *, walk_id: uuid.UUID | str, user_id: uuid.UUID | str, summary: str
) -> None:
    """Store the end-of-walk recap on the walk (owner-checked), so it's readable later."""
    if not summary:
        return
    walk = await session.get(Walk, _as_uuid(walk_id))
    if walk is not None and walk.user_id == _as_uuid(user_id):
        walk.summary = summary
        await session.flush()


async def list_walks(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | str,
    limit: int = 20,
    before: datetime | None = None,
) -> list[Walk]:
    """Most-recent-first page of a user's walks. ``before`` is a keyset cursor
    (``started_at`` of the last row seen) for stable pagination."""
    stmt = select(Walk).where(Walk.user_id == _as_uuid(user_id))
    if before is not None:
        stmt = stmt.where(Walk.started_at < before)
    stmt = stmt.order_by(Walk.started_at.desc()).limit(max(1, min(limit, 100)))
    return list(await session.scalars(stmt))


async def count_walks(session: AsyncSession, *, user_id: uuid.UUID | str) -> int:
    """Total walks a user owns — the saved-history cap check (free = 10)."""
    return (
        await session.scalar(
            select(func.count(Walk.id)).where(Walk.user_id == _as_uuid(user_id))
        )
    ) or 0


async def count_walks_since(
    session: AsyncSession, *, user_id: uuid.UUID | str, since: datetime
) -> int:
    """Walks started since ``since`` — the daily tour-quota check (free = N/day)."""
    return (
        await session.scalar(
            select(func.count(Walk.id)).where(
                Walk.user_id == _as_uuid(user_id), Walk.started_at >= since
            )
        )
    ) or 0


async def delete_oldest_walk(session: AsyncSession, *, user_id: uuid.UUID | str) -> bool:
    """Drop the user's oldest walk (and its events, via cascade) — the free-tier
    ring-buffer: keep only the most recent N. Returns False if they have none."""
    oldest = await session.scalar(
        select(Walk.id)
        .where(Walk.user_id == _as_uuid(user_id))
        .order_by(Walk.started_at.asc())
        .limit(1)
    )
    if oldest is None:
        return False
    await session.execute(delete(Walk).where(Walk.id == oldest))
    return True


async def get_walk(
    session: AsyncSession,
    *,
    walk_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
) -> Walk | None:
    """A single walk with its events, only if it belongs to ``user_id``."""
    return await session.scalar(
        select(Walk)
        .where(Walk.id == _as_uuid(walk_id), Walk.user_id == _as_uuid(user_id))
        .options(selectinload(Walk.events))
    )


async def delete_walk(
    session: AsyncSession,
    *,
    walk_id: uuid.UUID | str,
    user_id: uuid.UUID | str,
) -> bool:
    """Delete a walk (and its events, via cascade) — the right-to-be-forgotten hook.
    Returns False if it didn't exist or isn't the caller's."""
    result = await session.execute(
        delete(Walk).where(
            Walk.id == _as_uuid(walk_id), Walk.user_id == _as_uuid(user_id)
        )
    )
    return (result.rowcount or 0) > 0
