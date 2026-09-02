"""Backfill product-facing video-edit fields in historical preview snapshots.

Revision ID: 0012_video_edit_preview
Revises: 0011_professional_refactor
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_video_edit_preview"
down_revision: str | Sequence[str] | None = "0011_professional_refactor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE {SCHEMA}.video_repairs
        SET preview_json = preview_json || jsonb_build_object(
            'editIntent', edit_intent,
            'instruction', instruction
        )
        WHERE NOT (preview_json ? 'editIntent')
           OR NOT (preview_json ? 'instruction')
        """
    )


def downgrade() -> None:
    # This migration enriches immutable historical snapshots. Removing the keys on downgrade
    # would also damage snapshots created by newer application versions.
    pass
