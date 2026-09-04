from __future__ import annotations

import importlib.util
from pathlib import Path


def _table_order() -> tuple[str, ...]:
    path = Path(__file__).resolve().parents[2] / "scripts" / "local_backup.py"
    spec = importlib.util.spec_from_file_location("catflow_local_backup", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load backup script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TABLE_ORDER


def test_backup_covers_paid_authorization_and_project_scoped_environment_state() -> None:
    table_order = _table_order()

    assert "validation_runs" in table_order
    assert "environment_presets" not in table_order
    assert table_order.index("validation_runs") < table_order.index("jobs")
    assert table_order.index("assets") < table_order.index("project_selections")
    assert "provider_rate_cards" in table_order
