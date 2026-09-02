from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete, make_url, select

from catflow.application.gateways import ProviderGatewayError
from catflow.application.service import (
    PlannerMessageCommand,
    ProjectCreate,
    SegmentRepairCreateCommand,
    SegmentRepairPreviewCommand,
    ShotPlanGenerationCommand,
    StudioService,
)
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import EnvironmentPresetRecord, JobRecord, ProjectRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.runner import (
    DurableJobWorker,
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


def test_local_input_preparation_failure_happens_before_provider_submission_boundary() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
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
            session.execute(
                delete(EnvironmentPresetRecord).where(
                    EnvironmentPresetRecord.source_project_id == project.id
                )
            )
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_worker_claims_only_its_configured_provider_queue() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="Provider 队列隔离", theme="线团", targetDurationSeconds=12)
    )
    ark_job_id = uuid.uuid4()
    fake_job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            for job_id, provider in ((ark_job_id, "ark"), (fake_job_id, "fake")):
                session.add(
                    JobRecord(
                        id=job_id,
                        project_id=project.id,
                        kind="generate_video",
                        status="queued",
                        input_hash=provider[0] * 64,
                        idempotency_key=f"provider-isolation-{job_id}",
                        provider=provider,
                        model="video-model",
                        frozen_input_json={"prompt": provider},
                    )
                )

        provider = RecordingProvider()
        worker = DurableJobWorker(
            sessions,
            provider,
            worker_id="fake-provider-worker",
            provider_name="fake",
        )
        assert worker.run_once() is True

        with sessions() as session:
            assert session.get(JobRecord, ark_job_id).status == "queued"  # type: ignore[union-attr]
            assert session.get(JobRecord, fake_job_id).status == "submitted"  # type: ignore[union-attr]
        assert provider.submissions == [fake_job_id]
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_worker_persists_provider_task_before_polling_and_never_resubmits() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    settings = DatabaseSettings.from_env()
    assert make_url(settings.url).database == "catflow_studio"
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
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
                    provider="fake",
                    model="fake-video",
                    expected_cost_micros=0,
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


def test_base64_probe_persists_task_then_cancels_after_worker_restart_without_polling() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="Base64 探针恢复", theme="雨天擦爪", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="probe_segment_video_data_url",
                    status="queued",
                    input_hash="f" * 64,
                    idempotency_key=f"test-base64-probe-{job_id}",
                    provider="ark",
                    model="video-model",
                    expected_cost_micros=None,
                    frozen_input_json={"sourceVideoAssetId": str(uuid.uuid4())},
                )
            )

        provider = RecordingProvider()
        first_worker = DurableJobWorker(sessions, provider, worker_id="probe-submit")
        restarted_worker = DurableJobWorker(sessions, provider, worker_id="probe-cancel")

        assert first_worker.run_once() is True
        with sessions() as session:
            submitted = session.get(JobRecord, job_id)
            assert submitted is not None
            assert submitted.status == "submitted"
            assert submitted.provider_task_id == f"provider-{job_id}"
        assert provider.submissions == [job_id]

        assert restarted_worker.run_once() is True
        with sessions() as session:
            completed = session.get(JobRecord, job_id)
            assert completed is not None
            assert completed.status == "succeeded"
            assert completed.provider_task_id == f"provider-{job_id}"
            assert completed.provider_result_json == {
                "transport": "data_url_experimental",
                "transportAccepted": True,
                "cancelRequested": True,
            }
        assert provider.submissions == [job_id]
        assert provider.polls == []
        assert provider.cancellations == [f"provider-{job_id}"]
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_fake_planner_worker_creates_a_proposal_without_a_paid_provider_call() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="Planner Worker 测试", theme="折叠毛巾", targetDurationSeconds=10)
    )
    try:
        job = service.enqueue_planner_message(
            project.id,
            PlannerMessageCommand(
                text="折叠毛巾",
                expectedContextRevision=1,
                idempotencyKey=f"planner-worker-{project.id}",
            ),
        )
        provider = RecordingProvider()
        worker = DurableJobWorker(
            sessions,
            provider,
            worker_id="planner-worker",
            studio_service=service,
        )

        assert worker.run_once() is True

        snapshot = service.get_planner(project.id)
        assert snapshot.proposals[0].title == "折叠毛巾"
        assert service.get_job(job.id).status == "succeeded"
        assert provider.submissions == []
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_fake_director_worker_creates_a_professional_plan_without_provider_submission() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="Director Worker 测试", theme="折叠毛巾", targetDurationSeconds=12)
    )
    try:
        service.enqueue_planner_message(
            project.id,
            PlannerMessageCommand(
                text="折叠毛巾",
                expectedContextRevision=1,
                idempotencyKey=f"director-source-{project.id}",
            ),
        )
        provider = RecordingProvider()
        worker = DurableJobWorker(
            sessions,
            provider,
            worker_id="director-worker",
            studio_service=service,
        )
        assert worker.run_once() is True
        proposal = service.get_planner(project.id).proposals[0]
        service.adopt_proposal(project.id, proposal.id)
        environment = service.register_asset(project.id, role="environment", sha256="8" * 64)
        service.select_asset(project.id, slot="environment", asset_id=environment.id)
        job = service.create_shot_plan_generation_job(
            project.id,
            ShotPlanGenerationCommand(idempotencyKey=f"director-plan-{project.id}"),
        )

        assert worker.run_once() is True

        plan = service.list_shot_plans(project.id)[0]
        assert plan.director_prompt_revision == "catflow-director-v2"
        assert plan.shots[0].lens is not None
        assert plan.shots[0].child_blocking is not None
        assert plan.shots[-1].continuity is not None
        assert "主动" in plan.shots[-1].continuity.final_frame
        assert service.get_job(job.id).status == "succeeded"
        assert provider.submissions == []
    finally:
        with sessions.begin() as session:
            session.execute(
                delete(EnvironmentPresetRecord).where(
                    EnvironmentPresetRecord.source_project_id == project.id
                )
            )
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_worker_persists_submission_boundary_and_never_retries_unknown_acceptance() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
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
    service = StudioService(PostgresStudioRepository(sessions))
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
    service = StudioService(PostgresStudioRepository(sessions))
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
