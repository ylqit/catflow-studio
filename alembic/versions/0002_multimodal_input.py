"""将Episode视觉策略升级为明确的Seedance输入模式。

Revision ID: 0002_multimodal_input
Revises: 0001_compact_workflow
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_multimodal_input"
down_revision: str | Sequence[str] | None = "0001_compact_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def upgrade() -> None:
    schema = _schema()
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(
            "episodes",
            schema=schema,
        )
    }
    if "visual_strategy" in columns and "video_input_mode" not in columns:
        op.alter_column(
            "episodes",
            "visual_strategy",
            new_column_name="video_input_mode",
            schema=schema,
        )
    op.execute(
        sa.text(
            f"""
            UPDATE {schema}.episodes
            SET video_input_mode = CASE video_input_mode
                WHEN 'direct_references' THEN 'multimodal_reference'
                WHEN 'first_frame' THEN 'strict_first_frame'
                WHEN 'first_last_frames' THEN 'strict_first_last'
                WHEN 'first_last_frame' THEN 'strict_first_last'
                ELSE video_input_mode
            END
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        sa.text(
            f"""
            UPDATE {schema}.episodes
            SET video_input_mode = CASE video_input_mode
                WHEN 'multimodal_reference' THEN 'direct_references'
                WHEN 'strict_first_frame' THEN 'first_frame'
                WHEN 'strict_first_last' THEN 'first_last_frames'
                ELSE video_input_mode
            END
            """
        )
    )
    columns = {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(
            "episodes",
            schema=schema,
        )
    }
    if "video_input_mode" in columns and "visual_strategy" not in columns:
        op.alter_column(
            "episodes",
            "video_input_mode",
            new_column_name="visual_strategy",
            schema=schema,
        )
