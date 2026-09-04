from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_shot_plan_review_migration_preserves_existing_versions_as_accepted() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "api"
        / "alembic"
        / "versions"
        / "0020_shot_plan_review_workflow.py"
    )
    spec = importlib.util.spec_from_file_location("shot_plan_review_workflow", path)
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
    assert migration.down_revision == "0019_project_scoped_environment"
    assert "ADD COLUMN review_status" in sql
    assert "SET review_status = 'accepted', decided_at = created_at" in sql
    assert "uq_shot_plan_versions_candidate" in sql
    assert "review_status = 'candidate'" in sql
    assert "DROP TABLE" not in sql
