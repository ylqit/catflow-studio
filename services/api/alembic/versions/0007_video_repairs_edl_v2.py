"""Add frame-accurate video repairs and non-destructive EDL v2 versions.

Revision ID: 0007_video_repairs_edl_v2
Revises: 0006_validation_canon_required
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_video_repairs_edl_v2"
down_revision: str | Sequence[str] | None = "0006_validation_canon_required"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.add_column(
        "edit_versions",
        sa.Column("parent_edit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "edit_versions",
        sa.Column("format_version", sa.SmallInteger(), server_default="1", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "edit_versions",
        sa.Column("active", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "edit_versions",
        sa.Column("timeline_hash", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_edit_versions_parent",
        "edit_versions",
        "edit_versions",
        ["parent_edit_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_edit_versions_format_version",
        "edit_versions",
        "format_version IN (1,2)",
        schema=SCHEMA,
    )
    op.execute(
        sa.text(
            f"WITH latest AS (SELECT DISTINCT ON (project_id) id "
            f"FROM {SCHEMA}.edit_versions ORDER BY project_id, revision DESC) "
            f"UPDATE {SCHEMA}.edit_versions AS edit SET active = true "
            "FROM latest WHERE edit.id = latest.id"
        )
    )
    op.create_index(
        "uq_edit_versions_active",
        "edit_versions",
        ["project_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )

    op.create_table(
        "video_repairs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_video_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_edit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_timeline_hash", sa.String(length=64), nullable=False),
        sa.Column("frame_rate_numerator", sa.Integer(), nullable=False),
        sa.Column("frame_rate_denominator", sa.Integer(), nullable=False),
        sa.Column("issue_start_frame", sa.Integer(), nullable=False),
        sa.Column("issue_end_frame", sa.Integer(), nullable=False),
        sa.Column("generation_start_frame", sa.Integer(), nullable=False),
        sa.Column("generation_end_frame", sa.Integer(), nullable=False),
        sa.Column("candidate_core_start_frame", sa.Integer(), nullable=False),
        sa.Column("candidate_core_end_frame", sa.Integer(), nullable=False),
        sa.Column("provider_duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("negative_prompt", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("candidate_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_candidate_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_edit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_idempotency_key", sa.String(length=96), nullable=True),
        sa.Column("preview_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','generating','candidate_ready','approved','rejected',"
            "'outdated','cancelled')",
            name="ck_video_repairs_status",
        ),
        sa.CheckConstraint(
            "issue_start_frame >= 0 AND issue_end_frame > issue_start_frame",
            name="ck_video_repairs_issue_range",
        ),
        sa.CheckConstraint(
            "generation_start_frame >= 0 AND generation_end_frame > generation_start_frame",
            name="ck_video_repairs_generation_range",
        ),
        sa.CheckConstraint(
            "provider_duration_seconds BETWEEN 4 AND 15",
            name="ck_video_repairs_provider_duration",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["base_video_asset_id"], [f"{SCHEMA}.assets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["base_edit_version_id"],
            [f"{SCHEMA}.edit_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_asset_id"], [f"{SCHEMA}.assets.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["approved_candidate_asset_id"],
            [f"{SCHEMA}.assets.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approved_edit_version_id"],
            [f"{SCHEMA}.edit_versions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "approval_idempotency_key", name="uq_video_repairs_approval_idempotency"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_video_repairs_project_created",
        "video_repairs",
        ["project_id", "created_at"],
        schema=SCHEMA,
    )

    op.drop_constraint("ck_jobs_kind", "jobs", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','generate_image','diagnose_image','generate_video',"
        "'diagnose_video','regenerate_video_segment','render_export')",
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column("video_repair_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_jobs_video_repair",
        "jobs",
        "video_repairs",
        ["video_repair_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_jobs_video_repair", "jobs", type_="foreignkey", schema=SCHEMA)
    op.drop_column("jobs", "video_repair_id", schema=SCHEMA)
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','generate_image','diagnose_image','generate_video',"
        "'diagnose_video','render_export')",
        schema=SCHEMA,
    )
    op.drop_index("ix_video_repairs_project_created", table_name="video_repairs", schema=SCHEMA)
    op.drop_table("video_repairs", schema=SCHEMA)
    op.drop_index("uq_edit_versions_active", table_name="edit_versions", schema=SCHEMA)
    op.drop_constraint(
        "ck_edit_versions_format_version", "edit_versions", type_="check", schema=SCHEMA
    )
    op.drop_constraint(
        "fk_edit_versions_parent", "edit_versions", type_="foreignkey", schema=SCHEMA
    )
    op.drop_column("edit_versions", "timeline_hash", schema=SCHEMA)
    op.drop_column("edit_versions", "active", schema=SCHEMA)
    op.drop_column("edit_versions", "format_version", schema=SCHEMA)
    op.drop_column("edit_versions", "parent_edit_version_id", schema=SCHEMA)
