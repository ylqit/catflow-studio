"""Run增加流水线设置列，支撑分阶段自动/手动推进与持久化付费授权。

Revision ID: 0007_pipeline_settings
Revises: 0006_core_simplification
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_pipeline_settings"
down_revision: str | Sequence[str] | None = "0006_core_simplification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def upgrade() -> None:
    op.add_column(
        "production_runs",
        sa.Column(
            "pipeline_settings_json",
            postgresql.JSONB(),
            nullable=True,
        ),
        schema=_schema(),
    )


def downgrade() -> None:
    op.drop_column("production_runs", "pipeline_settings_json", schema=_schema())
