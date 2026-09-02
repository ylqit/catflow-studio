from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

from catflow.infrastructure.models import SCHEMA_NAME, Base

EXPECTED_TABLES = {
    "projects",
    "canon_profiles",
    "assets",
    "project_selections",
    "life_planner_sessions",
    "life_planner_messages",
    "life_planner_proposals",
    "story_versions",
    "shot_plan_versions",
    "jobs",
    "job_events",
    "edit_versions",
    "validation_runs",
    "environment_presets",
    "video_repairs",
    "media_publications",
    "provider_rate_cards",
}


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "api"
        / "alembic"
        / "versions"
        / "0001_catflow_core.py"
    )
    spec = importlib.util.spec_from_file_location("catflow_core_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metadata_contains_goal_tables_and_paid_validation_authority() -> None:
    assert {table.name for table in Base.metadata.tables.values()} == EXPECTED_TABLES
    assert all(table.schema == SCHEMA_NAME for table in Base.metadata.tables.values())
    assert "production_runs" not in Base.metadata.tables
    assert "canvas_graph_nodes" not in Base.metadata.tables


def test_core_constraints_keep_versions_jobs_and_media_recoverable() -> None:
    tables = {table.name: table for table in Base.metadata.tables.values()}

    assert {
        "input_hash",
        "idempotency_key",
        "provider_task_id",
        "frozen_input_json",
        "actual_cost_micros",
        "billing_status",
        "rate_card_revision",
        "pricing_snapshot_json",
        "provider_request_id",
    } <= set(
        tables["jobs"].columns.keys()
    )
    assert tables["jobs"].columns.idempotency_key.unique is True
    assert {"storage_key", "sha256", "producing_job_id"} <= set(tables["assets"].columns.keys())
    assert "uq_assets_job_role_candidate" in {
        constraint.name for constraint in tables["assets"].constraints
    }
    assert {
        "uq_assets_project_sha_role_unproduced",
        "uq_assets_global_sha_role_unproduced",
    } <= {index.name for index in tables["assets"].indexes}
    assert {
        "source_story_version_id",
        "source_selection_hash",
        "shots_json",
        "director_treatment_json",
        "director_prompt_revision",
        "director_model",
        "director_input_hash",
    } <= set(
        tables["shot_plan_versions"].columns.keys()
    )
    assert {
        "source_selection_hash",
        "edl_json",
        "rendered_asset_id",
        "parent_edit_version_id",
        "format_version",
        "active",
        "timeline_hash",
    } <= set(tables["edit_versions"].columns.keys())
    assert {
        "base_timeline_hash",
        "issue_start_frame",
        "issue_end_frame",
        "generation_start_frame",
        "generation_end_frame",
        "provider_duration_seconds",
        "approved_edit_version_id",
        "selection_policy_version",
        "edit_intent",
        "instruction",
    } <= set(tables["video_repairs"].columns.keys())
    assert {"canon_snapshot_json", "repair_snapshot_json"} <= set(
        tables["validation_runs"].columns.keys()
    )
    assert "ck_validation_runs_canon_snapshot" in {
        constraint.name for constraint in tables["validation_runs"].constraints
    }
    assert {
        "job_id",
        "source_asset_id",
        "object_key",
        "source_sha256",
        "state",
        "signed_url_expires_at",
        "delete_after",
    } <= set(tables["media_publications"].columns.keys())
    assert tables["media_publications"].columns.job_id.unique is True
    assert {
        "provider",
        "model",
        "metric",
        "unit",
        "unit_price_micros",
        "currency",
        "revision",
        "active",
    } <= set(tables["provider_rate_cards"].columns.keys())


def test_new_alembic_baseline_renders_the_original_goal_tables() -> None:
    migration = _load_migration()
    assert migration.revision == "0001_catflow_core"  # type: ignore[attr-defined]
    assert migration.down_revision is None  # type: ignore[attr-defined]

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    for table_name in EXPECTED_TABLES - {
        "validation_runs",
        "environment_presets",
        "video_repairs",
        "media_publications",
        "provider_rate_cards",
    }:
        assert f"CREATE TABLE {SCHEMA_NAME}.{table_name}" in sql
    assert "production_runs" not in sql
    assert "canvas_graph_nodes" not in sql
