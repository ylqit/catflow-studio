"""Allow every intentional story import to create a new source document.

Revision ID: 0024_story_source_new_records
Revises: 0023_series_continuity_assets
"""

from __future__ import annotations

from alembic import op

revision = "0024_story_source_new_records"
down_revision = "0023_series_continuity_assets"
branch_labels = None
depends_on = None
SCHEMA = "catflow"


def upgrade() -> None:
    op.drop_constraint(
        "uq_story_source_content_hash",
        "story_source_documents",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_index(
        "ix_story_source_documents_content_hash",
        "story_source_documents",
        ["content_hash"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_story_source_documents_content_hash",
        table_name="story_source_documents",
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_story_source_content_hash",
        "story_source_documents",
        ["content_hash"],
        schema=SCHEMA,
    )
