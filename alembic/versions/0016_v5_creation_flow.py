"""Add the portable V5 creation flow.

Revision ID: 0016_v5_creation_flow
Revises: 0015_shot_queue_core
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016_v5_creation_flow"
down_revision: str | Sequence[str] | None = "0015_shot_queue_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def upgrade() -> None:
    schema = _schema()
    json_default = sa.text("'[]'::jsonb")
    op.add_column(
        "production_runs",
        sa.Column(
            "default_reference_bindings_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=json_default,
        ),
        schema=schema,
    )
    op.drop_constraint(
        "ck_production_runs_contract_version",
        "production_runs",
        type_="check",
        schema=schema,
    )
    op.execute(sa.text(f"UPDATE {schema}.production_runs SET contract_version = 5"))
    op.create_check_constraint(
        "ck_production_runs_contract_version",
        "production_runs",
        "contract_version = 5",
        schema=schema,
    )

    op.add_column(
        "scenes",
        sa.Column("story_mode", sa.String(16), nullable=False, server_default="single"),
        schema=schema,
    )
    op.add_column(
        "scenes",
        sa.Column("target_shot_count", sa.SmallInteger(), nullable=False, server_default="1"),
        schema=schema,
    )
    op.add_column(
        "scenes",
        sa.Column("look_plan_json", postgresql.JSONB(), nullable=True),
        schema=schema,
    )
    op.add_column(
        "scenes",
        sa.Column("selected_look_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {schema}.scenes AS scene
            SET story_mode = CASE WHEN counts.shot_count <= 1 THEN 'single' ELSE 'multi' END,
                target_shot_count = CASE
                    WHEN counts.shot_count <= 1 THEN 1
                    ELSE LEAST(counts.shot_count, 6)
                END
            FROM (
                SELECT scene_row.id, count(shot.id)::int AS shot_count
                FROM {schema}.scenes AS scene_row
                LEFT JOIN {schema}.shot_cards AS shot ON shot.scene_id = scene_row.id
                GROUP BY scene_row.id
            ) AS counts
            WHERE scene.id = counts.id
            """
        )
    )
    op.create_check_constraint(
        "ck_scenes_story_shape",
        "scenes",
        "(story_mode = 'single' AND target_shot_count = 1) OR "
        "(story_mode = 'multi' AND target_shot_count BETWEEN 2 AND 6)",
        schema=schema,
    )
    op.create_foreign_key(
        "fk_scenes_selected_look_asset",
        "scenes",
        "assets",
        ["selected_look_asset_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )

    op.add_column(
        "shot_cards",
        sa.Column(
            "inherit_project_references",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        schema=schema,
    )
    op.add_column(
        "shot_cards",
        sa.Column("use_scene_look", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema=schema,
    )

    op.add_column(
        "assets",
        sa.Column("storage_key", sa.Text(), nullable=True),
        schema=schema,
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {schema}.assets
            SET storage_key = COALESCE(
                substring(
                    replace(local_path, chr(92), '/')
                    from 'imported/sha256/.*$'
                ),
                substring(
                    replace(local_path, chr(92), '/')
                    from 'generated/sha256/.*$'
                ),
                'legacy:' || local_path
            )
            """
        )
    )
    op.alter_column("assets", "storage_key", nullable=False, schema=schema)
    op.drop_column("assets", "local_path", schema=schema)


def downgrade() -> None:
    raise RuntimeError("V5 portable asset storage cannot be downgraded safely")
