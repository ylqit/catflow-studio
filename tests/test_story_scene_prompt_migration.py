from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = (
        PROJECT_ROOT
        / "alembic"
        / "versions"
        / "0027_story_scene_prompt_compilation.py"
    )
    spec = importlib.util.spec_from_file_location("story_scene_prompt_compilation", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_story_scene_prompt_migration_renders_versioned_scene_constraints() -> None:
    migration = _load_migration()
    assert migration.revision == "0027_story_scene_prompts"  # type: ignore[attr-defined]
    assert len(migration.revision) <= 32  # type: ignore[attr-defined]
    migration._schema = lambda: "cat_video"  # type: ignore[attr-defined]
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "DROP CONSTRAINT uq_scenes_run_order" in sql
    assert "ADD COLUMN story_revision_id UUID" in sql
    assert "ADD COLUMN scene_key VARCHAR(80)" in sql
    assert "ADD COLUMN active BOOLEAN DEFAULT true NOT NULL" in sql
    assert "ADD COLUMN stale_reason TEXT" in sql
    assert "CONSTRAINT uq_scenes_story_revision_key" in sql
    assert "CREATE UNIQUE INDEX uq_scenes_active_run_order" in sql
    assert "WHERE active = true" in sql
