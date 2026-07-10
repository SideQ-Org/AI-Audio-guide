"""Fire-and-forget walk-history writes (phase 4, design §5).

Called from the orchestrator at the single point where it has just narrated a fresh
object. Two-part contract to keep the hot path safe:

  * SYNCHRONOUS, in-memory: decide whether this object continues the current walk or
    starts a new one (after a long pause), and stamp ``walk_id`` / ``walk_last_event_at``
    onto the ``SessionState``. The orchestrator persists that in its normal save — no
    race with a background task.
  * ASYNCHRONOUS, best-effort: the actual DB I/O runs in a detached task with a
    pre-generated ``walk_id``. It never touches SessionState, and any failure is logged
    and swallowed so the tour is never blocked or broken by the database.

No-op for guests (``user_id is None``) or when the durable store is off — the caller
only imports this module when ``settings.database_url`` is set, so the base install
(no ``accounts`` extra) never loads SQLAlchemy.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from app.config import settings

from . import repository as repo
from .db import accounts_enabled, session_scope

_log = logging.getLogger("aiguide.history")

# Keep strong refs so detached tasks aren't GC'd mid-flight (asyncio only holds weakrefs).
_tasks: set[asyncio.Task] = set()


def record_object(state, place, significance, narration: str) -> None:
    """Record a just-narrated object into the current walk (creating/rotating the walk
    as needed). Safe to call on every narration; returns immediately."""
    if not accounts_enabled() or not state.user_id:
        return

    now = time.time()
    gap = settings.walk_gap_s
    new_walk = state.walk_id is None or (
        gap > 0
        and state.walk_last_event_at is not None
        and now - state.walk_last_event_at > gap
    )
    # A gap-rotation (an EXISTING walk timed out) starts a fresh route; the very first
    # walk of a session keeps the whole accumulated trail (it's all one walk).
    rotated = new_walk and state.walk_id is not None
    if new_walk:
        wid = uuid.uuid4()
        state.walk_id = str(wid)
    else:
        try:
            wid = uuid.UUID(state.walk_id)
        except (ValueError, TypeError):  # corrupt id — start clean rather than crash
            wid = uuid.uuid4()
            state.walk_id = str(wid)
            new_walk = True
    state.walk_last_event_at = now
    # On a gap-rotation keep only the latest point so the new walk's stored path is its
    # own trail, not the previous walk's. (First walk of a session keeps everything.)
    if rotated and state.path:
        state.path = state.path[-1:]

    event = {
        "place_id": place.id,
        "name": place.name,
        "category": place.category,
        "lat": place.location.lat,
        "lon": place.location.lon,
        "significance": significance.value if significance is not None else "MEDIUM",
        "narration": narration or None,
    }
    meta = {
        "user_id": state.user_id,
        "tier": getattr(state, "tier", "free"),  # free => ring-buffer the saved history
        "sid": state.session_id,
        "language": state.language,
        "city": state.address.city or None,
        "district": state.address.district or None,
        # snapshot the downsampled route so far (persisted to the walk row below)
        "path": list(getattr(state, "path", []) or []),
    }
    task = asyncio.create_task(_write(new_walk, wid, meta, event))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _write(new_walk: bool, wid: uuid.UUID, meta: dict, event: dict) -> None:
    try:
        async with session_scope() as session:
            if new_walk:
                # Lazily materialize the user row (id seeded from the JWT sub == auth.uid()
                # so RLS matches) before the walk's FK needs it.
                await repo.get_or_create_user(
                    session,
                    provider="supabase",
                    provider_uid=meta["user_id"],
                    user_id=meta["user_id"],
                )
                title = f"Прогулка по {meta['city']}" if meta["city"] else "Прогулка"
                await repo.start_walk(
                    session,
                    walk_id=wid,
                    user_id=meta["user_id"],
                    sid=meta["sid"],
                    language=meta["language"],
                    city=meta["city"],
                    district=meta["district"],
                    title=title,
                )
                # Free-tier saved-history cap (feature: account tiers): keep only the
                # newest N walks — evict the oldest once this new one tips it over. Paid
                # (limit unlimited) skips this. The just-created walk is newest, so it is
                # never the one dropped.
                limit = settings.free_tier_walk_limit
                if meta.get("tier") == "free" and limit > 0:
                    while (await repo.count_walks(session, user_id=meta["user_id"])) > limit:
                        if not await repo.delete_oldest_walk(session, user_id=meta["user_id"]):
                            break
            await repo.append_event(session, walk_id=wid, **event)
            # ended_at trails the last narrated object (MVP: no explicit stop signal),
            # giving the list view a sensible "last activity" time + duration.
            await repo.end_walk(session, walk_id=wid)
            # Snapshot the route breadcrumb accumulated so far onto the walk row.
            await repo.update_walk_path(session, walk_id=wid, path=meta.get("path"))
    except Exception as e:  # noqa: BLE001 — history is best-effort; never break the tour
        _log.warning("history write failed (walk=%s): %r", wid, e)
