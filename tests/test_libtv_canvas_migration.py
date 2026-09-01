from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0021_libtv_canvas_subject_assistant.py"
    spec = importlib.util.spec_from_file_location("libtv_canvas_subject_assistant", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_libtv_canvas_migration_renders_assistant_config_and_recovery_tables() -> None:
    migration = _load_migration()
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
    assert "CREATE TABLE cat_video.subject_completion_runs" in sql
    assert "CREATE TABLE cat_video.node_generation_configs" in sql
    assert "CREATE TABLE cat_video.canvas_recovery_points" in sql
    assert "ADD COLUMN failure_reason" in sql
    assert "ADD COLUMN last_confirmed_event_id" in sql
