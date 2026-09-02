"""Enforce four-second video edits and retain legacy repair history.

Revision ID: 0013_video_edit_range
Revises: 0012_video_edit_preview
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_video_edit_range"
down_revision: str | Sequence[str] | None = "0012_video_edit_preview"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.add_column(
        "video_repairs",
        sa.Column(
            "selection_policy_version",
            sa.SmallInteger(),
            nullable=False,
            server_default=sa.text("2"),
        ),
        schema=SCHEMA,
    )
    op.execute(f"UPDATE {SCHEMA}.video_repairs SET selection_policy_version = 1")
    op.execute(
        f"""
        UPDATE {SCHEMA}.video_repairs
        SET status = 'outdated'
        WHERE issue_end_frame - issue_start_frame < 96
          AND status IN ('draft', 'generating', 'candidate_ready')
        """
    )
    op.drop_constraint(
        "ck_video_repairs_edit_intent", "video_repairs", schema=SCHEMA, type_="check"
    )
    op.alter_column(
        "video_repairs",
        "edit_intent",
        schema=SCHEMA,
        existing_type=sa.String(length=24),
        nullable=True,
        server_default=None,
    )
    op.create_check_constraint(
        "ck_video_repairs_selection_policy",
        "video_repairs",
        "selection_policy_version IN (1, 2)",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_video_repairs_v2_issue_duration",
        "video_repairs",
        "selection_policy_version = 1 OR "
        "issue_end_frame - issue_start_frame BETWEEN 96 AND 360",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_video_repairs_v2_issue_duration",
        "video_repairs",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_video_repairs_selection_policy",
        "video_repairs",
        schema=SCHEMA,
        type_="check",
    )
    op.execute(
        f"UPDATE {SCHEMA}.video_repairs SET edit_intent = 'action' WHERE edit_intent IS NULL"
    )
    op.alter_column(
        "video_repairs",
        "edit_intent",
        schema=SCHEMA,
        existing_type=sa.String(length=24),
        nullable=False,
        server_default=sa.text("'action'"),
    )
    op.create_check_constraint(
        "ck_video_repairs_edit_intent",
        "video_repairs",
        "edit_intent IN ('action','character','object','environment','style')",
        schema=SCHEMA,
    )
    op.drop_column("video_repairs", "selection_policy_version", schema=SCHEMA)
