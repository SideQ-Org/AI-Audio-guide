"""narration_samples + interest_signals (Block 4 §D2, Phase 0)

Revision ID: 0008_narration_samples
Revises: 0007_walk_summary
Create Date: 2026-07-16

Adds the self-improvement corpus tables: narration_samples (a narrated blurb + the FACTS
and full context that produced it — the interestingness eval corpus / groundedness source)
and interest_signals (real interest signals: follow-up/skip/…). RLS for both is applied
separately via db/rls.sql after `alembic upgrade head`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_narration_samples"
down_revision: str | Sequence[str] | None = "0007_walk_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "narration_samples",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("walk_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("place_id", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("significance", sa.String(length=16), nullable=True),
        sa.Column("facts", sa.Text(), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("narration", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["walk_id"],
            ["walks.id"],
            name=op.f("fk_narration_samples_walk_id_walks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_narration_samples")),
    )
    op.create_index(
        op.f("ix_narration_samples_walk_id"), "narration_samples", ["walk_id"], unique=False
    )
    op.create_index(
        op.f("ix_narration_samples_user_id"), "narration_samples", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_narration_samples_created_at"),
        "narration_samples",
        ["created_at"],
        unique=False,
    )
    op.create_table(
        "interest_signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("walk_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("weight", sa.Double(), nullable=False),
        sa.Column("place_id", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("significance", sa.String(length=16), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interest_signals")),
    )
    op.create_index(
        op.f("ix_interest_signals_user_id"), "interest_signals", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_interest_signals_walk_id"), "interest_signals", ["walk_id"], unique=False
    )
    op.create_index(
        op.f("ix_interest_signals_created_at"),
        "interest_signals",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_interest_signals_created_at"), table_name="interest_signals")
    op.drop_index(op.f("ix_interest_signals_walk_id"), table_name="interest_signals")
    op.drop_index(op.f("ix_interest_signals_user_id"), table_name="interest_signals")
    op.drop_table("interest_signals")
    op.drop_index(op.f("ix_narration_samples_created_at"), table_name="narration_samples")
    op.drop_index(op.f("ix_narration_samples_user_id"), table_name="narration_samples")
    op.drop_index(op.f("ix_narration_samples_walk_id"), table_name="narration_samples")
    op.drop_table("narration_samples")
