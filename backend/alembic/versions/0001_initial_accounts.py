"""initial accounts + walk history schema

Revision ID: 0001_initial_accounts
Revises:
Create Date: 2026-07-02

Creates users / identities / walks / walk_events (design §4). Postgres Row-Level
Security is applied separately by db/rls.sql after this migration.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_initial_accounts"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_uid", sa.String(length=320), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_identities_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_identities"),
        sa.UniqueConstraint(
            "provider", "provider_uid", name="uq_identities_provider"
        ),
    )
    op.create_index("ix_identities_user_id", "identities", ["user_id"])

    op.create_table(
        "walks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("sid", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("city", sa.String(length=200), nullable=True),
        sa.Column("district", sa.String(length=200), nullable=True),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("object_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_walks_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_walks"),
    )
    op.create_index("ix_walks_sid", "walks", ["sid"])
    # the history list query: a user's walks, newest first
    op.create_index(
        "ix_walks_user_id_started_at", "walks", ["user_id", sa.text("started_at DESC")]
    )

    op.create_table(
        "walk_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("walk_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("place_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=400), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("lat", sa.Double(), nullable=False),
        sa.Column("lon", sa.Double(), nullable=False),
        sa.Column("significance", sa.String(length=16), nullable=False),
        sa.Column("narration", sa.Text(), nullable=True),
        sa.Column("said_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["walk_id"], ["walks.id"], name="fk_walk_events_walk_id_walks",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_walk_events"),
        sa.UniqueConstraint("walk_id", "seq", name="uq_walk_events_walk_id"),
    )
    op.create_index("ix_walk_events_walk_id", "walk_events", ["walk_id"])


def downgrade() -> None:
    op.drop_index("ix_walk_events_walk_id", table_name="walk_events")
    op.drop_table("walk_events")
    op.drop_index("ix_walks_user_id_started_at", table_name="walks")
    op.drop_index("ix_walks_sid", table_name="walks")
    op.drop_table("walks")
    op.drop_index("ix_identities_user_id", table_name="identities")
    op.drop_table("identities")
    op.drop_table("users")
