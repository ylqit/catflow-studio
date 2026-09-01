from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0019_aigc_canvas_v2.py"
    spec = importlib.util.spec_from_file_location("aigc_canvas_v2_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canvas_v2_migration_renders_valid_postgresql_sql() -> None:
    migration = _load_migration()
    migration._schema = lambda: "cat_video"  # type: ignore[attr-defined]
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "ADD COLUMN canvas_v2_enabled BOOLEAN DEFAULT false NOT NULL" in sql
    assert "CREATE TABLE cat_video.story_briefs" in sql
    assert "CREATE TABLE cat_video.subjects" in sql
    assert "CREATE TABLE cat_video.shot_beats" in sql
    assert "CREATE TABLE cat_video.generation_attempts" in sql
    assert "legacy_import" in sql
    assert "%(legacy)s" not in sql
