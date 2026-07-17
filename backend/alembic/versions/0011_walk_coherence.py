"""walk-level coherence columns on walk_quality (Block 4 coherence extension)

Revision ID: 0011_walk_coherence
Revises: 0010_block4_tier
Create Date: 2026-07-17

Cross-object coherence (бесшовность / связность / интеграция в арку) is scored per walk by the
quality-worker sidecar. Adds three nullable columns to ``walk_quality``; existing rows stay valid
(NULL = scored before this metric existed). Additive only — the live backend never reads these.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_walk_coherence"
down_revision: str | Sequence[str] | None = "0010_block4_tier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("walk_quality", sa.Column("coherence_mean", sa.Double(), nullable=True))
    op.add_column("walk_quality", sa.Column("seamlessness", sa.Double(), nullable=True))
    op.add_column("walk_quality", sa.Column("arc_coherence", sa.Double(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("walk_quality", "arc_coherence")
    op.drop_column("walk_quality", "seamlessness")
    op.drop_column("walk_quality", "coherence_mean")
