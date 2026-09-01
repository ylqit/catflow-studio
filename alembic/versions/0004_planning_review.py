"""增加导演自动修复后的人工规划审核状态。

Revision ID: 0004_planning_review
Revises: 0003_asset_semantic_key
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_planning_review"
down_revision: str | Sequence[str] | None = "0003_asset_semantic_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def upgrade() -> None:
    schema = _schema()
    op.drop_constraint(
        "ck_production_runs_status",
        "production_runs",
        schema=schema,
        type_="check",
    )
    op.create_check_constraint(
        "ck_production_runs_status",
        "production_runs",
        "status IN ('draft','planning_review','planned','generating','reviewing',"
        "'ready','delivered','failed','archived')",
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    # 降级环境无法表达planning_review；保留记录但归入通用failed状态。
    op.execute(
        sa.text(
            f"UPDATE {schema}.production_runs "
            "SET status = 'failed' WHERE status = 'planning_review'"
        )
    )
    op.drop_constraint(
        "ck_production_runs_status",
        "production_runs",
        schema=schema,
        type_="check",
    )
    op.create_check_constraint(
        "ck_production_runs_status",
        "production_runs",
        "status IN ('draft','planned','generating','reviewing','ready',"
        "'delivered','failed','archived')",
        schema=schema,
    )
