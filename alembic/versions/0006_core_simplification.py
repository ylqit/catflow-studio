"""收敛为单一Episode脚本、single-pass步骤和类型化输入快照。

Revision ID: 0006_core_simplification
Revises: 0005_episode_prompt_overrides
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_core_simplification"
down_revision: str | Sequence[str] | None = "0005_episode_prompt_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SCHEMA = "cat_video"


def _schema() -> str:
    configured = op.get_context().config.attributes.get("schema")
    return str(configured or os.environ.get("CAT_VIDEO_DB_SCHEMA", DEFAULT_SCHEMA))


def _require_empty_runtime() -> None:
    """旧Run必须先完成可验证归档与显式清理，迁移绝不静默丢数据。"""

    schema = _schema()
    connection = op.get_bind()
    populated = {
        table: int(
            connection.execute(sa.text(f"SELECT count(*) FROM {schema}.{table}")).scalar_one()
        )
        for table in (
            "production_runs",
            "episodes",
            "workflow_steps",
            "prompt_records",
            "reviews",
            "delivery_packages",
            "delivery_items",
        )
    }
    non_canon = int(
        connection.execute(
            sa.text(f"SELECT count(*) FROM {schema}.assets WHERE scope <> 'canon'")
        ).scalar_one()
    )
    invalid_canon = int(
        connection.execute(
            sa.text(
                f"SELECT count(*) FROM {schema}.assets "
                "WHERE scope='canon' AND (status <> 'approved' OR semantic_key IS NULL "
                "OR semantic_key LIKE 'legacy:%')"
            )
        ).scalar_one()
    )
    if any(populated.values()) or non_canon or invalid_canon:
        raise RuntimeError(
            "0006迁移要求先归档并清空旧业务记录；"
            f"runtime={populated}, nonCanon={non_canon}, invalidCanon={invalid_canon}"
        )


def upgrade() -> None:
    schema = _schema()
    _require_empty_runtime()

    op.alter_column(
        "production_runs",
        "context_json",
        new_column_name="planning_json",
        schema=schema,
    )
    for column in ("theme", "plan_json", "selected_candidate", "archived_source"):
        op.drop_column("production_runs", column, schema=schema)

    op.drop_column("episodes", "title", schema=schema)
    op.drop_column("episodes", "video_input_mode", schema=schema)

    op.add_column(
        "workflow_steps",
        sa.Column("operation_key", sa.String(120), nullable=False),
        schema=schema,
    )
    op.alter_column(
        "workflow_steps",
        "request_summary_json",
        new_column_name="input_snapshot_json",
        schema=schema,
    )
    op.drop_constraint("ck_workflow_steps_kind", "workflow_steps", schema=schema)
    op.create_check_constraint(
        "ck_workflow_steps_kind",
        "workflow_steps",
        "kind IN ('director','image','video')",
        schema=schema,
    )
    op.create_index(
        "ix_workflow_steps_operation",
        "workflow_steps",
        ["episode_id", "kind", "operation_key", "attempt"],
        schema=schema,
    )

    op.drop_column("prompt_records", "char_count", schema=schema)
    op.drop_column("prompt_records", "utf8_bytes", schema=schema)

    op.drop_constraint("ck_production_runs_status", "production_runs", schema=schema)
    op.create_check_constraint(
        "ck_production_runs_status",
        "production_runs",
        "status IN ('draft','planning_review','planned','generating','reviewing',"
        "'ready','delivered','failed')",
        schema=schema,
    )
    op.drop_constraint("ck_episodes_status", "episodes", schema=schema)
    op.create_check_constraint(
        "ck_episodes_status",
        "episodes",
        "status IN ('planned','preparing_visuals','video_pending','video_generating',"
        "'media_qc','content_review','ready','failed')",
        schema=schema,
    )
    op.drop_constraint("ck_workflow_steps_status", "workflow_steps", schema=schema)
    op.create_check_constraint(
        "ck_workflow_steps_status",
        "workflow_steps",
        "status IN ('pending','submitting','submission_unknown','queued','running',"
        "'awaiting_review','succeeded','failed','expired','cancelled')",
        schema=schema,
    )
    op.drop_constraint("ck_assets_status", "assets", schema=schema)
    op.create_check_constraint(
        "ck_assets_status",
        "assets",
        "status IN ('candidate','approved','rejected','ready')",
        schema=schema,
    )


def downgrade() -> None:
    raise RuntimeError("核心Schema启用后禁止恢复已经归档的旧运行时结构")
