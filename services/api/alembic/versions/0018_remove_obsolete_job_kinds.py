"""Remove the retired Base64 video transport probe job kind.

Revision ID: 0018_remove_obsolete_job_kinds
Revises: 0017_project_library
"""

from alembic import op

revision = "0018_remove_obsolete_job_kinds"
down_revision = "0017_project_library"
branch_labels = None
depends_on = None

CURRENT_KINDS = (
    "plan_story",
    "plan_shots",
    "generate_image",
    "diagnose_image",
    "generate_video",
    "diagnose_video",
    "regenerate_video_segment",
    "render_export",
)
LEGACY_KINDS = (*CURRENT_KINDS[:-2], "probe_segment_video_data_url", *CURRENT_KINDS[-2:])


def _constraint(kinds: tuple[str, ...]) -> str:
    values = ",".join(f"'{kind}'" for kind in kinds)
    return f"kind IN ({values})"


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM catflow.jobs WHERE kind = 'probe_segment_video_data_url'
          ) THEN
            RAISE EXCEPTION 'obsolete probe jobs remain; run catflow cleanup audit/execute first';
          END IF;
        END $$
        """
    )
    op.drop_constraint("ck_jobs_kind", "jobs", schema="catflow", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", _constraint(CURRENT_KINDS), schema="catflow")


def downgrade() -> None:
    op.drop_constraint("ck_jobs_kind", "jobs", schema="catflow", type_="check")
    op.create_check_constraint("ck_jobs_kind", "jobs", _constraint(LEGACY_KINDS), schema="catflow")
