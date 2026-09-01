"""Add typed AIGC canvas, generic subjects and durable audit state.

Revision ID: 0019_aigc_canvas_v2
Revises: 0018_v5_shot_assistance
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019_aigc_canvas_v2"
down_revision: str | Sequence[str] | None = "0018_v5_shot_assistance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", "cat_video"))


def _uuid() -> postgresql.UUID:
    return postgresql.UUID(as_uuid=True)


def _json() -> postgresql.JSONB:
    return postgresql.JSONB()


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    schema = _schema()
    op.add_column(
        "production_runs",
        sa.Column(
            "canvas_v2_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema=schema,
    )
    _extend_workflow_steps(schema)
    _extend_prompt_records(schema)
    _create_story_tables(schema)
    _create_subject_tables(schema)
    _create_story_revision_tables(schema)
    _create_beat_tables(schema)
    _create_canvas_and_capability_tables(schema)
    _migrate_legacy_projects(schema)


def _extend_workflow_steps(schema: str) -> None:
    for column in (
        sa.Column("lease_owner", sa.String(length=160)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("request_hash", sa.String(length=64)),
        sa.Column(
            "retry_chain_json",
            _json(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    ):
        op.add_column("workflow_steps", column, schema=schema)
    op.create_index(
        "ix_workflow_steps_claim",
        "workflow_steps",
        ["status", "next_retry_at", "lease_expires_at", "created_at"],
        schema=schema,
    )


def _extend_prompt_records(schema: str) -> None:
    columns = (
        sa.Column("call_purpose", sa.String(length=120)),
        sa.Column("node_id", _uuid()),
        sa.Column("business_object_type", sa.String(length=80)),
        sa.Column("business_object_id", _uuid()),
        sa.Column("parent_prompt_id", _uuid()),
        sa.Column("template_name", sa.String(length=160)),
        sa.Column("template_version", sa.String(length=80)),
        sa.Column("system_prompt", sa.Text()),
        sa.Column("user_prompt", sa.Text()),
        sa.Column("final_prompt", sa.Text()),
        sa.Column(
            "provider_request_json",
            _json(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "provider_internal_transform",
            sa.String(length=32),
            nullable=False,
            server_default="not_observable",
        ),
        sa.Column(
            "input_snapshot_json",
            _json(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("raw_response_json", _json()),
        sa.Column("structured_response_json", _json()),
        sa.Column("accepted_response_json", _json()),
        sa.Column("response_diff_json", _json()),
        sa.Column(
            "parameters_json",
            _json(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "token_usage_json",
            _json(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("cost_micros", sa.BigInteger()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="succeeded",
        ),
        sa.Column("error_json", _json()),
        sa.Column("input_hash", sa.String(length=64)),
        sa.Column("output_hash", sa.String(length=64)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    for column in columns:
        op.add_column("prompt_records", column, schema=schema)
    op.create_foreign_key(
        "fk_prompt_records_parent_prompt",
        "prompt_records",
        "prompt_records",
        ["parent_prompt_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_prompt_records_business_object",
        "prompt_records",
        ["business_object_type", "business_object_id", "created_at"],
        schema=schema,
    )
    op.execute(
        sa.text(
            f"UPDATE {schema}.prompt_records SET "
            "call_purpose = 'legacy_unavailable', "
            "template_name = 'legacy_unavailable', "
            "template_version = 'legacy_unavailable', "
            "final_prompt = prompt_text, input_hash = sha256 "
            "WHERE template_name IS NULL"
        )
    )


def _create_story_tables(schema: str) -> None:
    op.create_table(
        "story_briefs",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("theme", sa.Text(), nullable=False),
        sa.Column("audience", sa.String(length=300), nullable=False),
        sa.Column("genre", sa.String(length=200), nullable=False),
        sa.Column("tone", sa.String(length=300), nullable=False),
        sa.Column("aspect_ratio", sa.String(length=16), nullable=False),
        sa.Column("target_duration_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "constraints_json",
            _json(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        _created_at(),
        sa.CheckConstraint(
            "target_duration_seconds BETWEEN 5 AND 600",
            name="ck_story_briefs_duration",
        ),
        sa.UniqueConstraint(
            "production_run_id",
            "revision",
            name="uq_story_briefs_run_revision",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_story_briefs_current",
        "story_briefs",
        ["production_run_id", "revision"],
        schema=schema,
    )


def _create_subject_tables(schema: str) -> None:
    op.create_table(
        "subjects",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
        sa.Column("current_revision_id", _uuid()),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=schema,
    )
    op.create_index(
        "ix_subjects_run_role",
        "subjects",
        ["production_run_id", "role", "created_at"],
        schema=schema,
    )
    op.create_table(
        "subject_revisions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "subject_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("identity_anchors_json", _json(), nullable=False),
        sa.Column("immutable_traits_json", _json(), nullable=False),
        sa.Column("relationship_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("dramatic_function", sa.Text(), nullable=False, server_default=""),
        sa.Column("visual_risks_json", _json(), nullable=False),
        sa.Column("revision_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "approval_status",
            sa.String(length=24),
            nullable=False,
            server_default="draft",
        ),
        _created_at(),
        sa.UniqueConstraint(
            "subject_id",
            "revision",
            name="uq_subject_revisions_subject_revision",
        ),
        sa.UniqueConstraint(
            "subject_id",
            "revision_hash",
            name="uq_subject_revisions_subject_hash",
        ),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_subjects_current_revision",
        "subjects",
        "subject_revisions",
        ["current_revision_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_table(
        "subject_references",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "subject_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.subject_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("semantic_role", sa.String(length=40), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "subject_revision_id",
            "asset_id",
            "semantic_role",
            name="uq_subject_references_binding",
        ),
        schema=schema,
    )


def _create_story_revision_tables(schema: str) -> None:
    op.create_table(
        "story_revisions",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "brief_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.story_briefs.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "parent_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.story_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="candidate",
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("logline", sa.Text(), nullable=False),
        sa.Column("synopsis", sa.Text(), nullable=False),
        sa.Column("subject_ids_json", _json(), nullable=False),
        sa.Column("scene_plan_json", _json(), nullable=False),
        sa.Column(
            "candidate_prompt_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.prompt_records.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "critic_prompt_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.prompt_records.id", ondelete="SET NULL"),
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        _created_at(),
        sa.UniqueConstraint(
            "production_run_id",
            "revision",
            name="uq_story_revisions_run_revision",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_story_revisions_run_status",
        "story_revisions",
        ["production_run_id", "status", "revision"],
        schema=schema,
    )
    op.create_table(
        "story_scores",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "story_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.story_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("opening_hook", sa.SmallInteger(), nullable=False),
        sa.Column("causal_completeness", sa.SmallInteger(), nullable=False),
        sa.Column("subject_necessity", sa.SmallInteger(), nullable=False),
        sa.Column("emotional_arc", sa.SmallInteger(), nullable=False),
        sa.Column("visualizability", sa.SmallInteger(), nullable=False),
        sa.Column("duration_fit", sa.SmallInteger(), nullable=False),
        sa.Column("continuity_risk", sa.SmallInteger(), nullable=False),
        sa.Column("safety", sa.SmallInteger(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "warnings_json",
            _json(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        _created_at(),
        sa.CheckConstraint(
            "opening_hook BETWEEN 0 AND 10 AND "
            "causal_completeness BETWEEN 0 AND 10 AND "
            "subject_necessity BETWEEN 0 AND 10 AND "
            "emotional_arc BETWEEN 0 AND 10 AND "
            "visualizability BETWEEN 0 AND 10 AND "
            "duration_fit BETWEEN 0 AND 10 AND "
            "continuity_risk BETWEEN 0 AND 10 AND safety BETWEEN 0 AND 10",
            name="ck_story_scores_range",
        ),
        sa.UniqueConstraint("story_revision_id", name="uq_story_scores_revision"),
        schema=schema,
    )
    op.create_table(
        "scene_subject_bindings",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "scene_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.scenes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.subject_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "state_json", _json(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("action", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "relationship_json",
            _json(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "scene_id", "subject_revision_id", name="uq_scene_subject_binding"
        ),
        schema=schema,
    )


def _create_beat_tables(schema: str) -> None:
    op.create_table(
        "shot_beats",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "scene_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.scenes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "shot_card_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.shot_cards.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "story_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.story_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "prompt_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.prompt_records.id", ondelete="SET NULL"),
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("camera", sa.Text(), nullable=False, server_default=""),
        sa.Column("dialogue", sa.Text(), nullable=False, server_default=""),
        sa.Column("duration_seconds", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="draft"),
        sa.Column("stale_reason", sa.Text()),
        _created_at(),
        sa.CheckConstraint("duration_seconds > 0", name="ck_shot_beats_duration"),
        sa.UniqueConstraint(
            "scene_id",
            "sort_order",
            "revision",
            name="uq_shot_beats_order_revision",
        ),
        schema=schema,
    )
    op.create_index(
        "ix_shot_beats_story",
        "shot_beats",
        ["story_revision_id", "scene_id", "sort_order"],
        schema=schema,
    )
    op.create_table(
        "shot_subject_states",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "shot_beat_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.shot_beats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_revision_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.subject_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("start_state_json", _json(), nullable=False),
        sa.Column("end_state_json", _json(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False, server_default=""),
        sa.Column("interaction", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "shot_beat_id", "subject_revision_id", name="uq_shot_subject_state"
        ),
        schema=schema,
    )


def _create_canvas_and_capability_tables(schema: str) -> None:
    op.create_table(
        "canvas_layouts",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "nodes_json", _json(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "edges_json", _json(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "viewport_json", _json(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "operations_json", _json(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("sync_status", sa.String(length=24), nullable=False, server_default="saved"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("production_run_id", name="uq_canvas_layouts_run"),
        schema=schema,
    )
    op.create_table(
        "provider_capabilities",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("media_kind", sa.String(length=24), nullable=False),
        sa.Column("capabilities_json", _json(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "provider",
            "model",
            "media_kind",
            name="uq_provider_capabilities_model_kind",
        ),
        schema=schema,
    )
    op.create_table(
        "generation_attempts",
        sa.Column("id", _uuid(), primary_key=True),
        sa.Column(
            "production_run_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.production_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_step_id",
            _uuid(),
            sa.ForeignKey(f"{schema}.workflow_steps.id", ondelete="SET NULL"),
        ),
        sa.Column("retry_of_id", _uuid()),
        sa.Column("business_object_type", sa.String(length=80), nullable=False),
        sa.Column("business_object_id", _uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_task_id", sa.String(length=200)),
        sa.Column("request_json", _json(), nullable=False),
        sa.Column("response_json", _json()),
        sa.Column("error_json", _json()),
        sa.Column("cost_micros", sa.BigInteger()),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_generation_attempts_idempotency"),
        schema=schema,
    )
    op.create_foreign_key(
        "fk_generation_attempts_retry_of",
        "generation_attempts",
        "generation_attempts",
        ["retry_of_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_generation_attempts_object",
        "generation_attempts",
        ["business_object_type", "business_object_id"],
        schema=schema,
    )


def _migrate_legacy_projects(schema: str) -> None:
    # Deterministic UUIDs make the import repeatable in restored database snapshots.
    op.execute(
        sa.text(
            f"""
            INSERT INTO {schema}.story_briefs
                (id, production_run_id, revision, theme, audience, genre, tone,
                 aspect_ratio, target_duration_seconds, constraints_json)
            SELECT md5(p.id::text || '-legacy-brief')::uuid, p.id, 1,
                   COALESCE((
                       SELECT string_agg(s.source_text, E'\n\n' ORDER BY s.sort_order)
                       FROM {schema}.scenes s WHERE s.production_run_id = p.id
                   ), p.title),
                   'legacy_unavailable', 'legacy_import', 'legacy_unavailable', '9:16',
                   LEAST(600, GREATEST(5, COALESCE((
                       SELECT SUM(sc.duration_seconds)
                       FROM {schema}.shot_cards sc
                       JOIN {schema}.scenes s ON s.id = sc.scene_id
                       WHERE s.production_run_id = p.id
                   ), 60)))::int,
                   '["legacy_import"]'::jsonb
            FROM {schema}.production_runs p
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {schema}.subjects
                (id, production_run_id, kind, role, status)
            SELECT md5(p.id::text || suffix)::uuid, p.id, kind, role, 'approved'
            FROM {schema}.production_runs p
            CROSS JOIN (VALUES
                ('-legacy-person', 'person', 'protagonist'),
                ('-legacy-cat', 'animal', 'co_protagonist')
            ) AS legacy(suffix, kind, role)
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {schema}.subject_revisions
                (id, subject_id, revision, name, identity_anchors_json,
                 immutable_traits_json, relationship_notes, dramatic_function,
                 visual_risks_json, revision_hash, approval_status)
            SELECT md5(p.id::text || legacy.revision_suffix)::uuid,
                   md5(p.id::text || legacy.subject_suffix)::uuid, 1, legacy.name,
                   jsonb_build_array(CASE legacy.name
                       WHEN '旧人物主体' THEN vp.person_identity
                       ELSE vp.cat_identity END),
                   '[]'::jsonb, 'legacy_unavailable', 'legacy_import', '[]'::jsonb,
                   md5(p.id::text || legacy.revision_suffix), 'approved'
            FROM {schema}.production_runs p
            JOIN {schema}.visual_profile_revisions vp
              ON vp.id = p.current_visual_profile_revision_id
            CROSS JOIN (VALUES
                ('-legacy-person', '-legacy-person-rev1', '旧人物主体'),
                ('-legacy-cat', '-legacy-cat-rev1', '旧动物主体')
            ) AS legacy(subject_suffix, revision_suffix, name)
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {schema}.subjects subject
            SET current_revision_id = revision.id
            FROM {schema}.subject_revisions revision
            WHERE revision.subject_id = subject.id
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {schema}.story_revisions
                (id, production_run_id, brief_id, revision, strategy, status, title,
                 logline, synopsis, subject_ids_json, scene_plan_json, approved_at)
            SELECT md5(p.id::text || '-legacy-story')::uuid, p.id,
                   md5(p.id::text || '-legacy-brief')::uuid, 1, 'legacy_import',
                   'approved', p.title, p.title, b.theme,
                   jsonb_build_array(
                       md5(p.id::text || '-legacy-person')::text,
                       md5(p.id::text || '-legacy-cat')::text
                   ),
                   COALESCE((
                       SELECT jsonb_agg(jsonb_build_object(
                           'sceneId', s.id, 'title', s.title, 'sourceText', s.source_text
                       ) ORDER BY s.sort_order)
                       FROM {schema}.scenes s WHERE s.production_run_id = p.id
                   ), '[]'::jsonb), now()
            FROM {schema}.production_runs p
            JOIN {schema}.story_briefs b ON b.production_run_id = p.id AND b.revision = 1
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {schema}.shot_beats
                (id, scene_id, shot_card_id, story_revision_id, sort_order, revision,
                 title, action, camera, dialogue, duration_seconds, status)
            SELECT md5(sc.id::text || '-legacy-beat')::uuid, sc.scene_id, sc.id,
                   md5(s.production_run_id::text || '-legacy-story')::uuid,
                   sc.sort_order, 1, sc.title, sc.direction,
                   'legacy_unavailable', 'legacy_unavailable', sc.duration_seconds,
                   'legacy_import'
            FROM {schema}.shot_cards sc
            JOIN {schema}.scenes s ON s.id = sc.scene_id
            """
        )
    )


def downgrade() -> None:
    schema = _schema()
    op.drop_table("generation_attempts", schema=schema)
    op.drop_table("provider_capabilities", schema=schema)
    op.drop_table("canvas_layouts", schema=schema)
    op.drop_table("shot_subject_states", schema=schema)
    op.drop_table("shot_beats", schema=schema)
    op.drop_table("scene_subject_bindings", schema=schema)
    op.drop_table("story_scores", schema=schema)
    op.drop_table("story_revisions", schema=schema)
    op.drop_table("subject_references", schema=schema)
    op.drop_constraint(
        "fk_subjects_current_revision", "subjects", type_="foreignkey", schema=schema
    )
    op.drop_table("subject_revisions", schema=schema)
    op.drop_table("subjects", schema=schema)
    op.drop_table("story_briefs", schema=schema)
    op.drop_index("ix_prompt_records_business_object", table_name="prompt_records", schema=schema)
    op.drop_constraint(
        "fk_prompt_records_parent_prompt",
        "prompt_records",
        type_="foreignkey",
        schema=schema,
    )
    for column in (
        "completed_at",
        "output_hash",
        "input_hash",
        "error_json",
        "status",
        "duration_ms",
        "cost_micros",
        "token_usage_json",
        "parameters_json",
        "response_diff_json",
        "accepted_response_json",
        "structured_response_json",
        "raw_response_json",
        "input_snapshot_json",
        "provider_internal_transform",
        "provider_request_json",
        "final_prompt",
        "user_prompt",
        "system_prompt",
        "template_version",
        "template_name",
        "parent_prompt_id",
        "business_object_id",
        "business_object_type",
        "node_id",
        "call_purpose",
    ):
        op.drop_column("prompt_records", column, schema=schema)
    op.drop_index("ix_workflow_steps_claim", table_name="workflow_steps", schema=schema)
    for column in (
        "retry_chain_json",
        "request_hash",
        "next_retry_at",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
    ):
        op.drop_column("workflow_steps", column, schema=schema)
    op.drop_column("production_runs", "canvas_v2_enabled", schema=schema)
