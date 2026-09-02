"""Add durable S3-compatible segment-reference publication records.

Revision ID: 0010_media_publications
Revises: 0009_base64_video_probe
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_media_publications"
down_revision: str | Sequence[str] | None = "0009_base64_video_probe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.create_table(
        "media_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("backend", sa.String(length=16), nullable=False),
        sa.Column("bucket", sa.String(length=128), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("public_host", sa.String(length=255), nullable=False),
        sa.Column("signed_url_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('uploading','ready','delete_pending','deleted','failed')",
            name="ck_media_publications_state",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], [f"{SCHEMA}.jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_asset_id"], [f"{SCHEMA}.assets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_media_publications_cleanup",
        "media_publications",
        ["state", "delete_after"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_media_publications_cleanup",
        table_name="media_publications",
        schema=SCHEMA,
    )
    op.drop_table("media_publications", schema=SCHEMA)
