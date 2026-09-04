from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete, make_url, select

from catflow.application.gateways import ProviderGatewayError
from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import (
    ProjectCreate,
    SegmentRepairCreateCommand,
    SegmentRepairPreviewCommand,
    StudioService,
)
from catflow.application.story_imports import StoryImportCreateCommand, StoryImportPreviewCommand
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import (
    JobEventRecord,
    JobRecord,
    ProjectRecord,
    StorySourceDocumentRecord,
)
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.runner import (
    DurableJobWorker,
    JobResultError,
    ProviderPoll,
    ProviderSubmission,
    ProviderTaskGateway,
)


class RecordingProvider(ProviderTaskGateway):
    def __init__(self) -> None:
        self.submissions: list[uuid.UUID] = []
        self.polls: list[str] = []
        self.cancellations: list[str] = []

    def prepare_submission(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> None:
        return None

    def submit(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> ProviderSubmission:
        self.submissions.append(job_id)
        return ProviderSubmission(taskId=f"provider-{job_id}")

    def poll(self, provider_task_id: str) -> ProviderPoll:
        self.polls.append(provider_task_id)
        return ProviderPoll(status="running")

    def cancel(self, provider_task_id: str) -> bool:
        self.cancellations.append(provider_task_id)
        return True


class SubmissionUnknownProvider(RecordingProvider):
    def __init__(self, sessions) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._sessions = sessions

    def submit(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> ProviderSubmission:
        with self._sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.provider_submission_started_at is not None
        self.submissions.append(job_id)
        raise ProviderGatewayError(
            code="provider_timeout",
            message="provider acceptance is unknown",
            retryable=False,
            submission_unknown=True,
            timed_out=True,
        )


class ImmediateProvider(RecordingProvider):
    def submit(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> ProviderSubmission:
        self.submissions.append(job_id)
        return ProviderSubmission(
            result={
                "proposal": {"title": "雨天擦爪"},
                "responseId": "planning-request-1",
            },
            usage={"inputTokens": 120, "outputTokens": 80},
        )


class FailedPreparationProvider(RecordingProvider):
    def prepare_submission(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> None:
        raise ValueError("local context extraction failed")


class IncompleteDirectorProvider(RecordingProvider):
    def submit(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> ProviderSubmission:
        self.submissions.append(job_id)
        raise ProviderGatewayError(
            code="response_not_completed",
            message="Ark response status is 'incomplete'",
            retryable=False,
            submission_unknown=False,
            request_id="response-incomplete-worker",
            provider_status="incomplete",
            incomplete_reason="max_output_tokens",
            max_output_tokens=8000,
            usage={"inputTokens": 321, "outputTokens": 8000, "totalTokens": 8321},
        )


class DeleteClaimedJobWorker(DurableJobWorker):
    def __init__(self, sessions, provider, *, worker_id: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(sessions, provider, worker_id=worker_id)
        self._test_sessions = sessions
        self._delete_next_claim = True

    def _claim(self):  # type: ignore[no-untyped-def]
        claimed = super()._claim()
        if claimed is not None and self._delete_next_claim:
            self._delete_next_claim = False
            with self._test_sessions.begin() as session:
                session.execute(delete(JobRecord).where(JobRecord.id == claimed[0]))
        return claimed


class ExplodingSubmitWorker(DurableJobWorker):
    def __init__(self, *args, after_boundary: bool, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self._after_boundary = after_boundary

    def _submit(self, job_id: uuid.UUID, *args) -> None:  # type: ignore[no-untyped-def]
        if self._after_boundary:
            assert self._begin_submission(job_id) is True
        raise RuntimeError("unexpected submit iteration failure")


class ExplodingPollWorker(DurableJobWorker):
    def _poll(self, job_id: uuid.UUID, provider_task_id: str | None) -> None:
        raise RuntimeError("unexpected poll iteration failure")


class InvalidDirectorResultHandler:
    def store_result(self, job_id: uuid.UUID) -> None:
        raise JobResultError(
            code="director_output_validation_failed",
            message="模型返回的分镜结构未通过校验，本次没有生成新版本。",
            detail="shots: List should have at most 4 items after validation",
        )


def _ark_runtime() -> ProviderRuntime:
    return ProviderRuntime(
        provider="ark",
        planning_model="planning",
        image_model="image",
        video_model="video",
        diagnostic_model="diagnostic",
        capability_revision="ark-seedance-2.0-v1",
        paid_calls_enabled=True,
        maximum_video_references=5,
        segment_reference_publishing_ready=True,
    )


def test_worker_events_keep_the_story_import_scope_of_the_claimed_job() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    source_text = "森林野餐：孩子整理野餐篮，猫咪把毛线球放进去。"
    preview = service.preview_story_import(
        StoryImportPreviewCommand(rawText=source_text, sourceFormat="paste")
    )
    created = service.create_story_import(
        StoryImportCreateCommand(
            rawText=source_text,
            sourceFormat="paste",
            expectedInputHash=preview.input_hash,
            idempotencyKey=f"worker-story-import-scope-{uuid.uuid4()}",
        )
    )
    assert created.analysis_job is not None
    try:
        provider = RecordingProvider()
        worker = DurableJobWorker(sessions, provider, worker_id="story-import-scope-worker")

        assert worker.run_once() is True

        with sessions() as session:
            persisted = session.get(JobRecord, created.analysis_job.id)
            assert persisted is not None
            assert persisted.status == "submitted"
            events = list(
                session.scalars(
                    select(JobEventRecord)
                    .where(JobEventRecord.job_id == created.analysis_job.id)
                    .order_by(JobEventRecord.id)
                )
            )
            assert events
            assert all(event.project_id is None for event in events)
            assert all(event.series_id is None for event in events)
            assert all(
                event.story_source_document_id == created.document.id for event in events
            )
    finally:
        with sessions.begin() as session:
            session.execute(
                delete(StorySourceDocumentRecord).where(
                    StorySourceDocumentRecord.id == created.document.id
                )
            )
        engine.dispose()


def test_local_input_preparation_failure_happens_before_provider_submission_boundary() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="输入准备失败", theme="雨天擦爪", targetDurationSeconds=12)
    )
    try:
        base = service.register_asset(
            project.id,
            role="video",
            media_type="video",
            sha256="e" * 64,
            metadata={
                "durationFrames": 288,
                "frameRateNumerator": 24,
                "frameRateDenominator": 1,
            },
        )
        environment = service.register_asset(
            project.id,
            role="environment",
            media_type="image",
            sha256="f" * 64,
        )
        service.select_asset(project.id, slot="video", asset_id=base.id)
        service.select_asset(project.id, slot="environment", asset_id=environment.id)
        for index, role in enumerate(
            ("episode_child", "episode_cat", "pair_scale", "style_board"),
            start=1,
        ):
            reference = service.register_asset(
                project.id,
                role=role,
                media_type="image",
                sha256=f"{index}" * 64,
            )
            service.select_asset(project.id, slot=role, asset_id=reference.id)
        preview = service.preview_video_repair(
            project.id,
            SegmentRepairPreviewCommand(
                baseVideoAssetId=base.id,
                issueRange={"startFrame": 0, "endFrame": 96},
                instruction="修正擦爪动作。",
            ),
        )
        created = service.create_video_repair_job(
            project.id,
            SegmentRepairCreateCommand(
                baseVideoAssetId=base.id,
                issueRange={"startFrame": 0, "endFrame": 96},
                instruction="修正擦爪动作。",
                expectedInputHash=preview.input_hash,
                idempotencyKey=f"prepare-failure-{project.id}",
            ),
        )
        assert created.video_repair_id is not None

        provider = FailedPreparationProvider()
        worker = DurableJobWorker(sessions, provider, worker_id="prepare-failure")

        assert worker.run_once() is True
        with sessions() as session:
            job = session.get(JobRecord, created.id)
            assert job is not None
            assert job.status == "failed"
            assert job.provider_submission_started_at is None
            assert job.error_json["code"] == "provider_input_preparation_failed"
        assert service.get_video_repair(created.video_repair_id).status == "failed"
        assert provider.submissions == []
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_worker_persists_provider_task_before_polling_and_never_resubmits() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    settings = DatabaseSettings.from_env()
    database_name = make_url(settings.url).database
    assert database_name is not None
    assert database_name.startswith("catflow_studio_test_")
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="Worker 恢复测试", theme="纸星星", targetDurationSeconds=8)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="queued",
                    input_hash="a" * 64,
                    idempotency_key=f"worker-{job_id}",
                    provider="ark",
                    model="video-model",
                    expected_cost_micros=None,
                    frozen_input_json={"prompt": "纸星星"},
                )
            )

        provider = RecordingProvider()
        first_worker = DurableJobWorker(sessions, provider, worker_id="worker-first")
        second_worker = DurableJobWorker(sessions, provider, worker_id="worker-after-restart")

        assert first_worker.run_once() is True
        with sessions() as session:
            submitted = session.scalar(select(JobRecord).where(JobRecord.id == job_id))
            assert submitted is not None
            assert submitted.status == "submitted"
            assert submitted.provider_task_id == f"provider-{job_id}"
        assert provider.submissions == [job_id]

        assert second_worker.run_once() is True
        assert provider.submissions == [job_id]
        assert provider.polls == [f"provider-{job_id}"]
        assert second_worker.run_once() is False
        assert provider.polls == [f"provider-{job_id}"]
        with sessions() as session:
            polling = session.get(JobRecord, job_id)
            assert polling is not None
            assert polling.status == "polling"
            assert polling.leased_until is not None
            assert polling.leased_until > datetime.now(UTC)
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_worker_persists_submission_boundary_and_never_retries_unknown_acceptance() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="提交不确定测试", theme="雨天擦爪", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="queued",
                    input_hash="b" * 64,
                    idempotency_key=f"unknown-{job_id}",
                    provider="ark",
                    model="video-model",
                    expected_cost_micros=None,
                    frozen_input_json={"prompt": "雨天擦爪"},
                )
            )

        provider = SubmissionUnknownProvider(sessions)
        worker = DurableJobWorker(sessions, provider, worker_id="unknown-worker")

        assert worker.run_once() is True
        assert worker.run_once() is False

        with sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.status == "submission_unknown"
            assert persisted.provider_submission_started_at is not None
            assert persisted.error_json == {
                "code": "provider_timeout",
                "message": "provider acceptance is unknown",
                "retryable": False,
                "submissionUnknown": True,
                "requestId": None,
                "timedOut": True,
            }
        assert provider.submissions == [job_id]
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_worker_does_not_resubmit_a_started_job_after_process_restart() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="重启边界测试", theme="线团", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="submitting",
                    input_hash="c" * 64,
                    idempotency_key=f"restart-unknown-{job_id}",
                    provider="ark",
                    model="video-model",
                    expected_cost_micros=None,
                    frozen_input_json={"prompt": "线团"},
                    provider_submission_started_at=datetime.now(UTC),
                )
            )

        provider = RecordingProvider()
        worker = DurableJobWorker(sessions, provider, worker_id="restart-worker")

        assert worker.run_once() is True

        with sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.status == "submission_unknown"
        assert provider.submissions == []
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_worker_persists_immediate_provider_result_before_finishing_job() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="同步结果测试", theme="雨天擦爪", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="plan_story",
                    status="queued",
                    input_hash="d" * 64,
                    idempotency_key=f"immediate-{job_id}",
                    provider="ark",
                    model="planning-model",
                    expected_cost_micros=None,
                    frozen_input_json={"text": "雨天擦爪"},
                    pricing_snapshot_json={
                        "revision": "ark-planning-2026-09-02",
                        "sourceUrl": "https://example.test/rates",
                        "rates": [
                            {
                                "metric": "inputTokens",
                                "unit": "million_tokens",
                                "unitPriceMicros": 2_000_000,
                            },
                            {
                                "metric": "outputTokens",
                                "unit": "million_tokens",
                                "unitPriceMicros": 5_000_000,
                            },
                        ],
                    },
                )
            )

        provider = ImmediateProvider()
        worker = DurableJobWorker(sessions, provider, worker_id="immediate-worker")

        assert worker.run_once() is True

        with sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.status == "succeeded"
            assert persisted.provider_task_id is None
            assert persisted.provider_result_json == {
                "proposal": {"title": "雨天擦爪"},
                "responseId": "planning-request-1",
            }
            assert persisted.actual_usage_json == {
                "inputTokens": 120,
                "outputTokens": 80,
            }
            assert persisted.provider_request_id == "planning-request-1"
            assert persisted.actual_cost_micros == 640
            assert persisted.billing_status == "calculated"
            assert persisted.rate_card_revision == "ark-planning-2026-09-02"
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_worker_preserves_incomplete_director_reason_response_and_usage() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="分镜未完成记录", theme="浇花", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="plan_shots",
                    status="queued",
                    input_hash="f" * 64,
                    idempotency_key=f"incomplete-director-{job_id}",
                    provider="ark",
                    model="planning-model",
                    expected_cost_micros=None,
                    frozen_input_json={"storyVersionId": str(uuid.uuid4())},
                )
            )

        worker = DurableJobWorker(
            sessions,
            IncompleteDirectorProvider(),
            worker_id="incomplete-director-worker",
        )
        assert worker.run_once() is True

        with sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.status == "failed"
            assert persisted.provider_request_id == "response-incomplete-worker"
            assert persisted.actual_usage_json == {
                "inputTokens": 321,
                "outputTokens": 8000,
                "totalTokens": 8321,
            }
            assert persisted.error_json["incompleteReason"] == "max_output_tokens"
            assert persisted.error_json["maxOutputTokens"] == 8000
            assert persisted.billing_status == "unpriced"
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_claimed_job_disappearing_does_not_stop_the_worker() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="任务消失隔离", theme="纸星星", targetDurationSeconds=8)
    )
    first_job_id = uuid.uuid4()
    second_job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            for job_id, created_at in (
                (first_job_id, datetime(2026, 1, 1, tzinfo=UTC)),
                (second_job_id, datetime(2026, 1, 2, tzinfo=UTC)),
            ):
                session.add(
                    JobRecord(
                        id=job_id,
                        project_id=project.id,
                        kind="generate_video",
                        status="queued",
                        input_hash=str(job_id).replace("-", "") * 2,
                        idempotency_key=f"disappearing-{job_id}",
                        provider="ark",
                        model="video-model",
                        expected_cost_micros=None,
                        frozen_input_json={"prompt": "纸星星"},
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )

        provider = RecordingProvider()
        worker = DeleteClaimedJobWorker(
            sessions,
            provider,
            worker_id="disappearing-job-worker",
        )

        assert worker.run_once() is True
        assert provider.submissions == []
        assert worker.run_once() is True
        assert provider.submissions == [second_job_id]
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_unexpected_error_before_submission_is_isolated_and_failed() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="提交前异常隔离", theme="纸星星", targetDurationSeconds=8)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="queued",
                    input_hash="1" * 64,
                    idempotency_key=f"before-boundary-{job_id}",
                    provider="ark",
                    model="video-model",
                    expected_cost_micros=None,
                    frozen_input_json={"prompt": "纸星星"},
                )
            )

        worker = ExplodingSubmitWorker(
            sessions,
            RecordingProvider(),
            worker_id="before-boundary-worker",
            after_boundary=False,
        )
        assert worker.run_once() is True

        with sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.status == "failed"
            assert persisted.provider_submission_started_at is None
            assert persisted.error_json["code"] == "worker_internal_error_before_submission"
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_unexpected_error_after_submission_boundary_becomes_unknown() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="提交中异常隔离", theme="纸星星", targetDurationSeconds=8)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="queued",
                    input_hash="2" * 64,
                    idempotency_key=f"after-boundary-{job_id}",
                    provider="ark",
                    model="video-model",
                    expected_cost_micros=None,
                    frozen_input_json={"prompt": "纸星星"},
                )
            )

        worker = ExplodingSubmitWorker(
            sessions,
            RecordingProvider(),
            worker_id="after-boundary-worker",
            after_boundary=True,
        )
        assert worker.run_once() is True

        with sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.status == "submission_unknown"
            assert persisted.provider_submission_started_at is not None
            assert persisted.provider_task_id is None
            assert persisted.error_json["code"] == "worker_internal_error_after_submission"
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_unexpected_poll_error_releases_lease_without_resubmitting() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="轮询异常隔离", theme="纸星星", targetDurationSeconds=8)
    )
    job_id = uuid.uuid4()
    task_id = f"provider-{job_id}"
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="submitted",
                    input_hash="3" * 64,
                    idempotency_key=f"poll-error-{job_id}",
                    provider="ark",
                    model="video-model",
                    expected_cost_micros=None,
                    frozen_input_json={"prompt": "纸星星"},
                    provider_submission_started_at=datetime.now(UTC),
                    provider_task_id=task_id,
                )
            )

        provider = RecordingProvider()
        exploding_worker = ExplodingPollWorker(
            sessions,
            provider,
            worker_id="exploding-poll-worker",
        )
        assert exploding_worker.run_once() is True

        with sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.status == "submitted"
            assert persisted.provider_task_id == task_id
            assert persisted.locked_by is None
            assert persisted.leased_until is None
        assert provider.submissions == []

        recovering_worker = DurableJobWorker(
            sessions,
            provider,
            worker_id="recovering-poll-worker",
        )
        assert recovering_worker.run_once() is True
        assert provider.submissions == []
        assert provider.polls == [task_id]
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_director_validation_failure_is_not_misreported_as_storage_failure() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions), provider_runtime=_ark_runtime())
    project = service.create_project(
        ProjectCreate(title="分镜结构校验", theme="浇花", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="plan_shots",
                    status="storing",
                    input_hash="4" * 64,
                    idempotency_key=f"director-validation-{job_id}",
                    provider="ark",
                    model="planning-model",
                    expected_cost_micros=None,
                    frozen_input_json={"storyVersionId": str(uuid.uuid4())},
                    provider_result_json={"responseId": "response-invalid-director"},
                )
            )

        worker = DurableJobWorker(
            sessions,
            RecordingProvider(),
            worker_id="director-validation-worker",
            result_handler=InvalidDirectorResultHandler(),
        )
        assert worker.run_once() is True

        with sessions() as session:
            persisted = session.get(JobRecord, job_id)
            assert persisted is not None
            assert persisted.status == "failed"
            assert persisted.error_json == {
                "code": "director_output_validation_failed",
                "message": "模型返回的分镜结构未通过校验，本次没有生成新版本。",
                "retryable": False,
                "detail": "shots: List should have at most 4 items after validation",
            }
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
