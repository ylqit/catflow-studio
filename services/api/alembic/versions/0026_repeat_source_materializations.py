"""Allow one analyzed source suggestion to create more than one explicit target.

Revision ID: 0026_repeat_source_materializations
Revises: 0025_flexible_series_planning
"""

from __future__ import annotations

from alembic import op

revision = "0026_repeat_materializations"
down_revision = "0025_flexible_series_planning"
branch_labels = None
depends_on = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.drop_constraint(
        "uq_story_source_materialization_suggestion",
        "story_source_materializations",
        schema=SCHEMA,
        type_="unique",
    )
    op.create_index(
        "ix_story_source_materializations_suggestion_id",
        "story_source_materializations",
        ["suggestion_id"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_story_source_materializations_suggestion_id",
        table_name="story_source_materializations",
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        "uq_story_source_materialization_suggestion",
        "story_source_materializations",
        ["suggestion_id"],
        schema=SCHEMA,
    )
