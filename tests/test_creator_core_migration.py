from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

from cat_video_generator.infrastructure.db.models import Base, SCHEMA_NAME
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0032_creator_core.py"
    spec = importlib.util.spec_from_file_location("creator_core_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_creator_core_migration_is_current_head_and_renders_minimal_tables() -> None:
    migration = _load_migration()
    assert migration.revision == "0032_creator_core"  # type: ignore[attr-defined]
    assert migration.down_revision == "0031_workflow_task_cancellation"  # type: ignore[attr-defined]
    assert ALEMBIC_HEAD == "0032_creator_core"

    migration._schema = lambda: SCHEMA_NAME  # type: ignore[attr-defined]
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE cat_video.creator_project_states" in sql
    assert "CREATE TABLE cat_video.creator_shots" in sql
    assert "CREATE TABLE cat_video.generation_snapshots" in sql
    assert "ADD COLUMN creator_shot_id UUID" in sql
    assert "ADD COLUMN generation_snapshot_id UUID" in sql


def test_creator_core_models_expose_only_the_minimal_creator_truth() -> None:
    tables = Base.metadata.tables
    project_columns = tables[f"{SCHEMA_NAME}.creator_project_states"].columns
    shot_columns = tables[f"{SCHEMA_NAME}.creator_shots"].columns
    snapshot_columns = tables[f"{SCHEMA_NAME}.generation_snapshots"].columns

    assert {
        "project_id",
        "version",
        "brief_body",
        "story_candidates_json",
        "current_story_json",
        "target_duration_seconds",
        "aspect_ratio",
        "quality_tier",
        "reference_bindings_json",
    } <= set(project_columns.keys())
    assert {
        "id",
        "project_id",
        "sort_order",
        "version",
        "title",
        "direction",
        "duration_seconds",
        "scene_label",
        "reference_bindings_json",
        "prompt_draft",
        "selected_video_asset_id",
    } <= set(shot_columns.keys())
    assert {
        "id",
        "project_id",
        "creator_shot_id",
        "kind",
        "prompt_text",
        "ordered_references_json",
        "provider_config_json",
        "input_hash",
        "estimated_cost_micros",
        "confirmed_at",
    } <= set(snapshot_columns.keys())

    workflow_columns = tables[f"{SCHEMA_NAME}.workflow_steps"].columns
    asset_columns = tables[f"{SCHEMA_NAME}.assets"].columns
    assert {"creator_shot_id", "generation_snapshot_id"} <= set(workflow_columns.keys())
    assert {"creator_shot_id", "generation_snapshot_id"} <= set(asset_columns.keys())
