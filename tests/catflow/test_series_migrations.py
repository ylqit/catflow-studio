from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


def _render_migration(name: str) -> tuple[object, str]:
    path = Path(__file__).resolve().parents[2] / "services" / "api" / "alembic" / "versions" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
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
    return migration, output.getvalue()


def test_series_planning_migration_supports_series_scoped_jobs_and_lazy_projects() -> None:
    migration, sql = _render_migration("0021_story_series_planning.py")

    assert migration.down_revision == "0020_shot_plan_review_workflow"
    assert "CREATE TABLE catflow.story_series" in sql
    assert "CREATE TABLE catflow.series_plan_versions" in sql
    assert "CREATE TABLE catflow.series_episodes" in sql
    assert "CREATE TABLE catflow.series_episode_outline_versions" in sql
    assert "ALTER COLUMN project_id DROP NOT NULL" in sql
    assert "ADD COLUMN series_id" in sql
    assert "plan_series" in sql
    assert "plan_series_episode" in sql
    assert "analyze_story_source" in sql
    assert "extract_continuity_frames" in sql
    assert "DROP TABLE" not in sql


def test_story_source_and_continuity_migrations_form_a_linear_chain() -> None:
    source_migration, source_sql = _render_migration("0022_story_source_ingestion.py")
    continuity_migration, continuity_sql = _render_migration("0023_series_continuity_assets.py")

    assert source_migration.down_revision == "0021_story_series_planning"
    assert "CREATE TABLE catflow.story_source_documents" in source_sql
    assert "CREATE TABLE catflow.story_source_units" in source_sql
    assert "content_hash" in source_sql
    assert continuity_migration.down_revision == "0022_story_source_ingestion"
    assert "CREATE TABLE catflow.episode_continuity_snapshots" in continuity_sql
    assert "CREATE TABLE catflow.series_asset_bindings" in continuity_sql
    assert "CREATE TABLE catflow.episode_reference_manifests" in continuity_sql


def test_story_source_creation_semantics_keep_hash_as_a_non_unique_index() -> None:
    migration, sql = _render_migration("0024_story_source_creation_semantics.py")

    assert migration.down_revision == "0023_series_continuity_assets"
    assert "DROP CONSTRAINT uq_story_source_content_hash" in sql
    assert "CREATE INDEX ix_story_source_documents_content_hash" in sql
    assert "CREATE UNIQUE INDEX ix_story_source_documents_content_hash" not in sql


def test_flexible_series_planning_separates_beats_episode_targets_and_segments() -> None:
    migration, sql = _render_migration("0025_flexible_series_planning.py")

    assert migration.down_revision == "0024_story_source_new_records"
    assert "ADD COLUMN length_mode" in sql
    assert "ALTER COLUMN planned_episode_count DROP NOT NULL" in sql
    assert "TYPE BIGINT" in sql
    assert "CREATE TABLE catflow.series_source_bindings" in sql
    assert "CREATE TABLE catflow.series_episode_outline_source_coverage" in sql
    assert "CREATE TABLE catflow.series_plan_segments" in sql
    assert "CREATE TABLE catflow.series_plan_segment_versions" in sql
    assert "plan_series_segment" in sql
    assert "DROP TABLE catflow.story_series" not in sql


def test_repeat_source_materializations_keep_each_explicit_creation_independent() -> None:
    migration, sql = _render_migration("0026_repeat_source_materializations.py")

    assert migration.down_revision == "0025_flexible_series_planning"
    assert "DROP CONSTRAINT uq_story_source_materialization_suggestion" in sql
    assert "CREATE INDEX ix_story_source_materializations_suggestion_id" in sql
    assert "CREATE UNIQUE INDEX ix_story_source_materializations_suggestion_id" not in sql
