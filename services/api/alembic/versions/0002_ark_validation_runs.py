"""Add Ark submission safety and validation run authorization.

Revision ID: 0002_ark_validation_runs
Revises: 0001_catflow_core
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_ark_validation_runs"
down_revision: str | None = "0001_catflow_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.create_table(
        "validation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("topics_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("resolution", sa.String(length=16), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("target_budget_cny", sa.Integer(), nullable=False),
        sa.Column("call_limits_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("usage_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("models_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capability_revision", sa.String(length=120), nullable=False),
        sa.Column("cost_estimate_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','authorized','paused','completed','cancelled')",
            name="ck_validation_runs_status",
        ),
        sa.CheckConstraint("duration_seconds = 12", name="ck_validation_runs_duration"),
        sa.CheckConstraint("resolution = '480p'", name="ck_validation_runs_resolution"),
        sa.CheckConstraint("aspect_ratio = '9:16'", name="ck_validation_runs_aspect_ratio"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column("validation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column("parent_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column("provider_submission_started_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column(
            "provider_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column("actual_usage_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_jobs_validation_run",
        "jobs",
        "validation_runs",
        ["validation_run_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_jobs_parent_job",
        "jobs",
        "jobs",
        ["parent_job_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','generate_image','diagnose_image','generate_video',"
        "'diagnose_video','render_export')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_jobs_status", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_status",
        "jobs",
        "status IN ('queued','submitting','submitted','polling','storing','succeeded',"
        "'failed','cancel_requested','cancelled','submission_unknown')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_status", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_status",
        "jobs",
        "status IN ('queued','submitting','submitted','polling','storing','succeeded',"
        "'failed','cancel_requested','cancelled')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','generate_image','diagnose_image','generate_video',"
        "'render_export')",
        schema=SCHEMA,
    )
    op.drop_constraint("fk_jobs_parent_job", "jobs", schema=SCHEMA, type_="foreignkey")
    op.drop_constraint("fk_jobs_validation_run", "jobs", schema=SCHEMA, type_="foreignkey")
    for column in (
        "actual_usage_json",
        "provider_result_json",
        "provider_submission_started_at",
        "parent_job_id",
        "validation_run_id",
    ):
        op.drop_column("jobs", column, schema=SCHEMA)
    op.drop_table("validation_runs", schema=SCHEMA)
