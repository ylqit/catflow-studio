"""Separate concise story events from complete story revisions.

Revision ID: 0028_story_event_candidates
Revises: 0027_story_scene_prompts
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028_story_event_candidates"
down_revision: str | Sequence[str] | None = "0027_story_scene_prompts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "story_event_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "production_recipe_instance_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("story_brief_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_index", sa.SmallInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("strategy", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="candidate", nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("premise", sa.Text(), nullable=False),
        sa.Column("child_action", sa.Text(), nullable=False),
        sa.Column("cat_participation", sa.Text(), nullable=False),
        sa.Column("small_change", sa.Text(), nullable=False),
        sa.Column("warm_ending", sa.Text(), nullable=False),
        sa.Column(
            "suggested_scenes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("duration_fit_summary", sa.Text(), nullable=False),
        sa.Column(
            "requires_scene_change", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("cat_behavior_mode_suggestion", sa.String(length=40), nullable=False),
        sa.Column(
            "score_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("generation_prompt_id", postgresql.UUID(as_uuid=True)),
        sa.Column("selected_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'selected', 'superseded')",
            name="ck_story_event_candidates_status",
        ),
        sa.ForeignKeyConstraint(
            ["production_run_id"],
            [f"{schema}.production_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["production_recipe_instance_id"],
            [f"{schema}.production_recipe_instances.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["story_brief_id"],
            [f"{schema}.story_briefs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_prompt_id"],
            [f"{schema}.prompt_records.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "production_recipe_instance_id",
            "batch_id",
            "candidate_index",
            name="uq_story_event_candidates_batch_index",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_story_event_candidates_instance_status",
        "story_event_candidates",
        ["production_recipe_instance_id", "status", "created_at"],
        schema=schema,
    )
    op.add_column(
        "story_revisions",
        sa.Column("source_event_candidate_id", postgresql.UUID(as_uuid=True)),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_story_revisions_source_event",
        "story_revisions",
        "story_event_candidates",
        ["source_event_candidate_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_story_revisions_source_event",
        "story_revisions",
        ["source_event_candidate_id"],
        schema=schema,
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_index("ix_story_revisions_source_event", table_name="story_revisions", schema=schema)
    op.drop_constraint(
        "fk_story_revisions_source_event",
        "story_revisions",
        schema=schema,
        type_="foreignkey",
    )
    op.drop_column("story_revisions", "source_event_candidate_id", schema=schema)
    op.drop_index(
        "ix_story_event_candidates_instance_status",
        table_name="story_event_candidates",
        schema=schema,
    )
    op.drop_table("story_event_candidates", schema=schema)
