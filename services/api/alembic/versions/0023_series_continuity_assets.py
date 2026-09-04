"""Add immutable episode continuity and shared series asset bindings.

Revision ID: 0023_series_continuity_assets
Revises: 0022_story_source_ingestion
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_series_continuity_assets"
down_revision = "0022_story_source_ingestion"
branch_labels = None
depends_on = None
SCHEMA = "catflow"


def upgrade() -> None:
    op.create_table(
        "episode_continuity_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("direction", sa.String(length=12), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("decisions_json", postgresql.JSONB(), nullable=False),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "direction IN ('incoming','outgoing')", name="ck_episode_continuity_direction"
        ),
        sa.CheckConstraint(
            "source IN ('planned','confirmed','final_video')", name="ck_episode_continuity_source"
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], [f"{SCHEMA}.series_episodes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_episode_continuity_idempotency_key"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_episode_continuity_active",
        "episode_continuity_snapshots",
        ["episode_id", "direction"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )
    op.create_table(
        "series_asset_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_key", sa.String(length=80), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["series_id"], [f"{SCHEMA}.story_series.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["asset_id"], [f"{SCHEMA}.assets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_series_asset_binding_active",
        "series_asset_bindings",
        ["series_id", "binding_key"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("active = true"),
    )
    op.create_table(
        "project_asset_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_asset_binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["project_id"], [f"{SCHEMA}.projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["series_asset_binding_id"], [f"{SCHEMA}.series_asset_bindings.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_table(
        "episode_reference_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("continuity_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("references_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"], [f"{SCHEMA}.series_episodes.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], [f"{SCHEMA}.jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["continuity_snapshot_id"],
            [f"{SCHEMA}.episode_continuity_snapshots.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_episode_reference_manifest_job"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("episode_reference_manifests", schema=SCHEMA)
    op.drop_table("project_asset_bindings", schema=SCHEMA)
    op.drop_index(
        "uq_series_asset_binding_active", table_name="series_asset_bindings", schema=SCHEMA
    )
    op.drop_table("series_asset_bindings", schema=SCHEMA)
    op.drop_index(
        "uq_episode_continuity_active", table_name="episode_continuity_snapshots", schema=SCHEMA
    )
    op.drop_table("episode_continuity_snapshots", schema=SCHEMA)
