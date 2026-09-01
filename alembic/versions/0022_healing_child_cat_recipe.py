"""Add the fixed-IP healing child-and-cat production recipe.

Revision ID: 0022_healing_child_cat_recipe
Revises: 0021_libtv_subject_assistant
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022_healing_child_cat_recipe"
down_revision: str | Sequence[str] | None = "0021_libtv_subject_assistant"
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
    op.drop_constraint("ck_assets_status", "assets", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_assets_status",
        "assets",
        "status IN ('candidate', 'approved', 'rejected', 'ready', 'stale')",
        schema=schema,
    )
    op.create_table(
        "production_recipe_instances",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("recipe_key", sa.String(length=80), nullable=False),
        sa.Column("recipe_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("theme", sa.Text(), nullable=False),
        sa.Column("inspiration_key", sa.String(length=80)),
        sa.Column("target_duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("quality_tier", sa.String(length=24), nullable=False),
        sa.Column("canon_profile_id", sa.String(length=80), nullable=False),
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
        sa.CheckConstraint("revision >= 1", name="ck_production_recipe_instances_revision"),
        sa.CheckConstraint(
            "target_duration_seconds BETWEEN 8 AND 60",
            name="ck_production_recipe_instances_duration",
        ),
        sa.CheckConstraint(
            "quality_tier IN ('quick', 'balanced', 'premium')",
            name="ck_production_recipe_instances_quality_tier",
        ),
        schema=schema,
    )
    op.create_table(
        "human_review_decisions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_recipe_instance_id",
            _uuid(),
            sa.ForeignKey(
                f"{schema}.production_recipe_instances.id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=80), nullable=False),
        sa.Column("target_id", _uuid(), nullable=False),
        sa.Column("target_revision", sa.Integer()),
        sa.Column("target_hash", sa.String(length=64)),
        sa.Column("decision", sa.String(length=24), nullable=False),
        sa.Column(
            "blocking_diagnostic_present",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "issues_json",
            _json(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'request_changes', 'override')",
            name="ck_human_review_decisions_decision",
        ),
        sa.CheckConstraint(
            "target_revision IS NOT NULL OR target_hash IS NOT NULL",
            name="ck_human_review_decisions_pinned_target",
        ),
        sa.CheckConstraint(
            "NOT (decision = 'approve' AND blocking_diagnostic_present)",
            name="ck_human_review_decisions_blocking_approval",
        ),
        sa.CheckConstraint(
            "decision != 'override' OR NULLIF(BTRIM(reason), '') IS NOT NULL",
            name="ck_human_review_decisions_override_reason",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_human_review_decisions_target",
        "human_review_decisions",
        [
            "production_recipe_instance_id",
            "target_type",
            "target_id",
            "created_at",
        ],
        schema=schema,
    )
    op.add_column(
        "story_revisions",
        sa.Column(
            "episode_rules_json",
            _json(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=schema,
    )
    op.add_column(
        "shot_beats",
        sa.Column(
            "temporal_beats_json",
            _json(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_column("shot_beats", "temporal_beats_json", schema=schema)
    op.drop_column("story_revisions", "episode_rules_json", schema=schema)
    op.drop_index(
        "ix_human_review_decisions_target",
        table_name="human_review_decisions",
        schema=schema,
    )
    op.drop_table("human_review_decisions", schema=schema)
    op.drop_table("production_recipe_instances", schema=schema)
    op.drop_constraint("ck_assets_status", "assets", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_assets_status",
        "assets",
        "status IN ('candidate', 'approved', 'rejected', 'ready')",
        schema=schema,
    )
