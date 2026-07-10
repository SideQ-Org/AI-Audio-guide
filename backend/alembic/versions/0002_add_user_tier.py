"""add user subscription tier

Revision ID: 0002_add_user_tier
Revises: 0001_initial_accounts
Create Date: 2026-07-03

Adds the free/paid subscription columns to ``users`` (feature: account tiers).
``tier`` is written server-side only (billing receipt verification); RLS is
unchanged because clients read tier through ``GET /me`` (the backend, service role),
never directly. Apply on prod via the raw-asyncpg path (the Supabase pooler hangs the
SQLAlchemy engine — see PROD_INFRA.md §3).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_add_user_tier"
down_revision = "0001_initial_accounts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("tier", sa.String(length=16), nullable=False, server_default="free"),
    )
    op.add_column(
        "users",
        sa.Column("subscription_platform", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("subscription_product", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("users", sa.Column("subscription_token", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "subscription_token")
    op.drop_column("users", "subscription_expires_at")
    op.drop_column("users", "subscription_product")
    op.drop_column("users", "subscription_platform")
    op.drop_column("users", "tier")
