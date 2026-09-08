"""Allow immutable v3 timelines with segmented audio; historical EDLs stay unchanged."""

from alembic import op

revision = "0028_video_trials_audio"
down_revision = "0027_video_edit_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_edit_versions_format_version", "edit_versions", schema="catflow")
    op.create_check_constraint(
        "ck_edit_versions_format_version",
        "edit_versions",
        "format_version IN (1,2,3)",
        schema="catflow",
    )


def downgrade() -> None:
    raise RuntimeError("v3 audio and trial history must not be discarded by downgrade")
