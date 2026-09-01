"""Materialize approved story scenes without deleting historical versions.

Revision ID: 0027_story_scene_prompts
Revises: 0026_canvas_node_archives
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027_story_scene_prompts"
down_revision: str | Sequence[str] | None = "0026_canvas_node_archives"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    op.drop_constraint("uq_scenes_run_order", "scenes", schema=schema, type_="unique")
    op.add_column(
        "scenes",
        sa.Column("story_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column("scenes", sa.Column("scene_key", sa.String(length=80)), schema=schema)
    op.add_column(
        "scenes",
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        schema=schema,
    )
    op.add_column("scenes", sa.Column("stale_reason", sa.Text()), schema=schema)
    op.create_foreign_key(
        "fk_scenes_story_revision",
        "scenes",
        "story_revisions",
        ["story_revision_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_scenes_story_revision_key",
        "scenes",
        ["story_revision_id", "scene_key"],
        schema=schema,
    )
    op.create_index(
        "uq_scenes_active_run_order",
        "scenes",
        ["production_run_id", "sort_order"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("active = true"),
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("uq_scenes_active_run_order", table_name="scenes", schema=schema)
    op.drop_constraint(
        "uq_scenes_story_revision_key", "scenes", schema=schema, type_="unique"
    )
    op.drop_constraint("fk_scenes_story_revision", "scenes", schema=schema, type_="foreignkey")
    op.drop_column("scenes", "stale_reason", schema=schema)
    op.drop_column("scenes", "active", schema=schema)
    op.drop_column("scenes", "scene_key", schema=schema)
    op.drop_column("scenes", "story_revision_id", schema=schema)
    op.create_unique_constraint(
        "uq_scenes_run_order",
        "scenes",
        ["production_run_id", "sort_order"],
        schema=schema,
    )
