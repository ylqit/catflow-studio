"""Preserve the verified local result used to start an independent draft."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0030_candidate_lineage"
down_revision = "0029_production_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_edit_drafts",
        sa.Column(
            "source_result_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("catflow.jobs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        schema="catflow",
    )
    op.add_column(
        "video_edit_drafts",
        sa.Column("source_timeline_hash", sa.String(64), nullable=True),
        schema="catflow",
    )


def downgrade() -> None:
    raise RuntimeError("Candidate lineage must be retained.")
