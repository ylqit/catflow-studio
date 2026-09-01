from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0020_universal_media_canvas.py"
    spec = importlib.util.spec_from_file_location("universal_media_canvas_migration", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_universal_media_canvas_migration_renders_business_graph_and_edit_tables() -> None:
    migration = _load_migration()
    migration._schema = lambda: "cat_video"  # type: ignore[attr-defined]
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE cat_video.canvas_graph_nodes" in sql
    assert "CREATE TABLE cat_video.canvas_graph_edges" in sql
    assert "CREATE TABLE cat_video.media_generation_batches" in sql
    assert "CREATE TABLE cat_video.video_edit_recipes" in sql
    assert "CREATE TABLE cat_video.video_edit_annotations" in sql
    assert "ADD COLUMN canvas_node_id UUID" in sql
    assert "short_drama" in sql
    assert "ADD COLUMN universal_canvas_enabled BOOLEAN" in sql
    assert "ADD COLUMN product_ad_template_enabled BOOLEAN" in sql
    assert "ADD COLUMN video_edit_v2_enabled BOOLEAN" in sql
