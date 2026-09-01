"""Create the goal-focused CatFlow schema.

Revision ID: 0001_catflow_core
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_catflow_core"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def _uuid_id() -> sa.Column[object]:
    return sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False)


def _created_at() -> sa.Column[object]:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "canon_profiles",
        _uuid_id(),
        sa.Column("profile_key", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("profile_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("profile_hash", sa.String(length=64), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_hash", name="uq_canon_profiles_profile_hash"),
        sa.UniqueConstraint("profile_key", "version", name="uq_canon_profiles_key_version"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_canon_profiles_active",
        "canon_profiles",
        ["profile_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )
    op.create_table(
        "projects",
        _uuid_id(),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("theme", sa.Text(), nullable=False),
        sa.Column("target_duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("canon_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("target_duration_seconds BETWEEN 8 AND 15", name="ck_projects_duration"),
        sa.CheckConstraint("aspect_ratio = '9:16'", name="ck_projects_aspect_ratio"),
        sa.ForeignKeyConstraint(
            ["canon_profile_id"], [f"{SCHEMA}.canon_profiles.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_table(
        "jobs",
        _uuid_id(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("provider_task_id", sa.String(length=200), nullable=True),
        sa.Column("expected_cost_micros", sa.BigInteger(), nullable=True),
        sa.Column("frozen_input_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("supersedes_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("locked_by", sa.String(length=120), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('plan_story','generate_image','diagnose_image','generate_video','render_export')",
            name="ck_jobs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued','submitting','submitted','polling','storing','succeeded',"
            "'failed','cancel_requested','cancelled')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "expected_cost_micros IS NULL OR expected_cost_micros >= 0", name="ck_jobs_cost"
        ),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supersedes_job_id"], [f"{SCHEMA}.jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_jobs_queue", "jobs", ["status", "created_at"], schema=SCHEMA)
    op.create_index(
        "ix_jobs_provider_task", "jobs", ["provider", "provider_task_id"], schema=SCHEMA
    )
    op.create_table(
        "assets",
        _uuid_id(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("canon_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("producing_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_index", sa.SmallInteger(), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.CheckConstraint("media_type IN ('image','video','audio')", name="ck_assets_media_type"),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["canon_profile_id"], [f"{SCHEMA}.canon_profiles.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["producing_job_id"], [f"{SCHEMA}.jobs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "sha256", "role", name="uq_assets_project_sha_role"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_assets_project_role", "assets", ["project_id", "role", "created_at"], schema=SCHEMA
    )
    op.create_table(
        "project_selections",
        _uuid_id(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "slot IN ('episode_child','episode_cat','pair_scale','environment','style_board',"
            "'video','final')",
            name="ck_project_selections_slot",
        ),
        sa.CheckConstraint(
            "decision IN ('selected','rejected','approved')", name="ck_project_selections_decision"
        ),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], [f"{SCHEMA}.assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_project_selections_current",
        "project_selections",
        ["project_id", "slot", "created_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "life_planner_sessions",
        _uuid_id(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_revision", sa.Integer(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_life_planner_sessions_project_id"),
        schema=SCHEMA,
    )
    op.create_table(
        "life_planner_messages",
        _uuid_id(),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        _created_at(),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_life_planner_messages_role"),
        sa.ForeignKeyConstraint(
            ["session_id"], [f"{SCHEMA}.life_planner_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "ordinal", name="uq_life_planner_messages_order"),
        schema=SCHEMA,
    )
    op.create_table(
        "life_planner_proposals",
        _uuid_id(),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("proposal_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('draft','adopted','outdated')", name="ck_proposals_status"),
        sa.ForeignKeyConstraint(
            ["session_id"], [f"{SCHEMA}.life_planner_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_proposals_project_status",
        "life_planner_proposals",
        ["project_id", "status", "created_at"],
        schema=SCHEMA,
    )
    op.create_table(
        "story_versions",
        _uuid_id(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("micro_event_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("target_duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("dialogue_policy", sa.String(length=16), nullable=False),
        sa.Column("environment_intent", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_proposal_id"],
            [f"{SCHEMA}.life_planner_proposals.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "revision", name="uq_story_versions_revision"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_story_versions_active",
        "story_versions",
        ["project_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )
    op.create_table(
        "shot_plan_versions",
        _uuid_id(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_story_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_selection_hash", sa.String(length=64), nullable=False),
        sa.Column("clip_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("shots_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "total_duration_seconds BETWEEN 8 AND 15", name="ck_shot_plans_duration"
        ),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_story_version_id"], [f"{SCHEMA}.story_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "revision", name="uq_shot_plan_versions_revision"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_shot_plan_versions_active",
        "shot_plan_versions",
        ["project_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )
    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(["job_id"], [f"{SCHEMA}.jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_job_events_project_id", "job_events", ["project_id", "id"], schema=SCHEMA
    )
    op.create_table(
        "edit_versions",
        _uuid_id(),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_selection_hash", sa.String(length=64), nullable=False),
        sa.Column("edl_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rendered_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        _created_at(),
        sa.CheckConstraint("status IN ('draft','rendered','approved')", name="ck_edit_versions_status"),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["rendered_asset_id"], [f"{SCHEMA}.assets.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "revision", name="uq_edit_versions_revision"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table_name in (
        "edit_versions",
        "job_events",
        "shot_plan_versions",
        "story_versions",
        "life_planner_proposals",
        "life_planner_messages",
        "life_planner_sessions",
        "project_selections",
        "assets",
        "jobs",
        "projects",
        "canon_profiles",
    ):
        op.drop_table(table_name, schema=SCHEMA)
