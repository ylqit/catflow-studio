from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_project_scoped_environment_migration_preserves_only_source_project_selection() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "api"
        / "alembic"
        / "versions"
        / "0019_project_scoped_environment.py"
    )
    spec = importlib.util.spec_from_file_location("project_scoped_environment", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()

    sql = output.getvalue()
    assert migration.down_revision == "0018_remove_obsolete_job_kinds"
    assert "preset.source_project_id" in sql
    assert "asset.project_id = preset.source_project_id" in sql
    assert "DROP TABLE catflow.environment_presets" in sql
    assert "CROSS JOIN" not in sql
