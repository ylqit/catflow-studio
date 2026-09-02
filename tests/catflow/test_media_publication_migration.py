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
        / "0010_media_publications.py"
    )
    spec = importlib.util.spec_from_file_location("media_publications", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_media_publication_migration_adds_durable_object_lifecycle_records() -> None:
    migration = _load_migration()
    assert migration.revision == "0010_media_publications"  # type: ignore[attr-defined]
    assert migration.down_revision == "0009_base64_video_probe"  # type: ignore[attr-defined]
    assert len(migration.revision) <= 32  # type: ignore[attr-defined]

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE catflow.media_publications" in sql
    assert "UNIQUE (job_id)" in sql
    assert "ck_media_publications_state" in sql
    assert "ix_media_publications_cleanup" in sql
