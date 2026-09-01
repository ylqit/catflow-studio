"""同步故事板生产链路实际使用的Prompt用途。

Revision ID: 0009_storyboard_prompt_purposes
Revises: 0008_storyboard_first_core
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_storyboard_prompt_purposes"
down_revision: str | Sequence[str] | None = "0008_storyboard_first_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"
PURPOSES = ("director", "storyboard", "storyboard_review", "video", "review")


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def _assert_known_purposes(allowed: tuple[str, ...]) -> None:
    table = sa.table("prompt_records", sa.column("purpose", sa.String()), schema=_schema())
    unknown = (
        op.get_bind()
        .execute(
            sa.select(table.c.purpose)
            .select_from(table)
            .where(table.c.purpose.not_in(allowed))
            .distinct()
        )
        .scalars()
        .all()
    )
    if unknown:
        raise RuntimeError(f"prompt_records存在未知purpose，拒绝迁移: {sorted(unknown)}")


def upgrade() -> None:
    _assert_known_purposes(("director", "image", "video", "review"))
    op.drop_constraint(
        "ck_prompt_records_purpose",
        "prompt_records",
        type_="check",
        schema=_schema(),
    )
    prompt_records = sa.table("prompt_records", sa.column("purpose", sa.String()), schema=_schema())
    # 0008及更早版本用image表示图片生成Prompt。新生产链路只有故事板图片，
    # 先完成语义迁移再收紧CHECK，避免旧记录因约束重建而成为非法数据。
    op.get_bind().execute(
        prompt_records.update()
        .where(prompt_records.c.purpose == "image")
        .values(purpose="storyboard")
    )
    op.create_check_constraint(
        "ck_prompt_records_purpose",
        "prompt_records",
        "purpose IN ('director', 'storyboard', 'storyboard_review', 'video', 'review')",
        schema=_schema(),
    )


def downgrade() -> None:
    _assert_known_purposes(("director", "video", "review"))
    op.drop_constraint(
        "ck_prompt_records_purpose",
        "prompt_records",
        type_="check",
        schema=_schema(),
    )
    op.create_check_constraint(
        "ck_prompt_records_purpose",
        "prompt_records",
        "purpose IN ('director', 'image', 'video', 'review')",
        schema=_schema(),
    )
