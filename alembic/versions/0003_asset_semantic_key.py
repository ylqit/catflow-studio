"""为资产增加精确语义寻址键。

Revision ID: 0003_asset_semantic_key
Revises: 0002_multimodal_input
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_asset_semantic_key"
down_revision: str | Sequence[str] | None = "0002_multimodal_input"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "assets",
        sa.Column("semantic_key", sa.String(160), nullable=True),
        schema=schema,
    )
    # 只回填能够从已批准元数据确定的视角。其余历史资产隔离为legacy键，
    # 新Run的精确选择器不会把它们当作活动Canon或元素素材。
    op.execute(
        sa.text(
            f"""
            UPDATE {schema}.assets
            SET semantic_key = CASE
                WHEN role IN ('person', 'cat')
                 AND (metadata_json::jsonb)->>'referenceView'
                     IN ('front', 'side', 'back')
                    THEN role || ':' || ((metadata_json::jsonb)->>'referenceView')
                ELSE 'legacy:' || id::text
            END
            WHERE semantic_key IS NULL
            """
        )
    )
    op.create_index(
        "ix_assets_semantic_selection",
        "assets",
        ["scope", "semantic_key", "status", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index(
        "ix_assets_semantic_selection",
        table_name="assets",
        schema=schema,
    )
    op.drop_column("assets", "semantic_key", schema=schema)
