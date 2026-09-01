"""Persist one selected environment preset shared by every project.

Revision ID: 0004_shared_environment_presets
Revises: 0003_validation_job_scope
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_shared_environment_presets"
down_revision: str | Sequence[str] | None = "0003_validation_job_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.create_table(
        "environment_presets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_project_id"],
            [f"{SCHEMA}.projects.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            [f"{SCHEMA}.assets.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_environment_presets_active",
        "environment_presets",
        ["active"],
        unique=True,
        schema=SCHEMA,
        postgresql_where="active = true",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_environment_presets_active",
        table_name="environment_presets",
        schema=SCHEMA,
    )
    op.drop_table("environment_presets", schema=SCHEMA)
