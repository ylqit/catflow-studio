"""Add explicit shot-plan candidate review workflow.

Revision ID: 0020_shot_plan_review_workflow
Revises: 0019_project_scoped_environment
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020_shot_plan_review_workflow"
down_revision = "0019_project_scoped_environment"
branch_labels = None
depends_on = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.add_column(
        "shot_plan_versions",
        sa.Column(
            "review_status",
            sa.String(length=20),
            server_default="accepted",
            nullable=False,
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "shot_plan_versions",
        sa.Column("producing_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "shot_plan_versions",
        sa.Column("base_shot_plan_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "shot_plan_versions",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.execute(
        "UPDATE catflow.shot_plan_versions SET review_status = 'accepted', "
        "decided_at = created_at"
    )
    op.alter_column(
        "shot_plan_versions",
        "review_status",
        server_default=None,
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_shot_plan_versions_producing_job",
        "shot_plan_versions",
        "jobs",
        ["producing_job_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_shot_plan_versions_base_version",
        "shot_plan_versions",
        "shot_plan_versions",
        ["base_shot_plan_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_shot_plan_versions_producing_job",
        "shot_plan_versions",
        ["producing_job_id"],
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_shot_plan_versions_review_status",
        "shot_plan_versions",
        "review_status IN ('accepted','candidate','rejected','superseded')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_shot_plan_versions_active_accepted",
        "shot_plan_versions",
        "NOT active OR review_status = 'accepted'",
        schema=SCHEMA,
    )
    op.create_index(
        "uq_shot_plan_versions_candidate",
        "shot_plan_versions",
        ["project_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("review_status = 'candidate'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_shot_plan_versions_candidate",
        table_name="shot_plan_versions",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_shot_plan_versions_active_accepted",
        "shot_plan_versions",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_shot_plan_versions_review_status",
        "shot_plan_versions",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "uq_shot_plan_versions_producing_job",
        "shot_plan_versions",
        schema=SCHEMA,
        type_="unique",
    )
    op.drop_constraint(
        "fk_shot_plan_versions_base_version",
        "shot_plan_versions",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_shot_plan_versions_producing_job",
        "shot_plan_versions",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("shot_plan_versions", "decided_at", schema=SCHEMA)
    op.drop_column("shot_plan_versions", "base_shot_plan_version_id", schema=SCHEMA)
    op.drop_column("shot_plan_versions", "producing_job_id", schema=SCHEMA)
    op.drop_column("shot_plan_versions", "review_status", schema=SCHEMA)
