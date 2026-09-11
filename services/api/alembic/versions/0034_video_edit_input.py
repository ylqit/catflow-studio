"""Durable editable video intent and explicit optional planning jobs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_video_edit_input"
down_revision = "0033_import_confirm_snapshot"
branch_labels = None
depends_on = None

KINDS = (
    "'plan_story','plan_shots','plan_series','plan_series_segment','plan_series_episode',"
    "'analyze_story_source','generate_image','diagnose_image','generate_video','diagnose_video',"
    "'regenerate_video_segment','render_export','render_edit_preview','extract_continuity_frames'"
)


def upgrade() -> None:
    op.add_column(
        "video_edit_drafts",
        sa.Column(
            "editing_input_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="catflow",
    )
    op.add_column(
        "video_edit_drafts",
        sa.Column("input_revision", sa.Integer(), nullable=False, server_default="0"),
        schema="catflow",
    )
    op.drop_constraint("ck_jobs_kind", "jobs", schema="catflow", type_="check")
    op.create_check_constraint(
        "ck_jobs_kind", "jobs", "kind IN (" + KINDS + ",'plan_video_edit')", schema="catflow"
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_kind", "jobs", schema="catflow", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", "kind IN (" + KINDS + ")", schema="catflow")
    op.drop_column("video_edit_drafts", "input_revision", schema="catflow")
    op.drop_column("video_edit_drafts", "editing_input_json", schema="catflow")
