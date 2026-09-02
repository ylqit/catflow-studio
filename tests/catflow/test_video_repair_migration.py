from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "api"
        / "alembic"
        / "versions"
        / "0007_video_repairs_edl_v2.py"
    )
    spec = importlib.util.spec_from_file_location("video_repairs_edl_v2", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_video_repair_migration_adds_durable_repair_and_active_edit_boundaries() -> None:
    migration = _load_migration()
    assert migration.revision == "0007_video_repairs_edl_v2"  # type: ignore[attr-defined]
    assert migration.down_revision == "0006_validation_canon_required"  # type: ignore[attr-defined]

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE catflow.video_repairs" in sql
    assert "ADD COLUMN video_repair_id UUID" in sql
    assert "ADD COLUMN parent_edit_version_id UUID" in sql
    assert "ADD COLUMN format_version" in sql
    assert "ADD COLUMN active BOOLEAN" in sql
    assert "ADD COLUMN timeline_hash VARCHAR(64)" in sql
    assert "CREATE UNIQUE INDEX uq_edit_versions_active" in sql
    assert "regenerate_video_segment" in sql
