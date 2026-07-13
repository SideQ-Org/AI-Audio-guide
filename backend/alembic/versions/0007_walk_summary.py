"""walk_summary

Revision ID: 0007_walk_summary
Revises: 0006_walk_shared
Create Date: 2026-07-13 08:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_walk_summary"
down_revision: str | Sequence[str] | None = "0006_walk_shared"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("walks", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("walks", "summary")
