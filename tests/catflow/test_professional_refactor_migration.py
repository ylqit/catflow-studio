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
        / "0011_professional_edit_billing_director.py"
    )
    spec = importlib.util.spec_from_file_location("professional_refactor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_professional_refactor_migration_preserves_history_and_adds_owned_state() -> None:
    migration = _load_migration()
    assert migration.revision == "0011_professional_refactor"  # type: ignore[attr-defined]
    assert migration.down_revision == "0010_media_publications"  # type: ignore[attr-defined]

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE catflow.provider_rate_cards" in sql
    assert "ADD COLUMN edit_intent" in sql
    assert "ADD COLUMN instruction" in sql
    assert "ADD COLUMN actual_cost_micros" in sql
    assert "ADD COLUMN pricing_snapshot_json" in sql
    assert "ADD COLUMN director_treatment_json" in sql
    assert "plan_shots" in sql
    assert "DROP TABLE catflow.video_repairs" not in sql
    assert "DROP TABLE catflow.validation_runs" not in sql
