from __future__ import annotations

import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete, make_url, select

from catflow.application.service import PlannerMessageCommand, ProjectCreate, StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import JobRecord, ProjectRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.runner import DurableJobWorker, ProviderPoll, ProviderTaskGateway


class RecordingProvider(ProviderTaskGateway):
    def __init__(self) -> None:
        self.submissions: list[uuid.UUID] = []
        self.polls: list[str] = []

    def submit(self, *, job_id: uuid.UUID, frozen_input: dict[str, object]) -> str:
        self.submissions.append(job_id)
        return f"provider-{job_id}"

    def poll(self, provider_task_id: str) -> ProviderPoll:
        self.polls.append(provider_task_id)
        return ProviderPoll(status="running")

    def cancel(self, provider_task_id: str) -> bool:
        return True


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
