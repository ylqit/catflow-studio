"""Application boundary for fixed-IP production recipe instances."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, Sequence

from ..domain.production_recipes import (
    CANON_V2_PROFILE_ID,
    CANON_V2_STYLE_NEGATIVE,
    CANON_V2_STYLE_POSITIVE,
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    CANON_V4_PROFILE_ID,
    CANON_V4_STYLE_NEGATIVE,
    CANON_V4_STYLE_POSITIVE,
    HEALING_CHILD_CAT_RECIPE,
    CanvasGroupRunRequest,
    CatBehaviorMode,
    CharacterDesignBatchDraft,
    CharacterDesignRecipeRunRequest,
    CharacterDesignRunStage,
    DirectorWorkflowAdoptionRequest,
    EpisodeRules,
    GenerationPlanRevisionDraft,
    HumanReviewDraft,
    PaidRecipeRunRequest,
    ProductionRecipeInstanceDraft,
    ProductionRecipeInstancePatch,
    RecipePhaseKey,
    RecipeSequenceRunRequest,
    RecipeStage,
    SoundPlan,
    StoryboardCreationMode,
    StoryboardProductionPlanConfirmation,
    StoryboardRecipeRunRequest,
    recipe_task_source_hash,
    split_editorial_shot_durations,
    split_shot_durations,
)
from ..domain.workflow import StepKind, StepStatus
from .universal_media_worker import MediaExecutionResult


class ProductionRecipeRepository(Protocol):
    def preview_director_workflow_adoption(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, Any]: ...

    def adopt_director_workflow(
        self,
        project_id: uuid.UUID,
        payload: DirectorWorkflowAdoptionRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def create_instance(
        self,
        project_id: uuid.UUID,
        payload: ProductionRecipeInstanceDraft,
    ) -> dict[str, Any]: ...

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, Any]: ...

    def enqueue_task(
        self,
        instance_id: uuid.UUID,
        *,
        operation_key: str,
        kind: StepKind,
        payload: dict[str, Any],
        idempotency_key: str,
        expected_phase: str,
        expected_revision: int,
        canvas_node_id: uuid.UUID,
        shot_id: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        creation_mode: str | None = None,
    ) -> dict[str, Any]: ...

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, Any]: ...

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, Any]: ...

    def confirm_storyboard_production_plan(
        self,
        instance_id: uuid.UUID,
        payload: StoryboardProductionPlanConfirmation,
    ) -> dict[str, Any]: ...

    def revise_generation_plan(
        self,
        instance_id: uuid.UUID,
        plan_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: GenerationPlanRevisionDraft,
    ) -> dict[str, Any]: ...

    def materialize_storyboard(
        self,
        instance_id: uuid.UUID,
        storyboard: dict[str, Any],
    ) -> dict[str, Any]: ...

    def store_suggested_episode_rules(
        self,
        instance_id: uuid.UUID,
        candidate_ids: tuple[uuid.UUID, ...],
        rules: EpisodeRules,
    ) -> None: ...

    def prepare_character_design(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
        candidate_count: int,
        stage: CharacterDesignRunStage = CharacterDesignRunStage.ALL,
    ) -> dict[str, Any]: ...

    def preview_character_design(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
        candidate_count: int,
        stage: CharacterDesignRunStage = CharacterDesignRunStage.ALL,
    ) -> dict[str, Any]: ...

    def prepare_character_design_validation(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def preview_character_design_validation(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def validate_storyboard_character_references(
        self,
        instance_id: uuid.UUID,
        reference_asset_ids: tuple[uuid.UUID, ...],
    ) -> None: ...

    def validate_anchor_prompt_readiness(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
    ) -> None: ...

    def record_task_children(
        self,
        parent_step_id: uuid.UUID,
        child_step_ids: tuple[uuid.UUID, ...],
    ) -> tuple[dict[str, Any], ...]: ...

    def get_group(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, Any]: ...

    def convert_to_shot_groups(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def group_download_assets(self, group_id: uuid.UUID) -> dict[str, Any]: ...


class StoryRecipeWorkflow(Protocol):
    def complete_creative_brief(
        self,
        project_id: uuid.UUID,
        *,
        theme: str,
        target_duration_seconds: int,
    ) -> dict[str, Any]: ...

    def run_story_strategies(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]: ...

    def run_story_event_strategies(
        self,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]: ...

    def expand_selected_story_event(
        self,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]: ...

    def create_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        source_story_revision_id: uuid.UUID | None = None,
        exact_durations: tuple[int, ...] | None = None,
        healing_recipe: bool = False,
        idempotency_key: str | None = None,
        creation_mode: str = StoryboardCreationMode.FROM_STORY.value,
        reference_asset_ids: tuple[uuid.UUID, ...] = (),
        instruction: str | None = None,
    ) -> dict[str, Any]: ...

    def create_generation_batch(self, payload: Any) -> dict[str, Any]: ...

    def create_generation_batches(
        self,
        payloads: Sequence[Any],
        *,
        parent_step_id: uuid.UUID,
    ) -> tuple[dict[str, Any], ...]: ...

    def preview_generation_batches(
        self,
        payloads: Sequence[Any],
    ) -> tuple[dict[str, Any], ...]: ...


class ShotRecipeWorkflow(Protocol):
    def generate_anchor(self, shot_id: uuid.UUID, **values: Any) -> dict[str, Any]: ...

    def generate_video(self, shot_id: uuid.UUID, **values: Any) -> dict[str, Any]: ...


class SequenceRecipeWorkflow(Protocol):
    def build_project_sequence(self, project_id: uuid.UUID, **values: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class RecipeStoryCommand:
    idempotency_key: str
    rewrite_instruction: str


class ProductionRecipeService:
    """Validates recipe choices and derives workflow state from durable facts."""

    def __init__(
        self,
        *,
        repository: ProductionRecipeRepository,
        story_workflow: StoryRecipeWorkflow | None = None,
        shot_workflow: ShotRecipeWorkflow | None = None,
        sequence_workflow: SequenceRecipeWorkflow | None = None,
        director_call_cost_micros: int | None = None,
        image_call_cost_micros: int | None = None,
        video_call_cost_micros: int | None = None,
        asset_root: Path | None = None,
    ) -> None:
        self._repository = repository
        self._story_workflow = story_workflow
        self._shot_workflow = shot_workflow
        self._sequence_workflow = sequence_workflow
        self._director_call_cost_micros = director_call_cost_micros
        self._image_call_cost_micros = image_call_cost_micros
        self._video_call_cost_micros = video_call_cost_micros
        self._asset_root = None if asset_root is None else asset_root.expanduser().resolve()

    def list_recipes(self) -> list[dict[str, Any]]:
        return [HEALING_CHILD_CAT_RECIPE.model_dump(mode="json", by_alias=True)]

    def preview_director_workflow_adoption(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, Any]:
        return self._repository.preview_director_workflow_adoption(project_id)

    def adopt_director_workflow(
        self,
        project_id: uuid.UUID,
        payload: DirectorWorkflowAdoptionRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if payload.recipe_key != HEALING_CHILD_CAT_RECIPE.key:
            raise ValueError("当前版本只支持一人一猫治愈短片配方")
        return self._repository.adopt_director_workflow(
            project_id,
            payload,
            idempotency_key=idempotency_key,
        )

    def create_instance(
        self,
        project_id: uuid.UUID,
        payload: ProductionRecipeInstanceDraft,
    ) -> dict[str, Any]:
        if payload.recipe_key != HEALING_CHILD_CAT_RECIPE.key:
            raise ValueError("当前版本只支持一人一猫治愈短片配方")
        return self._project_instance(self._repository.create_instance(project_id, payload))

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, Any]:
        return self._project_instance(self._repository.get_instance(instance_id))

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, Any]:
        return self._project_instance(
            self._repository.update_instance(
                instance_id,
                expected_revision=expected_revision,
                payload=payload,
            )
        )

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, Any]:
        return self._repository.record_review(
            instance_id,
            payload,
            episode_rules=episode_rules,
        )

    def confirm_storyboard_production_plan(
        self,
        instance_id: uuid.UUID,
        payload: StoryboardProductionPlanConfirmation,
    ) -> dict[str, Any]:
        return self._repository.confirm_storyboard_production_plan(instance_id, payload)

    def revise_generation_plan(
        self,
        instance_id: uuid.UUID,
        plan_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: GenerationPlanRevisionDraft,
    ) -> dict[str, Any]:
        return self._project_instance(
            self._repository.revise_generation_plan(
                instance_id,
                plan_id,
                expected_revision=expected_revision,
                payload=payload,
            )
        )

    def enqueue_recipe_task(
        self,
        instance_id: uuid.UUID,
        *,
        operation_key: str,
        payload: PaidRecipeRunRequest,
        shot_id: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        creation_mode: str | None = None,
    ) -> dict[str, Any]:
        instance = self.get_instance(instance_id)
        self._validate_enqueued_operation(
            instance,
            instance_id=instance_id,
            operation_key=operation_key,
            payload=payload,
            shot_id=shot_id,
        )
        project_id = uuid.UUID(str(instance["projectId"]))
        return self._repository.enqueue_task(
            instance_id,
            operation_key=operation_key,
            kind=_task_kind(operation_key, instance),
            payload=payload.model_dump(mode="json", by_alias=True),
            idempotency_key=payload.idempotency_key,
            expected_phase=str(instance["phase"]),
            expected_revision=int(instance["revision"]),
            canvas_node_id=_task_canvas_node_id(project_id, operation_key),
            shot_id=shot_id,
            group_id=group_id,
            creation_mode=creation_mode,
        )

    def enqueue_group_task(
        self,
        group_id: uuid.UUID,
        payload: CanvasGroupRunRequest,
    ) -> dict[str, Any]:
        compiled = self.compile_group(group_id)
        instance_id = uuid.UUID(str(compiled["recipeInstanceId"]))
        return self.enqueue_recipe_task(
            instance_id,
            operation_key="canvas-group:run",
            payload=payload,
            group_id=group_id,
        )

    def execute_queued_task(
        self,
        step_id: uuid.UUID,
        *,
        operation_key: str,
        input_snapshot: dict[str, object],
    ) -> MediaExecutionResult:
        instance_id = uuid.UUID(str(input_snapshot["recipeInstanceId"]))
        instance = self.get_instance(instance_id)
        expected_revision = int(input_snapshot["expectedInstanceRevision"])
        expected_phase = str(input_snapshot["phase"])
        if int(instance["revision"]) != expected_revision:
            raise ValueError("任务输入版本已经过期，请重新提交")
        if str(instance["phase"]) != expected_phase:
            raise ValueError("任务阶段已经变化，请重新提交")
        payload_document = dict(input_snapshot["payload"])  # type: ignore[arg-type]
        current_hash = recipe_task_source_hash(
            payload=payload_document,
            instance_id=instance_id,
            expected_revision=expected_revision,
            phase=expected_phase,
        )
        if current_hash != input_snapshot.get("sourceContentHash"):
            raise ValueError("任务固定输入内容校验失败，请重新提交")
        if operation_key == "recipe:storyboard":
            payload = StoryboardRecipeRunRequest.model_validate(payload_document)
            result = self.run_storyboard(instance_id, payload)
        elif operation_key == "recipe:sequence":
            sequence_payload = RecipeSequenceRunRequest.model_validate(payload_document)
            result = self.run_sequence(instance_id, sequence_payload)
        elif operation_key == "canvas-group:run":
            group_id = uuid.UUID(str(input_snapshot["canvasGroupId"]))
            group_payload = CanvasGroupRunRequest.model_validate(payload_document)
            result = self.run_group(group_id, group_payload, parent_step_id=step_id)
        elif operation_key == "recipe:character_design":
            character_payload = CharacterDesignRecipeRunRequest.model_validate(payload_document)
            result = self.run_character_design(
                instance_id,
                character_payload,
                parent_step_id=step_id,
            )
        else:
            payload = PaidRecipeRunRequest.model_validate(payload_document)
            shot_value = input_snapshot.get("shotId")
            if operation_key == "recipe:creative":
                result = self.run_creative_brief(instance_id, payload)
            elif operation_key == "recipe:story_events":
                result = self.run_story_events(instance_id, payload)
            elif operation_key == "recipe:story_script":
                result = self.run_story_script(instance_id, payload)
            elif operation_key == "recipe:story":
                result = self.run_story(instance_id, payload)
            elif operation_key == "recipe:character_design_validation":
                result = self.run_character_design_validation(
                    instance_id,
                    payload,
                    parent_step_id=step_id,
                )
            elif operation_key == "recipe:anchor" and shot_value is not None:
                result = self.run_anchor(instance_id, uuid.UUID(str(shot_value)), payload)
            elif operation_key == "recipe:video" and shot_value is not None:
                result = self.run_video(instance_id, uuid.UUID(str(shot_value)), payload)
            else:
                raise ValueError(f"不支持的持久配方任务：{operation_key}")
        summary = _task_result_summary(operation_key, result)
        child_step_ids = _result_child_step_ids(result)
        if not child_step_ids:
            return MediaExecutionResult(
                payload=summary,
                status=StepStatus.AWAITING_REVIEW,
            )

        child_steps = self._repository.record_task_children(step_id, child_step_ids)
        child_statuses = {str(item["status"]) for item in child_steps}
        summary["parentStepId"] = str(step_id)
        summary["childStepIds"] = [str(item["stepId"]) for item in child_steps]
        summary["childStatuses"] = [
            {
                "stepId": str(item["stepId"]),
                "status": str(item["status"]),
                "resultSummary": item.get("resultSummary"),
            }
            for item in child_steps
        ]
        summary["reviewTargets"] = _child_review_targets(operation_key, child_steps)
        failed_steps = [item for item in child_steps if item["status"] == StepStatus.FAILED.value]
        if failed_steps:
            failed_ids = ", ".join(str(item["stepId"]) for item in failed_steps)
            raise RuntimeError(f"子任务执行失败：{failed_ids}")
        if StepStatus.SUBMISSION_UNKNOWN.value in child_statuses:
            summary["message"] = "Provider 提交状态未知，请打开对应子任务进行人工对账"
            return MediaExecutionResult(payload=summary, status=StepStatus.SUBMISSION_UNKNOWN)
        active_statuses = {
            StepStatus.PENDING.value,
            StepStatus.SUBMITTING.value,
            StepStatus.QUEUED.value,
            StepStatus.RUNNING.value,
        }
        if child_statuses.intersection(active_statuses):
            summary["status"] = "running"
            summary["message"] = "子任务仍在生成或查询 Provider 状态"
            return MediaExecutionResult(
                payload=summary,
                status=StepStatus.QUEUED,
                next_retry_at=datetime.now(UTC) + timedelta(seconds=2),
            )
        summary["message"] = "全部子任务已完成，等待人工审核"
        return MediaExecutionResult(payload=summary, status=StepStatus.AWAITING_REVIEW)

    def run_creative_brief(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.CREATIVE.value:
            raise ValueError("只有创意阶段可以执行 AI 创意补全")
        self._accept_cost(payload, self._director_call_cost_micros)
        result = workflow.complete_creative_brief(
            uuid.UUID(str(instance["projectId"])),
            theme=str(instance["theme"]),
            target_duration_seconds=int(instance["targetDurationSeconds"]),
        )
        return {**result, "recipeInstanceId": str(instance_id), "status": "awaiting_review"}

    def run_story(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.STORY.value:
            raise ValueError("创意简报人工批准后才能生成故事候选")
        if not instance.get("progress", {}).get("creativeApproved", True):
            raise ValueError("创意简报尚未人工批准")
        self._accept_cost(payload, self._director_call_cost_micros)
        rules = _suggest_episode_rules(str(instance["theme"]), instance)
        command = RecipeStoryCommand(
            idempotency_key=payload.idempotency_key,
            rewrite_instruction=(
                "一次生成 1–5 个原创、低压力的完整故事候选，以可直接编辑的长正文为主；"
                "候选应在事件冲突、人物行动和情绪落点上有明显差异。"
                "保持固定儿童、固定猫咪及二者稳定关系，猫咪行为以自然可信为宜；"
                "对白、场景数量、情绪节奏和结尾方式属于创作建议，不要输出质量评分，"
                "也不要模仿或复制任何参考账号的独特角色造型、画风或品牌元素。"
                f"{_narrative_visual_instruction(instance)}"
            ),
        )
        result = workflow.run_story_strategies(
            uuid.UUID(str(instance["projectId"])),
            command,
        )
        candidates = list(result.get("candidates") or [])
        candidate_ids = tuple(uuid.UUID(str(item["id"])) for item in candidates)
        self._repository.store_suggested_episode_rules(instance_id, candidate_ids, rules)
        return {
            **result,
            "recipeInstanceId": str(instance_id),
            "suggestedEpisodeRules": rules.model_dump(mode="json", by_alias=True),
            "candidates": [
                {
                    **candidate,
                    "episodeRules": rules.model_dump(mode="json", by_alias=True),
                }
                for candidate in candidates
            ],
        }

    def run_story_events(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        # HTTP compatibility boundary: old clients may still post to
        # ``story-event-runs``.  New work must nevertheless execute the same
        # single creative-text batch as ``story-runs``; no event rows, Critic,
        # or second expansion call are created.
        result = self.run_story(instance_id, payload)
        return {
            **result,
            "legacyCompatibilityOperation": "story-event-runs",
        }

    def run_story_script(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.STORY.value:
            raise ValueError("只有剧情阶段可以扩写完整剧情脚本")
        story_workflow = dict(instance.get("storyWorkflow") or {})
        if story_workflow.get("status") != "expand_script":
            raise ValueError("请先从最新一批事件方案中人工选择一个事件")
        self._accept_cost(
            payload,
            _multiply_cost(self._director_call_cost_micros, 2),
        )
        command = RecipeStoryCommand(
            idempotency_key=payload.idempotency_key,
            rewrite_instruction=(
                "把已选择事件扩写为完整、可拍摄的剧情脚本，明确因果链、稳定 sceneKey、"
                "场景目的、换场原因、声音计划、角色造型与场景资产需求；保持无对白和"
                "固定儿童、猫咪及画风约束。"
                f"{_narrative_visual_instruction(instance)}"
            ),
        )
        result = workflow.expand_selected_story_event(
            uuid.UUID(str(instance["projectId"])),
            instance_id,
            command,
        )
        story = dict(result.get("story") or {})
        story_id_value = story.get("id")
        if story_id_value is None:
            raise RuntimeError("剧情脚本扩写没有返回版本标识")
        rules = _suggest_episode_rules(str(instance["theme"]), instance)
        self._repository.store_suggested_episode_rules(
            instance_id,
            (uuid.UUID(str(story_id_value)),),
            rules,
        )
        return {
            **result,
            "recipeInstanceId": str(instance_id),
            "revisionId": str(story_id_value),
            "suggestedEpisodeRules": rules.model_dump(mode="json", by_alias=True),
            "status": "awaiting_review",
        }

    def run_character_design(
        self,
        instance_id: uuid.UUID,
        payload: CharacterDesignRecipeRunRequest,
        *,
        parent_step_id: uuid.UUID,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.CHARACTER_DESIGN.value:
            raise ValueError("故事与 EpisodeRules 人工批准后才能生成角色设计")
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[instance["qualityTier"]]
        candidate_count = tier.character_design_candidate_count
        preview = self.preview_character_design(instance_id, payload)
        if payload.expected_input_hash is None:
            raise ValueError("付费角色设计必须先查看三个槽位的服务端输入预览")
        if payload.expected_input_hash != preview["inputHash"]:
            raise ValueError("角色设计引用、Prompt 或模型已变化，请重新查看费用与输入")
        self._accept_cost(payload, preview["estimatedCostMicros"])
        prepared = self._repository.prepare_character_design(
            instance_id,
            idempotency_key=payload.idempotency_key,
            candidate_count=candidate_count,
            stage=payload.character_design_stage,
        )
        drafts = tuple(
            CharacterDesignBatchDraft.model_validate(batch) for batch in prepared["batches"]
        )
        batches = workflow.create_generation_batches(
            drafts,
            parent_step_id=parent_step_id,
        )
        return {
            **prepared,
            "status": "generating",
            "generationBatches": batches,
        }

    def preview_character_design(
        self,
        instance_id: uuid.UUID,
        payload: CharacterDesignRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.CHARACTER_DESIGN.value:
            raise ValueError("故事与 EpisodeRules 人工批准后才能预览角色设计")
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[instance["qualityTier"]]
        candidate_count = tier.character_design_candidate_count
        prepared = self._repository.preview_character_design(
            instance_id,
            idempotency_key=payload.idempotency_key,
            candidate_count=candidate_count,
            stage=payload.character_design_stage,
        )
        drafts = tuple(
            CharacterDesignBatchDraft.model_validate(batch) for batch in prepared["batches"]
        )
        slots = workflow.preview_generation_batches(drafts)
        aggregate = hashlib.sha256(
            json.dumps(
                [item["inputHash"] for item in slots],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        estimated_cost = _sum_preview_costs(slots)
        return {
            "recipeInstanceId": str(instance_id),
            "characterDesignRevisionId": prepared["id"],
            "candidateCountPerSlot": candidate_count,
            "stage": payload.character_design_stage.value,
            "slots": slots,
            "estimatedCostMicros": estimated_cost,
            "inputHash": aggregate,
        }

    def run_character_design_validation(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
        *,
        parent_step_id: uuid.UUID,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        preview = self.preview_character_design_validation(instance_id, payload)
        if payload.expected_input_hash is None:
            raise ValueError("付费引用顺序验证必须先查看三个槽位的服务端输入预览")
        if payload.expected_input_hash != preview["inputHash"]:
            raise ValueError("验证引用、Prompt 或模型已变化，请重新查看费用与输入")
        self._accept_cost(payload, preview["estimatedCostMicros"])
        prepared = self._repository.prepare_character_design_validation(
            instance_id,
            idempotency_key=payload.idempotency_key,
        )
        drafts = tuple(
            CharacterDesignBatchDraft.model_validate(batch) for batch in prepared["batches"]
        )
        frozen_slots = workflow.preview_generation_batches(drafts)
        frozen_aggregate = hashlib.sha256(
            json.dumps(
                [item["inputHash"] for item in frozen_slots],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if frozen_aggregate != payload.expected_input_hash:
            raise ValueError("验证引用在任务冻结前发生变化，未创建任何图片批次，请重新预览")
        batches = workflow.create_generation_batches(
            drafts,
            parent_step_id=parent_step_id,
        )
        return {
            **prepared,
            "mode": "validation_only",
            "preservesApprovedSelection": True,
            "providerCallCount": 3,
            "status": "generating",
            "generationBatches": batches,
        }

    def preview_character_design_validation(
        self,
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        prepared = self._repository.preview_character_design_validation(
            instance_id,
            idempotency_key=payload.idempotency_key,
        )
        drafts = tuple(
            CharacterDesignBatchDraft.model_validate(batch) for batch in prepared["batches"]
        )
        slots = workflow.preview_generation_batches(drafts)
        aggregate = hashlib.sha256(
            json.dumps(
                [item["inputHash"] for item in slots],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "mode": "validation_only",
            "recipeInstanceId": str(instance_id),
            "baseCharacterDesignRevisionId": prepared["id"],
            "characterDesignRevisionId": prepared["id"],
            "candidateCountPerSlot": 1,
            "preservesApprovedSelection": True,
            "providerCallCount": 3,
            "slots": slots,
            "estimatedCostMicros": _sum_preview_costs(slots),
            "inputHash": aggregate,
        }

    def run_storyboard(
        self,
        instance_id: uuid.UUID,
        payload: StoryboardRecipeRunRequest,
    ) -> dict[str, Any]:
        workflow = self._require_story_workflow()
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["phase"] != RecipePhaseKey.STORYBOARD.value:
            raise ValueError("角色设计全部人工批准后才能生成分镜")
        progress = dict(instance.get("progress") or {})
        if not progress.get("characterDesignApproved", True):
            raise ValueError("三个角色设计槽位尚未全部人工批准")
        if int(progress.get("shotCount") or 0) > 0:
            raise ValueError("当前分镜已经生成，请先完成审核或创建明确的新版本")
        reference_asset_ids = tuple(payload.reference_asset_ids)
        if payload.creation_mode is StoryboardCreationMode.FROM_CHARACTERS:
            self._repository.validate_storyboard_character_references(
                instance_id,
                reference_asset_ids,
            )
        self._accept_cost(payload, self._director_call_cost_micros)
        durations = split_editorial_shot_durations(int(instance["targetDurationSeconds"]))
        storyboard = workflow.create_storyboard(
            uuid.UUID(str(instance["projectId"])),
            source_story_revision_id=payload.source_story_revision_id,
            exact_durations=durations,
            healing_recipe=True,
            idempotency_key=payload.idempotency_key,
            creation_mode=payload.creation_mode.value,
            reference_asset_ids=reference_asset_ids,
            instruction=payload.instruction,
        )
        return self._repository.materialize_storyboard(instance_id, storyboard)

    def run_anchor(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        if self._shot_workflow is None:
            raise RuntimeError("视觉锚点工作流未配置")
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["stage"] != RecipeStage.ANCHORS.value:
            raise ValueError("当前阶段不能生成视觉锚点")
        if not instance.get("progress", {}).get("storyboardApproved", True):
            raise ValueError("分镜尚未人工批准")
        self._repository.validate_anchor_prompt_readiness(instance_id, shot_id)
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[instance["qualityTier"]]
        self._accept_cost(
            payload,
            _multiply_cost(self._image_call_cost_micros, tier.anchor_candidate_count),
        )
        results = [
            self._shot_workflow.generate_anchor(
                shot_id,
                allow_paid_generation=True,
                regenerate=index > 0 or payload.reason is not None,
                reason=payload.reason,
                expected_input_hash=payload.expected_input_hash,
                request_idempotency_key=f"{payload.idempotency_key}:anchor:{index + 1}",
            )
            for index in range(tier.anchor_candidate_count)
        ]
        return {"shotId": str(shot_id), "status": "awaiting_review", "candidates": results}

    def run_video(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        if self._shot_workflow is None:
            raise RuntimeError("逐镜视频工作流未配置")
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["stage"] != RecipeStage.VIDEO.value:
            raise ValueError("全部视觉锚点人工批准后才能生成视频")
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[instance["qualityTier"]]
        self._accept_cost(
            payload,
            _multiply_cost(self._video_call_cost_micros, tier.video_candidate_count),
        )
        results = [
            self._shot_workflow.generate_video(
                shot_id,
                allow_paid_generation=True,
                regenerate=index > 0 or payload.reason is not None,
                reason=payload.reason,
                expected_input_hash=payload.expected_input_hash,
                request_idempotency_key=f"{payload.idempotency_key}:video:{index + 1}",
            )
            for index in range(tier.video_candidate_count)
        ]
        return {"shotId": str(shot_id), "status": "submitted", "candidates": results}

    def run_sequence(
        self,
        instance_id: uuid.UUID,
        payload: RecipeSequenceRunRequest,
    ) -> dict[str, Any]:
        if self._sequence_workflow is None:
            raise RuntimeError("最终音画工作流未配置")
        instance = _recipe_projection(self._repository.get_instance(instance_id))
        if instance["stage"] != RecipeStage.SEQUENCE.value:
            raise ValueError("全部视频镜头人工批准后才能合成最终音画")
        self._accept_cost(payload, 0)
        sequence = self._sequence_workflow.build_project_sequence(
            uuid.UUID(str(instance["projectId"])),
            transitions={item.after_shot_id: item.transition for item in payload.transitions},
            intro_transition=payload.intro_transition,
            outro_transition=payload.outro_transition,
            request_idempotency_key=payload.idempotency_key,
        )
        return {
            "id": str(sequence.id),
            "projectId": str(sequence.project_id),
            "revision": sequence.revision,
            "status": sequence.status.value,
            "durationMs": sequence.plan.duration_ms,
            "renderedAssetId": (
                None if sequence.rendered_asset_id is None else str(sequence.rendered_asset_id)
            ),
        }

    def compile_group(self, group_id: uuid.UUID) -> dict[str, Any]:
        group = self._repository.get_group(group_id)
        instance_id = group.get("recipeInstanceId")
        if instance_id is None:
            raise ValueError("该分组未绑定可执行的一人一猫配方")
        instance = self.get_instance(uuid.UUID(str(instance_id)))
        return {
            "groupId": str(group_id),
            "recipeInstanceId": str(instance_id),
            "projectId": instance["projectId"],
            "phase": instance["phase"],
            "primaryAction": instance["primaryAction"],
            "blocker": instance["currentBlocker"],
            "estimatedCostMicros": instance["estimatedCostMicros"],
            "costEstimateStatus": instance["costEstimateStatus"],
            "costEstimateLabel": instance["costEstimateLabel"],
            "stopsAtReviewGate": instance["phase"] != RecipePhaseKey.COMPLETE.value,
            "reviewStages": instance["reviewStages"],
        }

    def run_group(
        self,
        group_id: uuid.UUID,
        payload: CanvasGroupRunRequest,
        *,
        parent_step_id: uuid.UUID,
    ) -> dict[str, Any]:
        group = self._repository.get_group(group_id)
        if group.get("lifecycleStatus") != "active":
            raise ValueError("已解组或归档的分组不能执行")
        instance_id_value = group.get("recipeInstanceId")
        if instance_id_value is None:
            raise ValueError("该分组未绑定可执行的一人一猫配方")
        instance_id = uuid.UUID(str(instance_id_value))
        instance = self.get_instance(instance_id)
        self._accept_cost(payload, instance["estimatedCostMicros"])
        phase = RecipePhaseKey(instance["phase"])

        if phase is RecipePhaseKey.COMPLETE:
            return self._group_stop(group_id, instance, "complete")
        if phase is RecipePhaseKey.CREATIVE:
            brief = instance.get("creativeBrief") or {}
            if int(brief.get("revision") or 0) >= 2:
                return self._group_stop(group_id, instance, "awaiting_review")
            result = self.run_creative_brief(instance_id, payload)
        elif phase is RecipePhaseKey.STORY:
            story_workflow = dict(instance.get("storyWorkflow") or {})
            story_status = str(story_workflow.get("status") or "generate_candidates")
            if story_status in {"select_story", "complete"}:
                return self._group_stop(group_id, instance, "awaiting_review")
            if story_status in {"select_event", "expand_script", "approve_script"}:
                return self._group_stop(group_id, instance, "awaiting_review")
            result = self.run_story(instance_id, payload)
        elif phase is RecipePhaseKey.CHARACTER_DESIGN:
            character_design = instance.get("characterDesign") or {}
            if character_design.get("status") in {"generating", "awaiting_review"}:
                return self._group_stop(group_id, instance, "awaiting_review")
            result = self.run_character_design(
                instance_id,
                payload,
                parent_step_id=parent_step_id,
            )
        elif phase is RecipePhaseKey.STORYBOARD:
            if int(instance.get("progress", {}).get("shotCount") or 0) > 0:
                return self._group_stop(group_id, instance, "awaiting_review")
            source_story_revision_id = _approved_story_revision_id(instance)
            result = self.run_storyboard(
                instance_id,
                StoryboardRecipeRunRequest(
                    idempotencyKey=payload.idempotency_key,
                    acceptEstimatedCostMicros=payload.accept_estimated_cost_micros,
                    reason=payload.reason,
                    creationMode=StoryboardCreationMode.FROM_STORY,
                    sourceStoryRevisionId=source_story_revision_id,
                ),
            )
        elif phase is RecipePhaseKey.RENDER:
            result = self._run_next_render_step(instance_id, instance, payload)
        else:
            if instance.get("sequenceCandidate") is not None:
                return self._group_stop(group_id, instance, "awaiting_review")
            result = self.run_sequence(
                instance_id,
                RecipeSequenceRunRequest(
                    idempotencyKey=payload.idempotency_key,
                    acceptEstimatedCostMicros=payload.accept_estimated_cost_micros,
                    reason=payload.reason,
                    transitions=[],
                ),
            )
        return {
            "groupId": str(group_id),
            "recipeInstanceId": str(instance_id),
            "executedPhase": phase.value,
            "status": "awaiting_review",
            "stoppedAtReviewGate": True,
            "result": result,
        }

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.save_group_template(group_id)

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        return self._repository.ungroup(
            group_id,
            expected_revision=expected_revision,
        )

    def convert_group_to_shots(self, group_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.convert_to_shot_groups(group_id)

    def group_download_manifest(self, group_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.group_download_assets(group_id)

    def build_group_download(self, group_id: uuid.UUID) -> tuple[bytes, str]:
        if self._asset_root is None:
            raise RuntimeError("批量下载未配置资产根目录")
        manifest = self._repository.group_download_assets(group_id)
        missing: list[dict[str, str]] = []
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in manifest.get("assets", []):
                storage_key = str(item.get("storageKey") or "")
                asset_id = str(item.get("id") or "unknown")
                if not storage_key:
                    missing.append({"assetId": asset_id, "reason": "资产没有存储键"})
                    continue
                source = (self._asset_root / Path(storage_key)).resolve()
                if not source.is_relative_to(self._asset_root):
                    missing.append({"assetId": asset_id, "reason": "资产路径越出允许目录"})
                    continue
                if not source.is_file():
                    missing.append({"assetId": asset_id, "reason": "资产文件不存在"})
                    continue
                role = str(item.get("role") or "asset").replace("/", "-")
                archive.write(source, f"media/{role}/{asset_id}{source.suffix.lower()}")
            manifest_document = {**manifest, "missing": missing}
            archive.writestr(
                "manifest.json",
                json.dumps(manifest_document, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "人工审核清单.md",
                "# 一人一猫成片审核清单\n\n"
                "- [ ] 创意简报已批准\n"
                "- [ ] 故事与 EpisodeRules 已批准\n"
                "- [ ] 儿童、猫咪、同框比例角色图已批准\n"
                "- [ ] 分镜已批准\n"
                "- [ ] 每镜锚点与视频已批准\n"
                "- [ ] 原生音轨与最终技术 QC 已通过\n"
                "- [ ] 完整播放并批准最终成片\n\n"
                f"缺失或不可读取文件：{len(missing)} 个。详情见 manifest.json。\n",
            )
        title = str(manifest.get("title") or "one-child-one-cat")
        safe_title = "".join(character for character in title if character not in '<>:"/\\|?*')
        return buffer.getvalue(), f"{safe_title or 'one-child-one-cat'}-assets.zip"

    def _run_next_render_step(
        self,
        instance_id: uuid.UUID,
        instance: dict[str, Any],
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        shots = list(instance.get("shots") or [])
        for shot in shots:
            if shot.get("selectedAnchorAssetId"):
                continue
            candidates = [
                item
                for item in shot.get("anchorCandidates") or []
                if item.get("status") not in {"rejected", "stale", "failed"}
            ]
            if candidates:
                return self._group_stop(
                    uuid.UUID(str(instance["groupId"])),
                    instance,
                    "awaiting_review",
                )
            return self.run_anchor(
                instance_id,
                uuid.UUID(str(shot["shotId"])),
                payload,
            )
        for shot in shots:
            if shot.get("selectedVideoAssetId"):
                continue
            candidates = [
                item
                for item in shot.get("videoCandidates") or []
                if item.get("status") not in {"rejected", "stale", "failed"}
            ]
            if candidates:
                return self._group_stop(
                    uuid.UUID(str(instance["groupId"])),
                    instance,
                    "awaiting_review",
                )
            return self.run_video(
                instance_id,
                uuid.UUID(str(shot["shotId"])),
                payload,
            )
        return self._group_stop(
            uuid.UUID(str(instance["groupId"])),
            instance,
            "awaiting_review",
        )

    @staticmethod
    def _group_stop(
        group_id: uuid.UUID,
        instance: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        return {
            "groupId": str(group_id),
            "recipeInstanceId": str(instance["id"]),
            "phase": instance["phase"],
            "status": status,
            "stoppedAtReviewGate": status == "awaiting_review",
            "blocker": instance.get("currentBlocker"),
            "primaryAction": instance.get("primaryAction"),
        }

    def _require_story_workflow(self) -> StoryRecipeWorkflow:
        if self._story_workflow is None:
            raise RuntimeError("故事工作流未配置")
        return self._story_workflow

    def _project_instance(self, source: dict[str, Any]) -> dict[str, Any]:
        document = _recipe_projection(source)
        tier = HEALING_CHILD_CAT_RECIPE.quality_tiers[document["qualityTier"]]
        if document["phase"] == RecipePhaseKey.STORY.value:
            story_status = str(
                (document.get("storyWorkflow") or {}).get("status") or "generate_candidates"
            )
            estimate = (
                self._director_call_cost_micros
                if story_status in {"generate_candidates", "generate_events"}
                else _multiply_cost(self._director_call_cost_micros, 2)
                if story_status == "expand_script"
                else 0
            )
        elif document["phase"] in {
            RecipePhaseKey.CREATIVE.value,
            RecipePhaseKey.STORYBOARD.value,
        }:
            estimate = self._director_call_cost_micros
        elif document["phase"] == RecipePhaseKey.CHARACTER_DESIGN.value:
            estimate = _multiply_cost(
                self._image_call_cost_micros,
                tier.character_design_candidate_count * 3,
            )
        elif document["stage"] == RecipeStage.ANCHORS.value:
            estimate = _multiply_cost(
                self._image_call_cost_micros,
                tier.anchor_candidate_count,
            )
        elif document["stage"] == RecipeStage.VIDEO.value:
            estimate = _multiply_cost(
                self._video_call_cost_micros,
                tier.video_candidate_count,
            )
        else:
            estimate = 0
        estimate_status = "unmetered_paid" if estimate is None else "metered"
        return {
            **document,
            "estimatedCostMicros": estimate,
            "storyGenerationEstimatedCostMicros": self._director_call_cost_micros,
            "costEstimateStatus": estimate_status,
            "costEstimateLabel": (
                "付费调用·暂未计量" if estimate is None else f"预计费用 ¥{estimate / 1_000_000:.3f}"
            ),
        }

    def _validate_enqueued_operation(
        self,
        instance: dict[str, Any],
        *,
        instance_id: uuid.UUID,
        operation_key: str,
        payload: PaidRecipeRunRequest,
        shot_id: uuid.UUID | None,
    ) -> None:
        if instance.get("lifecycleStatus") != "active":
            raise ValueError("已归档的配方不能提交新任务")
        required_phase = {
            "recipe:creative": RecipePhaseKey.CREATIVE.value,
            "recipe:story": RecipePhaseKey.STORY.value,
            "recipe:story_events": RecipePhaseKey.STORY.value,
            "recipe:story_script": RecipePhaseKey.STORY.value,
            "recipe:character_design": RecipePhaseKey.CHARACTER_DESIGN.value,
            "recipe:storyboard": RecipePhaseKey.STORYBOARD.value,
            "recipe:sequence": RecipePhaseKey.EXPORT.value,
        }.get(operation_key)
        required_stage = {
            "recipe:anchor": RecipeStage.ANCHORS.value,
            "recipe:video": RecipeStage.VIDEO.value,
        }.get(operation_key)
        if required_phase is not None and instance["phase"] != required_phase:
            raise ValueError(f"当前阶段不能执行 {operation_key}")
        if required_stage is not None and instance["stage"] != required_stage:
            raise ValueError(f"当前阶段不能执行 {operation_key}")
        if operation_key in {"recipe:anchor", "recipe:video"} and shot_id is None:
            raise ValueError("逐镜任务必须指定镜头")
        if (
            operation_key == "canvas-group:run"
            and instance["phase"] == RecipePhaseKey.COMPLETE.value
        ):
            raise ValueError("已完成的分组没有可执行阶段")
        estimated_cost = instance["estimatedCostMicros"]
        if operation_key in {
            "recipe:creative",
            "recipe:story",
            "recipe:story_events",
        }:
            estimated_cost = self._director_call_cost_micros
        elif operation_key == "recipe:story_script":
            estimated_cost = _multiply_cost(self._director_call_cost_micros, 2)
        elif operation_key == "recipe:character_design":
            estimated_cost = self.preview_character_design(
                instance_id,
                payload,
            )["estimatedCostMicros"]
        elif operation_key == "recipe:character_design_validation":
            estimated_cost = self.preview_character_design_validation(
                instance_id,
                payload,
            )["estimatedCostMicros"]
        self._accept_cost(payload, estimated_cost)

    @staticmethod
    def _accept_cost(
        payload: PaidRecipeRunRequest,
        estimated_cost_micros: int | None,
    ) -> None:
        if estimated_cost_micros is None:
            if payload.accept_estimated_cost_micros != 0:
                raise ValueError(
                    "该 Provider 调用属于付费但暂未计量；"
                    "请以 0 作为未计量费用确认值，实际费用以供应商账单为准"
                )
            return
        if payload.accept_estimated_cost_micros != estimated_cost_micros:
            raise ValueError(
                "费用预估已变化："
                f"当前 {estimated_cost_micros}，提交 {payload.accept_estimated_cost_micros}"
            )


def _multiply_cost(unit_cost_micros: int | None, count: int) -> int | None:
    return None if unit_cost_micros is None else unit_cost_micros * count


def _sum_preview_costs(previews: Sequence[dict[str, Any]]) -> int | None:
    costs = [item.get("estimatedCostMicros") for item in previews]
    if any(cost is None for cost in costs):
        return None
    return sum(int(cost) for cost in costs)


def _recipe_projection(source: dict[str, Any]) -> dict[str, Any]:
    document = dict(source)
    progress = dict(document.get("progress") or {})
    shot_count = int(progress.get("shotCount") or 0)
    approved_anchors = int(progress.get("approvedAnchorCount") or 0)
    required_anchors = int(progress.get("requiredAnchorCount", shot_count) or 0)
    approved_videos = int(progress.get("approvedVideoCount") or 0)
    creative_approved = bool(progress.get("creativeApproved", True))
    creative_completed = bool(progress.get("creativeCompleted", True))
    story_approved = bool(progress.get("storyApproved"))
    character_design_approved = bool(progress.get("characterDesignApproved", True))
    storyboard_approved = bool(progress.get("storyboardApproved", shot_count > 0))
    storyboard_structure_approved = bool(progress.get("storyboardStructureApproved"))
    generation_plan_approved = bool(progress.get("generationPlanApproved"))
    story_candidates = list(document.get("storyCandidates") or [])
    full_text_story_candidates_exist = any(
        candidate.get("sourceEventCandidateId") is None
        or candidate.get("contractKind") == "creative_text"
        for candidate in story_candidates
        if isinstance(candidate, dict)
    )
    story_events_exist = bool(document.get("storyEvents"))
    source_story_workflow = dict(document.get("storyWorkflow") or {})
    source_story_status = str(source_story_workflow.get("status") or "")
    if story_approved:
        story_workflow = {
            "currentStep": 2,
            "totalSteps": 2,
            "status": "complete",
        }
    elif full_text_story_candidates_exist:
        story_workflow = {
            "currentStep": 2,
            "totalSteps": 2,
            "status": "select_story",
        }
    elif story_events_exist and source_story_status in {
        "generate_events",
        "select_event",
        "expand_script",
        "approve_script",
    }:
        story_workflow = source_story_workflow
    else:
        story_workflow = {
            "currentStep": 1,
            "totalSteps": 2,
            "status": "generate_candidates",
        }
    story_workflow_status = str(story_workflow["status"])
    document["storyWorkflow"] = story_workflow
    sequence_ready = bool(progress.get("sequenceReady"))
    final_approved = bool(progress.get("finalApproved"))

    if final_approved:
        stage = RecipeStage.COMPLETE
        phase = RecipePhaseKey.COMPLETE
        blocker = None
        primary_action = "导出最终成片"
    elif sequence_ready or (shot_count > 0 and approved_videos >= shot_count):
        stage = RecipeStage.SEQUENCE
        phase = RecipePhaseKey.EXPORT
        blocker = "最终音画尚未人工批准"
        primary_action = "审核最终成片" if sequence_ready else "合成最终成片"
    elif storyboard_approved and shot_count > 0 and approved_anchors >= required_anchors:
        stage = RecipeStage.VIDEO
        phase = RecipePhaseKey.RENDER
        blocker = "有视频镜头待生成或审核"
        primary_action = "生成下一镜视频"
    elif storyboard_approved and shot_count > 0:
        stage = RecipeStage.ANCHORS
        phase = RecipePhaseKey.RENDER
        blocker = "有视觉锚点待生成或审核"
        primary_action = "生成下一镜视觉锚点"
    elif story_approved and character_design_approved:
        stage = RecipeStage.STORYBOARD
        phase = RecipePhaseKey.STORYBOARD
        if not document.get("editorialShots"):
            blocker = "导演分镜尚未生成"
            primary_action = "生成导演分镜"
        elif not storyboard_structure_approved:
            blocker = "导演分镜结构等待人工批准"
            primary_action = "批准分镜结构"
        elif not generation_plan_approved:
            blocker = "Agent 生成编排等待人工批准"
            primary_action = "审核生成编排"
        elif not storyboard_approved:
            blocker = "场景资产、Scene Look 或 Prompt 尚未锁定"
            primary_action = "完成生产分镜包"
        else:
            blocker = None
            primary_action = "生成视觉锚点"
    elif story_approved:
        stage = RecipeStage.CONCEPT
        phase = RecipePhaseKey.CHARACTER_DESIGN
        blocker = "儿童、猫咪与同框比例角色图片尚未全部批准"
        primary_action = "审核角色设计" if document.get("characterDesign") else "生成角色设计"
    elif creative_approved:
        stage = RecipeStage.CONCEPT
        phase = RecipePhaseKey.STORY
        if story_workflow_status == "select_story":
            blocker = "完整故事候选等待人工选择"
            primary_action = "选择为当前剧情"
        elif story_events_exist:
            blocker = "历史事件流程仅供查看；请选择已有完整剧情或重新生成候选"
            primary_action = "查看历史剧情数据"
        else:
            blocker = "完整故事候选尚未生成"
            primary_action = "生成完整故事候选"
    else:
        stage = RecipeStage.CONCEPT
        phase = RecipePhaseKey.CREATIVE
        blocker = "创意简报尚未人工批准"
        primary_action = "审核创意简报" if creative_completed else "补全创意输入"

    target_duration = int(document["targetDurationSeconds"])
    document.update(
        {
            "stage": stage.value,
            "phase": phase.value,
            "shotDurations": list(split_shot_durations(target_duration)),
            "currentBlocker": blocker,
            "primaryAction": primary_action,
            "reviewStages": [
                {"key": "creative", "complete": creative_approved},
                {"key": "story", "complete": story_approved},
                {"key": "character_design", "complete": character_design_approved},
                {"key": "storyboard", "complete": storyboard_approved},
                {
                    "key": "render",
                    "complete": (
                        shot_count > 0
                        and approved_anchors >= required_anchors
                        and approved_videos >= shot_count
                    ),
                },
                {"key": "export", "complete": final_approved},
            ],
        }
    )
    return document


def _approved_story_revision_id(instance: dict[str, Any]) -> uuid.UUID:
    approved = [
        candidate
        for candidate in instance.get("storyCandidates") or []
        if isinstance(candidate, dict) and candidate.get("status") == "approved"
    ]
    if len(approved) != 1 or not approved[0].get("id"):
        raise ValueError("生成分镜必须明确选择唯一的当前批准剧情脚本")
    return uuid.UUID(str(approved[0]["id"]))


@dataclass(frozen=True)
class EpisodeVisualConstraints:
    canon_profile_id: str
    positive: tuple[str, ...]
    negative: tuple[str, ...]


def _episode_visual_constraints(instance: dict[str, Any]) -> EpisodeVisualConstraints:
    canon_profile_id = str(instance.get("canonProfileId") or "")
    profile = instance.get("visualProfile")
    if isinstance(profile, dict) and profile:
        source_profile_id = str(profile.get("sourceProfileId") or "")
        if source_profile_id != canon_profile_id:
            raise ValueError(
                "本集视觉档案与配方 Canon 不一致："
                f"{source_profile_id or 'missing'} != {canon_profile_id or 'missing'}"
            )
        positive = tuple(
            str(value).strip() for value in profile.get("stylePositive") or () if str(value).strip()
        )
        negative = tuple(
            str(value).strip() for value in profile.get("styleNegative") or () if str(value).strip()
        )
        if not positive:
            raise ValueError("本集视觉档案缺少正向画风约束")
        if canon_profile_id == CANON_V4_PROFILE_ID:
            positive = tuple(dict.fromkeys((*positive, *CANON_V4_STYLE_POSITIVE)))
            negative = tuple(dict.fromkeys((*negative, *CANON_V4_STYLE_NEGATIVE)))
        elif canon_profile_id == CANON_V3_PROFILE_ID:
            positive = tuple(dict.fromkeys((*positive, *CANON_V3_STYLE_POSITIVE)))
            negative = tuple(dict.fromkeys((*negative, *CANON_V3_STYLE_NEGATIVE)))
        return EpisodeVisualConstraints(canon_profile_id, positive, negative)
    if canon_profile_id == CANON_V4_PROFILE_ID:
        return EpisodeVisualConstraints(
            canon_profile_id,
            CANON_V4_STYLE_POSITIVE,
            CANON_V4_STYLE_NEGATIVE,
        )
    if canon_profile_id == CANON_V3_PROFILE_ID:
        return EpisodeVisualConstraints(
            canon_profile_id,
            CANON_V3_STYLE_POSITIVE,
            CANON_V3_STYLE_NEGATIVE,
        )
    if canon_profile_id == CANON_V2_PROFILE_ID:
        return EpisodeVisualConstraints(
            canon_profile_id,
            CANON_V2_STYLE_POSITIVE,
            CANON_V2_STYLE_NEGATIVE,
        )
    raise ValueError(f"不支持的 Canon 配置：{canon_profile_id or 'missing'}")


def _narrative_visual_instruction(instance: dict[str, Any]) -> str:
    constraints = _episode_visual_constraints(instance)
    return (
        "叙事阶段只把项目锁定画风作为情绪与可视化约束，不提交或改写视觉参考图；"
        f"画风正向约束：{'、'.join(constraints.positive)}；"
        f"排除：{'、'.join(constraints.negative)}。"
    )


def _suggest_episode_rules(theme: str, instance: dict[str, Any]) -> EpisodeRules:
    indoor_markers = ("室内", "屋", "厨房", "窗", "睡前", "床", "餐桌")
    environment = "indoor" if any(marker in theme for marker in indoor_markers) else "outdoor"
    visual = _episode_visual_constraints(instance)
    return EpisodeRules(
        personWardrobe=(
            "服装由当前故事与本集儿童设计确定；不得改变脸型、五官、8–9 岁年龄感、"
            "深棕黑色齐下颌短发、发际线或儿童身体比例；造型一经批准须在本集连续镜头保持一致"
        ),
        timeWeather="依据主题确定整集时间推进与天气基调，逐场细节由场景连续性规则锁定",
        mainScene="以批准故事的 scenes 为准；只有叙事必要时换场",
        environment=environment,
        coreProps=[],
        catBehaviorMode=CatBehaviorMode.NATURAL,
        soundPlan=SoundPlan(
            ambient=["与场景一致的轻柔原生环境声"],
            foley=["关键动作的克制拟音"],
            musicMood="轻柔、留白、不抢动作的治愈配乐",
            dialoguePolicy="none",
        ),
        stylePositive=list(visual.positive),
        styleExcluded=list(visual.negative),
        canonProfileId=visual.canon_profile_id,
    )


def _task_kind(operation_key: str, instance: dict[str, Any]) -> StepKind:
    if operation_key in {
        "recipe:character_design",
        "recipe:character_design_validation",
        "recipe:anchor",
    }:
        return StepKind.IMAGE
    if operation_key in {"recipe:video", "recipe:sequence"}:
        return StepKind.VIDEO
    if operation_key == "canvas-group:run":
        phase = str(instance["phase"])
        if phase == RecipePhaseKey.CHARACTER_DESIGN.value:
            return StepKind.IMAGE
        if phase in {RecipePhaseKey.RENDER.value, RecipePhaseKey.EXPORT.value}:
            return StepKind.VIDEO
    return StepKind.DIRECTOR


def _task_canvas_node_id(project_id: uuid.UUID, operation_key: str) -> uuid.UUID:
    semantic_key = {
        "recipe:creative": "creative-brief-approval",
        "recipe:story": "story-planner",
        "recipe:story_events": "story-planner",
        "recipe:story_script": "story-script-expander",
        "recipe:character_design": "character-design-approval",
        "recipe:character_design_validation": "character-design-approval",
        "recipe:storyboard": "storyboard-director",
        "recipe:anchor": "recipe-anchor-stage",
        "recipe:video": "recipe-video-stage",
        "recipe:sequence": "recipe-sequence",
        "canvas-group:run": "story-planner",
    }[operation_key]
    return uuid.uuid5(project_id, semantic_key)


def _task_result_summary(operation_key: str, result: dict[str, Any]) -> dict[str, object]:
    candidates = result.get("candidates") or result.get("generationBatches") or []
    summary: dict[str, object] = {
        "operationKey": operation_key,
        "status": str(result.get("status") or "awaiting_review"),
        "message": "任务已完成，等待人工审核",
        "candidateCount": len(candidates) if isinstance(candidates, list) else 0,
        "outputCount": len(candidates) if isinstance(candidates, list) else 1,
        "recipeInstanceId": result.get("recipeInstanceId"),
        "shotId": result.get("shotId"),
        "assetId": result.get("renderedAssetId"),
        "revisionId": result.get("revisionId")
        or (
            result.get("id")
            if operation_key in {"recipe:character_design", "recipe:character_design_validation"}
            else None
        ),
    }
    summary["reviewTargets"] = _direct_review_targets(operation_key, result)
    return summary


def _review_target(
    target_type: str,
    value: object,
    *,
    revision: object = None,
    target_hash: object = None,
) -> dict[str, object] | None:
    if value is None or value == "":
        return None
    target: dict[str, object] = {
        "targetType": target_type,
        "targetId": str(value),
    }
    if revision is not None and revision != "":
        target["targetRevision"] = int(str(revision))
    if target_hash is not None and target_hash != "":
        target["targetHash"] = str(target_hash)
    return target


def _direct_review_targets(
    operation_key: str,
    result: dict[str, Any],
) -> list[dict[str, object]]:
    if operation_key == "recipe:creative":
        target = _review_target(
            "creative_brief",
            result.get("id"),
            revision=result.get("revision"),
        )
        return [] if target is None else [target]
    if operation_key in {"recipe:story", "recipe:story_events", "recipe:story_script"}:
        candidates = result.get("candidates") or []
        if operation_key == "recipe:story_script":
            story = result.get("story")
            candidates = [story] if isinstance(story, dict) else []
        return [
            target
            for candidate in candidates
            if isinstance(candidate, dict)
            and (
                target := _review_target(
                    "story_revision",
                    candidate.get("id"),
                    revision=candidate.get("revision"),
                )
            )
            is not None
        ]
    if operation_key == "recipe:storyboard":
        target = _review_target(
            "storyboard_structure",
            result.get("storyboardRevisionId"),
            revision=result.get("storyboardRevision"),
            target_hash=result.get("structureHash"),
        )
        return [] if target is None else [target]
    if operation_key == "recipe:sequence":
        target = _review_target(
            "final_sequence",
            result.get("id"),
            revision=result.get("revision"),
        )
        return [] if target is None else [target]
    return []


def _child_review_targets(
    operation_key: str,
    child_steps: tuple[dict[str, Any], ...],
) -> list[dict[str, object]]:
    target_type = {
        "recipe:character_design": "character_design",
        "recipe:anchor": "anchor_asset",
        "recipe:video": "video_asset",
    }.get(operation_key)
    if target_type is None:
        return []
    targets: list[dict[str, object]] = []
    seen: set[str] = set()
    for child in child_steps:
        result_summary = child.get("resultSummary")
        if not isinstance(result_summary, dict):
            continue
        asset_id = result_summary.get("assetId")
        target = _review_target(
            target_type,
            asset_id,
            target_hash=result_summary.get("sha256"),
        )
        if target is None or str(target["targetId"]) in seen:
            continue
        seen.add(str(target["targetId"]))
        targets.append(target)
    return targets


def _result_child_step_ids(result: dict[str, Any]) -> tuple[uuid.UUID, ...]:
    """Collect durable child work created by a recipe operation without guessing asset IDs."""

    ordered: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()

    def add(value: object) -> None:
        if value is None or value == "":
            return
        try:
            step_id = uuid.UUID(str(value))
        except (TypeError, ValueError):
            return
        if step_id not in seen:
            seen.add(step_id)
            ordered.append(step_id)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            candidate_ids = value.get("candidateStepIds")
            if isinstance(candidate_ids, list):
                for item in candidate_ids:
                    add(item)
            if "stepId" in value:
                add(value["stepId"])
            for key in ("candidates", "candidateSteps", "generationBatches", "result"):
                nested = value.get(key)
                if nested is not None:
                    visit(nested)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return tuple(ordered)
