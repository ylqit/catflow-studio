"""Make video-repair failure a durable lifecycle state.

Revision ID: 0015_video_repair_failed
Revises: 0014_asset_job_lineage
"""

from alembic import op

revision = "0015_video_repair_failed"
down_revision = "0014_asset_job_lineage"
branch_labels = None
depends_on = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.drop_constraint(
        "ck_video_repairs_status",
        "video_repairs",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_video_repairs_status",
        "video_repairs",
        "status IN ('draft','generating','candidate_ready','failed','approved','rejected',"
        "'outdated','cancelled')",
        schema=SCHEMA,
    )
    op.execute(
        f"""
        UPDATE {SCHEMA}.video_repairs AS repair
        SET status = 'failed'
        FROM {SCHEMA}.jobs AS job
        WHERE job.video_repair_id = repair.id
          AND job.status = 'failed'
          AND repair.status = 'generating'
        """
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE {SCHEMA}.video_repairs SET status = 'outdated' WHERE status = 'failed'"
    )
    op.drop_constraint(
        "ck_video_repairs_status",
        "video_repairs",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_video_repairs_status",
        "video_repairs",
        "status IN ('draft','generating','candidate_ready','approved','rejected',"
        "'outdated','cancelled')",
        schema=SCHEMA,
    )
