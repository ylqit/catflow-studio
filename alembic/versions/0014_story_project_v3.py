"""生活故事项目V3契约；保留V2为只读历史。

Revision ID: 0014_story_project_v3
Revises: 0013_canvas_video_sequences

本迁移只调整版本准入，不猜测、转换或补齐旧JSON。应用层仅允许V3继续生产，
V2记录保留供历史查询和审计。
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_story_project_v3"
down_revision: str | Sequence[str] | None = "0013_canvas_video_sequences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video")


def upgrade() -> None:
    schema = _schema()
    op.drop_constraint(
        "ck_production_runs_contract_version",
        "production_runs",
        type_="check",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_production_runs_contract_version",
        "production_runs",
        "contract_version IN (2, 3)",
        schema=schema,
    )
    op.alter_column(
        "production_runs",
        "contract_version",
        server_default=sa.text("3"),
        existing_type=sa.SmallInteger(),
        existing_nullable=False,
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    runs = sa.table(
        "production_runs",
        sa.column("contract_version", sa.SmallInteger()),
        schema=schema,
    )
    v3_count = (
        op.get_bind()
        .execute(sa.select(sa.func.count()).select_from(runs).where(runs.c.contract_version == 3))
        .scalar_one()
    )
    if v3_count:
        raise RuntimeError("存在V3生活故事项目，不能降级为仅支持V2的契约")
    op.drop_constraint(
        "ck_production_runs_contract_version",
        "production_runs",
        type_="check",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_production_runs_contract_version",
        "production_runs",
        "contract_version = 2",
        schema=schema,
    )
    op.alter_column(
        "production_runs",
        "contract_version",
        server_default=sa.text("2"),
        existing_type=sa.SmallInteger(),
        existing_nullable=False,
        schema=schema,
    )
