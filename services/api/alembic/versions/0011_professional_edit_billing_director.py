"""Add professional video-edit, billing, and director state.

Revision ID: 0011_professional_refactor
Revises: 0010_media_publications
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_professional_refactor"
down_revision: str | Sequence[str] | None = "0010_media_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catflow"


def upgrade() -> None:
    op.add_column(
        "video_repairs",
        sa.Column("edit_intent", sa.String(length=24), server_default="action", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "video_repairs",
        sa.Column("instruction", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.execute(f"UPDATE {SCHEMA}.video_repairs SET instruction = prompt WHERE instruction IS NULL")
    op.alter_column("video_repairs", "instruction", nullable=False, schema=SCHEMA)
    op.create_check_constraint(
        "ck_video_repairs_edit_intent",
        "video_repairs",
        "edit_intent IN ('action','character','object','environment','style')",
        schema=SCHEMA,
    )

    op.add_column(
        "jobs", sa.Column("actual_cost_micros", sa.BigInteger(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "jobs",
        sa.Column("currency", sa.String(length=3), server_default="CNY", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs",
        sa.Column("billing_status", sa.String(length=24), server_default="pending", nullable=False),
        schema=SCHEMA,
    )
    op.add_column(
        "jobs", sa.Column("rate_card_revision", sa.String(length=80), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "jobs", sa.Column("pricing_snapshot_json", postgresql.JSONB(), nullable=True), schema=SCHEMA
    )
    op.add_column(
        "jobs",
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_jobs_billing_status",
        "jobs",
        "billing_status IN "
        "('pending','usage_reported','calculated','unpriced','provider_adjusted')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','plan_shots','generate_image','diagnose_image','generate_video',"
        "'diagnose_video','probe_segment_video_data_url','regenerate_video_segment','render_export')",
        schema=SCHEMA,
    )

    op.add_column(
        "shot_plan_versions",
        sa.Column("director_treatment_json", postgresql.JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "shot_plan_versions",
        sa.Column("director_prompt_revision", sa.String(length=80), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "shot_plan_versions",
        sa.Column("director_model", sa.String(length=120), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "shot_plan_versions",
        sa.Column("director_input_hash", sa.String(length=64), nullable=True),
        schema=SCHEMA,
    )

    op.create_table(
        "provider_rate_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("metric", sa.String(length=40), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("unit_price_micros", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.String(length=80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("unit_price_micros >= 0", name="ck_rate_cards_nonnegative_price"),
        sa.CheckConstraint("currency = 'CNY'", name="ck_rate_cards_currency"),
        sa.CheckConstraint(
            "unit IN ('million_tokens','image','video_second')",
            name="ck_rate_cards_unit",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "model", "metric", "revision", name="uq_provider_rate_card_revision"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_provider_rate_cards_active",
        "provider_rate_cards",
        ["provider", "model", "active"],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_rate_cards_active", table_name="provider_rate_cards", schema=SCHEMA
    )
    op.drop_table("provider_rate_cards", schema=SCHEMA)
    for column in (
        "director_input_hash",
        "director_model",
        "director_prompt_revision",
        "director_treatment_json",
    ):
        op.drop_column("shot_plan_versions", column, schema=SCHEMA)
    op.drop_constraint("ck_jobs_kind", "jobs", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind IN ('plan_story','generate_image','diagnose_image','generate_video','diagnose_video',"
        "'probe_segment_video_data_url','regenerate_video_segment','render_export')",
        schema=SCHEMA,
    )
    op.drop_constraint("ck_jobs_billing_status", "jobs", schema=SCHEMA, type_="check")
    for column in (
        "provider_request_id",
        "pricing_snapshot_json",
        "rate_card_revision",
        "billing_status",
        "currency",
        "actual_cost_micros",
    ):
        op.drop_column("jobs", column, schema=SCHEMA)
    op.drop_constraint(
        "ck_video_repairs_edit_intent", "video_repairs", schema=SCHEMA, type_="check"
    )
    op.drop_column("video_repairs", "instruction", schema=SCHEMA)
    op.drop_column("video_repairs", "edit_intent", schema=SCHEMA)
