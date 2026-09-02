"""Retain concurrent SHA deduplication for imported and uploaded assets.

Revision ID: 0016_unproduced_asset_dedup
Revises: 0015_video_repair_failed
"""

from alembic import op

revision = "0016_unproduced_asset_dedup"
down_revision = "0015_video_repair_failed"
branch_labels = None
depends_on = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.create_index(
        "uq_assets_project_sha_role_unproduced",
        "assets",
        ["project_id", "sha256", "role"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="producing_job_id IS NULL AND project_id IS NOT NULL",
    )
    op.create_index(
        "uq_assets_global_sha_role_unproduced",
        "assets",
        ["sha256", "role"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="producing_job_id IS NULL AND project_id IS NULL",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_assets_global_sha_role_unproduced",
        table_name="assets",
        schema=SCHEMA,
    )
    op.drop_index(
        "uq_assets_project_sha_role_unproduced",
        table_name="assets",
        schema=SCHEMA,
    )
