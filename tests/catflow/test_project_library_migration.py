from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations

from catflow.infrastructure.models import Base


def test_project_library_metadata_owns_collections_tags_and_archive_state() -> None:
    tables = {table.name: table for table in Base.metadata.tables.values()}

    assert {"project_collections", "project_tags"} <= tables.keys()
    assert {"collection_id", "pinned_at", "archived_at"} <= set(tables["projects"].columns.keys())
    assert "uq_project_tags_project_normalized" in {
        constraint.name for constraint in tables["project_tags"].constraints
    }


def test_project_library_migration_preserves_projects_and_backfills_short_themes() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "api"
        / "alembic"
        / "versions"
        / "0017_project_library.py"
    )
    spec = importlib.util.spec_from_file_location("project_library_migration", path)
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
    assert migration.down_revision == "0016_unproduced_asset_dedup"
    assert "CREATE TABLE catflow.project_collections" in sql
    assert "CREATE TABLE catflow.project_tags" in sql
    assert "normalize(btrim(theme), NFKC)" in sql
    assert ") BETWEEN 1 AND 24" in sql
    assert "DELETE FROM catflow.projects" not in sql
