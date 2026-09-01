from pathlib import Path

from cat_video_generator.infrastructure.db.models import CanvasEvent, WorkflowStep

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_durable_task_events_migration_declares_progress_and_sequence() -> None:
    migration = (
        PROJECT_ROOT / "alembic" / "versions" / "0024_durable_task_events.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0024_durable_task_events"' in migration
    assert 'down_revision: str | Sequence[str] | None = "0023_six_stage_canvas_groups"' in migration
    assert '"progress_json"' in migration
    assert '"sequence"' in migration
    assert '"ix_canvas_events_run_sequence"' in migration


def test_durable_task_models_expose_progress_and_monotonic_cursor() -> None:
    assert "progress_json" in WorkflowStep.__table__.columns
    assert "sequence" in CanvasEvent.__table__.columns
    assert CanvasEvent.__table__.columns["sequence"].identity is not None
