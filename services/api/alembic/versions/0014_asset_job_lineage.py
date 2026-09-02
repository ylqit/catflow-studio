"""Preserve one immutable asset lineage record for every producing job.

Revision ID: 0014_asset_job_lineage
Revises: 0013_video_edit_range
"""

from alembic import op

revision = "0014_asset_job_lineage"
down_revision = "0013_video_edit_range"
branch_labels = None
depends_on = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.drop_constraint(
        "uq_assets_project_sha_role",
        "assets",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_assets_job_role_candidate",
        "assets",
        ["producing_job_id", "role", "candidate_index"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_assets_job_role_candidate",
        "assets",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_assets_project_sha_role",
        "assets",
        ["project_id", "sha256", "role"],
        schema=SCHEMA,
    )
