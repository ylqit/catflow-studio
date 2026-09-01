"""固定语义画布所需的非破坏性视频版本。

Revision ID: 0013_canvas_video_sequences
Revises: 0012_minimal_director_contract
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_canvas_video_sequences"
down_revision: str | Sequence[str] | None = "0012_minimal_director_contract"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    return os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "video_sequences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "episode_id",
            sa.Uuid(),
            sa.ForeignKey(f"{schema}.episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "parent_sequence_id",
            sa.Uuid(),
            sa.ForeignKey(f"{schema}.video_sequences.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "base_asset_id",
            sa.Uuid(),
            sa.ForeignKey(f"{schema}.assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "rendered_asset_id",
            sa.Uuid(),
            sa.ForeignKey(f"{schema}.assets.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "audio_policy",
            sa.String(32),
            nullable=False,
            server_default="preserve_original",
        ),
        sa.Column("clips_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("revision >= 1", name="ck_video_sequences_revision"),
        sa.CheckConstraint(
            "status IN ('draft', 'generating', 'content_review', 'approved', 'rejected')",
            name="ck_video_sequences_status",
        ),
        sa.CheckConstraint(
            "duration_ms > 0 AND duration_ms <= 45000",
            name="ck_video_sequences_duration",
        ),
        sa.CheckConstraint(
            "audio_policy = 'preserve_original'",
            name="ck_video_sequences_audio_policy",
        ),
        sa.UniqueConstraint("episode_id", "revision", name="uq_video_sequences_episode_revision"),
        schema=schema,
    )
    op.create_index(
        "ix_video_sequences_episode_status",
        "video_sequences",
        ["episode_id", "status", "revision"],
        schema=schema,
    )


def downgrade() -> None:
    op.drop_table("video_sequences", schema=_schema())
