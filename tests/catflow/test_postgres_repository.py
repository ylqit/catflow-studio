from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, make_url

from catflow.application.service import (
    JobDto,
    PlannerMessageCommand,
    ProjectCreate,
    StudioConflictError,
    StudioService,
)
from catflow.domain.models import LifeStoryProposalDraft
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import ProjectRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository


def test_postgres_repository_persists_and_recovers_planner_workflow() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    settings = DatabaseSettings.from_env()
    database_name = make_url(settings.url).database
    assert database_name is not None
    assert database_name.startswith("catflow_studio_test_")
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    repository = PostgresStudioRepository(sessions)
    service = StudioService(repository)
    project = service.create_project(
        ProjectCreate(title="仓储恢复测试", theme="雨天擦爪", targetDurationSeconds=10)
    )

    try:
        command = PlannerMessageCommand(
            text="写一个孩子为猫咪擦干爪子的微事件",
            expectedContextRevision=1,
            idempotencyKey=f"planner-{project.id}",
        )
        first = service.enqueue_planner_message(project.id, command)
        same = service.enqueue_planner_message(project.id, command)
        assert same.id == first.id
        assert first.frozen_input["targetDurationSeconds"] == project.target_duration_seconds

        proposal = service.complete_planner_job(
            first.id,
            LifeStoryProposalDraft(
                title="雨天擦爪",
                summary="孩子在玄关帮猫咪擦干湿爪。",
                body="猫咪回家留下湿脚印，孩子铺开毛巾并轻轻擦干它的爪子。",
                trigger="猫咪留下湿脚印",
                childAction="孩子铺开毛巾",
                catResponse="猫咪把前爪放到毛巾上",
                visibleChange="湿脚印不再延伸",
                warmEnding="猫咪靠着孩子打呼噜",
                targetDurationSeconds=10,
                dialoguePolicy="none",
                environmentIntent="雨天玄关暖光",
            ),
        )
        story = service.adopt_proposal(project.id, proposal.id)

        recovered_service = StudioService(PostgresStudioRepository(sessions))
        recovered_project = recovered_service.get_project(project.id)
        recovered_planner = recovered_service.get_planner(project.id)
        recovered_job = recovered_service.get_job(first.id)

        assert recovered_project is not None
        assert recovered_project.title == "仓储恢复测试"
        assert recovered_planner.proposals[0].status == "adopted"
        assert recovered_service.list_stories(project.id)[0].id == story.id
        assert recovered_job.status == "succeeded"
        assert len(recovered_planner.messages) == 2
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_postgres_repository_reports_idempotency_input_conflict_without_inserting() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    settings = DatabaseSettings.from_env()
    database_name = make_url(settings.url).database
    assert database_name is not None
    assert database_name.startswith("catflow_studio_test_")
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    repository = PostgresStudioRepository(sessions)
    service = StudioService(repository)
    project = service.create_project(
        ProjectCreate(title="幂等冲突测试", theme="雨天擦爪", targetDurationSeconds=10)
    )
    now = datetime.now(UTC)
    first = JobDto(
        id=uuid.uuid4(),
        projectId=project.id,
        kind="plan_shots",
        status="failed",
        inputHash="a" * 64,
        idempotencyKey=f"postgres-conflict-{project.id}",
        frozenInput={},
        resultAssetIds=[],
        createdAt=now,
        updatedAt=now,
    )

    try:
        repository.create_job(first)
        with pytest.raises(StudioConflictError) as caught:
            repository.create_job(
                first.model_copy(update={"id": uuid.uuid4(), "input_hash": "b" * 64})
            )

        assert getattr(caught.value, "code", None) == "idempotency_input_conflict"
        assert len(repository.list_project_jobs(project.id)) == 1
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
