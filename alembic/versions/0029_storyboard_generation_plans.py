"""Separate editorial storyboard revisions from provider generation clips.

Revision ID: 0029_storyboard_generation_plans
Revises: 0028_story_event_candidates
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029_storyboard_generation_plans"
down_revision: str | Sequence[str] | None = "0028_story_event_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def _digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_cat_action(value: Any) -> str:
    """Read the first legacy temporal beat without assuming valid historic JSON."""

    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        return "猫咪保持自然四足动作"
    return str(value[0].get("catAction") or "猫咪保持自然四足动作")


def upgrade() -> None:
    schema = _schema()
    op.create_table(
        "storyboard_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("production_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("story_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("structure_hash", sa.String(length=64), nullable=False),
        sa.Column("source_step_id", postgresql.UUID(as_uuid=True)),
        sa.Column("approved_structure_at", sa.DateTime(timezone=True)),
        sa.Column("production_package_hash", sa.String(length=64)),
        sa.Column("production_approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'structure_approved', 'production_approved', "
            "'changes_requested', 'superseded')",
            name="ck_storyboard_revisions_status",
        ),
        sa.ForeignKeyConstraint(
            ["production_run_id"], [f"{schema}.production_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["story_revision_id"], [f"{schema}.story_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_step_id"], [f"{schema}.workflow_steps.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "production_run_id", "revision", name="uq_storyboard_revisions_run_revision"
        ),
        schema=schema,
    )
    op.create_index(
        "ix_storyboard_revisions_current",
        "storyboard_revisions",
        ["production_run_id", "status", "revision"],
        schema=schema,
    )
    op.create_table(
        "generation_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storyboard_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=24), server_default="proposed", nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("capability_revision", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("estimated_image_call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_video_call_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_cost_micros", sa.BigInteger()),
        sa.Column(
            "warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "blockers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'approved', 'stale')",
            name="ck_generation_plans_status",
        ),
        sa.ForeignKeyConstraint(
            ["storyboard_revision_id"],
            [f"{schema}.storyboard_revisions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "storyboard_revision_id",
            "revision",
            name="uq_generation_plans_storyboard_revision",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_generation_plans_current",
        "generation_plans",
        ["storyboard_revision_id", "status", "revision"],
        schema=schema,
    )

    op.drop_constraint("ck_shot_cards_duration", "shot_cards", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_shot_cards_duration",
        "shot_cards",
        "duration_seconds BETWEEN 1 AND 60",
        schema=schema,
    )
    op.add_column(
        "shot_cards",
        sa.Column("generation_plan_id", postgresql.UUID(as_uuid=True)),
        schema=schema,
    )
    op.add_column(
        "shot_cards",
        sa.Column("plan_sort_order", sa.Integer()),
        schema=schema,
    )
    op.add_column(
        "shot_cards",
        sa.Column("prompt_id", postgresql.UUID(as_uuid=True)),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_shot_cards_generation_plan",
        "shot_cards",
        "generation_plans",
        ["generation_plan_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "uq_shot_cards_scene_order", "shot_cards", schema=schema, type_="unique"
    )
    op.create_unique_constraint(
        "uq_shot_cards_generation_plan_order",
        "shot_cards",
        ["generation_plan_id", "plan_sort_order"],
        schema=schema,
    )
    op.create_check_constraint(
        "ck_shot_cards_plan_sort_order",
        "shot_cards",
        "plan_sort_order IS NULL OR plan_sort_order >= 1",
        schema=schema,
    )
    op.create_foreign_key(
        "fk_shot_cards_prompt",
        "shot_cards",
        "prompt_records",
        ["prompt_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )

    beat_columns = [
        sa.Column("storyboard_revision_id", postgresql.UUID(as_uuid=True)),
        sa.Column("visual_description", sa.Text(), server_default="", nullable=False),
        sa.Column("child_action", sa.Text(), server_default="", nullable=False),
        sa.Column("cat_action", sa.Text(), server_default="", nullable=False),
        sa.Column("spatial_relation", sa.Text(), server_default="", nullable=False),
        sa.Column("contact_occlusion", sa.Text(), server_default="", nullable=False),
        sa.Column("shot_size", sa.String(length=200), server_default="中景", nullable=False),
        sa.Column("lighting", sa.Text(), server_default="", nullable=False),
        sa.Column("sound_effect", sa.Text(), server_default="", nullable=False),
        sa.Column("music_intent", sa.Text(), server_default="", nullable=False),
        sa.Column("wardrobe_state", sa.Text(), server_default="", nullable=False),
        sa.Column("prop_state", sa.Text(), server_default="", nullable=False),
        sa.Column("continuity_in", sa.Text(), server_default="", nullable=False),
        sa.Column("continuity_out", sa.Text(), server_default="", nullable=False),
        sa.Column("cut_intent", sa.String(length=24), server_default="continuous", nullable=False),
    ]
    for column in beat_columns:
        op.add_column("shot_beats", column, schema=schema)
    op.create_foreign_key(
        "fk_shot_beats_storyboard_revision",
        "shot_beats",
        "storyboard_revisions",
        ["storyboard_revision_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "uq_shot_beats_order_revision",
        "shot_beats",
        schema=schema,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_shot_beats_storyboard_order",
        "shot_beats",
        ["storyboard_revision_id", "scene_id", "sort_order"],
        schema=schema,
    )
    op.create_check_constraint(
        "ck_shot_beats_cut_intent",
        "shot_beats",
        "cut_intent IN ('continuous', 'soft_cut', 'hard_cut')",
        schema=schema,
    )

    op.create_table(
        "generation_clip_shots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shot_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("shot_beat_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_second", sa.SmallInteger(), nullable=False),
        sa.Column("end_second", sa.SmallInteger(), nullable=False),
        sa.Column(
            "transition_in", sa.String(length=24), server_default="continuous", nullable=False
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_generation_clip_shots_ordinal"),
        sa.CheckConstraint(
            "start_second >= 0 AND end_second > start_second",
            name="ck_generation_clip_shots_interval",
        ),
        sa.CheckConstraint(
            "transition_in IN ('continuous', 'soft_cut', 'hard_cut')",
            name="ck_generation_clip_shots_transition",
        ),
        sa.ForeignKeyConstraint(
            ["generation_plan_id"], [f"{schema}.generation_plans.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shot_card_id"], [f"{schema}.shot_cards.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shot_beat_id"], [f"{schema}.shot_beats.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generation_plan_id",
            "shot_card_id",
            "ordinal",
            name="uq_generation_clip_shots_clip_order",
        ),
        sa.UniqueConstraint(
            "generation_plan_id",
            "shot_beat_id",
            name="uq_generation_clip_shots_plan_beat",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_generation_clip_shots_plan_clip",
        "generation_clip_shots",
        ["generation_plan_id", "shot_card_id", "ordinal"],
        schema=schema,
    )
    _backfill_existing_storyboards(schema)


def _backfill_existing_storyboards(schema: str) -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            f"""
            WITH ranked AS (
                SELECT
                    beat.id,
                    beat.scene_id,
                    beat.shot_card_id,
                    beat.story_revision_id,
                    beat.prompt_id,
                    prompt.call_purpose AS prompt_call_purpose,
                    beat.sort_order,
                    beat.revision,
                    beat.title,
                    beat.action,
                    beat.camera,
                    beat.dialogue,
                    beat.duration_seconds,
                    beat.temporal_beats_json,
                    beat.status,
                    scene.production_run_id,
                    scene.sort_order AS scene_order,
                    story.revision AS story_version,
                    row_number() OVER (
                        PARTITION BY scene.production_run_id, beat.story_revision_id,
                                     beat.scene_id, beat.sort_order
                        ORDER BY beat.revision DESC, beat.created_at DESC, beat.id DESC
                    ) AS version_rank
                FROM {schema}.shot_beats AS beat
                JOIN {schema}.scenes AS scene ON scene.id = beat.scene_id
                JOIN {schema}.story_revisions AS story ON story.id = beat.story_revision_id
                LEFT JOIN {schema}.prompt_records AS prompt ON prompt.id = beat.prompt_id
                WHERE beat.status <> 'superseded'
                  AND beat.story_revision_id IS NOT NULL
            )
            SELECT *
            FROM ranked
            WHERE version_rank = 1
            ORDER BY production_run_id, story_version, scene_order, sort_order
            """
        )
    ).mappings()
    grouped: dict[tuple[uuid.UUID, uuid.UUID], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        document = dict(row)
        grouped[(document["production_run_id"], document["story_revision_id"])].append(
            document
        )

    storyboard_table = sa.table(
        "storyboard_revisions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("production_run_id", postgresql.UUID(as_uuid=True)),
        sa.column("story_revision_id", postgresql.UUID(as_uuid=True)),
        sa.column("revision", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("structure_hash", sa.String()),
        sa.column("approved_structure_at", sa.DateTime(timezone=True)),
        sa.column("production_package_hash", sa.String()),
        sa.column("production_approved_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    plan_table = sa.table(
        "generation_plans",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("storyboard_revision_id", postgresql.UUID(as_uuid=True)),
        sa.column("revision", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("provider", sa.String()),
        sa.column("model", sa.String()),
        sa.column("capability_revision", sa.String()),
        sa.column("input_hash", sa.String()),
        sa.column("estimated_image_call_count", sa.Integer()),
        sa.column("estimated_video_call_count", sa.Integer()),
        sa.column("estimated_cost_micros", sa.BigInteger()),
        sa.column("warnings_json", postgresql.JSONB()),
        sa.column("blockers_json", postgresql.JSONB()),
        sa.column("approved_at", sa.DateTime(timezone=True)),
        schema=schema,
    )
    mapping_table = sa.table(
        "generation_clip_shots",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("generation_plan_id", postgresql.UUID(as_uuid=True)),
        sa.column("shot_card_id", postgresql.UUID(as_uuid=True)),
        sa.column("shot_beat_id", postgresql.UUID(as_uuid=True)),
        sa.column("ordinal", sa.Integer()),
        sa.column("start_second", sa.SmallInteger()),
        sa.column("end_second", sa.SmallInteger()),
        sa.column("transition_in", sa.String()),
        schema=schema,
    )
    now = datetime.now(UTC)
    run_revision: dict[uuid.UUID, int] = defaultdict(int)
    for (project_id, story_id), beats in grouped.items():
        run_revision[project_id] += 1
        structure_document = [
            {
                "id": str(item["id"]),
                "sceneId": str(item["scene_id"]),
                "order": item["sort_order"],
                "revision": item["revision"],
                "durationSeconds": item["duration_seconds"],
                "childAction": item["action"],
                "catAction": _legacy_cat_action(item["temporal_beats_json"]),
                "spatialRelation": "儿童与猫咪保持同场连续关系",
                "camera": item["camera"],
                "continuityIn": "",
                "continuityOut": "",
                "wardrobeState": "",
                "propState": "",
                "cutIntent": "continuous",
            }
            for item in beats
        ]
        structure_hash = _digest(structure_document)
        all_approved = all(item["status"] == "approved" for item in beats)
        all_compiled = all(
            item["prompt_id"] is not None
            and item["prompt_call_purpose"] == "storyboard_prompt_compilation"
            for item in beats
        )
        status = (
            "production_approved"
            if all_approved and all_compiled
            else "structure_approved"
            if all_approved
            else "draft"
        )
        storyboard_id = uuid.uuid4()
        connection.execute(
            storyboard_table.insert(),
            {
                "id": storyboard_id,
                "production_run_id": project_id,
                "story_revision_id": story_id,
                "revision": run_revision[project_id],
                "status": status,
                "structure_hash": structure_hash,
                "approved_structure_at": None if status == "draft" else now,
                "production_package_hash": (
                    structure_hash if status == "production_approved" else None
                ),
                "production_approved_at": now if status == "production_approved" else None,
            },
        )
        for item in beats:
            connection.execute(
                sa.text(
                    f"""
                    UPDATE {schema}.shot_beats
                    SET storyboard_revision_id = :storyboard_id,
                        visual_description = action,
                        child_action = action,
                        cat_action = CASE
                            WHEN jsonb_typeof(temporal_beats_json) = 'array'
                                 AND jsonb_array_length(temporal_beats_json) > 0
                            THEN COALESCE(
                                NULLIF(temporal_beats_json->0->>'catAction', ''),
                                '猫咪保持自然四足动作'
                            )
                            ELSE '猫咪保持自然四足动作'
                        END,
                        spatial_relation = '儿童与猫咪保持同场连续关系',
                        shot_size = '中景',
                        cut_intent = 'continuous'
                    WHERE id = :beat_id
                    """
                ),
                {"storyboard_id": storyboard_id, "beat_id": item["id"]},
            )
        mapped_beats = [item for item in beats if item["shot_card_id"] is not None]
        if not mapped_beats:
            continue
        plan_id = uuid.uuid4()
        plan_document = [
            {
                "shotCardId": str(item["shot_card_id"]),
                "shotBeatId": str(item["id"]),
                "durationSeconds": item["duration_seconds"],
            }
            for item in mapped_beats
        ]
        connection.execute(
            plan_table.insert(),
            {
                "id": plan_id,
                "storyboard_revision_id": storyboard_id,
                "revision": 1,
                "status": "approved" if status != "draft" else "proposed",
                "provider": "ark",
                "model": "legacy-configured-video-model",
                "capability_revision": "legacy-8-15-v1",
                "input_hash": _digest({"structureHash": structure_hash, "clips": plan_document}),
                "estimated_image_call_count": len(mapped_beats),
                "estimated_video_call_count": len(mapped_beats),
                "estimated_cost_micros": None,
                "warnings_json": ["由 0029 迁移从旧的一镜一片段结构回填"],
                "blockers_json": [],
                "approved_at": now if status != "draft" else None,
            },
        )
        for clip_order, item in enumerate(mapped_beats, 1):
            connection.execute(
                mapping_table.insert(),
                {
                    "id": uuid.uuid4(),
                    "generation_plan_id": plan_id,
                    "shot_card_id": item["shot_card_id"],
                    "shot_beat_id": item["id"],
                    "ordinal": 1,
                    "start_second": 0,
                    "end_second": item["duration_seconds"],
                    "transition_in": "continuous",
                },
            )
            connection.execute(
                sa.text(
                    f"""
                    UPDATE {schema}.shot_cards
                    SET generation_plan_id = :plan_id,
                        plan_sort_order = :plan_sort_order,
                        prompt_id = :prompt_id
                    WHERE id = :shot_card_id
                    """
                ),
                {
                    "plan_id": plan_id,
                    "plan_sort_order": clip_order,
                    "prompt_id": (
                        item["prompt_id"]
                        if item["prompt_call_purpose"]
                        == "storyboard_prompt_compilation"
                        else None
                    ),
                    "shot_card_id": item["shot_card_id"],
                },
            )


def downgrade() -> None:
    schema = _schema()
    connection = op.get_bind()
    duplicate_order = connection.execute(
        sa.text(
            f"""
            SELECT scene_id, sort_order
            FROM {schema}.shot_cards
            GROUP BY scene_id, sort_order
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    unsupported_duration = connection.execute(
        sa.text(
            f"""
            SELECT id, duration_seconds
            FROM {schema}.shot_cards
            WHERE duration_seconds NOT BETWEEN 8 AND 15
            LIMIT 1
            """
        )
    ).first()
    duplicate_legacy_beat_revision = connection.execute(
        sa.text(
            f"""
            SELECT scene_id, sort_order, revision
            FROM {schema}.shot_beats
            GROUP BY scene_id, sort_order, revision
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if (
        duplicate_order is not None
        or unsupported_duration is not None
        or duplicate_legacy_beat_revision is not None
    ):
        raise RuntimeError(
            "0029 downgrade would discard versioned storyboards or generation clips, or "
            "reject valid 4-15 second model durations; archive or migrate those records "
            "explicitly first"
        )
    op.drop_index(
        "ix_generation_clip_shots_plan_clip",
        table_name="generation_clip_shots",
        schema=schema,
    )
    op.drop_table("generation_clip_shots", schema=schema)
    op.drop_constraint(
        "fk_shot_beats_storyboard_revision", "shot_beats", schema=schema, type_="foreignkey"
    )
    op.drop_constraint(
        "uq_shot_beats_storyboard_order",
        "shot_beats",
        schema=schema,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_shot_beats_order_revision",
        "shot_beats",
        ["scene_id", "sort_order", "revision"],
        schema=schema,
    )
    op.drop_constraint("ck_shot_beats_cut_intent", "shot_beats", schema=schema, type_="check")
    for column in (
        "cut_intent",
        "continuity_out",
        "continuity_in",
        "prop_state",
        "wardrobe_state",
        "music_intent",
        "sound_effect",
        "lighting",
        "shot_size",
        "contact_occlusion",
        "spatial_relation",
        "cat_action",
        "child_action",
        "visual_description",
        "storyboard_revision_id",
    ):
        op.drop_column("shot_beats", column, schema=schema)
    op.drop_constraint("fk_shot_cards_prompt", "shot_cards", schema=schema, type_="foreignkey")
    op.drop_constraint(
        "fk_shot_cards_generation_plan", "shot_cards", schema=schema, type_="foreignkey"
    )
    op.drop_constraint(
        "uq_shot_cards_generation_plan_order",
        "shot_cards",
        schema=schema,
        type_="unique",
    )
    op.drop_constraint(
        "ck_shot_cards_plan_sort_order",
        "shot_cards",
        schema=schema,
        type_="check",
    )
    op.create_unique_constraint(
        "uq_shot_cards_scene_order",
        "shot_cards",
        ["scene_id", "sort_order"],
        schema=schema,
    )
    op.drop_column("shot_cards", "prompt_id", schema=schema)
    op.drop_column("shot_cards", "plan_sort_order", schema=schema)
    op.drop_column("shot_cards", "generation_plan_id", schema=schema)
    op.drop_constraint("ck_shot_cards_duration", "shot_cards", schema=schema, type_="check")
    op.create_check_constraint(
        "ck_shot_cards_duration",
        "shot_cards",
        "duration_seconds BETWEEN 8 AND 15",
        schema=schema,
    )
    op.drop_index(
        "ix_generation_plans_current", table_name="generation_plans", schema=schema
    )
    op.drop_table("generation_plans", schema=schema)
    op.drop_index(
        "ix_storyboard_revisions_current", table_name="storyboard_revisions", schema=schema
    )
    op.drop_table("storyboard_revisions", schema=schema)
