from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from cat_video_generator.application.ports import StoredStep
from cat_video_generator.domain.workflow import StepKind, StepStatus
from cat_video_generator.infrastructure.db.durable_queue import (
    PersistentTaskCancellation,
)
from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.jobs import JobRegistry


class _RecipeService:
    def __init__(self) -> None:
        self.instance_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.group_id = uuid.uuid4()
        self.last_payload: object | None = None
        self.last_expected_revision: int | None = None
        self.review_error: Exception | None = None
        self.adoption_idempotency_key: str | None = None

    def list_recipes(self) -> list[dict[str, object]]:
        return [{"key": "healing_child_cat_v1", "title": "一人一猫治愈短片"}]

    def create_instance(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.project_id = project_id
        self.last_payload = payload
        return {
            "id": str(self.instance_id),
            "projectId": str(project_id),
            "recipeKey": "healing_child_cat_v1",
            "revision": 1,
            "targetDurationSeconds": payload.target_duration_seconds,  # type: ignore[attr-defined]
            "qualityTier": payload.quality_tier,  # type: ignore[attr-defined]
            "stage": "concept",
        }

    def preview_director_workflow_adoption(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, object]:
        assert project_id == self.project_id
        return {
            "projectId": str(project_id),
            "eligible": True,
            "alreadyAdopted": False,
            "sourceHash": "a" * 64,
            "summary": {
                "sceneCount": 1,
                "shotCount": 2,
                "selectedVideoCount": 0,
            },
            "warnings": ["人物与猫咪 Canon 尚未完整，可在采用后继续补齐"],
            "blockers": [],
            "providerCallCount": 0,
        }

    def adopt_director_workflow(
        self,
        project_id: uuid.UUID,
        payload: object,
        *,
        idempotency_key: str,
    ) -> dict[str, object]:
        assert project_id == self.project_id
        self.last_payload = payload
        self.adoption_idempotency_key = idempotency_key
        return {
            "projectId": str(project_id),
            "recipeInstanceId": str(self.instance_id),
            "adopted": True,
            "sourceHash": payload.expected_source_hash,  # type: ignore[attr-defined]
            "providerCallCount": 0,
        }

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, object]:
        assert instance_id == self.instance_id
        return {
            "id": str(instance_id),
            "projectId": str(self.project_id),
            "revision": 1,
            "stage": "concept",
        }

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: object,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_expected_revision = expected_revision
        self.last_payload = payload
        return {"id": str(instance_id), "revision": expected_revision + 1}

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: object,
        *,
        episode_rules: object | None = None,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        if self.review_error is not None:
            raise self.review_error
        return {
            "id": str(uuid.uuid4()),
            "recipeInstanceId": str(instance_id),
            "decision": payload.decision,  # type: ignore[attr-defined]
            "targetRevision": payload.target_revision,  # type: ignore[attr-defined]
        }

    def confirm_storyboard_production_plan(
        self,
        instance_id: uuid.UUID,
        payload: object,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {
            "recipeInstanceId": str(instance_id),
            "status": "approved",
            "storyboardRevisionId": str(payload.storyboard_revision_id),  # type: ignore[attr-defined]
            "generationPlanId": str(payload.generation_plan_id),  # type: ignore[attr-defined]
            "warnings": [],
        }

    def run_story(self, instance_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"id": "story-run", "status": "succeeded"}

    def run_storyboard(self, instance_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"id": "storyboard-run", "status": "succeeded"}

    def run_anchor(
        self, instance_id: uuid.UUID, shot_id: uuid.UUID, payload: object
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"shotId": str(shot_id), "status": "queued"}

    def run_video(
        self, instance_id: uuid.UUID, shot_id: uuid.UUID, payload: object
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"shotId": str(shot_id), "status": "queued"}

    def run_sequence(self, instance_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {"id": "sequence-run", "status": "content_review"}

    def enqueue_recipe_task(
        self,
        instance_id: uuid.UUID,
        *,
        operation_key: str,
        payload: object,
        shot_id: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        creation_mode: str | None = None,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.last_payload = payload
        return {
            "jobId": str(uuid.uuid4()),
            "kind": "director",
            "status": "pending",
            "projectId": str(self.project_id),
            "shotId": None if shot_id is None else str(shot_id),
            "canvasGroupId": None if group_id is None else str(group_id),
            "recipeInstanceId": str(instance_id),
            "creationMode": creation_mode,
            "operationKey": operation_key,
            "workflowStage": operation_key.removeprefix("recipe:"),
            "phase": operation_key.removeprefix("recipe:"),
        }

    def enqueue_group_task(self, group_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert group_id == self.group_id
        self.last_payload = payload
        return {
            "jobId": str(uuid.uuid4()),
            "kind": "director",
            "status": "pending",
            "projectId": str(self.project_id),
            "canvasGroupId": str(group_id),
            "recipeInstanceId": str(self.instance_id),
            "operationKey": "canvas-group:run",
            "workflowStage": "creative",
            "phase": "creative",
        }

    def compile_group(self, group_id: uuid.UUID) -> dict[str, object]:
        assert group_id == self.group_id
        return {
            "groupId": str(group_id),
            "projectId": str(self.project_id),
            "recipeInstanceId": str(self.instance_id),
            "phase": "creative",
            "primaryAction": "补全创意输入",
            "blocker": "创意简报尚未人工批准",
            "estimatedCostMicros": 0,
        }

    def run_group(self, group_id: uuid.UUID, payload: object) -> dict[str, object]:
        assert group_id == self.group_id
        self.last_payload = payload
        return {"groupId": str(group_id), "status": "awaiting_review"}

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, object]:
        assert group_id == self.group_id
        return {"id": str(uuid.uuid4()), "templateKey": "six-stage-v1"}

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, object]:
        assert group_id == self.group_id
        self.last_expected_revision = expected_revision
        return {"id": str(group_id), "status": "detached", "archived": True}

    def convert_group_to_shots(self, group_id: uuid.UUID) -> dict[str, object]:
        assert group_id == self.group_id
        return {"parentGroupId": str(group_id), "groups": []}

    def group_download_manifest(self, group_id: uuid.UUID) -> dict[str, object]:
        assert group_id == self.group_id
        return {"groupId": str(group_id), "assets": []}

    def build_group_download(self, group_id: uuid.UUID) -> tuple[bytes, str]:
        assert group_id == self.group_id
        return b"zip-content", "one-child-one-cat-assets.zip"


def _client(tmp_path: Path, service: _RecipeService) -> TestClient:
    container = SimpleNamespace(
        repository=object(),
        editing=object(),
        production_recipes=service,
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    return TestClient(
        create_app(container, job_registry=JobRegistry(inline=True))  # type: ignore[arg-type]
    )


def test_recipe_catalog_create_get_and_if_match_patch(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    project_id = uuid.uuid4()

    catalog = client.get("/api/v2/production-recipes")
    created = client.post(
        f"/api/v2/projects/{project_id}/recipe-instances",
        json={
            "recipeKey": "healing_child_cat_v1",
            "theme": "孩子与猫在雨后收集落叶",
            "targetDurationSeconds": 31,
            "qualityTier": "balanced",
        },
    )
    loaded = client.get(f"/api/v2/recipe-instances/{service.instance_id}")
    patched = client.patch(
        f"/api/v2/recipe-instances/{service.instance_id}",
        headers={"If-Match": '"1"'},
        json={"qualityTier": "premium"},
    )

    assert catalog.status_code == 200
    assert catalog.json()[0]["key"] == "healing_child_cat_v1"
    assert created.status_code == 201
    assert created.json()["targetDurationSeconds"] == 31
    assert loaded.status_code == 200
    assert patched.status_code == 200
    assert service.last_expected_revision == 1


def test_legacy_project_adoption_preview_and_atomic_apply(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)

    preview = client.get(
        f"/api/v2/projects/{service.project_id}/director-workflow-adoption-preview"
    )
    adopted = client.post(
        f"/api/v2/projects/{service.project_id}/director-workflow-adoptions",
        headers={"Idempotency-Key": "legacy-adoption-001"},
        json={
            "expectedSourceHash": "a" * 64,
            "recipeKey": "healing_child_cat_v1",
            "targetDurationSeconds": 15,
            "qualityTier": "quick",
        },
    )

    assert preview.status_code == 200
    assert preview.json()["providerCallCount"] == 0
    assert preview.json()["summary"]["shotCount"] == 2
    assert adopted.status_code == 201
    assert adopted.json()["adopted"] is True
    assert adopted.json()["providerCallCount"] == 0
    assert service.adoption_idempotency_key == "legacy-adoption-001"


def test_legacy_project_adoption_requires_explicit_idempotency_key(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)

    response = client.post(
        f"/api/v2/projects/{service.project_id}/director-workflow-adoptions",
        json={
            "expectedSourceHash": "a" * 64,
            "recipeKey": "healing_child_cat_v1",
            "targetDurationSeconds": 15,
            "qualityTier": "quick",
        },
    )

    assert response.status_code == 422
    assert service.adoption_idempotency_key is None


def test_review_endpoint_requires_pinned_target_and_override_reason(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    target_id = uuid.uuid4()

    unpinned = client.post(
        "/api/v2/review-decisions",
        json={
            "recipeInstanceId": str(service.instance_id),
            "targetType": "anchor_asset",
            "targetId": str(target_id),
            "decision": "approve",
        },
    )
    invalid_override = client.post(
        "/api/v2/review-decisions",
        json={
            "recipeInstanceId": str(service.instance_id),
            "targetType": "anchor_asset",
            "targetId": str(target_id),
            "targetHash": "a" * 64,
            "decision": "override",
            "blockingDiagnosticPresent": True,
        },
    )
    approved = client.post(
        "/api/v2/review-decisions",
        json={
            "recipeInstanceId": str(service.instance_id),
            "targetType": "anchor_asset",
            "targetId": str(target_id),
            "targetHash": "a" * 64,
            "decision": "override",
            "blockingDiagnosticPresent": True,
            "reason": "人工逐帧确认该提示为误报",
        },
    )

    assert unpinned.status_code == 422
    assert invalid_override.status_code == 422
    assert approved.status_code == 201
    assert approved.json()["decision"] == "override"


def test_storyboard_production_confirmation_is_one_atomic_api_command(
    tmp_path: Path,
) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    storyboard_id = uuid.uuid4()
    plan_id = uuid.uuid4()

    response = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/storyboard-production-confirmations",
        json={
            "idempotencyKey": "storyboard-confirmation-001",
            "storyboardRevisionId": str(storyboard_id),
            "storyboardRevision": 2,
            "structureHash": "a" * 64,
            "generationPlanId": str(plan_id),
            "generationPlanRevision": 3,
            "generationPlanHash": "b" * 64,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "approved"
    assert service.last_payload.storyboard_revision_id == storyboard_id  # type: ignore[attr-defined]
    assert service.last_payload.generation_plan_id == plan_id  # type: ignore[attr-defined]
    assert service.last_payload.idempotency_key == "storyboard-confirmation-001"  # type: ignore[attr-defined]


def test_review_endpoint_returns_safe_correlation_for_unexpected_failure(
    tmp_path: Path,
) -> None:
    service = _RecipeService()
    service.review_error = RuntimeError("internal scene materialization trace")
    client = _client(tmp_path, service)

    response = client.post(
        "/api/v2/review-decisions",
        headers={"X-Correlation-ID": "review-correlation-001"},
        json={
            "recipeInstanceId": str(service.instance_id),
            "targetType": "story_revision",
            "targetId": str(uuid.uuid4()),
            "targetRevision": 1,
            "decision": "approve",
            "episodeRules": {
                "personWardrobe": "雨后日常服装",
                "timeWeather": "雨后初晴",
                "mainScene": "小院",
                "coreProps": ["发亮的叶子"],
                "catBehaviorMode": "natural",
                "soundPlan": {
                    "ambient": ["雨滴"],
                    "foley": ["猫咪靠近叶子"],
                    "musicMood": "安静温暖",
                    "dialoguePolicy": "none",
                },
                "stylePositive": ["细腻数字插画", "克制轮廓线", "柔和漫射光"],
                "styleExcluded": ["摄影写实", "身份漂移"],
                "canonProfileId": "canon-v3-healing-child-cat-line-texture",
                "environment": "outdoor",
            },
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "message": "剧情审核事务未完成，剧情仍未批准且任务仍等待审核",
        "errorType": "review_transaction_failed",
        "correlationId": "review-correlation-001",
    }
    assert "internal scene materialization trace" not in response.text


def test_review_endpoint_preserves_known_conflict_status(tmp_path: Path) -> None:
    from cat_video_generator.infrastructure.db.repositories import WorkflowConflictError

    service = _RecipeService()
    service.review_error = WorkflowConflictError("已批准规则不能原地改写")
    client = _client(tmp_path, service)

    response = client.post(
        "/api/v2/review-decisions",
        json={
            "recipeInstanceId": str(service.instance_id),
            "targetType": "story_revision",
            "targetId": str(uuid.uuid4()),
            "targetRevision": 1,
            "decision": "approve",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "已批准规则不能原地改写"


def test_persistent_task_recovery_requeues_the_same_character_design_step(
    tmp_path: Path,
) -> None:
    service = _RecipeService()
    step_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    now = datetime.now(UTC)
    original_snapshot = {
        "recipeInstanceId": str(service.instance_id),
        "businessObjectId": str(revision_id),
        "phase": "character_design",
        "payload": {
            "idempotencyKey": "character-design-recovery-0001",
            "acceptEstimatedCostMicros": 0,
        },
    }

    class Repository:
        def __init__(self) -> None:
            self.step = StoredStep(
                id=step_id,
                project_id=service.project_id,
                scene_id=None,
                shot_card_id=None,
                kind=StepKind.IMAGE,
                status=StepStatus.FAILED,
                attempt=1,
                operation_key="recipe:character_design",
                input_snapshot=original_snapshot,
                error={
                    "code": "recipe_dispatch_failed",
                    "failedStep": "create_generation_batches",
                    "recoverable": True,
                    "providerSubmitted": False,
                    "message": "三个角色设计批次未能原子落库",
                },
                progress={"providerStatus": "not_submitted"},
                created_at=now,
                updated_at=now,
                completed_at=now,
            )

        def get_step(self, requested_step_id: uuid.UUID) -> StoredStep:
            assert requested_step_id == step_id
            return self.step

    repository = Repository()

    class Queue:
        def __init__(self) -> None:
            self.recovered: list[uuid.UUID] = []

        def recover(self, requested_step_id: uuid.UUID) -> None:
            assert requested_step_id == step_id
            self.recovered.append(requested_step_id)
            repository.step = StoredStep(
                id=step_id,
                project_id=service.project_id,
                scene_id=None,
                shot_card_id=None,
                kind=StepKind.IMAGE,
                status=StepStatus.QUEUED,
                attempt=2,
                operation_key="recipe:character_design",
                input_snapshot=original_snapshot,
                progress={
                    "currentStep": 0,
                    "totalSteps": 3,
                    "percent": 0,
                    "message": "准备从角色设计批次调度失败步骤继续",
                    "providerStatus": "not_submitted",
                    "childStepIds": [],
                },
                created_at=now,
                updated_at=now,
            )

        def cancellation_for(self, requested_step_id: uuid.UUID) -> PersistentTaskCancellation:
            assert requested_step_id == step_id
            return PersistentTaskCancellation(
                allowed=True,
                mode="local_before_provider",
                label="取消，尚未提交 Provider",
                provider_status="not_submitted",
                cost_may_already_apply=False,
            )

    queue = Queue()
    container = SimpleNamespace(
        repository=repository,
        editing=object(),
        production_recipes=service,
        workflow_queue=queue,
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    client = TestClient(
        create_app(container, job_registry=JobRegistry(inline=True))  # type: ignore[arg-type]
    )

    response = client.post(f"/api/v1/task-center/tasks/{step_id}/recover")

    assert response.status_code == 200
    assert queue.recovered == [step_id]
    body = response.json()
    assert body["stepId"] == str(step_id)
    assert body["status"] == "queued"
    assert body["attempt"] == 2
    assert body["inputSnapshot"] == original_snapshot
    assert body["progress"]["providerStatus"] == "not_submitted"
    assert "recovery" not in body
    assert body["cancellation"]["mode"] == "local_before_provider"


def test_persistent_task_cancellation_uses_expected_state_and_returns_policy(
    tmp_path: Path,
) -> None:
    service = _RecipeService()
    step_id = uuid.uuid4()
    now = datetime.now(UTC)

    class Repository:
        def __init__(self) -> None:
            self.step = StoredStep(
                id=step_id,
                project_id=service.project_id,
                scene_id=None,
                shot_card_id=None,
                kind=StepKind.VIDEO,
                status=StepStatus.QUEUED,
                attempt=1,
                operation_key="media:video:batch:test:candidate:1",
                input_snapshot={},
                progress={"providerStatus": "not_submitted"},
                created_at=now,
                updated_at=now,
            )

        def get_step(self, requested_step_id: uuid.UUID) -> StoredStep:
            assert requested_step_id == step_id
            return self.step

    repository = Repository()

    class Queue:
        def __init__(self) -> None:
            self.requests: list[dict[str, object]] = []

        def cancel(self, requested_step_id: uuid.UUID, **values: object) -> None:
            assert requested_step_id == step_id
            self.requests.append(values)
            repository.step = replace(
                repository.step,
                status=StepStatus.CANCELLED,
                progress={
                    "providerStatus": "not_submitted",
                    "message": "已在提交 Provider 前取消，Provider 调用 0 次",
                },
                completed_at=now,
            )

        def cancellation_for(self, requested_step_id: uuid.UUID) -> PersistentTaskCancellation:
            assert requested_step_id == step_id
            return PersistentTaskCancellation(
                allowed=False,
                mode="unavailable",
                label="当前不可取消",
                disabled_reason="任务已经取消",
                provider_status="cancelled",
                cost_may_already_apply=False,
            )

    queue = Queue()
    container = SimpleNamespace(
        repository=repository,
        editing=object(),
        production_recipes=service,
        workflow_queue=queue,
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    client = TestClient(
        create_app(container, job_registry=JobRegistry(inline=True))  # type: ignore[arg-type]
    )

    response = client.post(
        f"/api/v1/steps/{step_id}/cancellation",
        json={
            "expectedStatus": "queued",
            "expectedProviderTaskId": None,
            "reason": "Web 任务中心人工取消",
        },
    )

    assert response.status_code == 200
    assert queue.requests == [
        {
            "expected_status": "queued",
            "expected_provider_task_id": None,
            "reason": "Web 任务中心人工取消",
        }
    ]
    assert response.json()["status"] == "cancelled"
    assert response.json()["progress"]["providerStatus"] == "not_submitted"
    assert response.json()["cancellation"] == {
        "allowed": False,
        "mode": "unavailable",
        "label": "当前不可取消",
        "disabledReason": "任务已经取消",
        "providerStatus": "cancelled",
        "costMayAlreadyApply": False,
    }


def test_task_center_projects_cancellation_policies_in_one_bulk_query(
    tmp_path: Path,
) -> None:
    service = _RecipeService()
    now = datetime.now(UTC)
    steps = [
        StoredStep(
            id=uuid.uuid4(),
            project_id=service.project_id,
            scene_id=None,
            shot_card_id=None,
            kind=StepKind.VIDEO,
            status=StepStatus.QUEUED,
            attempt=index,
            operation_key=f"media:video:batch:{index}:candidate:1",
            input_snapshot={},
            progress={"providerStatus": "not_submitted"},
            created_at=now,
            updated_at=now,
        )
        for index in (1, 2)
    ]

    class Repository:
        def task_center_steps(self) -> list[StoredStep]:
            return steps

        def list_assets(self) -> list[object]:
            return []

    class Queue:
        def __init__(self) -> None:
            self.requests: list[tuple[uuid.UUID, ...]] = []

        def cancellations_for(
            self,
            step_ids: tuple[uuid.UUID, ...],
        ) -> dict[uuid.UUID, PersistentTaskCancellation]:
            self.requests.append(step_ids)
            return {
                step_id: PersistentTaskCancellation(
                    allowed=True,
                    mode="local_before_provider",
                    label="取消，尚未提交 Provider",
                    provider_status="not_submitted",
                    cost_may_already_apply=False,
                )
                for step_id in step_ids
            }

        def cancellation_for(self, _step_id: uuid.UUID) -> PersistentTaskCancellation:
            raise AssertionError("task-center must not open one cancellation query per task")

    queue = Queue()
    container = SimpleNamespace(
        repository=Repository(),
        editing=object(),
        production_recipes=service,
        workflow_queue=queue,
        runtime_settings=SimpleNamespace(work_root=tmp_path, asset_root=tmp_path),
    )
    client = TestClient(
        create_app(container, job_registry=JobRegistry(inline=True))  # type: ignore[arg-type]
    )

    response = client.get("/api/v1/task-center")

    assert response.status_code == 200
    assert queue.requests == [tuple(step.id for step in steps)]
    body = response.json()
    assert len(body["persistentTasks"]) == 2
    assert all(
        item["cancellation"]["mode"] == "local_before_provider"
        for item in body["persistentTasks"]
    )


def test_recipe_stage_run_endpoints_require_idempotency_and_cost_acceptance(
    tmp_path: Path,
) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    shot_id = uuid.uuid4()
    payload = {
        "idempotencyKey": "stage-run-0001",
        "acceptEstimatedCostMicros": 0,
    }

    story = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/story-runs",
        json=payload,
    )
    storyboard = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/storyboard-runs",
        json={**payload, "sourceStoryRevisionId": str(uuid.uuid4())},
    )
    anchor = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/shots/{shot_id}/anchor-runs",
        json=payload,
    )
    video = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/shots/{shot_id}/video-runs",
        json=payload,
    )
    sequence = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/sequence-runs",
        json=payload,
    )
    invalid = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/story-runs",
        json={"acceptEstimatedCostMicros": 0},
    )

    assert [item.status_code for item in (story, storyboard, anchor, video, sequence)] == [
        202,
        202,
        202,
        202,
        202,
    ]
    assert invalid.status_code == 422
    assert storyboard.json()["kind"] == "director"
    assert storyboard.json()["projectId"] == str(service.project_id)
    assert storyboard.json()["recipeInstanceId"] == str(service.instance_id)
    assert storyboard.json()["creationMode"] == "from_story"
    assert storyboard.json()["operationKey"] == "recipe:storyboard"
    assert storyboard.json()["phase"] == "storyboard"


def test_canvas_group_actions_compile_run_and_preserve_if_match(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)

    compiled = client.post(f"/api/v2/canvas-groups/{service.group_id}/compile-run")
    executed = client.post(
        f"/api/v2/canvas-groups/{service.group_id}/runs",
        json={
            "idempotencyKey": "group-run-0001",
            "acceptEstimatedCostMicros": 0,
        },
    )
    detached = client.post(
        f"/api/v2/canvas-groups/{service.group_id}/ungroup",
        headers={"If-Match": '"3"'},
    )

    assert compiled.status_code == 200
    assert compiled.json()["phase"] == "creative"
    assert executed.status_code == 202
    assert executed.json()["kind"] == "director"
    assert executed.json()["canvasGroupId"] == str(service.group_id)
    assert executed.json()["phase"] == "creative"
    assert detached.status_code == 200
    assert detached.json()["archived"] is True
    assert service.last_expected_revision == 3


def test_canvas_group_download_exposes_manifest_and_attachment(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)

    manifest = client.get(f"/api/v2/canvas-groups/{service.group_id}/download-manifest")
    archive = client.get(f"/api/v2/canvas-groups/{service.group_id}/download")

    assert manifest.status_code == 200
    assert manifest.json() == {"groupId": str(service.group_id), "assets": []}
    assert archive.status_code == 200
    assert archive.content == b"zip-content"
    assert archive.headers["content-type"] == "application/zip"
    assert "one-child-one-cat-assets.zip" in archive.headers["content-disposition"]


def test_sequence_endpoint_accepts_recipe_bounded_transition_plan(tmp_path: Path) -> None:
    service = _RecipeService()
    client = _client(tmp_path, service)
    shot_id = uuid.uuid4()

    response = client.post(
        f"/api/v2/recipe-instances/{service.instance_id}/sequence-runs",
        json={
            "idempotencyKey": "sequence-run-0001",
            "acceptEstimatedCostMicros": 0,
            "transitions": [
                {
                    "afterShotId": str(shot_id),
                    "transition": {"type": "fade_black", "durationMs": 500},
                }
            ],
        },
    )

    assert response.status_code == 202
    assert service.last_payload.transitions[0].after_shot_id == shot_id  # type: ignore[attr-defined]
