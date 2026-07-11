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
