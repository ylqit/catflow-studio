"""Add first-class story series planning and series-scoped jobs.

Revision ID: 0021_story_series_planning
Revises: 0020_shot_plan_review_workflow
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_story_series_planning"
down_revision = "0020_shot_plan_review_workflow"
branch_labels = None
depends_on = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.create_table(
        "story_series",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("premise", sa.Text(), nullable=False),
        sa.Column("narrative_mode", sa.String(length=24), nullable=False),
        sa.Column("planned_episode_count", sa.SmallInteger(), nullable=False),
        sa.Column("default_episode_duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("world_setting", sa.Text(), nullable=False),
        sa.Column("emotional_direction", sa.Text(), nullable=False),
        sa.Column("ending_goal", sa.Text(), nullable=True),
        sa.Column("recurring_elements_json", postgresql.JSONB(), nullable=False),
        sa.Column("must_keep_json", postgresql.JSONB(), nullable=False),
        sa.Column("must_avoid_json", postgresql.JSONB(), nullable=False),
        sa.Column("additional_notes", sa.Text(), nullable=True),
        sa.Column("canon_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "narrative_mode IN ('continuous','lightly_serialized','anthology')",
            name="ck_story_series_narrative_mode",
        ),
        sa.CheckConstraint(
            "planned_episode_count BETWEEN 2 AND 30",
            name="ck_story_series_episode_count",
        ),
        sa.CheckConstraint(
            "default_episode_duration_seconds BETWEEN 8 AND 15",
            name="ck_story_series_duration",
        ),
        sa.ForeignKeyConstraint(
            ["canon_profile_id"], [f"{SCHEMA}.canon_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_jobs_series_id",
        "jobs",
        "story_series",
        ["series_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.create_index("ix_jobs_series", "jobs", ["series_id", "created_at"], schema=SCHEMA)
    op.alter_column(
        "jobs", "project_id", existing_type=postgresql.UUID(), nullable=True, schema=SCHEMA
    )
    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','plan_shots','plan_series','plan_series_episode',"
        "'analyze_story_source','generate_image','diagnose_image','generate_video',"
        "'diagnose_video','regenerate_video_segment','render_export',"
        "'extract_continuity_frames')",
        schema=SCHEMA,
    )
    op.add_column(
        "job_events",
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_job_events_series_id",
        "job_events",
        "story_series",
        ["series_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.alter_column(
        "job_events", "project_id", existing_type=postgresql.UUID(), nullable=True, schema=SCHEMA
    )
    op.create_table(
        "series_plan_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(), nullable=False),
        sa.Column("issues_json", postgresql.JSONB(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_revision", sa.String(length=80), nullable=False),
        sa.Column("producing_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_plan_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("materialization_idempotency_key", sa.String(length=96), nullable=True),
        sa.Column("activation_idempotency_key", sa.String(length=96), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('candidate','accepted','rejected','superseded')",
            name="ck_series_plan_status",
        ),
        sa.CheckConstraint(
            "disposition IN ('candidate_ready','needs_input','invalid')",
            name="ck_series_plan_disposition",
        ),
        sa.CheckConstraint("NOT active OR status = 'accepted'", name="ck_series_plan_active"),
        sa.ForeignKeyConstraint(["series_id"], [f"{SCHEMA}.story_series.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["producing_job_id"], [f"{SCHEMA}.jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["base_plan_version_id"],
            [f"{SCHEMA}.series_plan_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("series_id", "revision", name="uq_series_plan_revision"),
        sa.UniqueConstraint("producing_job_id", name="uq_series_plan_job"),
        sa.UniqueConstraint(
            "materialization_idempotency_key",
            name="uq_series_plan_materialization_idempotency",
        ),
        sa.UniqueConstraint(
            "activation_idempotency_key", name="uq_series_plan_activation_idempotency"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_series_plan_active",
        "series_plan_versions",
        ["series_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )
    op.create_index(
        "uq_series_plan_candidate",
        "series_plan_versions",
        ["series_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'candidate'"),
    )
    op.create_table(
        "series_episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_order", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("materialization_idempotency_key", sa.String(length=96), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["series_id"], [f"{SCHEMA}.story_series.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("series_id", "episode_order", name="uq_series_episode_order"),
        sa.UniqueConstraint("project_id", name="uq_series_episode_project"),
        sa.UniqueConstraint(
            "materialization_idempotency_key",
            name="uq_series_episode_materialization_idempotency",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "series_episode_outline_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outline_json", postgresql.JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], [f"{SCHEMA}.series_episodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_plan_version_id"], [f"{SCHEMA}.series_plan_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("episode_id", "revision", name="uq_episode_outline_revision"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_episode_outline_active",
        "series_episode_outline_versions",
        ["episode_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_episode_outline_active", table_name="series_episode_outline_versions", schema=SCHEMA
    )
    op.drop_table("series_episode_outline_versions", schema=SCHEMA)
    op.drop_table("series_episodes", schema=SCHEMA)
    op.drop_index("uq_series_plan_candidate", table_name="series_plan_versions", schema=SCHEMA)
    op.drop_index("uq_series_plan_active", table_name="series_plan_versions", schema=SCHEMA)
    op.drop_table("series_plan_versions", schema=SCHEMA)
    op.alter_column(
        "job_events", "project_id", existing_type=postgresql.UUID(), nullable=False, schema=SCHEMA
    )
    op.drop_constraint("fk_job_events_series_id", "job_events", schema=SCHEMA, type_="foreignkey")
    op.drop_column("job_events", "series_id", schema=SCHEMA)
    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','plan_shots','generate_image','diagnose_image',"
        "'generate_video','diagnose_video','regenerate_video_segment','render_export')",
        schema=SCHEMA,
    )
    op.alter_column(
        "jobs", "project_id", existing_type=postgresql.UUID(), nullable=False, schema=SCHEMA
    )
    op.drop_index("ix_jobs_series", table_name="jobs", schema=SCHEMA)
    op.drop_constraint("fk_jobs_series_id", "jobs", schema=SCHEMA, type_="foreignkey")
    op.drop_column("jobs", "series_id", schema=SCHEMA)
    op.drop_table("story_series", schema=SCHEMA)
