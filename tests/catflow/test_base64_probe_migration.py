from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_base64_probe_migration_extends_only_the_job_kind_constraint() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "api"
        / "alembic"
        / "versions"
        / "0009_base64_video_transport_probe.py"
    )
    spec = importlib.util.spec_from_file_location("base64_video_transport_probe", path)
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
    assert len(module.revision) <= 32
    assert module.down_revision == "0008_validation_repair_snapshot"
    assert "probe_segment_video_data_url" in sql
    assert "DROP CONSTRAINT ck_jobs_kind" in sql
    assert "validation_runs" not in sql
