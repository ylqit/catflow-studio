"""Selectable cat references and immutable production/remake ownership."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0035_character_references"
down_revision = "0034_video_edit_input"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("projects", "story_series"):
        op.add_column(
            table, sa.Column("production_started_at", sa.DateTime(timezone=True)), schema="catflow"
        )
    op.create_table(
        "cat_reference_options",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column(
            "canon_profile_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("catflow.canon_profiles.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "auxiliary_json", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("unavailable_reason", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        schema="catflow",
    )
    op.create_table(
        "character_remakes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "canon_profile_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("catflow.canon_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_snapshot_json", pg.JSONB(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "source_type IN ('project','series')", name="ck_character_remakes_source_type"
        ),
        sa.UniqueConstraint("source_type", "target_id", name="uq_character_remake_target"),
        schema="catflow",
    )
    op.execute("""UPDATE catflow.projects p SET production_started_at = p.created_at WHERE
        EXISTS (SELECT 1 FROM catflow.jobs j WHERE j.project_id=p.id
                   AND j.provider IS DISTINCT FROM 'local_ffmpeg')
        OR EXISTS (SELECT 1 FROM catflow.story_versions s WHERE s.project_id=p.id)
        OR EXISTS (SELECT 1 FROM catflow.shot_plan_versions s WHERE s.project_id=p.id)
        OR EXISTS (SELECT 1 FROM catflow.assets a WHERE a.project_id=p.id)""")
    op.execute("""UPDATE catflow.story_series s SET production_started_at = s.created_at WHERE
        EXISTS (SELECT 1 FROM catflow.jobs j WHERE j.series_id=s.id)
        OR EXISTS (SELECT 1 FROM catflow.series_plan_versions v WHERE v.series_id=s.id)
        OR EXISTS (SELECT 1 FROM catflow.series_episodes e
                   JOIN catflow.projects p ON p.id=e.project_id
                   WHERE e.series_id=s.id AND p.production_started_at IS NOT NULL)""")


def downgrade() -> None:
    op.drop_table("character_remakes", schema="catflow")
    op.drop_table("cat_reference_options", schema="catflow")
    for table in ("projects", "story_series"):
        op.drop_column(table, "production_started_at", schema="catflow")
