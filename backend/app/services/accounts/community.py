"""Community data-access + derivations (design/COMMUNITY.md §3).

Kept separate from ``repository.py`` (core account/walk CRUD) the same way ``history.py``
is: this module owns friendships, the activity feed, challenges, and the derived values
(level, streak, presence, challenge progress) computed from the durable ``walks`` rows.

Every function takes an ``AsyncSession`` so the caller owns the transaction. Ownership /
friendship is enforced in-app here (belt) as well as by Postgres RLS (braces, db/rls.sql).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    ActivityEvent,
    Challenge,
    ChallengeParticipant,
    Friendship,
    GroupStreak,
    GroupStreakMember,
    User,
    Walk,
)

# A friend counts as "walking now" if they've been active within this window.
PRESENCE_WINDOW = timedelta(minutes=12)
_HANDLE_RE = re.compile(r"^[a-z0-9_]{3,32}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# -- level (mirror of mobile lib/ui/level.dart / DESIGN_SPEC §6) -------------- #


def level_for_walks(walk_count: int) -> int:
    """points = walks*50; threshold T(L)=25*L*(L+1); level = max L with points>=T(L),
    capped at 50. 1 walk → L1, 3 → L2, 6 → L3."""
    points = walk_count * 50
    level = 0
    while level < 50 and points >= 25 * (level + 1) * (level + 2):
        level += 1
    return max(1, level) if walk_count > 0 else 0


# -- handles ----------------------------------------------------------------- #


def normalize_handle(raw: str) -> str | None:
    """Lowercase, strip a leading @, validate. Returns None if invalid."""
    h = raw.strip().lstrip("@").lower()
    return h if _HANDLE_RE.match(h) else None


async def get_by_handle(session: AsyncSession, *, handle: str) -> User | None:
    return await session.scalar(select(User).where(User.handle == handle.lower()))


async def set_handle(session: AsyncSession, *, user_id, handle: str) -> bool:
    """Claim a unique handle for the user. False if taken by someone else."""
    h = normalize_handle(handle)
    if h is None:
        return False
    uid = _as_uuid(user_id)
    owner = await get_by_handle(session, handle=h)
    if owner is not None and owner.id != uid:
        return False
    user = await session.get(User, uid)
    if user is None:
        return False
    user.handle = h
    await session.flush()
    return True


async def touch_active(session: AsyncSession, *, user_id) -> None:
    """Bump last_active_at (drives presence). Best-effort; no-op if user absent."""
    user = await session.get(User, _as_uuid(user_id))
    if user is not None:
        user.last_active_at = _now()
        await session.flush()


async def update_public_profile(
    session: AsyncSession, *, user_id, avatar_url: str | None = None
) -> None:
    """Mirror the metadata avatar onto the durable row for public rendering."""
    user = await session.get(User, _as_uuid(user_id))
    if user is not None and avatar_url is not None:
        user.avatar_url = avatar_url
        await session.flush()


async def search_users(
    session: AsyncSession, *, query: str, exclude, limit: int = 20
) -> list[User]:
    """Find users by handle or display_name prefix/substring (case-insensitive)."""
    q = query.strip().lstrip("@").lower()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    rows = await session.scalars(
        select(User)
        .where(
            User.id != _as_uuid(exclude),
            or_(
                func.lower(User.handle).like(like),
                func.lower(User.display_name).like(like),
            ),
        )
        .limit(limit)
    )
    return list(rows)


# -- friendships ------------------------------------------------------------- #


async def _friendship_between(
    session: AsyncSession, a, b
) -> Friendship | None:
    a, b = _as_uuid(a), _as_uuid(b)
    return await session.scalar(
        select(Friendship).where(
            or_(
                (Friendship.requester_id == a) & (Friendship.addressee_id == b),
                (Friendship.requester_id == b) & (Friendship.addressee_id == a),
            )
        )
    )


async def friend_ids(session: AsyncSession, *, user_id) -> set[uuid.UUID]:
    uid = _as_uuid(user_id)
    rows = await session.execute(
        select(Friendship.requester_id, Friendship.addressee_id).where(
            Friendship.status == "accepted",
            or_(Friendship.requester_id == uid, Friendship.addressee_id == uid),
        )
    )
    ids: set[uuid.UUID] = set()
    for req, add in rows:
        ids.add(add if req == uid else req)
    return ids


async def send_friend_request(
    session: AsyncSession, *, user_id, target_id
) -> str:
    """Send (or auto-accept a reciprocal) request. Returns the resulting status:
    'accepted' | 'pending' | 'self' | 'exists'."""
    uid, tid = _as_uuid(user_id), _as_uuid(target_id)
    if uid == tid:
        return "self"
    existing = await _friendship_between(session, uid, tid)
    if existing is not None:
        if existing.status == "accepted":
            return "exists"
        # A pending request the other way → accept it (reciprocal).
        if existing.addressee_id == uid and existing.status == "pending":
            existing.status = "accepted"
            existing.responded_at = _now()
            await session.flush()
            return "accepted"
        return "pending"  # our own pending request already stands
    session.add(Friendship(requester_id=uid, addressee_id=tid, status="pending"))
    await session.flush()
    return "pending"


async def respond_friend_request(
    session: AsyncSession, *, user_id, requester_id, accept: bool
) -> bool:
    """Accept/decline a pending request addressed to me. False if none pending."""
    uid, rid = _as_uuid(user_id), _as_uuid(requester_id)
    fr = await session.scalar(
        select(Friendship).where(
            Friendship.requester_id == rid,
            Friendship.addressee_id == uid,
            Friendship.status == "pending",
        )
    )
    if fr is None:
        return False
    if accept:
        fr.status = "accepted"
        fr.responded_at = _now()
    else:
        await session.delete(fr)
    await session.flush()
    return True


async def remove_friend(session: AsyncSession, *, user_id, other_id) -> bool:
    fr = await _friendship_between(session, user_id, other_id)
    if fr is None:
        return False
    await session.delete(fr)
    await session.flush()
    return True


async def list_requests(session: AsyncSession, *, user_id) -> dict:
    """Pending requests: {'incoming': [...], 'outgoing': [...]} with the other user."""
    uid = _as_uuid(user_id)
    incoming = await session.execute(
        select(Friendship, User)
        .join(User, User.id == Friendship.requester_id)
        .where(Friendship.addressee_id == uid, Friendship.status == "pending")
    )
    outgoing = await session.execute(
        select(Friendship, User)
        .join(User, User.id == Friendship.addressee_id)
        .where(Friendship.requester_id == uid, Friendship.status == "pending")
    )
    return {
        "incoming": [u for _fr, u in incoming],
        "outgoing": [u for _fr, u in outgoing],
    }


# -- streak / presence ------------------------------------------------------- #


def _tz(tz_offset_min: int) -> tzinfo:
    """The walker's local timezone from a client-supplied UTC offset in minutes.
    Day bucketing must be LOCAL: a 23:30 MSK walk belongs to that local day, not to
    the next UTC one — in UTC the streak visibly broke/joined at local midnight.
    Clamped to the real offset range; 0 (absent param) keeps the old UTC behaviour."""
    return timezone(timedelta(minutes=max(-14 * 60, min(14 * 60, tz_offset_min))))


def _days_from_ts(ts_list: list[datetime], tz_offset_min: int = 0) -> list[date]:
    """Distinct LOCAL dates from walk timestamps, newest first (pure — no I/O)."""
    tz = _tz(tz_offset_min)
    seen: set[date] = set()
    out: list[date] = []
    for ts in sorted(ts_list, reverse=True):
        # SQLite (tests) hands back NAIVE datetimes; the app writes them in UTC. A naive
        # astimezone() would assume the SERVER's local zone — pin to UTC explicitly.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        d = ts.astimezone(tz).date()
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _streak_from_days(days: list[date], *, tz_offset_min: int = 0) -> int:
    """Consecutive LOCAL days (ending today or yesterday), from newest-first dates (pure)."""
    if not days:
        return 0
    today = _now().astimezone(_tz(tz_offset_min)).date()
    if days[0] not in (today, today - timedelta(days=1)):
        return 0
    streak = 1
    for i in range(1, len(days)):
        if days[i - 1] - days[i] == timedelta(days=1):
            streak += 1
        else:
            break
    return streak


async def walks_ts_by_user(
    session: AsyncSession, user_ids
) -> dict[uuid.UUID, list[datetime]]:
    """ONE query: every listed user's walk timestamps. The bulk substrate for
    count/level/streak so N-user endpoints stop paying an N×~200 ms round-trip tax to
    the remote pooler (the community tab measured 2-6 s per endpoint live)."""
    ids = list({_as_uuid(u) for u in user_ids})
    if not ids:
        return {}
    rows = await session.execute(
        select(Walk.user_id, Walk.started_at).where(Walk.user_id.in_(ids))
    )
    out: dict[uuid.UUID, list[datetime]] = {}
    for uid, ts in rows:
        out.setdefault(uid, []).append(ts)
    return out


async def _walk_days(
    session: AsyncSession, uid: uuid.UUID, *, tz_offset_min: int = 0
) -> list[date]:
    rows = await session.scalars(
        select(Walk.started_at).where(Walk.user_id == uid).order_by(Walk.started_at.desc())
    )
    return _days_from_ts(list(rows), tz_offset_min)


async def compute_streak(session: AsyncSession, *, user_id, tz_offset_min: int = 0) -> int:
    """Consecutive LOCAL days (ending today or yesterday) with at least one walk."""
    days = await _walk_days(session, _as_uuid(user_id), tz_offset_min=tz_offset_min)
    return _streak_from_days(days, tz_offset_min=tz_offset_min)


async def walking_now_ids(session: AsyncSession, *, user_ids: set[uuid.UUID]) -> set[uuid.UUID]:
    if not user_ids:
        return set()
    cutoff = _now() - PRESENCE_WINDOW
    rows = await session.scalars(
        select(User.id).where(User.id.in_(user_ids), User.last_active_at >= cutoff)
    )
    return set(rows)


# -- activity feed ----------------------------------------------------------- #


async def record_activity(
    session: AsyncSession, *, user_id, kind: str, payload: dict | None = None
) -> None:
    session.add(ActivityEvent(user_id=_as_uuid(user_id), kind=kind, payload=payload))
    await session.flush()


async def list_feed(
    session: AsyncSession, *, user_id, limit: int = 30
) -> list[tuple[ActivityEvent, User]]:
    """Recent activity from the user and their accepted friends, newest first."""
    ids = await friend_ids(session, user_id=user_id)
    ids.add(_as_uuid(user_id))
    rows = await session.execute(
        select(ActivityEvent, User)
        .join(User, User.id == ActivityEvent.user_id)
        .where(ActivityEvent.user_id.in_(ids))
        .order_by(ActivityEvent.created_at.desc())
        .limit(limit)
    )
    return list(rows)


async def list_friends_walks(
    session: AsyncSession, *, user_id, limit: int = 12
) -> list[tuple[Walk, User]]:
    """Recent walks by the user's friends (for the route cards)."""
    ids = await friend_ids(session, user_id=user_id)
    if not ids:
        return []
    rows = await session.execute(
        select(Walk, User)
        .join(User, User.id == Walk.user_id)
        .where(Walk.user_id.in_(ids), Walk.shared.is_(True))
        .order_by(Walk.started_at.desc())
        .limit(limit)
    )
    return list(rows)


async def share_walk(session: AsyncSession, *, walk_id, user_id) -> bool:
    """Mark one of the caller's own walks as shared (visible to friends). False if not
    theirs."""
    w = await session.get(Walk, _as_uuid(walk_id))
    if w is None or w.user_id != _as_uuid(user_id):
        return False
    w.shared = True
    await session.flush()
    return True


async def get_walk_for_viewer(
    session: AsyncSession, *, walk_id, user_id
) -> tuple[Walk, User] | None:
    """(Walk with events, owner) if the viewer may see it — it's theirs, or it's a
    friend's SHARED walk. Else None."""
    wid, uid = _as_uuid(walk_id), _as_uuid(user_id)
    w = await session.scalar(
        select(Walk).where(Walk.id == wid).options(selectinload(Walk.events))
    )
    if w is None:
        return None
    if w.user_id != uid:
        if not (w.shared and w.user_id in await friend_ids(session, user_id=uid)):
            return None
    owner = await session.get(User, w.user_id)
    return w, owner


# -- challenges -------------------------------------------------------------- #


async def create_challenge(
    session: AsyncSession,
    *,
    creator_id,
    title: str,
    metric: str,
    goal: int,
    scope: str = "friends",
    days: int = 7,
) -> Challenge:
    now = _now()
    ch = Challenge(
        creator_id=_as_uuid(creator_id),
        title=title.strip()[:200],
        metric=metric if metric in ("distance", "places", "districts") else "distance",
        goal=max(1, int(goal)),
        scope=scope if scope in ("friends", "global") else "friends",
        starts_at=now,
        ends_at=now + timedelta(days=max(1, min(days, 90))),
    )
    session.add(ch)
    await session.flush()
    session.add(ChallengeParticipant(challenge_id=ch.id, user_id=_as_uuid(creator_id)))
    await session.flush()
    return ch


async def join_challenge(session: AsyncSession, *, challenge_id, user_id) -> bool:
    cid, uid = _as_uuid(challenge_id), _as_uuid(user_id)
    exists = await session.scalar(
        select(ChallengeParticipant).where(
            ChallengeParticipant.challenge_id == cid,
            ChallengeParticipant.user_id == uid,
        )
    )
    if exists is not None:
        return False
    if await session.get(Challenge, cid) is None:
        return False
    session.add(ChallengeParticipant(challenge_id=cid, user_id=uid))
    await session.flush()
    return True


async def _progress(
    session: AsyncSession, *, user_id: uuid.UUID, ch: Challenge
) -> int:
    """Metric total from the user's walks inside the challenge window."""
    win = (
        Walk.user_id == user_id,
        Walk.started_at >= ch.starts_at,
        Walk.started_at <= ch.ends_at,
    )
    if ch.metric == "places":
        val = await session.scalar(
            select(func.coalesce(func.sum(Walk.object_count), 0)).where(*win)
        )
    elif ch.metric == "districts":
        val = await session.scalar(
            select(func.count(func.distinct(Walk.district))).where(*win, Walk.district.isnot(None))
        )
    else:  # distance
        val = await session.scalar(select(func.coalesce(func.sum(Walk.distance_m), 0)).where(*win))
    return int(val or 0)


def _metric_from_rows(ch: Challenge, rows: list[tuple]) -> int:
    """A user's metric total inside the challenge window, from prefetched walk rows
    (started_at, object_count, district, distance_m) — pure, no I/O."""
    inside = [r for r in rows if ch.starts_at <= r[0] <= ch.ends_at]
    if ch.metric == "places":
        return int(sum(r[1] or 0 for r in inside))
    if ch.metric == "districts":
        return len({r[2] for r in inside if r[2]})
    return int(sum(r[3] or 0 for r in inside))


async def _boards_bulk(
    session: AsyncSession, challenges: list[Challenge]
) -> dict[uuid.UUID, list[tuple[User, int]]]:
    """Leaderboards for MANY challenges in TWO queries total (participants join users +
    one walks sweep for every participant), instead of one query per participant per
    challenge — the old shape cost (challenges × participants) × ~200 ms to the remote
    pooler and dominated the community tab's load time."""
    if not challenges:
        return {}
    ch_ids = [ch.id for ch in challenges]
    parts = await session.execute(
        select(ChallengeParticipant.challenge_id, User)
        .join(User, ChallengeParticipant.user_id == User.id)
        .where(ChallengeParticipant.challenge_id.in_(ch_ids))
    )
    by_ch: dict[uuid.UUID, list[User]] = {}
    all_uids: set[uuid.UUID] = set()
    for cid, user in parts:
        by_ch.setdefault(cid, []).append(user)
        all_uids.add(user.id)
    walks_by_uid: dict[uuid.UUID, list[tuple]] = {}
    if all_uids:
        lo = min(ch.starts_at for ch in challenges)
        hi = max(ch.ends_at for ch in challenges)
        rows = await session.execute(
            select(
                Walk.user_id, Walk.started_at, Walk.object_count, Walk.district, Walk.distance_m
            ).where(Walk.user_id.in_(all_uids), Walk.started_at >= lo, Walk.started_at <= hi)
        )
        for uid, *rest in rows:
            walks_by_uid.setdefault(uid, []).append(tuple(rest))
    boards: dict[uuid.UUID, list[tuple[User, int]]] = {}
    for ch in challenges:
        board = [
            (u, _metric_from_rows(ch, walks_by_uid.get(u.id, [])))
            for u in by_ch.get(ch.id, [])
        ]
        board.sort(key=lambda t: t[1], reverse=True)
        boards[ch.id] = board
    return boards


async def challenge_leaderboard(
    session: AsyncSession, *, challenge_id
) -> tuple[Challenge, list[tuple[User, int]]] | None:
    ch = await session.get(Challenge, _as_uuid(challenge_id))
    if ch is None:
        return None
    boards = await _boards_bulk(session, [ch])
    return ch, boards.get(ch.id, [])


async def list_challenges(session: AsyncSession, *, user_id) -> list[dict]:
    """Active challenges the user is in or could join (own + friends' + global), each
    with the caller's progress and rank. Batched: the boards for ALL challenges come
    from two queries (see _boards_bulk)."""
    uid = _as_uuid(user_id)
    now = _now()
    fids = await friend_ids(session, user_id=uid)
    joined_ids = set(
        await session.scalars(
            select(ChallengeParticipant.challenge_id).where(ChallengeParticipant.user_id == uid)
        )
    )
    # Candidate set: active challenges that are global, mine, a friend's, or already joined.
    creators = fids | {uid}
    rows = list(
        await session.scalars(
            select(Challenge).where(
                Challenge.ends_at >= now,
                or_(
                    Challenge.scope == "global",
                    Challenge.creator_id.in_(creators),
                    Challenge.id.in_(joined_ids) if joined_ids else Challenge.id.is_(None),
                ),
            ).order_by(Challenge.ends_at.asc())
        )
    )
    boards = await _boards_bulk(session, rows)
    out: list[dict] = []
    for ch in rows:
        board = boards.get(ch.id, [])
        rank = next((i + 1 for i, (u, _p) in enumerate(board) if u.id == uid), None)
        mine = next((p for u, p in board if u.id == uid), 0)
        out.append(
            {
                "challenge": ch,
                "joined": ch.id in joined_ids,
                "participants": len(board),
                "my_progress": mine,
                "my_rank": rank,
            }
        )
    return out


async def ensure_weekly_challenge(session: AsyncSession) -> Challenge:
    """The always-on system 'challenge of the week' (global). Creates one if none active."""
    now = _now()
    ch = await session.scalar(
        select(Challenge).where(
            Challenge.creator_id.is_(None),
            Challenge.scope == "global",
            Challenge.ends_at >= now,
        ).order_by(Challenge.ends_at.desc())
    )
    if ch is not None:
        return ch
    ch = Challenge(
        creator_id=None,
        title="10 км за 7 дней",
        metric="distance",
        goal=10000,
        scope="global",
        starts_at=now,
        ends_at=now + timedelta(days=7),
    )
    session.add(ch)
    await session.flush()
    return ch


# -- my walks (with path) ---------------------------------------------------- #


async def my_walks_with_path(
    session: AsyncSession, *, user_id, limit: int = 12
) -> list[tuple[Walk, User]]:
    """The caller's own recent walks (incl. GPS path) for the "My routes" cards."""
    uid = _as_uuid(user_id)
    rows = await session.execute(
        select(Walk, User)
        .join(User, User.id == Walk.user_id)
        .where(Walk.user_id == uid)
        .order_by(Walk.started_at.desc())
        .limit(limit)
    )
    return list(rows)


# -- group streaks ----------------------------------------------------------- #


def _group_value_from_days(
    per_member: list[set[date]], *, tz_offset_min: int = 0
) -> int:
    """Consecutive LOCAL days (ending today or yesterday) on which EVERY member walked
    — pure core shared by the single and the bulk paths."""
    if not per_member:
        return 0
    common = set.intersection(*per_member)
    if not common:
        return 0
    today = _now().astimezone(_tz(tz_offset_min)).date()
    if today in common:
        cursor = today
    elif (today - timedelta(days=1)) in common:
        cursor = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    while cursor in common:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def group_streak_value(
    session: AsyncSession, *, member_ids, tz_offset_min: int = 0
) -> int:
    """Consecutive LOCAL days (ending today or yesterday) on which EVERY member walked."""
    ids = [_as_uuid(m) for m in member_ids]
    if not ids:
        return 0
    per_member = [
        set(await _walk_days(session, mid, tz_offset_min=tz_offset_min)) for mid in ids
    ]
    common = set.intersection(*per_member) if per_member else set()
    if not common:
        return 0
    today = _now().astimezone(_tz(tz_offset_min)).date()
    if today in common:
        cursor = today
    elif (today - timedelta(days=1)) in common:
        cursor = today - timedelta(days=1)
    else:
        return 0
    streak = 0
    while cursor in common:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def create_group_streak(
    session: AsyncSession, *, creator_id, member_ids, title: str | None = None
) -> GroupStreak:
    cid = _as_uuid(creator_id)
    gs = GroupStreak(creator_id=cid, title=(title.strip()[:120] if title else None))
    session.add(gs)
    await session.flush()
    ids = {cid} | {_as_uuid(m) for m in member_ids}
    for uid in ids:
        session.add(GroupStreakMember(streak_id=gs.id, user_id=uid))
    await session.flush()
    return gs


async def list_group_streaks(
    session: AsyncSession, *, user_id, tz_offset_min: int = 0
) -> list[dict]:
    """The caller's group streaks with members and the current derived value.
    Batched: members for ALL streaks in one query + one walks sweep for every member
    (the old per-streak-per-member days query paid ~200 ms each to the remote pooler)."""
    uid = _as_uuid(user_id)
    streak_ids = set(
        await session.scalars(
            select(GroupStreakMember.streak_id).where(GroupStreakMember.user_id == uid)
        )
    )
    if not streak_ids:
        return []
    streaks = list(
        await session.scalars(
            select(GroupStreak)
            .where(GroupStreak.id.in_(streak_ids))
            .order_by(GroupStreak.created_at.desc())
        )
    )
    rows = await session.execute(
        select(GroupStreakMember.streak_id, User)
        .join(User, GroupStreakMember.user_id == User.id)
        .where(GroupStreakMember.streak_id.in_(streak_ids))
    )
    members_by_streak: dict[uuid.UUID, list[User]] = {}
    all_ids: set[uuid.UUID] = set()
    for sid, user in rows:
        members_by_streak.setdefault(sid, []).append(user)
        all_ids.add(user.id)
    ts_by_uid = await walks_ts_by_user(session, all_ids)
    days_by_uid = {
        u: set(_days_from_ts(ts, tz_offset_min)) for u, ts in ts_by_uid.items()
    }
    out: list[dict] = []
    for gs in streaks:
        members = members_by_streak.get(gs.id, [])
        value = _group_value_from_days(
            [days_by_uid.get(u.id, set()) for u in members], tz_offset_min=tz_offset_min
        )
        out.append({"streak": gs, "members": members, "days": value})
    return out


async def leave_group_streak(session: AsyncSession, *, streak_id, user_id) -> bool:
    sid, uid = _as_uuid(streak_id), _as_uuid(user_id)
    m = await session.scalar(
        select(GroupStreakMember).where(
            GroupStreakMember.streak_id == sid, GroupStreakMember.user_id == uid
        )
    )
    if m is None:
        return False
    await session.delete(m)
    await session.flush()
    remaining = await session.scalar(
        select(func.count())
        .select_from(GroupStreakMember)
        .where(GroupStreakMember.streak_id == sid)
    )
    if not remaining:
        gs = await session.get(GroupStreak, sid)
        if gs is not None:
            await session.delete(gs)
        await session.flush()
    return True
