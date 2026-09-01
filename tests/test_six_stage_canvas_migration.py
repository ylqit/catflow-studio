from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

from cat_video_generator.infrastructure.db.models import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0023_six_stage_canvas_groups.py"
    spec = importlib.util.spec_from_file_location("six_stage_canvas_groups", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_six_stage_models_are_declared() -> None:
    tables = Base.metadata.tables

    assert "cat_video.character_design_revisions" in tables
    assert "cat_video.character_design_assets" in tables
    assert "cat_video.canvas_groups" in tables
    assert "cat_video.canvas_group_members" in tables
    assert "cat_video.canvas_group_templates" in tables
    assert "lifecycle_status" in tables["cat_video.production_recipe_instances"].c


def test_six_stage_migration_renders_tables_and_idempotent_group_backfill() -> None:
    migration = _load_migration()
    assert migration.down_revision == "0022_healing_child_cat_recipe"  # type: ignore[attr-defined]
    migration._schema = lambda: "cat_video"  # type: ignore[attr-defined]
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE cat_video.character_design_revisions" in sql
    assert "CREATE TABLE cat_video.character_design_assets" in sql
    assert "CREATE TABLE cat_video.canvas_groups" in sql
    assert "CREATE TABLE cat_video.canvas_group_members" in sql
    assert "CREATE TABLE cat_video.canvas_group_templates" in sql
    assert "node.node_type <> 'RecipeGroupNode'" in sql
    assert "ON CONFLICT (group_id, canvas_node_id) DO NOTHING" in sql
