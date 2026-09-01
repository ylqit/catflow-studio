"""Add durable task progress and monotonic canvas event cursors.

Revision ID: 0024_durable_task_events
Revises: 0023_six_stage_canvas_groups
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024_durable_task_events"
down_revision: str | Sequence[str] | None = "0023_six_stage_canvas_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "workflow_steps",
        sa.Column(
            "progress_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=schema,
    )
    op.add_column(
        "canvas_events",
        sa.Column(
            "sequence",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        schema=schema,
    )
    op.create_unique_constraint(
        "uq_canvas_events_sequence",
        "canvas_events",
        ["sequence"],
        schema=schema,
    )
    op.create_index(
        "ix_canvas_events_run_sequence",
        "canvas_events",
        ["production_run_id", "sequence"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ix_canvas_events_run_sequence", table_name="canvas_events", schema=schema)
    op.drop_constraint(
        "uq_canvas_events_sequence",
        "canvas_events",
        type_="unique",
        schema=schema,
    )
    op.drop_column("canvas_events", "sequence", schema=schema)
    op.drop_column("workflow_steps", "progress_json", schema=schema)
