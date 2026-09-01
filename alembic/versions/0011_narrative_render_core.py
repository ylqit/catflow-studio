"""收敛为导演、视觉锚点与长短视频渲染内核。

Revision ID: 0011_narrative_render_core
Revises: 0010_look_prompt_purposes
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_narrative_render_core"
down_revision: str | Sequence[str] | None = "0010_look_prompt_purposes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"
OLD_PURPOSES = (
    "director",
    "look",
    "look_review",
    "storyboard",
    "storyboard_review",
    "video",
    "review",
)
NEW_PURPOSES = ("director", "image", "video", "review")


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA)


def _assert_production_history_cleared() -> None:
    runs = sa.table("production_runs", sa.column("id", sa.Uuid()), schema=_schema())
    count = op.get_bind().execute(sa.select(sa.func.count()).select_from(runs)).scalar_one()
    if count:
        raise RuntimeError("0011 只支持全新的生产内核；请先按诊断清单清理全部历史 Run，再执行迁移")


def _replace_prompt_constraint(purposes: tuple[str, ...]) -> None:
    op.drop_constraint(
        "ck_prompt_records_purpose",
        "prompt_records",
        type_="check",
        schema=_schema(),
    )
    values = ", ".join(repr(item) for item in purposes)
    op.create_check_constraint(
        "ck_prompt_records_purpose",
        "prompt_records",
        f"purpose IN ({values})",
        schema=_schema(),
    )


def upgrade() -> None:
    _assert_production_history_cleared()
    _replace_prompt_constraint(NEW_PURPOSES)


def downgrade() -> None:
    _assert_production_history_cleared()
    _replace_prompt_constraint(OLD_PURPOSES)
