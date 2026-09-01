"""收敛为故事板优先流程，并让流水线配置成为新Run必填状态。

Revision ID: 0008_storyboard_first_core
Revises: 0007_pipeline_settings
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_storyboard_first_core"
down_revision: str | Sequence[str] | None = "0007_pipeline_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def upgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(
            f"UPDATE {schema}.production_runs "
            "SET pipeline_settings_json = '{}'::jsonb "
            "WHERE pipeline_settings_json IS NULL"
        )
    )
    op.alter_column(
        "production_runs",
        "pipeline_settings_json",
        schema=schema,
        existing_type=postgresql.JSONB(),
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )


def downgrade() -> None:
    op.alter_column(
        "production_runs",
        "pipeline_settings_json",
        schema=_schema(),
        existing_type=postgresql.JSONB(),
        nullable=True,
        server_default=None,
    )
