"""walk_quality (Block 4 Phase 4)

Revision ID: 0009_walk_quality
Revises: 0008_narration_samples
Create Date: 2026-07-16

Per-walk interestingness score written by the quality-worker sidecar. One row per walk
(unique walk_id = idempotency marker). RLS applied separately via db/rls.sql.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_walk_quality"
down_revision: str | Sequence[str] | None = "0008_narration_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "walk_quality",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("walk_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n_blurbs", sa.Integer(), nullable=False),
        sa.Column("score", sa.Double(), nullable=False),
        sa.Column("interest_mean", sa.Double(), nullable=False),
        sa.Column("grounded_rate", sa.Double(), nullable=False),
        sa.Column("cliche_rate", sa.Double(), nullable=False),
        sa.Column("novelty_mean", sa.Double(), nullable=False),
        sa.Column("distinct_2", sa.Double(), nullable=False),
        sa.Column("used_judge", sa.Boolean(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["walk_id"],
            ["walks.id"],
            name=op.f("fk_walk_quality_walk_id_walks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_walk_quality")),
        sa.UniqueConstraint("walk_id", name=op.f("uq_walk_quality_walk_id")),
    )
    op.create_index(
        op.f("ix_walk_quality_walk_id"), "walk_quality", ["walk_id"], unique=False
    )
    op.create_index(
        op.f("ix_walk_quality_user_id"), "walk_quality", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_walk_quality_computed_at"), "walk_quality", ["computed_at"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_walk_quality_computed_at"), table_name="walk_quality")
    op.drop_index(op.f("ix_walk_quality_user_id"), table_name="walk_quality")
    op.drop_index(op.f("ix_walk_quality_walk_id"), table_name="walk_quality")
    op.drop_table("walk_quality")
