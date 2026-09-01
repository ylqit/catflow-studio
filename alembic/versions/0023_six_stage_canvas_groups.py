"""Add six-stage character design and LibTV-style canvas groups.

Revision ID: 0023_six_stage_canvas_groups
Revises: 0022_healing_child_cat_recipe
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_six_stage_canvas_groups"
down_revision: str | Sequence[str] | None = "0022_healing_child_cat_recipe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _json() -> postgresql.JSONB:
    return postgresql.JSONB()


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "production_recipe_instances",
        sa.Column(
            "lifecycle_status",
            sa.String(length=24),
            nullable=False,
            server_default="active",
        ),
        schema=schema,
    )
    op.add_column(
        "production_recipe_instances",
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    op.create_check_constraint(
        "ck_production_recipe_instances_lifecycle",
        "production_recipe_instances",
        "lifecycle_status IN ('active', 'archived')",
        schema=schema,
    )

    op.create_table(
        "character_design_revisions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_recipe_instance_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_recipe_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_story_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.story_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False, unique=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="generating"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "production_recipe_instance_id",
            "revision",
            name="uq_character_design_revisions_instance_revision",
        ),
        sa.CheckConstraint(
            "status IN ('generating', 'awaiting_review', 'approved', 'stale')",
            name="ck_character_design_revisions_status",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_character_design_revisions_current",
        "character_design_revisions",
        ["production_recipe_instance_id", "revision"],
        schema=schema,
    )
    op.create_table(
        "character_design_assets",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "character_design_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.character_design_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.assets.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("slot", sa.String(length=24), nullable=False),
        sa.Column("candidate_index", sa.SmallInteger(), nullable=False),
        sa.Column("semantic_role", sa.String(length=40), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "character_design_revision_id",
            "slot",
            "candidate_index",
            name="uq_character_design_assets_slot_candidate",
        ),
        sa.CheckConstraint(
            "slot IN ('child', 'cat', 'pair_scale')",
            name="ck_character_design_assets_slot",
        ),
        sa.CheckConstraint("candidate_index >= 1", name="ck_character_design_assets_candidate"),
        schema=schema,
    )
    op.create_index(
        "ix_character_design_assets_revision_slot",
        "character_design_assets",
        ["character_design_revision_id", "slot"],
        schema=schema,
    )

    op.create_table(
        "canvas_groups",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "production_recipe_instance_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_recipe_instances.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "parent_group_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_groups.id", ondelete="CASCADE"),
        ),
        sa.Column("group_type", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column(
            "lifecycle_status",
            sa.String(length=24),
            nullable=False,
            server_default="active",
        ),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#7c9cff"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("data_json", _json(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("group_type IN ('recipe', 'shot')", name="ck_canvas_groups_type"),
        sa.CheckConstraint(
            "lifecycle_status IN ('active', 'detached')",
            name="ck_canvas_groups_lifecycle",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_canvas_groups_run_status",
        "canvas_groups",
        ["production_run_id", "lifecycle_status"],
        schema=schema,
    )
    op.create_table(
        "canvas_group_members",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "group_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "canvas_node_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("group_id", "canvas_node_id", name="uq_canvas_group_members_node"),
        schema=schema,
    )
    op.create_index(
        "ix_canvas_group_members_node",
        "canvas_group_members",
        ["canvas_node_id"],
        schema=schema,
    )
    op.create_table(
        "canvas_group_templates",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("template_key", sa.String(length=120), nullable=False, unique=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("definition_json", _json(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=schema,
    )

    op.execute(
        sa.text(
            f"""
            INSERT INTO {schema}.canvas_groups (
                id, production_run_id, production_recipe_instance_id,
                group_type, title, lifecycle_status, color, revision, data_json
            )
            SELECT
                md5('canvas-group:' || pri.id::text)::uuid,
                pri.production_run_id,
                pri.id,
                'recipe',
                '一人一猫治愈短片',
                'active',
                '#7c9cff',
                1,
                jsonb_build_object('recipeKey', pri.recipe_key, 'migrated', true)
            FROM {schema}.production_recipe_instances pri
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {schema}.canvas_group_members (id, group_id, canvas_node_id, sort_order)
            SELECT
                md5('canvas-group-member:' || node.id::text)::uuid,
                md5('canvas-group:' || pri.id::text)::uuid,
                node.id,
                row_number() OVER (PARTITION BY pri.id ORDER BY node.created_at, node.id)
            FROM {schema}.production_recipe_instances pri
            JOIN {schema}.canvas_graph_nodes node
              ON node.production_run_id = pri.production_run_id
             AND node.node_type <> 'RecipeGroupNode'
            ON CONFLICT (group_id, canvas_node_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("canvas_group_templates", schema=schema)
    op.drop_index("ix_canvas_group_members_node", table_name="canvas_group_members", schema=schema)
    op.drop_table("canvas_group_members", schema=schema)
    op.drop_index("ix_canvas_groups_run_status", table_name="canvas_groups", schema=schema)
    op.drop_table("canvas_groups", schema=schema)
    op.drop_index(
        "ix_character_design_assets_revision_slot",
        table_name="character_design_assets",
        schema=schema,
    )
    op.drop_table("character_design_assets", schema=schema)
    op.drop_index(
        "ix_character_design_revisions_current",
        table_name="character_design_revisions",
        schema=schema,
    )
    op.drop_table("character_design_revisions", schema=schema)
    op.drop_constraint(
        "ck_production_recipe_instances_lifecycle",
        "production_recipe_instances",
        type_="check",
        schema=schema,
    )
    op.drop_column("production_recipe_instances", "archived_at", schema=schema)
    op.drop_column("production_recipe_instances", "lifecycle_status", schema=schema)
