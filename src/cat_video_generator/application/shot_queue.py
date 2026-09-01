"""V5 project editing, independent video-clip production and project sequences."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..domain.contracts import (
    AcceptedVisualAssetPlan,
    AnchorMode,
    LookReferenceBinding,
    LookReferencePurpose,
    ReferenceBinding,
    ReferenceImageDraft,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
    SceneAssetReadiness,
    SceneAssetSlotReadiness,
    SceneLookDraft,
    SceneLookPlan,
    SceneLookUsage,
    ShotAssistAnalysis,
    ShotAssistPatch,
    ShotCardDraft,
    ShotPromptContext,
    ShotSuggestion,
    ShotSuggestionOutput,
    StoryDiagnosisOutput,
    StoryExpansionOutput,
    StoryProjectInput,
    StoryRewriteOutput,
    StoryRewriteStrategy,
    VisualAssetAction,
    VisualAssetPlanOutput,
    VisualAssetPurpose,
    VisualProfileDraft,
)
from ..domain.creative_workflow import shot_snapshot_hash, story_source_hash
from ..domain.production_recipes import SEEDANCE_2_0_CAPABILITY
from ..domain.prompts import (
    CompiledPrompt,
    compile_anchor_prompt,
    compile_anchor_review_prompt,
    compile_range_edit_prompt,
    compile_reference_image_prompt,
    compile_scene_look_prompt,
    compile_shot_assistance_prompt,
    compile_shot_suggestion_prompt,
    compile_shot_video_prompt_parts,
    compile_story_diagnosis_prompt,
    compile_story_expansion_prompt,
    compile_story_rewrite_prompt,
    compile_video_review_prompt,
    compile_visual_asset_plan_prompt,
)
from ..domain.rendering import (
    MediaSource,
    build_edit_input_plan,
    build_shot_input_plan,
)
from ..domain.shot_assistance import analyze_shot_draft
from ..domain.workflow import PromptPurpose, StepKind, StepStatus
from .generation_specs import (
    ProviderInputMode,
    ShotCompilationContext,
    ShotGenerationSpec,
)
from .ports import (
    AssetStore,
    DirectorGateway,
    FrameExtractor,
    GatewayError,
    MediaGateway,
    MediaProbe,
    ProjectReadModel,
    RuntimePreflight,
    ShotGenerationReadModel,
    ShotQueueStore,
    StoredAsset,
    StoredProject,
    StoredScene,
    StoredShot,
    StoredStep,
    StoredVisualProfileRevision,
    VideoTaskResult,
)
from .read_models import (
    asset_projection,
    project_graph_projection,
    shot_projection,
    step_projection,
)

_INTERNAL_CHARACTER_DESIGN_LABEL = re.compile(
    r"character-design:[0-9a-fA-F-]{36}:(child|cat|pair_scale):candidate:\d+"
)
_CHARACTER_DESIGN_LABELS = {
    "child": "本集儿童设计",
    "cat": "本集猫咪设计",
    "pair_scale": "一人一猫同框比例",
}


def _creator_prompt_preview(prompt: str) -> str:
    """Render legacy storage keys as production roles without mutating history.

    Historical production packages used semantic keys as visible reference
    titles. They remain immutable audit records and are blocked from new paid
    submission; this compatibility boundary only makes their editor preview
    readable while the user recompiles the package through the normal flow.
    """

    return _INTERNAL_CHARACTER_DESIGN_LABEL.sub(
        lambda match: _CHARACTER_DESIGN_LABELS[match.group(1)],
        prompt,
    )


class GatewayUnavailableError(RuntimeError):
    """Raised before a paid task is submitted when its Ark gateway is unavailable."""


@dataclass(frozen=True, slots=True)
class SuggestionResult:
    step_id: uuid.UUID
    output: ShotSuggestionOutput


@dataclass(frozen=True, slots=True)
class StoryDiagnosisResult:
    step_id: uuid.UUID
    output: StoryDiagnosisOutput


@dataclass(frozen=True, slots=True)
class StoryExpansionResult:
    step_id: uuid.UUID
    output: StoryExpansionOutput


@dataclass(frozen=True, slots=True)
class StoryRewriteResult:
    step_id: uuid.UUID
    output: StoryRewriteOutput


@dataclass(frozen=True, slots=True)
class ShotAssistanceResult:
    step_id: uuid.UUID
    analysis: ShotAssistAnalysis


@dataclass(frozen=True, slots=True)
class VisualAssetPlanResult:
    step_id: uuid.UUID
    output: VisualAssetPlanOutput


class RevisionConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SceneLookInputSet:
    scene: StoredScene
    profile: StoredVisualProfileRevision
    draft: SceneLookDraft
    bindings: tuple[LookReferenceBinding, ...]
    assets: tuple[StoredAsset, ...]
    descriptions: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PreviousTailState:
    previous_shot: StoredShot | None
    source_video_id: uuid.UUID | None
    active: StoredAsset | None
    bound: StoredAsset | None
    stale: bool


@dataclass(frozen=True, slots=True)
class ShotAssistCandidate:
    asset: StoredAsset
    source_layer: str
    responsibility: str


@dataclass(frozen=True, slots=True)
class AcceptedAnchorBrief:
    step_id: uuid.UUID
    text: str
    source: str
    source_draft_revision: int
    accepted_draft_revision: int
    accepted_at: str | None
    created_at: str | None
    stale: bool


@dataclass(frozen=True, slots=True)
class ReferenceImageSpec:
    project: StoredProject
    scene: StoredScene | None
    profile: StoredVisualProfileRevision
    draft: ReferenceImageDraft
    scope: str
    operation_key: str
    sources: tuple[StoredAsset, ...]
    descriptions: tuple[str, ...]
    prompt: CompiledPrompt
    snapshot: dict[str, Any]
    input_hash: str


class ProjectEditingService:
    def __init__(
        self,
        *,
        repository: ShotQueueStore,
        director: DirectorGateway | None,
        provider_name: str,
    ) -> None:
        self._repository = repository
        self._director = director
        self._provider_name = provider_name

    def create_project(
        self, source: StoryProjectInput, *, content_date: date | None = None
    ) -> dict[str, Any]:
        project = self._repository.create_project(
            source,
            content_date=content_date or date.today(),
        )
        return {"projectId": str(project.id), "sceneCount": 1}

    def expand_story(
        self,
        scene_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
    ) -> StoryExpansionResult:
        self._require_paid_director(
            allow_paid_generation,
            "story expansion requires explicit paid-generation permission",
        )
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        profile = self._repository.get_visual_profile(project.id)
        self._assert_scene_stage_available(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-expansion",
        )
        prompt = compile_story_expansion_prompt(
            project_title=project.title,
            scene=scene.draft,
            visual_profile=profile.draft,
        )
        snapshot = {
            "sourceHash": story_source_hash(scene.draft),
            "scene": scene.draft.model_dump(mode="json", by_alias=True),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
        }
        step = self._run_scene_director_stage(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-expansion",
            prompt=prompt,
            snapshot=snapshot,
            output_type=StoryExpansionOutput,
            output_name="StoryExpansionOutput",
        )
        return StoryExpansionResult(
            step_id=step.id,
            output=StoryExpansionOutput.model_validate(
                step.input_snapshot.get("providerOutput")
            ),
        )

    def accept_story_expansion(
        self,
        step_id: uuid.UUID,
        *,
        expansion: StoryExpansionOutput,
    ) -> StoredScene:
        step = self._repository.get_step(step_id)
        self._validate_scene_stage_step(
            step,
            operation_key="director:story-expansion",
        )
        assert step.scene_id is not None
        scene = self._repository.get_scene(step.scene_id)
        expected_hash = str(step.input_snapshot.get("sourceHash") or "")
        if story_source_hash(scene.draft) != expected_hash:
            raise RevisionConflictError(
                "scene story changed after expansion; generate a new expansion"
            )
        expanded_draft = scene.draft.model_copy(
            update={"source_text": expansion.expanded_story}
        )
        accepted_output = {
            **expansion.model_dump(mode="json", by_alias=True),
            "acceptedStoryHash": story_source_hash(expanded_draft),
        }
        return self._repository.accept_story_expansion(
            step_id=step.id,
            expected_source_hash=expected_hash,
            accepted_output=accepted_output,
            expanded_story=expansion.expanded_story,
        )

    def diagnose_story(
        self,
        scene_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
    ) -> StoryDiagnosisResult:
        self._require_paid_director(
            allow_paid_generation,
            "story diagnosis requires explicit paid-generation permission",
        )
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        profile = self._repository.get_visual_profile(project.id)
        self._assert_scene_stage_available(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-diagnosis",
        )
        previous, following = self._adjacent_scenes(scene)
        prompt = compile_story_diagnosis_prompt(
            project_title=project.title,
            scene=scene.draft,
            visual_profile=profile.draft,
            previous_scene_summary=(
                None if previous is None else previous.draft.source_text
            ),
            next_scene_summary=(
                None if following is None else following.draft.source_text
            ),
        )
        snapshot = {
            "sourceHash": story_source_hash(scene.draft),
            "scene": scene.draft.model_dump(mode="json", by_alias=True),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "previousScene": _scene_story_snapshot(previous),
            "nextScene": _scene_story_snapshot(following),
        }
        step = self._run_scene_director_stage(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-diagnosis",
            prompt=prompt,
            snapshot=snapshot,
            output_type=StoryDiagnosisOutput,
            output_name="StoryDiagnosisOutput",
        )
        return StoryDiagnosisResult(
            step_id=step.id,
            output=StoryDiagnosisOutput.model_validate(
                step.input_snapshot.get("providerOutput")
            ),
        )

    def accept_story_diagnosis(
        self,
        step_id: uuid.UUID,
        *,
        diagnosis: StoryDiagnosisOutput,
        selected_strategy: StoryRewriteStrategy | str | None,
        additional_instructions: str,
        preserve_original: bool,
    ) -> StoredStep:
        step = self._repository.get_step(step_id)
        self._validate_scene_stage_step(
            step,
            operation_key="director:story-diagnosis",
        )
        assert step.scene_id is not None
        scene = self._repository.get_scene(step.scene_id)
        expected_hash = str(step.input_snapshot.get("sourceHash") or "")
        if story_source_hash(scene.draft) != expected_hash:
            raise RevisionConflictError(
                "scene story changed after diagnosis; run the diagnosis again"
            )
        selected = (
            None
            if selected_strategy is None
            else StoryRewriteStrategy(selected_strategy)
        )
        if preserve_original and selected is not None:
            raise ValueError(
                "preserve-original and a rewrite strategy are mutually exclusive"
            )
        if not preserve_original and selected is None:
            raise ValueError("select a rewrite strategy or preserve the original story")
        if selected is not None and selected not in {
            item.strategy for item in diagnosis.rewrite_options
        }:
            raise ValueError("selected rewrite strategy is not present in the accepted diagnosis")
        accepted_output = {
            "diagnosis": diagnosis.model_dump(mode="json", by_alias=True),
            "selectedStrategy": None if selected is None else selected.value,
            "additionalInstructions": additional_instructions.strip(),
            "preserveOriginal": preserve_original,
        }
        return self._repository.accept_story_diagnosis(
            step_id=step.id,
            expected_source_hash=expected_hash,
            accepted_output=accepted_output,
        )

    def rewrite_story(
        self,
        scene_id: uuid.UUID,
        *,
        diagnosis_step_id: uuid.UUID,
        allow_paid_generation: bool,
    ) -> StoryRewriteResult:
        self._require_paid_director(
            allow_paid_generation,
            "story rewrite requires explicit paid-generation permission",
        )
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        profile = self._repository.get_visual_profile(project.id)
        diagnosis_step = self._repository.get_step(diagnosis_step_id)
        if (
            diagnosis_step.scene_id != scene.id
            or diagnosis_step.operation_key != "director:story-diagnosis"
            or diagnosis_step.status is not StepStatus.SUCCEEDED
            or "acceptedOutput" not in diagnosis_step.input_snapshot
        ):
            raise ValueError("story rewrite requires an accepted story diagnosis")
        current_story_hash = story_source_hash(scene.draft)
        if diagnosis_step.input_snapshot.get("sourceHash") != current_story_hash:
            raise RevisionConflictError(
                "scene story changed after diagnosis; run the diagnosis again"
            )
        latest_accepted_diagnosis = next(
            (
                item
                for item in reversed(
                    self._repository.list_steps(
                        project_id=project.id,
                        scene_id=scene.id,
                    )
                )
                if item.operation_key == "director:story-diagnosis"
                and item.status is StepStatus.SUCCEEDED
                and item.input_snapshot.get("sourceHash") == current_story_hash
                and isinstance(item.input_snapshot.get("acceptedOutput"), dict)
            ),
            None,
        )
        if latest_accepted_diagnosis is None or latest_accepted_diagnosis.id != diagnosis_step.id:
            raise RevisionConflictError(
                "story rewrite must use the latest accepted story diagnosis version"
            )
        accepted_diagnosis = diagnosis_step.input_snapshot["acceptedOutput"]
        if (
            accepted_diagnosis.get("preserveOriginal") is True
            or not accepted_diagnosis.get("selectedStrategy")
        ):
            raise ValueError(
                "story rewrite requires a selected diagnosis rewrite strategy"
            )
        self._assert_scene_stage_available(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-rewrite",
        )
        prompt = compile_story_rewrite_prompt(
            project_title=project.title,
            scene=scene.draft,
            visual_profile=profile.draft,
            accepted_diagnosis=accepted_diagnosis,
        )
        snapshot = {
            "sourceHash": story_source_hash(scene.draft),
            "scene": scene.draft.model_dump(mode="json", by_alias=True),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "diagnosisStepId": str(diagnosis_step.id),
            "acceptedDiagnosis": accepted_diagnosis,
        }
        step = self._run_scene_director_stage(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:story-rewrite",
            prompt=prompt,
            snapshot=snapshot,
            output_type=StoryRewriteOutput,
            output_name="StoryRewriteOutput",
        )
        return StoryRewriteResult(
            step_id=step.id,
            output=StoryRewriteOutput.model_validate(
                step.input_snapshot.get("providerOutput")
            ),
        )

    def accept_story_rewrite(
        self,
        step_id: uuid.UUID,
        *,
        rewrite: StoryRewriteOutput,
    ) -> StoredScene:
        step = self._repository.get_step(step_id)
        self._validate_scene_stage_step(
            step,
            operation_key="director:story-rewrite",
        )
        assert step.scene_id is not None
        scene = self._repository.get_scene(step.scene_id)
        expected_hash = str(step.input_snapshot.get("sourceHash") or "")
        if story_source_hash(scene.draft) != expected_hash:
            raise RevisionConflictError(
                "scene story changed after rewrite; generate a new rewrite"
            )
        revised_draft = scene.draft.model_copy(
            update={"source_text": rewrite.rewritten_story}
        )
        accepted_output = {
            **rewrite.model_dump(mode="json", by_alias=True),
            "acceptedStoryHash": story_source_hash(revised_draft),
        }
        return self._repository.accept_story_rewrite(
            step_id=step.id,
            expected_source_hash=expected_hash,
            accepted_output=accepted_output,
            rewritten_story=rewrite.rewritten_story,
        )

    def plan_visual_assets(
        self,
        scene_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        storyboard_revision_id: uuid.UUID,
        structure_hash: str,
        generation_plan_id: uuid.UUID,
        generation_plan_hash: str,
    ) -> VisualAssetPlanResult:
        self._require_paid_director(
            allow_paid_generation,
            "visual asset planning requires explicit paid-generation permission",
        )
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        profile = self._repository.get_visual_profile(project.id)
        storyboard_context = self._repository.storyboard_production_context(scene.id)
        if not storyboard_context.get("structureApproved"):
            raise ValueError("视觉资产规划需要先批准当前分镜结构")
        if not storyboard_context.get("generationPlanApproved"):
            raise ValueError("视觉资产规划需要先批准 Agent 生成编排")
        if int(storyboard_context.get("sceneGenerationClipCount") or 0) < 1:
            raise ValueError("当前场景尚未被生成编排完整覆盖")
        editorial_shots = list(storyboard_context.get("editorialShots") or [])
        if not editorial_shots:
            raise ValueError("当前场景没有已批准的导演分镜")
        submitted_lineage = {
            "storyboardRevisionId": str(storyboard_revision_id),
            "structureHash": structure_hash,
            "generationPlanId": str(generation_plan_id),
            "generationPlanHash": generation_plan_hash,
        }
        for key, submitted in submitted_lineage.items():
            if submitted != storyboard_context.get(key):
                raise RevisionConflictError("页面中的分镜结构或生成编排已过期，请刷新后重试")
        self._assert_scene_stage_available(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:visual-asset-plan",
        )
        existing_assets = tuple(
            asset
            for asset in self._repository.list_assets(
                project_id=project.id,
                include_canon=True,
            )
            if asset.media_type == "image"
            and asset.status in {"approved", "ready"}
            and asset.content_ready
        )
        prompt = compile_visual_asset_plan_prompt(
            project_title=project.title,
            scene=scene.draft,
            shot_summaries=tuple(
                f"{item['order']}. {item['title']}"
                f"（{item['durationSeconds']}秒）："
                f"{item.get('visualDescription') or ''}；"
                f"儿童：{item.get('childAction') or ''}；"
                f"猫咪：{item.get('catAction') or ''}；"
                f"空间关系：{item.get('spatialRelation') or ''}；"
                f"镜头：{item.get('camera') or ''}"
                for item in editorial_shots
            ),
            visual_profile=profile.draft,
            existing_assets=tuple(
                f"assetId={asset.id}；名称={asset.display_name}；scope={asset.scope}；"
                f"职责={asset.reference_purpose or asset.role}"
                for asset in existing_assets
            ),
        )
        snapshot = {
            "sceneStoryHash": story_source_hash(scene.draft),
            "storyboardRevisionId": storyboard_context["storyboardRevisionId"],
            "structureHash": storyboard_context["structureHash"],
            "generationPlanId": storyboard_context["generationPlanId"],
            "generationPlanHash": storyboard_context["generationPlanHash"],
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "existingAssetIds": [str(item.id) for item in existing_assets],
        }
        step = self._run_scene_director_stage(
            project_id=project.id,
            scene_id=scene.id,
            operation_key="director:visual-asset-plan",
            prompt=prompt,
            snapshot=snapshot,
            output_type=VisualAssetPlanOutput,
            output_name="VisualAssetPlanOutput",
        )
        return VisualAssetPlanResult(
            step_id=step.id,
            output=VisualAssetPlanOutput.model_validate(
                step.input_snapshot.get("providerOutput")
            ),
        )

    def accept_visual_asset_plan(
        self,
        step_id: uuid.UUID,
        *,
        plan: AcceptedVisualAssetPlan,
    ) -> StoredStep:
        step = self._repository.get_step(step_id)
        self._validate_visual_asset_plan_selection(step, plan, allow_accepted=False)
        return self._repository.accept_visual_asset_plan(
            step_id=step.id,
            expected_storyboard_revision_id=uuid.UUID(
                str(step.input_snapshot["storyboardRevisionId"])
            ),
            expected_structure_hash=str(step.input_snapshot["structureHash"]),
            expected_generation_plan_id=uuid.UUID(
                str(step.input_snapshot["generationPlanId"])
            ),
            expected_generation_plan_hash=str(
                step.input_snapshot["generationPlanHash"]
            ),
            accepted_output=plan,
        )

    def revise_visual_asset_plan(
        self,
        step_id: uuid.UUID,
        *,
        expected_revision: int,
        plan: AcceptedVisualAssetPlan,
        note: str = "",
    ) -> StoredStep:
        step = self._repository.get_step(step_id)
        if step.attempt != expected_revision:
            raise RevisionConflictError("视觉资产规划已更新，请基于最新规划版本重新应用修改")
        if not isinstance(step.input_snapshot.get("acceptedOutput"), dict):
            raise ValueError("只有已经采用的视觉资产规划才能创建人工修订版本")
        assert step.scene_id is not None
        accepted_steps = [
            item
            for item in self._repository.list_steps(
                project_id=step.project_id,
                scene_id=step.scene_id,
            )
            if item.operation_key == "director:visual-asset-plan"
            and isinstance(item.input_snapshot.get("acceptedOutput"), dict)
        ]
        latest = max(accepted_steps, key=lambda item: item.attempt, default=None)
        if latest is None or latest.id != step.id:
            raise RevisionConflictError("视觉资产规划已更新，请基于最新规划版本重新应用修改")
        self._validate_visual_asset_plan_selection(step, plan, allow_accepted=True)
        return self._repository.revise_visual_asset_plan(
            step_id=step.id,
            expected_revision=expected_revision,
            accepted_output=plan,
            note=note.strip(),
        )

    def _validate_visual_asset_plan_selection(
        self,
        step: StoredStep,
        plan: AcceptedVisualAssetPlan,
        *,
        allow_accepted: bool,
    ) -> None:
        self._validate_scene_stage_step(
            step,
            operation_key="director:visual-asset-plan",
            allow_accepted=allow_accepted,
        )
        assert step.scene_id is not None
        scene = self._repository.get_scene(step.scene_id)
        storyboard_context = self._repository.storyboard_production_context(scene.id)
        if (
            not storyboard_context.get("structureApproved")
            or not storyboard_context.get("generationPlanApproved")
            or storyboard_context.get("storyboardRevisionId")
            != step.input_snapshot.get("storyboardRevisionId")
            or storyboard_context.get("structureHash")
            != step.input_snapshot.get("structureHash")
            or storyboard_context.get("generationPlanId")
            != step.input_snapshot.get("generationPlanId")
            or storyboard_context.get("generationPlanHash")
            != step.input_snapshot.get("generationPlanHash")
        ):
            raise RevisionConflictError(
                "分镜结构或生成编排在规划后已变化，请保留当前记录并建立新规划"
            )
        output = VisualAssetPlanOutput.model_validate(
            step.input_snapshot.get("providerOutput")
        )
        proposed = {item.suggestion_key: item for item in output.suggestions}
        proposed_keys = set(proposed)
        selected_keys = {item.suggestion_key for item in plan.selections}
        if not selected_keys.issubset(proposed_keys):
            raise ValueError("accepted visual assets must come from this planning version")
        for item in plan.selections:
            source = proposed[item.suggestion_key]
            if (
                item.purpose is not source.purpose
                or item.target_scope is not source.target_scope
                or item.display_name != source.display_name
            ):
                raise ValueError(
                    "visual asset identity, purpose, and scope are fixed by the planning version"
                )
        available_ids = {
            item.id
            for item in self._repository.list_assets(
                project_id=scene.project_id,
                include_canon=True,
            )
            if item.media_type == "image"
            and item.status in {"approved", "ready"}
            and item.content_ready
        }
        referenced_ids: set[uuid.UUID] = set()
        for item in plan.selections:
            if item.action is VisualAssetAction.GENERATE:
                referenced_ids.update(item.reference_asset_ids)
            elif item.action is VisualAssetAction.EXISTING:
                assert item.existing_asset_id is not None
                referenced_ids.add(item.existing_asset_id)
        if not referenced_ids.issubset(available_ids):
            raise ValueError("accepted visual asset plan contains unavailable references")

    def scene_asset_readiness(self, scene_id: uuid.UUID) -> SceneAssetReadiness:
        return _scene_asset_readiness(self._repository, scene_id)

    def creative_workflow(self, scene_id: uuid.UUID) -> dict[str, Any]:
        scene = self._repository.get_scene(scene_id)
        current_shots = self._repository.list_shots(scene.id)
        current_shot_snapshot_hash = shot_snapshot_hash(
            (item.id, item.draft_revision, item.draft) for item in current_shots
        )
        steps = self._repository.list_steps(
            project_id=scene.project_id,
            scene_id=scene.id,
        )
        stage_keys = {
            "expansion": "director:story-expansion",
            "diagnosis": "director:story-diagnosis",
            "rewrite": "director:story-rewrite",
            "storyboard": "director:shot-suggestions",
            "visualAssets": "director:visual-asset-plan",
        }
        source_steps = [
            item
            for item in steps
            if item.operation_key
            in {"director:story-expansion", "director:story-diagnosis"}
        ]
        first_scene_snapshot = (
            None
            if not source_steps
            else source_steps[0].input_snapshot.get("scene")
        )
        original_story = (
            first_scene_snapshot.get("sourceText")
            if isinstance(first_scene_snapshot, dict)
            else scene.draft.source_text
        )
        current_source = "scene_draft"
        current_source_step_id: str | None = None
        current_hash = story_source_hash(scene.draft)
        for step in reversed(steps):
            accepted = step.input_snapshot.get("acceptedOutput")
            if not isinstance(accepted, dict):
                continue
            if (
                step.operation_key == "director:story-rewrite"
                and accepted.get("acceptedStoryHash") == current_hash
            ):
                current_source = "accepted_rewrite"
                current_source_step_id = str(step.id)
                break
            if (
                step.operation_key == "director:story-expansion"
                and accepted.get("acceptedStoryHash") == current_hash
            ):
                current_source = "accepted_expansion"
                current_source_step_id = str(step.id)
                break
            if (
                step.operation_key == "director:story-diagnosis"
                and accepted.get("preserveOriginal") is True
                and step.input_snapshot.get("sourceHash") == current_hash
            ):
                current_source = "preserved_original"
                current_source_step_id = str(step.id)
                break
        return {
            "sceneId": str(scene.id),
            "originalStory": original_story,
            "currentStory": scene.draft.source_text,
            "currentStoryHash": current_hash,
            "currentStorySource": current_source,
            "currentStorySourceStepId": current_source_step_id,
            "currentShotSnapshotHash": current_shot_snapshot_hash,
            "stages": {
                name: [
                    _creative_step_json(item)
                    for item in reversed(steps)
                    if item.operation_key == operation_key
                ]
                for name, operation_key in stage_keys.items()
            },
            "reviews": [
                _creative_step_json(item)
                for item in reversed(steps)
                if item.operation_key == "director:shot-assistance"
            ],
        }

    def restore_project_canon_references(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, Any]:
        current = self._repository.get_visual_profile(project_id)
        canon = self._repository.get_default_visual_profile(project_id)
        purposes = {item.purpose for item in canon.reference_bindings}
        required = {
            LookReferencePurpose.PERSON_IDENTITY,
            LookReferencePurpose.CAT_IDENTITY,
            LookReferencePurpose.STYLE,
        }
        if not required.issubset(purposes):
            raise ValueError(
                "Canon references are incomplete; repair Canon assets before restoring the project"
            )
        restored_draft: VisualProfileDraft = current.draft.model_copy(
            update={"reference_bindings": canon.reference_bindings}
        )
        profile, cleaned_shot_count = self._repository.restore_project_canon_references(
            project_id,
            restored_draft,
        )
        return {
            "projectId": str(project_id),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileRevision": profile.revision,
            "referenceCount": len(profile.draft.reference_bindings),
            "cleanedShotCount": cleaned_shot_count,
        }

    def suggest_shots(
        self,
        scene_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
    ) -> SuggestionResult:
        if not allow_paid_generation:
            raise ValueError("AI shot suggestions require explicit paid-generation permission")
        if self._director is None:
            raise GatewayUnavailableError("Director gateway is not configured")
        scene = self._repository.get_scene(scene_id)
        project = self._repository.get_project(scene.project_id)
        profile = self._repository.get_visual_profile(project.id)
        approved_story_step = self._approved_story_step(scene)
        approved_story_step_id = (
            None if approved_story_step is None else str(approved_story_step.id)
        )
        prior_steps = [
            item
            for item in self._repository.list_steps(
                project_id=project.id,
                scene_id=scene.id,
            )
            if item.shot_card_id is None and item.operation_key == "director:shot-suggestions"
        ]
        unresolved = next(
            (item for item in prior_steps if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if unresolved is not None:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; do not repeat the paid request"
            )
        active = next(
            (
                item
                for item in prior_steps
                if item.status
                in {
                    StepStatus.PENDING,
                    StepStatus.SUBMITTING,
                    StepStatus.QUEUED,
                    StepStatus.RUNNING,
                }
            ),
            None,
        )
        if active is not None:
            raise ValueError(f"step {active.id} is still active")
        prompt = compile_shot_suggestion_prompt(
            project_title=project.title,
            scene_title=scene.draft.title,
            source_text=scene.draft.source_text,
            context_note=scene.draft.context_note,
            story_mode=scene.draft.story_mode.value,
            target_shot_count=scene.draft.target_shot_count,
            visual_profile=profile.draft,
        )
        input_hash = _hash_json(
            {
                "prompt": prompt,
                "project": str(project.id),
                "scene": scene.draft.model_dump(mode="json", by_alias=True),
                "approvedStoryStepId": approved_story_step_id,
                "visualProfileRevisionId": str(profile.id),
                "visualProfileHash": profile.profile_hash,
            }
        )
        attempt = self._repository.next_scene_attempt(
            scene_id=scene.id,
            operation_key="director:shot-suggestions",
        )
        step, _ = self._repository.create_step_with_prompt(
            project_id=project.id,
            scene_id=scene.id,
            shot_id=None,
            kind=StepKind.DIRECTOR,
            operation_key="director:shot-suggestions",
            attempt=attempt,
            provider=self._provider_name,
            model=self._director.model,
            input_hash=input_hash,
            input_snapshot={
                "scene": scene.draft.model_dump(mode="json", by_alias=True),
                "sourceHash": story_source_hash(scene.draft),
                "approvedStoryStepId": approved_story_step_id,
                "visualProfileRevisionId": str(profile.id),
                "visualProfileHash": profile.profile_hash,
            },
            purpose=PromptPurpose.DIRECTOR,
            prompt_text=prompt,
        )
        if step.status is StepStatus.SUCCEEDED:
            saved = step.input_snapshot.get("providerOutput")
            output = ShotSuggestionOutput.model_validate(saved)
            _validate_suggestion_count(output, scene.draft.target_shot_count)
            return SuggestionResult(step.id, output)
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._director.generate_structured(
                prompt=prompt,
                schema=ShotSuggestionOutput.model_json_schema(by_alias=True),
                output_name="ShotSuggestionOutput",
            )
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(
                step.id,
                status=status,
                error=_error_payload(exc),
            )
            raise
        except Exception as exc:
            error = _error_payload(exc)
            self._repository.update_step(step.id, status=StepStatus.FAILED, error=error)
            raise
        snapshot = {
            "scene": scene.draft.model_dump(mode="json", by_alias=True),
            "sourceHash": story_source_hash(scene.draft),
            "approvedStoryStepId": approved_story_step_id,
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "providerOutput": result.payload,
            "responseId": result.response_id,
            "requestHash": result.request_hash,
            "normalizationWarnings": [],
        }
        try:
            output = ShotSuggestionOutput.model_validate(result.payload)
            _validate_suggestion_count(output, scene.draft.target_shot_count)
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
                input_snapshot=snapshot,
            )
            raise
        self._repository.update_step(
            step.id,
            status=StepStatus.SUCCEEDED,
            input_snapshot=snapshot,
        )
        return SuggestionResult(step.id, output)

    def accept_suggestions(
        self,
        step_id: uuid.UUID,
        *,
        look_plan: SceneLookPlan | None,
        shots: tuple[ShotSuggestion, ...],
        apply_mode: str = "replace",
        source_shot_revisions: dict[uuid.UUID, int] | None = None,
    ) -> tuple[StoredShot, ...]:
        step = self._repository.get_step(step_id)
        if step.kind is not StepKind.DIRECTOR or step.scene_id is None:
            raise ValueError("step is not a scene shot-suggestion result")
        if step.status is not StepStatus.SUCCEEDED:
            raise ValueError("only a succeeded suggestion step can be accepted")
        scene = self._repository.get_scene(step.scene_id)
        if step.input_snapshot.get("sourceHash") != story_source_hash(scene.draft):
            raise RevisionConflictError(
                "scene story changed after storyboard generation; generate suggestions again"
            )
        if len(shots) != scene.draft.target_shot_count:
            raise ValueError(
                f"当前模式必须接受{scene.draft.target_shot_count}个视频片段"
            )
        drafts = tuple(
            ShotCardDraft(
                title=item.title,
                direction=item.direction,
                durationSeconds=item.suggested_duration_seconds,
                anchorMode=item.anchor_mode,
                sceneLookUsage=item.scene_look_usage,
            )
            for item in shots
        )
        accepted_output = {
            "lookPlan": (
                None
                if look_plan is None
                else look_plan.model_dump(mode="json", by_alias=True)
            ),
            "shots": [item.model_dump(mode="json", by_alias=True) for item in shots],
            "applyMode": apply_mode,
        }
        return self._repository.accept_scene_suggestions(
            step_id=step.id,
            drafts=drafts,
            look_plan=look_plan,
            accepted_output=accepted_output,
            apply_mode=apply_mode,
            source_shot_revisions=source_shot_revisions or {},
        )

    def _require_paid_director(self, allowed: bool, message: str) -> None:
        if not allowed:
            raise ValueError(message)
        if self._director is None:
            raise GatewayUnavailableError("Director gateway is not configured")

    def _assert_scene_stage_available(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID,
        operation_key: str,
    ) -> None:
        prior = [
            item
            for item in self._repository.list_steps(
                project_id=project_id,
                scene_id=scene_id,
            )
            if item.shot_card_id is None and item.operation_key == operation_key
        ]
        unresolved = next(
            (item for item in prior if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if unresolved is not None:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; do not repeat the paid request"
            )
        active = next(
            (
                item
                for item in prior
                if item.status
                in {
                    StepStatus.PENDING,
                    StepStatus.SUBMITTING,
                    StepStatus.QUEUED,
                    StepStatus.RUNNING,
                }
            ),
            None,
        )
        if active is not None:
            raise ValueError(f"step {active.id} is still active")

    def _run_scene_director_stage(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID,
        operation_key: str,
        prompt: str,
        snapshot: dict[str, Any],
        output_type: type[BaseModel],
        output_name: str,
    ) -> StoredStep:
        if self._director is None:
            raise GatewayUnavailableError("Director gateway is not configured")
        attempt = self._repository.next_scene_attempt(
            scene_id=scene_id,
            operation_key=operation_key,
        )
        step, _ = self._repository.create_step_with_prompt(
            project_id=project_id,
            scene_id=scene_id,
            shot_id=None,
            kind=StepKind.DIRECTOR,
            operation_key=operation_key,
            attempt=attempt,
            provider=self._provider_name,
            model=self._director.model,
            input_hash=_hash_json({"prompt": prompt, "snapshot": snapshot}),
            input_snapshot=snapshot,
            purpose=PromptPurpose.DIRECTOR,
            prompt_text=prompt,
        )
        if step.status is StepStatus.SUCCEEDED:
            output_type.model_validate(step.input_snapshot.get("providerOutput"))
            return step
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._director.generate_structured(
                prompt=prompt,
                schema=output_type.model_json_schema(by_alias=True),
                output_name=output_name,
            )
            output_type.model_validate(result.payload)
        except GatewayError as exc:
            status = (
                StepStatus.SUBMISSION_UNKNOWN
                if exc.submission_unknown
                else StepStatus.FAILED
            )
            self._repository.update_step(
                step.id,
                status=status,
                error=_error_payload(exc),
            )
            raise
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
            )
            raise
        return self._repository.update_step(
            step.id,
            status=StepStatus.SUCCEEDED,
            input_snapshot={
                **snapshot,
                "providerOutput": result.payload,
                "responseId": result.response_id,
                "requestHash": result.request_hash,
            },
        )

    def _adjacent_scenes(
        self,
        scene: StoredScene,
    ) -> tuple[StoredScene | None, StoredScene | None]:
        ordered = sorted(
            self._repository.list_scenes(scene.project_id),
            key=lambda item: item.order,
        )
        index = next(index for index, item in enumerate(ordered) if item.id == scene.id)
        previous = ordered[index - 1] if index > 0 else None
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        return previous, following

    def _validate_scene_stage_step(
        self,
        step: StoredStep,
        *,
        operation_key: str,
        allow_accepted: bool = False,
    ) -> None:
        if (
            step.kind is not StepKind.DIRECTOR
            or step.status is not StepStatus.SUCCEEDED
            or step.scene_id is None
            or step.shot_card_id is not None
            or step.operation_key != operation_key
        ):
            raise ValueError(f"step is not a succeeded {operation_key} result")
        if not allow_accepted and "acceptedAt" in step.input_snapshot:
            raise RevisionConflictError("creative workflow step has already been accepted")

    def _approved_story_step(self, scene: StoredScene) -> StoredStep | None:
        current_hash = story_source_hash(scene.draft)
        steps = reversed(
            self._repository.list_steps(
                project_id=scene.project_id,
                scene_id=scene.id,
            )
        )
        for step in steps:
            accepted = step.input_snapshot.get("acceptedOutput")
            if not isinstance(accepted, dict):
                continue
            if (
                step.operation_key == "director:story-rewrite"
                and accepted.get("acceptedStoryHash") == current_hash
            ):
                return step
            if (
                step.operation_key == "director:story-expansion"
                and accepted.get("acceptedStoryHash") == current_hash
            ):
                return step
            if (
                step.operation_key == "director:story-diagnosis"
                and accepted.get("preserveOriginal") is True
                and step.input_snapshot.get("sourceHash") == current_hash
            ):
                return step
        # Starting the paid storyboard action is itself the user's explicit
        # approval to use the currently saved Scene.sourceText.  A prior LLM
        # diagnosis or rewrite is optional and should not become a hidden gate.
        return None

    def assist_shot(
        self,
        shot_id: uuid.UUID,
        *,
        source_draft_revision: int,
        candidate_asset_ids: tuple[uuid.UUID, ...],
        allow_paid_generation: bool,
    ) -> ShotAssistanceResult:
        if not allow_paid_generation:
            raise ValueError("shot assistance requires explicit paid-generation permission")
        if self._director is None:
            raise GatewayUnavailableError("Director gateway is not configured")
        shot = self._repository.get_shot(shot_id)
        if shot.draft_revision != source_draft_revision:
            raise RevisionConflictError("片段草稿已更新，请基于最新版本重新分析")
        prior_steps = [
            item
            for item in self._repository.list_steps(
                project_id=shot.project_id,
                shot_id=shot.id,
            )
            if item.operation_key == "director:shot-assistance"
        ]
        unresolved = next(
            (item for item in prior_steps if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if unresolved is not None:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; do not repeat the paid request"
            )
        active = next(
            (
                item
                for item in prior_steps
                if item.status
                in {
                    StepStatus.PENDING,
                    StepStatus.SUBMITTING,
                    StepStatus.QUEUED,
                    StepStatus.RUNNING,
                }
            ),
            None,
        )
        if active is not None:
            raise ValueError(f"step {active.id} is still active")

        scene = self._repository.get_scene(shot.scene_id)
        project = self._repository.get_project(shot.project_id)
        candidates = self._shot_assist_candidates(shot, scene, project)
        candidate_by_id = {item.asset.id: item for item in candidates}
        unknown_ids = set(candidate_asset_ids).difference(candidate_by_id)
        if unknown_ids:
            raise ValueError(
                "shot-assistance references must come from the current shot input context"
            )

        requested_ids = set(candidate_asset_ids)
        selected_candidates: list[ShotAssistCandidate] = []
        seen_hashes: set[str] = set()
        for candidate in candidates:
            asset = candidate.asset
            if asset.id not in requested_ids:
                continue
            if (
                asset.media_type != "image"
                or asset.status not in {"approved", "ready"}
                or not asset.content_ready
            ):
                raise ValueError("shot-assistance reference is unavailable or not an image")
            if asset.sha256 in seen_hashes:
                continue
            seen_hashes.add(asset.sha256)
            selected_candidates.append(candidate)
        if len(selected_candidates) > 9:
            raise ValueError("shot assistance accepts at most 9 unique images")

        assets = tuple(item.asset for item in selected_candidates)
        profile = self._repository.get_visual_profile(shot.project_id)
        ordered_shots = sorted(
            self._repository.list_shots(shot.scene_id),
            key=lambda item: item.order,
        )
        current_index = next(
            index for index, item in enumerate(ordered_shots) if item.id == shot.id
        )
        previous = ordered_shots[current_index - 1] if current_index > 0 else None
        following = (
            ordered_shots[current_index + 1]
            if current_index + 1 < len(ordered_shots)
            else None
        )
        local_analysis = analyze_shot_draft(shot.draft)
        reference_manifest = tuple(
            f"@图片{index}={candidate.asset.display_name}；"
            f"assetId={candidate.asset.id}；来源={candidate.source_layer}；"
            f"当前职责={candidate.responsibility}"
            for index, candidate in enumerate(selected_candidates, 1)
        )
        prompt = compile_shot_assistance_prompt(
            project_title=project.title,
            scene_title=scene.draft.title,
            scene_text=scene.draft.source_text,
            current=shot.draft,
            previous=None if previous is None else previous.draft,
            following=None if following is None else following.draft,
            visual_profile=profile.draft,
            local_analysis=local_analysis,
            reference_manifest=reference_manifest,
        )
        snapshot = {
            "sourceDraftRevision": shot.draft_revision,
            "currentShot": shot.draft.model_dump(mode="json", by_alias=True),
            "previousShot": (
                None
                if previous is None
                else previous.draft.model_dump(mode="json", by_alias=True)
            ),
            "nextShot": (
                None
                if following is None
                else following.draft.model_dump(mode="json", by_alias=True)
            ),
            "localAnalysis": local_analysis.model_dump(mode="json", by_alias=True),
            "sceneStoryHash": story_source_hash(scene.draft),
            "visualProfileRevisionId": str(profile.id),
            "visualProfileHash": profile.profile_hash,
            "candidateAssets": [
                {
                    "assetId": str(candidate.asset.id),
                    "sha256": candidate.asset.sha256,
                    "ordinal": index,
                    "sourceLayer": candidate.source_layer,
                    "responsibility": candidate.responsibility,
                }
                for index, candidate in enumerate(selected_candidates, 1)
            ],
        }
        attempt = self._repository.next_attempt(
            shot_id=shot.id,
            operation_key="director:shot-assistance",
        )
        step, _ = self._repository.create_step_with_prompt(
            project_id=shot.project_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            kind=StepKind.DIRECTOR,
            operation_key="director:shot-assistance",
            attempt=attempt,
            provider=self._provider_name,
            model=self._director.analysis_model,
            input_hash=_hash_json({"prompt": prompt, "snapshot": snapshot}),
            input_snapshot=snapshot,
            purpose=PromptPurpose.DIRECTOR,
            prompt_text=prompt,
        )
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._director.analyze_structured(
                prompt=prompt,
                schema=ShotAssistAnalysis.model_json_schema(by_alias=True),
                output_name="ShotAssistAnalysis",
                image_paths=tuple(asset.require_path() for asset in assets),
            )
            analysis = ShotAssistAnalysis.model_validate(result.payload)
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
            raise
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
            )
            raise
        completed_snapshot = {
            **snapshot,
            "providerOutput": result.payload,
            "responseId": result.response_id,
            "requestHash": result.request_hash,
        }
        self._repository.update_step(
            step.id,
            status=StepStatus.SUCCEEDED,
            input_snapshot=completed_snapshot,
        )
        return ShotAssistanceResult(step.id, analysis)

    def shot_assist_context(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self._repository.get_shot(shot_id)
        scene = self._repository.get_scene(shot.scene_id)
        project = self._repository.get_project(shot.project_id)
        ordered_shots = sorted(
            self._repository.list_shots(shot.scene_id),
            key=lambda item: item.order,
        )
        current_index = next(
            index for index, item in enumerate(ordered_shots) if item.id == shot.id
        )
        tail_state = _previous_tail_state(self._repository, shot)
        candidates: list[dict[str, Any]] = []
        selected_ids: list[str] = []
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        for candidate in self._shot_assist_candidates(shot, scene, project):
            asset = candidate.asset
            duplicate = asset.id in seen_ids or asset.sha256 in seen_hashes
            if not duplicate:
                seen_ids.add(asset.id)
                seen_hashes.add(asset.sha256)
            available = (
                asset.media_type == "image"
                and asset.status in {"approved", "ready"}
                and asset.content_ready
            )
            if not duplicate and available and len(selected_ids) < 9:
                selected_ids.append(str(asset.id))
            candidates.append(
                {
                    "assetId": str(asset.id),
                    "displayName": asset.display_name,
                    "sha256": asset.sha256,
                    "sourceLayer": candidate.source_layer,
                    "responsibility": candidate.responsibility,
                    "contentReady": asset.content_ready,
                    "available": available,
                    "duplicate": duplicate,
                }
            )
        return {
            "shotId": str(shot.id),
            "sourceDraftRevision": shot.draft_revision,
            "model": (
                None if self._director is None else self._director.analysis_model
            ),
            "localAnalysis": analyze_shot_draft(shot.draft).model_dump(
                mode="json", by_alias=True
            ),
            "previousShot": (
                None
                if current_index == 0
                else {
                    "id": str(ordered_shots[current_index - 1].id),
                    "title": ordered_shots[current_index - 1].draft.title,
                }
            ),
            "nextShot": (
                None
                if current_index + 1 >= len(ordered_shots)
                else {
                    "id": str(ordered_shots[current_index + 1].id),
                    "title": ordered_shots[current_index + 1].draft.title,
                }
            ),
            "previousTail": _tail_state_json(tail_state),
            "candidates": candidates,
            "defaultCandidateAssetIds": selected_ids,
            "warnings": (
                ["可用候选图片超过9张，请在付费分析前取消部分选择"]
                if sum(1 for item in candidates if item["available"] and not item["duplicate"])
                > 9
                else []
            ),
        }

    def _shot_assist_candidates(
        self,
        shot: StoredShot,
        scene: StoredScene,
        project: StoredProject,
    ) -> tuple[ShotAssistCandidate, ...]:
        candidate_ids: list[uuid.UUID] = []
        if shot.selected_anchor_asset_id is not None:
            candidate_ids.append(shot.selected_anchor_asset_id)
        candidate_ids.extend(
            binding.asset_id
            for binding in shot.draft.reference_bindings
            if binding.asset_id != scene.selected_look_asset_id
        )
        if scene.selected_look_asset_id is not None:
            candidate_ids.append(scene.selected_look_asset_id)
        candidate_ids.extend(
            binding.asset_id
            for binding in _project_reference_bindings(
                self._repository,
                shot,
                scene,
                project,
            )
            if binding.asset_id != scene.selected_look_asset_id
        )
        tail_state = _previous_tail_state(self._repository, shot)
        if tail_state.active is not None:
            candidate_ids.append(tail_state.active.id)

        seen_ids: set[uuid.UUID] = set()
        candidates: list[ShotAssistCandidate] = []
        for asset_id in candidate_ids:
            if asset_id in seen_ids:
                continue
            seen_ids.add(asset_id)
            asset = self._repository.get_asset(asset_id)
            if asset.project_id not in {None, shot.project_id}:
                raise ValueError("shot-assistance reference belongs to another project")
            candidates.append(
                ShotAssistCandidate(
                    asset=asset,
                    source_layer=_shot_assist_asset_layer(shot, scene, project, asset),
                    responsibility=_shot_assist_asset_responsibility(shot, asset),
                )
            )
        return tuple(candidates)

    def list_shot_assistance(self, shot_id: uuid.UUID) -> list[dict[str, Any]]:
        shot = self._repository.get_shot(shot_id)
        records: list[dict[str, Any]] = []
        for step in reversed(
            self._repository.list_steps(project_id=shot.project_id, shot_id=shot.id)
        ):
            if step.operation_key != "director:shot-assistance":
                continue
            source_revision = step.input_snapshot.get("sourceDraftRevision")
            accepted_revision = step.input_snapshot.get("acceptedDraftRevision")
            records.append(
                {
                    "stepId": str(step.id),
                    "status": step.status.value,
                    "sourceDraftRevision": source_revision,
                    "stale": (
                        accepted_revision != shot.draft_revision
                        if step.input_snapshot.get("acceptedAt")
                        else source_revision != shot.draft_revision
                    ),
                    "analysis": step.input_snapshot.get("providerOutput"),
                    "acceptedOutput": step.input_snapshot.get("acceptedOutput"),
                    "acceptedAnchorBrief": step.input_snapshot.get("acceptedAnchorBrief"),
                    "acceptedPatchAt": step.input_snapshot.get("acceptedPatchAt"),
                    "acceptedAnchorBriefAt": step.input_snapshot.get(
                        "acceptedAnchorBriefAt"
                    ),
                    "acceptedAt": step.input_snapshot.get("acceptedAt"),
                    "error": step.error,
                    "createdAt": (
                        None if step.created_at is None else step.created_at.isoformat()
                    ),
                }
            )
        return records

    def accept_shot_assistance(
        self,
        step_id: uuid.UUID,
        *,
        source_draft_revision: int,
        patch: ShotAssistPatch | None,
        accepted_anchor_brief: str | None = None,
    ) -> StoredShot:
        step = self._repository.get_step(step_id)
        if (
            step.kind is not StepKind.DIRECTOR
            or step.status is not StepStatus.SUCCEEDED
            or step.operation_key != "director:shot-assistance"
            or step.shot_card_id is None
        ):
            raise ValueError("step is not a succeeded shot-assistance analysis")
        shot = self._repository.get_shot(step.shot_card_id)
        if step.input_snapshot.get("sourceDraftRevision") != source_draft_revision:
            raise RevisionConflictError("接受请求与分析来源版本不一致")
        if shot.draft_revision != source_draft_revision:
            raise RevisionConflictError("片段草稿已更新，旧分析不能再接受")
        analysis = ShotAssistAnalysis.model_validate(
            step.input_snapshot.get("providerOutput")
        )
        if patch is None and not (accepted_anchor_brief or "").strip():
            raise ValueError("至少接受一项片段修改或开场静态画面稿")
        selected = (
            {}
            if patch is None
            else patch.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        proposed = (
            {}
            if analysis.patch is None
            else analysis.patch.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
        )
        proposed.setdefault(
            "durationSeconds",
            analysis.pacing_plan.recommended_duration_seconds,
        )
        proposed.setdefault(
            "sceneLookUsage",
            analysis.recommended_scene_look_usage.value,
        )
        proposed.setdefault(
            "anchorMode",
            analysis.recommended_anchor_mode.value,
        )
        creative_bodies = {
            body
            for body in (
                analysis.creative_body,
                *(item.body for item in analysis.creative_alternatives),
            )
            if body is not None
        }
        for key, value in selected.items():
            if key == "direction" and value in creative_bodies:
                continue
            if proposed.get(key) != value:
                raise ValueError("只能接受该次LLM分析实际提出的字段值")
        static_brief = None
        if accepted_anchor_brief is not None:
            static_brief = accepted_anchor_brief.strip()
            if not static_brief:
                raise ValueError("开场静态画面稿不能为空")
            if len(static_brief) > 4_000:
                raise ValueError("开场静态画面稿不能超过4000字")
            if analysis.anchor_brief is None:
                raise ValueError("该次分析没有提出开场静态画面稿，请重新审稿")
        return self._repository.accept_shot_assistance(
            step_id=step.id,
            source_draft_revision=source_draft_revision,
            patch=patch,
            accepted_anchor_brief=static_brief,
        )

    def save_anchor_brief(
        self,
        shot_id: uuid.UUID,
        *,
        source_draft_revision: int,
        brief: str,
    ) -> StoredStep:
        normalized = brief.strip()
        if not normalized:
            raise ValueError("开场静态画面稿不能为空")
        if len(normalized) > 4_000:
            raise ValueError("开场静态画面稿不能超过4000字")
        shot = self._repository.get_shot(shot_id)
        if shot.draft_revision != source_draft_revision:
            raise RevisionConflictError("片段草稿已更新，请基于最新版本重新保存开场静态画面稿")
        return self._repository.save_manual_anchor_brief(
            shot_id=shot_id,
            source_draft_revision=source_draft_revision,
            brief=normalized,
            input_hash=_hash_json(
                {
                    "shotId": str(shot_id),
                    "sourceDraftRevision": source_draft_revision,
                    "brief": normalized,
                }
            ),
        )


class ShotProductionService:
    def __init__(
        self,
        *,
        repository: ShotQueueStore,
        gateway: MediaGateway | None,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        frame_extractor: FrameExtractor | None,
        provider_name: str,
        resolution: str,
        runtime_preflight: RuntimePreflight | None = None,
        enable_video_advice: bool = True,
        poll_interval_seconds: float = 10,
        task_timeout_seconds: float = 1800,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._asset_store = asset_store
        self._media_probe = media_probe
        self._frame_extractor = frame_extractor
        self._provider_name = provider_name
        self._resolution = resolution
        self._runtime_preflight = runtime_preflight
        self._enable_video_advice = enable_video_advice
        self._poll_interval_seconds = poll_interval_seconds
        self._task_timeout_seconds = task_timeout_seconds

    @property
    def _video_resolution(self) -> str:
        if self._runtime_preflight is not None:
            return self._runtime_preflight.video_resolution
        return self._resolution

    @property
    def _semantic_review_enabled(self) -> bool:
        if self._runtime_preflight is not None:
            return self._runtime_preflight.semantic_review_enabled
        return self._enable_video_advice

    def _assert_scene_assets_ready(self, scene_id: uuid.UUID) -> None:
        scene = self._repository.get_scene(scene_id)
        if not _scene_continuity_context(scene):
            return
        readiness = _scene_asset_readiness(self._repository, scene.id)
        if readiness.can_compile_shot_prompt:
            return
        raise ValueError("场景资产未就绪：" + "；".join(readiness.blockers))

    def _generation_clip_production_context(
        self,
        shot: StoredShot,
    ) -> dict[str, Any] | None:
        load_context = getattr(
            self._repository,
            "generation_clip_production_context",
            None,
        )
        if not callable(load_context):
            return None
        context = load_context(shot.id)
        if not context.get("managedByStoryboard"):
            return None
        if not context.get("generationPlanApproved"):
            raise ValueError("当前真实生成片段的 Agent 生成编排尚未批准")
        if not context.get("productionPackageApproved"):
            raise ValueError("当前真实生成片段的生产分镜包尚未批准")
        if not context.get("lineageCurrent"):
            raise ValueError("生产分镜包或编译 Prompt 已过期，请回到画布重新编译并批准")
        if not context.get("compiledPrompt"):
            raise ValueError("当前真实生成片段缺少已批准的编译 Prompt")
        return context

    def _compiled_production_reference_pairs(
        self,
        production_context: dict[str, Any],
        *,
        target: ReferenceTarget,
    ) -> tuple[tuple[ReferenceBinding, StoredAsset], ...]:
        """Restore the approved provider manifest without re-resolving or reordering it."""

        role_map = {
            "identity": ReferenceRole.IDENTITY,
            "appearance": ReferenceRole.IDENTITY,
            "style": ReferenceRole.STYLE,
            "scene": ReferenceRole.SCENE,
            "environment": ReferenceRole.SCENE,
            "wardrobe": ReferenceRole.SCENE,
            "prop": ReferenceRole.PROP,
            "composition": ReferenceRole.COMPOSITION,
            "scale": ReferenceRole.COMPOSITION,
        }
        pairs: list[tuple[ReferenceBinding, StoredAsset]] = []
        for item in production_context.get("referenceBindings") or []:
            if not isinstance(item, dict) or item.get("providerIncluded") is not True:
                continue
            asset = self._repository.get_asset(uuid.UUID(str(item["assetId"])))
            pairs.append(
                (
                    ReferenceBinding(
                        assetId=asset.id,
                        usage=ReferenceUsage.GENERATION_REFERENCE,
                        role=role_map.get(
                            str(item.get("role") or item.get("semanticRole") or ""),
                            ReferenceRole.COMPOSITION,
                        ),
                        applyTo=target,
                    ),
                    asset,
                )
            )
        return tuple(pairs)

    def import_reference(
        self,
        *,
        project_id: uuid.UUID,
        path: Path,
        usage: str,
        role: str,
        display_name: str | None = None,
        scope: str = "project",
        scene_id: uuid.UUID | None = None,
        purpose: VisualAssetPurpose | None = None,
    ) -> StoredAsset:
        if usage not in {"approved_anchor", "generation_reference"}:
            raise ValueError("unsupported reference usage")
        if scope not in {"project", "scene"}:
            raise ValueError("uploaded reference scope must be project or scene")
        if scope == "scene":
            if scene_id is None or self._repository.get_scene(scene_id).project_id != project_id:
                raise ValueError("scene reference must belong to the selected project")
        elif scene_id is not None:
            raise ValueError("project reference cannot be attached to a scene")
        expected_role = None if purpose is None else _reference_role_for_purpose(purpose).value
        if expected_role is not None and role != expected_role:
            raise ValueError("uploaded visual reference role must match its declared purpose")
        if purpose is None and role not in {"style", "prop", "composition"}:
            raise ValueError(
                "uploaded generic references may only use style, prop, or composition; "
                "identity and scene responsibilities come from managed sources"
            )
        landed = self._asset_store.import_local(path)
        qc = self._media_probe.inspect_image(landed.path)
        return self._repository.add_asset(
            landed=landed,
            role="external_reference",
            media_type="image",
            scope=scope,
            status="approved" if usage == "approved_anchor" else "ready",
            project_id=project_id,
            scene_id=scene_id,
            shot_id=None,
            step_id=None,
            semantic_key=f"external:{landed.sha256[:16]}",
            metadata={
                "usage": usage,
                "referenceRole": role,
                **({} if purpose is None else {"referencePurpose": purpose.value}),
                "displayName": (display_name or path.stem).strip(),
                "qc": qc,
            },
        )

    def get_scene_look_draft(self, scene_id: uuid.UUID) -> dict[str, Any]:
        scene = self._repository.get_scene_look_draft(scene_id)
        draft = scene.look_draft or self._default_scene_look_draft(scene)
        return {
            "sceneId": str(scene.id),
            "revision": scene.look_draft_revision,
            "draft": draft.model_dump(mode="json", by_alias=True),
        }

    def save_scene_look_draft(
        self,
        scene_id: uuid.UUID,
        *,
        expected_revision: int,
        draft: SceneLookDraft,
    ) -> dict[str, Any]:
        scene = self._repository.save_scene_look_draft(
            scene_id,
            expected_revision=expected_revision,
            draft=draft,
        )
        if scene.look_draft is None:
            raise RuntimeError("saved scene look draft was not returned")
        return {
            "sceneId": str(scene.id),
            "revision": scene.look_draft_revision,
            "draft": scene.look_draft.model_dump(mode="json", by_alias=True),
        }

    def _compile_scene_look_generation_input(
        self,
        scene_id: uuid.UUID,
        *,
        strict: bool,
        regeneration_instruction: str | None = None,
    ) -> tuple[SceneLookInputSet, Any, dict[str, Any], str]:
        """Compile the exact ordered Scene Look request shared by preview and execution."""

        inputs = self._scene_look_inputs(scene_id, strict=strict)
        project = self._repository.get_project(inputs.scene.project_id)
        prompt = compile_scene_look_prompt(
            project_title=project.title,
            scene_title=inputs.scene.draft.title,
            scene_text=inputs.scene.draft.source_text,
            look_plan=inputs.draft.look_plan,
            visual_profile=inputs.profile.draft,
            reference_descriptions=inputs.descriptions,
            regeneration_instruction=regeneration_instruction,
        )
        references = [
            {
                "assetId": str(asset.id),
                "sha256": asset.sha256,
                "semanticKey": asset.semantic_key,
                "semanticRole": binding.purpose.value,
                "purpose": binding.purpose.value,
                "instruction": binding.instruction,
                "ordinal": index,
                "locked": True,
                "providerIncluded": index <= 14,
                "providerSlot": f"reference_image_{index}" if index <= 14 else None,
                "omissionReason": (
                    None
                    if index <= 14
                    else "必需引用超出 Seedream 14 张上限，生成已阻断"
                ),
                "origin": "scene_look_draft",
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "evidenceLevel": "frozen",
            }
            for index, (binding, asset) in enumerate(
                zip(inputs.bindings, inputs.assets, strict=True),
                1,
            )
        ]
        snapshot = {
            "sceneId": str(inputs.scene.id),
            "lookDraftRevision": inputs.scene.look_draft_revision,
            "visualProfileRevisionId": str(inputs.profile.id),
            "visualProfileRevision": inputs.profile.revision,
            "lookPlan": inputs.draft.look_plan.model_dump(mode="json", by_alias=True),
            "references": references,
            "referenceAssetIds": [item["assetId"] for item in references],
            "promptSha256": hashlib.sha256(prompt.text.encode("utf-8")).hexdigest(),
            "provider": self._provider_name,
            "model": (
                "unconfigured" if self._gateway is None else self._gateway.image_model
            ),
            "capabilityRevision": "seedream-reference-images-v1",
        }
        return inputs, prompt, snapshot, _hash_json({"prompt": prompt.text, "snapshot": snapshot})

    def preview_scene_look_prompt(self, scene_id: uuid.UUID) -> dict[str, Any]:
        inputs, prompt, snapshot, input_hash = self._compile_scene_look_generation_input(
            scene_id,
            strict=False,
        )
        readiness = _scene_asset_readiness_if_available(self._repository, scene_id)
        return {
            "prompt": prompt.text,
            "charCount": prompt.char_count,
            "utf8Bytes": prompt.utf8_bytes,
            "referenceCount": sum(
                reference["providerIncluded"] for reference in snapshot["references"]
            ),
            "references": [
                {**reference, "index": reference["ordinal"], "contentReady": True}
                for reference in snapshot["references"]
            ],
            "warnings": [
                *inputs.warnings,
                *(() if readiness is None else _scene_look_asset_blockers(readiness)),
            ],
            "visualProfileRevisionId": str(inputs.profile.id),
            "visualProfileRevision": inputs.profile.revision,
            "draftRevision": inputs.scene.look_draft_revision,
            "provider": self._provider_name,
            "model": (
                "unconfigured" if self._gateway is None else self._gateway.image_model
            ),
            "capabilityRevision": snapshot["capabilityRevision"],
            "inputHash": input_hash,
        }

    def validate_scene_look_request(self, scene_id: uuid.UUID, draft_revision: int) -> None:
        scene = self._repository.get_scene(scene_id)
        if scene.look_draft is None:
            raise RevisionConflictError("请先保存场景视觉基准草稿再生成")
        if scene.look_draft_revision != draft_revision:
            raise RevisionConflictError("场景视觉基准草稿已更新，请重新预览后再生成")
        readiness = _scene_asset_readiness_if_available(self._repository, scene_id)
        if readiness is not None:
            blockers = _scene_look_asset_blockers(readiness)
            if blockers:
                raise ValueError("场景视觉资产未就绪：" + "；".join(blockers))
        self._scene_look_inputs(scene_id, strict=True)

    def preview_shot_prompt(
        self,
        shot_id: uuid.UUID,
        *,
        target: ReferenceTarget = ReferenceTarget.VIDEO,
        regeneration_instruction: str | None = None,
    ) -> dict[str, Any]:
        if target is ReferenceTarget.BOTH:
            raise ValueError("prompt preview target must be anchor or video")
        read_model_loader = getattr(self._repository, "shot_generation_read_model", None)
        read_model = read_model_loader(shot_id) if callable(read_model_loader) else None
        shot = read_model.shot if read_model is not None else self._repository.get_shot(shot_id)
        self._assert_scene_assets_ready(shot.scene_id)
        compilation = self._shot_compilation_context(
            shot,
            shot_read_model=read_model,
        )
        try:
            production_context = self._generation_clip_production_context(shot)
        except ValueError:
            production_context = None
        spec = self._compile_shot_generation(
            shot,
            target=target,
            regeneration_instruction=regeneration_instruction,
            require_ready=False,
            compilation=compilation,
            production_context=production_context,
        )
        return self._prompt_preview_projection(
            shot=shot,
            compilation=compilation,
            spec=spec,
            production_context=production_context,
            previous_tail=_tail_state_json(
                _previous_tail_state_from_shot_read_model(read_model)
                if read_model is not None
                else _previous_tail_state(self._repository, shot)
            ),
        )

    def _prompt_preview_projection(
        self,
        *,
        shot: StoredShot,
        compilation: ShotCompilationContext,
        spec: ShotGenerationSpec,
        production_context: dict[str, Any] | None,
        previous_tail: dict[str, Any],
    ) -> dict[str, Any]:
        local_analysis = analyze_shot_draft(shot.draft)
        references = self._spec_reference_projection(
            shot=shot,
            scene=compilation.scene,
            project=compilation.project,
            spec=spec,
            production_context=production_context,
        )
        prompt_preview = _creator_prompt_preview(spec.prompt.text)
        return {
            "target": spec.target.value,
            "providerInputMode": spec.provider_input_mode.value,
            "ready": not spec.blockers,
            "blockers": list(spec.blockers),
            "inputHash": spec.input_hash,
            "sourceRevisionHash": spec.source_revision_hash,
            "prompt": prompt_preview,
            "creativeBody": _creator_prompt_preview(spec.creative_body),
            "systemShell": spec.system_shell,
            "charCount": spec.prompt.char_count,
            "utf8Bytes": spec.prompt.utf8_bytes,
            "inputPlan": (
                None
                if spec.input_plan is None
                else spec.input_plan.model_dump(mode="json")
            ),
            "draftRevision": shot.draft_revision,
            "anchorMode": shot.draft.anchor_mode.value,
            "sceneLookUsage": shot.draft.scene_look_usage.value,
            "localAnalysis": local_analysis.model_dump(mode="json", by_alias=True),
            "qualitativePacing": local_analysis.qualitative_pacing,
            "linkWarnings": list(spec.warnings),
            "actualInputCount": spec.actual_input_count,
            "references": references,
            "actualInputs": references,
            "upstreamLineage": (
                []
                if production_context is None
                else list(production_context.get("referenceBindings") or [])
            ),
            "providerReferencePolicy": (
                "approved_anchor_only_baked_lineage"
                if spec.target is ReferenceTarget.VIDEO
                and shot.selected_anchor_asset_id is not None
                and production_context is not None
                else "compiled_production_references"
                if production_context is not None
                else "draft_reference_resolution"
            ),
            "legacyPromptLabels": prompt_preview != spec.prompt.text,
            "previousTail": previous_tail,
        }

    @staticmethod
    def _spec_reference_projection(
        *,
        shot: StoredShot,
        scene: StoredScene,
        project: StoredProject,
        spec: ShotGenerationSpec,
        production_context: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        manifest_by_asset = {
            str(item.get("assetId")): item
            for item in (
                []
                if production_context is None
                else production_context.get("referenceBindings") or []
            )
            if isinstance(item, dict) and item.get("assetId")
        }
        return [
            {
                "index": index,
                "assetId": str(asset.id),
                "displayName": asset.display_name,
                "promptAlias": f"@图片{index}",
                "subjectLabel": _asset_subject_label(asset),
                "sourceLayer": _shot_assist_asset_layer(shot, scene, project, asset),
                "responsibility": spec.descriptions[index - 1],
                "contentReady": asset.content_ready,
                "sha256": asset.sha256,
                "purpose": manifest_by_asset.get(str(asset.id), {}).get("purpose"),
                "providerIncluded": True,
                "providerSlot": (
                    manifest_by_asset.get(str(asset.id), {}).get("providerSlot")
                    or f"reference_image_{index}"
                ),
                "locked": manifest_by_asset.get(str(asset.id), {}).get("locked", False),
            }
            for index, asset in enumerate(spec.sources, 1)
        ]

    def production_board(self, project_id: uuid.UUID) -> dict[str, Any]:
        """Build the production board from the same specs used for submission."""

        read_model = self._repository.project_read_model(project_id)
        graph = project_graph_projection(read_model)
        scenes_by_id = {item.id: item for item in read_model.scenes}
        shots_by_id = {item.id: item for item in read_model.shots}
        assets_by_id = {item["id"]: item for item in graph["assets"]}
        for graph_scene in graph["scenes"]:
            for graph_shot in graph_scene["shots"]:
                assets_by_id.update({item["id"]: item for item in graph_shot["assets"]})
        active_statuses = {"pending", "submitting", "queued", "running"}
        actionable_statuses = active_statuses | {
            "awaiting_review",
            "submission_unknown",
            "failed",
        }
        scene_summaries: list[dict[str, Any]] = []
        for scene_data in graph["scenes"]:
            scene = scenes_by_id[uuid.UUID(scene_data["id"])]
            scene_look_versions = [
                item
                for item in graph["assets"]
                if item.get("sceneId") == scene_data["id"]
                and item.get("role") == "scene_look"
            ]
            shot_summaries: list[dict[str, Any]] = []
            for shot_data in scene_data["shots"]:
                shot = shots_by_id[uuid.UUID(shot_data["id"])]
                compilation = self._shot_compilation_context(
                    shot,
                    read_model=read_model,
                )
                try:
                    production_context = self._generation_clip_production_context(shot)
                except ValueError:
                    production_context = None
                spec = self._compile_shot_generation(
                    shot,
                    target=ReferenceTarget.VIDEO,
                    require_ready=False,
                    compilation=compilation,
                    production_context=production_context,
                )
                references = self._spec_reference_projection(
                    shot=shot,
                    scene=scene,
                    project=read_model.project,
                    spec=spec,
                    production_context=production_context,
                )
                latest_attempts_by_operation: dict[str, dict[str, Any]] = {}
                for item in reversed(shot_data["attempts"]):
                    latest_attempts_by_operation.setdefault(item["operationKey"], item)
                latest_attempts = list(latest_attempts_by_operation.values())
                active_attempts = [
                    item
                    for item in latest_attempts
                    if item["status"] in active_statuses
                ]
                active_anchor = next(
                    (
                        item
                        for item in active_attempts
                        if item["operationKey"] == "image:anchor"
                    ),
                    None,
                )
                active_video = next(
                    (
                        item
                        for item in active_attempts
                        if item["operationKey"] in {"video:shot", "video:range-edit"}
                    ),
                    None,
                )
                anchor_assets = [
                    item for item in shot_data["assets"] if item["role"] == "shot_anchor"
                ]
                video_assets = [
                    item
                    for item in shot_data["assets"]
                    if item["role"] in {"shot_video", "shot_video_edit"}
                ]
                selected_anchor = assets_by_id.get(shot_data.get("selectedAnchorAssetId"))
                if (
                    selected_anchor is None
                    and spec.provider_input_mode is ProviderInputMode.FIRST_FRAME
                    and spec.sources
                ):
                    # An adopted previous tail is stored as an approved-anchor
                    # binding, not in selected_anchor_asset_id.  The compiled spec
                    # is the authority for the effective Provider opening input.
                    selected_anchor = assets_by_id.get(str(spec.sources[0].id))
                opening_asset_id = selected_anchor["id"] if selected_anchor else None
                selected_video = assets_by_id.get(shot_data.get("selectedVideoAssetId"))
                candidate_video = next(
                    (
                        item
                        for item in reversed(video_assets)
                        if item["status"] == "candidate"
                    ),
                    None,
                )
                selected_video_step = next(
                    (
                        item
                        for item in shot_data["attempts"]
                        if selected_video
                        and item["id"] == selected_video.get("producingStepId")
                    ),
                    None,
                )
                selected_snapshot = (
                    selected_video_step.get("inputSnapshot", {})
                    if selected_video_step
                    else {}
                )
                generated_spec_hash = selected_snapshot.get("sourceRevisionHash")
                stale = bool(
                    selected_video
                    and generated_spec_hash is not None
                    and generated_spec_hash != spec.source_revision_hash
                )
                needs_anchor = (
                    shot.draft.anchor_mode in {AnchorMode.GENERATE, AnchorMode.EXISTING}
                    and spec.provider_input_mode is not ProviderInputMode.FIRST_FRAME
                )
                if active_anchor:
                    state, next_action = "generating_anchor", "open_task"
                    state_label, action_label = "开场图生成中", "查看生成任务"
                elif active_video:
                    state, next_action = "generating_video", "open_task"
                    state_label, action_label = "视频生成中", "查看生成任务"
                elif selected_video and stale:
                    state, next_action = "stale", "open_versions"
                    state_label, action_label = "基于旧输入", "查看并决定是否重做"
                elif selected_video:
                    state, next_action = "approved", "open_versions"
                    state_label, action_label = "已批准", "查看视频版本"
                elif candidate_video:
                    state, next_action = "awaiting_review", "review_media"
                    state_label, action_label = "等待审核", "审核视频版本"
                elif needs_anchor:
                    state, next_action = "needs_opening", "generate_anchor"
                    state_label = "待设计开场"
                    action_label = (
                        "生成片段开场图"
                        if shot.draft.anchor_mode is AnchorMode.GENERATE
                        else "选择已有开场图"
                    )
                elif spec.blockers:
                    state, next_action = "blocked", "fix_inputs"
                    state_label, action_label = "生成条件未完成", "检查生成输入"
                else:
                    state, next_action = "ready_video", "generate_video"
                    state_label, action_label = "可以生成视频", "生成视频片段"

                counts = {
                    "custom": sum(
                        item["sourceLayer"] == "shot"
                        and item["assetId"] != opening_asset_id
                        for item in references
                    ),
                    "scene": sum(item["sourceLayer"] == "scene_look" for item in references),
                    "project": sum(item["sourceLayer"] == "project" for item in references),
                    "opening": int(spec.provider_input_mode is ProviderInputMode.FIRST_FRAME),
                    "person": sum(
                        str(assets_by_id.get(item["assetId"], {}).get("semanticKey") or "")
                        .startswith("person:")
                        for item in references
                    ),
                    "cat": sum(
                        str(assets_by_id.get(item["assetId"], {}).get("semanticKey") or "")
                        .startswith("cat:")
                        for item in references
                    ),
                    "style": sum(
                        str(assets_by_id.get(item["assetId"], {}).get("semanticKey") or "")
                        .startswith("style:")
                        for item in references
                    ),
                    "prop": sum(
                        item["sourceLayer"] not in {"project", "scene_look"}
                        and item["assetId"] != opening_asset_id
                        for item in references
                    ),
                    "total": spec.actual_input_count,
                }
                preview_asset = (
                    selected_video
                    or selected_anchor
                    or candidate_video
                    or (anchor_assets[-1] if anchor_assets else None)
                    or assets_by_id.get(scene_data.get("selectedLookAssetId"))
                )
                latest_actionable = next(
                    (
                        item
                        for item in latest_attempts
                        if item["status"] in actionable_statuses
                    ),
                    None,
                )
                upstream_lineage: list[str] = []
                if selected_anchor and selected_anchor.get("producingStepId"):
                    anchor_step = next(
                        (
                            item
                            for item in shot_data["attempts"]
                            if item["id"] == selected_anchor["producingStepId"]
                        ),
                        None,
                    )
                    source_ids = (anchor_step or {}).get("inputSnapshot", {}).get(
                        "sourceAssetIds",
                        [],
                    )
                    if isinstance(source_ids, list):
                        upstream_lineage = [str(item) for item in source_ids]
                shot_summaries.append(
                    {
                        "shotId": shot_data["id"],
                        "sceneId": scene_data["id"],
                        "state": state,
                        "stateLabel": state_label,
                        "nextAction": next_action,
                        "primaryActionLabel": action_label,
                        "providerInputMode": spec.provider_input_mode.value,
                        "actualInputCount": spec.actual_input_count,
                        "actualInputs": references,
                        "upstreamLineage": upstream_lineage,
                        "ready": spec.ready,
                        "blockers": list(spec.blockers),
                        "referenceCounts": counts,
                        "anchorVersionCount": len(anchor_assets),
                        "videoVersionCount": len(video_assets),
                        "activeTaskCount": len(active_attempts),
                        "latestActionableTask": latest_actionable,
                        "previewAssetId": preview_asset["id"] if preview_asset else None,
                        "previewMediaType": (
                            preview_asset["mediaType"] if preview_asset else None
                        ),
                        "usesSceneLook": any(
                            item["sourceLayer"] == "scene_look" for item in references
                        ),
                        "inputHash": spec.input_hash,
                        "currentInputHash": spec.input_hash,
                    }
                )
            scene_summaries.append(
                {
                    "sceneId": scene_data["id"],
                    "selectedLookAssetId": scene_data.get("selectedLookAssetId"),
                    "lookVersionCount": len(scene_look_versions),
                    "lookStatus": (
                        assets_by_id.get(
                            scene_data.get("selectedLookAssetId"),
                            {},
                        ).get("status")
                        if scene_data.get("selectedLookAssetId")
                        else "optional"
                    ),
                    "lookRecommended": bool(
                        scene.draft.look_plan and scene.draft.look_plan.image_recommended
                    ),
                    "lookRecommendationReason": (
                        scene.draft.look_plan.recommendation_reason
                        if scene.draft.look_plan
                        else None
                    ),
                    "shots": shot_summaries,
                }
            )
        return {
            "projectId": str(project_id),
            "projectGraph": graph,
            "scenes": scene_summaries,
        }

    def generation_workspace(self, shot_id: uuid.UUID) -> dict[str, Any]:
        """Load and compile a shot workspace from one batch read."""

        read_model = self._repository.shot_generation_read_model(shot_id)
        shot = read_model.shot
        compilation = self._shot_compilation_context(
            shot,
            shot_read_model=read_model,
        )
        previous_tail_state = _previous_tail_state_from_shot_read_model(read_model)
        previous_tail = _tail_state_json(previous_tail_state)
        try:
            production_context = self._generation_clip_production_context(shot)
        except ValueError:
            production_context = None
        anchor_spec = self._compile_shot_generation(
            shot,
            target=ReferenceTarget.ANCHOR,
            require_ready=False,
            compilation=compilation,
            production_context=production_context,
        )
        video_spec = self._compile_shot_generation(
            shot,
            target=ReferenceTarget.VIDEO,
            require_ready=False,
            compilation=compilation,
            production_context=production_context,
        )
        anchor_preview = self._prompt_preview_projection(
            shot=shot,
            compilation=compilation,
            spec=anchor_spec,
            production_context=production_context,
            previous_tail=previous_tail,
        )
        video_preview = self._prompt_preview_projection(
            shot=shot,
            compilation=compilation,
            spec=video_spec,
            production_context=production_context,
            previous_tail=previous_tail,
        )
        prompts_by_step = {item.step_id: item for item in read_model.prompts}
        reviews_by_step: dict[uuid.UUID, list[Any]] = {}
        for review in read_model.reviews:
            reviews_by_step.setdefault(review.step_id, []).append(review)
        shot_steps = list(read_model.steps)
        attempt_projections = [
            step_projection(
                item,
                prompt=prompts_by_step.get(item.id),
                reviews=reviews_by_step.get(item.id, []),
            )
            for item in shot_steps
        ]
        shot_assets = [
            item for item in read_model.assets if item.shot_card_id == shot.id
        ]
        assets_by_id = {str(item.id): item for item in read_model.assets}

        def reference_slots(
            preview: dict[str, Any],
            target: str,
        ) -> list[dict[str, Any]]:
            grouped: dict[str, list[dict[str, Any]]] = {
                "person": [],
                "cat": [],
                "style": [],
                "scene": [],
                "prop": [],
                "opening": [],
                "custom": [],
            }
            for reference in preview["references"]:
                asset = assets_by_id[reference["assetId"]]
                semantic_key = asset.semantic_key or ""
                if reference["assetId"] == str(shot.selected_anchor_asset_id):
                    key = "opening"
                elif reference["sourceLayer"] == "scene_look":
                    key = "scene"
                elif semantic_key.startswith("person:"):
                    key = "person"
                elif semantic_key.startswith("cat:"):
                    key = "cat"
                elif semantic_key.startswith("style:"):
                    key = "style"
                elif reference["sourceLayer"] == "shot":
                    key = "custom"
                else:
                    key = "prop"
                grouped[key].append(
                    {**reference, "asset": asset_projection(asset)}
                )
            labels = {
                "person": "人物身份",
                "cat": "猫咪身份",
                "style": "系列画风",
                "scene": "场景视觉基准",
                "prop": "道具与构图",
                "opening": "批准开场图",
                "custom": "片段专用素材",
            }
            return [
                {"key": key, "label": labels[key], "target": target, "items": items}
                for key, items in grouped.items()
                if items
            ]

        def version_projection(asset: StoredAsset) -> dict[str, Any]:
            step = next((item for item in shot_steps if item.id == asset.step_id), None)
            return {
                **asset_projection(asset),
                "attempt": None if step is None else step.attempt,
                "prompt": (
                    None
                    if step is None or prompts_by_step.get(step.id) is None
                    else step_projection(
                        step,
                        prompt=prompts_by_step[step.id],
                    )["prompt"]
                ),
                "inputSnapshot": {} if step is None else step.input_snapshot,
            }

        anchor_versions = [
            version_projection(item)
            for item in shot_assets
            if item.role in {"shot_anchor", "shot_tail_frame"}
        ]
        video_versions = [
            version_projection(item)
            for item in shot_assets
            if item.role in {"shot_video", "shot_video_edit"}
        ]
        upstream_lineage: list[dict[str, Any]] = []
        if shot.selected_anchor_asset_id is not None:
            selected_anchor = compilation.assets_by_id.get(shot.selected_anchor_asset_id)
            anchor_step = next(
                (
                    item
                    for item in shot_steps
                    if selected_anchor is not None and item.id == selected_anchor.step_id
                ),
                None,
            )
            source_ids = (
                anchor_step.input_snapshot.get("sourceAssetIds", [])
                if anchor_step is not None
                else []
            )
            if isinstance(source_ids, list):
                upstream_lineage = [
                    asset_projection(assets_by_id[str(asset_id)])
                    for asset_id in source_ids
                    if str(asset_id) in assets_by_id
                ]
        active_statuses = {
            StepStatus.PENDING,
            StepStatus.SUBMITTING,
            StepStatus.QUEUED,
            StepStatus.RUNNING,
            StepStatus.SUBMISSION_UNKNOWN,
            StepStatus.FAILED,
        }
        latest_steps_by_operation: dict[str, StoredStep] = {}
        for item in reversed(shot_steps):
            latest_steps_by_operation.setdefault(item.operation_key, item)
        latest_step_ids = {item.id for item in latest_steps_by_operation.values()}

        brief_records = self._anchor_brief_records(shot, compilation=compilation)
        current_brief = next(
            (item for item in reversed(brief_records) if not item.stale),
            None,
        )
        anchor_brief_versions = [
            {
                "stepId": str(item.step_id),
                "version": index,
                "source": item.source,
                "brief": item.text,
                "sourceDraftRevision": item.source_draft_revision,
                "acceptedDraftRevision": item.accepted_draft_revision,
                "acceptedAt": item.accepted_at,
                "createdAt": item.created_at,
                "stale": item.stale,
                "current": current_brief is not None
                and item.step_id == current_brief.step_id,
            }
            for index, item in enumerate(brief_records, 1)
        ]

        running_statuses = {
            StepStatus.PENDING,
            StepStatus.SUBMITTING,
            StepStatus.QUEUED,
            StepStatus.RUNNING,
        }
        active_anchor_step = next(
            (
                item
                for item in reversed(shot_steps)
                if item.operation_key == "image:anchor"
                and item.status in running_statuses
            ),
            None,
        )
        active_video_step = next(
            (
                item
                for item in reversed(shot_steps)
                if item.operation_key in {"video:shot", "video:range-edit"}
                and item.status in running_statuses
            ),
            None,
        )
        active_assistance_step = next(
            (
                item
                for item in reversed(shot_steps)
                if item.operation_key == "director:shot-assistance"
                and item.input_snapshot.get("sourceDraftRevision")
                == shot.draft_revision
                and item.status in running_statuses
            ),
            None,
        )
        reviewable_assistance = next(
            (
                item
                for item in reversed(shot_steps)
                if item.operation_key == "director:shot-assistance"
                and item.status is StepStatus.SUCCEEDED
                and item.input_snapshot.get("sourceDraftRevision")
                == shot.draft_revision
                and isinstance(item.input_snapshot.get("providerOutput"), dict)
                and not str(
                    item.input_snapshot.get("acceptedAnchorBrief") or ""
                ).strip()
            ),
            None,
        )
        anchor_candidate = next(
            (
                item
                for item in reversed(shot_assets)
                if item.role == "shot_anchor" and item.status == "candidate"
            ),
            None,
        )
        video_candidate = next(
            (
                item
                for item in reversed(shot_assets)
                if item.role in {"shot_video", "shot_video_edit"}
                and item.status == "candidate"
            ),
            None,
        )

        if shot.draft.anchor_mode is AnchorMode.GENERATE and shot.selected_anchor_asset_id is None:
            if active_anchor_step is not None:
                next_action = "anchor_generating"
                next_label = "开场图生成中"
                next_blockers = ["开场图正在后台生成，可离开本页面继续其他工作"]
            elif anchor_candidate is not None:
                next_action = "review_anchor"
                next_label = "审核开场图"
                next_blockers = []
            elif current_brief is None and active_assistance_step is not None:
                next_action = "assistance_running"
                next_label = "LLM 正在生成建议"
                next_blockers = ["分析完成后可逐项查看；也可以现在直接填写手工静态稿"]
            elif current_brief is None and reviewable_assistance is not None:
                next_action = "review_assistance"
                next_label = "查看并接受开场建议"
                next_blockers = []
            elif current_brief is None:
                next_action = "write_anchor_brief"
                next_label = "保存开场静态画面稿"
                next_blockers = ["请先描述当前片段动作开始前的稳定画面"]
            elif anchor_spec.ready:
                next_action = "generate_anchor"
                next_label = "生成片段开场图"
                next_blockers = []
            else:
                next_action = "fix_inputs"
                next_label = "修复开场图输入"
                next_blockers = list(anchor_spec.blockers)
        elif active_video_step is not None:
            next_action = "video_generating"
            next_label = "视频生成中"
            next_blockers = ["视频正在后台生成，可离开本页面继续其他工作"]
        elif shot.selected_video_asset_id is not None:
            next_action = "completed"
            next_label = "当前片段已批准"
            next_blockers = []
        elif video_candidate is not None:
            next_action = "review_video"
            next_label = "审核视频"
            next_blockers = []
        elif video_spec.ready:
            next_action = "generate_video"
            next_label = "生成视频片段"
            next_blockers = []
        else:
            next_action = "fix_inputs"
            next_label = "修复视频输入"
            next_blockers = list(video_spec.blockers)

        anchor_brief = (
            None
            if current_brief is None
            else next(
                item for item in anchor_brief_versions if item["current"]
            )
        )
        return {
            "projectId": str(shot.project_id),
            "shot": shot_projection(
                shot,
                assets=shot_assets,
                attempts=attempt_projections,
            ),
            "scene": {
                "id": str(compilation.scene.id),
                "title": compilation.scene.draft.title,
                "selectedLookAssetId": (
                    None
                    if compilation.scene.selected_look_asset_id is None
                    else str(compilation.scene.selected_look_asset_id)
                ),
            },
            "assets": [asset_projection(item) for item in read_model.assets],
            "generationSpec": {
                "providerInputMode": video_spec.provider_input_mode.value,
                "actualInputCount": video_spec.actual_input_count,
                "actualInputs": video_preview["actualInputs"],
                "ready": video_spec.ready,
                "blockers": list(video_spec.blockers),
                "warnings": list(video_spec.warnings),
                "inputHash": video_spec.input_hash,
                "sourceRevisionHash": video_spec.source_revision_hash,
            },
            "anchorPreview": anchor_preview,
            "videoPreview": video_preview,
            "actualInputs": video_preview["actualInputs"],
            "upstreamLineage": upstream_lineage,
            "referenceSlots": {
                "anchor": reference_slots(anchor_preview, "anchor"),
                "video": reference_slots(video_preview, "video"),
            },
            "anchorVersions": anchor_versions,
            "videoVersions": video_versions,
            "anchorBrief": anchor_brief,
            "anchorBriefVersions": anchor_brief_versions,
            "nextAction": next_action,
            "nextActionLabel": next_label,
            "blockers": next_blockers,
            "previousTail": previous_tail,
            "activeTasks": [
                projection
                for step, projection in zip(
                    shot_steps,
                    attempt_projections,
                    strict=True,
                )
                if step.id in latest_step_ids and step.status in active_statuses
            ],
        }

    def validate_anchor_request(
        self,
        shot_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        expected_input_hash: str | None = None,
        regeneration_instruction: str | None = None,
    ) -> None:
        self._require_paid_gateway(allow_paid_generation)
        shot = self._repository.get_shot(shot_id)
        production_context = self._generation_clip_production_context(shot)
        self._assert_scene_assets_ready(shot.scene_id)
        spec = self._compile_shot_generation(
            shot,
            target=ReferenceTarget.ANCHOR,
            regeneration_instruction=regeneration_instruction,
            require_ready=True,
            production_context=production_context,
        )
        self._assert_expected_input_hash(spec, expected_input_hash)

    def validate_video_request(
        self,
        shot_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        expected_input_hash: str | None = None,
        regeneration_instruction: str | None = None,
    ) -> None:
        if self._runtime_preflight is not None:
            self._runtime_preflight.validate_for_video_generation(
                allow_paid_generation=allow_paid_generation
            )
        self._require_paid_gateway(allow_paid_generation)
        shot = self._repository.get_shot(shot_id)
        production_context = self._generation_clip_production_context(shot)
        self._assert_scene_assets_ready(shot.scene_id)
        spec = self._compile_shot_generation(
            shot,
            target=ReferenceTarget.VIDEO,
            regeneration_instruction=regeneration_instruction,
            require_ready=True,
            production_context=production_context,
        )
        self._assert_expected_input_hash(spec, expected_input_hash)

    def generate_anchor(
        self,
        shot_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        regenerate: bool = False,
        reason: str | None = None,
        expected_input_hash: str | None = None,
        request_idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        shot = self._repository.get_shot(shot_id)
        self._require_paid_gateway(allow_paid_generation)
        production_context = self._generation_clip_production_context(shot)
        self._assert_scene_assets_ready(shot.scene_id)
        spec = self._compile_shot_generation(
            shot,
            target=ReferenceTarget.ANCHOR,
            regeneration_instruction=reason if regenerate else None,
            require_ready=True,
            production_context=production_context,
        )
        self._assert_expected_input_hash(spec, expected_input_hash)
        step, _ = self._new_paid_step(
            shot,
            kind=StepKind.IMAGE,
            operation_key="image:anchor",
            model=self._gateway.image_model,
            purpose=PromptPurpose.IMAGE,
            prompt=spec.prompt.text,
            snapshot=spec.snapshot,
            force_new_attempt=regenerate,
            retry_reason=reason,
            request_idempotency_key=request_idempotency_key,
        )
        if step.status is not StepStatus.PENDING:
            existing_asset = next(
                (
                    item
                    for item in self._repository.list_assets(shot_id=shot.id)
                    if item.step_id == step.id and item.media_type == "image"
                ),
                None,
            )
            return {
                "stepId": str(step.id),
                "assetId": None if existing_asset is None else str(existing_asset.id),
                "reused": True,
                "status": step.status.value,
            }
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._gateway.generate_image(
                prompt=spec.prompt.text,
                reference_paths=tuple(item.require_path() for item in spec.sources),
            )
            landed = self._asset_store.download(result.url, suffix=".png")
            qc = self._media_probe.inspect_image(landed.path)
            asset = self._repository.add_asset(
                landed=landed,
                role="shot_anchor",
                media_type="image",
                scope="shot",
                status="candidate",
                project_id=shot.project_id,
                scene_id=shot.scene_id,
                shot_id=shot.id,
                step_id=step.id,
                semantic_key=f"shot:{shot.id}:anchor",
                metadata={
                    "qc": qc,
                    "providerUrl": result.url,
                    "syntheticFixture": self._provider_name == "local-fake-provider",
                    "generationInputHash": spec.input_hash,
                    "referenceManifest": spec.snapshot.get(
                        "providerReferenceManifest", []
                    ),
                    "upstreamLineage": spec.snapshot.get(
                        "productionReferenceBindings", []
                    ),
                    "providerOrderEvidence": "frozen",
                    "advisories": (
                        ["画面边缘大面积接近纯黑，请人工确认是否存在异常黑边"]
                        if qc.get("blackBorderDetected")
                        else []
                    ),
                },
            )
            self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
            self._record_anchor_review(shot, step, asset)
            return {"stepId": str(step.id), "assetId": str(asset.id), "status": "awaiting_review"}
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(
                step.id,
                status=status,
                error=_error_payload(exc),
            )
            raise
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
            )
            raise

    def generate_scene_look(
        self,
        scene_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        draft_revision: int,
        regenerate: bool = False,
        reason: str | None = None,
        expected_input_hash: str | None = None,
    ) -> dict[str, Any]:
        self.validate_scene_look_request(scene_id, draft_revision)
        self._require_paid_gateway(allow_paid_generation)
        if expected_input_hash is None:
            raise ValueError("付费生成必须先查看服务端 Scene Look 输入预览")
        inputs, prompt, snapshot, input_hash = self._compile_scene_look_generation_input(
            scene_id,
            strict=True,
            regeneration_instruction=reason if regenerate else None,
        )
        if input_hash != expected_input_hash:
            raise RevisionConflictError("Scene Look 输入已变化，请重新查看引用、Prompt 与费用")
        scene = inputs.scene
        project = self._repository.get_project(scene.project_id)
        references = inputs.assets
        operation_key = "image:scene-look"
        operation_steps = [
            item
            for item in self._repository.list_steps(
                project_id=project.id,
                scene_id=scene.id,
            )
            if item.shot_card_id is None and item.operation_key == operation_key
        ]
        unresolved = next(
            (item for item in operation_steps if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if regenerate and unresolved is not None:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; reconcile it before regeneration"
            )
        if regenerate:
            previous = operation_steps[-1] if operation_steps else None
            if previous is not None and previous.status in {
                StepStatus.PENDING,
                StepStatus.SUBMITTING,
                StepStatus.QUEUED,
                StepStatus.RUNNING,
            }:
                raise ValueError(f"step {previous.id} is still active and cannot be regenerated")
            attempt = self._repository.next_scene_attempt(
                scene_id=scene.id,
                operation_key=operation_key,
            )
            snapshot = {
                **snapshot,
                "retryOfStepId": None if previous is None else str(previous.id),
                "retryReason": reason or "explicit regeneration",
            }
        else:
            existing = [
                item
                for item in operation_steps
                if item.input_snapshot.get("inputHash") == input_hash
            ]
            attempt = (
                existing[-1].attempt
                if existing
                else self._repository.next_scene_attempt(
                    scene_id=scene.id,
                    operation_key=operation_key,
                )
            )
        snapshot = {**snapshot, "inputHash": input_hash}
        step, _ = self._repository.create_step_with_prompt(
            project_id=project.id,
            scene_id=scene.id,
            shot_id=None,
            kind=StepKind.IMAGE,
            operation_key=operation_key,
            attempt=attempt,
            provider=self._provider_name,
            model=self._gateway.image_model,
            input_hash=input_hash,
            input_snapshot=snapshot,
            purpose=PromptPurpose.IMAGE,
            prompt_text=prompt.text,
        )
        if step.status is not StepStatus.PENDING:
            existing_asset = next(
                (
                    item
                    for item in self._repository.list_assets(project_id=project.id)
                    if item.step_id == step.id and item.role == "scene_look"
                ),
                None,
            )
            return {
                "stepId": str(step.id),
                "assetId": None if existing_asset is None else str(existing_asset.id),
                "reused": True,
                "status": step.status.value,
            }
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._gateway.generate_image(
                prompt=prompt.text,
                reference_paths=tuple(item.require_path() for item in references),
            )
            landed = self._asset_store.download(result.url, suffix=".png")
            qc = self._media_probe.inspect_image(landed.path)
            asset = self._repository.add_asset(
                landed=landed,
                role="scene_look",
                media_type="image",
                scope="scene",
                status="candidate",
                project_id=project.id,
                scene_id=scene.id,
                shot_id=None,
                step_id=step.id,
                semantic_key=f"scene:{scene.id}:look:{step.attempt}",
                metadata={
                    "qc": qc,
                    "providerUrl": result.url,
                    "syntheticFixture": self._provider_name == "local-fake-provider",
                    "visualProfileRevisionId": str(inputs.profile.id),
                    "visualProfileRevision": inputs.profile.revision,
                    "lookDraftRevision": scene.look_draft_revision,
                    "promptSha256": snapshot["promptSha256"],
                    "referenceAssetIds": snapshot["referenceAssetIds"],
                    "generationInputHash": input_hash,
                    "referenceManifest": snapshot["references"],
                    "providerOrderEvidence": "frozen",
                },
            )
            self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
            return {
                "stepId": str(step.id),
                "assetId": str(asset.id),
                "status": "awaiting_review",
            }
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
            raise
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
            )
            raise

    def validate_reference_image_request(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None,
        scope: str,
        draft: ReferenceImageDraft,
        allow_paid_generation: bool,
    ) -> str:
        self._require_paid_gateway(allow_paid_generation)
        return self._compile_reference_image(
            project_id=project_id,
            scene_id=scene_id,
            scope=scope,
            draft=draft,
        ).operation_key

    def generate_reference_image(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None,
        scope: str,
        draft: ReferenceImageDraft,
        allow_paid_generation: bool,
        regenerate: bool = False,
        reason: str | None = None,
        expected_input_hash: str | None = None,
    ) -> dict[str, Any]:
        self._require_paid_gateway(allow_paid_generation)
        spec = self._compile_reference_image(
            project_id=project_id,
            scene_id=scene_id,
            scope=scope,
            draft=draft,
            regeneration_instruction=reason if regenerate else None,
        )
        if expected_input_hash is None:
            raise ValueError("付费生成必须先查看服务端参考图输入预览")
        if spec.input_hash != expected_input_hash:
            raise RevisionConflictError("参考图输入已变化，请重新查看有序引用、Prompt 与费用")
        steps = [
            item
            for item in self._repository.list_steps(project_id=project_id)
            if item.operation_key == spec.operation_key
            and item.scene_id == scene_id
            and item.shot_card_id is None
        ]
        unresolved = next(
            (item for item in steps if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if regenerate and unresolved is not None:
            raise ValueError(
                f"step {unresolved.id} is submission_unknown; reconcile it before regeneration"
            )
        previous = steps[-1] if steps else None
        if regenerate and previous is not None and previous.status in {
            StepStatus.PENDING,
            StepStatus.SUBMITTING,
            StepStatus.QUEUED,
            StepStatus.RUNNING,
        }:
            raise ValueError(f"step {previous.id} is still active and cannot be regenerated")
        existing = [
            item
            for item in steps
            if item.input_snapshot.get("inputHash") == spec.input_hash
        ]
        if regenerate:
            attempt = (
                self._repository.next_scene_attempt(
                    scene_id=scene_id,
                    operation_key=spec.operation_key,
                )
                if scene_id is not None
                else self._repository.next_project_attempt(
                    project_id=project_id,
                    operation_key=spec.operation_key,
                )
            )
            snapshot = {
                **spec.snapshot,
                "retryOfStepId": None if previous is None else str(previous.id),
                "retryReason": reason or "explicit regeneration",
            }
        else:
            attempt = (
                existing[-1].attempt
                if existing
                else (
                    self._repository.next_scene_attempt(
                        scene_id=scene_id,
                        operation_key=spec.operation_key,
                    )
                    if scene_id is not None
                    else self._repository.next_project_attempt(
                        project_id=project_id,
                        operation_key=spec.operation_key,
                    )
                )
            )
            snapshot = spec.snapshot
        snapshot = {**snapshot, "inputHash": spec.input_hash}
        step, _ = self._repository.create_step_with_prompt(
            project_id=project_id,
            scene_id=scene_id,
            shot_id=None,
            kind=StepKind.IMAGE,
            operation_key=spec.operation_key,
            attempt=attempt,
            provider=self._provider_name,
            model=self._gateway.image_model,
            input_hash=spec.input_hash,
            input_snapshot=snapshot,
            purpose=PromptPurpose.IMAGE,
            prompt_text=spec.prompt.text,
        )
        if step.status is not StepStatus.PENDING:
            asset = next(
                (
                    item
                    for item in self._repository.list_assets(project_id=project_id)
                    if item.step_id == step.id and item.role == "generated_reference"
                ),
                None,
            )
            return {
                "stepId": str(step.id),
                "assetId": None if asset is None else str(asset.id),
                "reused": True,
                "status": step.status.value,
            }
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            result = self._gateway.generate_image(
                prompt=spec.prompt.text,
                reference_paths=tuple(item.require_path() for item in spec.sources),
            )
            landed = self._asset_store.download(result.url, suffix=".png")
            qc = self._media_probe.inspect_image(landed.path)
            role = _reference_role_for_purpose(spec.draft.purpose)
            asset = self._repository.add_asset(
                landed=landed,
                role="generated_reference",
                media_type="image",
                scope=spec.scope,
                status="candidate",
                project_id=project_id,
                scene_id=scene_id,
                shot_id=None,
                step_id=step.id,
                semantic_key=(
                    f"{spec.scope}:{scene_id or project_id}:reference:"
                    f"{spec.draft.purpose.value}:{step.attempt}"
                ),
                metadata={
                    "displayName": spec.draft.display_name,
                    "referenceRole": role.value,
                    "referencePurpose": spec.draft.purpose.value,
                    "purpose": spec.draft.purpose.value,
                    "creativePrompt": spec.draft.prompt,
                    "promptSha256": hashlib.sha256(
                        spec.prompt.text.encode("utf-8")
                    ).hexdigest(),
                    "sourceRevision": spec.draft.source_revision,
                    "referenceAssetIds": [str(item.id) for item in spec.sources],
                    "generationInputHash": spec.input_hash,
                    "referenceManifest": [
                        {
                            "assetId": str(source.id),
                            "sha256": source.sha256,
                            "semanticRole": spec.draft.purpose.value,
                            "purpose": spec.draft.purpose.value,
                            "instruction": spec.descriptions[index - 1],
                            "ordinal": index,
                            "locked": False,
                            "providerIncluded": True,
                            "providerSlot": f"reference_image_{index}",
                            "omissionReason": None,
                            "origin": "visual_asset_plan",
                            "contentUrl": f"/api/v1/assets/{source.id}/content",
                            "evidenceLevel": "frozen",
                        }
                        for index, source in enumerate(spec.sources, 1)
                    ],
                    "providerOrderEvidence": "frozen",
                    "providerModel": result.model,
                    "providerUrl": result.url,
                    "syntheticFixture": self._provider_name == "local-fake-provider",
                    "qc": qc,
                },
            )
            self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
            return {
                "stepId": str(step.id),
                "assetId": str(asset.id),
                "status": "awaiting_review",
            }
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
            raise
        except Exception as exc:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error=_error_payload(exc),
            )
            raise

    def _compile_reference_image(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None,
        scope: str,
        draft: ReferenceImageDraft,
        regeneration_instruction: str | None = None,
    ) -> ReferenceImageSpec:
        if scope not in {"project", "scene"}:
            raise ValueError("generated reference scope must be project or scene")
        if scope == "scene" and scene_id is None:
            raise ValueError("scene reference image requires a scene")
        if scope == "project" and scene_id is not None:
            raise ValueError("project reference image cannot be attached to a scene")
        project = self._repository.get_project(project_id)
        scene = None if scene_id is None else self._repository.get_scene(scene_id)
        if scene is not None and scene.project_id != project.id:
            raise ValueError("scene belongs to another project")
        self._validate_visual_asset_plan_reference(
            project_id=project.id,
            scene_id=scene_id,
            scope=scope,
            draft=draft,
        )
        profile = self._repository.get_visual_profile(project.id)
        available = {
            item.id: item
            for item in self._repository.list_assets(
                project_id=project.id,
                include_canon=True,
            )
        }
        unknown = set(draft.reference_asset_ids).difference(available)
        if unknown:
            raise ValueError("reference image inputs must belong to this project or Canon")
        selected_sources: list[StoredAsset] = []
        seen_hashes: set[str] = set()
        for asset_id in draft.reference_asset_ids:
            asset = available[asset_id]
            if (
                asset.media_type != "image"
                or asset.status not in {"approved", "ready"}
                or not asset.content_ready
            ):
                raise ValueError(f"reference image input {asset.id} is unavailable")
            if asset.sha256 in seen_hashes:
                continue
            seen_hashes.add(asset.sha256)
            selected_sources.append(asset)
        sources = list(
            _order_reference_image_sources(
                draft.purpose,
                tuple(selected_sources),
            )
        )
        if len(sources) > 14:
            raise ValueError("Seedream最多允许14张参考图")
        descriptions = tuple(
            _reference_image_input_description(
                index,
                purpose=draft.purpose,
                asset=asset,
            )
            for index, asset in enumerate(sources, 1)
        )
        prompt = compile_reference_image_prompt(
            purpose=draft.purpose,
            display_name=draft.display_name,
            creative_prompt=draft.prompt,
            reference_descriptions=descriptions,
            visual_profile=profile.draft,
            regeneration_instruction=regeneration_instruction,
        )
        identity = hashlib.sha256(
            f"{draft.source_revision}|{draft.purpose.value}|{draft.display_name}".encode(
                "utf-8"
            )
        ).hexdigest()[:12]
        operation_key = f"image:reference:{draft.purpose.value}:{identity}"
        snapshot = {
            "scope": scope,
            "projectId": str(project.id),
            "sceneId": None if scene is None else str(scene.id),
            "displayName": draft.display_name,
            "purpose": draft.purpose.value,
            "creativePrompt": draft.prompt,
            "referenceRole": _reference_role_for_purpose(draft.purpose).value,
            "sourceRevision": draft.source_revision,
            "visualProfileRevisionId": str(profile.id),
            "referenceAssetIds": [str(item.id) for item in sources],
            "references": [
                {"assetId": str(item.id), "sha256": item.sha256, "ordinal": index}
                for index, item in enumerate(sources, 1)
            ],
        }
        input_hash = _hash_json({"prompt": prompt.text, "snapshot": snapshot})
        return ReferenceImageSpec(
            project=project,
            scene=scene,
            profile=profile,
            draft=draft,
            scope=scope,
            operation_key=operation_key,
            sources=tuple(sources),
            descriptions=descriptions,
            prompt=prompt,
            snapshot=snapshot,
            input_hash=input_hash,
        )

    def preview_reference_image(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None,
        scope: str,
        draft: ReferenceImageDraft,
        regeneration_instruction: str | None = None,
    ) -> dict[str, Any]:
        spec = self._compile_reference_image(
            project_id=project_id,
            scene_id=scene_id,
            scope=scope,
            draft=draft,
            regeneration_instruction=regeneration_instruction,
        )
        references = [
            {
                "assetId": str(asset.id),
                "sha256": asset.sha256,
                "semanticRole": spec.draft.purpose.value,
                "purpose": spec.draft.purpose.value,
                "instruction": spec.descriptions[index - 1],
                "ordinal": index,
                "locked": False,
                "providerIncluded": True,
                "providerSlot": f"reference_image_{index}",
                "omissionReason": None,
                "origin": "visual_asset_plan",
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "evidenceLevel": "frozen",
            }
            for index, asset in enumerate(spec.sources, 1)
        ]
        return {
            "provider": self._provider_name,
            "model": self._gateway.image_model,
            "mode": "image_to_image" if references else "text_to_image",
            "capabilityRevision": "seedream-reference-images-v1",
            "prompt": spec.prompt.text,
            "references": references,
            "blockers": [],
            "warnings": [],
            "estimatedCostMicros": None,
            "inputHash": spec.input_hash,
            "operationKey": spec.operation_key,
        }

    def _validate_visual_asset_plan_reference(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None,
        scope: str,
        draft: ReferenceImageDraft,
    ) -> None:
        try:
            source_step_id = uuid.UUID(draft.source_revision)
        except ValueError:
            return
        step = self._repository.get_step(source_step_id)
        if step.operation_key != "director:visual-asset-plan":
            return
        if step.project_id != project_id or step.scene_id is None:
            raise ValueError("视觉资产生成来源不属于当前项目场景")
        accepted = step.input_snapshot.get("acceptedOutput")
        if not isinstance(accepted, dict):
            raise RevisionConflictError("请先人工接受该视觉资产规划，再生成参考图")
        storyboard_context = self._repository.storyboard_production_context(step.scene_id)
        if (
            not storyboard_context.get("structureApproved")
            or not storyboard_context.get("generationPlanApproved")
            or storyboard_context.get("storyboardRevisionId")
            != step.input_snapshot.get("storyboardRevisionId")
            or storyboard_context.get("structureHash")
            != step.input_snapshot.get("structureHash")
            or storyboard_context.get("generationPlanId")
            != step.input_snapshot.get("generationPlanId")
            or storyboard_context.get("generationPlanHash")
            != step.input_snapshot.get("generationPlanHash")
        ):
            raise RevisionConflictError(
                "分镜结构或生成编排已更新，请保留当前规划记录并建立新规划"
            )
        plan = AcceptedVisualAssetPlan.model_validate(accepted)
        matching = [
            item
            for item in plan.selections
            if item.display_name == draft.display_name
            and item.purpose is draft.purpose
            and item.action.value == "generate"
        ]
        if len(matching) != 1:
            raise ValueError("当前图片不属于已接受规划中的唯一生成项")
        selection = matching[0]
        expected_scene_id = step.scene_id if selection.target_scope.value == "scene" else None
        if scope != selection.target_scope.value or scene_id != expected_scene_id:
            raise ValueError("图片作用范围与已接受的视觉资产规划不一致")
        if (
            selection.prompt != draft.prompt
            or selection.reference_asset_ids != draft.reference_asset_ids
        ):
            raise RevisionConflictError("视觉资产选择稿已变化，请重新保存规划后再生成")

    def generate_video(
        self,
        shot_id: uuid.UUID,
        *,
        allow_paid_generation: bool,
        regenerate: bool = False,
        reason: str | None = None,
        expected_input_hash: str | None = None,
        request_idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if self._runtime_preflight is not None:
            self._runtime_preflight.validate_for_video_generation(
                allow_paid_generation=allow_paid_generation
            )
        self._require_paid_gateway(allow_paid_generation)
        shot = self._repository.get_shot(shot_id)
        production_context = self._generation_clip_production_context(shot)
        self._assert_scene_assets_ready(shot.scene_id)
        spec = self._compile_shot_generation(
            shot,
            target=ReferenceTarget.VIDEO,
            regeneration_instruction=reason if regenerate else None,
            require_ready=True,
            production_context=production_context,
        )
        self._assert_expected_input_hash(spec, expected_input_hash)
        if spec.input_plan is None:
            raise RuntimeError("video generation specification has no input plan")
        step, _ = self._new_paid_step(
            shot,
            kind=StepKind.VIDEO,
            operation_key="video:shot",
            model=self._gateway.video_model,
            purpose=PromptPurpose.VIDEO,
            prompt=spec.prompt.text,
            snapshot=spec.snapshot,
            force_new_attempt=regenerate,
            retry_reason=reason,
            request_idempotency_key=request_idempotency_key,
        )
        if step.status is not StepStatus.PENDING:
            return {"stepId": str(step.id), "reused": True, "status": step.status.value}
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=spec.prompt.text,
                input_plan=spec.input_plan,
                input_sources=tuple(item.require_path() for item in spec.sources),
            )
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
            raise
        return self._continue_submitted_video(step, task)

    def resume_step(self, step_id: uuid.UUID, *, wait: bool = False) -> dict[str, Any]:
        self._require_gateway()
        step = self._repository.get_step(step_id)
        if step.kind is not StepKind.VIDEO or not step.provider_task_id:
            raise ValueError("only a video step with a provider task can be resumed")
        deadline = time.monotonic() + (self._task_timeout_seconds if wait else 0)
        while True:
            task = self._gateway.get_video_task(step.provider_task_id)
            normalized = _provider_step_status(task.status)
            if normalized in {StepStatus.QUEUED, StepStatus.RUNNING}:
                if normalized is not step.status:
                    self._repository.update_step(step.id, status=normalized)
                    step = self._repository.get_step(step.id)
                if not wait or time.monotonic() >= deadline:
                    return {
                        "stepId": str(step.id),
                        "status": normalized.value,
                        "taskId": task.task_id,
                    }
                time.sleep(self._poll_interval_seconds)
                continue
            if normalized is StepStatus.FAILED:
                self._repository.update_step(
                    step.id,
                    status=StepStatus.FAILED,
                    error={"code": task.error_code, "message": task.error_message},
                )
                return {"stepId": str(step.id), "status": "failed", "taskId": task.task_id}
            if not task.video_url:
                raise ValueError("succeeded provider task has no downloadable video URL")
            return self._land_video(
                step,
                task.video_url,
                last_frame_url=task.last_frame_url,
            )

    def _continue_submitted_video(
        self,
        step: StoredStep,
        task: VideoTaskResult,
    ) -> dict[str, Any]:
        submitted_status = _provider_step_status(task.status)
        if submitted_status is StepStatus.FAILED:
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                task_id=task.task_id,
                error={"code": task.error_code, "message": task.error_message},
            )
            return {"stepId": str(step.id), "status": "failed", "taskId": task.task_id}

        # Receiving a provider task ID is the durable submission boundary.  Even
        # when the provider already reports running or succeeded, record queued
        # first so recovery observes the same honest lifecycle as async tasks.
        self._repository.update_step(step.id, status=StepStatus.QUEUED, task_id=task.task_id)
        if submitted_status is StepStatus.RUNNING:
            self._repository.update_step(step.id, status=StepStatus.RUNNING)
        if submitted_status is StepStatus.SUCCEEDED and task.video_url:
            return self._land_video(
                self._repository.get_step(step.id),
                task.video_url,
                last_frame_url=task.last_frame_url,
            )
        return self.resume_step(step.id, wait=False)

    def decide_asset(
        self,
        asset_id: uuid.UUID,
        *,
        decision: str,
        reason: str | None,
        select: bool,
    ) -> dict[str, Any]:
        asset = self._repository.decide_asset(asset_id, decision=decision, reason=reason)
        if decision == "approved" and select:
            if asset.role == "scene_look" and asset.scene_id is not None:
                self._repository.select_scene_look_asset(asset.scene_id, asset.id)
            elif asset.shot_card_id is not None:
                kind = "anchor" if asset.media_type == "image" else "video"
                self._repository.select_shot_asset(asset.shot_card_id, kind=kind, asset_id=asset.id)
        tail_frame: dict[str, Any] | None = None
        if (
            decision == "approved"
            and asset.media_type == "video"
            and asset.shot_card_id is not None
        ):
            try:
                tail = self._ensure_tail_frame(asset, shot_id=asset.shot_card_id)
                tail_frame = {"status": "ready", "assetId": str(tail.id)}
            except Exception as exc:
                tail_frame = {
                    "status": "unavailable",
                    "error": _error_payload(exc),
                }
        return {
            "assetId": str(asset.id),
            "decision": decision,
            "selected": select,
            "tailFrame": tail_frame,
        }

    def adopt_previous_tail_anchor(self, shot_id: uuid.UUID) -> StoredShot:
        shot = self._repository.get_shot(shot_id)
        ordered = sorted(
            self._repository.list_shots(shot.scene_id),
            key=lambda item: item.order,
        )
        current_index = next(index for index, item in enumerate(ordered) if item.id == shot.id)
        if current_index == 0:
            raise ValueError("第一个片段没有可采用的上一片段尾帧")
        previous = ordered[current_index - 1]
        if previous.selected_video_asset_id is None:
            raise ValueError("上一片段尚未选择批准视频")
        source_video = self._repository.get_asset(previous.selected_video_asset_id)
        tail = self._ensure_tail_frame(source_video, shot_id=previous.id)
        bindings = [
            binding
            for binding in shot.draft.reference_bindings
            if binding.usage is not ReferenceUsage.APPROVED_ANCHOR
        ]
        bindings.append(
            ReferenceBinding(
                assetId=tail.id,
                usage=ReferenceUsage.APPROVED_ANCHOR,
                role=ReferenceRole.COMPOSITION,
                applyTo=ReferenceTarget.BOTH,
            )
        )
        values = shot.draft.model_dump(mode="python")
        values.update(
            {
                "anchor_mode": AnchorMode.EXISTING,
                "reference_bindings": bindings,
                "scene_look_usage": (
                    SceneLookUsage.APPEARANCE_ONLY
                    if shot.draft.scene_look_usage is SceneLookUsage.DERIVE_ANCHOR
                    else shot.draft.scene_look_usage
                ),
            }
        )
        return self._repository.update_shot(
            shot.id,
            ShotCardDraft.model_validate(values),
        )

    def tail_frame_status(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self._repository.get_shot(shot_id)
        return _tail_state_json(_previous_tail_state(self._repository, shot))

    def _ensure_tail_frame(
        self,
        source_video: StoredAsset,
        *,
        shot_id: uuid.UUID,
    ) -> StoredAsset:
        if source_video.media_type != "video" or source_video.status != "approved":
            raise ValueError("尾帧只能从已批准视频抽取")
        existing = next(
            (
                asset
                for asset in reversed(self._repository.list_assets(shot_id=shot_id))
                if asset.role == "shot_tail_frame"
                and asset.metadata.get("sourceVideoAssetId") == str(source_video.id)
                and asset.metadata.get("sourceVideoSha256") == source_video.sha256
                and asset.content_ready
            ),
            None,
        )
        if existing is not None:
            return existing
        if self._frame_extractor is None:
            raise RuntimeError("尾帧抽取需要可用的FFmpeg")
        frame_path, timestamp_ms = self._frame_extractor.extract_tail_frame(source_video)
        try:
            landed = self._asset_store.import_local(frame_path)
            qc = self._media_probe.inspect_image(landed.path)
        finally:
            frame_path.unlink(missing_ok=True)
        return self._repository.add_asset(
            landed=landed,
            role="shot_tail_frame",
            media_type="image",
            scope="shot",
            status="approved",
            project_id=source_video.project_id,
            scene_id=source_video.scene_id,
            shot_id=shot_id,
            step_id=None,
            semantic_key=f"shot:{shot_id}:tail:{source_video.id}",
            metadata={
                "sourceVideoAssetId": str(source_video.id),
                "sourceVideoSha256": source_video.sha256,
                "syntheticFixture": source_video.metadata.get("syntheticFixture") is True,
                "timestampMs": timestamp_ms,
                "qc": qc,
            },
        )

    def reconcile_candidates(self, step_id: uuid.UUID) -> tuple[dict[str, Any], ...]:
        self._require_gateway()
        step = self._repository.get_step(step_id)
        if step.status is not StepStatus.SUBMISSION_UNKNOWN or step.kind is not StepKind.VIDEO:
            raise ValueError("only submission_unknown video steps can be reconciled")
        candidates = self._gateway.list_video_tasks(model=step.model or self._gateway.video_model)
        input_plan = step.input_snapshot.get("inputPlan")
        expected_duration = (
            input_plan.get("duration_seconds") if isinstance(input_plan, dict) else None
        )
        expected_resolution = input_plan.get("resolution") if isinstance(input_plan, dict) else None
        bound_task_ids = {
            item.provider_task_id
            for item in self._repository.list_steps(project_id=step.project_id)
            if item.id != step.id and item.provider_task_id
        }
        return tuple(
            {
                "taskId": item.task_id,
                "status": item.status,
                "createdAt": None if item.created_at is None else item.created_at.isoformat(),
                "durationSeconds": item.duration_seconds,
                "resolution": item.resolution,
                "ratio": item.ratio,
                "generateAudio": item.generate_audio,
            }
            for item in candidates
            if item.task_id not in bound_task_ids
            and item.duration_seconds in {None, expected_duration}
            and item.resolution in {None, expected_resolution}
            and item.ratio in {None, "9:16"}
            and item.generate_audio in {None, True}
            and (
                item.created_at is None
                or step.created_at is None
                or abs((item.created_at - step.created_at).total_seconds()) <= 1800
            )
        )

    def reconcile(self, step_id: uuid.UUID, *, task_id: str) -> dict[str, Any]:
        step = self._repository.get_step(step_id)
        if step.status is not StepStatus.SUBMISSION_UNKNOWN:
            raise ValueError("step is not awaiting reconciliation")
        if task_id not in {str(item["taskId"]) for item in self.reconcile_candidates(step_id)}:
            raise ValueError("the selected provider task does not match this video intent")
        self._repository.update_step(step_id, status=StepStatus.QUEUED, task_id=task_id)
        return self.resume_step(step_id, wait=False)

    def range_edit(
        self,
        shot_id: uuid.UUID,
        *,
        source_asset_id: uuid.UUID,
        start_ms: int,
        end_ms: int,
        instruction: str,
        allow_paid_generation: bool,
    ) -> dict[str, Any]:
        if self._runtime_preflight is not None:
            self._runtime_preflight.validate_for_range_edit(
                allow_paid_generation=allow_paid_generation
            )
        self._require_paid_gateway(allow_paid_generation)
        if self._frame_extractor is None:
            raise RuntimeError("range editing requires ffmpeg frame extraction")
        if not 500 <= end_ms - start_ms <= 13_000:
            raise ValueError("range edit selection must be between 0.5 and 13 seconds")
        shot = self._repository.get_shot(shot_id)
        source = self._repository.get_asset(source_asset_id)
        duration_ms = int(source.metadata.get("qc", {}).get("durationMs") or 0)
        if source.shot_card_id != shot_id or source.media_type != "video":
            raise ValueError("range edit source must be a video version of this shot")
        if not 0 <= start_ms < end_ms <= duration_ms:
            raise ValueError("range edit selection is outside the source video")
        provider_url = source.metadata.get("providerUrl")
        if not isinstance(provider_url, str) or not provider_url.startswith("https://"):
            raise ValueError("source video no longer has an accessible Ark HTTPS URL")
        boundary_paths = self._frame_extractor.extract_frames_at(
            source,
            timestamps_ms=(max(0, start_ms - 1), min(duration_ms - 1, end_ms)),
        )
        try:
            boundary_assets = tuple(
                self._repository.add_asset(
                    landed=self._asset_store.import_local(path),
                    role="range_boundary",
                    media_type="image",
                    scope="shot",
                    status="ready",
                    project_id=shot.project_id,
                    scene_id=shot.scene_id,
                    shot_id=shot.id,
                    step_id=None,
                    semantic_key=f"shot:{shot.id}:boundary:{index}",
                    metadata={"timestampMs": timestamp},
                )
                for index, (path, timestamp) in enumerate(
                    zip(boundary_paths, (start_ms, end_ms), strict=True), 1
                )
            )
        finally:
            for path in boundary_paths:
                path.unlink(missing_ok=True)
        provider_duration = min(13, max(4, math.ceil((end_ms - start_ms) / 1000)))
        plan = build_edit_input_plan(
            resolution=self._video_resolution,
            duration_seconds=provider_duration,
            source_video=_media_source(source),
            before_frame=_media_source(boundary_assets[0]),
            after_frame=_media_source(boundary_assets[1]),
        )
        prompt = compile_range_edit_prompt(
            self._prompt_context(shot),
            instruction=instruction,
            source_start_ms=start_ms,
            source_end_ms=end_ms,
        )
        snapshot = {
            "shotCardId": str(shot.id),
            "sourceAssetId": str(source.id),
            "startMs": start_ms,
            "endMs": end_ms,
            "targetDurationMs": end_ms - start_ms,
            "inputPlan": plan.model_dump(mode="json"),
            "boundaryAssetIds": [str(item.id) for item in boundary_assets],
        }
        step, _ = self._new_paid_step(
            shot,
            kind=StepKind.VIDEO,
            operation_key="video:range-edit",
            model=self._gateway.video_model,
            purpose=PromptPurpose.VIDEO,
            prompt=prompt.text,
            snapshot=snapshot,
            force_new_attempt=True,
            retry_reason="用户显式发起区间重拍",
        )
        self._repository.update_step(step.id, status=StepStatus.SUBMITTING)
        try:
            task = self._gateway.submit_video(
                prompt=prompt.text,
                input_plan=plan,
                input_sources=(
                    provider_url,
                    boundary_assets[0].require_path(),
                    boundary_assets[1].require_path(),
                ),
            )
        except GatewayError as exc:
            status = StepStatus.SUBMISSION_UNKNOWN if exc.submission_unknown else StepStatus.FAILED
            self._repository.update_step(step.id, status=status, error=_error_payload(exc))
            raise
        return self._continue_submitted_video(step, task)

    def _land_video(
        self,
        step: StoredStep,
        video_url: str,
        *,
        last_frame_url: str | None = None,
    ) -> dict[str, Any]:
        if step.shot_card_id is None:
            raise ValueError("video step is not bound to a shot card")
        shot = self._repository.get_shot(step.shot_card_id)
        existing_asset = next(
            (
                item
                for item in self._repository.list_assets(shot_id=shot.id)
                if item.step_id == step.id and item.media_type == "video"
            ),
            None,
        )
        if existing_asset is not None:
            if step.status in {StepStatus.QUEUED, StepStatus.RUNNING}:
                self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
            tail_frame = self._provider_tail_result(
                source_video=existing_asset,
                shot_id=shot.id,
                step_id=step.id,
                last_frame_url=last_frame_url,
            )
            return {
                "stepId": str(step.id),
                "taskId": step.provider_task_id,
                "assetId": str(existing_asset.id),
                "status": "awaiting_review",
                "qc": existing_asset.metadata.get("qc", {}),
                "reused": True,
                "tailFrame": tail_frame,
            }
        landed = self._asset_store.download(video_url, suffix=".mp4")
        if step.operation_key == "video:range-edit":
            source = self._repository.get_asset(
                uuid.UUID(str(step.input_snapshot["sourceAssetId"]))
            )
            replacement_qc = self._media_probe.inspect_video(
                landed.path,
                expected_duration_seconds=max(
                    4,
                    round(int(step.input_snapshot["targetDurationMs"]) / 1000),
                ),
                expected_resolution=self._video_resolution,
                minimum_duration_seconds=1,
                maximum_duration_seconds=15,
                duration_tolerance_ms=5000,
                require_audio=False,
            )
            replacement_duration_ms = int(replacement_qc.get("durationMs") or 0)
            if replacement_duration_ms <= 0:
                raise ValueError("range edit provider result has no measurable duration")
            landed = self._asset_store.render_range_replacement(
                base_path=source.require_path(),
                replacement_path=landed.path,
                replacement_duration_ms=replacement_duration_ms,
                start_ms=int(step.input_snapshot["startMs"]),
                end_ms=int(step.input_snapshot["endMs"]),
            )
        qc = self._media_probe.inspect_video(
            landed.path,
            expected_duration_seconds=shot.draft.duration_seconds,
            expected_resolution=self._video_resolution,
        )
        if not qc.get("passed"):
            self._repository.update_step(
                step.id,
                status=StepStatus.FAILED,
                error={"code": "technical_qc_failed", "qc": qc},
            )
            raise ValueError(f"video technical QC failed: {qc.get('failures')}")
        asset = self._repository.add_asset(
            landed=landed,
            role=("shot_video_edit" if step.operation_key == "video:range-edit" else "shot_video"),
            media_type="video",
            scope="shot",
            status="candidate",
            project_id=shot.project_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            step_id=step.id,
            semantic_key=f"shot:{shot.id}:video:{step.attempt}",
            metadata={
                "qc": qc,
                "providerUrl": (None if step.operation_key == "video:range-edit" else video_url),
                "syntheticFixture": self._provider_name == "local-fake-provider",
                "providerSegmentUrl": (
                    video_url if step.operation_key == "video:range-edit" else None
                ),
                "taskId": step.provider_task_id,
                "generationInputHash": step.input_snapshot.get("inputHash"),
                "referenceManifest": step.input_snapshot.get(
                    "providerReferenceManifest", []
                ),
                "upstreamLineage": step.input_snapshot.get(
                    "productionReferenceBindings", []
                ),
                "providerOrderEvidence": "frozen",
                "rangeEdit": (
                    None
                    if step.operation_key != "video:range-edit"
                    else {
                        "sourceAssetId": step.input_snapshot["sourceAssetId"],
                        "startMs": step.input_snapshot["startMs"],
                        "endMs": step.input_snapshot["endMs"],
                    }
                ),
            },
        )
        self._repository.update_step(step.id, status=StepStatus.AWAITING_REVIEW)
        self._record_video_review(shot, step, asset)
        tail_frame = self._provider_tail_result(
            source_video=asset,
            shot_id=shot.id,
            step_id=step.id,
            last_frame_url=last_frame_url,
        )
        return {
            "stepId": str(step.id),
            "taskId": step.provider_task_id,
            "assetId": str(asset.id),
            "status": "awaiting_review",
            "qc": qc,
            "tailFrame": tail_frame,
        }

    def _provider_tail_result(
        self,
        *,
        source_video: StoredAsset,
        shot_id: uuid.UUID,
        step_id: uuid.UUID,
        last_frame_url: str | None,
    ) -> dict[str, Any] | None:
        if not last_frame_url:
            return None
        try:
            tail = self._land_provider_tail_frame(
                source_video=source_video,
                shot_id=shot_id,
                step_id=step_id,
                last_frame_url=last_frame_url,
            )
            return {"status": "ready", "assetId": str(tail.id), "source": "provider"}
        except Exception as exc:
            return {"status": "unavailable", "error": _error_payload(exc)}

    def _land_provider_tail_frame(
        self,
        *,
        source_video: StoredAsset,
        shot_id: uuid.UUID,
        step_id: uuid.UUID,
        last_frame_url: str,
    ) -> StoredAsset:
        if source_video.media_type != "video":
            raise ValueError("供应商尾帧必须关联一个视频版本")
        existing = next(
            (
                asset
                for asset in reversed(self._repository.list_assets(shot_id=shot_id))
                if asset.role == "shot_tail_frame"
                and asset.metadata.get("sourceVideoAssetId") == str(source_video.id)
                and asset.metadata.get("sourceVideoSha256") == source_video.sha256
                and asset.content_ready
            ),
            None,
        )
        if existing is not None:
            return existing
        landed = self._asset_store.download(last_frame_url, suffix=".png")
        qc = self._media_probe.inspect_image(landed.path)
        if qc.get("passed") is False:
            raise ValueError(f"供应商返回尾帧技术检查失败：{qc}")
        duration_ms = source_video.metadata.get("qc", {}).get("durationMs")
        return self._repository.add_asset(
            landed=landed,
            role="shot_tail_frame",
            media_type="image",
            scope="shot",
            status="approved",
            project_id=source_video.project_id,
            scene_id=source_video.scene_id,
            shot_id=shot_id,
            step_id=step_id,
            semantic_key=f"shot:{shot_id}:tail:{source_video.id}",
            metadata={
                "sourceVideoAssetId": str(source_video.id),
                "sourceVideoSha256": source_video.sha256,
                "providerReturned": True,
                "timestampMs": duration_ms,
                "qc": qc,
            },
        )

    def _record_video_review(
        self,
        shot: StoredShot,
        step: StoredStep,
        asset: StoredAsset,
    ) -> None:
        if self._frame_extractor is None:
            return
        frames: tuple[Path, ...] = ()
        editorial_frames: tuple[Path, ...] = ()
        try:
            duration_ms = int(asset.metadata["qc"]["durationMs"])
            production_context = self._repository.generation_clip_production_context(
                shot.id
            )
            compiled_shot = production_context.get("compiledShot")
            director_shots = (
                compiled_shot.get("directorShots")
                if isinstance(compiled_shot, dict)
                else None
            )
            thumbnail_windows = [
                item
                for item in (director_shots or [])[1:]
                if isinstance(item, dict)
                and item.get("beatId")
                and isinstance(item.get("startSecond"), int)
                and isinstance(item.get("endSecond"), int)
                and item["endSecond"] > item["startSecond"]
            ]
            if thumbnail_windows:
                timestamps_ms = tuple(
                    min(
                        duration_ms - 1,
                        max(
                            0,
                            round(
                                (
                                    int(item["startSecond"])
                                    + int(item["endSecond"])
                                )
                                * 500
                            ),
                        ),
                    )
                    for item in thumbnail_windows
                )
                editorial_frames = self._frame_extractor.extract_frames_at(
                    asset,
                    timestamps_ms=timestamps_ms,
                )
                for window, timestamp_ms, frame in zip(
                    thumbnail_windows,
                    timestamps_ms,
                    editorial_frames,
                    strict=True,
                ):
                    beat_id = str(window["beatId"])
                    self._repository.add_asset(
                        landed=self._asset_store.import_local(frame),
                        role="editorial_thumbnail",
                        media_type="image",
                        scope="shot",
                        status="ready",
                        project_id=shot.project_id,
                        scene_id=shot.scene_id,
                        shot_id=shot.id,
                        step_id=step.id,
                        semantic_key=(
                            f"shot:{shot.id}:video:{asset.id}:editorial:{beat_id}"
                        ),
                        metadata={
                            "sourceVideoAssetId": str(asset.id),
                            "shotBeatId": beat_id,
                            "timestampMs": timestamp_ms,
                            "localExtraction": True,
                        },
                    )
            duration_seconds = math.ceil(duration_ms / 1000)
            count = min(8, max(6, math.ceil(duration_seconds / 3) + 3))
            frames = self._frame_extractor.extract_review_frames(asset, count=count)
            for ordinal, frame in enumerate(frames, 1):
                self._repository.add_asset(
                    landed=self._asset_store.import_local(frame),
                    role="review_frame",
                    media_type="image",
                    scope="shot",
                    status="ready",
                    project_id=shot.project_id,
                    scene_id=shot.scene_id,
                    shot_id=shot.id,
                    step_id=step.id,
                    semantic_key=f"shot:{shot.id}:video:{asset.id}:frame:{ordinal}",
                    metadata={
                        "sourceVideoAssetId": str(asset.id),
                        "syntheticFixture": asset.metadata.get("syntheticFixture") is True,
                        "ordinal": ordinal,
                        "frameCount": len(frames),
                    },
                )
            if not self._semantic_review_enabled or self._gateway is None:
                return
            diagnostic_reference_assets: list[StoredAsset] = []
            diagnostic_reference_labels: list[str] = []
            seen_reference_hashes: set[str] = set()
            visual_profile = self._repository.get_visual_profile(shot.project_id)
            for binding in visual_profile.draft.reference_bindings:
                reference_asset = self._repository.get_asset(binding.asset_id)
                if (
                    reference_asset.semantic_key
                    and reference_asset.semantic_key.startswith("style_source:")
                ):
                    continue
                if (
                    not reference_asset.content_ready
                    or reference_asset.sha256 in seen_reference_hashes
                ):
                    continue
                seen_reference_hashes.add(reference_asset.sha256)
                diagnostic_reference_assets.append(reference_asset)
                diagnostic_reference_labels.append(
                    f"Canon {binding.purpose.value}：{reference_asset.display_name}"
                )
            for design_asset in self._repository.approved_character_design_assets(
                shot.project_id
            ):
                if not design_asset.content_ready or design_asset.sha256 in seen_reference_hashes:
                    continue
                seen_reference_hashes.add(design_asset.sha256)
                slot = str(
                    design_asset.metadata.get("characterDesignSlot")
                    or design_asset.metadata.get("slot")
                    or design_asset.role
                )
                diagnostic_reference_assets.append(design_asset)
                diagnostic_reference_labels.append(
                    f"已批准本集设计 {slot}：{design_asset.display_name}"
                )
            result = self._gateway.diagnose_video_frames(
                prompt=(
                    compile_video_review_prompt(self._prompt_context(shot))
                    + "\n固定特征发生真实改变时标记 mismatch；图片信息不足时标记 uncertain；"
                    "不要仅根据文字推测 consistent。AI 结果只进入人工审核。"
                ),
                frame_paths=frames,
                reference_paths=tuple(
                    asset.require_path() for asset in diagnostic_reference_assets
                ),
                reference_labels=tuple(diagnostic_reference_labels),
            )
            warnings = tuple(
                {"severity": "suggestion", "message": item} for item in result.violations
            )
            self._repository.add_review(
                step_id=step.id,
                asset_id=asset.id,
                source="ark_visual",
                decision="pending",
                reason="AI suggestions do not automatically approve or reject media",
                warnings=warnings,
                evidence={
                    "confidence": result.confidence,
                    "evidence": list(result.evidence),
                    "shotBoundariesSeconds": list(result.shot_boundaries_seconds),
                    "identityReferenceAssetIds": [
                        str(item.id) for item in diagnostic_reference_assets
                    ],
                },
            )
        except Exception as exc:
            self._repository.add_review(
                step_id=step.id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="AI advice was unavailable; manual review remains available",
                warnings=({"severity": "warning", "message": str(exc)},),
                evidence={},
            )
        finally:
            for frame in (*editorial_frames, *frames):
                frame.unlink(missing_ok=True)

    def _record_anchor_review(
        self,
        shot: StoredShot,
        step: StoredStep,
        asset: StoredAsset,
    ) -> None:
        try:
            if not self._semantic_review_enabled or self._gateway is None:
                raise RuntimeError("视觉锚点 AI 诊断未启用")
            result = self._gateway.diagnose_image(
                prompt=compile_anchor_review_prompt(self._prompt_context(shot)),
                image_path=asset.require_path(),
            )
            violations = list(result.violations)
            if not result.identity_ok:
                violations.append("人物或猫咪身份与 Canon 不一致")
            if not result.style_ok:
                violations.append("画风与本集单一水彩参考不一致")
            if not result.constraints_ok:
                violations.append("身体结构、服装、道具或构图约束不满足")
            self._repository.add_review(
                step_id=step.id,
                asset_id=asset.id,
                source="ark_visual",
                decision="pending",
                reason="AI suggestions do not automatically approve or reject media",
                warnings=tuple(
                    {"severity": "suggestion", "message": item}
                    for item in dict.fromkeys(violations)
                ),
                evidence={
                    "confidence": result.confidence,
                    "identityAssessment": result.identity_assessment,
                    "evidence": list(result.evidence),
                    "requestHash": result.request_hash,
                },
            )
        except Exception as exc:
            self._repository.add_review(
                step_id=step.id,
                asset_id=asset.id,
                source="technical",
                decision="pending",
                reason="AI advice was unavailable; manual override remains available",
                warnings=({"severity": "warning", "message": str(exc)},),
                evidence={},
            )

    def _compile_shot_generation(
        self,
        shot: StoredShot,
        *,
        target: ReferenceTarget,
        regeneration_instruction: str | None = None,
        require_ready: bool,
        compilation: ShotCompilationContext | None = None,
        production_context: dict[str, Any] | None = None,
    ) -> ShotGenerationSpec:
        """Compile the exact assets, prompt and audit snapshot for one target."""

        if target is ReferenceTarget.BOTH:
            raise ValueError("generation target must be anchor or video")
        compilation = compilation or self._shot_compilation_context(shot)
        if compilation.shot.id != shot.id:
            raise ValueError("shot compilation context does not match requested shot")
        profile = compilation.visual_profile
        scene = compilation.scene
        project = compilation.project
        context = self._prompt_context(shot, compilation=compilation)
        if target is ReferenceTarget.VIDEO and production_context is not None:
            context = context.model_copy(
                update={"direction": str(production_context["compiledPrompt"])}
            )
        blockers: list[str] = []
        warnings: tuple[str, ...] = ()

        if target is ReferenceTarget.ANCHOR:
            if shot.draft.anchor_mode is not AnchorMode.GENERATE:
                blockers.append("当前锚点方式不是“生成新锚点”，无需提交锚点生成任务")
            if production_context is not None:
                reference_pairs = self._compiled_production_reference_pairs(
                    production_context,
                    target=ReferenceTarget.ANCHOR,
                )
            else:
                reference_pairs = self._resolved_reference_pairs(
                    shot,
                    target=ReferenceTarget.ANCHOR,
                    strict=False,
                    compilation=compilation,
                )
            sources = tuple(asset for _binding, asset in reference_pairs)
            if len(sources) > 14:
                blockers.append("Seedream最多允许14张参考图")
            descriptions = tuple(
                _video_reference_description(
                    index,
                    binding,
                    scene_look_usage=shot.draft.scene_look_usage,
                    asset=asset,
                )
                for index, (binding, asset) in enumerate(reference_pairs, 1)
            )
            if production_context is None:
                anchor_brief, anchor_brief_step_id = self._accepted_anchor_brief(
                    shot,
                    compilation=compilation,
                )
            else:
                anchor_brief = self._production_anchor_brief(production_context)
                anchor_brief_step_id = None
                if anchor_brief is None:
                    anchor_brief, anchor_brief_step_id = self._accepted_anchor_brief(
                        shot,
                        compilation=compilation,
                    )
            if anchor_brief is None:
                blockers.append(
                    "请先填写并接受开场静态画面稿，或使用可选的 LLM 创作分析生成建议"
                )
                anchor_brief = "尚未接受开场静态画面稿"
            prompt = compile_anchor_prompt(
                context,
                anchor_brief=anchor_brief,
                reference_descriptions=descriptions,
                regeneration_instruction=regeneration_instruction,
                visual_profile=profile.draft,
            )
            creative_body = anchor_brief
            system_shell = prompt.text
            input_plan = None
            provider_input_mode = (
                ProviderInputMode.REFERENCE_MEDIA
                if sources
                else ProviderInputMode.TEXT_ONLY
            )
        else:
            anchor_brief_step_id = None
            anchor, references, descriptions = self._resolve_video_inputs(
                shot,
                require_generated_anchor=False,
                strict=False,
                compilation=compilation,
            )
            if production_context is not None and anchor is None:
                production_pairs = self._compiled_production_reference_pairs(
                    production_context,
                    target=ReferenceTarget.VIDEO,
                )
                references = tuple(asset for _binding, asset in production_pairs)
                descriptions = tuple(
                    _video_reference_description(
                        index,
                        binding,
                        scene_look_usage=shot.draft.scene_look_usage,
                        asset=asset,
                    )
                    for index, (binding, asset) in enumerate(production_pairs, 1)
                )
            if (
                shot.draft.anchor_mode is AnchorMode.GENERATE
                and shot.selected_anchor_asset_id is None
            ):
                blockers.append("请先生成、批准并选择片段开场锚点")
            sources = (() if anchor is None else (anchor,)) + references
            if shot.draft.anchor_mode is AnchorMode.EXISTING and anchor is None:
                blockers.append("请先选择一张已批准的片段开场图")
            reference_limit_exceeded = (
                anchor is None
                and len(references) > SEEDANCE_2_0_CAPABILITY.maximum_image_references
            )
            if reference_limit_exceeded:
                blockers.append(
                    "当前视频模型最多允许"
                    f"{SEEDANCE_2_0_CAPABILITY.maximum_image_references}张参考图；"
                    "请在费用确认前人工精简，系统不会静默删除人物或猫咪身份参考"
                )
            input_plan = build_shot_input_plan(
                resolution=self._video_resolution,
                duration_seconds=shot.draft.duration_seconds,
                anchor=None if anchor is None else _media_source(anchor),
                references=(
                    ()
                    if reference_limit_exceeded
                    else tuple(_media_source(item) for item in references)
                ),
            )
            prompt_parts = compile_shot_video_prompt_parts(
                context,
                input_plan,
                binding_descriptions=() if reference_limit_exceeded else descriptions,
                regeneration_instruction=regeneration_instruction,
                visual_profile=profile.draft,
                semantic_aliases=_semantic_reference_aliases(sources),
                strict_semantic_links=False,
                precompiled_creative_body=production_context is not None,
            )
            warnings = prompt_parts.link_warnings
            blockers.extend(prompt_parts.link_warnings)
            prompt = prompt_parts.final
            creative_body = prompt_parts.creative_body
            system_shell = prompt_parts.system_shell.text
            provider_input_mode = (
                ProviderInputMode.FIRST_FRAME
                if anchor is not None
                else (
                    ProviderInputMode.REFERENCE_MEDIA
                    if references
                    else ProviderInputMode.TEXT_ONLY
                )
            )
            if _INTERNAL_CHARACTER_DESIGN_LABEL.search(prompt.text):
                blockers.append(
                    "当前制作包 Prompt 使用历史内部素材标识；"
                    "请在生产画布重新编译并确认制作包后再生成"
                )

        for source in sources:
            if _is_synthetic_fixture(source):
                blockers.append(
                    f"素材“{source.display_name}”是历史测试占位，不能用于真实 Ark 生成"
                )
            elif source.media_type != "image":
                blockers.append(f"素材“{source.display_name}”不是可用图片")
            elif source.status not in {"approved", "ready"}:
                blockers.append(f"素材“{source.display_name}”尚未批准")
            elif not source.content_ready:
                blockers.append(f"素材“{source.display_name}”文件不可读取")

        source_assets = [
            {
                "assetId": str(item.id),
                "sha256": item.sha256,
                "semanticKey": item.semantic_key,
            }
            for item in sources
        ]
        source_revision_hash = _hash_json(
            {
                "target": target.value,
                "projectId": str(project.id),
                "projectTitle": project.title,
                "scene": scene.draft.model_dump(mode="json", by_alias=True),
                "selectedSceneLookAssetId": (
                    None
                    if scene.selected_look_asset_id is None
                    else str(scene.selected_look_asset_id)
                ),
                "shot": shot.draft.model_dump(mode="json", by_alias=True),
                "shotDraftRevision": shot.draft_revision,
                "visualProfileRevisionId": str(profile.id),
                "visualProfileHash": profile.profile_hash,
                "sourceAssets": source_assets,
                "anchorBriefStepId": (
                    None if target is ReferenceTarget.VIDEO else anchor_brief_step_id
                ),
                "productionPackage": production_context,
            }
        )
        snapshot: dict[str, Any] = {
            "generationTarget": target.value,
            "providerInputMode": provider_input_mode.value,
            "sourceRevisionHash": source_revision_hash,
            "shotCardId": str(shot.id),
            "shotDraftRevision": shot.draft_revision,
            "visualProfileRevisionId": str(profile.id),
            "visualProfileRevision": profile.revision,
            "sceneLookUsage": shot.draft.scene_look_usage.value,
            "anchorMode": shot.draft.anchor_mode.value,
            "sourceAssetIds": [item["assetId"] for item in source_assets],
            "sourceAssets": source_assets,
            "anchorBriefStepId": (
                None if target is ReferenceTarget.VIDEO else anchor_brief_step_id
            ),
        }
        if target is ReferenceTarget.ANCHOR and production_context is not None:
            provider_reference_manifest = [
                item
                for item in production_context.get("referenceBindings") or []
                if isinstance(item, dict) and item.get("providerIncluded") is True
            ]
        else:
            provider_reference_manifest = [
                {
                    "assetId": str(source.id),
                    "sha256": source.sha256,
                    "semanticRole": (
                        "approved_anchor"
                        if provider_input_mode is ProviderInputMode.FIRST_FRAME and index == 1
                        else "reference"
                    ),
                    "purpose": (
                        "video_first_frame"
                        if provider_input_mode is ProviderInputMode.FIRST_FRAME and index == 1
                        else target.value
                    ),
                    "instruction": (
                        "批准锚点是视频 Provider 唯一图片输入；人物、猫咪、场景与画风已烘焙其中"
                        if provider_input_mode is ProviderInputMode.FIRST_FRAME and index == 1
                        else "按服务端编译顺序作为实际供应商图片输入"
                    ),
                    "ordinal": index,
                    "locked": True,
                    "providerIncluded": True,
                    "providerSlot": (
                        "first_frame"
                        if provider_input_mode is ProviderInputMode.FIRST_FRAME and index == 1
                        else f"reference_image_{index}"
                    ),
                    "omissionReason": None,
                    "origin": (
                        "approved_anchor"
                        if provider_input_mode is ProviderInputMode.FIRST_FRAME
                        else "shot_generation"
                    ),
                    "contentUrl": f"/api/v1/assets/{source.id}/content",
                    "evidenceLevel": "frozen",
                }
                for index, source in enumerate(sources, 1)
            ]
        snapshot["providerReferenceManifest"] = provider_reference_manifest
        if production_context is not None:
            snapshot.update(
                {
                    "storyboardRevisionId": production_context["storyboardRevisionId"],
                    "structureHash": production_context["structureHash"],
                    "generationPlanId": production_context["generationPlanId"],
                    "generationPlanHash": production_context["generationPlanHash"],
                    "productionPackageHash": production_context[
                        "productionPackageHash"
                    ],
                    "compiledPromptId": production_context["compiledPromptId"],
                    "compiledPromptInputHash": production_context[
                        "compiledPromptInputHash"
                    ],
                    "compiledPromptHash": production_context["compiledPromptHash"],
                    "productionReferenceBindings": production_context.get(
                        "referenceBindings", []
                    ),
                    "videoReferencePolicy": (
                        "approved_anchor_only_baked_lineage"
                        if target is ReferenceTarget.VIDEO
                        and shot.selected_anchor_asset_id is not None
                        else "compiled_production_references"
                    ),
                }
            )
        if input_plan is None:
            snapshot["referenceAssetIds"] = [item["assetId"] for item in source_assets]
            snapshot["durationSeconds"] = shot.draft.duration_seconds
        else:
            snapshot["inputPlan"] = input_plan.model_dump(mode="json")
        input_hash = _hash_json({"prompt": prompt.text, "snapshot": snapshot})
        unique_blockers = tuple(dict.fromkeys(blockers))
        if require_ready and unique_blockers:
            raise ValueError("；".join(unique_blockers))
        return ShotGenerationSpec(
            target=target,
            provider_input_mode=provider_input_mode,
            prompt=prompt,
            creative_body=creative_body,
            system_shell=system_shell,
            input_plan=input_plan,
            sources=sources,
            descriptions=descriptions,
            snapshot=snapshot,
            input_hash=input_hash,
            source_revision_hash=source_revision_hash,
            blockers=unique_blockers,
            warnings=warnings,
        )

    @staticmethod
    def _production_anchor_brief(
        production_context: dict[str, Any],
    ) -> str | None:
        shot = production_context.get("compiledShot")
        if not isinstance(shot, dict):
            return None
        director_shots = shot.get("directorShots")
        first = (
            director_shots[0]
            if isinstance(director_shots, list)
            and director_shots
            and isinstance(director_shots[0], dict)
            else shot
        )
        title = str(first.get("title") or shot.get("title") or "开场状态")
        visible_state = str(
            first.get("continuityIn")
            or first.get("visualDescription")
            or shot.get("action")
            or "人物、猫咪与场景保持已批准的开场状态"
        )
        child_action = str(first.get("childAction") or "保持动作开始前的自然准备姿态")
        cat_action = str(first.get("catAction") or "保持自然四足准备姿态")
        spatial_relation = str(first.get("spatialRelation") or "保持已批准的人猫相对比例")
        contact = str(first.get("contactOcclusion") or "接触与遮挡遵循批准分镜")
        shot_size = str(first.get("shotSize") or "中景")
        lighting = str(first.get("lighting") or "遵循已批准 Scene Look")
        return (
            f"真实生成片段《{title}》的 t=0 开场静态画面：{visible_state}。"
            f"儿童处于即将开始“{child_action}”之前的稳定姿态；"
            f"猫咪处于即将开始“{cat_action}”之前的自然四足姿态；"
            f"空间关系：{spatial_relation}；接触与遮挡：{contact}；"
            f"景别：{shot_size}；光线：{lighting}。"
            "只表现动作发生前的单帧状态，不提前展示后续动作、变化或收尾。"
        )

    def _new_paid_step(
        self,
        shot: StoredShot,
        *,
        kind: StepKind,
        operation_key: str,
        model: str,
        purpose: PromptPurpose,
        prompt: str,
        snapshot: dict[str, Any],
        force_new_attempt: bool = False,
        retry_reason: str | None = None,
        request_idempotency_key: str | None = None,
    ) -> tuple[StoredStep, Any]:
        if request_idempotency_key is not None:
            snapshot = {
                **snapshot,
                "requestIdempotencyKey": request_idempotency_key,
            }
        input_hash = _hash_json({"prompt": prompt, "snapshot": snapshot})
        # Reuse the current input's first attempt.  A changed input or explicit
        # regenerate request receives a new attempt without touching old media.
        existing = [
            item
            for item in self._repository.list_steps(
                project_id=shot.project_id,
                shot_id=shot.id,
            )
            if item.operation_key == operation_key
            and item.input_snapshot.get("inputHash") == input_hash
        ]
        operation_steps = [
            item
            for item in self._repository.list_steps(
                project_id=shot.project_id,
                shot_id=shot.id,
            )
            if item.operation_key == operation_key
        ]
        unresolved = next(
            (item for item in operation_steps if item.status is StepStatus.SUBMISSION_UNKNOWN),
            None,
        )
        if request_idempotency_key is not None and existing:
            attempt = existing[-1].attempt
        elif force_new_attempt:
            if unresolved is not None:
                raise ValueError(
                    f"step {unresolved.id} is submission_unknown; reconcile it before regeneration"
                )
            previous = operation_steps[-1] if operation_steps else None
            if previous is not None and previous.status in {
                StepStatus.PENDING,
                StepStatus.SUBMITTING,
                StepStatus.QUEUED,
                StepStatus.RUNNING,
            }:
                raise ValueError(f"step {previous.id} is still active and cannot be regenerated")
            attempt = self._repository.next_attempt(
                shot_id=shot.id,
                operation_key=operation_key,
            )
            snapshot = {
                **snapshot,
                "retryOfStepId": None if previous is None else str(previous.id),
                "retryReason": retry_reason or "explicit regeneration",
            }
        elif existing:
            attempt = existing[-1].attempt
        else:
            attempt = self._repository.next_attempt(
                shot_id=shot.id,
                operation_key=operation_key,
            )
        snapshot = {**snapshot, "inputHash": input_hash}
        return self._repository.create_step_with_prompt(
            project_id=shot.project_id,
            scene_id=shot.scene_id,
            shot_id=shot.id,
            kind=kind,
            operation_key=operation_key,
            attempt=attempt,
            provider=self._provider_name,
            model=model,
            input_hash=input_hash,
            input_snapshot=snapshot,
            purpose=purpose,
            prompt_text=prompt,
        )

    def _resolve_video_inputs(
        self,
        shot: StoredShot,
        *,
        require_generated_anchor: bool = True,
        strict: bool = True,
        compilation: ShotCompilationContext | None = None,
    ) -> tuple[StoredAsset | None, tuple[StoredAsset, ...], tuple[str, ...]]:
        def resolve(asset_id: uuid.UUID) -> StoredAsset:
            if compilation is not None:
                return compilation.assets_by_id[asset_id]
            return self._repository.get_asset(asset_id)

        explicit_pairs = [
            (binding, resolve(binding.asset_id))
            for binding in shot.draft.reference_bindings
        ]
        anchor: StoredAsset | None = None
        if shot.draft.anchor_mode is AnchorMode.EXISTING:
            anchor = next(
                (
                    asset
                    for binding, asset in explicit_pairs
                    if binding.usage is ReferenceUsage.APPROVED_ANCHOR
                ),
                None,
            )
        elif shot.draft.anchor_mode is AnchorMode.GENERATE:
            if shot.selected_anchor_asset_id is None:
                if require_generated_anchor:
                    raise ValueError("generated anchor must be approved and selected before video")
            else:
                anchor = resolve(shot.selected_anchor_asset_id)
        if (
            strict
            and anchor is not None
            and (
                anchor.media_type != "image"
                or anchor.status not in {"approved", "ready"}
                or not anchor.content_ready
            )
        ):
            raise ValueError("the selected anchor is missing, damaged, or not approved")
        # Seedance first-frame input and ordinary reference media are distinct,
        # mutually exclusive request modes.  The anchor was already generated
        # from the selected identity, style, scene and prop inputs, so sending
        # those sources again would both conflict visually and be rejected by
        # Ark before task creation.
        if anchor is not None:
            return (
                anchor,
                (),
                (
                    "@图片1=本片段已批准开场锚点；它是本次Seedance唯一图片输入，"
                    "锁定动作开始前已经确认的人物、猫咪、服装、环境、道具和构图状态",
                ),
            )
        generation_pairs = list(
            self._resolved_reference_pairs(
                shot,
                target=ReferenceTarget.VIDEO,
                strict=strict,
                compilation=compilation,
            )
        )
        references = tuple(asset for _binding, asset in generation_pairs)
        if len(references) > 9:
            raise ValueError("Seedance最多允许9张普通参考图片输入")
        descriptions = tuple(
            _video_reference_description(
                index,
                binding,
                scene_look_usage=shot.draft.scene_look_usage,
                asset=_asset,
            )
            for index, (binding, _asset) in enumerate(
                generation_pairs,
                1,
            )
        )
        return None, references, descriptions

    def _resolved_reference_pairs(
        self,
        shot: StoredShot,
        *,
        target: ReferenceTarget,
        strict: bool = True,
        compilation: ShotCompilationContext | None = None,
    ) -> tuple[tuple[ReferenceBinding, StoredAsset], ...]:
        resolved: list[tuple[ReferenceBinding, StoredAsset]] = []
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        for binding in self._reference_bindings(
            shot,
            target=target,
            compilation=compilation,
        ):
            asset = (
                compilation.assets_by_id[binding.asset_id]
                if compilation is not None
                else self._repository.get_asset(binding.asset_id)
            )
            if strict and (
                asset.media_type != "image"
                or asset.status not in {"approved", "ready"}
                or not asset.content_ready
            ):
                raise ValueError(
                    "a selected generation reference is unavailable or not an image"
                )
            if asset.id in seen_ids or asset.sha256 in seen_hashes:
                continue
            seen_ids.add(asset.id)
            seen_hashes.add(asset.sha256)
            resolved.append((binding, asset))
        return tuple(resolved)

    def _reference_bindings(
        self,
        shot: StoredShot,
        *,
        target: ReferenceTarget,
        compilation: ShotCompilationContext | None = None,
    ) -> tuple[ReferenceBinding, ...]:
        project = (
            compilation.project
            if compilation is not None
            else self._repository.get_project(shot.project_id)
        )
        scene = (
            compilation.scene
            if compilation is not None
            else self._repository.get_scene(shot.scene_id)
        )
        return tuple(
            item
            for item in _merge_generation_references(
                custom=tuple(shot.draft.reference_bindings),
                scene_references=self._scene_reference_bindings(scene),
                scene_look_asset_id=scene.selected_look_asset_id,
                has_approved_anchor=(
                    shot.selected_anchor_asset_id is not None
                    or any(
                        item.usage is ReferenceUsage.APPROVED_ANCHOR
                        for item in shot.draft.reference_bindings
                    )
                ),
                project_defaults=_project_reference_bindings(
                    self._repository,
                    shot,
                    scene,
                    project,
                    profile=(
                        compilation.visual_profile if compilation is not None else None
                    ),
                    assets_by_id=(
                        compilation.assets_by_id if compilation is not None else None
                    ),
                ),
                inherit_project_references=shot.draft.inherit_project_references,
                scene_look_usage=shot.draft.scene_look_usage,
                target=target,
            )
            if item.apply_to in {target, ReferenceTarget.BOTH}
        )

    def _accepted_anchor_brief(
        self,
        shot: StoredShot,
        *,
        compilation: ShotCompilationContext | None = None,
    ) -> tuple[str | None, str | None]:
        current = next(
            (
                item
                for item in reversed(
                    self._anchor_brief_records(shot, compilation=compilation)
                )
                if not item.stale
            ),
            None,
        )
        if current is not None:
            return current.text, str(current.step_id)
        return None, None

    def _anchor_brief_records(
        self,
        shot: StoredShot,
        *,
        compilation: ShotCompilationContext | None = None,
    ) -> tuple[AcceptedAnchorBrief, ...]:
        records: list[AcceptedAnchorBrief] = []
        steps = (
            compilation.shot_steps
            if compilation is not None
            else self._repository.list_steps(project_id=shot.project_id, shot_id=shot.id)
        )
        for step in steps:
            if step.operation_key not in {
                "editor:anchor-brief",
                "director:shot-assistance",
            }:
                continue
            value = step.input_snapshot.get("acceptedAnchorBrief")
            if not isinstance(value, str) or not value.strip():
                continue
            accepted_revision = step.input_snapshot.get("acceptedDraftRevision")
            source_revision = step.input_snapshot.get("sourceDraftRevision")
            if not isinstance(accepted_revision, int) or not isinstance(source_revision, int):
                continue
            records.append(
                AcceptedAnchorBrief(
                    step_id=step.id,
                    text=value.strip(),
                    source=(
                        "manual"
                        if step.operation_key == "editor:anchor-brief"
                        else "llm"
                    ),
                    source_draft_revision=source_revision,
                    accepted_draft_revision=accepted_revision,
                    accepted_at=(
                        step.input_snapshot.get("acceptedAnchorBriefAt")
                        or step.input_snapshot.get("acceptedAt")
                    ),
                    created_at=(
                        None if step.created_at is None else step.created_at.isoformat()
                    ),
                    stale=accepted_revision != shot.draft_revision,
                )
            )
        return tuple(records)

    def _scene_reference_bindings(
        self,
        scene: StoredScene,
    ) -> tuple[ReferenceBinding, ...]:
        if scene.look_draft is None:
            return ()
        role_by_purpose = {
            LookReferencePurpose.WARDROBE: ReferenceRole.SCENE,
            LookReferencePurpose.ENVIRONMENT: ReferenceRole.SCENE,
            LookReferencePurpose.PROP: ReferenceRole.PROP,
            LookReferencePurpose.COMPOSITION: ReferenceRole.COMPOSITION,
        }
        return tuple(
            ReferenceBinding(
                assetId=item.asset_id,
                usage=ReferenceUsage.GENERATION_REFERENCE,
                role=role_by_purpose[item.purpose],
                applyTo=ReferenceTarget.BOTH,
            )
            for item in scene.look_draft.reference_bindings
            if item.purpose in role_by_purpose
        )

    def _default_scene_look_draft(self, scene: StoredScene) -> SceneLookDraft:
        profile = self._repository.get_visual_profile(scene.project_id)
        look_plan = scene.draft.look_plan or SceneLookPlan()
        bindings: list[LookReferenceBinding] = []
        for binding in _order_look_bindings(profile.draft.reference_bindings):
            if binding.purpose is not LookReferencePurpose.STYLE:
                bindings.append(binding)
                continue
            asset = self._repository.get_asset(binding.asset_id)
            semantic_key = asset.semantic_key or ""
            if semantic_key in {"style:outdoor", "style:indoor"}:
                expected = f"style:{look_plan.environment_style.value}"
                if semantic_key != expected:
                    continue
            bindings.append(binding)
        design_purposes = (
            LookReferencePurpose.WARDROBE,
            LookReferencePurpose.CAT_IDENTITY,
            LookReferencePurpose.COMPOSITION,
        )
        load_design_assets = getattr(
            self._repository,
            "approved_character_design_assets",
            None,
        )
        approved_design_assets = (
            load_design_assets(scene.project_id)
            if callable(load_design_assets)
            else ()
        )
        for purpose, asset in zip(
            design_purposes,
            approved_design_assets,
            strict=False,
        ):
            if any(binding.asset_id == asset.id for binding in bindings):
                continue
            bindings.append(
                LookReferenceBinding(
                    assetId=asset.id,
                    purpose=purpose,
                    instruction=(
                        "当前批准的本集角色设计；只锁定造型、身份外观或同框比例，"
                        "不得替换 Canon 身份"
                    ),
                )
            )
        return SceneLookDraft(
            visualProfileRevisionId=profile.id,
            lookPlan=look_plan,
            referenceBindings=bindings,
        )

    def _scene_look_inputs(self, scene_id: uuid.UUID, *, strict: bool) -> SceneLookInputSet:
        scene = self._repository.get_scene(scene_id)
        draft = scene.look_draft or self._default_scene_look_draft(scene)
        profile = self._repository.get_visual_profile_revision(
            draft.visual_profile_revision_id
        )
        if profile.project_id != scene.project_id:
            raise ValueError("场景视觉基准引用了其他项目的视觉档案")
        bindings: list[LookReferenceBinding] = []
        assets: list[StoredAsset] = []
        warnings: list[str] = []
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        load_design_assets = getattr(
            self._repository,
            "approved_character_design_assets",
            None,
        )
        approved_design_assets = (
            load_design_assets(scene.project_id)
            if callable(load_design_assets)
            else ()
        )
        required_design_bindings: list[LookReferenceBinding] = []
        for purpose, asset in zip(
            (
                LookReferencePurpose.WARDROBE,
                LookReferencePurpose.CAT_IDENTITY,
                LookReferencePurpose.COMPOSITION,
            ),
            approved_design_assets,
            strict=False,
        ):
            required_design_bindings.append(
                LookReferenceBinding(
                    assetId=asset.id,
                    purpose=purpose,
                    instruction="当前批准的本集儿童、猫咪或同框比例设计",
                )
            )
        ordered_bindings = _order_look_bindings(
            [*draft.reference_bindings, *required_design_bindings]
        )
        for binding in ordered_bindings:
            asset = self._repository.get_asset(binding.asset_id)
            if asset.id in seen_ids or asset.sha256 in seen_hashes:
                continue
            seen_ids.add(asset.id)
            seen_hashes.add(asset.sha256)
            bindings.append(binding)
            assets.append(asset)
            if (
                asset.media_type != "image"
                or asset.status not in {"approved", "ready"}
                or not asset.content_ready
            ):
                warnings.append(f"{asset.semantic_key or asset.id} 图片内容不可用")
        purposes = {item.purpose for item in bindings}
        required = {
            LookReferencePurpose.PERSON_IDENTITY: "至少选择一张人物身份参考",
            LookReferencePurpose.CAT_IDENTITY: "至少选择一张猫咪身份参考",
            LookReferencePurpose.STYLE: "至少选择一张画风参考",
        }
        requires_design_assets = getattr(
            self._repository,
            "requires_character_design_assets",
            None,
        )
        design_assets_required = bool(
            callable(requires_design_assets)
            and requires_design_assets(scene.project_id)
        )
        if design_assets_required:
            required.update(
                {
                    LookReferencePurpose.WARDROBE: "缺少当前批准的儿童本集造型",
                    LookReferencePurpose.COMPOSITION: "缺少当前批准的一人一猫同框比例图",
                }
            )
            if len(approved_design_assets) != 3:
                warnings.append(
                    "Scene Look 需要当前批准的儿童、猫咪和同框比例三个角色设计槽位"
                )
        warnings.extend(message for purpose, message in required.items() if purpose not in purposes)
        if len(assets) > 14:
            warnings.append("Seedream 最多允许 14 张参考图")
        if strict and warnings:
            raise ValueError("；".join(warnings))
        descriptions = tuple(
            _scene_look_reference_description(index, binding, asset)
            for index, (binding, asset) in enumerate(
                zip(bindings, assets, strict=True),
                1,
            )
        )
        return SceneLookInputSet(
            scene=scene,
            profile=profile,
            draft=draft,
            bindings=tuple(bindings),
            assets=tuple(assets),
            descriptions=descriptions,
            warnings=tuple(warnings),
        )

    def _prompt_context(
        self,
        shot: StoredShot,
        *,
        compilation: ShotCompilationContext | None = None,
    ) -> ShotPromptContext:
        scene = (
            compilation.scene
            if compilation is not None
            else self._repository.get_scene(shot.scene_id)
        )
        project = (
            compilation.project
            if compilation is not None
            else self._repository.get_project(shot.project_id)
        )
        return ShotPromptContext(
            project_title=project.title,
            scene_title=scene.draft.title,
            scene_text=scene.draft.source_text,
            context_note=scene.draft.context_note,
            shot_title=shot.draft.title,
            direction=shot.draft.direction,
            duration_seconds=shot.draft.duration_seconds,
        )

    def _shot_compilation_context(
        self,
        shot: StoredShot,
        *,
        read_model: ProjectReadModel | None = None,
        shot_read_model: ShotGenerationReadModel | None = None,
    ) -> ShotCompilationContext:
        if shot_read_model is not None:
            return ShotCompilationContext(
                project=shot_read_model.project,
                scene=shot_read_model.scene,
                shot=shot_read_model.shot,
                visual_profile=shot_read_model.visual_profile,
                assets_by_id={item.id: item for item in shot_read_model.assets},
                shot_steps=shot_read_model.steps,
            )
        if read_model is None:
            project = self._repository.get_project(shot.project_id)
            scene = self._repository.get_scene(shot.scene_id)
            visual_profile = self._repository.get_visual_profile(shot.project_id)
            assets_by_id = {
                item.id: item
                for item in self._repository.list_assets(
                    project_id=shot.project_id,
                    include_canon=True,
                )
            }
            referenced_asset_ids = {
                *(binding.asset_id for binding in project.default_reference_bindings),
                *(binding.asset_id for binding in visual_profile.draft.reference_bindings),
                *(binding.asset_id for binding in shot.draft.reference_bindings),
            }
            if scene.look_draft is not None:
                referenced_asset_ids.update(
                    binding.asset_id for binding in scene.look_draft.reference_bindings
                )
            referenced_asset_ids.update(
                asset_id
                for asset_id in (
                    scene.selected_look_asset_id,
                    shot.selected_anchor_asset_id,
                    shot.selected_video_asset_id,
                )
                if asset_id is not None
            )
            for asset_id in referenced_asset_ids - assets_by_id.keys():
                try:
                    assets_by_id[asset_id] = self._repository.get_asset(asset_id)
                except LookupError:
                    # Missing references remain visible as validation blockers in the
                    # compiled generation specification.
                    continue
            return ShotCompilationContext(
                project=project,
                scene=scene,
                shot=shot,
                visual_profile=visual_profile,
                assets_by_id=assets_by_id,
                shot_steps=self._repository.list_steps(
                    project_id=shot.project_id,
                    shot_id=shot.id,
                ),
            )
        scene = next(item for item in read_model.scenes if item.id == shot.scene_id)
        current_shot = next(item for item in read_model.shots if item.id == shot.id)
        return ShotCompilationContext(
            project=read_model.project,
            scene=scene,
            shot=current_shot,
            visual_profile=read_model.visual_profile,
            assets_by_id={item.id: item for item in read_model.assets},
            shot_steps=tuple(
                item for item in read_model.steps if item.shot_card_id == shot.id
            ),
        )

    def _require_gateway(self) -> None:
        if self._gateway is None:
            raise GatewayUnavailableError("Ark media gateway is not configured")

    @staticmethod
    def _assert_expected_input_hash(
        spec: ShotGenerationSpec,
        expected_input_hash: str | None,
    ) -> None:
        if expected_input_hash is None:
            raise ValueError("付费生成必须先查看服务端实际输入并提交 expectedInputHash")
        if spec.input_hash != expected_input_hash:
            raise RevisionConflictError(
                "生成输入已变化，请重新查看实际素材与 Prompt 后再确认费用"
            )

    def _require_paid_gateway(self, allowed: bool) -> None:
        if not allowed:
            raise ValueError("this operation requires explicit paid-generation permission")
        self._require_gateway()


def _project_reference_bindings(
    repository: ShotQueueStore,
    shot: StoredShot,
    scene: StoredScene,
    project: StoredProject,
    *,
    profile: StoredVisualProfileRevision | None = None,
    assets_by_id: Any | None = None,
) -> tuple[ReferenceBinding, ...]:
    bindings = project.default_reference_bindings
    if not bindings:
        profile = profile or repository.get_visual_profile(shot.project_id)
        bindings = tuple(
            ReferenceBinding(
                assetId=item.asset_id,
                usage=ReferenceUsage.GENERATION_REFERENCE,
                role=(
                    ReferenceRole.STYLE
                    if item.purpose is LookReferencePurpose.STYLE
                    else ReferenceRole.IDENTITY
                ),
                applyTo=ReferenceTarget.BOTH,
            )
            for item in profile.draft.reference_bindings
        )

    look_plan = (
        scene.look_draft.look_plan
        if scene.look_draft is not None
        else scene.draft.look_plan
    )
    expected_style = (
        None
        if look_plan is None
        else f"style:{look_plan.environment_style.value}"
    )
    filtered: list[ReferenceBinding] = []
    for binding in bindings:
        asset = (
            assets_by_id[binding.asset_id]
            if assets_by_id is not None
            else repository.get_asset(binding.asset_id)
        )
        if (
            expected_style is not None
            and asset.semantic_key in {"style:outdoor", "style:indoor"}
            and asset.semantic_key != expected_style
        ):
            continue
        filtered.append(binding)
    return tuple(filtered)


def _reference_role_for_purpose(purpose: VisualAssetPurpose) -> ReferenceRole:
    return {
        VisualAssetPurpose.WARDROBE: ReferenceRole.SCENE,
        VisualAssetPurpose.ENVIRONMENT: ReferenceRole.SCENE,
        VisualAssetPurpose.PROP: ReferenceRole.PROP,
        VisualAssetPurpose.COMPOSITION: ReferenceRole.COMPOSITION,
    }[purpose]


def _order_reference_image_sources(
    purpose: VisualAssetPurpose,
    assets: tuple[StoredAsset, ...],
) -> tuple[StoredAsset, ...]:
    """Apply the provider-facing responsibility order without changing selection.

    User choices remain authoritative.  This ordering only prevents identity and
    style inputs from moving ahead of the design reference they are meant to
    support.
    """

    def family(asset: StoredAsset) -> str:
        semantic = asset.semantic_key or ""
        if semantic.startswith("person:") or semantic.startswith("cat:"):
            return "identity"
        if semantic.startswith("style:"):
            return "style"
        role = str(asset.metadata.get("referenceRole") or asset.reference_purpose or asset.role)
        if role == "identity":
            return "identity"
        if role == "style":
            return "style"
        return "design"

    priorities = {
        VisualAssetPurpose.WARDROBE: {"design": 0, "identity": 1, "style": 2},
        VisualAssetPurpose.ENVIRONMENT: {"design": 0, "style": 1, "identity": 2},
        VisualAssetPurpose.PROP: {"design": 0, "style": 1, "identity": 2},
        VisualAssetPurpose.COMPOSITION: {"design": 0, "style": 1, "identity": 2},
    }[purpose]
    indexed = enumerate(assets)
    return tuple(
        asset
        for _index, asset in sorted(
            indexed,
            key=lambda item: (priorities[family(item[1])], item[0]),
        )
    )


def _reference_image_input_description(
    index: int,
    *,
    purpose: VisualAssetPurpose,
    asset: StoredAsset,
) -> str:
    semantic = asset.semantic_key or ""
    if semantic.startswith("person:") or semantic.startswith("cat:"):
        responsibility = (
            "只锁定长期角色外观，忽略原图服装、姿态和背景"
            if purpose is VisualAssetPurpose.WARDROBE
            else "只锁定长期角色外观，不要求角色出现在本张设定图"
        )
    elif semantic.startswith("style:"):
        responsibility = "只锁定线条、材质、色彩和光线"
    else:
        responsibility = {
            VisualAssetPurpose.WARDROBE: "只参考服装与配件设计，不改写角色身份",
            VisualAssetPurpose.ENVIRONMENT: "只参考空间、家具、出入口、光线和环境色调",
            VisualAssetPurpose.PROP: "只参考道具的结构、尺寸、图案和颜色",
            VisualAssetPurpose.COMPOSITION: "只参考机位、主体占位和空间层次",
        }[purpose]
    return f"@图片{index}={_asset_subject_label(asset)}；{responsibility}"


def _merge_generation_references(
    *,
    custom: tuple[ReferenceBinding, ...],
    scene_references: tuple[ReferenceBinding, ...] = (),
    scene_look_asset_id: uuid.UUID | None,
    has_approved_anchor: bool = False,
    project_defaults: tuple[ReferenceBinding, ...],
    inherit_project_references: bool,
    scene_look_usage: SceneLookUsage,
    target: ReferenceTarget,
) -> tuple[ReferenceBinding, ...]:
    ordered = [
        item
        for item in custom
        if item.usage is ReferenceUsage.GENERATION_REFERENCE
        and item.asset_id != scene_look_asset_id
    ]
    # Environment, wardrobe, prop and composition assets remain ordinary
    # reference media even when the composite Scene Look is disabled.
    ordered.extend(
        item
        for item in scene_references
        if item.usage is ReferenceUsage.GENERATION_REFERENCE
        and item.asset_id != scene_look_asset_id
    )
    include_scene_look = scene_look_usage in {
        SceneLookUsage.APPEARANCE_ONLY,
        SceneLookUsage.FULL_REFERENCE,
    } or (
        scene_look_usage is SceneLookUsage.DERIVE_ANCHOR
        and target is ReferenceTarget.ANCHOR
    )
    if target is ReferenceTarget.VIDEO and has_approved_anchor:
        include_scene_look = False
    if include_scene_look and scene_look_asset_id is not None:
        ordered.append(
            ReferenceBinding(
                assetId=scene_look_asset_id,
                usage=ReferenceUsage.GENERATION_REFERENCE,
                role=ReferenceRole.SCENE,
                applyTo=ReferenceTarget.BOTH,
            )
        )
    if inherit_project_references:
        ordered.extend(
            item
            for item in project_defaults
            if item.usage is ReferenceUsage.GENERATION_REFERENCE
            and item.asset_id != scene_look_asset_id
        )
    seen: set[uuid.UUID] = set()
    merged: list[ReferenceBinding] = []
    for item in ordered:
        if item.asset_id in seen:
            continue
        seen.add(item.asset_id)
        merged.append(item)
    return tuple(merged)


def _scene_asset_readiness(repository: ShotQueueStore, scene_id: uuid.UUID) -> SceneAssetReadiness:
    scene = repository.get_scene(scene_id)
    load_storyboard_context = getattr(
        repository,
        "storyboard_production_context",
        None,
    )
    storyboard_context = (
        load_storyboard_context(scene.id)
        if callable(load_storyboard_context)
        else {}
    )
    accepted_plan_step = next(
        (
            step
            for step in reversed(
                repository.list_steps(
                    project_id=scene.project_id,
                    scene_id=scene.id,
                )
            )
            if step.operation_key == "director:visual-asset-plan"
            and isinstance(step.input_snapshot.get("acceptedOutput"), dict)
        ),
        None,
    )
    if callable(load_storyboard_context):
        plan_is_current = bool(
            accepted_plan_step is not None
            and storyboard_context.get("structureApproved")
            and storyboard_context.get("generationPlanApproved")
            and accepted_plan_step.input_snapshot.get("storyboardRevisionId")
            == storyboard_context.get("storyboardRevisionId")
            and accepted_plan_step.input_snapshot.get("structureHash")
            == storyboard_context.get("structureHash")
            and accepted_plan_step.input_snapshot.get("generationPlanId")
            == storyboard_context.get("generationPlanId")
            and accepted_plan_step.input_snapshot.get("generationPlanHash")
            == storyboard_context.get("generationPlanHash")
        )
    else:
        list_shots = getattr(repository, "list_shots", None)
        current_shot_hash = (
            shot_snapshot_hash(
                (shot.id, shot.draft_revision, shot.draft)
                for shot in list_shots(scene.id)
            )
            if callable(list_shots)
            else None
        )
        plan_is_current = bool(
            accepted_plan_step is not None
            and current_shot_hash is not None
            and accepted_plan_step.input_snapshot.get("shotSnapshotHash")
            == current_shot_hash
        )
    accepted_plan = (
        None
        if accepted_plan_step is None
        else AcceptedVisualAssetPlan.model_validate(
            accepted_plan_step.input_snapshot["acceptedOutput"]
        )
    )
    all_assets = repository.list_assets(
        project_id=scene.project_id,
        include_canon=True,
    )
    assets_by_id = {item.id: item for item in all_assets}
    bound_bindings = (
        () if scene.look_draft is None else tuple(scene.look_draft.reference_bindings)
    )
    bound_ids = tuple(binding.asset_id for binding in bound_bindings)
    bound_assets = tuple(
        (asset, binding)
        for binding in bound_bindings
        if (asset := assets_by_id.get(binding.asset_id)) is not None
    )
    continuity = _scene_continuity_context(scene)

    accepted_selections = (
        []
        if accepted_plan is None
        else [
            selection
            for selection in accepted_plan.selections
            if selection.action.value != "skip"
        ]
    )
    if accepted_plan is None or not accepted_plan.selections:
        slot_specs: list[tuple[str, str, VisualAssetPurpose, uuid.UUID | None]] = [
            ("wardrobe", "本集服饰与配件", VisualAssetPurpose.WARDROBE, None),
            ("environment", "当前场景环境", VisualAssetPurpose.ENVIRONMENT, None),
        ]
        for index, label in enumerate(
            _required_scene_objects(continuity),
            start=1,
        ):
            slot_specs.append(
                (f"prop-{index}", label, VisualAssetPurpose.PROP, None)
            )
    else:
        slot_specs = [
            (
                selection.suggestion_key,
                selection.display_name,
                selection.purpose,
                selection.existing_asset_id,
            )
            for selection in accepted_selections
        ]

    slots: list[SceneAssetSlotReadiness] = []
    for key, display_name, purpose, expected_asset_id in slot_specs:
        candidates = [
            asset
            for asset, binding in bound_assets
            if binding.purpose.value == purpose.value
            and (
                expected_asset_id is None
                or asset.id == expected_asset_id
            )
            and (
                purpose is not VisualAssetPurpose.PROP
                or _asset_matches_required_object(asset, display_name)
            )
        ]
        ready_ids = [
            asset.id
            for asset in candidates
            if asset.status in {"approved", "ready"} and asset.content_ready
        ]
        stale_ids = [asset.id for asset in candidates if asset.status == "stale"]
        status = "ready" if ready_ids else "stale" if stale_ids else "missing"
        slots.append(
            SceneAssetSlotReadiness(
                key=key,
                displayName=display_name,
                purpose=purpose,
                assetIds=ready_ids or stale_ids,
                status=status,
            )
        )

    list_scene_shots = getattr(repository, "list_shots", None)
    scene_look_required = (
        True
        if not callable(list_scene_shots)
        else any(
            shot.draft.scene_look_usage is not SceneLookUsage.OFF
            for shot in list_scene_shots(scene.id)
        )
    )
    scene_look_status: str = "missing" if scene_look_required else "off"
    selected_look = (
        None
        if scene.selected_look_asset_id is None
        else assets_by_id.get(scene.selected_look_asset_id)
    )
    if scene_look_required and selected_look is not None:
        look_revision = selected_look.metadata.get("lookDraftRevision")
        look_is_current = look_revision in {None, scene.look_draft_revision}
        if selected_look.status == "stale" or not look_is_current:
            scene_look_status = "stale"
        elif selected_look.status == "approved" and selected_look.content_ready:
            scene_look_status = "approved"

    missing_keys = [item.key for item in slots if item.status == "missing"]
    stale_keys = [item.key for item in slots if item.status == "stale"]
    asset_blockers: list[str] = []
    if accepted_plan is None:
        asset_blockers.append("尚未人工接受当前场景的视觉资产规划")
    elif not plan_is_current:
        asset_blockers.append("分镜结构或生成编排已更新，视觉资产规划需要重新生成并接受")
    if missing_keys:
        labels = "、".join(item.display_name for item in slots if item.status == "missing")
        asset_blockers.append(f"缺少已批准并绑定的场景资产：{labels}")
    if stale_keys:
        labels = "、".join(item.display_name for item in slots if item.status == "stale")
        asset_blockers.append(f"场景资产已过期：{labels}")
    blockers = list(asset_blockers)
    if scene_look_required and scene_look_status == "missing":
        blockers.append("尚未选择已批准的场景视觉基准（Scene Look）")
    elif scene_look_required and scene_look_status == "stale":
        blockers.append("已选择的场景视觉基准已过期")
    bound_asset_ids = list(dict.fromkeys(
        [*bound_ids]
        + ([] if selected_look is None else [selected_look.id])
    ))
    return SceneAssetReadiness(
        requiredSlots=slots,
        boundAssetIds=bound_asset_ids,
        missingAssetKeys=missing_keys,
        staleAssetKeys=stale_keys,
        sceneLookStatus=scene_look_status,
        visualAssetPlanCurrent=plan_is_current,
        canGenerateSceneLook=not asset_blockers,
        canCompileShotPrompt=not blockers,
        blockers=blockers,
    )


def _scene_asset_readiness_if_available(
    repository: ShotQueueStore,
    scene_id: uuid.UUID,
) -> SceneAssetReadiness | None:
    """Keep standalone Scene Look stores compatible with Recipe-aware readiness.

    Scene asset planning requires the step and asset collections. Older standalone
    stores intentionally expose neither; their strict reference validation remains
    authoritative instead of manufacturing an incomplete Recipe projection.
    """

    if not callable(getattr(repository, "list_steps", None)) or not callable(
        getattr(repository, "list_assets", None)
    ):
        return None
    return _scene_asset_readiness(repository, scene_id)


def _scene_look_asset_blockers(
    readiness: SceneAssetReadiness,
) -> tuple[str, ...]:
    if readiness.can_generate_scene_look:
        return ()
    scene_look_only = {
        "尚未选择已批准的场景视觉基准（Scene Look）",
        "已选择的场景视觉基准已过期",
    }
    return tuple(item for item in readiness.blockers if item not in scene_look_only)



def _scene_story_snapshot(scene: StoredScene | None) -> dict[str, Any] | None:
    if scene is None:
        return None
    return {
        "sceneId": str(scene.id),
        "title": scene.draft.title,
        "sourceText": scene.draft.source_text,
    }


def _scene_continuity_context(scene: StoredScene) -> dict[str, Any]:
    note = scene.draft.context_note
    if not note:
        return {}
    try:
        document = json.loads(note)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(document, dict):
        return {}
    continuity = document.get("continuity")
    if not isinstance(continuity, dict):
        return {}
    return continuity


def _required_scene_objects(continuity: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for field in ("decorations", "props"):
        items = continuity.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            label = str(item).strip()
            if label and label not in values:
                values.append(label)
    return tuple(values)


def _asset_matches_required_object(asset: StoredAsset, required_name: str) -> bool:
    asset_name = "".join(asset.display_name.lower().split())
    target_name = "".join(required_name.lower().split())
    return bool(
        asset_name
        and target_name
        and (asset_name in target_name or target_name in asset_name)
    )


def _creative_step_json(step: StoredStep) -> dict[str, Any]:
    return {
        "stepId": str(step.id),
        "operationKey": step.operation_key,
        "status": step.status.value,
        "attempt": step.attempt,
        "model": step.model,
        "sourceHash": step.input_snapshot.get("sourceHash"),
        "shotSnapshotHash": step.input_snapshot.get("shotSnapshotHash"),
        "providerOutput": step.input_snapshot.get("providerOutput"),
        "acceptedOutput": step.input_snapshot.get("acceptedOutput"),
        "acceptedAt": step.input_snapshot.get("acceptedAt"),
        "source": step.input_snapshot.get("source"),
        "manualRevisionOfStepId": step.input_snapshot.get("manualRevisionOfStepId"),
        "manualRevisionNote": step.input_snapshot.get("manualRevisionNote"),
        "error": step.error,
        "createdAt": None if step.created_at is None else step.created_at.isoformat(),
    }


def _scene_look_reference_description(
    index: int,
    binding: LookReferenceBinding,
    asset: StoredAsset,
) -> str:
    responsibilities = {
        LookReferencePurpose.PERSON_IDENTITY: "只锁定人物脸型、五官、肤色和发型，忽略旧服装与背景",
        LookReferencePurpose.PERSON_BODY: "只锁定人物年龄感、身高和头身比例，忽略旧服装与姿态",
        LookReferencePurpose.CAT_IDENTITY: "只锁定猫咪脸部、毛色分区、虎斑、眼睛、尾巴和体型",
        LookReferencePurpose.STYLE: "只锁定线条、材质、色彩、自然光和景深，不改写角色身份",
        LookReferencePurpose.WARDROBE: "只参考本场景服装款式和材质，不替换人物身份",
        LookReferencePurpose.ENVIRONMENT: "只参考空间结构、固定家具、出入口、光线和环境色调",
        LookReferencePurpose.PROP: "只参考关键道具的外观、结构和比例",
        LookReferencePurpose.COMPOSITION: "只参考构图、机位和主体空间关系",
    }
    semantic = asset.semantic_key or f"asset:{asset.id}"
    instruction = f"；补充：{binding.instruction}" if binding.instruction else ""
    return f"@图片{index}={semantic}；{responsibilities[binding.purpose]}{instruction}"


def _video_reference_description(
    index: int,
    binding: ReferenceBinding,
    *,
    scene_look_usage: SceneLookUsage = SceneLookUsage.OFF,
    asset: StoredAsset | None = None,
) -> str:
    scene_responsibilities = {
        SceneLookUsage.OFF: "场景视觉基准已禁用",
        SceneLookUsage.APPEARANCE_ONLY: (
            "场景视觉基准，只继承服饰、配件、环境基调和共同道具；"
            "忽略基准图中的姿态、动作结果和构图"
        ),
        SceneLookUsage.FULL_REFERENCE: (
            "场景视觉基准，完整参考本场服装、道具、姿态和构图；它仍是普通参考图而不是首帧"
        ),
        SceneLookUsage.DERIVE_ANCHOR: (
            "场景视觉基准，用于派生本片段开场状态；生成锚点后不重复进入视频"
        ),
    }
    responsibilities = {
        ReferenceRole.IDENTITY: "项目角色身份，只锁定人物或猫咪的长期外观",
        ReferenceRole.STYLE: "项目系列画风，只锁定线条、材质、色彩和光线",
        ReferenceRole.SCENE: scene_responsibilities[scene_look_usage],
        ReferenceRole.PROP: "本片段道具外观、结构和比例",
        ReferenceRole.COMPOSITION: "本片段构图、机位和主体空间关系",
    }
    responsibility = responsibilities[binding.role]
    semantic = "" if asset is None else asset.semantic_key or ""
    if ":child:candidate:" in semantic:
        responsibility = "当前唯一儿童身份与本集造型来源；锁定脸型、短发、年龄感与本集服装"
    elif ":cat:candidate:" in semantic:
        responsibility = "当前唯一猫咪身份与本集造型来源；锁定灰白分区、虎斑、四足结构与尾巴环纹"
    elif ":pair_scale:candidate:" in semantic:
        responsibility = "只锁定一人一猫相对比例与自然接触尺度，不重新设计身份"
    purpose = "" if asset is None else str(asset.metadata.get("referencePurpose") or "")
    if purpose == "wardrobe":
        responsibility = "只继承本场服装与配件，不改变人物脸、发型、年龄、猫咪毛色或体型"
    elif purpose == "environment":
        responsibility = "只继承空间结构、固定家具、出入口、光线和环境色调"
    subject = _asset_subject_label(asset) if asset is not None else responsibilities[binding.role]
    return (
        f"@图片{index}={subject}；{responsibility}；"
        "只承担已声明职责，不改写其他主体或长期身份"
    )


def _asset_subject_label(asset: StoredAsset | None) -> str:
    if asset is None:
        return "未命名参考素材"
    semantic = asset.semantic_key or ""
    if ":child:candidate:" in semantic:
        return "本集儿童设计"
    if ":cat:candidate:" in semantic:
        return "本集猫咪设计"
    if ":pair_scale:candidate:" in semantic:
        return "一人一猫同框比例"
    if semantic.startswith("person:"):
        return f"人物“小孩”参考（{asset.display_name}）"
    if semantic.startswith("cat:"):
        return f"猫咪“灰白猫”参考（{asset.display_name}）"
    if semantic.startswith("style:"):
        return f"系列画风参考（{asset.display_name}）"
    if asset.role == "scene_look":
        return f"场景视觉基准（{asset.display_name}）"
    if asset.role in {"shot_anchor", "shot_tail_frame"}:
        return f"片段开场状态（{asset.display_name}）"
    purpose = str(asset.metadata.get("referencePurpose") or "")
    if purpose == "wardrobe":
        return f"当场服装与配件“{asset.display_name}”"
    if purpose == "environment":
        return f"场景环境“{asset.display_name}”"
    role = str(asset.metadata.get("referenceRole") or asset.reference_purpose or asset.role)
    names = {
        "prop": "道具",
        "composition": "构图",
        "style": "画风",
        "identity": "角色",
        "scene": "场景",
    }
    return f"{names.get(role, '素材')}“{asset.display_name}”"


def _semantic_reference_aliases(assets: tuple[StoredAsset, ...]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    people: list[str] = []
    cats: list[str] = []
    for index, asset in enumerate(assets, 1):
        alias = f"@图片{index}"
        semantic = asset.semantic_key or ""
        if semantic.startswith("person:"):
            people.append(alias)
        elif semantic.startswith("cat:"):
            cats.append(alias)
        elif asset.role == "scene_look":
            aliases["场景"] = f"场景视觉基准{alias}"
        else:
            role = str(asset.metadata.get("referenceRole") or asset.reference_purpose or "")
            if role == "prop":
                aliases[f"道具:{asset.display_name}"] = f"道具“{asset.display_name}”{alias}"
            elif role == "composition":
                aliases.setdefault("构图", f"构图参考{alias}")
            elif role == "style":
                aliases.setdefault("画风", f"画风参考{alias}")
    if people:
        aliases["人物"] = f"人物“小孩”{'、'.join(people)}"
    if cats:
        aliases["猫咪"] = f"猫咪“灰白猫”{'、'.join(cats)}"
    return aliases


def _order_look_bindings(
    bindings: list[LookReferenceBinding],
) -> tuple[LookReferenceBinding, ...]:
    purpose_order = {
        purpose: index
        for index, purpose in enumerate(
            (
                LookReferencePurpose.PERSON_IDENTITY,
                LookReferencePurpose.PERSON_BODY,
                LookReferencePurpose.CAT_IDENTITY,
                LookReferencePurpose.STYLE,
                LookReferencePurpose.WARDROBE,
                LookReferencePurpose.ENVIRONMENT,
                LookReferencePurpose.PROP,
                LookReferencePurpose.COMPOSITION,
            )
        )
    }
    return tuple(
        binding
        for _original_order, binding in sorted(
            enumerate(bindings),
            key=lambda item: (purpose_order[item[1].purpose], item[0]),
        )
    )


def _validate_suggestion_count(output: ShotSuggestionOutput, target_count: int) -> None:
    if len(output.shots) != target_count:
        raise ValueError(
            f"导演建议返回{len(output.shots)}个视频片段，但当前场景要求{target_count}个"
        )


def _shot_assist_asset_layer(
    shot: StoredShot,
    scene: StoredScene,
    project: StoredProject,
    asset: StoredAsset,
) -> str:
    if (asset.semantic_key or "").startswith("character-design:"):
        return "episode_design"
    if asset.role == "shot_tail_frame":
        return "previous_tail"
    if shot.selected_anchor_asset_id == asset.id:
        return "shot"
    if scene.selected_look_asset_id == asset.id:
        return "scene_look"
    if asset.scope == "scene" and asset.scene_id == scene.id:
        return "scene_look"
    if any(binding.asset_id == asset.id for binding in shot.draft.reference_bindings):
        return "shot"
    if any(binding.asset_id == asset.id for binding in project.default_reference_bindings):
        return "project"
    return "candidate"


def _shot_assist_asset_responsibility(shot: StoredShot, asset: StoredAsset) -> str:
    if shot.selected_anchor_asset_id == asset.id:
        return "批准锚点：锁定当前片段开场状态"
    if asset.role == "shot_tail_frame":
        return "上一片段尾帧：只用于判断连续衔接"
    if asset.role == "scene_look":
        return "场景基础定妆：只承担当前场景造型与共同视觉基线"
    return asset.reference_purpose or asset.role


def _previous_tail_state(repository: ShotQueueStore, shot: StoredShot) -> PreviousTailState:
    ordered = sorted(
        repository.list_shots(shot.scene_id),
        key=lambda item: item.order,
    )
    current_index = next(index for index, item in enumerate(ordered) if item.id == shot.id)
    if current_index == 0:
        return PreviousTailState(None, None, None, None, False)
    previous = ordered[current_index - 1]
    source_video_id = previous.selected_video_asset_id
    bound: StoredAsset | None = None
    for binding in shot.draft.reference_bindings:
        if binding.usage is not ReferenceUsage.APPROVED_ANCHOR:
            continue
        candidate = repository.get_asset(binding.asset_id)
        if candidate.role == "shot_tail_frame":
            bound = candidate
            break
    source_id_text = None if source_video_id is None else str(source_video_id)
    stale = bool(
        bound is not None
        and bound.metadata.get("sourceVideoAssetId") != source_id_text
    )
    active = next(
        (
            asset
            for asset in reversed(repository.list_assets(shot_id=previous.id))
            if asset.role == "shot_tail_frame"
            and asset.metadata.get("sourceVideoAssetId") == source_id_text
            and asset.content_ready
        ),
        None,
    )
    return PreviousTailState(previous, source_video_id, active, bound, stale)


def _previous_tail_state_from_read_model(
    read_model: ProjectReadModel,
    shot: StoredShot,
) -> PreviousTailState:
    return _previous_tail_state_from_loaded(
        shot,
        scene_shots=tuple(
            item for item in read_model.shots if item.scene_id == shot.scene_id
        ),
        assets=read_model.assets,
    )


def _previous_tail_state_from_shot_read_model(
    read_model: ShotGenerationReadModel,
) -> PreviousTailState:
    return _previous_tail_state_from_loaded(
        read_model.shot,
        scene_shots=read_model.scene_shots,
        assets=read_model.assets,
    )


def _previous_tail_state_from_loaded(
    shot: StoredShot,
    *,
    scene_shots: tuple[StoredShot, ...],
    assets: tuple[StoredAsset, ...],
) -> PreviousTailState:
    ordered = sorted(scene_shots, key=lambda item: item.order)
    current_index = next(index for index, item in enumerate(ordered) if item.id == shot.id)
    if current_index == 0:
        return PreviousTailState(None, None, None, None, False)
    previous = ordered[current_index - 1]
    assets_by_id = {item.id: item for item in assets}
    source_video_id = previous.selected_video_asset_id
    bound = next(
        (
            assets_by_id.get(binding.asset_id)
            for binding in shot.draft.reference_bindings
            if binding.usage is ReferenceUsage.APPROVED_ANCHOR
            and assets_by_id.get(binding.asset_id) is not None
            and assets_by_id[binding.asset_id].role == "shot_tail_frame"
        ),
        None,
    )
    source_id_text = None if source_video_id is None else str(source_video_id)
    stale = bool(
        bound is not None
        and bound.metadata.get("sourceVideoAssetId") != source_id_text
    )
    active = next(
        (
            asset
            for asset in reversed(assets)
            if asset.shot_card_id == previous.id
            and asset.role == "shot_tail_frame"
            and asset.metadata.get("sourceVideoAssetId") == source_id_text
            and asset.content_ready
        ),
        None,
    )
    return PreviousTailState(previous, source_video_id, active, bound, stale)


def _tail_state_json(state: PreviousTailState) -> dict[str, Any]:
    if state.previous_shot is None:
        return {"available": False, "reason": "first_shot", "stale": False}
    return {
        "available": state.active is not None,
        "previousShotId": str(state.previous_shot.id),
        "sourceVideoAssetId": (
            None if state.source_video_id is None else str(state.source_video_id)
        ),
        "assetId": None if state.active is None else str(state.active.id),
        "boundAssetId": None if state.bound is None else str(state.bound.id),
        "stale": state.stale,
    }


def _is_synthetic_fixture(asset: StoredAsset) -> bool:
    provider_url = str(asset.metadata.get("providerUrl", ""))
    return (
        asset.metadata.get("syntheticFixture") is True
        or provider_url.startswith("cvg-fake://")
    )


def _media_source(asset: StoredAsset) -> MediaSource:
    return MediaSource(
        asset_id=asset.id,
        semantic_key=asset.semantic_key or f"asset:{asset.id}",
        media_type=asset.media_type,
        sha256=asset.sha256,
        metadata=asset.metadata,
    )


def _provider_step_status(value: str) -> StepStatus:
    normalized = value.lower()
    if normalized in {"queued", "pending"}:
        return StepStatus.QUEUED
    if normalized in {"running", "processing"}:
        return StepStatus.RUNNING
    if normalized in {"succeeded", "completed"}:
        return StepStatus.SUCCEEDED
    return StepStatus.FAILED


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "code": getattr(exc, "code", exc.__class__.__name__),
        "message": str(exc),
        "retryable": bool(getattr(exc, "retryable", False)),
        "submissionUnknown": bool(getattr(exc, "submission_unknown", False)),
        "requestId": getattr(exc, "request_id", None),
    }
