"""add walk route path

Revision ID: 0003_add_walk_path
Revises: 0002_add_user_tier
Create Date: 2026-07-07

Adds ``walks.path`` — the downsampled GPS breadcrumb ([[lat, lon], ...]) captured during
a walk so the history screen can draw the real route. Nullable: old walks have no path.
RLS is unchanged (same row ownership). Apply on prod via the raw-asyncpg path (the
Supabase pooler hangs the SQLAlchemy engine — see PROD_INFRA.md §3).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0003_add_walk_path"
down_revision = "0002_add_user_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("walks", sa.Column("path", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("walks", "path")
