"""Replace the fixed episode workflow with the V4 scene and shot queue core.

Revision ID: 0015_shot_queue_core
Revises: 0014_story_project_v3

This is intentionally a one-way production reset.  Runtime history must be
archived and removed before upgrading.  Approved Canon rows are copied through
the rebuild; no V2/V3 JSON is guessed or converted.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015_shot_queue_core"
down_revision: str | Sequence[str] | None = "0014_story_project_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def _timestamp(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _v4_metadata(schema: str) -> sa.MetaData:
    """Return the immutable schema snapshot owned by this revision."""

    metadata = sa.MetaData()
    uuid_type = postgresql.UUID(as_uuid=True)
    runs = sa.Table(
        "production_runs",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("content_date", sa.Date(), nullable=False),
        sa.Column("contract_version", sa.SmallInteger(), nullable=False, server_default="4"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("selected_sequence_id", uuid_type),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("status IN ('active', 'failed')", name="ck_production_runs_status"),
        sa.CheckConstraint("contract_version = 4", name="ck_production_runs_contract_version"),
        schema=schema,
    )
    sa.Index("ix_production_runs_queue", runs.c.status, runs.c.content_date, runs.c.created_at)

    scenes = sa.Table(
        "scenes",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "production_run_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("chapter_label", sa.String(80)),
        sa.Column("context_note", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("status IN ('draft', 'ready')", name="ck_scenes_status"),
        sa.CheckConstraint("sort_order >= 1", name="ck_scenes_sort_order"),
        sa.UniqueConstraint("production_run_id", "sort_order", name="uq_scenes_run_order"),
        schema=schema,
    )
    sa.Index(
        "ix_scenes_run_status", scenes.c.production_run_id, scenes.c.status, scenes.c.sort_order
    )

    shots = sa.Table(
        "shot_cards",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "scene_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.scenes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("anchor_mode", sa.String(24), nullable=False),
        sa.Column(
            "reference_bindings_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("selected_anchor_asset_id", uuid_type),
        sa.Column("selected_video_asset_id", uuid_type),
        sa.Column("status", sa.String(32), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint(
            "status IN ('ready', 'video_pending', 'approved')", name="ck_shot_cards_status"
        ),
        sa.CheckConstraint("sort_order >= 1", name="ck_shot_cards_sort_order"),
        sa.CheckConstraint("duration_seconds BETWEEN 8 AND 15", name="ck_shot_cards_duration"),
        sa.CheckConstraint(
            "anchor_mode IN ('text_only', 'existing', 'generate')",
            name="ck_shot_cards_anchor_mode",
        ),
        sa.UniqueConstraint("scene_id", "sort_order", name="uq_shot_cards_scene_order"),
        schema=schema,
    )
    sa.Index("ix_shot_cards_scene_status", shots.c.scene_id, shots.c.status, shots.c.sort_order)

    steps = sa.Table(
        "workflow_steps",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "production_run_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scene_id", uuid_type, sa.ForeignKey(f"{schema}.scenes.id", ondelete="CASCADE")),
        sa.Column(
            "shot_card_id", uuid_type, sa.ForeignKey(f"{schema}.shot_cards.id", ondelete="CASCADE")
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("operation_key", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64)),
        sa.Column("provider_task_id", sa.String(200)),
        sa.Column("model", sa.String(200)),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("input_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("error_json", postgresql.JSONB()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("kind IN ('director', 'image', 'video')", name="ck_workflow_steps_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'submitting', 'submission_unknown', 'queued', 'running', "
            "'awaiting_review', 'succeeded', 'failed', 'expired', 'cancelled')",
            name="ck_workflow_steps_status",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_workflow_steps_attempt"),
        sa.UniqueConstraint("idempotency_key", name="uq_workflow_steps_idempotency"),
        schema=schema,
    )
    sa.Index(
        "uq_workflow_steps_provider_task",
        steps.c.provider_task_id,
        unique=True,
        postgresql_where=steps.c.provider_task_id.is_not(None),
    )
    sa.Index("ix_workflow_steps_resume", steps.c.status, steps.c.kind, steps.c.created_at)
    sa.Index(
        "uq_workflow_steps_shot_attempt",
        steps.c.shot_card_id,
        steps.c.operation_key,
        steps.c.attempt,
        unique=True,
        postgresql_where=steps.c.shot_card_id.is_not(None),
    )
    sa.Index(
        "uq_workflow_steps_scene_attempt",
        steps.c.scene_id,
        steps.c.operation_key,
        steps.c.attempt,
        unique=True,
        postgresql_where=sa.and_(
            steps.c.scene_id.is_not(None),
            steps.c.shot_card_id.is_(None),
        ),
    )

    prompts = sa.Table(
        "prompt_records",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "step_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.workflow_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        _timestamp("created_at"),
        sa.CheckConstraint(
            "purpose IN ('director', 'image', 'video', 'review')",
            name="ck_prompt_records_purpose",
        ),
        sa.UniqueConstraint("step_id", "sha256", name="uq_prompt_records_step_hash"),
        schema=schema,
    )
    sa.Index("ix_prompt_records_sha256", prompts.c.sha256)

    assets = sa.Table(
        "assets",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "production_run_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
        ),
        sa.Column("scene_id", uuid_type, sa.ForeignKey(f"{schema}.scenes.id", ondelete="CASCADE")),
        sa.Column(
            "shot_card_id", uuid_type, sa.ForeignKey(f"{schema}.shot_cards.id", ondelete="CASCADE")
        ),
        sa.Column(
            "producing_step_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.workflow_steps.id", ondelete="SET NULL"),
        ),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("semantic_key", sa.String(160)),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer()),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        _timestamp("created_at"),
        sa.CheckConstraint(
            "scope IN ('canon', 'project', 'scene', 'shot')", name="ck_assets_scope"
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'approved', 'rejected', 'ready')", name="ck_assets_status"
        ),
        schema=schema,
    )
    sa.Index("ix_assets_sha256_role", assets.c.sha256, assets.c.role)
    sa.Index("ix_assets_shot_role", assets.c.shot_card_id, assets.c.role, assets.c.created_at)
    sa.Index(
        "ix_assets_semantic_selection",
        assets.c.scope,
        assets.c.semantic_key,
        assets.c.status,
        assets.c.created_at,
    )

    sequences = sa.Table(
        "video_sequences",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "production_run_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "parent_sequence_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.video_sequences.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "rendered_asset_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.assets.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("audio_policy", sa.String(32), nullable=False, server_default="native_fades"),
        sa.Column("clips_json", postgresql.JSONB(), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.CheckConstraint("revision >= 1", name="ck_video_sequences_revision"),
        sa.CheckConstraint(
            "status IN ('content_review', 'approved', 'rejected')", name="ck_video_sequences_status"
        ),
        sa.CheckConstraint("duration_ms > 0", name="ck_video_sequences_duration"),
        sa.CheckConstraint("audio_policy = 'native_fades'", name="ck_video_sequences_audio_policy"),
        sa.UniqueConstraint(
            "production_run_id", "revision", name="uq_video_sequences_run_revision"
        ),
        schema=schema,
    )
    sa.Index(
        "ix_video_sequences_run_status",
        sequences.c.production_run_id,
        sequences.c.status,
        sequences.c.revision,
    )

    sa.Table(
        "reviews",
        metadata,
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "step_id",
            uuid_type,
            sa.ForeignKey(f"{schema}.workflow_steps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("asset_id", uuid_type, sa.ForeignKey(f"{schema}.assets.id", ondelete="CASCADE")),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("warnings_json", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_json", postgresql.JSONB(), nullable=False),
        _timestamp("created_at"),
        sa.CheckConstraint(
            "source IN ('human', 'ark_visual', 'technical')", name="ck_reviews_source"
        ),
        sa.CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected')", name="ck_reviews_decision"
        ),
        schema=schema,
    )
    runs.append_constraint(
        sa.ForeignKeyConstraint(
            [runs.c.selected_sequence_id],
            [sequences.c.id],
            name="fk_production_runs_selected_sequence",
            use_alter=True,
            ondelete="SET NULL",
        )
    )
    shots.append_constraint(
        sa.ForeignKeyConstraint(
            [shots.c.selected_anchor_asset_id],
            [assets.c.id],
            name="fk_shot_cards_selected_anchor",
            use_alter=True,
            ondelete="SET NULL",
        )
    )
    shots.append_constraint(
        sa.ForeignKeyConstraint(
            [shots.c.selected_video_asset_id],
            [assets.c.id],
            name="fk_shot_cards_selected_video",
            use_alter=True,
            ondelete="SET NULL",
        )
    )
    return metadata


def upgrade() -> None:
    schema = _schema()
    connection = op.get_bind()
    run_count = connection.execute(
        sa.text(f"SELECT count(*) FROM {schema}.production_runs")
    ).scalar_one()
    non_canon_count = connection.execute(
        sa.text(f"SELECT count(*) FROM {schema}.assets WHERE scope <> 'canon'")
    ).scalar_one()
    invalid_canon_count = connection.execute(
        sa.text(
            f"SELECT count(*) FROM {schema}.assets WHERE scope='canon' AND status <> 'approved'"
        )
    ).scalar_one()
    if run_count or non_canon_count or invalid_canon_count:
        raise RuntimeError(
            "0015 requires archive_v3_and_clear.py first; "
            f"runs={run_count}, nonCanon={non_canon_count}, invalidCanon={invalid_canon_count}"
        )

    connection.execute(
        sa.text(
            f"CREATE TEMP TABLE canon_assets_0015 AS "
            f"SELECT id, role, semantic_key, status, media_type, local_path, sha256, "
            f"byte_size, metadata_json, created_at FROM {schema}.assets "
            "WHERE scope='canon' AND status='approved'"
        )
    )
    for table in (
        "delivery_items",
        "delivery_packages",
        "reviews",
        "prompt_records",
        "video_sequences",
        "workflow_steps",
        "shot_cards",
        "scenes",
        "episodes",
        "assets",
        "production_runs",
    ):
        connection.execute(sa.text(f"DROP TABLE IF EXISTS {schema}.{table} CASCADE"))

    _v4_metadata(schema).create_all(bind=connection)
    connection.execute(
        sa.text(
            f"INSERT INTO {schema}.assets "
            "(id, role, semantic_key, scope, status, media_type, local_path, sha256, "
            " byte_size, metadata_json, created_at) "
            "SELECT id, role, semantic_key, 'canon', status, media_type, local_path, sha256, "
            "byte_size, metadata_json, created_at FROM canon_assets_0015"
        )
    )


def downgrade() -> None:
    raise RuntimeError("V4 shot queue projects cannot be downgraded to fixed-slot contracts")
