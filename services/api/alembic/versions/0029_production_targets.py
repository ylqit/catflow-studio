"""Separate new import production goals from immutable source content."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0029_production_targets"
down_revision = "0028_video_trials_audio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "story_source_documents",
        sa.Column("production_targets_json", postgresql.JSONB(), nullable=True),
        schema="catflow",
    )
    op.add_column(
        "story_series",
        sa.Column(
            "adaptation_policy", sa.String(24), nullable=False, server_default="preserve_all"
        ),
        schema="catflow",
    )


def downgrade() -> None:
    raise RuntimeError("Production targets and adaptation history must be retained.")
