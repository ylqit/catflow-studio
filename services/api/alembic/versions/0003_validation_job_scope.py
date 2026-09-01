"""Prevent repeat paid calls for one validation project and call kind.

Revision ID: 0003_validation_job_scope
Revises: 0002_ark_validation_runs
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_validation_job_scope"
down_revision: str | Sequence[str] | None = "0002_ark_validation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.create_index(
        "uq_jobs_validation_project_kind",
        "jobs",
        ["validation_run_id", "project_id", "kind"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="validation_run_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_jobs_validation_project_kind",
        table_name="jobs",
        schema=SCHEMA,
    )
