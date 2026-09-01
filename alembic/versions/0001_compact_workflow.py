"""Create the compact workflow schema.

Revision ID: 0001_compact_workflow
Revises:
Create Date: 2026-07-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_compact_workflow"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "cat_video"


def _select_schema() -> None:
    global SCHEMA
    SCHEMA = op.get_context().config.attributes.get("schema", "cat_video")


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    _select_schema()
    op.create_table(
        "production_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("content_date", sa.Date(), nullable=False),
        sa.Column("theme", sa.String(200)),
        sa.Column(
            "context_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("plan_json", postgresql.JSONB()),
        sa.Column("selected_candidate", sa.SmallInteger()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("archived_source", sa.String(64)),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('draft','planned','generating','reviewing','ready',"
            "'delivered','failed','archived')",
            name="ck_production_runs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_production_runs_queue",
        "production_runs",
        ["status", "content_date", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "episodes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("production_run_id", sa.UUID(), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("script_json", postgresql.JSONB(), nullable=False),
        sa.Column("video_input_mode", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("selected_video_asset_id", sa.UUID()),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('planned','preparing_visuals','video_pending',"
            "'video_generating','media_qc','content_review','ready','failed',"
            "'archived')",
            name="ck_episodes_status",
        ),
        sa.CheckConstraint(
            "(slot='morning' AND sort_order=1) OR "
            "(slot='noon' AND sort_order=2) OR "
            "(slot='evening' AND sort_order=3)",
            name="ck_episodes_slot_order",
        ),
        sa.ForeignKeyConstraint(
            ["production_run_id"],
            [f"{SCHEMA}.production_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "production_run_id",
            "slot",
            name="uq_episodes_run_slot",
        ),
        sa.UniqueConstraint(
            "production_run_id",
            "sort_order",
            name="uq_episodes_run_order",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("production_run_id", sa.UUID(), nullable=False),
        sa.Column("episode_id", sa.UUID()),
        sa.Column("parent_step_id", sa.UUID()),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64)),
        sa.Column("provider_task_id", sa.String(200)),
        sa.Column("model", sa.String(200)),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column(
            "request_summary_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_json", postgresql.JSONB()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('director','image','video','qc','review','delivery')",
            name="ck_workflow_steps_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','submitting','submission_unknown','queued',"
            "'running','awaiting_review','succeeded','failed','expired',"
            "'cancelled','archived')",
            name="ck_workflow_steps_status",
        ),
        sa.CheckConstraint("attempt>=1", name="ck_workflow_steps_attempt"),
        sa.ForeignKeyConstraint(
            ["production_run_id"],
            [f"{SCHEMA}.production_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            [f"{SCHEMA}.episodes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_step_id"],
            [f"{SCHEMA}.workflow_steps.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_workflow_steps_idempotency",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_workflow_steps_provider_task",
        "workflow_steps",
        ["provider_task_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("provider_task_id IS NOT NULL"),
    )
    op.create_index(
        "ix_workflow_steps_resume",
        "workflow_steps",
        ["status", "kind", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "prompt_records",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=False),
        sa.Column("parent_prompt_id", sa.UUID()),
        sa.Column("purpose", sa.String(24), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("utf8_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('director','image','video','review')",
            name="ck_prompt_records_purpose",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            [f"{SCHEMA}.workflow_steps.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_prompt_id"],
            [f"{SCHEMA}.prompt_records.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "step_id",
            "sha256",
            name="uq_prompt_records_step_hash",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_prompt_records_sha256",
        "prompt_records",
        ["sha256"],
        schema=SCHEMA,
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("production_run_id", sa.UUID()),
        sa.Column("episode_id", sa.UUID()),
        sa.Column("producing_step_id", sa.UUID()),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer()),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('canon','run','episode','delivery')",
            name="ck_assets_scope",
        ),
        sa.CheckConstraint(
            "status IN ('candidate','approved','rejected','ready','archived')",
            name="ck_assets_status",
        ),
        sa.ForeignKeyConstraint(
            ["production_run_id"],
            [f"{SCHEMA}.production_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            [f"{SCHEMA}.episodes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["producing_step_id"],
            [f"{SCHEMA}.workflow_steps.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_assets_sha256_role",
        "assets",
        ["sha256", "role"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_assets_run_episode_role",
        "assets",
        ["production_run_id", "episode_id", "role"],
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_episodes_selected_video_asset",
        "episodes",
        "assets",
        ["selected_video_asset_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )

    op.create_table(
        "reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("step_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID()),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "warnings_json",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('human','ark_visual','technical')",
            name="ck_reviews_source",
        ),
        sa.CheckConstraint(
            "decision IN ('pending','approved','rejected')",
            name="ck_reviews_decision",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            [f"{SCHEMA}.workflow_steps.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            [f"{SCHEMA}.assets.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )

    op.create_table(
        "delivery_packages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("production_run_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=False),
        sa.Column("manifest_sha256", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("revision>=1", name="ck_delivery_packages_revision"),
        sa.CheckConstraint(
            "status IN ('building','delivered','failed')",
            name="ck_delivery_packages_status",
        ),
        sa.ForeignKeyConstraint(
            ["production_run_id"],
            [f"{SCHEMA}.production_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "production_run_id",
            "revision",
            name="uq_delivery_packages_run_revision",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "delivery_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("delivery_package_id", sa.UUID(), nullable=False),
        sa.Column("episode_id", sa.UUID(), nullable=False),
        sa.Column("asset_id", sa.UUID(), nullable=False),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False),
        sa.Column("filename", sa.String(160), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "(slot='morning' AND sort_order=1) OR "
            "(slot='noon' AND sort_order=2) OR "
            "(slot='evening' AND sort_order=3)",
            name="ck_delivery_items_slot_order",
        ),
        sa.ForeignKeyConstraint(
            ["delivery_package_id"],
            [f"{SCHEMA}.delivery_packages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id"],
            [f"{SCHEMA}.episodes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            [f"{SCHEMA}.assets.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_package_id",
            "sort_order",
            name="uq_delivery_items_package_order",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    _select_schema()
    op.drop_table("delivery_items", schema=SCHEMA)
    op.drop_table("delivery_packages", schema=SCHEMA)
    op.drop_table("reviews", schema=SCHEMA)
    op.drop_constraint(
        "fk_episodes_selected_video_asset",
        "episodes",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("assets", schema=SCHEMA)
    op.drop_table("prompt_records", schema=SCHEMA)
    op.drop_table("workflow_steps", schema=SCHEMA)
    op.drop_table("episodes", schema=SCHEMA)
    op.drop_table("production_runs", schema=SCHEMA)
