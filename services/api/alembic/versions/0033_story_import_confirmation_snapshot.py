"""Persist the original story-import confirmation request for idempotent replay."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0033_import_confirm_snapshot"
down_revision = "0032_environment_draft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "story_source_materializations",
        sa.Column(
            "confirmation_request_snapshot_json",
            postgresql.JSONB(),
            nullable=True,
        ),
        schema="catflow",
    )


def downgrade() -> None:
    op.drop_column(
        "story_source_materializations",
        "confirmation_request_snapshot_json",
        schema="catflow",
    )
