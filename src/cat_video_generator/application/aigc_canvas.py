"""Application service for auditable story strategy and typed canvas commands."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from typing import Any, Protocol, Sequence

from ..domain.aigc_canvas import (
    CanvasDiagnostic,
    CreativeStoryCandidateBatch,
    PromptRunDraft,
    StoryboardPlanOutput,
    StoryBrief,
    StoryCandidateOutput,
    StoryEventCandidateOutput,
    StoryScorecard,
    StoryStrategy,
    allocate_bounded_durations,
    character_design_generation_input,
    generation_input_hash,
    parse_llm_story_candidate_output,
    parse_llm_storyboard_output,
    storyboard_quality_diagnostics,
    validate_story_event_candidate,
    validate_story_inputs,
    validate_story_scene_plan,
)
from ..domain.production_recipes import (
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    CANON_V4_PROFILE_ID,
    CANON_V4_STYLE_NEGATIVE,
    CANON_V4_STYLE_POSITIVE,
)
from ..domain.universal_canvas import ProviderEditCapability
from .ports import CreativeDirectorResult, DirectorGateway, GatewayError


class CanvasRepository(Protocol):
    def create_child_cat_project(self, payload: Any) -> dict[str, Any]: ...

    def get_current_brief(self, project_id: uuid.UUID) -> tuple[uuid.UUID, Any]: ...

    def list_subjects(self, project_id: uuid.UUID) -> tuple[Any, ...]: ...

    def begin_generation_attempt(self, **values: object) -> tuple[dict[str, Any], bool]: ...

    def begin_prompt_run(self, **values: object) -> tuple[uuid.UUID, uuid.UUID]: ...

    def complete_prompt_run(self, prompt_id: uuid.UUID, **values: object) -> None: ...

    def save_story_candidate(self, **values: object) -> dict[str, Any]: ...

    def save_story_candidate_batch(
        self, **values: object
    ) -> tuple[dict[str, Any], ...]: ...

    def save_story_revision_edit(self, **values: object) -> dict[str, Any]: ...

    def get_story_candidates(
        self,
        *,
        project_id: uuid.UUID,
        candidate_ids: Sequence[uuid.UUID],
    ) -> tuple[dict[str, Any], ...]: ...

    def get_succeeded_story_candidate_batch(
        self, **values: object
    ) -> dict[str, object] | None: ...

    def save_story_event_candidate(self, **values: object) -> dict[str, Any]: ...

    def get_selected_story_event(self, recipe_instance_id: uuid.UUID) -> dict[str, Any]: ...

    def finish_generation_attempt(self, attempt_id: str, **values: object) -> None: ...

    def save_brief(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def create_subject(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def create_subject_revision(self, subject_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def create_subject_completion_run(
        self,
        project_id: uuid.UUID,
        payload: Any,
        *,
        provider: str,
        model: str,
    ) -> dict[str, Any]: ...

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, Any]: ...

    def apply_subject_completion(self, run_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]: ...

    def list_visual_presets(self) -> list[dict[str, Any]]: ...

    def apply_visual_preset(self, project_id: uuid.UUID, preset_key: str) -> dict[str, Any]: ...

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def update_episode_visual_profile(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def compile_storyboard_prompts(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]: ...

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]: ...

    def get_video_filmstrip(self, asset_id: uuid.UUID, *, frame_count: int) -> dict[str, Any]: ...

    def list_provider_capabilities(
        self, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]: ...

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]: ...

    def get_storyboard_context(
        self,
        project_id: uuid.UUID,
        *,
        source_story_revision_id: uuid.UUID | None = None,
    ) -> dict[str, Any]: ...

    def get_storyboard_reference_inputs(
        self,
        project_id: uuid.UUID,
        asset_ids: tuple[uuid.UUID, ...],
    ) -> dict[str, Any]: ...

    def save_storyboard_plan(self, project_id: uuid.UUID, **values: object) -> dict[str, Any]: ...

    def update_shot_beat(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def create_generation_attempt(self, payload: Any) -> dict[str, Any]: ...

    def retry_generation_attempt(self, attempt_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def review_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]: ...

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, Any]: ...

    def get_workspace_shell(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def get_script_workspace(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def get_production_flow(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def save_production_flow_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def get_video_workbench(self, project_id: uuid.UUID) -> dict[str, Any]: ...

    def replace_shot_beat_references(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def get_asset_generation_lineage(self, asset_id: uuid.UUID) -> dict[str, Any]: ...

    def create_generation_batches(
        self,
        payloads: Sequence[Any],
        *,
        parent_step_id: uuid.UUID,
    ) -> tuple[dict[str, Any], ...]: ...

    def create_video_edit_recipe(self, payload: Any) -> dict[str, Any]: ...

    def update_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def replace_video_edit_annotations(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]: ...

    def compile_video_edit_recipe(
        self, recipe_id: uuid.UUID, capability: ProviderEditCapability
    ) -> dict[str, Any]: ...

    def submit_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        payload: Any,
        *,
        image_provider: str,
        image_model: str,
    ) -> dict[str, Any]: ...

    def events(
        self, project_id: uuid.UUID, *, after_sequence: int = 0
    ) -> tuple[dict[str, Any], ...]: ...


class AigcCanvasService:
    """Coordinates paid story calls without hiding persistence boundaries."""

    def __init__(
        self,
        *,
        repository: CanvasRepository,
        director: DirectorGateway,
        provider_name: str,
        video_edit_capability: ProviderEditCapability | None = None,
    ) -> None:
        self._repository = repository
        self._director = director
        self._provider_name = provider_name
        self._image_model = getattr(director, "image_model", director.model)
        self._video_edit_capability = video_edit_capability or ProviderEditCapability(
            provider=provider_name,
            model=getattr(director, "video_model", director.model),
            supportsDirectAnnotations=False,
            maxDirectReferenceImages=1,
            supportsControlAnchors=True,
            imageCallCostMicros=0,
            videoCallCostMicros=0,
        )

    def create_child_cat_project(self, payload: Any) -> dict[str, Any]:
        return self._repository.create_child_cat_project(payload)

    def save_brief(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.save_brief(project_id, payload)

    def complete_creative_brief(
        self,
        project_id: uuid.UUID,
        *,
        theme: str,
        target_duration_seconds: int,
    ) -> dict[str, Any]:
        visual_profile = self._repository.get_episode_visual_profile(project_id)
        source_profile_id = str(visual_profile.get("sourceProfileId") or "").strip()
        style_positive = tuple(
            str(value).strip()
            for value in visual_profile.get("stylePositive") or ()
            if str(value).strip()
        )
        style_negative = tuple(
            str(value).strip()
            for value in visual_profile.get("styleNegative") or ()
            if str(value).strip()
        )
        if not source_profile_id:
            raise ValueError("当前本集视觉档案缺少 Canon 来源")
        if not style_positive:
            raise ValueError("当前本集视觉档案缺少正向画风约束")
        if source_profile_id == CANON_V4_PROFILE_ID:
            style_positive = tuple(
                dict.fromkeys((*style_positive, *CANON_V4_STYLE_POSITIVE))
            )
            style_negative = tuple(
                dict.fromkeys((*style_negative, *CANON_V4_STYLE_NEGATIVE))
            )
        elif source_profile_id == CANON_V3_PROFILE_ID:
            style_positive = tuple(
                dict.fromkeys((*style_positive, *CANON_V3_STYLE_POSITIVE))
            )
            style_negative = tuple(
                dict.fromkeys((*style_negative, *CANON_V3_STYLE_NEGATIVE))
            )
        visual_constraint = (
            "叙事阶段只把当前项目锁定画风作为情绪与可视化约束，不提交或改写画风参考图；"
            f"正向约束：{'、'.join(style_positive)}；"
            f"排除：{'、'.join(style_negative) or '无额外排除项'}。"
        )
        prompt = (
            "你是治愈系无对白竖屏短片的创意编辑。把用户的一句话主题补全为结构化创意简报。"
            "保持固定儿童、固定猫咪、单一低压力事件、无对白、原生环境声。"
            "constraints 必须明确季节天气、主要场景、核心事件、核心道具、猫咪行为模式和温暖收尾。\n"
            f"{visual_constraint}\n"
            f"用户主题：{theme}\n目标时长：{target_duration_seconds} 秒；画幅：9:16。"
        )
        result = self._director.generate_structured(
            prompt=prompt,
            schema=StoryBrief.model_json_schema(),
            output_name="HealingCreativeBrief",
        )
        proposed = StoryBrief.model_validate(result.payload)
        fixed_constraints = [
            *proposed.constraints,
            "固定儿童与固定猫咪身份",
            f"锁定视觉档案：{source_profile_id}",
            *style_positive,
            *(f"排除：{value}" for value in style_negative),
            "禁止对白",
            "原生环境声、动作声与轻音乐",
        ]
        brief = proposed.model_copy(
            update={
                "theme": theme,
                "aspect_ratio": "9:16",
                "target_duration_seconds": target_duration_seconds,
                "constraints": list(dict.fromkeys(fixed_constraints))[:30],
            }
        )
        return self._repository.save_brief(project_id, brief)

    def create_subject(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.create_subject(project_id, payload)

    def list_subjects(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        return [
            {
                "id": str(item.id),
                "projectId": str(project_id),
                "revisionId": str(item.revision_id),
                "revision": item.revision,
                "status": item.status,
                **item.draft.model_dump(mode="json", by_alias=True),
            }
            for item in self._repository.list_subjects(project_id)
        ]

    def create_subject_revision(self, subject_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.create_subject_revision(subject_id, payload)

    def create_subject_completion_run(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.create_subject_completion_run(
            project_id,
            payload,
            provider=self._provider_name,
            model=self._director.model,
        )

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_subject_completion_run(run_id)

    def apply_subject_completion(self, run_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.apply_subject_completion(run_id, payload)

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]:
        return self._repository.list_project_assets(project_id, media_kind=media_kind)

    def list_visual_presets(self) -> list[dict[str, Any]]:
        return self._repository.list_visual_presets()

    def apply_visual_preset(self, project_id: uuid.UUID, preset_key: str) -> dict[str, Any]:
        return self._repository.apply_visual_preset(project_id, preset_key)

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_episode_visual_profile(project_id)

    def update_episode_visual_profile(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.update_episode_visual_profile(
            project_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]:
        return self._repository.create_video_filmstrip_run(asset_id, frame_count=frame_count)

    def get_video_filmstrip(self, asset_id: uuid.UUID, *, frame_count: int) -> dict[str, Any]:
        return self._repository.get_video_filmstrip(asset_id, frame_count=frame_count)

    def list_provider_capabilities(self, *, media_kind: str | None = None) -> list[dict[str, Any]]:
        return self._repository.list_provider_capabilities(media_kind=media_kind)

    def run_story_event_strategies(
        self,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        brief_id, brief = self._repository.get_current_brief(project_id)
        subjects = self._repository.list_subjects(project_id)
        validate_story_inputs(brief, tuple(item.draft for item in subjects))
        subject_snapshot = [
            {
                "subjectId": str(item.id),
                "revisionId": str(item.revision_id),
                **item.draft.model_dump(mode="json", by_alias=True),
            }
            for item in subjects
        ]
        input_snapshot = {
            "briefId": str(brief_id),
            "brief": brief.model_dump(mode="json", by_alias=True),
            "subjects": subject_snapshot,
            "recipeInstanceId": str(recipe_instance_id),
            "creativeDirection": str(getattr(payload, "rewrite_instruction", "") or ""),
            "narrativeContract": {
                "candidateCount": 3,
                "requiredBeats": [
                    "childAction",
                    "catParticipation",
                    "smallChange",
                    "warmEnding",
                ],
                "dialogueAllowed": False,
            },
        }
        input_hash = _json_hash(input_snapshot)
        idempotency_key = (
            getattr(payload, "idempotency_key", None)
            or hashlib.sha256(
                f"{recipe_instance_id}:story-events:{input_hash}".encode()
            ).hexdigest()
        )
        attempt, created = self._repository.begin_generation_attempt(
            project_id=project_id,
            business_object_type="recipe_story_event_batch",
            business_object_id=recipe_instance_id,
            idempotency_key=idempotency_key,
            provider=self._provider_name,
            model=self._director.model,
            request=input_snapshot,
        )
        if not created:
            return attempt

        attempt_id = str(attempt["id"])
        batch_id = uuid.UUID(attempt_id)
        candidates: list[dict[str, Any]] = []
        try:
            for candidate_index, strategy in enumerate(
                (
                    StoryStrategy.RELATIONSHIP,
                    StoryStrategy.PROBLEM_SOLVING,
                    StoryStrategy.TWIST_HOOK,
                ),
                1,
            ):
                candidate, prompt_id = self._generate_event_candidate(
                    project_id=project_id,
                    recipe_instance_id=recipe_instance_id,
                    strategy=strategy,
                    input_snapshot=input_snapshot,
                )
                score, _critic_prompt_id = self._score_event_candidate(
                    project_id=project_id,
                    recipe_instance_id=recipe_instance_id,
                    strategy=strategy,
                    candidate=candidate,
                    input_snapshot=input_snapshot,
                    parent_prompt_id=prompt_id,
                )
                candidates.append(
                    self._repository.save_story_event_candidate(
                        project_id=project_id,
                        recipe_instance_id=recipe_instance_id,
                        brief_id=brief_id,
                        batch_id=batch_id,
                        candidate_index=candidate_index,
                        strategy=strategy,
                        candidate=candidate,
                        scorecard=score,
                        generation_prompt_id=prompt_id,
                    )
                )
        except GatewayError as exc:
            status = "submission_unknown" if exc.submission_unknown else "failed"
            self._repository.finish_generation_attempt(
                attempt_id,
                status=status,
                error={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
            )
            raise
        except Exception as exc:
            self._repository.finish_generation_attempt(
                attempt_id,
                status="failed",
                error={"code": "internal", "message": str(exc)},
            )
            raise
        self._repository.finish_generation_attempt(
            attempt_id,
            status="succeeded",
            response={
                "candidateIds": [str(item["id"]) for item in candidates],
                "candidateCount": len(candidates),
            },
        )
        return {"id": attempt_id, "status": "succeeded", "candidates": candidates}

    def expand_selected_story_event(
        self,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        event = self._repository.get_selected_story_event(recipe_instance_id)
        brief_id, brief = self._repository.get_current_brief(project_id)
        subjects = self._repository.list_subjects(project_id)
        validate_story_inputs(brief, tuple(item.draft for item in subjects))
        input_snapshot = {
            "briefId": str(brief_id),
            "brief": brief.model_dump(mode="json", by_alias=True),
            "selectedEvent": event,
            "subjects": [
                {
                    "subjectId": str(item.id),
                    "revisionId": str(item.revision_id),
                    **item.draft.model_dump(mode="json", by_alias=True),
                }
                for item in subjects
            ],
            "instruction": str(getattr(payload, "rewrite_instruction", "") or ""),
        }
        input_hash = _json_hash(input_snapshot)
        event_id = uuid.UUID(str(event["id"]))
        idempotency_key = (
            getattr(payload, "idempotency_key", None)
            or hashlib.sha256(f"{event_id}:story-script:{input_hash}".encode()).hexdigest()
        )
        attempt, created = self._repository.begin_generation_attempt(
            project_id=project_id,
            business_object_type="story_event_script",
            business_object_id=event_id,
            idempotency_key=idempotency_key,
            provider=self._provider_name,
            model=self._director.model,
            request=input_snapshot,
        )
        if not created:
            return attempt

        attempt_id = str(attempt["id"])
        try:
            script, prompt_id = self._generate_script_from_event(
                project_id=project_id,
                event_id=event_id,
                input_snapshot=input_snapshot,
            )
            score, critic_prompt_id = self._score_candidate(
                project_id=project_id,
                strategy=StoryStrategy.COMBINED,
                candidate=script,
                input_snapshot=input_snapshot,
                parent_prompt_id=prompt_id,
            )
            stored = self._repository.save_story_candidate(
                project_id=project_id,
                brief_id=brief_id,
                strategy=StoryStrategy.COMBINED,
                candidate=script,
                scorecard=score,
                subject_ids=tuple(item.id for item in subjects),
                subject_revision_ids=tuple(item.revision_id for item in subjects),
                candidate_prompt_id=prompt_id,
                critic_prompt_id=critic_prompt_id,
                source_event_candidate_id=event_id,
            )
        except GatewayError as exc:
            status = "submission_unknown" if exc.submission_unknown else "failed"
            self._repository.finish_generation_attempt(
                attempt_id,
                status=status,
                error={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
            )
            raise
        except Exception as exc:
            self._repository.finish_generation_attempt(
                attempt_id,
                status="failed",
                error={"code": "internal", "message": str(exc)},
            )
            raise
        self._repository.finish_generation_attempt(
            attempt_id,
            status="succeeded",
            response={"revisionId": str(stored["id"]), "sourceEventCandidateId": str(event_id)},
        )
        return {"id": attempt_id, "status": "succeeded", "story": stored}

    def run_story_strategies(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        brief_id, brief = self._repository.get_current_brief(project_id)
        subjects = self._repository.list_subjects(project_id)
        validate_story_inputs(brief, tuple(item.draft for item in subjects))
        subject_snapshot = [
            {
                "subjectId": str(item.id),
                "revisionId": str(item.revision_id),
                **item.draft.model_dump(mode="json", by_alias=True),
            }
            for item in subjects
        ]
        input_snapshot = {
            "briefId": str(brief_id),
            "brief": brief.model_dump(mode="json", by_alias=True),
            "subjects": subject_snapshot,
            "creativeDirection": str(getattr(payload, "rewrite_instruction", "") or ""),
        }
        input_hash = _json_hash(input_snapshot)
        idempotency_key = (
            payload.idempotency_key
            or hashlib.sha256(f"{project_id}:story-strategies:{input_hash}".encode()).hexdigest()
        )
        attempt, created = self._repository.begin_generation_attempt(
            project_id=project_id,
            business_object_type="project_story_strategy",
            business_object_id=project_id,
            idempotency_key=idempotency_key,
            provider=self._provider_name,
            model=self._director.model,
            request=input_snapshot,
        )
        if not created and attempt.get("status") == "succeeded":
            response = dict(attempt.get("response") or {})
            raw_candidate_ids = response.get("candidateIds")
            if not isinstance(raw_candidate_ids, list) or not raw_candidate_ids:
                raise ValueError("成功故事生成尝试缺少候选版本引用")
            try:
                candidate_ids = tuple(uuid.UUID(str(item)) for item in raw_candidate_ids)
            except (TypeError, ValueError) as exc:
                raise ValueError("成功故事生成尝试的候选版本引用无效") from exc
            candidates = list(
                self._repository.get_story_candidates(
                    project_id=project_id,
                    candidate_ids=candidate_ids,
                )
            )
            return {
                "id": str(attempt["id"]),
                "status": "succeeded",
                **response,
                "candidateIds": [str(item) for item in candidate_ids],
                "candidates": candidates,
            }
        try:
            recovered_prompt = self._repository.get_succeeded_story_candidate_batch(
                project_id=project_id,
                business_object_type="project_story_strategy",
                business_object_id=project_id,
                call_purpose="story_candidate_batch",
                input_hash=input_hash,
            )
            if recovered_prompt is not None:
                recovered_batch = CreativeStoryCandidateBatch.model_validate(
                    recovered_prompt["batch"]
                )
                recovered_diagnostics = [
                    CanvasDiagnostic.model_validate(item).model_dump(
                        mode="json", by_alias=True
                    )
                    for item in recovered_prompt["diagnostics"]  # type: ignore[union-attr]
                ]
                recovered_candidates = list(
                    self._repository.save_story_candidate_batch(
                        project_id=project_id,
                        brief_id=brief_id,
                        strategy=StoryStrategy.COMBINED,
                        candidates=tuple(recovered_batch.candidates),
                        subject_ids=tuple(item.id for item in subjects),
                        subject_revision_ids=tuple(item.revision_id for item in subjects),
                        candidate_prompt_id=recovered_prompt["promptId"],
                    )
                )
        except Exception as exc:
            self._finish_story_strategy_failure(
                attempt_id=str(attempt["id"]),
                prompt_id=None,
                prompt_succeeded=True,
                prompt_failure=None,
                status="failed",
                attempt_error={
                    "code": "story_candidate_recovery_failed",
                    "message": str(exc),
                    "exceptionType": type(exc).__name__,
                },
            )
            raise
        if recovered_prompt is not None:
            recovered_response = {
                "candidateIds": [str(item["id"]) for item in recovered_candidates],
                "candidateCount": len(recovered_candidates),
                "diagnostics": recovered_diagnostics,
            }
            self._repository.finish_generation_attempt(
                str(attempt["id"]),
                status="succeeded",
                response=recovered_response,
            )
            return {
                "id": str(attempt["id"]),
                "status": "succeeded",
                "candidates": recovered_candidates,
                **recovered_response,
            }
        if not created:
            return attempt

        attempt_id = str(attempt["id"])
        system_prompt = (
            "你是故事创意编辑。基于创意简报和全部叙事主体，尽量一次返回 3 个结构明显不同、"
            "内容完整且可继续人工编辑的故事候选。这里只创作故事正文，不要预先拆分 scenes、"
            "儿童动作、猫咪动作、换场或分镜。最小 JSON 格式为 "
            '{"candidates":[{"title":"标题","body":"完整故事正文","summary":"可选摘要"}]}。'
            "允许返回 1 至 5 个候选；不要添加评分或评审结论。"
        )
        user_prompt = (
            "创作输入："
            f"{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        candidates: list[dict[str, Any]] = []
        prompt_id: uuid.UUID | None = None
        prompt_succeeded = False
        creative_result: CreativeDirectorResult | None = None
        try:
            prompt_id, _step_id = self._repository.begin_prompt_run(
                project_id=project_id,
                draft=PromptRunDraft(
                    purpose="story_candidate_batch",
                    nodeId=uuid.uuid5(project_id, "story-planner:creative-batch"),
                    businessObjectType="project_story_strategy",
                    businessObjectId=project_id,
                    templateName="story.creative-batch.v1",
                    templateVersion="1.0.0",
                    systemPrompt=system_prompt,
                    userPrompt=user_prompt,
                    finalPrompt=final_prompt,
                    provider=self._provider_name,
                    model=self._director.model,
                    providerRequestSnapshot={
                        "outputName": "StoryCandidateBatch",
                        "responseContract": CreativeStoryCandidateBatch.model_json_schema(),
                    },
                    inputSnapshot=input_snapshot,
                ),
            )
            creative_result = self._director.generate_creative_text(
                prompt=final_prompt,
                output_name="StoryCandidateBatch",
            )
            parsed = parse_llm_story_candidate_output(creative_result.payload)
            structured_response = {
                "batch": parsed.batch.model_dump(mode="json"),
                "diagnostics": [
                    item.model_dump(mode="json", by_alias=True)
                    for item in parsed.diagnostics
                ],
            }
            self._repository.complete_prompt_run(
                prompt_id,
                status="succeeded",
                raw_response=creative_result.payload,
                structured_response=structured_response,
                provider_response_id=creative_result.response_id,
                output_hash=_json_hash(creative_result.payload),
            )
            prompt_succeeded = True
            candidates.extend(
                self._repository.save_story_candidate_batch(
                    project_id=project_id,
                    brief_id=brief_id,
                    strategy=StoryStrategy.COMBINED,
                    candidates=tuple(parsed.batch.candidates),
                    subject_ids=tuple(item.id for item in subjects),
                    subject_revision_ids=tuple(item.revision_id for item in subjects),
                    candidate_prompt_id=prompt_id,
                )
            )
        except GatewayError as exc:
            prompt_failure: dict[str, object] | None = None
            if prompt_id is not None and not prompt_succeeded:
                prompt_failure = {
                    "status": "failed",
                    "error": {
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                    },
                }
                if creative_result is not None:
                    prompt_failure.update(
                        raw_response=creative_result.payload,
                        provider_response_id=creative_result.response_id,
                        output_hash=_json_hash(creative_result.payload),
                    )
            status = "submission_unknown" if exc.submission_unknown else "failed"
            self._finish_story_strategy_failure(
                attempt_id=attempt_id,
                prompt_id=prompt_id,
                prompt_succeeded=prompt_succeeded,
                prompt_failure=prompt_failure,
                status=status,
                attempt_error={
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.retryable,
                },
            )
            raise
        except Exception as exc:
            prompt_failure = None
            if prompt_id is not None and not prompt_succeeded:
                prompt_failure = {
                    "status": "failed",
                    "error": {"code": "internal", "message": str(exc)},
                }
                if creative_result is not None:
                    prompt_failure.update(
                        raw_response=creative_result.payload,
                        provider_response_id=creative_result.response_id,
                        output_hash=_json_hash(creative_result.payload),
                    )
            self._finish_story_strategy_failure(
                attempt_id=attempt_id,
                prompt_id=prompt_id,
                prompt_succeeded=prompt_succeeded,
                prompt_failure=prompt_failure,
                status="failed",
                attempt_error={"code": "internal", "message": str(exc)},
            )
            raise
        diagnostics = [
            item.model_dump(mode="json", by_alias=True) for item in parsed.diagnostics
        ]
        response = {
            "candidateIds": [str(item["id"]) for item in candidates],
            "candidateCount": len(candidates),
            "diagnostics": diagnostics,
        }
        self._repository.finish_generation_attempt(
            attempt_id,
            status="succeeded",
            response=response,
        )
        return {
            "id": attempt_id,
            "status": "succeeded",
            "candidates": candidates,
            **response,
        }

    def _finish_story_strategy_failure(
        self,
        *,
        attempt_id: str,
        prompt_id: uuid.UUID | None,
        prompt_succeeded: bool,
        prompt_failure: dict[str, object] | None,
        status: str,
        attempt_error: dict[str, object],
    ) -> None:
        prompt_audit_error: Exception | None = None
        if prompt_id is not None and not prompt_succeeded and prompt_failure is not None:
            try:
                self._repository.complete_prompt_run(prompt_id, **prompt_failure)
            except Exception as exc:
                prompt_audit_error = exc
        recorded_error = dict(attempt_error)
        if prompt_audit_error is not None:
            recorded_error["promptAuditError"] = {
                "type": type(prompt_audit_error).__name__,
                "message": str(prompt_audit_error),
            }
        self._repository.finish_generation_attempt(
            attempt_id,
            status=status,
            error=recorded_error,
        )

    def _generate_event_candidate(
        self,
        *,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        strategy: StoryStrategy,
        input_snapshot: dict[str, Any],
    ) -> tuple[StoryEventCandidateOutput, uuid.UUID]:
        system_prompt = (
            "你是一人一猫治愈短片的事件策划。这里只输出一个可供人选择的事件方向，"
            "不要扩写文学化完整剧情。事件必须分别说明儿童主动行动、猫咪参与、小变化和"
            "温暖收尾，并判断目标时长内是否可拍。固定儿童、固定猫咪和固定画风只作为"
            "不可修改的IP约束；禁止对白，禁止把猫咪生成人形肢体。8至15秒只能使用一个场景，"
            "更长视频只有在叙事必要时才能换场，每次换场必须说明目的。"
        )
        user_prompt = (
            f"事件策略：{strategy.value}\n"
            f"输入约束：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_event_candidate",
                nodeId=uuid.uuid5(project_id, f"story-event-planner:{strategy.value}"),
                businessObjectType="recipe_story_event_batch",
                businessObjectId=recipe_instance_id,
                templateName=f"story.event.{strategy.value}.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "StoryEventCandidateOutput",
                    "schema": StoryEventCandidateOutput.model_json_schema(),
                },
                inputSnapshot=input_snapshot,
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryEventCandidateOutput.model_json_schema(),
                output_name="StoryEventCandidateOutput",
            )
            candidate = StoryEventCandidateOutput.model_validate(result.payload)
            validate_story_event_candidate(
                candidate,
                target_duration_seconds=int(input_snapshot["brief"]["targetDurationSeconds"]),
            )
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=candidate.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return candidate, prompt_id

    def _score_event_candidate(
        self,
        *,
        project_id: uuid.UUID,
        recipe_instance_id: uuid.UUID,
        strategy: StoryStrategy,
        candidate: StoryEventCandidateOutput,
        input_snapshot: dict[str, Any],
        parent_prompt_id: uuid.UUID,
    ) -> tuple[StoryScorecard, uuid.UUID]:
        candidate_document = candidate.model_dump(mode="json", by_alias=True)
        system_prompt = (
            "你是短片事件方案评审。按开头吸引力、因果完整性、儿童与猫咪必要性、"
            "情绪弧线、可视化、时长适配、连续性和安全性逐项给出0到10分。"
            "对无法在目标时长完成、无必要换场或猫咪行为违规的方案必须明确警告。"
        )
        user_prompt = (
            f"原始输入：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}\n"
            f"事件方案：{json.dumps(candidate_document, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_event_critic",
                nodeId=uuid.uuid5(project_id, f"story-event-critic:{strategy.value}"),
                businessObjectType="recipe_story_event_batch",
                businessObjectId=recipe_instance_id,
                parentRunId=parent_prompt_id,
                templateName="story.event.critic.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "StoryEventScorecard",
                    "schema": StoryScorecard.model_json_schema(),
                },
                inputSnapshot={**input_snapshot, "candidate": candidate_document},
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryScorecard.model_json_schema(),
                output_name="StoryEventScorecard",
            )
            score = StoryScorecard.model_validate(result.payload)
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=score.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return score, prompt_id

    def _generate_script_from_event(
        self,
        *,
        project_id: uuid.UUID,
        event_id: uuid.UUID,
        input_snapshot: dict[str, Any],
    ) -> tuple[StoryCandidateOutput, uuid.UUID]:
        system_prompt = (
            "你是一人一猫治愈短片的剧情编剧。把已由人工选择的事件方案扩写为一个完整、"
            "可编辑、可分镜的剧情脚本，不得改变事件方向。脚本必须形成开始、发展、小变化、"
            "温暖收尾的因果链，明确儿童动作和猫咪反应；每个场景提供稳定sceneKey、叙事目的、"
            "地点、室内外、时间天气、关键装饰、道具和必要换场原因。保持固定IP和无对白约束。"
        )
        user_prompt = json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_script_expansion",
                nodeId=uuid.uuid5(project_id, "story-script-expander"),
                businessObjectType="story_event",
                businessObjectId=event_id,
                templateName="story.script.from_event.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "StoryScriptOutput",
                    "schema": StoryCandidateOutput.model_json_schema(),
                },
                inputSnapshot=input_snapshot,
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryCandidateOutput.model_json_schema(),
                output_name="StoryScriptOutput",
            )
            script = StoryCandidateOutput.model_validate(result.payload)
            validate_story_scene_plan(
                script,
                target_duration_seconds=int(input_snapshot["brief"]["targetDurationSeconds"]),
            )
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=script.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return script, prompt_id

    def _score_candidate(
        self,
        *,
        project_id: uuid.UUID,
        strategy: StoryStrategy,
        candidate: StoryCandidateOutput,
        input_snapshot: dict[str, Any],
        parent_prompt_id: uuid.UUID,
    ) -> tuple[StoryScorecard, uuid.UUID]:
        candidate_document = candidate.model_dump(mode="json", by_alias=True)
        system_prompt = (
            "你是独立短剧评审。按开头钩子、因果完整性、主体必要性、情绪曲线、"
            "可视化、时长适配、连续性与安全性逐项给出0到10分，不得替策划隐瞒风险。"
        )
        user_prompt = (
            f"原始输入：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}\n"
            f"候选故事：{json.dumps(candidate_document, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="story_critic",
                nodeId=uuid.uuid5(project_id, f"story-critic:{strategy.value}"),
                businessObjectType="project_story_strategy",
                businessObjectId=project_id,
                parentRunId=parent_prompt_id,
                templateName="story.critic.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "CanvasStoryCriticOutput",
                    "schema": StoryScorecard.model_json_schema(),
                },
                inputSnapshot={**input_snapshot, "candidate": candidate_document},
            ),
        )
        try:
            result = self._director.generate_structured(
                prompt=final_prompt,
                schema=StoryScorecard.model_json_schema(),
                output_name="CanvasStoryCriticOutput",
            )
            score = StoryScorecard.model_validate(result.payload)
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response=score.model_dump(mode="json", by_alias=True),
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        return score, prompt_id

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.approve_story_revision(revision_id)

    def edit_story_revision(self, revision_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.save_story_revision_edit(
            revision_id=revision_id,
            expected_revision=payload.expected_revision,
            idempotency_key=payload.idempotency_key,
            title=payload.title,
            body=payload.body,
            summary=payload.summary,
        )

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
    ) -> dict[str, Any]:
        context = self._repository.get_storyboard_context(
            project_id,
            source_story_revision_id=source_story_revision_id,
        )
        if context["existing"] is not None:
            return context["existing"]
        reference_inputs = (
            self._repository.get_storyboard_reference_inputs(
                project_id,
                reference_asset_ids,
            )
            if creation_mode == "from_characters"
            else {"bindings": [], "paths": ()}
        )
        story_binding = {
            "storyRevisionId": str(context["storyId"]),
            "sourceType": "approved_story",
            "semanticRole": "story_source",
            "purpose": "storyboard_structure",
            "instruction": "已批准剧情脚本是该分镜版本唯一叙事来源",
            "ordinal": 1,
            "locked": True,
            "evidenceLevel": "frozen",
        }
        storyboard_input_bindings = [
            story_binding,
            *[
                {**item, "ordinal": index}
                for index, item in enumerate(reference_inputs["bindings"], 2)
            ],
        ]
        input_snapshot = {
            "brief": context["brief"],
            "story": context["story"],
            "subjects": context["subjects"],
            "creationMode": creation_mode,
            "referenceAssetIds": [str(item) for item in reference_asset_ids],
            "referenceBindings": reference_inputs["bindings"],
            "inputBindings": storyboard_input_bindings,
            "instruction": instruction,
        }
        input_hash = _json_hash(input_snapshot)
        attempt, created = self._repository.begin_generation_attempt(
            project_id=project_id,
            business_object_type="storyboard",
            business_object_id=uuid.UUID(str(context["storyId"])),
            idempotency_key=(
                idempotency_key
                or hashlib.sha256(f"{project_id}:storyboard:{input_hash}".encode()).hexdigest()
            ),
            provider=self._provider_name,
            model=self._director.model,
            request=input_snapshot,
        )
        if not created:
            return attempt

        total_seconds = int(context["brief"]["targetDurationSeconds"])
        legacy_scene_count = len(context["story"]["scenes"])
        if exact_durations is not None:
            if sum(exact_durations) != total_seconds or any(
                not 2 <= duration <= 15 for duration in exact_durations
            ):
                raise ValueError("导演分镜时长必须精确覆盖总时长且每镜不少于2秒")
            minimum_beats = maximum_beats = len(exact_durations)
        else:
            minimum_beats = math.ceil(total_seconds / 15)
            maximum_beats = total_seconds // 8
        system_prompt = (
            "你是AIGC短剧分镜导演。把已批准故事拆成可独立编辑的镜头；"
            "每个镜头只需返回 order、title、direction、durationSeconds，"
            "可选返回 sceneLabel 和其他导演参数。direction 是完整镜头描述，"
            "应包含足够的画面、动作和叙事信息；不要为了满足字段而虚构内容。"
            "镜头不等于一次视频模型调用，后续生成编排可以组合相邻镜头。"
        )
        if legacy_scene_count == 0:
            system_prompt += (
                "当前完整故事没有预拆场景。请按正文自然分组为一个或多个连续场景，"
                "sceneOrder 必须从 1 开始连续编号；可用 sceneLabel 提供中性场景名称。"
            )
        if healing_recipe:
            system_prompt += (
                "当前为固定儿童、固定猫咪与项目锁定画风的原创治愈短片；"
                "可参考开始、小变化、温暖收尾的节奏；对白和其他高级信息均为可选内容。"
            )
        if creation_mode == "from_characters":
            system_prompt += (
                "用户从画布明确选择了角色素材。必须以已批准故事为叙事边界，"
                "将所选角色素材只作为身份与关系约束，不得用普通参考替换 Canon 身份。"
            )
            system_prompt += "\n" + "\n".join(
                f"@图片{item['ordinal']}：{item['instruction']}"
                for item in reference_inputs["bindings"]
            )
        user_prompt = (
            f"建议输出 {minimum_beats} 至 {maximum_beats} 个镜头，"
            + (
                f"可沿用 {legacy_scene_count} 个旧版场景作为分组参考，"
                "不需为每个旧版场景强制拆分镜头。\n"
                if legacy_scene_count
                else "按完整故事正文自然派生连续场景。\n"
            )
            + f"用户补充要求：{instruction or '无'}\n"
            f"输入快照：{json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True)}"
        )
        final_prompt = f"{system_prompt}\n\n{user_prompt}"
        prompt_id, _step_id = self._repository.begin_prompt_run(
            project_id=project_id,
            draft=PromptRunDraft(
                purpose="storyboard_director",
                nodeId=uuid.uuid5(project_id, "storyboard-director"),
                businessObjectType="story_revision",
                businessObjectId=uuid.UUID(str(context["storyId"])),
                templateName="storyboard.director.v1",
                templateVersion="1.0.0",
                systemPrompt=system_prompt,
                userPrompt=user_prompt,
                finalPrompt=final_prompt,
                provider=self._provider_name,
                model=self._director.model,
                providerRequestSnapshot={
                    "outputName": "CanvasStoryboardPlanOutput",
                    "schema": StoryboardPlanOutput.model_json_schema(),
                },
                inputSnapshot=input_snapshot,
            ),
        )
        attempt_id = str(attempt["id"])
        try:
            flexible_storyboard = getattr(
                self._director,
                "generate_storyboard_text",
                None,
            )
            if callable(flexible_storyboard):
                result = flexible_storyboard(
                    prompt=final_prompt,
                    output_name="CanvasStoryboardPlanOutput",
                    image_paths=tuple(reference_inputs["paths"]),
                )
            else:
                # Compatibility for external DirectorGateway implementations that
                # predate tolerant storyboard text. Production gateways implement
                # generate_storyboard_text and preserve non-JSON model output.
                result = self._director.generate_structured(
                    prompt=final_prompt,
                    schema=StoryboardPlanOutput.model_json_schema(),
                    output_name="CanvasStoryboardPlanOutput",
                    image_paths=tuple(reference_inputs["paths"]),
                )
            parsed = parse_llm_storyboard_output(result.payload)
            if parsed.status == "needs_structuring":
                structured_response = parsed.model_dump(mode="json", by_alias=True)
                self._repository.complete_prompt_run(
                    prompt_id,
                    status="succeeded",
                    raw_response=result.payload,
                    structured_response=structured_response,
                    provider_response_id=result.response_id,
                    output_hash=_json_hash(result.payload),
                )
                self._repository.finish_generation_attempt(
                    attempt_id,
                    status="succeeded",
                    response={
                        "status": "needs_structuring",
                        "promptId": str(prompt_id),
                        "diagnostics": structured_response["diagnostics"],
                    },
                )
                return {
                    "projectId": str(project_id),
                    "storyRevisionId": str(context["storyId"]),
                    "promptId": str(prompt_id),
                    **structured_response,
                }
            if parsed.plan is None:  # pragma: no cover - guarded by the parse result model
                raise RuntimeError("ready storyboard parse result is missing its plan")
            plan = parsed.plan
            diagnostics = storyboard_quality_diagnostics(plan.shots)
            if not minimum_beats <= len(plan.beats) <= maximum_beats:
                diagnostics.append(
                    CanvasDiagnostic(
                        code="storyboard_shot_count_recommendation",
                        severity="warning",
                        message=(
                            f"当前分镜为 {len(plan.beats)} 镜；按项目时长建议 "
                            f"{minimum_beats}–{maximum_beats} 镜。"
                        ),
                    )
                )
            scene_orders = {beat.scene_order for beat in plan.beats}
            expected_scene_orders = set(range(1, max(scene_orders) + 1))
            if legacy_scene_count and max(scene_orders) > legacy_scene_count:
                raise ValueError("分镜计划引用了不存在的旧版场景")
            if legacy_scene_count:
                diagnostics.extend(
                    CanvasDiagnostic(
                        code="storyboard_scene_uncovered",
                        severity="warning",
                        message=(
                            f"旧版场景 {scene_order} 未被当前分镜使用，"
                            "不影响保存或确认制作方案。"
                        ),
                    )
                    for scene_order in sorted(
                        set(range(1, legacy_scene_count + 1)) - scene_orders
                    )
                )
            if not legacy_scene_count and scene_orders != expected_scene_orders:
                raise ValueError("从完整故事派生的场景必须从 1 开始连续编号")
            if exact_durations is not None:
                durations = exact_durations
            elif all(beat.duration_seconds is not None for beat in plan.beats):
                durations = tuple(int(beat.duration_seconds or 0) for beat in plan.beats)
                if sum(durations) != total_seconds or any(
                    not 2 <= duration <= 15 for duration in durations
                ):
                    raise ValueError(
                        "导演分镜 durationSeconds 必须精确覆盖项目总时长且每镜为2至15秒"
                    )
            else:
                weights = tuple(int(beat.duration_weight or 0) for beat in plan.beats)
                durations = allocate_bounded_durations(
                    total_seconds,
                    weights,
                    minimum_seconds=8,
                    maximum_seconds=15,
                )
        except GatewayError as exc:
            status = "submission_unknown" if exc.submission_unknown else "failed"
            self._repository.complete_prompt_run(
                prompt_id,
                status=status,
                error={"code": exc.code, "message": str(exc)},
            )
            self._repository.finish_generation_attempt(
                attempt_id,
                status=status,
                error={"code": exc.code, "message": str(exc)},
            )
            raise
        except Exception as exc:
            self._repository.complete_prompt_run(
                prompt_id,
                status="failed",
                error={"message": str(exc)},
            )
            self._repository.finish_generation_attempt(
                attempt_id,
                status="failed",
                error={"code": "invalid_storyboard", "message": str(exc)},
            )
            raise
        self._repository.complete_prompt_run(
            prompt_id,
            status="succeeded",
            raw_response=result.payload,
            structured_response={
                "status": "ready",
                "plan": plan.model_dump(mode="json", by_alias=True),
                "diagnostics": [
                    item.model_dump(mode="json", by_alias=True) for item in diagnostics
                ],
            },
            provider_response_id=result.response_id,
            output_hash=_json_hash(result.payload),
        )
        storyboard = self._repository.save_storyboard_plan(
            project_id,
            story_id=uuid.UUID(str(context["storyId"])),
            plan=plan,
            durations=durations,
            prompt_id=prompt_id,
            input_bindings=storyboard_input_bindings,
        )
        self._repository.finish_generation_attempt(
            attempt_id,
            status="succeeded",
            response={"beatIds": [beat["id"] for beat in storyboard["beats"]]},
        )
        return {
            **storyboard,
            "diagnostics": [
                item.model_dump(mode="json", by_alias=True) for item in diagnostics
            ],
        }

    def update_shot_beat(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.update_shot_beat(
            beat_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def replace_shot_beat_references(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.replace_shot_beat_references(
            beat_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.save_manual_storyboard(
            project_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def compile_storyboard_prompts(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.compile_storyboard_prompts(project_id, payload)

    def create_generation_attempt(self, payload: Any) -> dict[str, Any]:
        return self._repository.create_generation_attempt(payload)

    def retry_generation_attempt(self, attempt_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.retry_generation_attempt(attempt_id, payload)

    def review_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.review_asset(asset_id, payload)

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_prompt_run(prompt_id)

    def get_workspace_shell(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_workspace_shell(project_id)

    def get_script_workspace(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_script_workspace(project_id)

    def get_production_flow(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_production_flow(project_id)

    def save_production_flow_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.save_production_flow_layout(
            project_id,
            expected_version=expected_version,
            payload=payload,
        )

    def get_video_workbench(self, project_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_video_workbench(project_id)

    def get_asset_generation_lineage(self, asset_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.get_asset_generation_lineage(asset_id)

    def create_generation_batches(
        self,
        payloads: Sequence[Any],
        *,
        parent_step_id: uuid.UUID,
    ) -> tuple[dict[str, Any], ...]:
        resolved_items: list[Any] = []
        for payload in payloads:
            provider = payload.provider or self._provider_name
            model = payload.model or self._image_model
            capability_revision = self._provider_capability_revision(provider, model)
            references = list(payload.input.get("referenceManifest") or [])
            prompt = str(payload.input.get("prompt") or "")
            exact_input = character_design_generation_input(
                provider=provider,
                model=model,
                candidate_count=payload.candidate_count,
                prompt=prompt,
                references=references,
                capability_revision=capability_revision,
            )
            resolved_items.append(
                payload.model_copy(
                    update={
                        "provider": provider,
                        "model": model,
                        "expected_input_hash": generation_input_hash(exact_input),
                        "input": {
                            **payload.input,
                            "capabilityRevision": capability_revision,
                        },
                    }
                )
            )
        resolved = tuple(resolved_items)
        return self._repository.create_generation_batches(
            resolved,
            parent_step_id=parent_step_id,
        )

    def preview_generation_batches(
        self,
        payloads: Sequence[Any],
    ) -> tuple[dict[str, Any], ...]:
        previews: list[dict[str, Any]] = []
        for payload in payloads:
            provider = payload.provider or self._provider_name
            model = payload.model or self._image_model
            capability = self._provider_image_capability(provider, model)
            document = dict(capability.get("capabilities") or {})
            capability_revision = str(
                document.get("capabilityRevision")
                or capability.get("updatedAt")
                or capability.get("id")
            )
            references = list(payload.input.get("referenceManifest") or [])
            prompt = str(payload.input.get("prompt") or "")
            exact_input = character_design_generation_input(
                provider=provider,
                model=model,
                candidate_count=payload.candidate_count,
                prompt=prompt,
                references=references,
                capability_revision=capability_revision,
            )
            input_image_cost = document.get("inputImageCostMicros")
            output_image_cost = document.get("outputImageCostMicros")
            estimated_cost = (
                None
                if input_image_cost is None or output_image_cost is None
                else payload.candidate_count
                * (
                    int(output_image_cost)
                    + len(references) * int(input_image_cost)
                )
            )
            previews.append(
                {
                    "provider": provider,
                    "model": model,
                    "mode": "all_reference",
                    "capabilityRevision": capability_revision,
                    "prompt": prompt,
                    "references": references,
                    "warnings": [],
                    "blockers": [],
                    "estimatedCostMicros": estimated_cost,
                    "inputHash": generation_input_hash(exact_input),
                    "slot": (payload.input.get("characterDesign") or {}).get("slot"),
                }
            )
        return tuple(previews)

    def _provider_capability_revision(self, provider: str, model: str) -> str:
        capability = self._provider_image_capability(provider, model)
        document = capability.get("capabilities") or {}
        return str(
            document.get("capabilityRevision")
            or capability.get("updatedAt")
            or capability.get("id")
        )

    def _provider_image_capability(self, provider: str, model: str) -> dict[str, Any]:
        capability = next(
            (
                item
                for item in self._repository.list_provider_capabilities(media_kind="image")
                if item.get("provider") == provider and item.get("model") == model
            ),
            None,
        )
        if capability is None:
            raise ValueError("角色设计图片模型没有启用的 Provider 能力档案")
        return capability

    def create_video_edit_recipe(self, payload: Any) -> dict[str, Any]:
        return self._repository.create_video_edit_recipe(payload)

    def update_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.update_video_edit_recipe(
            recipe_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def replace_video_edit_annotations(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        return self._repository.replace_video_edit_annotations(
            recipe_id,
            expected_revision=expected_revision,
            payload=payload,
        )

    def compile_video_edit_recipe(self, recipe_id: uuid.UUID) -> dict[str, Any]:
        return self._repository.compile_video_edit_recipe(recipe_id, self._video_edit_capability)

    def submit_video_edit_recipe(self, recipe_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        return self._repository.submit_video_edit_recipe(
            recipe_id,
            payload,
            image_provider=self._provider_name,
            image_model=self._image_model,
        )


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
