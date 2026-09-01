"""Add the minimal creator workflow truth.

Revision ID: 0032_creator_core
Revises: 0031_workflow_task_cancellation
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_creator_core"
down_revision: str | Sequence[str] | None = "0031_workflow_task_cancellation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "creator_project_states",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("brief_body", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "story_candidates_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "current_story_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("target_duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("quality_tier", sa.String(length=24), nullable=False),
        sa.Column(
            "reference_bindings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name="ck_creator_project_states_version"),
        sa.CheckConstraint(
            "target_duration_seconds BETWEEN 1 AND 360",
            name="ck_creator_project_states_duration",
        ),
        sa.CheckConstraint(
            "aspect_ratio IN ('9:16', '16:9', '1:1')",
            name="ck_creator_project_states_aspect_ratio",
        ),
        sa.CheckConstraint(
            "quality_tier IN ('quick', 'standard', 'quality')",
            name="ck_creator_project_states_quality_tier",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            [f"{schema}.production_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id"),
        schema=schema,
    )
    op.create_table(
        "creator_shots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("scene_label", sa.String(length=160), nullable=True),
        sa.Column(
            "reference_bindings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("prompt_draft", sa.Text(), nullable=True),
        sa.Column("selected_video_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("sort_order BETWEEN 1 AND 6", name="ck_creator_shots_order"),
        sa.CheckConstraint("version >= 1", name="ck_creator_shots_version"),
        sa.CheckConstraint(
            "duration_seconds BETWEEN 1 AND 60", name="ck_creator_shots_duration"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], [f"{schema}.production_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["selected_video_asset_id"], [f"{schema}.assets.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "sort_order", name="uq_creator_shots_project_order"),
        schema=schema,
    )
    op.create_index(
        "ix_creator_shots_project",
        "creator_shots",
        ["project_id", "sort_order"],
        unique=False,
        schema=schema,
    )
    op.create_table(
        "generation_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("creator_shot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column(
            "ordered_references_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "provider_config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("estimated_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('story_text', 'image', 'video', 'video_edit', 'composition')",
            name="ck_generation_snapshots_kind",
        ),
        sa.CheckConstraint(
            "estimated_cost_micros IS NULL OR estimated_cost_micros >= 0",
            name="ck_generation_snapshots_cost",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], [f"{schema}.production_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["creator_shot_id"], [f"{schema}.creator_shots.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=schema,
    )
    op.create_index(
        "ix_generation_snapshots_project_created",
        "generation_snapshots",
        ["project_id", "created_at"],
        unique=False,
        schema=schema,
    )
    op.add_column(
        "workflow_steps",
        sa.Column("creator_shot_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "workflow_steps",
        sa.Column("generation_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_workflow_steps_creator_shot",
        "workflow_steps",
        "creator_shots",
        ["creator_shot_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_workflow_steps_generation_snapshot",
        "workflow_steps",
        "generation_snapshots",
        ["generation_snapshot_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_workflow_steps_generation_snapshot",
        "workflow_steps",
        ["generation_snapshot_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("generation_snapshot_id IS NOT NULL"),
    )
    op.add_column(
        "assets",
        sa.Column("creator_shot_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.add_column(
        "assets",
        sa.Column("generation_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_assets_creator_shot",
        "assets",
        "creator_shots",
        ["creator_shot_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_assets_generation_snapshot",
        "assets",
        "generation_snapshots",
        ["generation_snapshot_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_assets_creator_shot_role",
        "assets",
        ["creator_shot_id", "role", "created_at"],
        unique=False,
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ix_assets_creator_shot_role", table_name="assets", schema=schema)
    op.drop_constraint("fk_assets_generation_snapshot", "assets", schema=schema, type_="foreignkey")
    op.drop_constraint("fk_assets_creator_shot", "assets", schema=schema, type_="foreignkey")
    op.drop_column("assets", "generation_snapshot_id", schema=schema)
    op.drop_column("assets", "creator_shot_id", schema=schema)
    op.drop_index(
        "uq_workflow_steps_generation_snapshot", table_name="workflow_steps", schema=schema
    )
    op.drop_constraint(
        "fk_workflow_steps_generation_snapshot",
        "workflow_steps",
        schema=schema,
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_workflow_steps_creator_shot",
        "workflow_steps",
        schema=schema,
        type_="foreignkey",
    )
    op.drop_column("workflow_steps", "generation_snapshot_id", schema=schema)
    op.drop_column("workflow_steps", "creator_shot_id", schema=schema)
    op.drop_index(
        "ix_generation_snapshots_project_created",
        table_name="generation_snapshots",
        schema=schema,
    )
    op.drop_table("generation_snapshots", schema=schema)
    op.drop_index("ix_creator_shots_project", table_name="creator_shots", schema=schema)
    op.drop_table("creator_shots", schema=schema)
    op.drop_table("creator_project_states", schema=schema)
