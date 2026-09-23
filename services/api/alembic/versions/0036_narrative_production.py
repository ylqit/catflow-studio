"""Versioned 8–60 second production plans; historical task inputs stay immutable."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0036_narrative_production"
down_revision = "0035_character_references"
branch_labels = None
depends_on = None


def upgrade():
    for table, column, name in (
        ("projects", "target_duration_seconds", "ck_projects_duration"),
        ("story_series", "default_episode_duration_seconds", "ck_story_series_duration"),
        ("shot_plan_versions", "total_duration_seconds", "ck_shot_plans_duration"),
    ):
        op.drop_constraint(name, table, schema="catflow", type_="check")
        op.create_check_constraint(name, table, f"{column} BETWEEN 8 AND 60", schema="catflow")
    op.add_column("story_versions", sa.Column("narrative_design_json", pg.JSONB(), nullable=True), schema="catflow")
    op.create_table(
        "production_plan_versions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("catflow.projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("shot_plan_version_id", pg.UUID(as_uuid=True), sa.ForeignKey("catflow.shot_plan_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("document_json", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "revision", name="uq_production_plan_revision"),
        sa.UniqueConstraint("idempotency_key", name="uq_production_plan_idempotency"),
        schema="catflow",
    )
    op.create_index("uq_production_plan_active", "production_plan_versions", ["project_id"], unique=True, postgresql_where=sa.text("active = true"), schema="catflow")
    op.create_table(
        "production_unit_selections",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", pg.UUID(as_uuid=True), sa.ForeignKey("catflow.projects.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("plan_id", pg.UUID(as_uuid=True), sa.ForeignKey("catflow.production_plan_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("asset_id", pg.UUID(as_uuid=True), sa.ForeignKey("catflow.assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("unit_id", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("document_json", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "unit_id", "revision", name="uq_unit_selection_revision"),
        sa.UniqueConstraint("idempotency_key", name="uq_unit_selection_idempotency"),
        schema="catflow",
    )
    op.create_index("uq_unit_selection_active", "production_unit_selections", ["project_id", "unit_id"], unique=True, postgresql_where=sa.text("active = true"), schema="catflow")


def downgrade():
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM catflow.production_plan_versions) OR EXISTS (SELECT 1 FROM catflow.projects WHERE target_duration_seconds > 15) OR EXISTS (SELECT 1 FROM catflow.story_series WHERE default_episode_duration_seconds > 15) OR EXISTS (SELECT 1 FROM catflow.shot_plan_versions WHERE total_duration_seconds > 15)")):
        raise RuntimeError("存在新版生产数据，拒绝破坏性降级；请使用兼容版本修复")
    op.drop_table("production_unit_selections", schema="catflow")
    op.drop_table("production_plan_versions", schema="catflow")
    op.drop_column("story_versions", "narrative_design_json", schema="catflow")
    for table, column, name in (
        ("projects", "target_duration_seconds", "ck_projects_duration"),
        ("story_series", "default_episode_duration_seconds", "ck_story_series_duration"),
        ("shot_plan_versions", "total_duration_seconds", "ck_shot_plans_duration"),
    ):
        op.drop_constraint(name, table, schema="catflow", type_="check")
        op.create_check_constraint(name, table, f"{column} BETWEEN 8 AND 15", schema="catflow")
