from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cat_video_generator.application.ports import StoredProject, StoredStep
from cat_video_generator.domain.workflow import RunStatus, StepKind, StepStatus
from cat_video_generator.infrastructure.db.repositories import WorkflowConflictError
from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.jobs import JobRegistry


def test_accept_suggestions_returns_409_when_generation_history_blocks_replacement(
    tmp_path: Path,
) -> None:
    class Editing:
        def accept_suggestions(self, *args: object, **kwargs: object) -> tuple[object, ...]:
            del args, kwargs
            raise WorkflowConflictError("已有图片或视频生成历史，不能整批覆盖视频片段")

    container = SimpleNamespace(
        repository=object(),
        editing=Editing(),
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    app = create_app(
        container,  # type: ignore[arg-type]
        job_registry=JobRegistry(inline=True),
    )

    response = TestClient(app).post(
        f"/api/v1/steps/{uuid.uuid4()}/accept-suggestions",
        json={
            "lookPlan": None,
            "shots": [
                {
                    "title": "不可覆盖",
                    "direction": "1. 固定中景，猫咪观察人物，稳定收尾。",
                    "suggestedDurationSeconds": 8,
                }
            ],
        },
    )

    assert response.status_code == 409
    assert "不能整批覆盖" in response.json()["detail"]


def test_project_tasks_exposes_persistent_provider_state_without_new_schema(
    tmp_path: Path,
) -> None:
    project_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    shot_id = uuid.uuid4()
    step = StoredStep(
        id=uuid.uuid4(),
        project_id=project_id,
        scene_id=scene_id,
        shot_card_id=shot_id,
        kind=StepKind.VIDEO,
        status=StepStatus.RUNNING,
        attempt=2,
        operation_key="video:shot",
        provider="fake",
        provider_task_id="provider-task-2",
        model="fake-seedance",
        input_snapshot={"sourceRevisionHash": "current-input"},
        created_at=datetime(2026, 8, 14, 1, 2, tzinfo=UTC),
    )

    class Repository:
        def get_project(self, requested_id: uuid.UUID) -> StoredProject:
            assert requested_id == project_id
            return StoredProject(
                id=project_id,
                title="湖泊钓鱼",
                content_date=date(2026, 8, 14),
                status=RunStatus.ACTIVE,
            )

        def list_steps(self, *, project_id: uuid.UUID) -> tuple[StoredStep, ...]:
            assert project_id == step.project_id
            return (step,)

    container = SimpleNamespace(
        repository=Repository(),
        editing=object(),
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    app = create_app(
        container,  # type: ignore[arg-type]
        job_registry=JobRegistry(inline=True),
    )

    response = TestClient(app).get(f"/api/v1/projects/{project_id}/tasks")

    assert response.status_code == 200
    assert response.json() == [
        {
            "stepId": str(step.id),
            "projectId": str(project_id),
            "sceneId": str(scene_id),
            "shotId": str(shot_id),
            "kind": "video",
            "status": "running",
            "attempt": 2,
            "operationKey": "video:shot",
            "provider": "fake",
            "providerTaskId": "provider-task-2",
            "canvasNodeId": None,
            "canvasGroupId": None,
            "recipeInstanceId": None,
            "businessObjectId": None,
            "creationMode": None,
            "parentStepId": None,
            "childStepIds": [],
            "workflowStage": None,
            "phase": None,
            "model": "fake-seedance",
            "inputSnapshot": {"sourceRevisionHash": "current-input"},
            "error": None,
            "progress": {},
            "resultSummary": None,
            "createdAt": "2026-08-14T01:02:00+00:00",
            "updatedAt": None,
            "completedAt": None,
        }
    ]
