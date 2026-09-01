"""Align persisted constraint names with the ORM schema contract.

Revision ID: 0025_schema_contract_alignment
Revises: 0024_durable_task_events
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0025_schema_contract_alignment"
down_revision: str | Sequence[str] | None = "0024_durable_task_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    replacements = (
        (
            "character_design_revisions",
            "character_design_revisions_idempotency_key_key",
            "uq_character_design_revisions_idempotency",
            ["idempotency_key"],
        ),
        (
            "character_design_assets",
            "character_design_assets_asset_id_key",
            "uq_character_design_assets_asset",
            ["asset_id"],
        ),
        (
            "media_generation_batches",
            "media_generation_batches_idempotency_key_key",
            "uq_media_batches_idempotency",
            ["idempotency_key"],
        ),
    )
    for table_name, old_name, new_name, columns in replacements:
        op.drop_constraint(old_name, table_name, type_="unique", schema=schema)
        op.create_unique_constraint(new_name, table_name, columns, schema=schema)


def downgrade() -> None:
    schema = _schema()
    replacements = (
        (
            "character_design_revisions",
            "uq_character_design_revisions_idempotency",
            "character_design_revisions_idempotency_key_key",
            ["idempotency_key"],
        ),
        (
            "character_design_assets",
            "uq_character_design_assets_asset",
            "character_design_assets_asset_id_key",
            ["asset_id"],
        ),
        (
            "media_generation_batches",
            "uq_media_batches_idempotency",
            "media_generation_batches_idempotency_key_key",
            ["idempotency_key"],
        ),
    )
    for table_name, old_name, new_name, columns in replacements:
        op.drop_constraint(old_name, table_name, type_="unique", schema=schema)
        op.create_unique_constraint(new_name, table_name, columns, schema=schema)
