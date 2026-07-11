"""REST surface for Community (design/COMMUNITY.md §3.2).

All routes require a Supabase JWT (``current_user``). The durable-layer imports are
deferred into the handlers via ``_community()`` so a base install without the ``accounts``
extra still starts (the endpoints answer 503). Backend runs under the service role and
enforces friendship/ownership in-app; RLS (db/rls.sql) is defence-in-depth.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.config import settings

from .api import current_user

router = APIRouter(prefix="/community", tags=["community"])


# -- response models ------------------------------------------------------- #


class CommunityUser(BaseModel):
    id: str
    handle: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None
    level: int = 0
    streak: int = 0
    walking_now: bool = False
    walk_count: int = 0


class FriendsOut(BaseModel):
    friends: list[CommunityUser]


class RequestsOut(BaseModel):
    incoming: list[CommunityUser]
    outgoing: list[CommunityUser]


class FeedItem(BaseModel):
    id: str
    kind: str
    payload: dict | None = None
    created_at: datetime
    user: CommunityUser


class FeedOut(BaseModel):
    items: list[FeedItem]


class FriendWalk(BaseModel):
    id: str
    started_at: datetime
    city: str | None = None
    district: str | None = None
    distance_m: int | None = None
    object_count: int = 0
    title: str | None = None
    path: list | None = None
    user: CommunityUser


class FriendWalksOut(BaseModel):
    walks: list[FriendWalk]


class ChallengeOut(BaseModel):
    id: str
    title: str
    metric: str
    goal: int
    scope: str
    starts_at: datetime
    ends_at: datetime
    creator_id: str | None = None
    joined: bool = False
    participants: int = 0
    my_progress: int = 0
    my_rank: int | None = None


class ChallengesOut(BaseModel):
    challenges: list[ChallengeOut]


class LeaderboardEntry(BaseModel):
    rank: int
    progress: int
    user: CommunityUser


class ChallengeDetailOut(ChallengeOut):
    leaderboard: list[LeaderboardEntry]


class GroupStreakOut(BaseModel):
    id: str
    title: str | None = None
    days: int = 0
    members: list[CommunityUser]


class GroupStreaksOut(BaseModel):
    streaks: list[GroupStreakOut]


# -- request bodies -------------------------------------------------------- #


class ProfileIn(BaseModel):
    handle: str | None = None
    avatar_url: str | None = None
    display_name: str | None = None


class FriendRequestIn(BaseModel):
    handle: str


class ChallengeIn(BaseModel):
    title: str
    metric: str = "distance"
    goal: int = 10000
    scope: str = "friends"
    days: int = 7


class GroupStreakIn(BaseModel):
    handles: list[str] = []
    title: str | None = None


# -- helpers --------------------------------------------------------------- #


def _community():
    """Lazily import the durable layer; 503 if unavailable/disabled."""
    try:
        from . import community as comm
        from . import repository as repo
        from .db import accounts_enabled, session_scope
    except Exception as e:  # noqa: BLE001 — extra not installed
        raise HTTPException(status_code=503, detail="accounts unavailable") from e
    if not accounts_enabled():
        raise HTTPException(status_code=503, detail="accounts disabled")
    return comm, repo, session_scope


async def _ensure(repo, session, user_id: str):
    """Materialize the caller's durable row (id seeded from the JWT sub == auth.uid())."""
    return await repo.get_or_create_user(
        session, provider="supabase", provider_uid=user_id, user_id=user_id
    )


def _user_light(user) -> CommunityUser:
    """Identity-only user (no per-user DB queries). Use where the UI shows just the
    name/handle/avatar (feed ticker, my-routes author) — avoids the N+1 that makes the
    heavy endpoints slow under concurrency."""
    return CommunityUser(
        id=str(user.id),
        handle=user.handle,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
    )


async def _user_out(comm, repo, session, user, *, walking: bool | None = None) -> CommunityUser:
    walk_count = await repo.count_walks(session, user_id=user.id)
    streak = await comm.compute_streak(session, user_id=user.id)
    if walking is None:
        walking = bool(await comm.walking_now_ids(session, user_ids={user.id}))
    return CommunityUser(
        id=str(user.id),
        handle=user.handle,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        level=comm.level_for_walks(walk_count),
        streak=streak,
        walking_now=walking,
        walk_count=walk_count,
    )


def _challenge_out(c, *, joined=False, participants=0, my_progress=0, my_rank=None) -> ChallengeOut:
    return ChallengeOut(
        id=str(c.id), title=c.title, metric=c.metric, goal=c.goal, scope=c.scope,
        starts_at=c.starts_at, ends_at=c.ends_at,
        creator_id=str(c.creator_id) if c.creator_id else None,
        joined=joined, participants=participants, my_progress=my_progress, my_rank=my_rank,
    )


# -- profile --------------------------------------------------------------- #


@router.get("/me", response_model=CommunityUser)
async def community_me(user_id: str = Depends(current_user)) -> CommunityUser:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        user = await _ensure(repo, session, user_id)
        return await _user_out(comm, repo, session, user)


@router.post("/profile", response_model=CommunityUser)
async def set_profile(body: ProfileIn, user_id: str = Depends(current_user)) -> CommunityUser:
    """Claim/update the public profile: unique @handle, mirrored avatar, display name."""
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        user = await _ensure(repo, session, user_id)
        if body.handle is not None:
            ok = await comm.set_handle(session, user_id=user_id, handle=body.handle)
            if not ok:
                raise HTTPException(status_code=409, detail="handle taken or invalid")
        if body.avatar_url is not None:
            await comm.update_public_profile(session, user_id=user_id, avatar_url=body.avatar_url)
        if body.display_name is not None and body.display_name.strip():
            user.display_name = body.display_name.strip()[:200]
        await session.flush()
        fresh = await repo.get_user(session, user_id=user_id)
        return await _user_out(comm, repo, session, fresh)


@router.get("/search", response_model=FriendsOut)
async def search(
    q: str = Query(min_length=2), user_id: str = Depends(current_user)
) -> FriendsOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        users = await comm.search_users(session, query=q, exclude=user_id, limit=20)
        return FriendsOut(friends=[await _user_out(comm, repo, session, u) for u in users])


# -- friends --------------------------------------------------------------- #


@router.get("/friends", response_model=FriendsOut)
async def friends(user_id: str = Depends(current_user)) -> FriendsOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        ids = await comm.friend_ids(session, user_id=user_id)
        walking = await comm.walking_now_ids(session, user_ids=ids)
        out = []
        for fid in ids:
            u = await repo.get_user(session, user_id=fid)
            if u is not None:
                out.append(await _user_out(comm, repo, session, u, walking=fid in walking))
        out.sort(key=lambda u: (not u.walking_now, -u.streak))
        return FriendsOut(friends=out)


@router.get("/friends/requests", response_model=RequestsOut)
async def friend_requests(user_id: str = Depends(current_user)) -> RequestsOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        reqs = await comm.list_requests(session, user_id=user_id)
        inc = [await _user_out(comm, repo, session, u) for u in reqs["incoming"]]
        out = [await _user_out(comm, repo, session, u) for u in reqs["outgoing"]]
        return RequestsOut(incoming=inc, outgoing=out)


@router.post("/friends/request")
async def friend_request(body: FriendRequestIn, user_id: str = Depends(current_user)) -> dict:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        target = await comm.get_by_handle(session, handle=body.handle)
        if target is None:
            raise HTTPException(status_code=404, detail="user not found")
        status = await comm.send_friend_request(session, user_id=user_id, target_id=target.id)
        return {"status": status}


@router.post("/friends/{other_id}/accept")
async def accept_request(other_id: str, user_id: str = Depends(current_user)) -> dict:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        ok = await comm.respond_friend_request(
            session, user_id=user_id, requester_id=other_id, accept=True
        )
        if not ok:
            raise HTTPException(status_code=404, detail="no pending request")
        return {"ok": True}


@router.post("/friends/{other_id}/decline")
async def decline_request(other_id: str, user_id: str = Depends(current_user)) -> dict:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await comm.respond_friend_request(
            session, user_id=user_id, requester_id=other_id, accept=False
        )
        return {"ok": True}


@router.delete("/friends/{other_id}", status_code=204)
async def unfriend(other_id: str, user_id: str = Depends(current_user)) -> None:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await comm.remove_friend(session, user_id=user_id, other_id=other_id)


# -- feed / friends' walks -------------------------------------------------- #


@router.get("/feed", response_model=FeedOut)
async def feed(
    limit: int = Query(default=30, ge=1, le=100), user_id: str = Depends(current_user)
) -> FeedOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        rows = await comm.list_feed(session, user_id=user_id, limit=limit)
        items = []
        for ev, u in rows:
            items.append(
                FeedItem(
                    id=str(ev.id), kind=ev.kind, payload=ev.payload, created_at=ev.created_at,
                    user=_user_light(u),
                )
            )
        return FeedOut(items=items)


@router.get("/friends/walks", response_model=FriendWalksOut)
async def friends_walks(
    limit: int = Query(default=12, ge=1, le=50), user_id: str = Depends(current_user)
) -> FriendWalksOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        rows = await comm.list_friends_walks(session, user_id=user_id, limit=limit)
        walks = []
        for w, u in rows:
            walks.append(
                FriendWalk(
                    id=str(w.id), started_at=w.started_at, city=w.city, district=w.district,
                    distance_m=w.distance_m, object_count=w.object_count, title=w.title,
                    path=w.path, user=await _user_out(comm, repo, session, u),
                )
            )
        return FriendWalksOut(walks=walks)


# -- challenges ------------------------------------------------------------- #


@router.get("/challenges", response_model=ChallengesOut)
async def challenges(user_id: str = Depends(current_user)) -> ChallengesOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        await comm.ensure_weekly_challenge(session)  # always-on system challenge
        rows = await comm.list_challenges(session, user_id=user_id)
        out = [
            _challenge_out(
                r["challenge"], joined=r["joined"], participants=r["participants"],
                my_progress=r["my_progress"], my_rank=r["my_rank"],
            )
            for r in rows
        ]
        return ChallengesOut(challenges=out)


@router.post("/challenges", response_model=ChallengeOut)
async def create_challenge(body: ChallengeIn, user_id: str = Depends(current_user)) -> ChallengeOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        ch = await comm.create_challenge(
            session, creator_id=user_id, title=body.title, metric=body.metric,
            goal=body.goal, scope=body.scope, days=body.days,
        )
        await comm.record_activity(
            session, user_id=user_id, kind="challenge_join", payload={"challenge_title": ch.title}
        )
        return _challenge_out(ch, joined=True, participants=1, my_progress=0, my_rank=1)


@router.post("/challenges/{challenge_id}/join")
async def join_challenge(challenge_id: str, user_id: str = Depends(current_user)) -> dict:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        ok = await comm.join_challenge(session, challenge_id=challenge_id, user_id=user_id)
        if ok:
            await comm.record_activity(session, user_id=user_id, kind="challenge_join", payload={})
        return {"joined": ok}


@router.get("/challenges/{challenge_id}", response_model=ChallengeDetailOut)
async def challenge_detail(
    challenge_id: str, user_id: str = Depends(current_user)
) -> ChallengeDetailOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        res = await comm.challenge_leaderboard(session, challenge_id=challenge_id)
        if res is None:
            raise HTTPException(status_code=404, detail="challenge not found")
        ch, board = res
        lb = []
        my_rank = None
        my_progress = 0
        joined = False
        for i, (u, prog) in enumerate(board):
            entry = LeaderboardEntry(
                rank=i + 1, progress=prog, user=await _user_out(comm, repo, session, u)
            )
            lb.append(entry)
            if str(u.id) == user_id:
                my_rank, my_progress, joined = i + 1, prog, True
        base = _challenge_out(
            ch, joined=joined, participants=len(board), my_progress=my_progress, my_rank=my_rank
        )
        return ChallengeDetailOut(**base.model_dump(), leaderboard=lb)


# -- my walks (for "My routes") --------------------------------------------- #


@router.get("/my/walks", response_model=FriendWalksOut)
async def my_walks(
    limit: int = Query(default=12, ge=1, le=50), user_id: str = Depends(current_user)
) -> FriendWalksOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        user = await _ensure(repo, session, user_id)
        # Free tier retains only free_tier_walk_limit walks; paid up to `limit`.
        cap = limit if repo.effective_tier(user) == "paid" else settings.free_tier_walk_limit
        rows = await comm.my_walks_with_path(session, user_id=user_id, limit=min(limit, cap))
        walks = []
        for w, u in rows:
            walks.append(
                FriendWalk(
                    id=str(w.id), started_at=w.started_at, city=w.city, district=w.district,
                    distance_m=w.distance_m, object_count=w.object_count, title=w.title,
                    path=w.path, user=_user_light(u),
                )
            )
        return FriendWalksOut(walks=walks)


# -- group streaks ----------------------------------------------------------- #


@router.get("/streaks", response_model=GroupStreaksOut)
async def group_streaks(user_id: str = Depends(current_user)) -> GroupStreaksOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        rows = await comm.list_group_streaks(session, user_id=user_id)
        out = []
        for r in rows:
            members = [await _user_out(comm, repo, session, u) for u in r["members"]]
            out.append(GroupStreakOut(
                id=str(r["streak"].id), title=r["streak"].title, days=r["days"], members=members))
        return GroupStreaksOut(streaks=out)


@router.post("/streaks", response_model=GroupStreakOut)
async def create_group_streak(
    body: GroupStreakIn, user_id: str = Depends(current_user)
) -> GroupStreakOut:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        await _ensure(repo, session, user_id)
        # Resolve invited handles → user ids, but only accepted friends may be added.
        fids = await comm.friend_ids(session, user_id=user_id)
        member_ids = []
        for h in body.handles:
            u = await comm.get_by_handle(session, handle=h)
            if u is not None and u.id in fids:
                member_ids.append(u.id)
        gs = await comm.create_group_streak(
            session, creator_id=user_id, member_ids=member_ids, title=body.title
        )
        await comm.record_activity(
            session, user_id=user_id, kind="group_streak", payload={"title": gs.title}
        )
        # Build the fresh view (creator + members).
        rows = await comm.list_group_streaks(session, user_id=user_id)
        row = next((r for r in rows if r["streak"].id == gs.id), None)
        members = [await _user_out(comm, repo, session, u) for u in (row["members"] if row else [])]
        return GroupStreakOut(
            id=str(gs.id), title=gs.title, days=row["days"] if row else 0, members=members)


@router.post("/streaks/{streak_id}/leave", status_code=200)
async def leave_group_streak(streak_id: str, user_id: str = Depends(current_user)) -> dict:
    comm, repo, session_scope = _community()
    async with session_scope() as session:
        ok = await comm.leave_group_streak(session, streak_id=streak_id, user_id=user_id)
        return {"ok": ok}
