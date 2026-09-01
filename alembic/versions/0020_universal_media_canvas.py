"""Add the universal business graph, product batches and video edit recipes.

Revision ID: 0020_universal_media_canvas
Revises: 0019_aigc_canvas_v2
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020_universal_media_canvas"
down_revision: str | Sequence[str] | None = "0019_aigc_canvas_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _json() -> postgresql.JSONB:
    return postgresql.JSONB()


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "production_runs",
        sa.Column(
            "canvas_template_key",
            sa.String(length=32),
            nullable=False,
            server_default="short_drama",
        ),
        schema=schema,
    )
    for name in (
        "universal_canvas_enabled",
        "product_ad_template_enabled",
        "video_edit_v2_enabled",
    ):
        op.add_column(
            "production_runs",
            sa.Column(
                name,
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            schema=schema,
        )
    _create_graph_tables(schema)
    _create_batch_table(schema)
    _create_video_edit_tables(schema)
    op.add_column(
        "assets",
        sa.Column("canvas_node_id", _uuid()),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_assets_canvas_node",
        "assets",
        "canvas_graph_nodes",
        ["canvas_node_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.drop_constraint("ck_assets_scope", "assets", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_assets_scope",
        "assets",
        "scope IN ('canon', 'project', 'scene', 'shot', 'canvas_node')",
        schema=schema,
    )


def _create_graph_tables(schema: str) -> None:
    op.create_table(
        "canvas_graph_nodes",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("node_type", sa.String(length=80), nullable=False),
        sa.Column("object_type", sa.String(length=80), nullable=False),
        sa.Column("object_id", _uuid()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
        sa.Column("data_json", _json(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=schema,
    )
    op.create_index(
        "ix_canvas_graph_nodes_run_type",
        "canvas_graph_nodes",
        ["production_run_id", "node_type"],
        schema=schema,
    )
    op.create_table(
        "canvas_graph_edges",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_node_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_port", sa.String(length=80), nullable=False),
        sa.Column(
            "target_node_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_port", sa.String(length=80), nullable=False),
        sa.Column("relation_type", sa.String(length=80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        _created_at(),
        sa.UniqueConstraint(
            "source_node_id",
            "source_port",
            "target_node_id",
            "target_port",
            name="uq_canvas_graph_edges_typed_connection",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_canvas_graph_edges_run",
        "canvas_graph_edges",
        ["production_run_id"],
        schema=schema,
    )
    op.create_table(
        "canvas_events",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("data_json", _json(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created_at(),
        schema=schema,
    )
    op.create_index(
        "ix_canvas_events_run_created",
        "canvas_events",
        ["production_run_id", "created_at"],
        schema=schema,
    )


def _create_batch_table(schema: str) -> None:
    op.create_table(
        "media_generation_batches",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "canvas_node_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_step_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.workflow_steps.id", ondelete="SET NULL"),
        ),
        sa.Column("media_kind", sa.String(length=24), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False, unique=True),
        sa.Column("input_json", _json(), nullable=False),
        sa.Column(
            "output_asset_ids_json",
            _json(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "candidate_count BETWEEN 1 AND 8", name="ck_media_batches_candidates"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_media_batches_run_status",
        "media_generation_batches",
        ["production_run_id", "status"],
        schema=schema,
    )


def _create_video_edit_tables(schema: str) -> None:
    op.create_table(
        "video_edit_recipes",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "canvas_node_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_asset_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.assets.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_recipe_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.video_edit_recipes.id", ondelete="SET NULL"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("compilation_json", _json()),
        sa.Column("estimated_cost_micros", sa.BigInteger()),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "end_ms - start_ms BETWEEN 500 AND 13000", name="ck_video_edit_interval"
        ),
        sa.UniqueConstraint(
            "canvas_node_id", "revision", name="uq_video_edit_node_revision"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_video_edit_recipes_run_status",
        "video_edit_recipes",
        ["production_run_id", "status"],
        schema=schema,
    )
    op.create_table(
        "video_edit_annotations",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "recipe_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.video_edit_recipes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("frame_timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("tool", sa.String(length=24), nullable=False),
        sa.Column("points_json", _json(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "recipe_id", "ordinal", name="uq_video_edit_annotations_order"
        ),
        schema=schema,
    )
    op.create_table(
        "video_edit_references",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "recipe_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.video_edit_recipes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.assets.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("semantic_role", sa.String(length=80), nullable=False),
        sa.Column("provider_included", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "recipe_id", "asset_id", name="uq_video_edit_reference_asset"
        ),
        sa.UniqueConstraint(
            "recipe_id", "ordinal", name="uq_video_edit_reference_order"
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_constraint("ck_assets_scope", "assets", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_assets_scope",
        "assets",
        "scope IN ('canon', 'project', 'scene', 'shot')",
        schema=schema,
    )
    op.drop_constraint("fk_assets_canvas_node", "assets", schema=schema, type_="foreignkey")
    op.drop_column("assets", "canvas_node_id", schema=schema)
    for table in (
        "video_edit_references",
        "video_edit_annotations",
        "video_edit_recipes",
        "media_generation_batches",
        "canvas_events",
        "canvas_graph_edges",
        "canvas_graph_nodes",
    ):
        op.drop_table(table, schema=schema)
    op.drop_column("production_runs", "canvas_template_key", schema=schema)
    for name in (
        "video_edit_v2_enabled",
        "product_ad_template_enabled",
        "universal_canvas_enabled",
    ):
        op.drop_column("production_runs", name, schema=schema)
