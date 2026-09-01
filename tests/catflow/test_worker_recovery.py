from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete, make_url, select

from catflow.application.gateways import ProviderGatewayError
from catflow.application.service import PlannerMessageCommand, ProjectCreate, StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import JobRecord, ProjectRecord
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

    def submit(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> ProviderSubmission:
        self.submissions.append(job_id)
        return ProviderSubmission(taskId=f"provider-{job_id}")

    def poll(self, provider_task_id: str) -> ProviderPoll:
        self.polls.append(provider_task_id)
        return ProviderPoll(status="running")

    def cancel(self, provider_task_id: str) -> bool:
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
            result={"proposal": {"title": "雨天擦爪"}},
            usage={"inputTokens": 120, "outputTokens": 80},
        )


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
                "proposal": {"title": "雨天擦爪"}
            }
            assert persisted.actual_usage_json == {
                "inputTokens": 120,
                "outputTokens": 80,
            }
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
