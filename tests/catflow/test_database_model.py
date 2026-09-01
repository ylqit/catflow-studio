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

    assert {"input_hash", "idempotency_key", "provider_task_id", "frozen_input_json"} <= set(
        tables["jobs"].columns.keys()
    )
    assert tables["jobs"].columns.idempotency_key.unique is True
    assert {"storage_key", "sha256", "producing_job_id"} <= set(tables["assets"].columns.keys())
    assert {"source_story_version_id", "source_selection_hash", "shots_json"} <= set(
        tables["shot_plan_versions"].columns.keys()
    )
    assert {"source_selection_hash", "edl_json", "rendered_asset_id"} <= set(
        tables["edit_versions"].columns.keys()
    )
    assert "canon_snapshot_json" in tables["validation_runs"].columns
    assert "ck_validation_runs_canon_snapshot" in {
        constraint.name for constraint in tables["validation_runs"].constraints
    }


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
    for table_name in EXPECTED_TABLES - {"validation_runs", "environment_presets"}:
        assert f"CREATE TABLE {SCHEMA_NAME}.{table_name}" in sql
    assert "production_runs" not in sql
    assert "canvas_graph_nodes" not in sql
