from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, make_url

from catflow.application.provider_config import ProviderRuntime
from catflow.application.series import (
    SERIES_NORMALIZATION_REVISION,
    SeriesCreateCommand,
    SeriesPatchCommand,
    SeriesPlanDraft,
    SeriesPlanGenerationCommand,
    SeriesPlanMaterializeCommand,
)
from catflow.application.service import (
    StudioConflictError,
    StudioIdempotencyInputConflictError,
    StudioService,
)
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import (
    SeriesSourceBindingRecord,
    StorySeriesRecord,
    StorySourceDocumentRecord,
    StorySourceUnitRecord,
)
from catflow.infrastructure.postgres_repository import PostgresStudioRepository

_FIXTURES = Path(__file__).with_name("fixtures")


def _runtime() -> ProviderRuntime:
    return ProviderRuntime(
        provider="offline-test",
        planning_model="offline-planning",
        image_model="offline-image",
        video_model="offline-video",
        diagnostic_model="offline-diagnostic",
        capability_revision="offline-v1",
        paid_calls_enabled=True,
        maximum_video_references=5,
        segment_reference_publishing_ready=True,
    )


def _postgres_service() -> tuple[object, object, StudioService]:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    settings = DatabaseSettings.from_env()
    database_name = make_url(settings.url).database
    assert database_name is not None and "test" in database_name
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    return engine, sessions, StudioService(
        PostgresStudioRepository(sessions), provider_runtime=_runtime()
    )


def _rain_plan() -> SeriesPlanDraft:
    return SeriesPlanDraft.model_validate(
        json.loads((_FIXTURES / "series-reception-rain.json").read_text(encoding="utf-8"))
    )


def _bind_three_sources(sessions, series_id) -> None:
    document = StorySourceDocumentRecord(
        content_hash="d" * 64,
        source_format="paste",
        raw_text="雨天的三个连续生活片段",
        status="confirmed",
    )
    with sessions.begin() as session:
        session.add(document)
        session.flush()
        for ordinal in range(1, 4):
            unit = StorySourceUnitRecord(
                document_id=document.id,
                ordinal=ordinal,
                title=f"雨天片段 {ordinal}",
                raw_text=f"第 {ordinal} 个雨天生活片段",
                analysis_json={},
            )
            session.add(unit)
            session.flush()
            session.add(
                SeriesSourceBindingRecord(
                    id=uuid.uuid4(),
                    series_id=series_id,
                    source_unit_id=unit.id,
                    source_ordinal=ordinal,
                    binding_order=ordinal,
                )
            )


def _create_pending_plan(service: StudioService, sessions):
    series = service.create_story_series(
        SeriesCreateCommand(
            title="雨天三章",
            adaptationPolicy="condense_mainline",
            premise="孩子和猫咪从白天到夜晚一起度过雨天",
            narrativeMode="anthology",
            plannedEpisodeCount=3,
            defaultEpisodeDurationSeconds=15,
            worldSetting="同一栋住宅、门口与窗边",
            emotionalDirection="从好奇玩耍到安静依偎",
        )
    )
    _bind_three_sources(sessions, series.id)
    preview = service.preview_series_plan(series.id)
    job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey=f"offline-rain-plan-{series.id}",
        ),
    )
    candidate = service.complete_series_plan_job(job.id, _rain_plan())
    return series, preview, job, candidate


def _saved_command(candidate_id, settings_hash: str, key: str):
    return SeriesPlanMaterializeCommand(
        basePlanVersionId=candidate_id,
        source="saved_result",
        expectedSettingsHash=settings_hash,
        idempotencyKey=key,
    )


def test_postgres_saved_result_normalizes_persisted_plan_without_a_paid_job() -> None:
    engine, sessions, service = _postgres_service()
    series, preview, job, candidate = _create_pending_plan(service, sessions)
    try:
        created = service.materialize_series_plan(
            series.id,
            candidate.id,
            _saved_command(candidate.id, preview.settings_input_hash, "adopt-saved-rain-plan"),
        )
        replayed = service.materialize_series_plan(
            series.id,
            candidate.id,
            _saved_command(candidate.id, preview.settings_input_hash, "adopt-saved-rain-plan"),
        )

        assert replayed.id == created.id
        assert created.base_plan_version_id == candidate.id
        assert created.producing_job_id is None
        assert created.prompt_revision == "normalized-series-plan-v1"
        assert len(service.list_series_jobs(series.id)) == 1
        assert service.list_series_jobs(series.id)[0].id == job.id
        simplified = created.plan.source_treatments[1]
        assert simplified.treatment == "simplified"
        assert created.plan.episodes[1].source_coverage[0].coverage == "partial"
        normalization_issues = [
            issue for issue in created.issues
            if issue.normalization_revision == SERIES_NORMALIZATION_REVISION
        ]
        assert normalization_issues

        recovered = StudioService(
            PostgresStudioRepository(sessions), provider_runtime=_runtime()
        ).list_series_plan_versions(series.id)
        assert recovered[0].id == created.id
        assert recovered[0].plan == created.plan
    finally:
        with sessions.begin() as session:
            session.execute(delete(StorySeriesRecord).where(StorySeriesRecord.id == series.id))
        engine.dispose()


def test_postgres_saved_result_rejects_changed_key_input_stale_settings_and_old_base() -> None:
    engine, sessions, service = _postgres_service()
    series, preview, _job, candidate = _create_pending_plan(service, sessions)
    series2 = None
    try:
        key = "saved-result-conflict-key"
        service.materialize_series_plan(
            series.id,
            candidate.id,
            _saved_command(candidate.id, preview.settings_input_hash, key),
        )
        with pytest.raises(StudioIdempotencyInputConflictError):
            service.materialize_series_plan(
                series.id, candidate.id, _saved_command(candidate.id, "b" * 64, key)
            )
        with pytest.raises(StudioConflictError, match="pending"):
            service.materialize_series_plan(
                series.id,
                candidate.id,
                _saved_command(candidate.id, preview.settings_input_hash, "old-base-second-key"),
            )

        series2, preview2, _job2, candidate2 = _create_pending_plan(service, sessions)
        service.update_story_series(
            series2.id, SeriesPatchCommand(additionalNotes="改为更舒缓的镜头节奏")
        )
        with pytest.raises(StudioConflictError, match="settings changed"):
            service.materialize_series_plan(
                series2.id,
                candidate2.id,
                _saved_command(candidate2.id, preview2.settings_input_hash, "stale-settings-key"),
            )
    finally:
        with sessions.begin() as session:
            session.execute(
                delete(StorySeriesRecord).where(
                    StorySeriesRecord.id.in_(
                        [series.id] + ([series2.id] if series2 is not None else [])
                    )
                )
            )
        engine.dispose()


def test_postgres_concurrent_saved_result_replay_creates_one_derived_version() -> None:
    engine, sessions, service = _postgres_service()
    series, preview, _job, candidate = _create_pending_plan(service, sessions)
    command = _saved_command(
        candidate.id, preview.settings_input_hash, "concurrent-saved-result-adoption"
    )
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(service.materialize_series_plan, series.id, candidate.id, command)
                for _ in range(2)
            ]
            results = [future.result(timeout=10) for future in futures]

        assert results[0].id == results[1].id
        versions = service.list_series_plan_versions(series.id)
        assert len(versions) == 2
        assert sum(item.prompt_revision == "normalized-series-plan-v1" for item in versions) == 1
    finally:
        with sessions.begin() as session:
            session.execute(delete(StorySeriesRecord).where(StorySeriesRecord.id == series.id))
        engine.dispose()
