from __future__ import annotations

import importlib.util
import json
import uuid
import zipfile
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from cat_video_generator.application.production_recipes import (
    ProductionRecipeService,
    _episode_visual_constraints,
    _narrative_visual_instruction,
    _suggest_episode_rules,
    _task_result_summary,
)
from cat_video_generator.domain.production_recipes import (
    CANON_V2_PROFILE_ID,
    CANON_V2_STYLE_POSITIVE,
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    CanvasGroupRunRequest,
    EpisodeRules,
    HumanReviewDraft,
    PaidRecipeRunRequest,
    ProductionRecipeInstanceDraft,
    ProductionRecipeInstancePatch,
    RecipeSequenceRunRequest,
    StoryboardRecipeRunRequest,
    recipe_task_source_hash,
    split_editorial_shot_durations,
)
from cat_video_generator.domain.workflow import StepStatus
from cat_video_generator.infrastructure.db.models import Base
from cat_video_generator.infrastructure.db.production_recipe_repository import (
    _aggregate_review_parent_status,
    _apply_child_aggregate_to_workflow_step,
    _apply_review_to_workflow_step,
    _sequence_candidate_json,
    _workflow_step_reviews_target,
)
from cat_video_generator.infrastructure.db.visual_preset_profiles import (
    canon_v3_subject_documents,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_migration() -> ModuleType:
    path = PROJECT_ROOT / "alembic" / "versions" / "0022_healing_child_cat_recipe.py"
    spec = importlib.util.spec_from_file_location("healing_child_cat_recipe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load migration: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Repository:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.instance_id = uuid.uuid4()
        self.row = {
            "id": str(self.instance_id),
            "projectId": str(self.project_id),
            "recipeKey": "healing_child_cat_v1",
            "recipeVersion": 1,
            "revision": 1,
            "theme": "雨天收衣服",
            "targetDurationSeconds": 31,
            "qualityTier": "balanced",
            "canonProfileId": CANON_V2_PROFILE_ID,
            "lifecycleStatus": "active",
            "visualProfile": {
                "sourceProfileId": CANON_V2_PROFILE_ID,
                "stylePositive": list(CANON_V2_STYLE_POSITIVE),
                "styleNegative": ["准写实", "3D塑料感"],
            },
            "progress": {
                "storyApproved": False,
                "shotCount": 0,
                "approvedAnchorCount": 0,
                "approvedVideoCount": 0,
                "sequenceReady": False,
                "finalApproved": False,
            },
        }
        self.review: HumanReviewDraft | None = None
        self.episode_rules: EpisodeRules | None = None
        self.materialized_storyboard: dict[str, object] | None = None
        self.suggested_rules: EpisodeRules | None = None
        self.suggested_rule_candidate_ids: tuple[uuid.UUID, ...] = ()
        self.validated_character_references: tuple[uuid.UUID, ...] | None = None
        self.validated_anchor_shot_ids: list[uuid.UUID] = []
        self.child_task_statuses: dict[uuid.UUID, str] = {}
        self.child_task_summaries: dict[uuid.UUID, dict[str, object]] = {}
        self.enqueued_task_count = 0

    def create_instance(
        self, project_id: uuid.UUID, payload: ProductionRecipeInstanceDraft
    ) -> dict[str, object]:
        assert project_id == self.project_id
        self.row = {
            **self.row,
            **payload.model_dump(mode="json", by_alias=True),
        }
        return self.row

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, object]:
        assert instance_id == self.instance_id
        return self.row

    def get_group(self, _group_id: uuid.UUID) -> dict[str, object]:
        return {
            "id": str(_group_id),
            "recipeInstanceId": str(self.instance_id),
            "lifecycleStatus": "active",
        }

    def enqueue_task(self, instance_id: uuid.UUID, **values: object) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.enqueued_task_count += 1
        return {
            "jobId": str(uuid.uuid4()),
            "status": "pending",
            "recipeInstanceId": str(instance_id),
            **values,
        }

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        if expected_revision != self.row["revision"]:
            raise ValueError("配方实例版本冲突")
        self.row = {
            **self.row,
            **payload.model_dump(mode="json", by_alias=True, exclude_none=True),
            "revision": expected_revision + 1,
        }
        return self.row

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.review = payload
        self.episode_rules = episode_rules
        return {
            "id": str(uuid.uuid4()),
            "recipeInstanceId": str(instance_id),
            **payload.model_dump(mode="json", by_alias=True),
        }

    def materialize_storyboard(
        self, instance_id: uuid.UUID, storyboard: dict[str, object]
    ) -> dict[str, object]:
        assert instance_id == self.instance_id
        self.materialized_storyboard = storyboard
        return {**storyboard, "materialized": True}

    def store_suggested_episode_rules(
        self,
        instance_id: uuid.UUID,
        candidate_ids: tuple[uuid.UUID, ...],
        rules: EpisodeRules,
    ) -> None:
        assert instance_id == self.instance_id
        assert candidate_ids
        self.suggested_rule_candidate_ids = candidate_ids
        self.suggested_rules = rules

    def validate_storyboard_character_references(
        self,
        instance_id: uuid.UUID,
        reference_asset_ids: tuple[uuid.UUID, ...],
    ) -> None:
        assert instance_id == self.instance_id
        self.validated_character_references = reference_asset_ids

    def validate_anchor_prompt_readiness(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
    ) -> None:
        assert instance_id == self.instance_id
        self.validated_anchor_shot_ids.append(shot_id)

    def record_task_children(
        self,
        _parent_step_id: uuid.UUID,
        child_step_ids: tuple[uuid.UUID, ...],
    ) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "stepId": str(step_id),
                "status": self.child_task_statuses.get(step_id, "pending"),
                "resultSummary": self.child_task_summaries.get(step_id),
            }
            for step_id in child_step_ids
        )


class _StoryWorkflow:
    def __init__(self, *, candidate_count: int = 1) -> None:
        self.story_command: object | None = None
        self.story_call_count = 0
        self.legacy_event_call_count = 0
        self.legacy_script_call_count = 0
        self.candidate_count = candidate_count
        self.storyboard_durations: tuple[int, ...] | None = None
        self.storyboard_source_story_revision_id: uuid.UUID | None = None
        self.storyboard_creation_mode: str | None = None
        self.storyboard_reference_asset_ids: tuple[uuid.UUID, ...] = ()
        self.storyboard_instruction: str | None = None

    def run_story_strategies(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.story_command = payload
        self.story_call_count += 1
        return {
            "id": str(uuid.uuid4()),
            "status": "succeeded",
            "candidates": [
                {
                    "id": str(uuid.uuid4()),
                    "title": f"雨后的小发现 {index + 1}",
                    "body": "孩子和猫咪在雨后一起观察发亮的叶子。",
                }
                for index in range(self.candidate_count)
            ],
        }

    def run_story_event_strategies(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        self.legacy_event_call_count += 1
        raise AssertionError("新的配方故事路径不得生成事件候选或调用 Critic")

    def expand_selected_story_event(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        self.legacy_script_call_count += 1
        raise AssertionError("新的配方故事路径不得再次扩写或调用 Critic")

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        source_story_revision_id: uuid.UUID | None = None,
        exact_durations: tuple[int, ...] | None = None,
        healing_recipe: bool = False,
        idempotency_key: str | None = None,
        creation_mode: str = "from_story",
        reference_asset_ids: tuple[uuid.UUID, ...] = (),
        instruction: str | None = None,
    ) -> dict[str, object]:
        assert healing_recipe is True
        assert idempotency_key
        self.storyboard_durations = exact_durations
        self.storyboard_source_story_revision_id = source_story_revision_id
        self.storyboard_creation_mode = creation_mode
        self.storyboard_reference_asset_ids = reference_asset_ids
        self.storyboard_instruction = instruction
        return {"projectId": str(project_id), "beats": []}


class _IdempotentStoryWorkflow(_StoryWorkflow):
    def __init__(self) -> None:
        super().__init__()
        self.director_call_count = 0
        self._result: dict[str, object] | None = None

    def run_story_strategies(self, project_id: uuid.UUID, payload: object) -> dict[str, object]:
        self.story_command = payload
        self.story_call_count += 1
        if self._result is None:
            self.director_call_count += 1
            self._result = {
                "id": str(uuid.uuid4()),
                "status": "succeeded",
                "candidateIds": [],
                "candidates": [
                    {
                        "id": str(uuid.uuid4()),
                        "revision": 1,
                        "title": "雨后的小发现",
                        "body": "孩子和猫咪在雨后一起观察发亮的叶子。",
                    }
                ],
            }
            self._result["candidateIds"] = [
                candidate["id"] for candidate in self._result["candidates"]  # type: ignore[index]
            ]
        return self._result


class _FailOnceSuggestedRulesRepository(_Repository):
    def __init__(self) -> None:
        super().__init__()
        self.rules_store_attempt_count = 0

    def store_suggested_episode_rules(
        self,
        instance_id: uuid.UUID,
        candidate_ids: tuple[uuid.UUID, ...],
        rules: EpisodeRules,
    ) -> None:
        self.rules_store_attempt_count += 1
        if self.rules_store_attempt_count == 1:
            raise RuntimeError("rules transaction failed")
        super().store_suggested_episode_rules(instance_id, candidate_ids, rules)


class _ShotWorkflow:
    def __init__(self) -> None:
        self.anchor_calls: list[dict[str, object]] = []
        self.video_calls: list[dict[str, object]] = []
        self.anchor_step_id = uuid.uuid4()
        self.video_step_id = uuid.uuid4()

    def generate_anchor(self, shot_id: uuid.UUID, **values: object) -> dict[str, object]:
        self.anchor_calls.append({"shotId": shot_id, **values})
        return {"stepId": str(self.anchor_step_id), "status": "pending"}

    def generate_video(self, shot_id: uuid.UUID, **values: object) -> dict[str, object]:
        self.video_calls.append({"shotId": shot_id, **values})
        return {"stepId": str(self.video_step_id), "status": "queued"}


class _SequenceWorkflow:
    def __init__(self) -> None:
        self.values: dict[str, object] | None = None

    def build_project_sequence(self, project_id: uuid.UUID, **values: object) -> object:
        self.values = {"projectId": project_id, **values}
        return SimpleNamespace(
            id=uuid.uuid4(),
            project_id=project_id,
            revision=1,
            status=SimpleNamespace(value="content_review"),
            plan=SimpleNamespace(duration_ms=31_000),
            rendered_asset_id=uuid.uuid4(),
        )


def test_model_metadata_contains_recipe_review_and_revision_payloads() -> None:
    recipe = Base.metadata.tables["cat_video.production_recipe_instances"]
    review = Base.metadata.tables["cat_video.human_review_decisions"]
    story = Base.metadata.tables["cat_video.story_revisions"]
    shot = Base.metadata.tables["cat_video.shot_beats"]

    assert recipe.c.production_run_id.unique is True
    assert recipe.c.revision.nullable is False
    assert review.c.production_recipe_instance_id.nullable is False
    assert review.c.target_hash.type.length == 64
    assert "episode_rules_json" in story.c
    assert "temporal_beats_json" in shot.c


def test_fixed_ip_subject_documents_lock_child_and_cat_to_canon_roles() -> None:
    documents = canon_v3_subject_documents()

    assert [item["kind"] for item in documents] == ["person", "animal"]
    assert [item["role"] for item in documents] == ["protagonist", "co_protagonist"]
    assert [reference["semanticKey"] for reference in documents[0]["references"]] == [
        "person:headshot",
        "person:fullbody",
    ]
    assert [reference["semanticKey"] for reference in documents[1]["references"]] == [
        "cat:front",
        "cat:side",
    ]
    assert "四足" in "".join(documents[1]["immutableTraits"])


def test_sequence_candidate_exposes_review_pin_and_export_asset() -> None:
    sequence_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    document = _sequence_candidate_json(
        SimpleNamespace(
            id=sequence_id,
            revision=3,
            status="content_review",
            duration_ms=15_000,
            rendered_asset_id=asset_id,
            audio_policy="native_fades",
        ),
        SimpleNamespace(
            id=asset_id,
            sha256="a" * 64,
            metadata_json={"qc": {"passed": True}},
            created_at=datetime.now(UTC),
        ),
    )

    assert document["id"] == str(sequence_id)
    assert document["revision"] == 3
    assert document["renderedAssetId"] == str(asset_id)
    assert document["contentUrl"].endswith(f"/{asset_id}/content")
    assert document["qc"] == {"passed": True}


def test_migration_renders_recipe_tables_and_revision_payload_columns() -> None:
    migration = _load_migration()
    assert migration.down_revision == "0021_libtv_subject_assistant"  # type: ignore[attr-defined]
    migration._schema = lambda: "cat_video"  # type: ignore[attr-defined]
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )

    with Operations.context(context):
        migration.upgrade()  # type: ignore[attr-defined]

    sql = output.getvalue()
    assert "CREATE TABLE cat_video.production_recipe_instances" in sql
    assert "CREATE TABLE cat_video.human_review_decisions" in sql
    assert "ADD COLUMN episode_rules_json" in sql
    assert "ADD COLUMN temporal_beats_json" in sql


def test_service_lists_recipe_and_projects_derived_stage_without_duplicate_state() -> None:
    repository = _Repository()
    service = ProductionRecipeService(repository=repository)

    recipes = service.list_recipes()
    created = service.create_instance(
        repository.project_id,
        ProductionRecipeInstanceDraft(
            theme="雨天收衣服",
            targetDurationSeconds=31,
            qualityTier="balanced",
        ),
    )

    assert recipes[0]["key"] == "healing_child_cat_v1"
    assert created["shotDurations"] == [11, 10, 10]
    assert created["stage"] == "concept"
    assert created["primaryAction"] == "生成完整故事候选"
    assert "stage" not in repository.row


def test_provider_backed_recipe_cost_is_never_presented_as_free_when_unconfigured() -> None:
    repository = _Repository()
    unmetered = ProductionRecipeService(repository=repository).get_instance(repository.instance_id)
    metered = ProductionRecipeService(
        repository=repository,
        director_call_cost_micros=12_500,
    ).get_instance(repository.instance_id)

    assert unmetered["estimatedCostMicros"] is None
    assert unmetered["storyGenerationEstimatedCostMicros"] is None
    assert unmetered["costEstimateStatus"] == "unmetered_paid"
    assert unmetered["costEstimateLabel"] == "付费调用·暂未计量"
    assert metered["estimatedCostMicros"] == 12_500
    assert metered["costEstimateStatus"] == "metered"


@pytest.mark.parametrize("candidate_count", [1, 2, 4])
def test_recipe_story_event_compatibility_route_uses_one_creative_candidate_batch(
    candidate_count: int,
) -> None:
    repository = _Repository()
    workflow = _StoryWorkflow(candidate_count=candidate_count)
    service = ProductionRecipeService(
        repository=repository,
        story_workflow=workflow,
        director_call_cost_micros=12_500,
    )

    result = service.run_story_events(
        repository.instance_id,
        PaidRecipeRunRequest(
            idempotencyKey=f"creative-batch-{candidate_count}",
            acceptEstimatedCostMicros=12_500,
        ),
    )

    assert len(result["candidates"]) == candidate_count
    assert workflow.story_call_count == 1
    assert workflow.legacy_event_call_count == 0
    assert workflow.legacy_script_call_count == 0
    assert len(repository.suggested_rule_candidate_ids) == candidate_count
    instruction = workflow.story_command.rewrite_instruction  # type: ignore[union-attr]
    assert "完整故事候选" in instruction
    assert "1–5" in instruction
    assert "childAction" not in instruction
    assert "无对白" not in instruction


def test_recipe_story_projection_uses_two_step_candidate_selection_without_critic() -> None:
    repository = _Repository()
    service = ProductionRecipeService(
        repository=repository,
        director_call_cost_micros=12_500,
    )

    empty = service.get_instance(repository.instance_id)
    repository.row["storyCandidates"] = [
        {
            "id": str(uuid.uuid4()),
            "status": "candidate",
            "body": "完整候选正文",
            "contractKind": "creative_text",
        }
    ]
    candidates = service.get_instance(repository.instance_id)

    assert empty["storyWorkflow"] == {
        "currentStep": 1,
        "totalSteps": 2,
        "status": "generate_candidates",
    }
    assert empty["primaryAction"] == "生成完整故事候选"
    assert empty["estimatedCostMicros"] == 12_500
    assert empty["storyGenerationEstimatedCostMicros"] == 12_500
    assert candidates["storyWorkflow"] == {
        "currentStep": 2,
        "totalSteps": 2,
        "status": "select_story",
    }
    assert candidates["primaryAction"] == "选择为当前剧情"
    assert candidates["estimatedCostMicros"] == 0
    assert candidates["storyGenerationEstimatedCostMicros"] == 12_500


def test_recipe_story_retry_restores_candidates_and_retries_rules_without_director_call() -> None:
    repository = _FailOnceSuggestedRulesRepository()
    workflow = _IdempotentStoryWorkflow()
    service = ProductionRecipeService(repository=repository, story_workflow=workflow)
    payload = PaidRecipeRunRequest(
        idempotencyKey="same-story-batch",
        acceptEstimatedCostMicros=0,
    )

    with pytest.raises(RuntimeError, match="rules transaction failed"):
        service.run_story(repository.instance_id, payload)

    recovered = service.run_story(repository.instance_id, payload)
    summary = _task_result_summary("recipe:story", recovered)

    assert workflow.story_call_count == 2
    assert workflow.director_call_count == 1
    assert repository.rules_store_attempt_count == 2
    assert repository.suggested_rule_candidate_ids == tuple(
        uuid.UUID(str(candidate["id"])) for candidate in recovered["candidates"]
    )
    assert summary["reviewTargets"] == [
        {
            "targetType": "story_revision",
            "targetId": recovered["candidates"][0]["id"],
            "targetRevision": 1,
        }
    ]


def test_storyboard_task_waits_on_the_structure_snapshot_that_the_web_confirms() -> None:
    storyboard_revision_id = uuid.uuid4()
    story_revision_id = uuid.uuid4()
    structure_hash = "a" * 64

    summary = _task_result_summary(
        "recipe:storyboard",
        {
            "status": "awaiting_review",
            "storyRevisionId": str(story_revision_id),
            "storyRevision": 3,
            "storyboardRevisionId": str(storyboard_revision_id),
            "storyboardRevision": 2,
            "structureHash": structure_hash,
        },
    )

    assert summary["reviewTargets"] == [
        {
            "targetType": "storyboard_structure",
            "targetId": str(storyboard_revision_id),
            "targetRevision": 2,
            "targetHash": structure_hash,
        }
    ]


def test_recipe_projection_prefers_current_story_then_full_candidates_then_legacy_events() -> None:
    repository = _Repository()
    service = ProductionRecipeService(repository=repository)
    repository.row["storyEvents"] = [{"id": str(uuid.uuid4()), "status": "candidate"}]
    repository.row["storyWorkflow"] = {
        "currentStep": 2,
        "totalSteps": 4,
        "status": "select_event",
        "legacy": True,
    }

    legacy_only = service.get_instance(repository.instance_id)
    repository.row["storyCandidates"] = [
        {
            "id": str(uuid.uuid4()),
            "status": "candidate",
            "sourceEventCandidateId": None,
            "body": "完整长文本候选",
            "contractKind": "creative_text",
        }
    ]
    with_candidate = service.get_instance(repository.instance_id)
    repository.row["progress"] = {
        **repository.row["progress"],
        "storyApproved": True,
        "characterDesignApproved": False,
        "episodeRulesLocked": True,
    }
    with_approved_story = service.get_instance(repository.instance_id)

    assert legacy_only["storyWorkflow"]["status"] == "select_event"
    assert legacy_only["primaryAction"] == "查看历史剧情数据"
    assert with_candidate["storyWorkflow"]["status"] == "select_story"
    assert with_candidate["primaryAction"] == "选择为当前剧情"
    assert with_approved_story["storyWorkflow"]["status"] == "complete"
    assert with_approved_story["phase"] == "character_design"


def test_story_event_compatibility_enqueue_rejects_stale_cost_before_creating_task() -> None:
    repository = _Repository()
    workflow = _StoryWorkflow(candidate_count=2)
    service = ProductionRecipeService(
        repository=repository,
        story_workflow=workflow,
        director_call_cost_micros=12_500,
    )

    with pytest.raises(ValueError, match="费用预估已变化"):
        service.enqueue_recipe_task(
            repository.instance_id,
            operation_key="recipe:story_events",
            payload=PaidRecipeRunRequest(
                idempotencyKey="stale-six-call-cost",
                acceptEstimatedCostMicros=75_000,
            ),
        )

    assert repository.enqueued_task_count == 0
    assert workflow.story_call_count == 0

    service.enqueue_recipe_task(
        repository.instance_id,
        operation_key="recipe:story_events",
        payload=PaidRecipeRunRequest(
            idempotencyKey="one-call-cost",
            acceptEstimatedCostMicros=12_500,
        ),
    )

    assert repository.enqueued_task_count == 1
    assert workflow.story_call_count == 0


def test_character_design_enqueue_uses_exact_frozen_preview_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _Repository()
    repository.row["progress"] = {
        **repository.row["progress"],
        "storyApproved": True,
        "episodeRulesLocked": True,
        "characterDesignApproved": False,
    }
    service = ProductionRecipeService(repository=repository)
    preview_calls: list[PaidRecipeRunRequest] = []

    def preview_character_design(
        _instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, object]:
        preview_calls.append(payload)
        return {"estimatedCostMicros": 1_120_000}

    monkeypatch.setattr(service, "preview_character_design", preview_character_design)
    payload = PaidRecipeRunRequest(
        idempotencyKey="character-preview-cost",
        acceptEstimatedCostMicros=1_120_000,
        expectedInputHash="a" * 64,
    )

    service._validate_enqueued_operation(
        service.get_instance(repository.instance_id),
        instance_id=repository.instance_id,
        operation_key="recipe:character_design",
        payload=payload,
        shot_id=None,
    )

    assert preview_calls == [payload]


@pytest.mark.parametrize("operation_key", ["recipe:story", "recipe:story_events"])
def test_story_generation_with_existing_candidates_still_accepts_one_director_cost(
    operation_key: str,
) -> None:
    repository = _Repository()
    repository.row["storyCandidates"] = [
        {
            "id": str(uuid.uuid4()),
            "status": "candidate",
            "sourceEventCandidateId": None,
            "body": "已存在的完整故事候选",
            "contractKind": "creative_text",
        }
    ]
    service = ProductionRecipeService(
        repository=repository,
        director_call_cost_micros=12_500,
    )

    instance = service.get_instance(repository.instance_id)
    service.enqueue_recipe_task(
        repository.instance_id,
        operation_key=operation_key,
        payload=PaidRecipeRunRequest(
            idempotencyKey=f"supplement-{operation_key}",
            acceptEstimatedCostMicros=12_500,
        ),
    )

    assert instance["estimatedCostMicros"] == 0
    assert instance["storyGenerationEstimatedCostMicros"] == 12_500
    assert repository.enqueued_task_count == 1


def test_recipe_group_runs_one_story_batch_and_stops_at_candidate_selection() -> None:
    repository = _Repository()
    workflow = _StoryWorkflow(candidate_count=2)
    service = ProductionRecipeService(
        repository=repository,
        story_workflow=workflow,
        director_call_cost_micros=12_500,
    )
    group_id = uuid.uuid4()

    generated = service.run_group(
        group_id,
        CanvasGroupRunRequest(
            idempotencyKey="group-story-batch",
            acceptEstimatedCostMicros=12_500,
        ),
        parent_step_id=uuid.uuid4(),
    )
    repository.row["storyCandidates"] = [
        {
            "id": str(uuid.uuid4()),
            "status": "candidate",
            "body": "完整候选正文",
            "contractKind": "creative_text",
        }
    ]
    stopped = service.run_group(
        group_id,
        CanvasGroupRunRequest(
            idempotencyKey="group-await-choice",
            acceptEstimatedCostMicros=0,
        ),
        parent_step_id=uuid.uuid4(),
    )

    assert generated["executedPhase"] == "story"
    assert workflow.story_call_count == 1
    assert workflow.legacy_event_call_count == 0
    assert workflow.legacy_script_call_count == 0
    assert stopped["status"] == "awaiting_review"
    assert stopped["primaryAction"] == "选择为当前剧情"
    assert workflow.story_call_count == 1


def test_recipe_group_storyboard_uses_the_current_approved_story_revision() -> None:
    repository = _Repository()
    approved_story_id = uuid.uuid4()
    repository.row.update(
        progress={
            "storyApproved": True,
            "characterDesignApproved": True,
            "episodeRulesLocked": True,
            "shotCount": 0,
            "approvedAnchorCount": 0,
            "approvedVideoCount": 0,
            "sequenceReady": False,
            "finalApproved": False,
        },
        storyCandidates=[
            {
                "id": str(approved_story_id),
                "status": "approved",
                "body": "当前完整剧情",
                "contractKind": "creative_text",
            }
        ],
    )
    workflow = _StoryWorkflow()
    service = ProductionRecipeService(
        repository=repository,
        story_workflow=workflow,
    )

    result = service.run_group(
        uuid.uuid4(),
        CanvasGroupRunRequest(
            idempotencyKey="group-storyboard-run",
            acceptEstimatedCostMicros=0,
        ),
        parent_step_id=uuid.uuid4(),
    )

    assert result["executedPhase"] == "storyboard"
    assert workflow.storyboard_source_story_revision_id == approved_story_id


def test_recipe_storyboard_does_not_use_episode_rules_as_story_quality_gate() -> None:
    repository = _Repository()
    approved_story_id = uuid.uuid4()
    repository.row.update(
        progress={
            "storyApproved": True,
            "characterDesignApproved": True,
            "episodeRulesLocked": False,
            "shotCount": 0,
            "approvedAnchorCount": 0,
            "approvedVideoCount": 0,
            "sequenceReady": False,
            "finalApproved": False,
        },
        storyCandidates=[
            {
                "id": str(approved_story_id),
                "status": "approved",
                "body": "当前完整剧情",
                "contractKind": "creative_text",
            }
        ],
    )
    workflow = _StoryWorkflow()
    service = ProductionRecipeService(
        repository=repository,
        story_workflow=workflow,
    )

    result = service.run_group(
        uuid.uuid4(),
        CanvasGroupRunRequest(
            idempotencyKey="group-storyboard-no-rules",
            acceptEstimatedCostMicros=0,
        ),
        parent_step_id=uuid.uuid4(),
    )

    assert result["executedPhase"] == "storyboard"
    assert workflow.storyboard_source_story_revision_id == approved_story_id


def test_selecting_story_revision_completes_story_workflow_without_provider_call() -> None:
    repository = _Repository()
    workflow = _StoryWorkflow(candidate_count=2)
    service = ProductionRecipeService(repository=repository, story_workflow=workflow)
    story_id = uuid.uuid4()

    service.record_review(
        repository.instance_id,
        HumanReviewDraft(
            targetType="story_revision",
            targetId=story_id,
            targetRevision=1,
            decision="approve",
        ),
        episode_rules=_suggest_episode_rules("雨后亮叶", repository.row),
    )
    repository.row["storyCandidates"] = [
        {
            "id": str(story_id),
            "status": "approved",
            "body": "孩子和猫咪一起观察雨后发亮的叶子。",
            "contractKind": "creative_text",
        }
    ]
    repository.row["progress"] = {
        **repository.row["progress"],
        "storyApproved": True,
        "episodeRulesLocked": True,
        "characterDesignApproved": False,
    }
    selected = service.get_instance(repository.instance_id)

    assert workflow.story_call_count == 0
    assert workflow.legacy_event_call_count == 0
    assert workflow.legacy_script_call_count == 0
    assert selected["storyWorkflow"]["status"] == "complete"
    assert selected["phase"] == "character_design"


def test_service_derives_video_stage_from_reviewed_anchor_progress() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 2,
        "approvedAnchorCount": 2,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
    }
    service = ProductionRecipeService(repository=repository)

    instance = service.get_instance(repository.instance_id)

    assert instance["stage"] == "video"
    assert instance["primaryAction"] == "生成下一镜视频"


def test_service_derives_video_stage_when_reference_media_needs_no_anchor() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "storyboardApproved": True,
        "shotCount": 1,
        "requiredAnchorCount": 0,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
    }
    service = ProductionRecipeService(repository=repository)

    instance = service.get_instance(repository.instance_id)

    assert instance["stage"] == "video"
    assert instance["primaryAction"] == "生成下一镜视频"


def test_service_prompts_for_human_story_choice_when_candidates_exist() -> None:
    repository = _Repository()
    repository.row["storyCandidates"] = [{"id": str(uuid.uuid4()), "status": "candidate"}]
    service = ProductionRecipeService(repository=repository)

    instance = service.get_instance(repository.instance_id)

    assert instance["stage"] == "concept"
    assert instance["primaryAction"] == "选择为当前剧情"
    assert instance["currentBlocker"] == "完整故事候选等待人工选择"


def test_service_derives_final_review_action_after_sequence_render() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 1,
        "approvedVideoCount": 1,
        "sequenceReady": True,
        "finalApproved": False,
    }
    service = ProductionRecipeService(repository=repository)

    instance = service.get_instance(repository.instance_id)

    assert instance["stage"] == "sequence"
    assert instance["primaryAction"] == "审核最终成片"


def test_service_updates_optimistically_and_records_pinned_review() -> None:
    repository = _Repository()
    service = ProductionRecipeService(repository=repository)
    updated = service.update_instance(
        repository.instance_id,
        expected_revision=1,
        payload=ProductionRecipeInstancePatch(qualityTier="premium"),
    )
    target_id = uuid.uuid4()
    review = service.record_review(
        repository.instance_id,
        HumanReviewDraft(
            targetType="story_revision",
            targetId=target_id,
            targetRevision=3,
            decision="approve",
        ),
    )

    assert updated["revision"] == 2
    assert updated["qualityTier"] == "premium"
    assert review["targetRevision"] == 3
    assert repository.review is not None

    with pytest.raises(ValueError, match="版本冲突"):
        service.update_instance(
            repository.instance_id,
            expected_revision=1,
            payload=ProductionRecipeInstancePatch(theme="旧页面提交"),
        )


def test_recipe_story_and_storyboard_runs_add_fixed_ip_constraints() -> None:
    repository = _Repository()
    story_workflow = _StoryWorkflow()
    service = ProductionRecipeService(
        repository=repository,
        story_workflow=story_workflow,
    )
    story = service.run_story(
        repository.instance_id,
        PaidRecipeRunRequest(
            idempotencyKey="story-run-0001",
            acceptEstimatedCostMicros=0,
        ),
    )

    assert story["suggestedEpisodeRules"]["environment"] in {"indoor", "outdoor"}
    assert story_workflow.story_command is not None
    assert "低压力" in story_workflow.story_command.rewrite_instruction  # type: ignore[attr-defined]
    assert "无对白" not in story_workflow.story_command.rewrite_instruction  # type: ignore[attr-defined]

    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 0,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
        "episodeRulesLocked": True,
    }
    storyboard = service.run_storyboard(
        repository.instance_id,
        StoryboardRecipeRunRequest(
            idempotencyKey="board-run-0001",
            acceptEstimatedCostMicros=0,
            sourceStoryRevisionId=story["candidates"][0]["id"],
        ),
    )

    assert story_workflow.storyboard_durations == split_editorial_shot_durations(31)
    assert story_workflow.storyboard_creation_mode == "from_story"
    assert storyboard["materialized"] is True


def test_canon_v3_story_constraints_use_line_texture_and_reject_old_watercolor_sources() -> None:
    repository = _Repository()
    repository.row.update(
        {
            "canonProfileId": CANON_V3_PROFILE_ID,
            "visualProfile": {
                "sourceProfileId": CANON_V3_PROFILE_ID,
                "stylePositive": ["克制轮廓线", "湿润半透明高光", "柔和漫射光"],
                "styleNegative": ["摄影写实", "复制参考物体或构图"],
            },
        }
    )

    visual = _episode_visual_constraints(repository.row)
    instruction = _narrative_visual_instruction(repository.row)
    rules = _suggest_episode_rules("雨后小院里的发亮叶子", repository.row)

    assert visual.canon_profile_id == CANON_V3_PROFILE_ID
    assert set(CANON_V3_STYLE_POSITIVE).issubset(visual.positive)
    assert set(CANON_V3_STYLE_NEGATIVE).issubset(visual.negative)
    assert rules.canon_profile_id == CANON_V3_PROFILE_ID
    assert "复制参考图中的叶片、露珠或微距构图" in rules.style_excluded
    assert "同时混入旧室内或户外水彩参考" in instruction
    assert "二维水彩" not in instruction
    assert "原创柔和水彩画风" not in instruction


def test_canon_v2_story_constraints_preserve_the_locked_historical_style() -> None:
    repository = _Repository()

    visual = _episode_visual_constraints(repository.row)
    rules = _suggest_episode_rules("雨天窗边", repository.row)

    assert visual.canon_profile_id == CANON_V2_PROFILE_ID
    assert visual.positive == CANON_V2_STYLE_POSITIVE
    assert rules.style_positive == list(CANON_V2_STYLE_POSITIVE)
    assert rules.canon_profile_id == CANON_V2_PROFILE_ID


def test_character_storyboard_passes_approved_references_and_instruction() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 0,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
        "episodeRulesLocked": True,
        "characterDesignApproved": True,
    }
    story_workflow = _StoryWorkflow()
    service = ProductionRecipeService(
        repository=repository,
        story_workflow=story_workflow,
    )
    child_asset_id = uuid.uuid4()
    cat_asset_id = uuid.uuid4()
    approved_story_id = uuid.uuid4()

    storyboard = service.run_storyboard(
        repository.instance_id,
        StoryboardRecipeRunRequest(
            idempotencyKey="board-run-0001",
            acceptEstimatedCostMicros=0,
                creationMode="from_characters",
                sourceStoryRevisionId=approved_story_id,
            referenceAssetIds=[child_asset_id, cat_asset_id],
            instruction="孩子和猫咪在雨后一起观察一片发亮的叶子",
        ),
    )

    assert storyboard["materialized"] is True
    assert repository.validated_character_references == (child_asset_id, cat_asset_id)
    assert story_workflow.storyboard_creation_mode == "from_characters"
    assert story_workflow.storyboard_reference_asset_ids == (child_asset_id, cat_asset_id)
    assert story_workflow.storyboard_instruction == "孩子和猫咪在雨后一起观察一片发亮的叶子"


def test_recipe_parent_waits_for_all_generated_children_before_review() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
        "storyboardApproved": True,
    }
    shot_workflow = _ShotWorkflow()
    service = ProductionRecipeService(
        repository=repository,
        shot_workflow=shot_workflow,
    )
    instance = service.get_instance(repository.instance_id)
    payload = PaidRecipeRunRequest(
        idempotencyKey="anchor-run-0001",
        acceptEstimatedCostMicros=0,
    )
    payload_document = payload.model_dump(mode="json", by_alias=True)
    snapshot = {
        "recipeInstanceId": str(repository.instance_id),
        "expectedInstanceRevision": repository.row["revision"],
        "phase": instance["phase"],
        "shotId": str(uuid.uuid4()),
        "payload": payload_document,
        "sourceContentHash": recipe_task_source_hash(
            payload=payload_document,
            instance_id=repository.instance_id,
            expected_revision=int(repository.row["revision"]),
            phase=str(instance["phase"]),
        ),
    }
    parent_step_id = uuid.uuid4()
    repository.child_task_statuses[shot_workflow.anchor_step_id] = StepStatus.PENDING.value

    running = service.execute_queued_task(
        parent_step_id,
        operation_key="recipe:anchor",
        input_snapshot=snapshot,
    )

    assert running.status is StepStatus.QUEUED
    assert running.next_retry_at is not None
    assert running.payload["childStepIds"] == [str(shot_workflow.anchor_step_id)]

    repository.child_task_statuses[shot_workflow.anchor_step_id] = StepStatus.AWAITING_REVIEW.value
    anchor_asset_id = uuid.uuid4()
    repository.child_task_summaries[shot_workflow.anchor_step_id] = {
        "assetId": str(anchor_asset_id)
    }
    review = service.execute_queued_task(
        parent_step_id,
        operation_key="recipe:anchor",
        input_snapshot=snapshot,
    )

    assert review.status is StepStatus.AWAITING_REVIEW
    assert review.payload["message"] == "全部子任务已完成，等待人工审核"
    assert review.payload["reviewTargets"] == [
        {"targetType": "anchor_asset", "targetId": str(anchor_asset_id)}
    ]


def test_review_task_matching_requires_exact_recipe_and_target() -> None:
    recipe_id = uuid.uuid4()
    target_id = uuid.uuid4()
    step = SimpleNamespace(
        input_snapshot_json={"recipeInstanceId": str(recipe_id)},
        progress_json={
            "resultSummary": {
                "reviewTargets": [
                    {
                        "targetType": "creative_brief",
                        "targetId": str(target_id),
                        "targetRevision": 2,
                    }
                ]
            }
        },
    )
    payload = HumanReviewDraft(
        targetType="creative_brief",
        targetId=target_id,
        targetRevision=2,
        decision="approve",
    )

    assert _workflow_step_reviews_target(
        step,
        payload,
        recipe_instance_id=recipe_id,
    )
    assert not _workflow_step_reviews_target(
        step,
        payload,
        recipe_instance_id=uuid.uuid4(),
    )
    step.progress_json["childStepIds"] = [str(uuid.uuid4())]
    assert not _workflow_step_reviews_target(
        step,
        payload,
        recipe_instance_id=recipe_id,
    )


def test_storyboard_structure_review_settles_legacy_storyboard_task_target() -> None:
    recipe_id = uuid.uuid4()
    step = SimpleNamespace(
        input_snapshot_json={"recipeInstanceId": str(recipe_id)},
        progress_json={
            "resultSummary": {
                "operationKey": "recipe:storyboard",
                "reviewTargets": [
                    {
                        "targetType": "storyboard_revision",
                        "targetId": str(uuid.uuid4()),
                        "targetRevision": 1,
                    }
                ],
            }
        },
    )
    payload = HumanReviewDraft(
        targetType="storyboard_structure",
        targetId=uuid.uuid4(),
        targetRevision=2,
        targetHash="b" * 64,
        decision="approve",
    )

    assert _workflow_step_reviews_target(
        step,
        payload,
        recipe_instance_id=recipe_id,
    )


def test_storyboard_structure_review_settles_legacy_task_without_review_targets() -> None:
    recipe_id = uuid.uuid4()
    step = SimpleNamespace(
        input_snapshot_json={"recipeInstanceId": str(recipe_id)},
        progress_json={
            "resultSummary": {
                "operationKey": "recipe:storyboard",
                "status": "awaiting_review",
            }
        },
    )
    payload = HumanReviewDraft(
        targetType="storyboard_structure",
        targetId=uuid.uuid4(),
        targetRevision=3,
        targetHash="c" * 64,
        decision="approve",
    )

    assert _workflow_step_reviews_target(
        step,
        payload,
        recipe_instance_id=recipe_id,
    )
    assert not _workflow_step_reviews_target(
        step,
        payload,
        recipe_instance_id=uuid.uuid4(),
    )


def test_storyboard_structure_review_uses_immutable_operation_for_legacy_summary() -> None:
    recipe_id = uuid.uuid4()
    step = SimpleNamespace(
        input_snapshot_json={
            "recipeInstanceId": str(recipe_id),
            "operationKey": "recipe:storyboard",
        },
        progress_json={
            "resultSummary": {
                "status": "awaiting_review",
                "reviewTargets": [
                    {
                        "targetType": "storyboard_structure",
                        "targetId": str(uuid.uuid4()),
                        "targetRevision": 1,
                    }
                ],
            }
        },
    )
    payload = HumanReviewDraft(
        targetType="storyboard_structure",
        targetId=uuid.uuid4(),
        targetRevision=3,
        targetHash="d" * 64,
        decision="approve",
    )

    assert _workflow_step_reviews_target(
        step,
        payload,
        recipe_instance_id=recipe_id,
    )


def test_review_closes_child_then_aggregates_parent_only_after_all_children() -> None:
    target_id = uuid.uuid4()
    child_a = SimpleNamespace(
        status=StepStatus.AWAITING_REVIEW.value,
        input_snapshot_json={},
        progress_json={"resultSummary": {"assetId": str(target_id)}},
        error_json=None,
        completed_at=None,
        heartbeat_at=None,
        next_retry_at=None,
        lease_owner=None,
        lease_expires_at=None,
    )
    child_b = SimpleNamespace(status=StepStatus.AWAITING_REVIEW.value)
    parent = SimpleNamespace(
        status=StepStatus.AWAITING_REVIEW.value,
        progress_json={"childStepIds": [str(uuid.uuid4()), str(uuid.uuid4())]},
        error_json=None,
        completed_at=None,
        heartbeat_at=None,
        next_retry_at=None,
        lease_owner=None,
        lease_expires_at=None,
    )
    payload = HumanReviewDraft(
        targetType="character_design",
        targetId=target_id,
        targetHash="a" * 64,
        decision="approve",
    )

    _apply_review_to_workflow_step(
        child_a,
        payload=payload,
        review=SimpleNamespace(id=uuid.uuid4()),
    )

    assert child_a.status == StepStatus.SUCCEEDED.value
    assert _aggregate_review_parent_status([child_a, child_b]) is None
    child_b.status = StepStatus.SUCCEEDED.value
    aggregate = _aggregate_review_parent_status([child_a, child_b])
    assert aggregate is StepStatus.SUCCEEDED
    _apply_child_aggregate_to_workflow_step(
        parent,
        children=[child_a, child_b],
        status=aggregate,
    )
    assert parent.status == StepStatus.SUCCEEDED.value
    assert parent.progress_json["resultSummary"]["childSucceededCount"] == 2


def test_story_review_can_lock_edited_episode_rules() -> None:
    repository = _Repository()
    service = ProductionRecipeService(repository=repository)
    rules = EpisodeRules(
        personWardrobe="米白色针织衫与棕色背带裤",
        timeWeather="雨后黄昏",
        mainScene="厨房窗边",
        environment="indoor",
        coreProps=["布篮"],
        catBehaviorMode="natural",
        soundPlan={
            "ambient": ["雨滴"],
            "foley": ["布料摩擦"],
            "musicMood": "轻柔",
            "dialoguePolicy": "none",
        },
        stylePositive=["原创水彩", "柔和纸张纹理", "低对比暖色"],
        styleExcluded=["准写实", "3D塑料感"],
        canonProfileId="canon-v2-healing-child-cat",
    )

    service.record_review(
        repository.instance_id,
        HumanReviewDraft(
            targetType="story_revision",
            targetId=uuid.uuid4(),
            targetRevision=1,
            decision="approve",
        ),
        episode_rules=rules,
    )

    assert repository.review is not None
    assert repository.episode_rules == rules


def test_recipe_candidate_runs_forward_stable_per_candidate_idempotency_keys() -> None:
    repository = _Repository()
    repository.row["qualityTier"] = "premium"
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
    }
    workflow = _ShotWorkflow()
    service = ProductionRecipeService(repository=repository, shot_workflow=workflow)
    shot_id = uuid.uuid4()
    request = PaidRecipeRunRequest(
        idempotencyKey="paid-stage-0001",
        acceptEstimatedCostMicros=0,
    )

    service.run_anchor(repository.instance_id, shot_id, request)

    assert repository.validated_anchor_shot_ids == [shot_id]
    assert [item["request_idempotency_key"] for item in workflow.anchor_calls] == [
        "paid-stage-0001:anchor:1",
        "paid-stage-0001:anchor:2",
        "paid-stage-0001:anchor:3",
        "paid-stage-0001:anchor:4",
    ]


def test_recipe_redo_reason_forces_a_new_first_candidate_attempt() -> None:
    repository = _Repository()
    repository.row["qualityTier"] = "quick"
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 0,
        "approvedVideoCount": 0,
        "sequenceReady": False,
        "finalApproved": False,
    }
    workflow = _ShotWorkflow()
    service = ProductionRecipeService(repository=repository, shot_workflow=workflow)

    service.run_anchor(
        repository.instance_id,
        uuid.uuid4(),
        PaidRecipeRunRequest(
            idempotencyKey="redo-anchor-0001",
            acceptEstimatedCostMicros=0,
            reason="人物身份错误，退回锚点重做",
        ),
    )

    assert workflow.anchor_calls[0]["regenerate"] is True
    assert workflow.anchor_calls[0]["reason"] == "人物身份错误，退回锚点重做"


def test_recipe_sequence_forwards_request_idempotency_key() -> None:
    repository = _Repository()
    repository.row["progress"] = {
        "storyApproved": True,
        "shotCount": 1,
        "approvedAnchorCount": 1,
        "approvedVideoCount": 1,
        "sequenceReady": False,
        "finalApproved": False,
    }
    workflow = _SequenceWorkflow()
    service = ProductionRecipeService(
        repository=repository,
        sequence_workflow=workflow,
    )

    result = service.run_sequence(
        repository.instance_id,
        RecipeSequenceRunRequest(
            idempotencyKey="sequence-stage-0001",
            acceptEstimatedCostMicros=0,
        ),
    )

    assert result["status"] == "content_review"
    assert workflow.values is not None
    assert workflow.values["request_idempotency_key"] == "sequence-stage-0001"


def test_group_download_contains_only_safe_successful_assets_and_review_manifest(
    tmp_path: Path,
) -> None:
    group_id = uuid.uuid4()
    valid_asset_id = uuid.uuid4()
    escaped_asset_id = uuid.uuid4()
    missing_asset_id = uuid.uuid4()
    media_dir = tmp_path / "sequence"
    media_dir.mkdir()
    (media_dir / "final.mp4").write_bytes(b"video-bytes")

    class _DownloadRepository(_Repository):
        def group_download_assets(self, requested_group_id: uuid.UUID) -> dict[str, object]:
            assert requested_group_id == group_id
            return {
                "groupId": str(group_id),
                "title": "一人一猫:雨后",
                "assets": [
                    {
                        "id": str(valid_asset_id),
                        "role": "final_sequence",
                        "storageKey": "sequence/final.mp4",
                    },
                    {
                        "id": str(escaped_asset_id),
                        "role": "video",
                        "storageKey": "../outside.mp4",
                    },
                    {
                        "id": str(missing_asset_id),
                        "role": "anchor",
                        "storageKey": "missing/anchor.png",
                    },
                ],
            }

    service = ProductionRecipeService(
        repository=_DownloadRepository(),
        asset_root=tmp_path,
    )

    content, filename = service.build_group_download(group_id)

    assert filename == "一人一猫雨后-assets.zip"
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
        assert f"media/final_sequence/{valid_asset_id}.mp4" in names
        assert "人工审核清单.md" in names
        manifest = json.loads(archive.read("manifest.json"))
        assert {item["assetId"] for item in manifest["missing"]} == {
            str(escaped_asset_id),
            str(missing_asset_id),
        }
        assert "分镜已批准" in archive.read("人工审核清单.md").decode("utf-8")
