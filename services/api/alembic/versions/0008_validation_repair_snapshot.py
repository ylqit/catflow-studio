"""Freeze the authorized segment-repair topic, range, and prompt.

Revision ID: 0008_validation_repair_snapshot
Revises: 0007_video_repairs_edl_v2
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_validation_repair_snapshot"
down_revision: str | Sequence[str] | None = "0007_video_repairs_edl_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"
REPAIR_SNAPSHOT = {
    "topic": "雨天擦爪",
    "issueRange": {"startFrame": 96, "endFrame": 192},
    "prompt": ("孩子蹲下，用软毛巾逐只擦干猫爪；猫咪自然抬爪配合，湿爪和地面水印明显减少。"),
}


def upgrade() -> None:
    op.add_column(
        "validation_runs",
        sa.Column("repair_snapshot_json", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.validation_runs "
            "SET repair_snapshot_json = CAST(:snapshot AS jsonb) "
            "WHERE repair_snapshot_json IS NULL"
        ).bindparams(snapshot=json.dumps(REPAIR_SNAPSHOT, ensure_ascii=False))
    )
    op.alter_column(
        "validation_runs",
        "repair_snapshot_json",
        existing_type=postgresql.JSONB(),
        nullable=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("validation_runs", "repair_snapshot_json", schema=SCHEMA)
