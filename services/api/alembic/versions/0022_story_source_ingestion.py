"""Persist original story documents, semantic units and confirmed materializations.

Revision ID: 0022_story_source_ingestion
Revises: 0021_story_series_planning
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022_story_source_ingestion"
down_revision = "0021_story_series_planning"
branch_labels = None
depends_on = None
SCHEMA = "catflow"


def upgrade() -> None:
    op.create_table(
        "story_source_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_format", sa.String(length=16), nullable=False),
        sa.Column("file_name", sa.String(length=260), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("analysis_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source_format IN ('paste','txt','md')", name="ck_story_source_format"),
        sa.CheckConstraint(
            "status IN ('pending','analyzing','analyzed','confirmed','failed')",
            name="ck_story_source_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_story_source_content_hash"),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column("story_source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_jobs_story_source_document",
        "jobs",
        "story_source_documents",
        ["story_source_document_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_story_source_analysis_job",
        "story_source_documents",
        "jobs",
        ["analysis_job_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    op.add_column(
        "job_events",
        sa.Column("story_source_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_job_events_story_source_document",
        "job_events",
        "story_source_documents",
        ["story_source_document_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        "ck_jobs_exactly_one_scope",
        "jobs",
        "num_nonnulls(project_id, series_id, story_source_document_id) = 1",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_job_events_exactly_one_scope",
        "job_events",
        "num_nonnulls(project_id, series_id, story_source_document_id) = 1",
        schema=SCHEMA,
    )
    op.create_table(
        "story_source_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("theme", sa.String(length=200), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("analysis_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], [f"{SCHEMA}.story_source_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_story_source_unit_ordinal"),
        schema=SCHEMA,
    )
    op.create_table(
        "story_source_relation_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=24), nullable=False),
        sa.Column("suggested_series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unit_ids_json", postgresql.JSONB(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("narrative_mode", sa.String(length=24), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], [f"{SCHEMA}.story_source_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["suggested_series_id"], [f"{SCHEMA}.story_series.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_table(
        "story_source_materializations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_series_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("target_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_ids_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id"],
            [f"{SCHEMA}.story_source_relation_suggestions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["series_id"], [f"{SCHEMA}.story_series.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["target_series_id"], [f"{SCHEMA}.story_series.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_project_id"], [f"{SCHEMA}.projects.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("suggestion_id", name="uq_story_source_materialization_suggestion"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_story_source_materialization_idempotency"
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("story_source_materializations", schema=SCHEMA)
    op.drop_table("story_source_relation_suggestions", schema=SCHEMA)
    op.drop_table("story_source_units", schema=SCHEMA)
    op.drop_constraint(
        "ck_job_events_exactly_one_scope", "job_events", schema=SCHEMA, type_="check"
    )
    op.drop_constraint("ck_jobs_exactly_one_scope", "jobs", schema=SCHEMA, type_="check")
    op.drop_constraint(
        "fk_job_events_story_source_document", "job_events", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_column("job_events", "story_source_document_id", schema=SCHEMA)
    op.drop_constraint(
        "fk_story_source_analysis_job", "story_source_documents", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_constraint("fk_jobs_story_source_document", "jobs", schema=SCHEMA, type_="foreignkey")
    op.drop_column("jobs", "story_source_document_id", schema=SCHEMA)
    op.drop_table("story_source_documents", schema=SCHEMA)
