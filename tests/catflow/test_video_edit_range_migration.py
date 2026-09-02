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
        / "0013_video_edit_range_prompt_snapshot.py"
    )
    spec = importlib.util.spec_from_file_location("video_edit_range", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_video_edit_range_migration_preserves_legacy_rows_and_limits_new_edits() -> None:
    migration = _load_migration()
    assert migration.revision == "0013_video_edit_range"  # type: ignore[attr-defined]
    assert migration.down_revision == "0012_video_edit_preview"  # type: ignore[attr-defined]

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "ADD COLUMN selection_policy_version" in sql
    assert "SET selection_policy_version = 1" in sql
    assert "issue_end_frame - issue_start_frame < 96" in sql
    assert "status IN ('draft', 'generating', 'candidate_ready')" in sql
    assert (
        "selection_policy_version = 1 OR "
        "issue_end_frame - issue_start_frame BETWEEN 96 AND 360"
    ) in sql
    assert "DROP TABLE" not in sql
