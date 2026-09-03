from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_obsolete_job_kind_migration_removes_the_base64_probe_kind() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "api"
        / "alembic"
        / "versions"
        / "0018_remove_obsolete_job_kinds.py"
    )
    spec = importlib.util.spec_from_file_location("remove_obsolete_job_kinds", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        module.upgrade()

    sql = output.getvalue()
    assert module.down_revision == "0017_project_library"
    assert "probe_segment_video_data_url" not in module.CURRENT_KINDS
    assert "RAISE EXCEPTION" in sql
    assert "DROP CONSTRAINT ck_jobs_kind" in sql
