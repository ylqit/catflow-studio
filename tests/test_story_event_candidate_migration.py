from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0028_story_event_candidates.py"
    spec = importlib.util.spec_from_file_location("story_event_candidates", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_story_event_candidate_migration_renders_event_and_script_linkage() -> None:
    migration = _load_migration()
    assert migration.revision == "0028_story_event_candidates"  # type: ignore[attr-defined]
    assert migration.down_revision == "0027_story_scene_prompts"  # type: ignore[attr-defined]
    assert len(migration.revision) <= 32  # type: ignore[attr-defined]
    migration._schema = lambda: "cat_video"  # type: ignore[attr-defined]
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE cat_video.story_event_candidates" in sql
    assert "CONSTRAINT ck_story_event_candidates_status" in sql
    assert "CONSTRAINT uq_story_event_candidates_batch_index" in sql
    assert "ADD COLUMN source_event_candidate_id UUID" in sql
    assert "fk_story_revisions_source_event" in sql
    assert "ix_story_revisions_source_event" in sql
