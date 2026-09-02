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
        / "0008_validation_repair_snapshot.py"
    )
    spec = importlib.util.spec_from_file_location("validation_repair_snapshot", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validation_repair_migration_backfills_the_frozen_paid_range() -> None:
    migration = _load_migration()
    assert migration.revision == "0008_validation_repair_snapshot"  # type: ignore[attr-defined]
    assert migration.down_revision == "0007_video_repairs_edl_v2"  # type: ignore[attr-defined]

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "ADD COLUMN repair_snapshot_json JSONB" in sql
    assert "SET repair_snapshot_json = CAST" in sql
    assert "ALTER COLUMN repair_snapshot_json SET NOT NULL" in sql
