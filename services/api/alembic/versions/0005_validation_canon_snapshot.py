"""Freeze the production Canon in every newly authorized validation run.

Revision ID: 0005_validation_canon_snapshot
Revises: 0004_shared_environment_presets
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_validation_canon_snapshot"
down_revision: str | Sequence[str] | None = "0004_shared_environment_presets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.add_column(
        "validation_runs",
        sa.Column("canon_snapshot_json", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )
    # A historical authorization cannot be reconstructed honestly after the Canon
    # contract changes. Preserve it for audit, but make it impossible to spend.
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.validation_runs "
            "SET status = 'cancelled' WHERE canon_snapshot_json IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("validation_runs", "canon_snapshot_json", schema=SCHEMA)
