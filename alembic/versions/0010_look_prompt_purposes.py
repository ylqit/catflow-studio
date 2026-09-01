"""为日内定妆图增加独立且可审计的Prompt用途。

Revision ID: 0010_look_prompt_purposes
Revises: 0009_storyboard_prompt_purposes
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_look_prompt_purposes"
down_revision: str | Sequence[str] | None = "0009_storyboard_prompt_purposes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"
OLD_PURPOSES = ("director", "storyboard", "storyboard_review", "video", "review")
NEW_PURPOSES = (
    "director",
    "look",
    "look_review",
    "storyboard",
    "storyboard_review",
    "video",
    "review",
)


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


def _replace_constraint(purposes: tuple[str, ...]) -> None:
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
    _assert_known_purposes(OLD_PURPOSES)
    _replace_constraint(NEW_PURPOSES)


def downgrade() -> None:
    _assert_known_purposes(OLD_PURPOSES)
    _replace_constraint(OLD_PURPOSES)
