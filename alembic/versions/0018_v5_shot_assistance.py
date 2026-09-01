"""Add shot draft revisions and scene-look usage strategies.

Revision ID: 0018_v5_shot_assistance
Revises: 0017_v5_visual_profile
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_v5_shot_assistance"
down_revision: str | Sequence[str] | None = "0017_v5_visual_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "shot_cards",
        sa.Column("draft_revision", sa.Integer(), nullable=False, server_default="1"),
        schema=schema,
    )
    op.add_column(
        "shot_cards",
        sa.Column(
            "scene_look_usage",
            sa.String(length=32),
            nullable=False,
            server_default="appearance_only",
        ),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"UPDATE {schema}.shot_cards "
            "SET scene_look_usage = CASE "
            "WHEN use_scene_look THEN 'appearance_only' ELSE 'off' END"
        )
    )
    op.create_check_constraint(
        "ck_shot_cards_scene_look_usage",
        "shot_cards",
        "scene_look_usage IN ('off', 'appearance_only', 'full_reference', 'derive_anchor')",
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_constraint(
        "ck_shot_cards_scene_look_usage",
        "shot_cards",
        type_="check",
        schema=schema,
    )
    op.drop_column("shot_cards", "scene_look_usage", schema=schema)
    op.drop_column("shot_cards", "draft_revision", schema=schema)
