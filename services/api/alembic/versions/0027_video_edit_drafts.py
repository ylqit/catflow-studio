"""Separate editing drafts and immutable reviews from official video selection."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0027_video_edit_drafts"
down_revision = "0026_repeat_materializations"
branch_labels = None
depends_on = None
SCHEMA = "catflow"


def upgrade() -> None:
    op.create_table(
        "video_edit_drafts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("catflow.projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_video_asset_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("catflow.assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("head_edit_version_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("references_json", pg.JSONB(), nullable=False),
        sa.Column("references_confirmed", sa.Boolean(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_video_edit_drafts_project_created",
        "video_edit_drafts",
        ["project_id", "created_at"],
        schema=SCHEMA,
    )
    op.add_column(
        "edit_versions",
        sa.Column("edit_draft_id", pg.UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "edit_versions", sa.Column("save_request_hash", sa.String(64), nullable=True), schema=SCHEMA
    )
    op.create_foreign_key(
        "fk_edit_versions_draft",
        "edit_versions",
        "video_edit_drafts",
        ["edit_draft_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_edit_drafts_head",
        "video_edit_drafts",
        "edit_versions",
        ["head_edit_version_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_table(
        "video_reviews",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("catflow.projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("catflow.assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "edit_version_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("catflow.edit_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column("document_json", pg.JSONB(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_video_reviews_asset_created", "video_reviews", ["asset_id", "created_at"], schema=SCHEMA
    )
    op.drop_constraint(
        "ck_video_repairs_selection_policy", "video_repairs", type_="check", schema=SCHEMA
    )
    op.create_check_constraint(
        "ck_video_repairs_selection_policy",
        "video_repairs",
        "selection_policy_version IN (1,2,3)",
        schema=SCHEMA,
    )
    op.drop_constraint(
        "ck_video_repairs_v2_issue_duration", "video_repairs", type_="check", schema=SCHEMA
    )
    op.create_check_constraint(
        "ck_video_repairs_v2_issue_duration",
        "video_repairs",
        "selection_policy_version != 2 OR issue_end_frame - issue_start_frame BETWEEN 96 AND 360",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_video_repairs_status", "video_repairs", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        "ck_video_repairs_status",
        "video_repairs",
        "status IN ('draft','generating','candidate_ready','failed','approved',"
        "'rejected','outdated','cancelled','applied_to_draft')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check", schema=SCHEMA)
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','plan_shots','plan_series','plan_series_segment',"
        "'plan_series_episode','analyze_story_source','generate_image','diagnose_image',"
        "'generate_video','diagnose_video','regenerate_video_segment','render_export',"
        "'render_edit_preview','extract_continuity_frames')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    # A downgrade would destroy draft/review provenance and make short repairs unreadable.
    raise RuntimeError(
        "0027 contains immutable editing history; restore a pre-upgrade backup instead"
    )
