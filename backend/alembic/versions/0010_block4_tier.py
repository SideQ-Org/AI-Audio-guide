"""tier on narration_samples + walk_quality (Block 4 tier-awareness)

Revision ID: 0010_block4_tier
Revises: 0009_walk_quality
Create Date: 2026-07-16

Free and paid walks use different generator models (DeepSeek vs a premium model), so quality
is scored and optimized PER TIER. Adds a ``tier`` column to both Block 4 tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_block4_tier"
down_revision: str | Sequence[str] | None = "0009_walk_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "narration_samples",
        sa.Column("tier", sa.String(length=8), nullable=False, server_default="free"),
    )
    op.add_column(
        "walk_quality",
        sa.Column("tier", sa.String(length=8), nullable=False, server_default="free"),
    )
    op.create_index(op.f("ix_walk_quality_tier"), "walk_quality", ["tier"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_walk_quality_tier"), table_name="walk_quality")
    op.drop_column("walk_quality", "tier")
    op.drop_column("narration_samples", "tier")
