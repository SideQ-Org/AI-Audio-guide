"""SQLAlchemy ORM models for the durable accounts/history layer (design §4).

Portable across Postgres (prod/Supabase) and SQLite (offline tests): UUID/timestamp
defaults are generated Python-side, and types (``Uuid``, ``DateTime(timezone=True)``,
``Double``) map cleanly to both. Row-Level Security lives in ``db/rls.sql`` — a
Postgres-only concern applied on top of these tables, not expressible in the ORM.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Double,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    DateTime as SADateTime,
)
from sqlalchemy import (
    Uuid as SAUuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Stable naming convention so Alembic autogenerate/downgrade names constraints
# deterministically across Postgres and SQLite.
_NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=_NAMING)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )

    # Subscription tier: "free" (DeepSeek, ads, capped) | "paid" (Gemini, no ads,
    # unlimited). Written server-side ONLY (billing receipt verification) — never
    # trusted from the client. The other columns hold the store subscription so it can
    # be re-verified / expired.  tier is derived from subscription_expires_at at read
    # time (a lapsed sub reverts to "free"); the stored tier is the last known state.
    tier: Mapped[str] = mapped_column(String(16), default="free", nullable=False)
    subscription_platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    subscription_product: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_expires_at: Mapped[datetime | None] = mapped_column(
        SADateTime(timezone=True), nullable=True
    )
    subscription_token: Mapped[str | None] = mapped_column(Text, nullable=True)

    # -- community (design/COMMUNITY.md) ------------------------------------- #
    # Public @handle (unique, lowercased). avatar_url mirrors the Supabase-metadata
    # avatar so public profiles can render it without the auth token. last_active_at is
    # bumped on position updates and drives the "на прогулке" presence flag.
    handle: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(
        SADateTime(timezone=True), nullable=True
    )

    identities: Mapped[list[Identity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    walks: Mapped[list[Walk]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Identity(Base):
    """External login binding (google/apple/email). One user can have several."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "provider_uid"),)

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_uid: Mapped[str] = mapped_column(String(320), nullable=False)

    user: Mapped[User] = relationship(back_populates="identities")


class Walk(Base):
    """One walk = one "start a tour" period. Survives WS reconnects (same sid); one
    sid can spawn several walks over time (design §5)."""

    __tablename__ = "walks"

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    sid: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        SADateTime(timezone=True), nullable=True
    )
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    city: Mapped[str | None] = mapped_column(String(200), nullable=True)
    district: Mapped[str | None] = mapped_column(String(200), nullable=True)
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    object_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # Downsampled GPS breadcrumb of the walk ([[lat, lon], ...]) for the history route
    # map. Nullable: old walks (and any where capture was off) simply have no path.
    path: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Community: friends see this walk under "Маршруты друзей" only once shared.
    shared: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Structured end-of-walk recap (LLM), generated on `end` — kept so it's readable later in
    # the walk detail, by the owner and by a friend the walk is shared with. Nullable: old walks
    # and any where generation failed simply have none.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="walks")
    events: Mapped[list[WalkEvent]] = relationship(
        back_populates="walk",
        cascade="all, delete-orphan",
        order_by="WalkEvent.seq",
    )


class WalkEvent(Base):
    """A narrated object, in tour order. ``narration`` keeps the spoken text so the
    walk can be replayed offline (design §5/§11.7)."""

    __tablename__ = "walk_events"
    __table_args__ = (UniqueConstraint("walk_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    walk_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("walks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    place_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(400), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    lat: Mapped[float] = mapped_column(Double, nullable=False)
    lon: Mapped[float] = mapped_column(Double, nullable=False)
    significance: Mapped[str] = mapped_column(String(16), nullable=False)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    said_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )

    walk: Mapped[Walk] = relationship(back_populates="events")


# -- self-improvement corpus (Block 4 §D2, Phase 0) --------------------------- #


class NarrationSample(Base):
    """One narrated blurb + the FULL input context that produced it — the eval corpus
    the interestingness metrics / self-improvement loop consume (Block 4).

    Distinct from ``WalkEvent`` (which stores only the final text + significance for
    replay): this keeps the ``facts`` handed to the narrator and the serialized
    ``NarratorInput`` (``input_json``), WITHOUT which the groundedness hard-gate cannot
    verify a claim against its source. FK→walks with CASCADE so deleting a walk (right to
    be forgotten) drops its samples too. Written best-effort, off the hot path, only when
    ``settings.capture_narration_samples`` is on."""

    __tablename__ = "narration_samples"

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    walk_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("walks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised owner so the sidecar can filter/aggregate + RLS can match auth.uid()
    # without a join. Not an FK: the walk's own FK already ties the row to a live user.
    user_id: Mapped[uuid.UUID] = mapped_column(SAUuid, nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # object | area | elaborate | reply — which kind of blurb this is.
    kind: Mapped[str] = mapped_column(String(16), default="object", nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    # The tier this walk ran under (free|paid) — free/paid use DIFFERENT generator models, so
    # quality is scored + optimized per tier (Block 4). Not a gate input (facts-only is universal).
    tier: Mapped[str] = mapped_column(String(8), default="free", nullable=False)
    place_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    significance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The FACTS text the narrator was given (enrichment snippet) — the grounding source.
    facts: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The serialized NarratorInput (build_narrator_user) — full context the model saw.
    input_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    narration: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False, index=True
    )


class InterestSignal(Base):
    """A real interest signal keyed to a narrated object (Block 4 Part C). Logged NOW so
    it accumulates as future ground-truth to calibrate/replace the LLM judge. Effortful
    positive (a follow-up question right after a blurb) ≫ passive (completion) ≫ negative
    (skip/mute/pause). Written best-effort when ``settings.capture_interest_signals`` is
    on; a plain analytics sink (no FK) so a signal is never lost to row-ordering."""

    __tablename__ = "interest_signals"

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(SAUuid, nullable=False, index=True)
    walk_id: Mapped[uuid.UUID | None] = mapped_column(SAUuid, nullable=True, index=True)
    # followup | complete | truncate | skip | mute | pause | control_patch
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    # Signed weight in the Twitter spirit (effort ≫ passive ≫ negative), set by the caller.
    weight: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    place_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    significance: Mapped[str | None] = mapped_column(String(16), nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False, index=True
    )


class WalkQuality(Base):
    """Per-walk interestingness score written by the quality-worker sidecar (Block 4
    Phase 4). One row per walk (``walk_id`` unique = the idempotency marker: a walk already
    here is skipped by the sweep). FK→walks CASCADE so it drops with the walk. Pure
    analytics — the backend never reads it on the hot path; the worker writes it, a
    dashboard reads it."""

    __tablename__ = "walk_quality"
    __table_args__ = (UniqueConstraint("walk_id"),)

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    walk_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("walks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(SAUuid, nullable=False, index=True)
    computed_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False, index=True
    )
    # free|paid — segment quality by tier (different generator models per tier).
    tier: Mapped[str] = mapped_column(String(8), default="free", nullable=False, index=True)
    n_blurbs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 0-100 walk interestingness (mean gated composite over blurbs).
    score: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    interest_mean: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    grounded_rate: Mapped[float] = mapped_column(Double, default=1.0, nullable=False)
    cliche_rate: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    novelty_mean: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    distinct_2: Mapped[float] = mapped_column(Double, default=0.0, nullable=False)
    used_judge: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Failure taxonomy + worst-blurb diagnostics (feeds the optimizer's error analysis).
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)


# -- community (design/COMMUNITY.md §3) --------------------------------------- #


class Friendship(Base):
    """A directed request that becomes a symmetric friendship once ``accepted``.

    Exactly one row per ordered (requester, addressee) pair. "Are A and B friends?" =
    an ``accepted`` row in either direction; a ``pending`` row is an outstanding request.
    """

    __tablename__ = "friendships"
    __table_args__ = (UniqueConstraint("requester_id", "addressee_id"),)

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    requester_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    addressee_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )
    responded_at: Mapped[datetime | None] = mapped_column(
        SADateTime(timezone=True), nullable=True
    )


class ActivityEvent(Base):
    """A feed/ticker item: something a user did that their friends should see."""

    __tablename__ = "activity_events"

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # walk | badge | streak | challenge_join | challenge_win
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False, index=True
    )


class Challenge(Base):
    """A competition over a time window. ``creator_id`` null = a system challenge (the
    weekly "challenge of the week"). Progress is derived from ``walks`` in the window."""

    __tablename__ = "challenges"

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID | None] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # metric: distance | places | districts
    metric: Mapped[str] = mapped_column(String(16), nullable=False)
    goal: Mapped[int] = mapped_column(Integer, nullable=False)
    # scope: friends | global
    scope: Mapped[str] = mapped_column(String(16), default="friends", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )
    ends_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )

    participants: Mapped[list[ChallengeParticipant]] = relationship(
        back_populates="challenge", cascade="all, delete-orphan"
    )


class ChallengeParticipant(Base):
    __tablename__ = "challenge_participants"
    __table_args__ = (UniqueConstraint("challenge_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    challenge_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )
    # Denormalised cache of progress in the window; recomputed from walks on read.
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    challenge: Mapped[Challenge] = relationship(back_populates="participants")


class GroupStreak(Base):
    """A shared walking streak among friends. The streak value is DERIVED from walks
    (consecutive days on which *every* member walked) — no counter to drift, no cron."""

    __tablename__ = "group_streaks"

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    creator_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )

    members: Mapped[list[GroupStreakMember]] = relationship(
        back_populates="streak", cascade="all, delete-orphan"
    )


class GroupStreakMember(Base):
    __tablename__ = "group_streak_members"
    __table_args__ = (UniqueConstraint("streak_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(SAUuid, primary_key=True, default=uuid.uuid4)
    streak_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("group_streaks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        SAUuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        SADateTime(timezone=True), default=_now, nullable=False
    )

    streak: Mapped[GroupStreak] = relationship(back_populates="members")
