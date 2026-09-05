"""Separate source beats, series length and provider-sized planning segments.

Revision ID: 0025_flexible_series_planning
Revises: 0024_story_source_new_records
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0025_flexible_series_planning"
down_revision = "0024_story_source_new_records"
branch_labels = None
depends_on = None
SCHEMA = "catflow"


def upgrade() -> None:
    op.add_column(
        "story_series",
        sa.Column("length_mode", sa.String(length=16), nullable=True),
        schema=SCHEMA,
    )
    op.execute(f"UPDATE {SCHEMA}.story_series SET length_mode = 'fixed'")
    op.alter_column("story_series", "length_mode", nullable=False, schema=SCHEMA)
    op.drop_constraint(
        "ck_story_series_episode_count", "story_series", schema=SCHEMA, type_="check"
    )
    op.alter_column(
        "story_series",
        "planned_episode_count",
        existing_type=sa.SmallInteger(),
        type_=sa.BigInteger(),
        nullable=True,
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_story_series_length_mode",
        "story_series",
        "length_mode IN ('fixed','ongoing')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_story_series_episode_count",
        "story_series",
        "(length_mode = 'fixed' AND planned_episode_count >= 2) OR "
        "(length_mode = 'ongoing' AND planned_episode_count IS NULL)",
        schema=SCHEMA,
    )
    op.alter_column(
        "series_episodes",
        "episode_order",
        existing_type=sa.SmallInteger(),
        type_=sa.BigInteger(),
        nullable=False,
        schema=SCHEMA,
    )
    op.add_column(
        "story_source_relation_suggestions",
        sa.Column("episode_count_recommendation_json", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )

    op.create_table(
        "series_source_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ordinal", sa.Integer(), nullable=False),
        sa.Column("binding_order", sa.Integer(), nullable=False),
        sa.Column("materialization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["series_id"], [f"{SCHEMA}.story_series.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_unit_id"], [f"{SCHEMA}.story_source_units.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["materialization_id"],
            [f"{SCHEMA}.story_source_materializations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("series_id", "source_unit_id", name="uq_series_source_unit"),
        sa.UniqueConstraint("series_id", "binding_order", name="uq_series_source_order"),
        schema=SCHEMA,
    )

    op.create_table(
        "series_episode_outline_source_coverage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outline_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_source_binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_order", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.String(length=16), nullable=False),
        sa.Column("coverage_note", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "coverage IN ('whole','partial','continuation')",
            name="ck_episode_source_coverage_mode",
        ),
        sa.ForeignKeyConstraint(
            ["outline_version_id"],
            [f"{SCHEMA}.series_episode_outline_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["series_source_binding_id"],
            [f"{SCHEMA}.series_source_bindings.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outline_version_id",
            "series_source_binding_id",
            "source_order",
            name="uq_episode_source_coverage",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "series_plan_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("start_episode_order", sa.BigInteger(), nullable=False),
        sa.Column("requested_episode_count", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "requested_episode_count BETWEEN 1 AND 30",
            name="ck_series_plan_segment_batch_size",
        ),
        sa.ForeignKeyConstraint(
            ["series_id"], [f"{SCHEMA}.story_series.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "series_id", "start_episode_order", name="uq_series_plan_segment_start"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "series_plan_segment_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("segment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(), nullable=False),
        sa.Column("issues_json", postgresql.JSONB(), nullable=False),
        sa.Column("producing_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expected_series_plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_segment_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_revision", sa.String(length=80), nullable=False),
        sa.Column("activation_idempotency_key", sa.String(length=96), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('candidate','accepted','rejected','superseded')",
            name="ck_series_plan_segment_version_status",
        ),
        sa.CheckConstraint(
            "disposition IN ('candidate_ready','needs_input','invalid')",
            name="ck_series_plan_segment_disposition",
        ),
        sa.CheckConstraint(
            "NOT active OR status = 'accepted'", name="ck_series_plan_segment_active"
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"], [f"{SCHEMA}.series_plan_segments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["producing_job_id"], [f"{SCHEMA}.jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["expected_series_plan_version_id"],
            [f"{SCHEMA}.series_plan_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_segment_version_id"],
            [f"{SCHEMA}.series_plan_segment_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "revision", name="uq_series_plan_segment_revision"),
        sa.UniqueConstraint("producing_job_id", name="uq_series_plan_segment_job"),
        sa.UniqueConstraint(
            "activation_idempotency_key", name="uq_series_plan_segment_activation_key"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_series_plan_segment_candidate",
        "series_plan_segment_versions",
        ["segment_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'candidate'"),
    )
    op.create_index(
        "uq_series_plan_segment_active",
        "series_plan_segment_versions",
        ["segment_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )
    op.add_column(
        "series_episode_outline_versions",
        sa.Column(
            "source_segment_version_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_episode_outline_source_segment",
        "series_episode_outline_versions",
        "series_plan_segment_versions",
        ["source_segment_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )

    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','plan_shots','plan_series','plan_series_segment',"
        "'plan_series_episode','analyze_story_source','generate_image','diagnose_image',"
        "'generate_video','diagnose_video','regenerate_video_segment','render_export',"
        "'extract_continuity_frames')",
        schema=SCHEMA,
    )


def downgrade() -> None:
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
    op.drop_constraint(
        "fk_episode_outline_source_segment",
        "series_episode_outline_versions",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column(
        "series_episode_outline_versions", "source_segment_version_id", schema=SCHEMA
    )
    op.drop_index(
        "uq_series_plan_segment_active",
        table_name="series_plan_segment_versions",
        schema=SCHEMA,
    )
    op.drop_index(
        "uq_series_plan_segment_candidate",
        table_name="series_plan_segment_versions",
        schema=SCHEMA,
    )
    op.drop_table("series_plan_segment_versions", schema=SCHEMA)
    op.drop_table("series_plan_segments", schema=SCHEMA)
    op.drop_table("series_episode_outline_source_coverage", schema=SCHEMA)
    op.drop_table("series_source_bindings", schema=SCHEMA)
    op.drop_column(
        "story_source_relation_suggestions",
        "episode_count_recommendation_json",
        schema=SCHEMA,
    )
    op.execute(
        f"UPDATE {SCHEMA}.story_series SET length_mode = 'fixed', "
        "planned_episode_count = GREATEST(COALESCE(planned_episode_count, 2), 2)"
    )
    op.alter_column(
        "series_episodes",
        "episode_order",
        existing_type=sa.BigInteger(),
        type_=sa.SmallInteger(),
        nullable=False,
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_story_series_episode_count", "story_series", schema=SCHEMA, type_="check"
    )
    op.drop_constraint(
        "ck_story_series_length_mode", "story_series", schema=SCHEMA, type_="check"
    )
    op.alter_column(
        "story_series",
        "planned_episode_count",
        existing_type=sa.BigInteger(),
        type_=sa.SmallInteger(),
        nullable=False,
        schema=SCHEMA,
    )
    op.drop_column("story_series", "length_mode", schema=SCHEMA)
    op.create_check_constraint(
        "ck_story_series_episode_count",
        "story_series",
        "planned_episode_count BETWEEN 2 AND 30",
        schema=SCHEMA,
    )
