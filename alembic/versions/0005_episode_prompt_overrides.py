"""Episode增加可编辑Prompt覆盖列，支撑主题创作台的关键帧重生成。

Revision ID: 0005_episode_prompt_overrides
Revises: 0004_planning_review
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_episode_prompt_overrides"
down_revision: str | Sequence[str] | None = "0004_planning_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def upgrade() -> None:
    op.add_column(
        "episodes",
        sa.Column(
            "prompt_overrides_json",
            postgresql.JSONB(),
            nullable=True,
        ),
        schema=_schema(),
    )


def downgrade() -> None:
    op.drop_column("episodes", "prompt_overrides_json", schema=_schema())
