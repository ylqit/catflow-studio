"""Add explicit Provider cancellation lifecycle states.

Revision ID: 0031_workflow_task_cancellation
Revises: 0030_generation_reference_lineage
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0031_workflow_task_cancellation"
down_revision: str | Sequence[str] | None = "0030_generation_reference_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    op.drop_constraint(
        "ck_workflow_steps_status",
        "workflow_steps",
        schema=schema,
        type_="check",
    )
    op.create_check_constraint(
        "ck_workflow_steps_status",
        "workflow_steps",
        "status IN ('pending', 'submitting', 'submission_unknown', 'cancelling', "
        "'cancellation_unknown', 'queued', 'running', 'awaiting_review', "
        "'succeeded', 'failed', 'expired', 'cancelled')",
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        f"UPDATE {schema}.workflow_steps "
        "SET status = 'submission_unknown' "
        "WHERE status IN ('cancelling', 'cancellation_unknown')"
    )
    op.drop_constraint(
        "ck_workflow_steps_status",
        "workflow_steps",
        schema=schema,
        type_="check",
    )
    op.create_check_constraint(
        "ck_workflow_steps_status",
        "workflow_steps",
        "status IN ('pending', 'submitting', 'submission_unknown', 'queued', "
        "'running', 'awaiting_review', 'succeeded', 'failed', 'expired', 'cancelled')",
        schema=schema,
    )
