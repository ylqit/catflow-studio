"""Make environment selection project-scoped and retire global presets.

Revision ID: 0019_project_scoped_environment
Revises: 0018_remove_obsolete_job_kinds
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_project_scoped_environment"
down_revision = "0018_remove_obsolete_job_kinds"
branch_labels = None
depends_on = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO catflow.project_selections (
            id,
            project_id,
            asset_id,
            slot,
            decision,
            source_hash,
            created_at
        )
        SELECT
            preset.id,
            preset.source_project_id,
            preset.asset_id,
            'environment',
            'selected',
            md5(preset.source_project_id::text || ':' || preset.asset_id::text)
                || md5(preset.asset_id::text || ':' || preset.source_project_id::text),
            preset.created_at
        FROM catflow.environment_presets AS preset
        JOIN catflow.assets AS asset
          ON asset.id = preset.asset_id
         AND asset.project_id = preset.source_project_id
         AND asset.role = 'environment'
         AND asset.media_type = 'image'
        WHERE NOT EXISTS (
            SELECT 1
            FROM catflow.project_selections AS selection
            WHERE selection.project_id = preset.source_project_id
              AND selection.asset_id = preset.asset_id
              AND selection.slot = 'environment'
              AND selection.decision IN ('selected', 'approved')
        )
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.drop_index(
        "uq_environment_presets_active",
        table_name="environment_presets",
        schema=SCHEMA,
    )
    op.drop_table("environment_presets", schema=SCHEMA)


def downgrade() -> None:
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
    op.execute(
        """
        INSERT INTO catflow.environment_presets (
            id, source_project_id, asset_id, active, created_at
        )
        SELECT
            selection.id,
            selection.project_id,
            selection.asset_id,
            true,
            selection.created_at
        FROM catflow.project_selections AS selection
        JOIN catflow.assets AS asset ON asset.id = selection.asset_id
        WHERE selection.slot = 'environment'
          AND selection.decision IN ('selected', 'approved')
          AND asset.project_id = selection.project_id
        ORDER BY selection.created_at DESC, selection.id DESC
        LIMIT 1
        """
    )
