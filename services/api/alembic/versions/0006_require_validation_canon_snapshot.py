"""Require Canon evidence for every spendable validation run.

Revision ID: 0006_validation_canon_required
Revises: 0005_validation_canon_snapshot
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_validation_canon_required"
down_revision: str | Sequence[str] | None = "0005_validation_canon_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.create_check_constraint(
        "ck_validation_runs_canon_snapshot",
        "validation_runs",
        "status = 'cancelled' OR canon_snapshot_json IS NOT NULL",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_validation_runs_canon_snapshot",
        "validation_runs",
        type_="check",
        schema=SCHEMA,
    )
