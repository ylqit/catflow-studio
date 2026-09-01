"""Add LibTV-style node configuration, subject completion and recovery state.

Revision ID: 0021_libtv_subject_assistant
Revises: 0020_universal_media_canvas
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021_libtv_subject_assistant"
down_revision: str | Sequence[str] | None = "0020_universal_media_canvas"
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
    op.create_check_constraint(
        "ck_subjects_generic_kind",
        "subjects",
        "kind IN ('person', 'animal', 'object', 'location', 'style', 'product')",
        schema=schema,
    )
    op.create_check_constraint(
        "ck_subjects_generic_role",
        "subjects",
        "role IN ('protagonist', 'co_protagonist', 'support', 'prop', "
        "'environment', 'hero_product')",
        schema=schema,
    )
    op.add_column("canvas_layouts", sa.Column("failure_reason", sa.Text()), schema=schema)
    op.add_column(
        "canvas_layouts", sa.Column("last_confirmed_event_id", _uuid()), schema=schema
    )
    op.create_foreign_key(
        "fk_canvas_layouts_last_confirmed_event",
        "canvas_layouts",
        "canvas_events",
        ["last_confirmed_event_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_table(
        "subject_completion_runs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.subject_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workflow_step_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.workflow_steps.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "prompt_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.prompt_records.id", ondelete="SET NULL"),
        ),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("missing_fields_json", _json(), nullable=False),
        sa.Column("proposal_json", _json()),
        sa.Column("accepted_fields_json", _json()),
        sa.Column("accepted_draft_json", _json()),
        sa.Column("error_json", _json()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_subject_completion_idempotency"),
        schema=schema,
    )
    op.create_index(
        "ix_subject_completion_runs_subject_status",
        "subject_completion_runs",
        ["subject_id", "status", "created_at"],
        schema=schema,
    )
    op.create_table(
        "node_generation_configs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "canvas_node_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_graph_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("mode", sa.String(length=40), nullable=False),
        sa.Column("config_json", _json(), nullable=False),
        sa.Column("actual_reference_bindings_json", _json(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "canvas_node_id", "revision", name="uq_node_generation_configs_revision"
        ),
        schema=schema,
    )
    op.create_table(
        "canvas_recovery_points",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layout_version", sa.Integer(), nullable=False),
        sa.Column(
            "event_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.canvas_events.id", ondelete="SET NULL"),
        ),
        sa.Column("reason", sa.String(length=80), nullable=False),
        sa.Column("snapshot_json", _json(), nullable=False),
        _created_at(),
        schema=schema,
    )
    op.create_index(
        "ix_canvas_recovery_points_run_version",
        "canvas_recovery_points",
        ["production_run_id", "layout_version", "created_at"],
        schema=schema,
    )
    op.create_index(
        "ix_assets_canvas_history",
        "assets",
        ["production_run_id", "media_type", "created_at"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ix_assets_canvas_history", table_name="assets", schema=schema)
    op.drop_table("canvas_recovery_points", schema=schema)
    op.drop_table("node_generation_configs", schema=schema)
    op.drop_table("subject_completion_runs", schema=schema)
    op.drop_constraint(
        "fk_canvas_layouts_last_confirmed_event",
        "canvas_layouts",
        schema=schema,
        type_="foreignkey",
    )
    op.drop_column("canvas_layouts", "last_confirmed_event_id", schema=schema)
    op.drop_column("canvas_layouts", "failure_reason", schema=schema)
    op.drop_constraint("ck_subjects_generic_role", "subjects", schema=schema, type_="check")
    op.drop_constraint("ck_subjects_generic_kind", "subjects", schema=schema, type_="check")
