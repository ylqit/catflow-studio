"""PostgreSQL persistence for production recipe choices and human reviews."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from ...domain.aigc_canvas import (
    CanvasDiagnostic,
    CanvasNodeType,
    CanvasPortType,
    StoryEventCandidateStatus,
    StoryRevisionStatus,
    creative_brief_canvas_node_id,
    storyboard_quality_diagnostics,
)
from ...domain.production_recipes import (
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    CANON_V4_PROFILE_ID,
    CANON_V4_STYLE_BOARD_KEY,
    CANON_V4_STYLE_NEGATIVE,
    CANON_V4_STYLE_POSITIVE,
    CHARACTER_DESIGN_SEMANTIC_ROLE_BY_SLOT,
    SEEDANCE_2_0_CAPABILITY,
    CatBehaviorMode,
    CharacterDesignRunStage,
    CharacterDesignSlot,
    DirectorWorkflowAdoptionRequest,
    EditorialCutIntent,
    EditorialShotDescriptor,
    EpisodeRules,
    GenerationPlanRevisionDraft,
    GenerationPlanStatus,
    HumanReviewDecision,
    HumanReviewDraft,
    ProductionRecipeInstanceDraft,
    ProductionRecipeInstancePatch,
    ReferenceAuthorityRole,
    StoryboardRevisionStatus,
    canon_reference_keys,
    plan_generation_clips,
    recipe_task_source_hash,
)
from ...domain.workflow import StepKind, StepStatus
from .durable_queue import (
    record_canvas_projection_changed_event,
    record_workflow_task_event,
)
from .models import (
    Asset,
    CanvasEvent,
    CanvasGraphEdge,
    CanvasGraphNode,
    CanvasGroup,
    CanvasGroupMember,
    CanvasGroupTemplate,
    CharacterDesignAsset,
    CharacterDesignRevision,
    GenerationClipShot,
    GenerationPlan,
    HumanReviewDecisionRecord,
    ProductionRecipeInstance,
    ProductionRun,
    PromptRecord,
    Review,
    Scene,
    ShotBeat,
    ShotCard,
    StoryboardRevision,
    StoryBriefRecord,
    StoryEventCandidateRecord,
    StoryRevisionRecord,
    StoryScore,
    Subject,
    VideoSequence,
    VisualProfileRevision,
    WorkflowStep,
)
from .repositories import RecordNotFoundError, WorkflowConflictError
from .story_revision_lifecycle import (
    invalidate_story_production_lineage,
    requires_legacy_story_approval_contract,
)
from .story_scenes import materialize_approved_story_scenes
from .storyboard_hashing import generation_plan_input_hash, storyboard_structure_hash
from .visual_preset_profiles import (
    CANON_V4_REQUIRED_KEYS,
    ensure_canon_subjects,
    ensure_canon_visual_profile,
    episode_visual_profile_json,
    generation_reference_bindings,
    visual_profile_bindings,
    visual_reference_json,
)

_CANON_PROFILE_ID = CANON_V4_PROFILE_ID
_CANON_REQUIRED_KEYS = CANON_V4_REQUIRED_KEYS
logger = logging.getLogger(__name__)
_APPROVING_DECISIONS = {
    HumanReviewDecision.APPROVE.value,
    HumanReviewDecision.OVERRIDE.value,
}


class SqlAlchemyProductionRecipeRepository:
    """Owns recipe persistence separately from the general canvas repository."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def preview_director_workflow_adoption(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, Any]:
        with self._sessions() as session:
            self._required(session, ProductionRun, project_id)
            return self._director_workflow_adoption_preview(session, project_id)

    def adopt_director_workflow(
        self,
        project_id: uuid.UUID,
        payload: DirectorWorkflowAdoptionRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._required(session, ProductionRun, project_id, lock=True)
            existing = session.scalar(
                select(ProductionRecipeInstance).where(
                    ProductionRecipeInstance.production_run_id == project_id
                )
            )
            if existing is not None:
                return {
                    "projectId": str(project_id),
                    "recipeInstanceId": str(existing.id),
                    "adopted": True,
                    "alreadyAdopted": True,
                    "sourceHash": payload.expected_source_hash,
                    "providerCallCount": 0,
                    "instance": self._instance_json(session, existing),
                }

            preview = self._director_workflow_adoption_preview(session, project_id)
            if preview["blockers"]:
                raise WorkflowConflictError(str(preview["blockers"][0]))
            if payload.expected_source_hash != preview["sourceHash"]:
                raise WorkflowConflictError(
                    "项目生产内容已变化，请重新预览后再应用当前制作流程"
                )

            scenes = list(
                session.scalars(
                    select(Scene)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order)
                )
            )
            shots = list(
                session.scalars(
                    select(ShotCard)
                    .join(Scene, ShotCard.scene_id == Scene.id)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order, ShotCard.sort_order)
                )
            )
            latest_story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == project_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
            )
            source_body = "\n\n".join(
                item.source_text.strip() for item in scenes if item.source_text.strip()
            )
            theme = (
                latest_story.title
                if latest_story is not None
                else source_body or project.title
            )
            subjects_by_role, canon_complete = self._initialize_adopted_inputs(
                session,
                project_id=project_id,
                theme=theme,
                target_duration_seconds=payload.target_duration_seconds,
                inspiration_key=None,
            )
            project.canvas_v2_enabled = True
            project.universal_canvas_enabled = True
            instance = ProductionRecipeInstance(
                id=uuid.uuid4(),
                production_run_id=project_id,
                recipe_key=payload.recipe_key.value,
                recipe_version=1,
                revision=1,
                theme=theme,
                inspiration_key=None,
                target_duration_seconds=payload.target_duration_seconds,
                quality_tier=payload.quality_tier.value,
                canon_profile_id=_CANON_PROFILE_ID,
            )
            session.add(instance)
            session.flush()
            self._initialize_recipe_canvas_group(
                session,
                instance,
                subjects_by_role=subjects_by_role,
                allow_missing_canon=not canon_complete,
            )

            story = latest_story
            if story is None:
                brief = session.scalar(
                    select(StoryBriefRecord)
                    .where(StoryBriefRecord.production_run_id == project_id)
                    .order_by(StoryBriefRecord.revision.desc())
                    .limit(1)
                )
                story = StoryRevisionRecord(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    brief_id=None if brief is None else brief.id,
                    revision=(
                        int(
                            session.scalar(
                                select(
                                    func.coalesce(func.max(StoryRevisionRecord.revision), 0)
                                ).where(StoryRevisionRecord.production_run_id == project_id)
                            )
                            or 0
                        )
                        + 1
                    ),
                    strategy="legacy_import",
                    status=StoryRevisionStatus.APPROVED.value,
                    title=project.title,
                    logline=(source_body or project.title)[:2_000],
                    synopsis=source_body or project.title,
                    subject_ids_json=[str(item.id) for item in subjects_by_role.values()],
                    scene_plan_json=[],
                    episode_rules_json={},
                    approved_at=datetime.now(UTC),
                )
                session.add(story)
                session.flush()
            for scene in scenes:
                scene.story_revision_id = story.id

            storyboard = StoryboardRevision(
                id=uuid.uuid4(),
                production_run_id=project_id,
                story_revision_id=story.id,
                revision=(
                    int(
                        session.scalar(
                            select(func.coalesce(func.max(StoryboardRevision.revision), 0)).where(
                                StoryboardRevision.production_run_id == project_id
                            )
                        )
                        or 0
                    )
                    + 1
                ),
                status=StoryboardRevisionStatus.DRAFT.value,
                structure_hash="0" * 64,
                input_bindings_json=[],
            )
            session.add(storyboard)
            session.flush()
            beats: list[ShotBeat] = []
            for shot in shots:
                beat = ShotBeat(
                    id=uuid.uuid5(storyboard.id, f"legacy-shot:{shot.id}"),
                    scene_id=shot.scene_id,
                    shot_card_id=shot.id,
                    story_revision_id=story.id,
                    storyboard_revision_id=storyboard.id,
                    reference_bindings_json=list(shot.reference_bindings_json or []),
                    reference_binding_revision=max(1, int(shot.draft_revision or 1)),
                    sort_order=shot.sort_order,
                    revision=1,
                    title=shot.title,
                    action=shot.direction,
                    visual_description=shot.direction,
                    child_action="",
                    cat_action="",
                    spatial_relation="",
                    contact_occlusion="",
                    shot_size="中景",
                    camera="",
                    lighting="",
                    dialogue="",
                    sound_effect="",
                    music_intent="",
                    wardrobe_state="",
                    prop_state="",
                    continuity_in="",
                    continuity_out="",
                    cut_intent="continuous",
                    duration_seconds=shot.duration_seconds,
                    temporal_beats_json=[],
                    status="draft",
                )
                session.add(beat)
                beats.append(beat)
            session.flush()
            storyboard.structure_hash = storyboard_structure_hash(beats)
            plan_document = {
                "structureHash": storyboard.structure_hash,
                "provider": SEEDANCE_2_0_CAPABILITY.provider,
                "model": SEEDANCE_2_0_CAPABILITY.model,
                "capabilityRevision": SEEDANCE_2_0_CAPABILITY.capability_revision,
                "legacyShotIds": [str(item.id) for item in shots],
            }
            plan = GenerationPlan(
                id=uuid.uuid4(),
                storyboard_revision_id=storyboard.id,
                revision=1,
                status=GenerationPlanStatus.PROPOSED.value,
                provider=SEEDANCE_2_0_CAPABILITY.provider,
                model=SEEDANCE_2_0_CAPABILITY.model,
                capability_revision=SEEDANCE_2_0_CAPABILITY.capability_revision,
                input_hash=_json_document_hash(plan_document),
                estimated_image_call_count=0,
                estimated_video_call_count=len(shots),
                estimated_cost_micros=None,
                warnings_json=["旧版镜头已映射为导演分镜，请在制作包确认前检查时长与参考素材"],
                blockers_json=[],
            )
            session.add(plan)
            session.flush()
            for plan_order, (shot, beat) in enumerate(zip(shots, beats, strict=True), 1):
                shot.generation_plan_id = plan.id
                shot.plan_sort_order = plan_order
                shot.use_scene_look = False
                shot.scene_look_usage = "off"
                session.add(
                    GenerationClipShot(
                        id=uuid.uuid5(plan.id, f"legacy-clip:{shot.id}"),
                        generation_plan_id=plan.id,
                        shot_card_id=shot.id,
                        shot_beat_id=beat.id,
                        ordinal=1,
                        start_second=0,
                        end_second=shot.duration_seconds,
                        transition_in="continuous",
                    )
                )
            adoption_event = CanvasEvent(
                production_run_id=project_id,
                event_type="director_workflow_adopted",
                data_json={
                    "projectId": str(project_id),
                    "recipeInstanceId": str(instance.id),
                    "sourceHash": preview["sourceHash"],
                    "idempotencyKey": idempotency_key,
                    "providerCallCount": 0,
                },
            )
            if session.get_bind().dialect.name == "sqlite":
                adoption_event.sequence = int(
                    session.scalar(select(func.coalesce(func.max(CanvasEvent.sequence), 0)))
                    or 0
                ) + 1
            session.add(adoption_event)
            session.flush()
            return {
                "projectId": str(project_id),
                "recipeInstanceId": str(instance.id),
                "adopted": True,
                "alreadyAdopted": False,
                "sourceHash": preview["sourceHash"],
                "providerCallCount": 0,
                "instance": self._instance_json(session, instance),
            }

    @staticmethod
    def _director_workflow_adoption_preview(
        session: Session,
        project_id: uuid.UUID,
    ) -> dict[str, Any]:
        existing = session.scalar(
            select(ProductionRecipeInstance).where(
                ProductionRecipeInstance.production_run_id == project_id
            )
        )
        scenes = list(
            session.scalars(
                select(Scene)
                .where(
                    Scene.production_run_id == project_id,
                    Scene.active.is_(True),
                )
                .order_by(Scene.sort_order)
            )
        )
        shots = list(
            session.scalars(
                select(ShotCard)
                .join(Scene, ShotCard.scene_id == Scene.id)
                .where(
                    Scene.production_run_id == project_id,
                    Scene.active.is_(True),
                )
                .order_by(Scene.sort_order, ShotCard.sort_order)
            )
        )
        active_paid_tasks = list(
            session.scalars(
                select(WorkflowStep).where(
                    WorkflowStep.production_run_id == project_id,
                    WorkflowStep.kind.in_((StepKind.IMAGE.value, StepKind.VIDEO.value)),
                    WorkflowStep.status.in_(
                        (
                            StepStatus.PENDING.value,
                            StepStatus.SUBMITTING.value,
                            StepStatus.SUBMISSION_UNKNOWN.value,
                            StepStatus.CANCELLING.value,
                            StepStatus.CANCELLATION_UNKNOWN.value,
                            StepStatus.QUEUED.value,
                            StepStatus.RUNNING.value,
                        )
                    ),
                )
            )
        )
        canon_keys = set(
            session.scalars(
                select(Asset.semantic_key).where(
                    Asset.scope == "canon",
                    Asset.status.in_(("ready", "approved")),
                    Asset.semantic_key.in_(_CANON_REQUIRED_KEYS),
                )
            )
        )
        missing_canon = sorted(set(_CANON_REQUIRED_KEYS).difference(canon_keys))
        source_document = {
            "projectId": str(project_id),
            "scenes": [
                {
                    "id": str(scene.id),
                    "order": scene.sort_order,
                    "title": scene.title,
                    "sourceText": scene.source_text,
                    "selectedLookAssetId": (
                        None
                        if scene.selected_look_asset_id is None
                        else str(scene.selected_look_asset_id)
                    ),
                }
                for scene in scenes
            ],
            "shots": [
                {
                    "id": str(shot.id),
                    "sceneId": str(shot.scene_id),
                    "order": shot.sort_order,
                    "title": shot.title,
                    "direction": shot.direction,
                    "durationSeconds": shot.duration_seconds,
                    "draftRevision": shot.draft_revision,
                    "selectedVideoAssetId": (
                        None
                        if shot.selected_video_asset_id is None
                        else str(shot.selected_video_asset_id)
                    ),
                }
                for shot in shots
            ],
        }
        blockers = []
        if not scenes:
            blockers.append("旧版看板没有可采用的活动场景")
        if not shots:
            blockers.append("旧版看板没有可采用的镜头")
        if active_paid_tasks:
            blockers.append("项目仍有付费媒体任务在排队、提交、运行或等待对账")
        warnings = []
        if missing_canon:
            warnings.append("人物与猫咪 Canon 尚未完整，可在采用后继续补齐")
        return {
            "projectId": str(project_id),
            "eligible": not blockers and existing is None,
            "alreadyAdopted": existing is not None,
            "recipeInstanceId": None if existing is None else str(existing.id),
            "sourceHash": _json_document_hash(source_document),
            "summary": {
                "sceneCount": len(scenes),
                "shotCount": len(shots),
                "selectedVideoCount": sum(
                    shot.selected_video_asset_id is not None for shot in shots
                ),
            },
            "warnings": warnings,
            "blockers": blockers,
            "providerCallCount": 0,
        }

    def create_instance(
        self,
        project_id: uuid.UUID,
        payload: ProductionRecipeInstanceDraft,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._required(session, ProductionRun, project_id, lock=True)
            existing = session.scalar(
                select(ProductionRecipeInstance).where(
                    ProductionRecipeInstance.production_run_id == project_id
                )
            )
            if existing is not None:
                raise WorkflowConflictError("项目已经存在创作配方实例")
            existing_node_types = set(
                session.scalars(
                    select(CanvasGraphNode.node_type).where(
                        CanvasGraphNode.production_run_id == project_id
                    )
                )
            )
            prerequisite_node_types = {
                CanvasNodeType.BRIEF.value,
                CanvasNodeType.SUBJECT.value,
                CanvasNodeType.STYLE_PRESET.value,
            }
            conflicting_node_types = sorted(
                existing_node_types.difference(prerequisite_node_types)
            )
            if conflicting_node_types:
                raise WorkflowConflictError(
                    "项目已经包含下游生产节点，不能再创建组合包："
                    + "、".join(conflicting_node_types)
                )
            subjects_by_role = self._initialize_fixed_ip_inputs(
                session,
                project_id=project_id,
                theme=payload.theme,
                target_duration_seconds=payload.target_duration_seconds,
                inspiration_key=payload.inspiration_key,
            )
            project.canvas_v2_enabled = True
            project.universal_canvas_enabled = True
            row = ProductionRecipeInstance(
                id=uuid.uuid4(),
                production_run_id=project_id,
                recipe_key=payload.recipe_key.value,
                recipe_version=1,
                revision=1,
                theme=payload.theme,
                inspiration_key=payload.inspiration_key,
                target_duration_seconds=payload.target_duration_seconds,
                quality_tier=payload.quality_tier.value,
                canon_profile_id=_CANON_PROFILE_ID,
            )
            session.add(row)
            session.flush()
            self._initialize_recipe_canvas_group(
                session,
                row,
                subjects_by_role=subjects_by_role,
            )
            session.flush()
            return self._instance_json(session, row)

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
    ) -> dict[str, Any]:
        durable_key = hashlib.sha256(
            f"recipe-task:{instance_id}:{shot_id or '-'}:{operation_key}:{idempotency_key}".encode()
        ).hexdigest()
        with self._sessions.begin() as session:
            instance = self._required(
                session,
                ProductionRecipeInstance,
                instance_id,
                lock=operation_key == "recipe:character_design_validation",
            )
            existing = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == durable_key)
            )
            if existing is not None:
                return _workflow_task_json(existing)
            if operation_key == "recipe:character_design_validation":
                active_validation = session.scalar(
                    select(WorkflowStep.id)
                    .where(
                        WorkflowStep.production_run_id == instance.production_run_id,
                        WorkflowStep.operation_key == operation_key,
                        WorkflowStep.status.in_(
                            {
                                StepStatus.PENDING.value,
                                StepStatus.SUBMITTING.value,
                                StepStatus.QUEUED.value,
                                StepStatus.RUNNING.value,
                                StepStatus.SUBMISSION_UNKNOWN.value,
                            }
                        ),
                    )
                    .limit(1)
                )
                if active_validation is not None:
                    raise WorkflowConflictError(
                        "已有三槽位引用验证任务仍在运行或等待 Provider 对账"
                    )
            snapshot = {
                "payload": payload,
                "projectId": str(instance.production_run_id),
                "shotId": None if shot_id is None else str(shot_id),
                "canvasNodeId": str(canvas_node_id),
                "canvasGroupId": None if group_id is None else str(group_id),
                "recipeInstanceId": str(instance_id),
                "creationMode": creation_mode,
                "workflowStage": expected_phase,
                "phase": expected_phase,
                "operationKey": operation_key,
                "canonProfileId": instance.canon_profile_id,
                "expectedInstanceRevision": expected_revision,
                "sourceContentHash": recipe_task_source_hash(
                    payload=payload,
                    instance_id=instance_id,
                    expected_revision=expected_revision,
                    phase=expected_phase,
                ),
            }
            input_hash = hashlib.sha256(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            attempt = 1
            if shot_id is not None:
                attempt = (
                    int(
                        session.scalar(
                            select(func.max(WorkflowStep.attempt)).where(
                                WorkflowStep.shot_card_id == shot_id,
                                WorkflowStep.operation_key == operation_key,
                            )
                        )
                        or 0
                    )
                    + 1
                )
            row = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=instance.production_run_id,
                shot_card_id=shot_id,
                kind=kind.value,
                status=StepStatus.PENDING.value,
                attempt=attempt,
                operation_key=operation_key,
                idempotency_key=durable_key,
                input_hash=input_hash,
                input_snapshot_json=snapshot,
                progress_json={
                    "currentStep": 0,
                    "totalSteps": 3,
                    "percent": 0,
                    "message": "任务已进入持久队列",
                },
            )
            session.add(row)
            session.flush()
            session.add(
                CanvasEvent(
                    production_run_id=instance.production_run_id,
                    event_type="task_queued",
                    data_json={
                        **{
                            key: snapshot.get(key)
                            for key in (
                                "projectId",
                                "shotId",
                                "canvasNodeId",
                                "canvasGroupId",
                                "recipeInstanceId",
                                "businessObjectId",
                                "parentStepId",
                                "creationMode",
                                "phase",
                            )
                        },
                        "stepId": str(row.id),
                        "status": row.status,
                        "operationKey": operation_key,
                        "kind": row.kind,
                        "progress": row.progress_json,
                        "childStepIds": row.progress_json.get("childStepIds", []),
                    },
                )
            )
            return _workflow_task_json(row)

    def record_task_children(
        self,
        parent_step_id: uuid.UUID,
        child_step_ids: tuple[uuid.UUID, ...],
    ) -> tuple[dict[str, Any], ...]:
        ordered_ids = tuple(dict.fromkeys(child_step_ids))
        if not ordered_ids:
            return ()
        if parent_step_id in ordered_ids:
            raise WorkflowConflictError("任务不能将自身登记为子任务")
        with self._sessions.begin() as session:
            parent = self._required(session, WorkflowStep, parent_step_id, lock=True)
            children = list(
                session.scalars(
                    select(WorkflowStep).where(WorkflowStep.id.in_(ordered_ids)).with_for_update()
                )
            )
            by_id = {child.id: child for child in children}
            missing = [step_id for step_id in ordered_ids if step_id not in by_id]
            if missing:
                raise RecordNotFoundError(
                    f"父任务引用了不存在的子任务：{', '.join(str(item) for item in missing)}"
                )
            if any(child.production_run_id != parent.production_run_id for child in children):
                raise WorkflowConflictError("父子任务必须属于同一个项目")
            serialized_ids = [str(step_id) for step_id in ordered_ids]
            parent_has_same_children = (
                list(dict(parent.progress_json or {}).get("childStepIds", [])) == serialized_ids
            )
            children_have_same_parent = all(
                dict(child.input_snapshot_json or {}).get("parentStepId") == str(parent.id)
                for child in children
            )
            if parent_has_same_children and children_have_same_parent:
                return tuple(
                    {
                        "stepId": str(step_id),
                        "status": by_id[step_id].status,
                        "providerTaskId": by_id[step_id].provider_task_id,
                        "error": by_id[step_id].error_json,
                        "resultSummary": dict(by_id[step_id].progress_json or {}).get(
                            "resultSummary"
                        ),
                    }
                    for step_id in ordered_ids
                )
            for child in children:
                child.input_snapshot_json = {
                    **dict(child.input_snapshot_json or {}),
                    "parentStepId": str(parent.id),
                }
            parent.progress_json = {
                **dict(parent.progress_json or {}),
                "childStepIds": serialized_ids,
            }
            parent_snapshot = dict(parent.input_snapshot_json or {})
            session.add(
                CanvasEvent(
                    production_run_id=parent.production_run_id,
                    event_type="task_progress",
                    data_json={
                        "stepId": str(parent.id),
                        "projectId": str(parent.production_run_id),
                        "status": parent.status,
                        "operationKey": parent.operation_key,
                        "kind": parent.kind,
                        "canvasNodeId": parent_snapshot.get("canvasNodeId"),
                        "canvasGroupId": parent_snapshot.get("canvasGroupId"),
                        "recipeInstanceId": parent_snapshot.get("recipeInstanceId"),
                        "businessObjectId": parent_snapshot.get("businessObjectId"),
                        "childStepIds": serialized_ids,
                        "phase": parent_snapshot.get("phase")
                        or parent_snapshot.get("workflowStage"),
                        "progress": parent.progress_json,
                    },
                )
            )
            return tuple(
                {
                    "stepId": str(step_id),
                    "status": by_id[step_id].status,
                    "providerTaskId": by_id[step_id].provider_task_id,
                    "error": by_id[step_id].error_json,
                    "resultSummary": dict(by_id[step_id].progress_json or {}).get("resultSummary"),
                }
                for step_id in ordered_ids
            )

    @staticmethod
    def _initialize_recipe_canvas_group(
        session: Session,
        instance: ProductionRecipeInstance,
        *,
        subjects_by_role: dict[str, Subject],
        allow_missing_canon: bool = False,
    ) -> None:
        project_id = instance.production_run_id
        brief = session.scalar(
            select(StoryBriefRecord)
            .where(StoryBriefRecord.production_run_id == project_id)
            .order_by(StoryBriefRecord.revision.desc())
            .limit(1)
        )
        if brief is None:
            raise RecordNotFoundError("组合包创作简报不存在")
        ids = {
            "brief": creative_brief_canvas_node_id(project_id),
            "creative_gate": uuid.uuid5(project_id, "creative-brief-approval"),
            "planner": uuid.uuid5(project_id, "story-planner"),
            "story_gate": uuid.uuid5(project_id, "story-approval"),
            "style_preset": uuid.uuid5(project_id, "style-preset:line-texture"),
            "child_design": uuid.uuid5(project_id, "character-design:child"),
            "cat_design": uuid.uuid5(project_id, "character-design:cat"),
            "pair_design": uuid.uuid5(project_id, "character-design:pair-scale"),
            "character_gate": uuid.uuid5(project_id, "character-design-approval"),
            "storyboard": uuid.uuid5(project_id, "storyboard-director"),
            "anchors": uuid.uuid5(project_id, "recipe-anchor-stage"),
            "videos": uuid.uuid5(project_id, "recipe-video-stage"),
            "video_review": uuid.uuid5(project_id, "recipe-video-review"),
            "timeline": uuid.uuid5(project_id, "recipe-sequence"),
        }
        child = subjects_by_role.get("protagonist")
        cat = subjects_by_role.get("co_protagonist")
        if child is None or cat is None:
            raise WorkflowConflictError("一人一猫组合包缺少固定儿童或固定猫咪")

        node_specs = [
            (
                ids["brief"],
                CanvasNodeType.BRIEF,
                "story_brief",
                brief.id,
                "待确认创意简报",
                "creative",
            ),
            (
                ids["creative_gate"],
                CanvasNodeType.APPROVAL_GATE,
                "creative_brief_approval",
                brief.id,
                "确认创意输入",
                "creative",
            ),
            (child.id, CanvasNodeType.SUBJECT, "subject", child.id, "固定儿童", "character_design"),
            (cat.id, CanvasNodeType.SUBJECT, "subject", cat.id, "固定猫咪", "character_design"),
            (
                ids["style_preset"],
                CanvasNodeType.STYLE_PRESET,
                "visual_preset",
                None,
                "线条材质",
                "character_design",
            ),
            (
                ids["planner"],
                CanvasNodeType.STORY_PLANNER,
                "story_planner",
                project_id,
                "生成完整故事候选",
                "story",
            ),
            (
                ids["story_gate"],
                CanvasNodeType.APPROVAL_GATE,
                "story_approval",
                project_id,
                "选择为当前剧情",
                "story",
            ),
            (
                ids["child_design"],
                CanvasNodeType.CHARACTER_DESIGN,
                "character_design_slot",
                None,
                "儿童本集造型图",
                "character_design",
            ),
            (
                ids["cat_design"],
                CanvasNodeType.CHARACTER_DESIGN,
                "character_design_slot",
                None,
                "猫咪本集造型图",
                "character_design",
            ),
            (
                ids["pair_design"],
                CanvasNodeType.CHARACTER_DESIGN,
                "character_design_slot",
                None,
                "一人一猫同框比例图",
                "character_design",
            ),
            (
                ids["character_gate"],
                CanvasNodeType.APPROVAL_GATE,
                "character_design_approval",
                project_id,
                "角色设计审核",
                "character_design",
            ),
            (
                ids["storyboard"],
                CanvasNodeType.STORYBOARD_DIRECTOR,
                "storyboard_director",
                project_id,
                "分镜生成",
                "storyboard",
            ),
            (
                ids["anchors"],
                CanvasNodeType.IMAGE_GENERATION,
                "recipe_anchor_stage",
                instance.id,
                "视觉锚点",
                "render",
            ),
            (
                ids["videos"],
                CanvasNodeType.VIDEO_GENERATION,
                "recipe_video_stage",
                instance.id,
                "逐镜视频渲染",
                "render",
            ),
            (
                ids["video_review"],
                CanvasNodeType.REVIEW,
                "recipe_video_review",
                instance.id,
                "逐镜视频审核",
                "render",
            ),
            (
                ids["timeline"],
                CanvasNodeType.TIMELINE,
                "recipe_sequence",
                instance.id,
                "成品导出",
                "export",
            ),
        ]
        slot_by_node = {
            ids["child_design"]: CharacterDesignSlot.CHILD.value,
            ids["cat_design"]: CharacterDesignSlot.CAT.value,
            ids["pair_design"]: CharacterDesignSlot.PAIR_SCALE.value,
        }
        graph_nodes: dict[uuid.UUID, CanvasGraphNode] = {}
        for node_id, node_type, object_type, object_id, title, phase in node_specs:
            node = session.get(CanvasGraphNode, node_id)
            data = {
                "title": title,
                "recipeInstanceId": str(instance.id),
                "phase": phase,
            }
            if node_id in slot_by_node:
                data["slot"] = slot_by_node[node_id]
            if node_id == ids["style_preset"]:
                is_v4 = instance.canon_profile_id == CANON_V4_PROFILE_ID
                style_key = CANON_V4_STYLE_BOARD_KEY if is_v4 else "style:line_texture"
                style_positive = CANON_V4_STYLE_POSITIVE if is_v4 else CANON_V3_STYLE_POSITIVE
                style_negative = CANON_V4_STYLE_NEGATIVE if is_v4 else CANON_V3_STYLE_NEGATIVE
                style_asset = session.scalar(
                    select(Asset).where(
                        Asset.scope == "canon",
                        Asset.status.in_(("ready", "approved")),
                        Asset.semantic_key == style_key,
                    )
                )
                if style_asset is None:
                    if not allow_missing_canon:
                        raise WorkflowConflictError("当前 Canon 缺少可提交 Provider 的画风板")
                    data.update(
                        {
                            "presetKey": (
                                "healing_child_cat_style_board_v4"
                                if is_v4
                                else "healing_child_cat_line_texture_v3"
                            ),
                            "canonProfileId": instance.canon_profile_id,
                            "references": [],
                            "stylePositive": list(style_positive),
                            "styleExcluded": list(style_negative),
                            "locked": False,
                            "blocker": "请补齐人物、猫咪身份 Canon 与纯画风板",
                        }
                    )
                else:
                    data.update(
                        {
                            "presetKey": (
                                "healing_child_cat_style_board_v4"
                                if is_v4
                                else "healing_child_cat_line_texture_v3"
                            ),
                            "canonProfileId": instance.canon_profile_id,
                            "references": [visual_reference_json(style_asset, required=True)],
                            "stylePositive": list(style_positive),
                            "styleExcluded": list(style_negative),
                            "locked": True,
                        }
                    )
            if node is None:
                node = CanvasGraphNode(
                    id=node_id,
                    production_run_id=project_id,
                    node_type=node_type.value,
                    object_type=object_type,
                    object_id=object_id,
                    status="ready" if phase in {"creative", "story"} else "blocked",
                    data_json=data,
                )
                session.add(node)
            else:
                node.node_type = node_type.value
                node.object_type = object_type
                node.object_id = object_id
                node.data_json = {**dict(node.data_json or {}), **data}
            graph_nodes[node_id] = node
        session.flush()

        group = CanvasGroup(
            id=uuid.uuid5(project_id, f"canvas-group:{instance.id}"),
            production_run_id=project_id,
            production_recipe_instance_id=instance.id,
            group_type="recipe",
            title="一人一猫治愈短片",
            lifecycle_status="active",
            color="#7c9cff",
            data_json={"recipeKey": instance.recipe_key, "sixStageWorkflow": True},
        )
        session.add(group)
        session.flush()
        for sort_order, node in enumerate(graph_nodes.values(), 1):
            session.add(
                CanvasGroupMember(
                    id=uuid.uuid5(group.id, f"member:{node.id}"),
                    group_id=group.id,
                    canvas_node_id=node.id,
                    sort_order=sort_order,
                )
            )

        edge_specs = [
            (
                ids["brief"],
                CanvasPortType.BRIEF,
                ids["creative_gate"],
                CanvasPortType.BRIEF,
                "creative_review",
            ),
            (
                ids["creative_gate"],
                CanvasPortType.BRIEF,
                ids["planner"],
                CanvasPortType.BRIEF,
                "approved_input",
            ),
            (
                child.id,
                CanvasPortType.SUBJECTS,
                ids["planner"],
                CanvasPortType.SUBJECTS,
                "story_subject",
            ),
            (
                cat.id,
                CanvasPortType.SUBJECTS,
                ids["planner"],
                CanvasPortType.SUBJECTS,
                "story_subject",
            ),
            (
                ids["planner"],
                CanvasPortType.STORY_REVISION,
                ids["story_gate"],
                CanvasPortType.STORY_REVISION,
                "story_candidates",
            ),
            (
                child.id,
                CanvasPortType.SUBJECTS,
                ids["child_design"],
                CanvasPortType.SUBJECTS,
                "identity_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["child_design"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["cat_design"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["pair_design"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["storyboard"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                ids["style_preset"],
                CanvasPortType.IMAGE_REFERENCES,
                ids["anchors"],
                CanvasPortType.IMAGE_REFERENCES,
                "style_source",
            ),
            (
                cat.id,
                CanvasPortType.SUBJECTS,
                ids["cat_design"],
                CanvasPortType.SUBJECTS,
                "identity_source",
            ),
            (
                child.id,
                CanvasPortType.SUBJECTS,
                ids["pair_design"],
                CanvasPortType.SUBJECTS,
                "identity_source",
            ),
            (
                cat.id,
                CanvasPortType.SUBJECTS,
                ids["pair_design"],
                CanvasPortType.SUBJECTS,
                "identity_source",
            ),
            (
                ids["story_gate"],
                CanvasPortType.STORY_REVISION,
                ids["child_design"],
                CanvasPortType.STORY_REVISION,
                "approved_story",
            ),
            (
                ids["story_gate"],
                CanvasPortType.STORY_REVISION,
                ids["cat_design"],
                CanvasPortType.STORY_REVISION,
                "approved_story",
            ),
            (
                ids["story_gate"],
                CanvasPortType.STORY_REVISION,
                ids["pair_design"],
                CanvasPortType.STORY_REVISION,
                "approved_story",
            ),
            (
                ids["child_design"],
                CanvasPortType.CHARACTER_DESIGN,
                ids["character_gate"],
                CanvasPortType.CHARACTER_DESIGN,
                "design_review",
            ),
            (
                ids["cat_design"],
                CanvasPortType.CHARACTER_DESIGN,
                ids["character_gate"],
                CanvasPortType.CHARACTER_DESIGN,
                "design_review",
            ),
            (
                ids["pair_design"],
                CanvasPortType.CHARACTER_DESIGN,
                ids["character_gate"],
                CanvasPortType.CHARACTER_DESIGN,
                "design_review",
            ),
            (
                ids["character_gate"],
                CanvasPortType.CHARACTER_DESIGN,
                ids["storyboard"],
                CanvasPortType.CHARACTER_DESIGN,
                "approved_design",
            ),
            (
                ids["storyboard"],
                CanvasPortType.SCENE_PLAN,
                ids["anchors"],
                CanvasPortType.SHOT_BEATS,
                "storyboard_to_anchor",
            ),
            (
                ids["anchors"],
                CanvasPortType.IMAGE_ASSET,
                ids["videos"],
                CanvasPortType.IMAGE_ASSET,
                "anchor_to_video",
            ),
            (
                ids["videos"],
                CanvasPortType.VIDEO_ASSET,
                ids["video_review"],
                CanvasPortType.VIDEO_ASSET,
                "video_review",
            ),
            (
                ids["video_review"],
                CanvasPortType.APPROVED_ASSET,
                ids["timeline"],
                CanvasPortType.APPROVED_ASSET,
                "video_to_sequence",
            ),
        ]
        for source_id, source_port, target_id, target_port, relation_type in edge_specs:
            session.add(
                CanvasGraphEdge(
                    id=uuid.uuid5(
                        source_id, f"{source_port.value}:{target_id}:{target_port.value}"
                    ),
                    production_run_id=project_id,
                    source_node_id=source_id,
                    source_port=source_port.value,
                    target_node_id=target_id,
                    target_port=target_port.value,
                    relation_type=relation_type,
                    revision=1,
                )
            )

    @staticmethod
    def _initialize_adopted_inputs(
        session: Session,
        *,
        project_id: uuid.UUID,
        theme: str,
        target_duration_seconds: int,
        inspiration_key: str | None,
    ) -> tuple[dict[str, Subject], bool]:
        assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.scope == "canon",
                    Asset.status.in_(("ready", "approved")),
                    Asset.semantic_key.in_(_CANON_REQUIRED_KEYS),
                )
            )
        )
        if {asset.semantic_key for asset in assets} == set(_CANON_REQUIRED_KEYS):
            return (
                SqlAlchemyProductionRecipeRepository._initialize_fixed_ip_inputs(
                    session,
                    project_id=project_id,
                    theme=theme,
                    target_duration_seconds=target_duration_seconds,
                    inspiration_key=inspiration_key,
                ),
                True,
            )

        SqlAlchemyProductionRecipeRepository._create_recipe_brief(
            session,
            project_id=project_id,
            theme=theme,
            target_duration_seconds=target_duration_seconds,
            inspiration_key=inspiration_key,
        )
        existing_subjects = list(
            session.scalars(
                select(Subject).where(
                    Subject.production_run_id == project_id,
                    Subject.role.in_(("protagonist", "co_protagonist")),
                )
            )
        )
        subjects_by_role: dict[str, Subject] = {}
        for subject in existing_subjects:
            current = subjects_by_role.get(subject.role)
            if current is None or (
                current.status not in {"ready", "approved"}
                and subject.status in {"ready", "approved"}
            ):
                subjects_by_role[subject.role] = subject
        for role, kind in (("protagonist", "person"), ("co_protagonist", "animal")):
            if role in subjects_by_role:
                continue
            subject = Subject(
                id=uuid.uuid5(project_id, f"legacy-adoption-subject:{role}"),
                production_run_id=project_id,
                kind=kind,
                role=role,
                status="draft",
            )
            session.add(subject)
            subjects_by_role[role] = subject
        session.flush()
        return subjects_by_role, False

    @staticmethod
    def _initialize_fixed_ip_inputs(
        session: Session,
        *,
        project_id: uuid.UUID,
        theme: str,
        target_duration_seconds: int,
        inspiration_key: str | None,
    ) -> dict[str, Subject]:
        required_keys = _CANON_REQUIRED_KEYS
        assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.scope == "canon",
                    Asset.status.in_(("ready", "approved")),
                    Asset.semantic_key.in_(required_keys),
                )
            )
        )
        assets_by_key = {asset.semantic_key: asset for asset in assets}
        missing = [key for key in required_keys if key not in assets_by_key]
        if missing:
            raise ValueError(f"创建组合包前请补齐 Canon-v4 参考：{', '.join(missing)}")

        SqlAlchemyProductionRecipeRepository._create_recipe_brief(
            session,
            project_id=project_id,
            theme=theme,
            target_duration_seconds=target_duration_seconds,
            inspiration_key=inspiration_key,
        )
        subjects_by_role = ensure_canon_subjects(
            session,
            project_id=project_id,
            assets_by_key=assets_by_key,
            canon_profile_id=_CANON_PROFILE_ID,
        )
        ensure_canon_visual_profile(
            session,
            project_id=project_id,
            canon_profile_id=_CANON_PROFILE_ID,
            assets_by_key=assets_by_key,
        )
        return subjects_by_role

    @staticmethod
    def _create_recipe_brief(
        session: Session,
        *,
        project_id: uuid.UUID,
        theme: str,
        target_duration_seconds: int,
        inspiration_key: str | None,
    ) -> None:
        latest_brief = session.scalar(
            select(StoryBriefRecord)
            .where(StoryBriefRecord.production_run_id == project_id)
            .order_by(StoryBriefRecord.revision.desc())
            .limit(1)
        )
        session.add(
            StoryBriefRecord(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=1 if latest_brief is None else latest_brief.revision + 1,
                theme=theme,
                audience="喜欢低压力、治愈日常内容的竖屏短视频观众",
                genre="原创一人一猫治愈日常",
                tone="安静、温暖、克制、有生活细节",
                aspect_ratio="9:16",
                target_duration_seconds=target_duration_seconds,
                constraints_json=[
                    "固定儿童与固定猫咪身份",
                    "原创二维治愈数字插画，统一线条、材质与光线",
                    "无对白",
                    "原生环境声、动作声与轻音乐",
                    "每镜包含开始、小变化、温暖收尾三个时间节拍",
                    *([] if inspiration_key is None else [f"灵感卡：{inspiration_key}"]),
                ],
            )
        )

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            row = self._required(session, ProductionRecipeInstance, instance_id)
            return self._instance_json(session, row)

    def validate_storyboard_character_references(
        self,
        instance_id: uuid.UUID,
        reference_asset_ids: tuple[uuid.UUID, ...],
    ) -> None:
        """Enforce that character-led storyboards use the approved child and cat slots."""

        with self._sessions() as session:
            self._required(session, ProductionRecipeInstance, instance_id)
            revision = session.scalar(
                select(CharacterDesignRevision)
                .where(
                    CharacterDesignRevision.production_recipe_instance_id == instance_id,
                    CharacterDesignRevision.status == "approved",
                )
                .order_by(CharacterDesignRevision.revision.desc())
                .limit(1)
            )
            if revision is None:
                raise WorkflowConflictError(
                    "基于固定角色补充分镜需要先批准本集儿童、猫咪和同框比例设计"
                )

            selected_assets = list(
                session.scalars(
                    select(CharacterDesignAsset).where(
                        CharacterDesignAsset.character_design_revision_id == revision.id,
                        CharacterDesignAsset.selected.is_(True),
                    )
                )
            )
            selected_by_id = {asset.asset_id: asset for asset in selected_assets}
            provided_ids = set(reference_asset_ids)
            unapproved_ids = provided_ids.difference(selected_by_id)
            if unapproved_ids:
                raise WorkflowConflictError(
                    "基于固定角色补充分镜只能引用当前已批准的角色设计素材，"
                    "普通参考图不能替代 Canon 身份"
                )

            provided_slots = {
                selected_by_id[asset_id].slot
                for asset_id in provided_ids
                if asset_id in selected_by_id
            }
            required_slots = {
                CharacterDesignSlot.CHILD.value,
                CharacterDesignSlot.CAT.value,
            }
            missing_slots = required_slots.difference(provided_slots)
            if missing_slots:
                missing_labels = [
                    label
                    for slot, label in (
                        (CharacterDesignSlot.CHILD.value, "儿童"),
                        (CharacterDesignSlot.CAT.value, "猫咪"),
                    )
                    if slot in missing_slots
                ]
                raise WorkflowConflictError(
                    f"基于固定角色补充分镜必须同时包含已批准的{'和'.join(missing_labels)}角色设计素材"
                )

    def validate_anchor_prompt_readiness(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
    ) -> None:
        """Reject anchor generation when its audited layered prompt is absent or stale."""

        with self._sessions() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id)
            shot = self._required(session, ShotCard, shot_id)
            scene = self._required(session, Scene, shot.scene_id)
            if scene.production_run_id != instance.production_run_id or not scene.active:
                raise WorkflowConflictError("镜头所属场景已过期，请重新生成分镜")
            plan = (
                None
                if shot.generation_plan_id is None
                else session.get(GenerationPlan, shot.generation_plan_id)
            )
            storyboard = (
                None
                if plan is None
                else session.get(StoryboardRevision, plan.storyboard_revision_id)
            )
            if (
                plan is None
                or plan.status != GenerationPlanStatus.APPROVED.value
                or storyboard is None
                or storyboard.status != StoryboardRevisionStatus.PRODUCTION_APPROVED.value
                or shot.prompt_id is None
            ):
                raise WorkflowConflictError("生产分镜包尚未批准，不能生成视觉锚点")
            prompt = self._required(session, PromptRecord, shot.prompt_id)
            snapshot = dict(prompt.input_snapshot_json or {})
            project = self._required(session, ProductionRun, instance.production_run_id)
            if (
                prompt.status != "succeeded"
                or prompt.call_purpose != "storyboard_prompt_compilation"
                or str(snapshot.get("storyboardRevisionId")) != str(storyboard.id)
                or str(snapshot.get("generationPlanId")) != str(plan.id)
                or str(snapshot.get("visualProfileRevisionId"))
                != str(project.current_visual_profile_revision_id)
                or int(snapshot.get("sceneLookDraftRevision") or 0) != scene.look_draft_revision
            ):
                raise WorkflowConflictError(
                    "镜头 Prompt 已因剧情、视觉档案或场景资产更新而过期，请重新编译"
                )

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            row = self._required(session, ProductionRecipeInstance, instance_id, lock=True)
            if row.revision != expected_revision:
                raise WorkflowConflictError(
                    f"配方实例版本冲突：当前 {row.revision}，提交 {expected_revision}"
                )
            changed = payload.model_dump(exclude_unset=True)
            semantic_changed = any(
                (
                    key == "theme"
                    and row.theme != value
                    or key == "inspiration_key"
                    and row.inspiration_key != value
                    or key == "target_duration_seconds"
                    and row.target_duration_seconds != value
                )
                for key, value in changed.items()
            )
            if "theme" in changed:
                row.theme = changed["theme"]
            if "inspiration_key" in changed:
                row.inspiration_key = changed["inspiration_key"]
            if "target_duration_seconds" in changed:
                row.target_duration_seconds = changed["target_duration_seconds"]
            if "quality_tier" in changed:
                row.quality_tier = changed["quality_tier"].value
            row.revision += 1
            if semantic_changed:
                latest_brief = session.scalar(
                    select(StoryBriefRecord)
                    .where(StoryBriefRecord.production_run_id == row.production_run_id)
                    .order_by(StoryBriefRecord.revision.desc())
                    .limit(1)
                )
                if latest_brief is None:
                    raise RecordNotFoundError("组合包创作简报不存在")
                constraints = [
                    item
                    for item in latest_brief.constraints_json
                    if not item.startswith("灵感卡：")
                ]
                if row.inspiration_key:
                    constraints.append(f"灵感卡：{row.inspiration_key}")
                session.add(
                    StoryBriefRecord(
                        id=uuid.uuid4(),
                        production_run_id=row.production_run_id,
                        revision=latest_brief.revision + 1,
                        theme=row.theme,
                        audience=latest_brief.audience,
                        genre=latest_brief.genre,
                        tone=latest_brief.tone,
                        aspect_ratio=latest_brief.aspect_ratio,
                        target_duration_seconds=row.target_duration_seconds,
                        constraints_json=constraints,
                    )
                )
                self._invalidate_downstream(session, row.production_run_id, row.revision)
            session.flush()
            return self._instance_json(session, row)

    def revise_generation_plan(
        self,
        instance_id: uuid.UUID,
        plan_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: GenerationPlanRevisionDraft,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id, lock=True)
            located_plan = session.get(GenerationPlan, plan_id)
            if located_plan is None:
                raise RecordNotFoundError(f"GenerationPlan not found: {plan_id}")
            storyboard = self._required(
                session,
                StoryboardRevision,
                located_plan.storyboard_revision_id,
                lock=True,
            )
            if storyboard.production_run_id != instance.production_run_id:
                raise ValueError("生成编排不属于当前配方项目")
            latest_plan = session.scalar(
                select(GenerationPlan)
                .where(GenerationPlan.storyboard_revision_id == storyboard.id)
                .order_by(GenerationPlan.revision.desc())
                .limit(1)
                .with_for_update(of=GenerationPlan)
            )
            if latest_plan is None or latest_plan.id != located_plan.id:
                raise WorkflowConflictError("只能调整当前最新生成编排")
            current = latest_plan
            if current.revision != expected_revision:
                raise WorkflowConflictError("生成编排已更新，请比较最新版本后重试")
            if storyboard.status not in {
                StoryboardRevisionStatus.STRUCTURE_APPROVED.value,
                StoryboardRevisionStatus.PRODUCTION_APPROVED.value,
            }:
                raise WorkflowConflictError("分镜结构尚未批准，不能调整生成编排")
            if (
                payload.provider != SEEDANCE_2_0_CAPABILITY.provider
                or payload.model != SEEDANCE_2_0_CAPABILITY.model
                or payload.capability_revision != SEEDANCE_2_0_CAPABILITY.capability_revision
            ):
                raise ValueError("所选视频模型没有已注册且可执行的能力档案")

            beats = list(
                session.scalars(
                    select(ShotBeat)
                    .join(Scene, Scene.id == ShotBeat.scene_id)
                    .where(
                        ShotBeat.storyboard_revision_id == storyboard.id,
                        ShotBeat.status != "superseded",
                    )
                    .order_by(Scene.sort_order, ShotBeat.sort_order)
                    .with_for_update(of=ShotBeat)
                )
            )
            beats_by_id = {beat.id: beat for beat in beats}
            requested_ids = [shot_id for clip in payload.clips for shot_id in clip.shot_beat_ids]
            if requested_ids != [beat.id for beat in beats]:
                raise ValueError("生成编排必须按原顺序完整覆盖全部导演分镜，不能遗漏、重复或换序")

            clip_documents: list[dict[str, Any]] = []
            grouped_beats: list[list[ShotBeat]] = []
            for clip_draft in payload.clips:
                clip_beats = [beats_by_id[shot_id] for shot_id in clip_draft.shot_beat_ids]
                _validate_generation_clip(clip_beats)
                grouped_beats.append(clip_beats)
                clip_documents.append(
                    {
                        "shotBeatIds": [str(beat.id) for beat in clip_beats],
                        "durationSeconds": sum(beat.duration_seconds for beat in clip_beats),
                    }
                )
            input_hash = generation_plan_input_hash(
                structure_hash=storyboard.structure_hash,
                provider=payload.provider,
                model=payload.model,
                capability_revision=payload.capability_revision,
                clips=clip_documents,
            )
            if input_hash == current.input_hash:
                return self._instance_json(session, instance)

            list(
                session.scalars(
                    select(GenerationClipShot)
                    .where(GenerationClipShot.generation_plan_id == current.id)
                    .order_by(GenerationClipShot.shot_card_id, GenerationClipShot.ordinal)
                    .with_for_update(of=GenerationClipShot)
                )
            )
            current_clips = list(
                session.scalars(
                    select(ShotCard)
                    .where(ShotCard.generation_plan_id == current.id)
                    .order_by(ShotCard.plan_sort_order, ShotCard.id)
                    .with_for_update(of=ShotCard)
                )
            )
            current_clip_ids = [clip.id for clip in current_clips]
            current.status = GenerationPlanStatus.STALE.value
            if current_clip_ids:
                session.execute(
                    update(Asset)
                    .where(
                        Asset.shot_card_id.in_(current_clip_ids),
                        Asset.role.in_(("shot_anchor", "shot_video", "shot_video_edit")),
                        Asset.status != "stale",
                    )
                    .values(status="stale")
                )
            revised = GenerationPlan(
                id=uuid.uuid4(),
                storyboard_revision_id=storyboard.id,
                revision=current.revision + 1,
                status=GenerationPlanStatus.PROPOSED.value,
                provider=payload.provider,
                model=payload.model,
                capability_revision=payload.capability_revision,
                input_hash=input_hash,
                estimated_image_call_count=len(grouped_beats),
                estimated_video_call_count=len(grouped_beats),
                estimated_cost_micros=(
                    None
                    if SEEDANCE_2_0_CAPABILITY.video_call_cost_micros is None
                    else len(grouped_beats) * SEEDANCE_2_0_CAPABILITY.video_call_cost_micros
                ),
                warnings_json=[payload.reason] if payload.reason else [],
                blockers_json=[],
            )
            session.add(revised)
            session.flush()
            clip_order_by_scene: dict[uuid.UUID, int] = {}
            for plan_order, clip_beats in enumerate(grouped_beats, 1):
                scene_id = clip_beats[0].scene_id
                clip_order_by_scene[scene_id] = clip_order_by_scene.get(scene_id, 0) + 1
                shot = ShotCard(
                    id=uuid.uuid4(),
                    scene_id=scene_id,
                    sort_order=clip_order_by_scene[scene_id],
                    plan_sort_order=plan_order,
                    title=(
                        clip_beats[0].title
                        if len(clip_beats) == 1
                        else f"{clip_beats[0].title}—{clip_beats[-1].title}"
                    )[:100],
                    direction=_editorial_clip_direction(clip_beats),
                    duration_seconds=sum(beat.duration_seconds for beat in clip_beats),
                    generation_plan_id=revised.id,
                    anchor_mode="text_only",
                    reference_bindings_json=[],
                    inherit_project_references=True,
                    use_scene_look=False,
                    scene_look_usage="off",
                    draft_revision=1,
                    status="ready",
                )
                session.add(shot)
                session.flush()
                cursor = 0
                for ordinal, beat in enumerate(clip_beats, 1):
                    beat.shot_card_id = shot.id
                    session.add(
                        GenerationClipShot(
                            id=uuid.uuid4(),
                            generation_plan_id=revised.id,
                            shot_card_id=shot.id,
                            shot_beat_id=beat.id,
                            ordinal=ordinal,
                            start_second=cursor,
                            end_second=cursor + beat.duration_seconds,
                            transition_in=beat.cut_intent,
                        )
                    )
                    cursor += beat.duration_seconds
            storyboard.status = StoryboardRevisionStatus.STRUCTURE_APPROVED.value
            storyboard.production_package_hash = None
            storyboard.production_approved_at = None
            project = self._required(session, ProductionRun, instance.production_run_id, lock=True)
            project.selected_sequence_id = None
            session.flush()
            return self._instance_json(session, instance)

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            instance = self._required(
                session,
                ProductionRecipeInstance,
                instance_id,
                lock=True,
            )
            target = self._review_target(session, payload.target_type, payload.target_id)
            self._validate_target_snapshot(payload, target)
            if target["project_id"] != instance.production_run_id:
                raise ValueError("审核目标不属于当前配方项目")
            target_row = target["row"]
            if (
                isinstance(target_row, StoryEventCandidateRecord)
                and target_row.production_recipe_instance_id != instance.id
            ):
                raise ValueError("事件方案不属于当前配方实例")
            if isinstance(target_row, StoryBriefRecord):
                latest_brief = session.scalar(
                    select(StoryBriefRecord)
                    .where(StoryBriefRecord.production_run_id == instance.production_run_id)
                    .order_by(StoryBriefRecord.revision.desc())
                    .limit(1)
                )
                if latest_brief is None or latest_brief.id != target_row.id:
                    raise WorkflowConflictError("只能审核当前最新创意简报版本")
                if target_row.revision < 2:
                    raise WorkflowConflictError("一句话创意尚未完成 AI 补全")
            server_blocking = False
            if isinstance(target_row, Asset):
                if target_row.status == "stale":
                    raise WorkflowConflictError("过期资产不能审核，请选择当前版本")
                if payload.target_type == "character_design":
                    binding = session.scalar(
                        select(CharacterDesignAsset).where(
                            CharacterDesignAsset.asset_id == target_row.id
                        )
                    )
                    if binding is None or target_row.media_type != "image":
                        raise ValueError("角色设计审核目标必须是当前角色图片候选")
                if payload.target_type == "anchor_asset" and (
                    target_row.media_type != "image" or target_row.role != "shot_anchor"
                ):
                    raise ValueError("视觉锚点审核目标必须是图片")
                if payload.target_type == "video_asset" and (
                    target_row.media_type != "video"
                    or target_row.role not in {"shot_video", "shot_video_edit"}
                ):
                    raise ValueError("视频镜头审核目标必须是视频")
                if target_row.role in {"shot_anchor", "shot_video", "shot_video_edit"}:
                    diagnostic_reviews = list(
                        session.scalars(
                            select(Review).where(
                                Review.asset_id == target_row.id,
                                Review.source == "ark_visual",
                            )
                        )
                    )
                    server_blocking = not diagnostic_reviews or any(
                        review.warnings_json for review in diagnostic_reviews
                    )
            if payload.decision is HumanReviewDecision.APPROVE and server_blocking:
                raise ValueError("专项语义诊断缺失或失败，必须修改或填写理由人工覆盖")
            effective_payload = payload.model_copy(
                update={
                    "blocking_diagnostic_present": (
                        payload.blocking_diagnostic_present or server_blocking
                    )
                }
            )
            locked_story_rules: EpisodeRules | None = None
            if (
                isinstance(target_row, StoryRevisionRecord)
                and effective_payload.target_type in {"story_revision", "episode_rules"}
                and effective_payload.decision.value in _APPROVING_DECISIONS
            ):
                requires_locked_rules = requires_legacy_story_approval_contract(target_row)
                locked_story_rules = episode_rules or (
                    EpisodeRules.model_validate(target_row.episode_rules_json)
                    if target_row.episode_rules_json
                    else None
                )
                if requires_locked_rules and locked_story_rules is None:
                    raise ValueError("批准配方故事时必须同时锁定 EpisodeRules")
                if locked_story_rules is not None:
                    requested_rules = locked_story_rules.model_dump(mode="json", by_alias=True)
                    if (
                        target_row.status == StoryRevisionStatus.APPROVED.value
                        and target_row.episode_rules_json
                        and target_row.episode_rules_json != requested_rules
                    ):
                        raise WorkflowConflictError(
                            "已批准剧情的 EpisodeRules 不允许原地改写；"
                            "请创建新的故事或规则版本"
                        )

            exact_review_filters = [
                HumanReviewDecisionRecord.production_recipe_instance_id == instance.id,
                HumanReviewDecisionRecord.target_type == effective_payload.target_type,
                HumanReviewDecisionRecord.target_id == effective_payload.target_id,
                HumanReviewDecisionRecord.target_revision == effective_payload.target_revision,
                HumanReviewDecisionRecord.decision == effective_payload.decision.value,
            ]
            exact_review_filters.append(
                HumanReviewDecisionRecord.target_hash.is_(None)
                if effective_payload.target_hash is None
                else HumanReviewDecisionRecord.target_hash == effective_payload.target_hash
            )
            existing_review = session.scalar(
                select(HumanReviewDecisionRecord)
                .where(*exact_review_filters)
                .order_by(HumanReviewDecisionRecord.created_at.desc())
                .limit(1)
            )
            if existing_review is not None:
                self._settle_review_workflow_steps(
                    session,
                    instance=instance,
                    payload=effective_payload,
                    review=existing_review,
                )
                session.flush()
                return _review_json(
                    existing_review,
                    warnings=target.get("diagnostics", ()),
                )

            row = HumanReviewDecisionRecord(
                id=uuid.uuid4(),
                production_recipe_instance_id=instance.id,
                production_run_id=instance.production_run_id,
                target_type=effective_payload.target_type,
                target_id=effective_payload.target_id,
                target_revision=effective_payload.target_revision,
                target_hash=effective_payload.target_hash,
                decision=effective_payload.decision.value,
                blocking_diagnostic_present=effective_payload.blocking_diagnostic_present,
                issues_json=effective_payload.issues,
                reason=effective_payload.reason,
            )
            session.add(row)
            if (
                isinstance(target_row, StoryRevisionRecord)
                and effective_payload.target_type in {"story_revision", "episode_rules"}
                and effective_payload.decision.value in _APPROVING_DECISIONS
            ):
                previous_approved = list(
                    session.scalars(
                        select(StoryRevisionRecord).where(
                            StoryRevisionRecord.production_run_id == instance.production_run_id,
                            StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                            StoryRevisionRecord.id != target_row.id,
                        )
                    )
                )
                if previous_approved:
                    invalidate_story_production_lineage(
                        session,
                        project_id=instance.production_run_id,
                        story_ids=tuple(previous.id for previous in previous_approved),
                        reason="故事或 EpisodeRules 已批准新版本",
                    )
                if locked_story_rules is not None:
                    target_row.episode_rules_json = locked_story_rules.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                    self._lock_canon_profile(
                        session,
                        project_id=instance.production_run_id,
                        rules=locked_story_rules,
                    )
                for previous in previous_approved:
                    previous.status = StoryRevisionStatus.SUPERSEDED.value
            if (
                isinstance(target_row, StoryEventCandidateRecord)
                and effective_payload.decision.value in _APPROVING_DECISIONS
            ):
                previous_selected = list(
                    session.scalars(
                        select(StoryEventCandidateRecord)
                        .where(
                            StoryEventCandidateRecord.production_recipe_instance_id == instance.id,
                            StoryEventCandidateRecord.status
                            == StoryEventCandidateStatus.SELECTED.value,
                            StoryEventCandidateRecord.id != target_row.id,
                        )
                        .with_for_update()
                    )
                )
                if previous_selected:
                    previous_ids = [candidate.id for candidate in previous_selected]
                    session.execute(
                        update(StoryRevisionRecord)
                        .where(
                            StoryRevisionRecord.source_event_candidate_id.in_(previous_ids),
                            StoryRevisionRecord.status != StoryRevisionStatus.SUPERSEDED.value,
                        )
                        .values(status=StoryRevisionStatus.SUPERSEDED.value)
                    )
                    self._invalidate_media_after_upstream_change(
                        session,
                        instance.production_run_id,
                        "已重新选择事件方案",
                    )
                    for previous in previous_selected:
                        previous.status = StoryEventCandidateStatus.SUPERSEDED.value
            self._apply_review_to_target(session, effective_payload, target_row)
            if (
                isinstance(target_row, StoryRevisionRecord)
                and effective_payload.target_type in {"story_revision", "episode_rules"}
                and effective_payload.decision.value in _APPROVING_DECISIONS
                and target_row.scene_plan_json
            ):
                logger.info(
                    "materializing scenes for approved recipe story",
                    extra={
                        "recipe_instance_id": str(instance.id),
                        "project_id": str(instance.production_run_id),
                        "story_revision_id": str(target_row.id),
                        "story_revision": target_row.revision,
                        "review_target_type": effective_payload.target_type,
                        "review_target_revision": effective_payload.target_revision,
                    },
                )
                try:
                    materialize_approved_story_scenes(session, target_row)
                except Exception:
                    logger.exception(
                        "failed to materialize scenes for approved recipe story",
                        extra={
                            "recipe_instance_id": str(instance.id),
                            "project_id": str(instance.production_run_id),
                            "story_revision_id": str(target_row.id),
                            "story_revision": target_row.revision,
                            "review_target_type": effective_payload.target_type,
                            "review_target_revision": effective_payload.target_revision,
                        },
                    )
                    raise
            self._settle_review_workflow_steps(
                session,
                instance=instance,
                payload=effective_payload,
                review=row,
            )
            session.flush()
            return _review_json(row, warnings=target.get("diagnostics", ()))

    def confirm_storyboard_production_plan(
        self,
        instance_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        """Approve the current storyboard structure and generation plan atomically."""

        with self._sessions.begin() as session:
            instance = self._required(
                session,
                ProductionRecipeInstance,
                instance_id,
                lock=True,
            )
            storyboard, plan, beats, _mappings, _clips = self._lock_storyboard_execution_graph(
                session,
                storyboard_revision_id=payload.storyboard_revision_id,
                generation_plan_id=payload.generation_plan_id,
            )
            if (
                storyboard.production_run_id != instance.production_run_id
                or plan.storyboard_revision_id != storyboard.id
            ):
                raise ValueError("分镜版本或生成编排不属于当前配方项目")
            if storyboard.revision != payload.storyboard_revision:
                raise WorkflowConflictError("分镜版本已变化，请重新确认制作方案")
            if plan.revision != payload.generation_plan_revision:
                raise WorkflowConflictError("生成编排版本已变化，请重新确认制作方案")
            current_structure_hash = storyboard_structure_hash(beats)
            if (
                current_structure_hash != storyboard.structure_hash
                or storyboard.structure_hash != payload.structure_hash
                or plan.input_hash != payload.generation_plan_hash
            ):
                raise WorkflowConflictError("确认快照已变化，请刷新后重新确认制作方案")
            confirmation_id = uuid.uuid5(
                instance.id,
                f"storyboard-production-confirmation:{payload.idempotency_key}",
            )
            review_ids = {
                "storyboard_structure": uuid.uuid5(
                    confirmation_id,
                    "storyboard_structure",
                ),
                "generation_plan": uuid.uuid5(
                    confirmation_id,
                    "generation_plan",
                ),
            }
            if (
                storyboard.status
                in {
                    StoryboardRevisionStatus.STRUCTURE_APPROVED.value,
                    StoryboardRevisionStatus.PRODUCTION_APPROVED.value,
                }
                and plan.status == GenerationPlanStatus.APPROVED.value
            ):
                settled_reviews = list(
                    session.scalars(
                        select(HumanReviewDecisionRecord)
                        .where(
                            HumanReviewDecisionRecord.production_recipe_instance_id
                            == instance.id,
                            HumanReviewDecisionRecord.decision.in_(_APPROVING_DECISIONS),
                            HumanReviewDecisionRecord.target_type.in_(
                                ("storyboard_structure", "generation_plan")
                            ),
                        )
                        .order_by(HumanReviewDecisionRecord.created_at.desc())
                        .with_for_update()
                    )
                )
                review_by_target = {
                    review.target_type: review
                    for review in settled_reviews
                    if (
                        review.target_type == "storyboard_structure"
                        and review.target_id == storyboard.id
                        and review.target_revision == storyboard.revision
                        and review.target_hash == storyboard.structure_hash
                    )
                    or (
                        review.target_type == "generation_plan"
                        and review.target_id == plan.id
                        and review.target_revision == plan.revision
                        and review.target_hash == plan.input_hash
                    )
                }
                if set(review_by_target) == {"storyboard_structure", "generation_plan"}:
                    review_payloads = (
                        HumanReviewDraft(
                            targetType="storyboard_structure",
                            targetId=storyboard.id,
                            targetRevision=storyboard.revision,
                            targetHash=storyboard.structure_hash,
                            decision=HumanReviewDecision.APPROVE,
                        ),
                        HumanReviewDraft(
                            targetType="generation_plan",
                            targetId=plan.id,
                            targetRevision=plan.revision,
                            targetHash=plan.input_hash,
                            decision=HumanReviewDecision.APPROVE,
                        ),
                    )
                    for review_payload in review_payloads:
                        self._settle_review_workflow_steps(
                            session,
                            instance=instance,
                            payload=review_payload,
                            review=review_by_target[review_payload.target_type],
                        )
                    session.flush()
                    active_scenes = list(
                        session.scalars(
                            select(Scene).where(
                                Scene.production_run_id == storyboard.production_run_id,
                                Scene.story_revision_id == storyboard.story_revision_id,
                                Scene.active.is_(True),
                            )
                        )
                    )
                    return _storyboard_confirmation_json(
                        instance=instance,
                        storyboard=storyboard,
                        plan=plan,
                        confirmation_id=confirmation_id,
                        reviews=list(review_by_target.values()),
                        warnings=_storyboard_diagnostics(beats, active_scenes),
                    )
            existing_reviews = list(
                session.scalars(
                    select(HumanReviewDecisionRecord)
                    .where(HumanReviewDecisionRecord.id.in_(review_ids.values()))
                    .order_by(HumanReviewDecisionRecord.target_type)
                    .with_for_update()
                )
            )
            if existing_reviews:
                if len(existing_reviews) != len(review_ids):
                    raise WorkflowConflictError("幂等确认记录不完整，请刷新后重新确认")
                expected_targets = {
                    "storyboard_structure": (
                        storyboard.id,
                        storyboard.revision,
                        payload.structure_hash,
                    ),
                    "generation_plan": (
                        plan.id,
                        plan.revision,
                        payload.generation_plan_hash,
                    ),
                }
                for review in existing_reviews:
                    expected = expected_targets.get(review.target_type)
                    if (
                        review.id != review_ids.get(review.target_type)
                        or review.production_recipe_instance_id != instance.id
                        or review.production_run_id != instance.production_run_id
                        or expected is None
                        or (review.target_id, review.target_revision, review.target_hash)
                        != expected
                        or review.decision != HumanReviewDecision.APPROVE.value
                        or review.reason != payload.reason
                    ):
                        raise WorkflowConflictError("同一幂等键对应不同确认快照")
                if (
                    storyboard_structure_hash(beats) != storyboard.structure_hash
                    or storyboard.structure_hash != payload.structure_hash
                    or plan.input_hash != payload.generation_plan_hash
                ):
                    raise WorkflowConflictError("确认快照已变化，请刷新后重新确认制作方案")
                active_scenes = list(
                    session.scalars(
                        select(Scene).where(
                            Scene.production_run_id == storyboard.production_run_id,
                            Scene.story_revision_id == storyboard.story_revision_id,
                            Scene.active.is_(True),
                        )
                    )
                )
                return _storyboard_confirmation_json(
                    instance=instance,
                    storyboard=storyboard,
                    plan=plan,
                    confirmation_id=confirmation_id,
                    reviews=existing_reviews,
                    warnings=_storyboard_diagnostics(beats, active_scenes),
                )

            storyboard_review = HumanReviewDraft(
                targetType="storyboard_structure",
                targetId=storyboard.id,
                targetRevision=storyboard.revision,
                targetHash=payload.structure_hash,
                decision=HumanReviewDecision.APPROVE,
                reason=payload.reason,
            )
            storyboard_target = self._review_target(
                session,
                storyboard_review.target_type,
                storyboard.id,
            )
            self._validate_target_snapshot(storyboard_review, storyboard_target)
            self._apply_review_to_target(session, storyboard_review, storyboard)

            plan_review = HumanReviewDraft(
                targetType="generation_plan",
                targetId=plan.id,
                targetRevision=plan.revision,
                targetHash=payload.generation_plan_hash,
                decision=HumanReviewDecision.APPROVE,
                reason=payload.reason,
            )
            plan_target = self._review_target(session, plan_review.target_type, plan.id)
            self._validate_target_snapshot(plan_review, plan_target)
            self._apply_review_to_target(session, plan_review, plan)

            reviews: list[tuple[HumanReviewDraft, HumanReviewDecisionRecord]] = []
            for review_payload in (storyboard_review, plan_review):
                review = HumanReviewDecisionRecord(
                    id=review_ids[review_payload.target_type],
                    production_recipe_instance_id=instance.id,
                    production_run_id=instance.production_run_id,
                    target_type=review_payload.target_type,
                    target_id=review_payload.target_id,
                    target_revision=review_payload.target_revision,
                    target_hash=review_payload.target_hash,
                    decision=review_payload.decision.value,
                    blocking_diagnostic_present=False,
                    issues_json=[],
                    reason=review_payload.reason,
                )
                session.add(review)
                reviews.append((review_payload, review))
            session.flush()
            for review_payload, review in reviews:
                self._settle_review_workflow_steps(
                    session,
                    instance=instance,
                    payload=review_payload,
                    review=review,
                )
            session.flush()
            warnings = storyboard_target.get("diagnostics", ())
            return _storyboard_confirmation_json(
                instance=instance,
                storyboard=storyboard,
                plan=plan,
                confirmation_id=confirmation_id,
                reviews=[review for _, review in reviews],
                warnings=warnings,
            )

    @staticmethod
    def _lock_storyboard_execution_graph(
        session: Session,
        *,
        storyboard_revision_id: uuid.UUID,
        generation_plan_id: uuid.UUID,
    ) -> tuple[
        StoryboardRevision,
        GenerationPlan,
        list[ShotBeat],
        list[GenerationClipShot],
        list[ShotCard],
    ]:
        """Lock one executable storyboard snapshot in canonical dependency order."""

        storyboard = session.scalar(
            select(StoryboardRevision)
            .where(StoryboardRevision.id == storyboard_revision_id)
            .with_for_update(of=StoryboardRevision)
        )
        if storyboard is None:
            raise RecordNotFoundError(f"StoryboardRevision not found: {storyboard_revision_id}")
        plan = session.scalar(
            select(GenerationPlan)
            .where(GenerationPlan.id == generation_plan_id)
            .with_for_update(of=GenerationPlan)
        )
        if plan is None:
            raise RecordNotFoundError(f"GenerationPlan not found: {generation_plan_id}")
        beats = list(
            session.scalars(
                select(ShotBeat)
                .join(Scene, Scene.id == ShotBeat.scene_id)
                .where(
                    ShotBeat.storyboard_revision_id == storyboard.id,
                    ShotBeat.status != "superseded",
                )
                .order_by(Scene.sort_order, ShotBeat.sort_order, ShotBeat.id)
                .with_for_update(of=ShotBeat)
            )
        )
        mappings = list(
            session.scalars(
                select(GenerationClipShot)
                .join(ShotCard, ShotCard.id == GenerationClipShot.shot_card_id)
                .where(GenerationClipShot.generation_plan_id == plan.id)
                .order_by(
                    ShotCard.plan_sort_order,
                    GenerationClipShot.ordinal,
                    GenerationClipShot.id,
                )
                .with_for_update(of=GenerationClipShot)
            )
        )
        clips = list(
            session.scalars(
                select(ShotCard)
                .where(ShotCard.generation_plan_id == plan.id)
                .order_by(ShotCard.plan_sort_order, ShotCard.id)
                .with_for_update(of=ShotCard)
            )
        )
        return storyboard, plan, beats, mappings, clips

    @staticmethod
    def _settle_review_workflow_steps(
        session: Session,
        *,
        instance: ProductionRecipeInstance,
        payload: HumanReviewDraft,
        review: HumanReviewDecisionRecord,
    ) -> None:
        """Close only durable tasks that explicitly own the reviewed target."""

        awaiting_steps = list(
            session.scalars(
                select(WorkflowStep)
                .where(
                    WorkflowStep.production_run_id == instance.production_run_id,
                    WorkflowStep.status == StepStatus.AWAITING_REVIEW.value,
                )
                .with_for_update()
            )
        )
        matched = [
            step
            for step in awaiting_steps
            if _workflow_step_reviews_target(
                step,
                payload,
                recipe_instance_id=instance.id,
            )
        ]
        if not matched:
            return

        parent_ids: set[uuid.UUID] = set()
        for step in matched:
            _apply_review_to_workflow_step(step, payload=payload, review=review)
            record_workflow_task_event(
                session,
                step,
                "task_succeeded" if step.status == StepStatus.SUCCEEDED.value else "task_failed",
            )
            record_canvas_projection_changed_event(session, step)
            parent_id = _workflow_parent_step_id(step)
            if parent_id is not None:
                parent_ids.add(parent_id)

        while parent_ids:
            parent_id = parent_ids.pop()
            parent = session.scalar(
                select(WorkflowStep).where(WorkflowStep.id == parent_id).with_for_update()
            )
            if parent is None:
                continue
            child_ids = _workflow_child_step_ids(parent)
            if not child_ids:
                continue
            children = list(
                session.scalars(
                    select(WorkflowStep).where(WorkflowStep.id.in_(child_ids)).with_for_update()
                )
            )
            next_status = _aggregate_review_parent_status(children)
            if next_status is None or parent.status == next_status.value:
                continue
            _apply_child_aggregate_to_workflow_step(
                parent,
                children=children,
                status=next_status,
            )
            record_workflow_task_event(
                session,
                parent,
                "task_succeeded" if next_status is StepStatus.SUCCEEDED else "task_failed",
            )
            record_canvas_projection_changed_event(session, parent)
            ancestor_id = _workflow_parent_step_id(parent)
            if ancestor_id is not None:
                parent_ids.add(ancestor_id)

    def materialize_storyboard(
        self,
        instance_id: uuid.UUID,
        storyboard: dict[str, Any],
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            instance = self._required(
                session,
                ProductionRecipeInstance,
                instance_id,
                lock=True,
            )
            story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == instance.production_run_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
            )
            if story is None or not story.episode_rules_json:
                raise WorkflowConflictError("故事与 EpisodeRules 尚未锁定")
            character_design = session.scalar(
                select(CharacterDesignRevision)
                .where(
                    CharacterDesignRevision.production_recipe_instance_id == instance.id,
                    CharacterDesignRevision.status == "approved",
                )
                .order_by(CharacterDesignRevision.revision.desc())
                .limit(1)
            )
            if character_design is None:
                raise WorkflowConflictError("儿童、猫咪与同框比例角色设计尚未全部批准")
            rules = EpisodeRules.model_validate(story.episode_rules_json)
            beat_documents = list(storyboard.get("beats") or [])
            beats = [
                self._required(session, ShotBeat, uuid.UUID(str(document["id"])), lock=True)
                for document in beat_documents
            ]
            existing_revision = session.scalar(
                select(StoryboardRevision)
                .where(
                    StoryboardRevision.production_run_id == instance.production_run_id,
                    StoryboardRevision.story_revision_id == story.id,
                    StoryboardRevision.status != StoryboardRevisionStatus.SUPERSEDED.value,
                )
                .order_by(StoryboardRevision.revision.desc())
                .limit(1)
            )
            if existing_revision is not None:
                return {
                    **storyboard,
                    "storyRevisionId": str(story.id),
                    "storyRevision": story.revision,
                    "storyboardRevisionId": str(existing_revision.id),
                    "storyboardRevision": existing_revision.revision,
                    "structureHash": existing_revision.structure_hash,
                    "beats": beat_documents,
                }
            scene_ids = {beat.scene_id for beat in beats}
            source_prompt = session.get(PromptRecord, beats[0].prompt_id) if beats else None
            storyboard_revision = StoryboardRevision(
                id=uuid.uuid4(),
                production_run_id=instance.production_run_id,
                story_revision_id=story.id,
                revision=(
                    int(
                        session.scalar(
                            select(func.coalesce(func.max(StoryboardRevision.revision), 0)).where(
                                StoryboardRevision.production_run_id == instance.production_run_id
                            )
                        )
                        or 0
                    )
                    + 1
                ),
                status=StoryboardRevisionStatus.DRAFT.value,
                structure_hash="0" * 64,
                source_step_id=None if source_prompt is None else source_prompt.step_id,
                input_bindings_json=list(storyboard.get("inputBindings") or []),
            )
            session.add(storyboard_revision)
            session.flush()
            scene_contexts = {
                scene_id: _scene_continuity_document(self._required(session, Scene, scene_id))
                for scene_id in scene_ids
            }
            cat_actions = _cat_beat_actions(rules.cat_behavior_mode)
            phases = ("beginning", "change", "warm_ending")
            for index, beat in enumerate(beats):
                beat.storyboard_revision_id = storyboard_revision.id
                beat.visual_description = beat.visual_description or beat.action
                beat.child_action = beat.child_action or beat.action
                beat.cat_action = beat.cat_action or cat_actions[index % len(cat_actions)]
                beat.spatial_relation = beat.spatial_relation or "儿童与猫咪保持同场连续关系"
                beat.temporal_beats_json = [
                    {
                        "phase": phases[index % len(phases)],
                        "startSecond": 0,
                        "endSecond": beat.duration_seconds,
                        "childAction": beat.child_action,
                        "catAction": beat.cat_action,
                        "camera": beat.camera or "固定或轻微移动",
                    }
                ]
            storyboard_revision.structure_hash = storyboard_structure_hash(beats)
            capability = SEEDANCE_2_0_CAPABILITY
            blockers: list[str] = []
            try:
                clip_proposals = plan_generation_clips(
                    tuple(
                        EditorialShotDescriptor(
                            shot_beat_id=beat.id,
                            scene_id=beat.scene_id,
                            duration_seconds=beat.duration_seconds,
                            cut_intent=EditorialCutIntent(beat.cut_intent),
                            wardrobe_state=beat.wardrobe_state,
                            prop_state=beat.prop_state,
                        )
                        for beat in beats
                    ),
                    capability=capability,
                )
            except ValueError as exc:
                clip_proposals = ()
                blockers.append(str(exc))
            plan_document = {
                "structureHash": storyboard_revision.structure_hash,
                "provider": capability.provider,
                "model": capability.model,
                "capabilityRevision": capability.capability_revision,
                "clips": [
                    {
                        "durationSeconds": clip.duration_seconds,
                        "shotBeatIds": [str(item) for item in clip.shot_beat_ids],
                    }
                    for clip in clip_proposals
                ],
            }
            generation_plan = GenerationPlan(
                id=uuid.uuid4(),
                storyboard_revision_id=storyboard_revision.id,
                revision=1,
                status=GenerationPlanStatus.PROPOSED.value,
                provider=capability.provider,
                model=capability.model,
                capability_revision=capability.capability_revision,
                input_hash=_json_document_hash(plan_document),
                estimated_image_call_count=len(clip_proposals),
                estimated_video_call_count=len(clip_proposals),
                estimated_cost_micros=None,
                warnings_json=[
                    f"Agent 将 {len(beats)} 个导演分镜编排为 {len(clip_proposals)} 个真实生成片段"
                ],
                blockers_json=blockers,
            )
            session.add(generation_plan)
            session.flush()
            beats_by_id = {beat.id: beat for beat in beats}
            clip_order_by_scene: dict[uuid.UUID, int] = {}
            clip_ids_by_beat: dict[uuid.UUID, uuid.UUID] = {}
            for plan_order, clip in enumerate(clip_proposals, 1):
                clip_beats = [beats_by_id[beat_id] for beat_id in clip.shot_beat_ids]
                scene_id = clip_beats[0].scene_id
                clip_order_by_scene[scene_id] = clip_order_by_scene.get(scene_id, 0) + 1
                shot = ShotCard(
                    id=uuid.uuid4(),
                    scene_id=scene_id,
                    sort_order=clip_order_by_scene[scene_id],
                    plan_sort_order=plan_order,
                    title=(
                        clip_beats[0].title
                        if len(clip_beats) == 1
                        else f"{clip_beats[0].title}—{clip_beats[-1].title}"
                    )[:100],
                    direction=_healing_clip_direction(
                        clip_beats,
                        rules,
                        scene_contexts.get(scene_id, {}),
                    ),
                    duration_seconds=clip.duration_seconds,
                    generation_plan_id=generation_plan.id,
                    anchor_mode="text_only",
                    reference_bindings_json=[],
                    inherit_project_references=True,
                    use_scene_look=False,
                    scene_look_usage="off",
                    draft_revision=1,
                    status="ready",
                )
                session.add(shot)
                session.flush()
                cursor = 0
                for ordinal, beat in enumerate(clip_beats, 1):
                    beat.shot_card_id = shot.id
                    clip_ids_by_beat[beat.id] = shot.id
                    session.add(
                        GenerationClipShot(
                            id=uuid.uuid4(),
                            generation_plan_id=generation_plan.id,
                            shot_card_id=shot.id,
                            shot_beat_id=beat.id,
                            ordinal=ordinal,
                            start_second=cursor,
                            end_second=cursor + beat.duration_seconds,
                            transition_in=beat.cut_intent,
                        )
                    )
                    cursor += beat.duration_seconds
            editorial_count_by_scene: dict[uuid.UUID, int] = {}
            for beat in beats:
                editorial_count_by_scene[beat.scene_id] = (
                    editorial_count_by_scene.get(beat.scene_id, 0) + 1
                )
            for scene_id, count in editorial_count_by_scene.items():
                scene = self._required(session, Scene, scene_id, lock=True)
                scene.story_mode = "single" if count == 1 else "multi"
                scene.target_shot_count = count
            materialized = [
                {
                    **document,
                    "shotId": (
                        None if beat.id not in clip_ids_by_beat else str(clip_ids_by_beat[beat.id])
                    ),
                    "storyboardRevisionId": str(storyboard_revision.id),
                    "temporalBeats": beat.temporal_beats_json,
                }
                for beat, document in zip(beats, beat_documents, strict=True)
            ]
            return {
                **storyboard,
                "storyRevisionId": str(story.id),
                "storyRevision": story.revision,
                "storyboardRevisionId": str(storyboard_revision.id),
                "storyboardRevision": storyboard_revision.revision,
                "structureHash": storyboard_revision.structure_hash,
                "generationPlanId": str(generation_plan.id),
                "generationPlanHash": generation_plan.input_hash,
                "beats": materialized,
            }

    def store_suggested_episode_rules(
        self,
        instance_id: uuid.UUID,
        candidate_ids: tuple[uuid.UUID, ...],
        rules: EpisodeRules,
    ) -> None:
        with self._sessions.begin() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id)
            rows = list(
                session.scalars(
                    select(StoryRevisionRecord).where(
                        StoryRevisionRecord.id.in_(candidate_ids),
                        StoryRevisionRecord.production_run_id == instance.production_run_id,
                    )
                )
            )
            if len(rows) != len(candidate_ids):
                raise WorkflowConflictError("故事候选与当前配方实例不一致")
            document = rules.model_dump(mode="json", by_alias=True)
            for row in rows:
                row.episode_rules_json = document

    def prepare_character_design(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
        candidate_count: int,
        stage: CharacterDesignRunStage = CharacterDesignRunStage.ALL,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id, lock=True)
            if instance.lifecycle_status != "active":
                raise WorkflowConflictError("已归档配方不能生成角色设计")
            story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == instance.production_run_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
            )
            if story is None or not story.episode_rules_json:
                raise WorkflowConflictError("故事与 EpisodeRules 尚未人工批准")
            if stage is CharacterDesignRunStage.PAIR_SCALE:
                revision = session.scalar(
                    select(CharacterDesignRevision)
                    .where(
                        CharacterDesignRevision.production_recipe_instance_id == instance.id,
                        CharacterDesignRevision.status != "stale",
                    )
                    .order_by(CharacterDesignRevision.revision.desc())
                    .limit(1)
                    .with_for_update()
                )
                if revision is None:
                    raise WorkflowConflictError("请先生成并批准本集儿童和猫咪设计")
                self._require_pair_scale_inputs(session, revision)
                revision.status = "generating"
                return self._character_design_run_json(
                    session,
                    instance,
                    revision,
                    candidate_count=candidate_count,
                    stage=stage,
                    batch_idempotency_key=idempotency_key,
                )

            existing = session.scalar(
                select(CharacterDesignRevision).where(
                    CharacterDesignRevision.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                if existing.production_recipe_instance_id != instance.id:
                    raise WorkflowConflictError("角色设计幂等键已用于其他配方")
                return self._character_design_run_json(
                    session,
                    instance,
                    existing,
                    candidate_count=candidate_count,
                    prepare_nodes=False,
                    stage=stage,
                )

            previous = list(
                session.scalars(
                    select(CharacterDesignRevision).where(
                        CharacterDesignRevision.production_recipe_instance_id == instance.id,
                        CharacterDesignRevision.status != "stale",
                    )
                )
            )
            for revision in previous:
                revision.status = "stale"
                bound_asset_ids = select(CharacterDesignAsset.asset_id).where(
                    CharacterDesignAsset.character_design_revision_id == revision.id
                )
                session.execute(
                    update(Asset)
                    .where(Asset.id.in_(bound_asset_ids), Asset.status != "stale")
                    .values(status="stale")
                )
            next_revision = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(CharacterDesignRevision.revision), 0)).where(
                            CharacterDesignRevision.production_recipe_instance_id == instance.id
                        )
                    )
                    or 0
                )
                + 1
            )
            revision = CharacterDesignRevision(
                id=uuid.uuid5(instance.id, f"character-design:{idempotency_key}"),
                production_recipe_instance_id=instance.id,
                production_run_id=instance.production_run_id,
                source_story_revision_id=story.id,
                revision=next_revision,
                idempotency_key=idempotency_key,
                status="generating",
            )
            session.add(revision)
            session.flush()
            return self._character_design_run_json(
                session,
                instance,
                revision,
                candidate_count=candidate_count,
                stage=stage,
            )

    def preview_character_design(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
        candidate_count: int,
        stage: CharacterDesignRunStage = CharacterDesignRunStage.ALL,
    ) -> dict[str, Any]:
        """Compile the exact three read-only batches without creating a revision or task."""

        with self._sessions() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id)
            if instance.lifecycle_status != "active":
                raise WorkflowConflictError("已归档配方不能预览角色设计")
            story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == instance.production_run_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
            )
            if story is None or not story.episode_rules_json:
                raise WorkflowConflictError("故事与 EpisodeRules 尚未人工批准")
            if stage is CharacterDesignRunStage.PAIR_SCALE:
                revision = session.scalar(
                    select(CharacterDesignRevision)
                    .where(
                        CharacterDesignRevision.production_recipe_instance_id == instance.id,
                        CharacterDesignRevision.status != "stale",
                    )
                    .order_by(CharacterDesignRevision.revision.desc())
                    .limit(1)
                )
                if revision is None:
                    raise WorkflowConflictError("请先生成并批准本集儿童和猫咪设计")
                self._require_pair_scale_inputs(session, revision)
                return self._character_design_run_json(
                    session,
                    instance,
                    revision,
                    candidate_count=candidate_count,
                    prepare_nodes=False,
                    stage=stage,
                    batch_idempotency_key=idempotency_key,
                )

            existing = session.scalar(
                select(CharacterDesignRevision).where(
                    CharacterDesignRevision.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                if existing.production_recipe_instance_id != instance.id:
                    raise WorkflowConflictError("角色设计预览幂等键已被其他配方占用")
                revision = existing
            else:
                revision = CharacterDesignRevision(
                    id=uuid.uuid5(instance.id, f"character-design:{idempotency_key}"),
                    production_recipe_instance_id=instance.id,
                    production_run_id=instance.production_run_id,
                    source_story_revision_id=story.id,
                    revision=(
                        int(
                            session.scalar(
                                select(
                                    func.coalesce(func.max(CharacterDesignRevision.revision), 0)
                                ).where(
                                    CharacterDesignRevision.production_recipe_instance_id
                                    == instance.id
                                )
                            )
                            or 0
                        )
                        + 1
                    ),
                    idempotency_key=idempotency_key,
                    status="generating",
                )
            return self._character_design_run_json(
                session,
                instance,
                revision,
                candidate_count=candidate_count,
                prepare_nodes=False,
                stage=stage,
            )

    @staticmethod
    def _require_pair_scale_inputs(
        session: Session,
        revision: CharacterDesignRevision,
    ) -> dict[CharacterDesignSlot, Asset]:
        selected = list(
            session.scalars(
                select(CharacterDesignAsset).where(
                    CharacterDesignAsset.character_design_revision_id == revision.id,
                    CharacterDesignAsset.slot.in_(
                        (CharacterDesignSlot.CHILD.value, CharacterDesignSlot.CAT.value)
                    ),
                    CharacterDesignAsset.selected.is_(True),
                )
            )
        )
        by_slot: dict[CharacterDesignSlot, Asset] = {}
        for binding in selected:
            asset = session.get(Asset, binding.asset_id)
            if asset is not None and asset.status == "approved":
                by_slot[CharacterDesignSlot(binding.slot)] = asset
        if set(by_slot) != {CharacterDesignSlot.CHILD, CharacterDesignSlot.CAT}:
            raise WorkflowConflictError(
                "必须先分别批准当前版本的本集儿童设计和本集猫咪设计，才能生成同框比例图"
            )
        return by_slot

    def prepare_character_design_validation(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Freeze three validation-only batches against the current approved revision."""

        with self._sessions.begin() as session:
            instance = self._required(
                session,
                ProductionRecipeInstance,
                instance_id,
                lock=True,
            )
            revision, offsets = self._character_design_validation_context(
                session,
                instance,
                idempotency_key=idempotency_key,
                lock=True,
            )
            return self._character_design_run_json(
                session,
                instance,
                revision,
                candidate_count=1,
                prepare_nodes=False,
                validation_only=True,
                batch_idempotency_key=idempotency_key,
                candidate_index_offsets=offsets,
            )

    def preview_character_design_validation(
        self,
        instance_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Compile the exact validation-only batches without mutating production state."""

        with self._sessions() as session:
            instance = self._required(session, ProductionRecipeInstance, instance_id)
            revision, offsets = self._character_design_validation_context(
                session,
                instance,
                idempotency_key=idempotency_key,
                lock=False,
            )
            return self._character_design_run_json(
                session,
                instance,
                revision,
                candidate_count=1,
                prepare_nodes=False,
                validation_only=True,
                batch_idempotency_key=idempotency_key,
                candidate_index_offsets=offsets,
            )

    @staticmethod
    def _character_design_validation_context(
        session: Session,
        instance: ProductionRecipeInstance,
        *,
        idempotency_key: str,
        lock: bool,
    ) -> tuple[CharacterDesignRevision, dict[str, int]]:
        if instance.lifecycle_status != "active":
            raise WorkflowConflictError("已归档配方不能验证角色设计引用")
        story = session.scalar(
            select(StoryRevisionRecord)
            .where(
                StoryRevisionRecord.production_run_id == instance.production_run_id,
                StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
            )
            .order_by(StoryRevisionRecord.revision.desc())
            .limit(1)
        )
        if story is None or not story.episode_rules_json:
            raise WorkflowConflictError("来源故事与 EpisodeRules 必须保持批准状态")

        revision_query = (
            select(CharacterDesignRevision)
            .where(CharacterDesignRevision.production_recipe_instance_id == instance.id)
            .order_by(CharacterDesignRevision.revision.desc())
            .limit(1)
        )
        if lock:
            revision_query = revision_query.with_for_update()
        revision = session.scalar(revision_query)
        if revision is None or revision.status != "approved":
            raise WorkflowConflictError("当前角色设计版本尚未完整批准，不能执行引用验证")
        if revision.source_story_revision_id != story.id:
            raise WorkflowConflictError("当前角色设计版本不再绑定最新批准故事")

        bindings = list(
            session.scalars(
                select(CharacterDesignAsset)
                .where(CharacterDesignAsset.character_design_revision_id == revision.id)
                .order_by(CharacterDesignAsset.slot, CharacterDesignAsset.candidate_index)
            )
        )
        selected_by_slot = {
            slot.value: [item for item in bindings if item.slot == slot.value and item.selected]
            for slot in CharacterDesignSlot
        }
        if any(len(items) != 1 for items in selected_by_slot.values()):
            raise WorkflowConflictError("儿童、猫咪和同框比例必须各有一个已选中的批准资产")
        selected_assets = [
            session.get(Asset, items[0].asset_id) for items in selected_by_slot.values()
        ]
        if any(asset is None or asset.status != "approved" for asset in selected_assets):
            raise WorkflowConflictError("当前选中的角色设计资产不完整或不再是批准状态")

        active_statuses = {
            StepStatus.PENDING.value,
            StepStatus.SUBMITTING.value,
            StepStatus.QUEUED.value,
            StepStatus.RUNNING.value,
            StepStatus.SUBMISSION_UNKNOWN.value,
        }
        active_validations = list(
            session.scalars(
                select(WorkflowStep).where(
                    WorkflowStep.production_run_id == instance.production_run_id,
                    WorkflowStep.operation_key == "recipe:character_design_validation",
                    WorkflowStep.status.in_(active_statuses),
                )
            )
        )
        for task in active_validations:
            task_payload = dict(task.input_snapshot_json.get("payload") or {})
            task_key = str(task_payload.get("idempotencyKey") or "")
            if task.status == StepStatus.SUBMISSION_UNKNOWN.value:
                raise WorkflowConflictError(
                    "已有引用验证任务的 Provider 提交状态未知，不能新建任务"
                )
            if task_key != idempotency_key:
                raise WorkflowConflictError("已有三槽位引用验证任务仍在运行，请等待页面状态更新")

        offsets = {
            slot.value: max(
                (binding.candidate_index for binding in bindings if binding.slot == slot.value),
                default=0,
            )
            for slot in CharacterDesignSlot
        }
        return revision, offsets

    @staticmethod
    def _character_design_run_json(
        session: Session,
        instance: ProductionRecipeInstance,
        revision: CharacterDesignRevision,
        *,
        candidate_count: int,
        prepare_nodes: bool = True,
        validation_only: bool = False,
        batch_idempotency_key: str | None = None,
        candidate_index_offsets: dict[str, int] | None = None,
        stage: CharacterDesignRunStage = CharacterDesignRunStage.ALL,
    ) -> dict[str, Any]:
        story = session.get(StoryRevisionRecord, revision.source_story_revision_id)
        if story is None or not story.episode_rules_json:
            raise WorkflowConflictError("角色设计来源故事不存在或规则未锁定")
        rules = EpisodeRules.model_validate(story.episode_rules_json)
        keys = canon_reference_keys(instance.canon_profile_id, rules.environment)
        canon_assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.scope == "canon",
                    Asset.status.in_(("ready", "approved")),
                    Asset.semantic_key.in_(keys),
                )
            )
        )
        by_key = {asset.semantic_key: asset for asset in canon_assets}
        missing = [key for key in keys if key not in by_key]
        if missing:
            raise WorkflowConflictError(f"角色设计缺少 Canon 参考：{', '.join(missing)}")
        project_id = instance.production_run_id
        nodes = {
            CharacterDesignSlot.CHILD: uuid.uuid5(project_id, "character-design:child"),
            CharacterDesignSlot.CAT: uuid.uuid5(project_id, "character-design:cat"),
            CharacterDesignSlot.PAIR_SCALE: uuid.uuid5(project_id, "character-design:pair-scale"),
        }
        references: dict[CharacterDesignSlot, tuple[str, ...]] = {
            CharacterDesignSlot.CHILD: ("person:headshot", "person:fullbody", keys[-1]),
            CharacterDesignSlot.CAT: ("cat:front", "cat:side", keys[-1]),
            CharacterDesignSlot.PAIR_SCALE: keys,
        }
        is_v4 = instance.canon_profile_id == CANON_V4_PROFILE_ID
        requested_slots = {
            CharacterDesignRunStage.ALL: tuple(CharacterDesignSlot),
            CharacterDesignRunStage.IDENTITY: (
                CharacterDesignSlot.CHILD,
                CharacterDesignSlot.CAT,
            ),
            CharacterDesignRunStage.PAIR_SCALE: (CharacterDesignSlot.PAIR_SCALE,),
        }[stage]
        episode_assets: dict[str, Asset] = {}
        if stage is CharacterDesignRunStage.PAIR_SCALE:
            selected = SqlAlchemyProductionRecipeRepository._require_pair_scale_inputs(
                session,
                revision,
            )
            episode_assets = {
                "episode:child": selected[CharacterDesignSlot.CHILD],
                "episode:cat": selected[CharacterDesignSlot.CAT],
            }
            references[CharacterDesignSlot.PAIR_SCALE] = (
                "episode:child",
                "episode:cat",
                keys[-1],
            )
        project = session.get(ProductionRun, instance.production_run_id)
        visual_profile = (
            None
            if project is None or project.current_visual_profile_revision_id is None
            else session.get(VisualProfileRevision, project.current_visual_profile_revision_id)
        )
        if (
            visual_profile is not None
            and visual_profile.source_profile_id != instance.canon_profile_id
        ):
            raise WorkflowConflictError("本集视觉档案与当前配方 Canon 版本不一致")
        active_style_positive = (
            tuple(visual_profile.style_positive_json)
            if visual_profile is not None
            else tuple(rules.style_positive)
        )
        active_style_negative = (
            tuple(visual_profile.style_negative_json)
            if visual_profile is not None
            else tuple(rules.style_excluded)
        )
        style_instruction = (
            f"遵循本集锁定画风：{'、'.join(active_style_positive)}；"
            f"排除：{'、'.join(active_style_negative)}。"
        )
        legacy_prompts = {
            CharacterDesignSlot.CHILD: (
                "生成固定儿童的本集造型设计图。必须保持 Canon 脸部、年龄、短发和"
                f"儿童身体比例；展示全身服装、关键表情与动作姿态。{style_instruction}\n"
                "@图片1 只负责儿童五官、年龄与短发；@图片2 只负责儿童全身比例和轮廓；"
                "@图片3 只负责线条、材质和光线，不复制其中物体或构图。"
            ),
            CharacterDesignSlot.CAT: (
                "生成固定猫咪的本集造型设计图。必须保持 Canon 脸部、毛色分区、体型与"
                f"环纹尾巴；保持四足猫科结构，展示关键姿态和允许配件。{style_instruction}\n"
                "@图片1 只负责猫咪脸部、眼睛与正面毛色；@图片2 只负责体型、虎斑分区"
                "和环纹尾巴；@图片3 只负责线条、材质和光线。"
            ),
            CharacterDesignSlot.PAIR_SCALE: (
                "生成固定儿童与固定猫咪的一人一猫同框比例设计图。必须同时保持两个 Canon "
                f"身份，展示稳定身高比例、空间关系和典型同框构图。{style_instruction}\n"
                "@图片1 锁定儿童面部；@图片2 锁定儿童全身比例；@图片3 锁定猫咪正面身份；"
                "@图片4 锁定猫咪侧面结构；@图片5 只锁定线条材质。"
            ),
        }
        v4_prompts = {
            CharacterDesignSlot.CHILD: (
                "任务：为本集生成固定儿童的完整造型设计图。\n\n"
                "参考职责：\n"
                "- @图片1 是唯一面部身份来源，锁定柔和圆润脸型、五官、8–9 岁年龄感、"
                "深棕黑色齐下颌短发、刘海和发际线。\n"
                "- @图片2 锁定儿童头身比例、肩宽、四肢长度和非成人化身体结构。\n"
                "- @图片3 只控制轮廓线、肤色处理、布料材质、色阶和光影；不得改变身份、"
                "年龄、发型、身体比例或服装内容。\n\n"
                f"本集造型：{rules.person_wardrobe}。服装一经批准，在本集连续镜头中保持一致。\n"
                f"画风：{style_instruction}\n"
                "输出一张中性浅暖灰背景的角色造型参考板，只出现这一名固定儿童；"
                "包含一张全身主视图、正面或侧面头像以及服装材质局部。"
                "不要出现猫咪、其他动物、其他人物、剧情场景、纸星星、窗边或复杂道具；"
                "不要复制参考背景、植物或构图。\n"
                "禁止马尾、长发、改变刘海或发际线、成人化身材、年龄漂移、摄影写实、"
                "3D 塑料感、额外手指或肢体、文字、Logo 和水印。"
            ),
            CharacterDesignSlot.CAT: (
                "任务：为本集生成同一只固定灰白虎斑猫的完整造型设计图。\n\n"
                "参考职责：\n"
                "- @图片1 锁定圆润头脸、金棕色眼睛、粉色鼻头、白色口鼻区、"
                "正面灰白分区和额头虎斑。\n"
                "- @图片2 锁定紧凑体型、侧面主要虎斑、真实四足猫科结构、尾巴长度和灰白环纹顺序。\n"
                "- @图片3 只控制轮廓线、毛发材质、色阶和光影；不得改变身份花纹。\n\n"
                f"画风：{style_instruction}\n"
                "输出一张中性浅暖灰背景的猫咪造型参考板，只出现这一只固定猫咪；"
                "包含正面、侧面和自然四足姿态。不要出现儿童、其他人物、其他动物、"
                "剧情场景、纸星星、窗边或复杂道具。"
                "允许变化姿态、表情、朝向和用户批准的轻量配件；所有动作保持四足猫科结构。\n"
                "禁止改变灰白分区、增删主要虎斑、改变眼睛鼻口或尾巴环纹、人类手掌、"
                "人形腿部、双足直立、额外肢体、摄影写实、3D 塑料感、文字、Logo 和水印。"
            ),
            CharacterDesignSlot.PAIR_SCALE: (
                "任务：生成人与猫咪的中性同框比例设计图；不重新设计人物或猫咪。\n\n"
                "参考职责：@图片1 是已经批准的本集儿童外观与身份；"
                "@图片2 是已经批准的本集猫咪外观与身份；"
                "@图片3 只控制轮廓线、材质、色阶和光影。不要重新设计任何角色。\n"
                "展示正面、侧面和自然互动三种空间关系，锁定身高、体积与自然接触尺度；"
                "不要遮挡双方关键身份特征。背景保持中性，不继承任何来源图中的植物或场景。\n"
                f"画风：{style_instruction}\n"
                "禁止改变身份、年龄、发型、毛色分区、虎斑、四足结构或比例，禁止文字、Logo 和水印。"
            ),
        }
        prompts = v4_prompts if is_v4 else legacy_prompts
        batches: list[dict[str, Any]] = []
        effective_idempotency_key = batch_idempotency_key or revision.idempotency_key
        offsets = candidate_index_offsets or {}
        for slot in requested_slots:
            node = session.get(CanvasGraphNode, nodes[slot])
            if node is None:
                raise RecordNotFoundError(f"角色设计节点不存在：{slot.value}")
            if prepare_nodes:
                node.object_id = revision.id
                node.status = "pending"
                node.revision += 1
                node.data_json = {
                    **node.data_json,
                    "characterDesignRevisionId": str(revision.id),
                    "characterDesignRevision": revision.revision,
                    "slot": slot.value,
                    "candidateCount": candidate_count,
                    "status": "pending",
                    "candidates": [],
                }
            assets_for_slot = {
                key: episode_assets.get(key) or by_key.get(key) for key in references[slot]
            }
            if any(asset is None for asset in assets_for_slot.values()):
                raise WorkflowConflictError(f"角色设计槽位 {slot.value} 缺少可用参考")
            reference_ids = [str(assets_for_slot[key].id) for key in references[slot]]  # type: ignore[union-attr]
            role_instructions = {
                "person:headshot": (
                    "person_identity",
                    "锁定儿童脸型、五官、8–9 岁年龄感、齐下颌短发、刘海与发际线",
                ),
                "person:fullbody": (
                    "person_body",
                    "锁定儿童头身比例、肩宽、四肢长度与非成人化身体结构",
                ),
                "cat:front": (
                    "cat_identity",
                    "锁定猫咪头脸、金棕色眼睛、粉色鼻口、正面灰白分区与额头虎斑",
                ),
                "cat:side": ("cat_body", "锁定猫咪体型、侧面主要虎斑、四足结构与尾巴环纹"),
                "episode:child": (
                    "episode_child_appearance",
                    "当前已批准的本集儿童是唯一人物身份与本集服装来源，不重新设计",
                ),
                "episode:cat": (
                    "episode_cat_appearance",
                    "当前已批准的本集猫咪是唯一猫咪身份与本集造型来源，不重新设计",
                ),
            }
            reference_manifest: list[dict[str, Any]] = []
            for ordinal, key in enumerate(references[slot], 1):
                asset = assets_for_slot[key]
                if asset is None:  # pragma: no cover - guarded above
                    raise RuntimeError("角色设计引用解析返回空资产")
                role, instruction = role_instructions.get(
                    key,
                    (
                        "style",
                        "只锁定插画线条、材质和光线，不复制参考中的物体、颜色或构图",
                    ),
                )
                authority_role = (
                    ReferenceAuthorityRole.STYLE_BOARD.value
                    if key.startswith("style:")
                    else (
                        ReferenceAuthorityRole.EPISODE_APPEARANCE.value
                        if key.startswith("episode:")
                        else ReferenceAuthorityRole.IDENTITY.value
                    )
                )
                reference_manifest.append(
                    {
                        "assetId": str(asset.id),
                        "sourceNodeId": None,
                        "sourceType": (
                            "approved_character_design"
                            if key.startswith("episode:")
                            else "canon_profile"
                        ),
                        "subjectRevisionId": asset.metadata_json.get("subjectRevisionId"),
                        "semanticRole": role,
                        "purpose": key,
                        "instruction": instruction,
                        "ordinal": ordinal,
                        "locked": True,
                        "sha256": asset.sha256,
                        "providerIncluded": True,
                        "providerSlot": f"reference_image_{ordinal}",
                        "omissionReason": None,
                        "origin": (
                            "episode_design"
                            if key.startswith("episode:")
                            else "canon_v4" if is_v4 else "canon_v3"
                        ),
                        "title": str(
                            asset.metadata_json.get("displayName")
                            or asset.semantic_key
                            or asset.role
                        ),
                        "contentUrl": f"/api/v1/assets/{asset.id}/content",
                        "evidenceLevel": "frozen",
                        "authority": {
                            "role": authority_role,
                            "providerEligible": True,
                            "priority": 50 if authority_role == "style_board" else 100,
                            "lockedTraits": (
                                ["轮廓线", "材质", "色阶", "光影"]
                                if authority_role == "style_board"
                                else ["角色身份", "年龄或体型", "基础结构与稳定标记"]
                            ),
                            "mutableTraits": (
                                ["与剧情一致的场景颜色和物体"]
                                if authority_role == "style_board"
                                else ["本集服装或配件", "表情", "动作", "朝向"]
                            ),
                            "forbiddenTransfer": (
                                ["具体物体", "背景", "构图"]
                                if authority_role == "style_board"
                                else ["背景", "参考图中的旧服装"]
                            ),
                        },
                    }
                )
            story_body = (story.synopsis or story.logline).strip()
            core_props = (
                "、".join(rules.core_props)
                if rules.core_props
                else "按故事正文，不额外添加道具"
            )
            story_context = (
                f"本集故事《{story.title}》：{story_body}\n"
                f"场景与时间：{rules.main_scene}；{rules.time_weather}。\n"
                f"核心道具：{core_props}。"
            )
            prompt = (
                f"{prompts[slot]}\n"
                f"{story_context}\n"
                "角色设计图只承担造型、姿态、比例或构图职责，不得改变身份。"
            )
            character_design_input: dict[str, Any] = {
                "revisionId": str(revision.id),
                "slot": slot.value,
                "semanticRole": CHARACTER_DESIGN_SEMANTIC_ROLE_BY_SLOT[slot],
                "candidateCount": candidate_count,
            }
            if validation_only:
                character_design_input.update(
                    {
                        "baseRevisionId": str(revision.id),
                        "validationOnly": True,
                        "candidateIndexOffset": int(offsets.get(slot.value, 0)),
                    }
                )
            batches.append(
                {
                    "projectId": str(project_id),
                    "canvasNodeId": str(node.id),
                    "mediaKind": "image",
                    "candidateCount": candidate_count,
                    "idempotencyKey": f"{effective_idempotency_key}:{slot.value}",
                    "input": {
                        "prompt": prompt,
                        "referenceAssetIds": reference_ids,
                        "referenceManifest": reference_manifest,
                        "promptBundle": {
                            "creativeText": prompt,
                            "referenceManifest": reference_manifest,
                            "executionParams": {
                                "mediaKind": "image",
                                "candidateCount": candidate_count,
                            },
                            "auditSnapshot": {
                                "storyRevisionId": str(story.id),
                                "characterDesignRevisionId": str(revision.id),
                                "canonProfileId": instance.canon_profile_id,
                            },
                        },
                        "characterDesign": character_design_input,
                    },
                }
            )
        return {
            "id": str(revision.id),
            "recipeInstanceId": str(instance.id),
            "projectId": str(project_id),
            "revision": revision.revision,
            "status": revision.status,
            "sourceStoryRevisionId": str(revision.source_story_revision_id),
            "validationOnly": validation_only,
            "batches": batches,
        }

    def get_group(self, group_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            group = self._required(session, CanvasGroup, group_id)
            return _canvas_group_json(session, group)

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions.begin() as session:
            group = self._required(session, CanvasGroup, group_id)
            members = list(
                session.scalars(
                    select(CanvasGroupMember)
                    .where(CanvasGroupMember.group_id == group.id)
                    .order_by(CanvasGroupMember.sort_order)
                )
            )
            node_ids = [member.canvas_node_id for member in members]
            nodes = list(
                session.scalars(select(CanvasGraphNode).where(CanvasGraphNode.id.in_(node_ids)))
            )
            edges = list(
                session.scalars(
                    select(CanvasGraphEdge).where(
                        CanvasGraphEdge.source_node_id.in_(node_ids),
                        CanvasGraphEdge.target_node_id.in_(node_ids),
                    )
                )
            )
            template_key = f"healing-child-cat-six-stage-v{group.revision}"
            definition = {
                "groupType": group.group_type,
                "nodes": [
                    {
                        "type": node.node_type,
                        "objectType": node.object_type,
                        "phase": node.data_json.get("phase"),
                        "slot": node.data_json.get("slot"),
                        "title": node.data_json.get("title"),
                    }
                    for node in nodes
                    if node.node_type != CanvasNodeType.RECIPE_GROUP.value
                ],
                "edges": [
                    {
                        "sourceNodeId": str(edge.source_node_id),
                        "sourcePort": edge.source_port,
                        "targetNodeId": str(edge.target_node_id),
                        "targetPort": edge.target_port,
                        "relationType": edge.relation_type,
                    }
                    for edge in edges
                ],
            }
            existing = session.scalar(
                select(CanvasGroupTemplate).where(CanvasGroupTemplate.template_key == template_key)
            )
            if existing is None:
                existing = CanvasGroupTemplate(
                    id=uuid.uuid4(),
                    template_key=template_key,
                    title="一人一猫六阶段工作流",
                    definition_json=definition,
                )
                session.add(existing)
                session.flush()
            return {
                "id": str(existing.id),
                "templateKey": existing.template_key,
                "title": existing.title,
                "definition": existing.definition_json,
            }

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            group = self._required(session, CanvasGroup, group_id, lock=True)
            if group.revision != expected_revision:
                raise WorkflowConflictError(
                    f"分组版本冲突：当前 {group.revision}，提交 {expected_revision}"
                )
            if group.lifecycle_status != "active":
                return {"id": str(group.id), "status": "detached", "archived": True}
            group.lifecycle_status = "detached"
            group.revision += 1
            session.execute(delete(CanvasGroupMember).where(CanvasGroupMember.group_id == group.id))
            if group.production_recipe_instance_id is not None:
                instance = self._required(
                    session,
                    ProductionRecipeInstance,
                    group.production_recipe_instance_id,
                    lock=True,
                )
                instance.lifecycle_status = "archived"
                instance.archived_at = datetime.now(UTC)
                instance.revision += 1
            return {
                "id": str(group.id),
                "status": group.lifecycle_status,
                "archived": True,
                "preserved": ["canon", "stories", "shots", "assets", "reviews", "history"],
            }

    def convert_to_shot_groups(self, group_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions.begin() as session:
            parent = self._required(session, CanvasGroup, group_id, lock=True)
            if parent.lifecycle_status != "active":
                raise WorkflowConflictError("已解组的配方不能转为分镜组")
            storyboard = session.scalar(
                select(StoryboardRevision)
                .where(
                    StoryboardRevision.production_run_id == parent.production_run_id,
                    StoryboardRevision.status == StoryboardRevisionStatus.PRODUCTION_APPROVED.value,
                )
                .order_by(StoryboardRevision.revision.desc())
                .limit(1)
            )
            plan = (
                None
                if storyboard is None
                else session.scalar(
                    select(GenerationPlan)
                    .where(
                        GenerationPlan.storyboard_revision_id == storyboard.id,
                        GenerationPlan.status == GenerationPlanStatus.APPROVED.value,
                    )
                    .order_by(GenerationPlan.revision.desc())
                    .limit(1)
                )
            )
            clips = (
                []
                if plan is None
                else list(
                    session.scalars(
                        select(ShotCard)
                        .where(ShotCard.generation_plan_id == plan.id)
                        .order_by(ShotCard.plan_sort_order)
                    )
                )
            )
            if plan is None or not clips:
                raise WorkflowConflictError("生产分镜包尚未批准，不能转为生成片段组")
            mappings = list(
                session.scalars(
                    select(GenerationClipShot)
                    .where(GenerationClipShot.generation_plan_id == plan.id)
                    .order_by(GenerationClipShot.ordinal)
                )
            )
            beat_ids_by_clip: dict[uuid.UUID, list[str]] = {}
            for mapping in mappings:
                beat_ids_by_clip.setdefault(mapping.shot_card_id, []).append(
                    str(mapping.shot_beat_id)
                )
            groups: list[dict[str, Any]] = []
            for index, clip in enumerate(clips, 1):
                node = session.get(CanvasGraphNode, clip.id)
                if node is None:
                    node = CanvasGraphNode(
                        id=clip.id,
                        production_run_id=parent.production_run_id,
                        node_type=CanvasNodeType.VIDEO_SEGMENT.value,
                        object_type="generation_clip",
                        object_id=clip.id,
                        status=clip.status,
                        data_json={
                            "title": clip.title,
                            "phase": "storyboard",
                            "generationPlanId": str(plan.id),
                            "editorialShotIds": beat_ids_by_clip.get(clip.id, []),
                        },
                    )
                    session.add(node)
                    session.flush()
                child_id = uuid.uuid5(parent.id, f"generation-clip-group:{clip.id}")
                child = session.get(CanvasGroup, child_id)
                if child is None:
                    child = CanvasGroup(
                        id=child_id,
                        production_run_id=parent.production_run_id,
                        production_recipe_instance_id=parent.production_recipe_instance_id,
                        parent_group_id=parent.id,
                        group_type="shot",
                        title=f"生成片段 {index} · {clip.title}",
                        lifecycle_status="active",
                        color="#52b7c8",
                        data_json={
                            "generationPlanId": str(plan.id),
                            "shotCardId": str(clip.id),
                            "editorialShotIds": beat_ids_by_clip.get(clip.id, []),
                        },
                    )
                    session.add(child)
                    session.flush()
                    session.add(
                        CanvasGroupMember(
                            id=uuid.uuid5(child.id, f"member:{node.id}"),
                            group_id=child.id,
                            canvas_node_id=node.id,
                            sort_order=1,
                        )
                    )
                groups.append(_canvas_group_json(session, child))
            return {"parentGroupId": str(parent.id), "groups": groups}

    def group_download_assets(self, group_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            group = self._required(session, CanvasGroup, group_id)
            assets = list(
                session.scalars(
                    select(Asset)
                    .where(
                        Asset.production_run_id == group.production_run_id,
                        Asset.status.in_(("approved", "ready")),
                        Asset.scope != "canon",
                    )
                    .order_by(Asset.created_at)
                )
            )
            return {
                "groupId": str(group.id),
                "title": group.title,
                "assets": [
                    {
                        "id": str(asset.id),
                        "mediaType": asset.media_type,
                        "role": asset.role,
                        "semanticKey": asset.semantic_key,
                        "storageKey": asset.storage_key,
                        "sha256": asset.sha256,
                    }
                    for asset in assets
                ],
            }

    def _instance_json(
        self,
        session: Session,
        row: ProductionRecipeInstance,
    ) -> dict[str, Any]:
        project = self._required(session, ProductionRun, row.production_run_id)
        visual_profile = (
            None
            if project.current_visual_profile_revision_id is None
            else session.get(
                VisualProfileRevision,
                project.current_visual_profile_revision_id,
            )
        )
        canvas_group = session.scalar(
            select(CanvasGroup)
            .where(
                CanvasGroup.production_recipe_instance_id == row.id,
                CanvasGroup.lifecycle_status == "active",
            )
            .order_by(CanvasGroup.created_at.desc())
            .limit(1)
        )
        latest_brief = session.scalar(
            select(StoryBriefRecord)
            .where(StoryBriefRecord.production_run_id == row.production_run_id)
            .order_by(StoryBriefRecord.revision.desc())
            .limit(1)
        )
        brief_decision = (
            None
            if latest_brief is None
            else session.scalar(
                select(HumanReviewDecisionRecord)
                .where(
                    HumanReviewDecisionRecord.production_recipe_instance_id == row.id,
                    HumanReviewDecisionRecord.target_type == "creative_brief",
                    HumanReviewDecisionRecord.target_id == latest_brief.id,
                )
                .order_by(HumanReviewDecisionRecord.created_at.desc())
                .limit(1)
            )
        )
        creative_approved = bool(
            brief_decision is not None and brief_decision.decision in _APPROVING_DECISIONS
        )
        creative_completed = bool(latest_brief is not None and latest_brief.revision >= 2)
        storyboard_revision = session.scalar(
            select(StoryboardRevision)
            .where(
                StoryboardRevision.production_run_id == row.production_run_id,
                StoryboardRevision.status != StoryboardRevisionStatus.SUPERSEDED.value,
            )
            .order_by(StoryboardRevision.revision.desc())
            .limit(1)
        )
        storyboard_scenes = (
            []
            if storyboard_revision is None
            else list(
                session.scalars(
                    select(Scene)
                    .where(
                        Scene.production_run_id == row.production_run_id,
                        Scene.story_revision_id == storyboard_revision.story_revision_id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order)
                )
            )
        )
        current_beats = list(
            session.scalars(
                select(ShotBeat)
                .join(Scene, Scene.id == ShotBeat.scene_id)
                .where(
                    Scene.production_run_id == row.production_run_id,
                    Scene.active.is_(True),
                    ShotBeat.status != "superseded",
                    *(
                        ()
                        if storyboard_revision is None
                        else (ShotBeat.storyboard_revision_id == storyboard_revision.id,)
                    ),
                )
                .order_by(Scene.sort_order, ShotBeat.sort_order)
            )
        )
        generation_plan = (
            None
            if storyboard_revision is None
            else session.scalar(
                select(GenerationPlan)
                .where(GenerationPlan.storyboard_revision_id == storyboard_revision.id)
                .order_by(GenerationPlan.revision.desc())
                .limit(1)
            )
        )
        clip_mappings = (
            []
            if generation_plan is None
            else list(
                session.scalars(
                    select(GenerationClipShot)
                    .join(ShotCard, ShotCard.id == GenerationClipShot.shot_card_id)
                    .where(GenerationClipShot.generation_plan_id == generation_plan.id)
                    .order_by(
                        ShotCard.plan_sort_order,
                        GenerationClipShot.ordinal,
                    )
                )
            )
        )
        shot_ids = list(
            dict.fromkeys(
                [mapping.shot_card_id for mapping in clip_mappings]
                or [beat.shot_card_id for beat in current_beats if beat.shot_card_id is not None]
            )
        )
        shots = list(session.scalars(select(ShotCard).where(ShotCard.id.in_(shot_ids))))
        shots_by_id = {shot.id: shot for shot in shots}
        beats_by_id = {beat.id: beat for beat in current_beats}
        mapped_beats_by_shot: dict[uuid.UUID, list[ShotBeat]] = {}
        clip_id_by_beat: dict[uuid.UUID, uuid.UUID] = {}
        for mapping in clip_mappings:
            beat = beats_by_id.get(mapping.shot_beat_id)
            if beat is not None:
                mapped_beats_by_shot.setdefault(mapping.shot_card_id, []).append(beat)
                clip_id_by_beat[beat.id] = mapping.shot_card_id
        if not clip_mappings:
            for beat in current_beats:
                if beat.shot_card_id is not None:
                    mapped_beats_by_shot.setdefault(beat.shot_card_id, []).append(beat)
                    clip_id_by_beat[beat.id] = beat.shot_card_id
        assets = list(
            session.scalars(
                select(Asset).where(Asset.shot_card_id.in_(shot_ids)).order_by(Asset.created_at)
            )
        )
        assets_by_shot: dict[uuid.UUID, list[Asset]] = {}
        for asset in assets:
            if asset.shot_card_id is not None:
                assets_by_shot.setdefault(asset.shot_card_id, []).append(asset)
        approved_story = session.scalar(
            select(StoryRevisionRecord)
            .where(
                StoryRevisionRecord.production_run_id == row.production_run_id,
                StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
            )
            .order_by(StoryRevisionRecord.revision.desc())
            .limit(1)
        )
        story_rows = list(
            session.scalars(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == row.production_run_id,
                    StoryRevisionRecord.status != StoryRevisionStatus.SUPERSEDED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
            )
        )
        story_ids = [story.id for story in story_rows]
        all_event_rows = list(
            session.scalars(
                select(StoryEventCandidateRecord)
                .where(StoryEventCandidateRecord.production_recipe_instance_id == row.id)
                .order_by(
                    StoryEventCandidateRecord.created_at.desc(),
                    StoryEventCandidateRecord.candidate_index,
                )
            )
        )
        latest_event_batch_id = all_event_rows[0].batch_id if all_event_rows else None
        selected_event = next(
            (
                candidate
                for candidate in all_event_rows
                if candidate.batch_id == latest_event_batch_id
                if candidate.status == StoryEventCandidateStatus.SELECTED.value
            ),
            None,
        )
        visible_event_rows = [
            candidate
            for candidate in all_event_rows
            if candidate.batch_id == latest_event_batch_id
            or (selected_event is not None and candidate.id == selected_event.id)
        ]
        event_script = next(
            (
                story
                for story in story_rows
                if selected_event is not None
                and story.source_event_candidate_id == selected_event.id
            ),
            None,
        )
        scores_by_story = {
            score.story_revision_id: score
            for score in session.scalars(
                select(StoryScore).where(StoryScore.story_revision_id.in_(story_ids))
            )
        }
        candidate_prompt_ids = [
            story.candidate_prompt_id
            for story in story_rows
            if story.candidate_prompt_id is not None
        ]
        candidate_prompts_by_id = {
            prompt.id: prompt
            for prompt in (
                session.scalars(
                    select(PromptRecord).where(PromptRecord.id.in_(candidate_prompt_ids))
                )
                if candidate_prompt_ids
                else ()
            )
        }
        story_approved = approved_story is not None
        episode_rules_locked = bool(
            approved_story is not None and approved_story.episode_rules_json
        )
        character_revision = session.scalar(
            select(CharacterDesignRevision)
            .where(CharacterDesignRevision.production_recipe_instance_id == row.id)
            .order_by(CharacterDesignRevision.revision.desc())
            .limit(1)
        )
        character_design = (
            None
            if character_revision is None
            else _character_design_json(session, character_revision)
        )
        character_design_approved = bool(
            character_revision is not None and character_revision.status == "approved"
        )
        storyboard_hash = storyboard_structure_hash(current_beats) if current_beats else None
        structure_approved = bool(
            storyboard_revision is not None
            and storyboard_revision.status
            in {
                StoryboardRevisionStatus.STRUCTURE_APPROVED.value,
                StoryboardRevisionStatus.PRODUCTION_APPROVED.value,
            }
            and storyboard_revision.structure_hash == storyboard_hash
        )
        generation_plan_approved = bool(
            generation_plan is not None
            and generation_plan.status == GenerationPlanStatus.APPROVED.value
        )
        ordered_clips = [shots_by_id[shot_id] for shot_id in shot_ids if shot_id in shots_by_id]
        production_package_review_hash = (
            _production_package_hash(
                session,
                storyboard_revision,
                generation_plan,
                ordered_clips,
            )
            if storyboard_revision is not None
            and generation_plan_approved
            and ordered_clips
            and all(clip.prompt_id is not None for clip in ordered_clips)
            else None
        )
        storyboard_package_approved = bool(
            storyboard_revision is not None
            and storyboard_revision.status == StoryboardRevisionStatus.PRODUCTION_APPROVED.value
            and storyboard_revision.production_package_hash
            and (
                (
                    generation_plan is not None
                    and generation_plan.capability_revision.startswith("legacy-")
                )
                or storyboard_revision.production_package_hash == production_package_review_hash
            )
        )
        approved_anchor_count = sum(shot.selected_anchor_asset_id is not None for shot in shots)
        required_anchor_count = sum(shot.anchor_mode != "text_only" for shot in shots)
        approved_video_count = sum(shot.selected_video_asset_id is not None for shot in shots)
        latest_sequence = session.scalar(
            select(VideoSequence)
            .where(VideoSequence.production_run_id == row.production_run_id)
            .order_by(VideoSequence.revision.desc())
            .limit(1)
        )
        rendered_asset = (
            None
            if latest_sequence is None or latest_sequence.rendered_asset_id is None
            else session.get(Asset, latest_sequence.rendered_asset_id)
        )
        sequence_ready = bool(
            latest_sequence is not None and latest_sequence.status in {"content_review", "approved"}
        )
        final_approved = bool(latest_sequence is not None and latest_sequence.status == "approved")
        full_text_story_rows = [
            story for story in story_rows if story.source_event_candidate_id is None
        ]
        if approved_story is not None:
            story_workflow = {
                "currentStep": 2,
                "totalSteps": 2,
                "status": "complete",
            }
        elif full_text_story_rows:
            story_workflow = {
                "currentStep": 2,
                "totalSteps": 2,
                "status": "select_story",
            }
        elif visible_event_rows:
            story_workflow = {
                "currentStep": (2 if selected_event is None else 3 if event_script is None else 4),
                "totalSteps": 4,
                "status": (
                    "select_event"
                    if selected_event is None
                    else "expand_script"
                    if event_script is None
                    else "approve_script"
                    if event_script.status != StoryRevisionStatus.APPROVED.value
                    else "complete"
                ),
                "scriptRevisionId": None if event_script is None else str(event_script.id),
                "legacy": True,
            }
        else:
            story_workflow = {
                "currentStep": 1,
                "totalSteps": 2,
                "status": "generate_candidates",
            }
        return {
            "id": str(row.id),
            "projectId": str(row.production_run_id),
            "recipeKey": row.recipe_key,
            "recipeVersion": row.recipe_version,
            "revision": row.revision,
            "theme": row.theme,
            "inspirationKey": row.inspiration_key,
            "targetDurationSeconds": row.target_duration_seconds,
            "qualityTier": row.quality_tier,
            "canonProfileId": row.canon_profile_id,
            "visualProfile": (
                None
                if visual_profile is None
                else episode_visual_profile_json(session, visual_profile)
            ),
            "lifecycleStatus": row.lifecycle_status,
            "groupId": str(
                canvas_group.id
                if canvas_group is not None
                else uuid.uuid5(row.production_run_id, f"canvas-group:{row.id}")
            ),
            "creativeBrief": (
                None
                if latest_brief is None
                else {
                    "id": str(latest_brief.id),
                    "revision": latest_brief.revision,
                    "theme": latest_brief.theme,
                    "audience": latest_brief.audience,
                    "genre": latest_brief.genre,
                    "tone": latest_brief.tone,
                    "aspectRatio": latest_brief.aspect_ratio,
                    "targetDurationSeconds": latest_brief.target_duration_seconds,
                    "constraints": latest_brief.constraints_json,
                    "approvalStatus": ("approved" if creative_approved else "awaiting_review"),
                    "completionStatus": ("completed" if creative_completed else "seed"),
                }
            ),
            "characterDesign": character_design,
            "storyboardHash": storyboard_hash,
            "storyboard": (
                None
                if storyboard_revision is None
                else {
                    **_storyboard_revision_json(
                        storyboard_revision,
                        current_beats,
                        storyboard_scenes,
                    ),
                    "productionPackageReviewHash": production_package_review_hash,
                }
            ),
            "generationPlan": (
                None
                if generation_plan is None
                else _generation_plan_json(
                    generation_plan,
                    shots_by_id,
                    mapped_beats_by_shot,
                )
            ),
            "episodeRules": (
                None
                if approved_story is None or not approved_story.episode_rules_json
                else approved_story.episode_rules_json
            ),
            "sequenceCandidate": (
                None
                if latest_sequence is None
                else _sequence_candidate_json(latest_sequence, rendered_asset)
            ),
            "storyCandidates": [
                _recipe_story_candidate_json(
                    story,
                    scores_by_story.get(story.id),
                    candidate_prompts_by_id.get(story.candidate_prompt_id),
                )
                for story in story_rows
            ],
            "storyEvents": [
                _recipe_story_event_json(candidate) for candidate in visible_event_rows
            ],
            "selectedStoryEventId": (None if selected_event is None else str(selected_event.id)),
            "storyWorkflow": story_workflow,
            "shots": [
                _generation_clip_json(
                    session,
                    shots_by_id[shot_id],
                    mapped_beats_by_shot.get(shot_id, []),
                    assets_by_shot.get(shot_id, []),
                )
                for shot_id in shot_ids
                if shot_id in shots_by_id
            ],
            "editorialShots": [
                _editorial_shot_json(
                    beat,
                    generation_clip_id=clip_id_by_beat.get(beat.id),
                )
                for beat in current_beats
            ],
            "progress": {
                "creativeApproved": creative_approved,
                "creativeCompleted": creative_completed,
                "storyApproved": story_approved,
                "episodeRulesLocked": episode_rules_locked,
                "characterDesignApproved": character_design_approved,
                "storyboardStructureApproved": structure_approved,
                "generationPlanApproved": generation_plan_approved,
                "storyboardPackageApproved": storyboard_package_approved,
                "storyboardApproved": storyboard_package_approved,
                "storyboardShotCount": len(current_beats),
                "generationClipCount": len(shots),
                "shotCount": len(shots),
                "approvedAnchorCount": approved_anchor_count,
                "requiredAnchorCount": required_anchor_count,
                "approvedVideoCount": approved_video_count,
                "sequenceReady": sequence_ready,
                "finalApproved": final_approved,
            },
            "createdAt": row.created_at.isoformat(),
            "updatedAt": row.updated_at.isoformat(),
        }

    @staticmethod
    def _lock_canon_profile(
        session: Session,
        *,
        project_id: uuid.UUID,
        rules: EpisodeRules,
    ) -> None:
        instance = session.scalar(
            select(ProductionRecipeInstance).where(
                ProductionRecipeInstance.production_run_id == project_id
            )
        )
        canon_profile_id = rules.canon_profile_id if instance is None else instance.canon_profile_id
        keys = canon_reference_keys(canon_profile_id, rules.environment)
        assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.scope == "canon",
                    Asset.status.in_(("ready", "approved")),
                    Asset.semantic_key.in_(keys),
                )
            )
        )
        by_key = {asset.semantic_key: asset for asset in assets}
        missing = [key for key in keys if key not in by_key]
        if missing:
            raise ValueError(f"{canon_profile_id} 缺少可用参考：{', '.join(missing)}")
        snapshot = [
            {
                "assetId": str(by_key[key].id),
                "semanticKey": key,
                "sha256": by_key[key].sha256,
                "role": "style" if key.startswith("style:") else "identity",
            }
            for key in keys
        ]
        digest = hashlib.sha256(
            json.dumps(
                {
                    "rules": rules.model_dump(mode="json", by_alias=True),
                    "references": snapshot,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        profile = session.scalar(
            select(VisualProfileRevision).where(
                VisualProfileRevision.production_run_id == project_id,
                VisualProfileRevision.profile_hash == digest,
            )
        )
        if profile is None:
            revision = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(VisualProfileRevision.revision), 0)).where(
                            VisualProfileRevision.production_run_id == project_id
                        )
                    )
                    or 0
                )
                + 1
            )
            bindings = visual_profile_bindings(by_key, keys)
            profile = VisualProfileRevision(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=revision,
                profile_hash=digest,
                source_profile_id=rules.canon_profile_id,
                person_identity=f"沿用 {canon_profile_id} 人物脸部身份，不改变年龄与五官",
                person_hair=f"沿用 {canon_profile_id} 发型与发色",
                person_body=f"沿用 {canon_profile_id} 儿童身体比例",
                cat_identity=f"沿用 {canon_profile_id} 猫咪脸部、毛色分区、体型与尾巴环纹",
                style_positive_json=rules.style_positive,
                style_negative_json=rules.style_excluded,
                reference_bindings_json=bindings,
                reference_snapshot_json=snapshot,
            )
            session.add(profile)
            session.flush()
        project = session.get(ProductionRun, project_id)
        if project is None:
            raise RecordNotFoundError(f"ProductionRun not found: {project_id}")
        project.current_visual_profile_revision_id = profile.id
        project.default_reference_bindings_json = generation_reference_bindings(by_key, keys)

    @staticmethod
    def _review_target(
        session: Session,
        target_type: str,
        target_id: uuid.UUID,
    ) -> dict[str, Any]:
        if target_type == "creative_brief":
            row = session.get(StoryBriefRecord, target_id)
            project_id = None if row is None else row.production_run_id
        elif target_type == "story_event":
            row = session.get(StoryEventCandidateRecord, target_id)
            project_id = None if row is None else row.production_run_id
        elif target_type in {"story_revision", "episode_rules"}:
            row = session.get(StoryRevisionRecord, target_id)
            project_id = None if row is None else row.production_run_id
        elif target_type == "storyboard_structure":
            row = session.get(StoryboardRevision, target_id)
            project_id = None if row is None else row.production_run_id
            if row is None:
                raise RecordNotFoundError(f"分镜版本不存在：{target_id}")
            beats = list(
                session.scalars(
                    select(ShotBeat)
                    .join(Scene, Scene.id == ShotBeat.scene_id)
                    .where(
                        ShotBeat.storyboard_revision_id == row.id,
                        ShotBeat.status != "superseded",
                    )
                    .order_by(Scene.sort_order, ShotBeat.sort_order)
                )
            )
            if not beats:
                raise WorkflowConflictError("分镜版本中没有可审核的导演分镜")
            latest_storyboard = session.scalar(
                select(StoryboardRevision)
                .where(StoryboardRevision.production_run_id == row.production_run_id)
                .order_by(StoryboardRevision.revision.desc())
                .limit(1)
            )
            if latest_storyboard is None or latest_storyboard.id != row.id:
                raise WorkflowConflictError("只能审核当前最新分镜版本")
            brief = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == row.production_run_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            if (
                brief is not None
                and sum(beat.duration_seconds for beat in beats) != brief.target_duration_seconds
            ):
                raise WorkflowConflictError("导演分镜总时长必须与项目目标时长完全一致")
            if any(not 2 <= beat.duration_seconds <= 15 for beat in beats):
                raise WorkflowConflictError("治愈组合包的导演分镜必须为2至15秒")
            active_scenes = list(
                session.scalars(
                    select(Scene).where(
                        Scene.production_run_id == row.production_run_id,
                        Scene.story_revision_id == row.story_revision_id,
                        Scene.active.is_(True),
                    )
                )
            )
            diagnostics = _storyboard_diagnostics(beats, active_scenes)
            current_hash = storyboard_structure_hash(beats)
            if current_hash != row.structure_hash:
                raise WorkflowConflictError("分镜结构已改变，请先保存为新版本")
            return {
                "row": row,
                "project_id": project_id,
                "snapshot_hash": row.structure_hash,
                "diagnostics": diagnostics,
            }
        elif target_type == "generation_plan":
            row = session.get(GenerationPlan, target_id)
            storyboard_revision = (
                None if row is None else session.get(StoryboardRevision, row.storyboard_revision_id)
            )
            project_id = (
                None if storyboard_revision is None else storyboard_revision.production_run_id
            )
            if storyboard_revision is None or (
                storyboard_revision.status
                not in {
                    StoryboardRevisionStatus.STRUCTURE_APPROVED.value,
                    StoryboardRevisionStatus.PRODUCTION_APPROVED.value,
                }
            ):
                raise WorkflowConflictError("批准生成编排前必须先批准分镜结构")
            if row is not None and row.blockers_json:
                raise WorkflowConflictError("生成编排存在阻断：" + "；".join(row.blockers_json))
            if row is None:
                raise RecordNotFoundError(f"生成编排不存在：{target_id}")
            latest_plan = session.scalar(
                select(GenerationPlan)
                .where(GenerationPlan.storyboard_revision_id == row.storyboard_revision_id)
                .order_by(GenerationPlan.revision.desc())
                .limit(1)
            )
            if latest_plan is None or latest_plan.id != row.id:
                raise WorkflowConflictError("只能审核当前最新生成编排")
            beats = list(
                session.scalars(
                    select(ShotBeat)
                    .join(Scene, Scene.id == ShotBeat.scene_id)
                    .where(
                        ShotBeat.storyboard_revision_id == row.storyboard_revision_id,
                        ShotBeat.status != "superseded",
                    )
                    .order_by(Scene.sort_order, ShotBeat.sort_order)
                )
            )
            mappings = list(
                session.scalars(
                    select(GenerationClipShot)
                    .join(ShotCard, ShotCard.id == GenerationClipShot.shot_card_id)
                    .where(GenerationClipShot.generation_plan_id == row.id)
                    .order_by(ShotCard.plan_sort_order, GenerationClipShot.ordinal)
                )
            )
            if [mapping.shot_beat_id for mapping in mappings] != [beat.id for beat in beats]:
                raise WorkflowConflictError("生成编排未按顺序完整覆盖全部导演分镜")
            clips = list(
                session.scalars(
                    select(ShotCard)
                    .where(ShotCard.generation_plan_id == row.id)
                    .order_by(ShotCard.plan_sort_order)
                )
            )
            if [clip.plan_sort_order for clip in clips] != list(range(1, len(clips) + 1)):
                raise WorkflowConflictError("真实生成片段顺序必须从 1 连续递增")
            clips_by_id = {clip.id: clip for clip in clips}
            beats_by_id = {beat.id: beat for beat in beats}
            grouped: dict[uuid.UUID, list[GenerationClipShot]] = {}
            for mapping in mappings:
                grouped.setdefault(mapping.shot_card_id, []).append(mapping)
            if set(grouped) != set(clips_by_id):
                raise WorkflowConflictError("生成编排存在未映射或跨版本的真实生成片段")
            clip_documents: list[dict[str, Any]] = []
            for clip in clips:
                clip_mappings = grouped[clip.id]
                clip_beats = [beats_by_id[item.shot_beat_id] for item in clip_mappings]
                if row.capability_revision == SEEDANCE_2_0_CAPABILITY.capability_revision:
                    _validate_generation_clip(clip_beats)
                cursor = 0
                for ordinal, mapping in enumerate(clip_mappings, 1):
                    beat = beats_by_id[mapping.shot_beat_id]
                    if (
                        mapping.ordinal != ordinal
                        or mapping.start_second != cursor
                        or mapping.end_second != cursor + beat.duration_seconds
                        or mapping.transition_in != beat.cut_intent
                    ):
                        raise WorkflowConflictError("生成片段时间窗口不连续或与导演分镜时长不一致")
                    cursor = mapping.end_second
                if clip.duration_seconds != cursor:
                    raise WorkflowConflictError("生成片段时长不等于所含导演分镜时间窗口总和")
                clip_documents.append(
                    {
                        "durationSeconds": cursor,
                        "shotBeatIds": [str(beat.id) for beat in clip_beats],
                    }
                )
            if not row.capability_revision.startswith("legacy-"):
                current_plan_hash = generation_plan_input_hash(
                    structure_hash=storyboard_revision.structure_hash,
                    provider=row.provider,
                    model=row.model,
                    capability_revision=row.capability_revision,
                    clips=clip_documents,
                )
                if current_plan_hash != row.input_hash:
                    raise WorkflowConflictError("生成编排内容哈希已变化，请重新建立编排")
            return {
                "row": row,
                "project_id": project_id,
                "snapshot_hash": None if row is None else row.input_hash,
            }
        elif target_type in {"storyboard_package", "storyboard_revision"}:
            row = session.get(StoryboardRevision, target_id)
            project_id = None if row is None else row.production_run_id
            if row is None:
                raise RecordNotFoundError(f"生产分镜包不存在：{target_id}")
            plan = session.scalar(
                select(GenerationPlan)
                .where(
                    GenerationPlan.storyboard_revision_id == row.id,
                    GenerationPlan.status == GenerationPlanStatus.APPROVED.value,
                )
                .order_by(GenerationPlan.revision.desc())
                .limit(1)
            )
            if plan is None:
                raise WorkflowConflictError("生成编排尚未批准")
            mappings = list(
                session.scalars(
                    select(GenerationClipShot)
                    .join(ShotCard, ShotCard.id == GenerationClipShot.shot_card_id)
                    .where(GenerationClipShot.generation_plan_id == plan.id)
                    .order_by(ShotCard.plan_sort_order, GenerationClipShot.ordinal)
                )
            )
            clip_ids = list(dict.fromkeys(item.shot_card_id for item in mappings))
            clips = list(
                session.scalars(
                    select(ShotCard)
                    .where(ShotCard.id.in_(clip_ids))
                    .order_by(ShotCard.plan_sort_order)
                )
            )
            missing_prompts = [clip.title for clip in clips if clip.prompt_id is None]
            if missing_prompts:
                raise WorkflowConflictError(
                    "以下生成片段尚未完成 Prompt 编译：" + "、".join(missing_prompts)
                )
            project = session.get(ProductionRun, row.production_run_id)
            current_profile_id = (
                None if project is None else project.current_visual_profile_revision_id
            )
            for clip in clips:
                prompt = (
                    None if clip.prompt_id is None else session.get(PromptRecord, clip.prompt_id)
                )
                snapshot = {} if prompt is None else dict(prompt.input_snapshot_json or {})
                scene = session.get(Scene, clip.scene_id)
                if (
                    prompt is None
                    or prompt.status != "succeeded"
                    or prompt.business_object_type != "generation_clip"
                    or prompt.business_object_id != clip.id
                    or snapshot.get("storyboardRevisionId") != str(row.id)
                    or snapshot.get("structureHash") != row.structure_hash
                    or snapshot.get("generationPlanId") != str(plan.id)
                    or snapshot.get("generationPlanHash") != plan.input_hash
                    or snapshot.get("visualProfileRevisionId")
                    != (None if current_profile_id is None else str(current_profile_id))
                    or scene is None
                    or snapshot.get("sceneId") != str(scene.id)
                    or snapshot.get("sceneLookDraftRevision") != scene.look_draft_revision
                ):
                    raise WorkflowConflictError(f"生成片段“{clip.title}”的 Prompt 输入版本已经过期")
                for reference in snapshot.get("referenceBindings") or []:
                    if not isinstance(reference, dict) or not reference.get("assetId"):
                        raise WorkflowConflictError(f"生成片段“{clip.title}”的 Prompt 引用清单无效")
                    try:
                        reference_asset_id = uuid.UUID(str(reference["assetId"]))
                    except ValueError as exc:
                        raise WorkflowConflictError(
                            f"生成片段“{clip.title}”的 Prompt 引用素材标识无效"
                        ) from exc
                    asset = session.get(Asset, reference_asset_id)
                    if (
                        asset is None
                        or asset.status not in {"approved", "ready"}
                        or asset.sha256 != reference.get("sha256")
                    ):
                        raise WorkflowConflictError(
                            f"生成片段“{clip.title}”的 Prompt 引用素材已缺失或过期"
                        )
            package_hash = _production_package_hash(session, row, plan, clips)
            return {
                "row": row,
                "project_id": project_id,
                "snapshot_hash": package_hash,
                "production_package_hash": package_hash,
            }
        elif target_type == "shot_beat":
            row = session.get(ShotBeat, target_id)
            scene = None if row is None else session.get(Scene, row.scene_id)
            project_id = None if scene is None else scene.production_run_id
        elif target_type in {"anchor_asset", "video_asset", "character_design"}:
            row = session.get(Asset, target_id)
            project_id = None if row is None else row.production_run_id
        elif target_type == "final_sequence":
            row = session.get(VideoSequence, target_id)
            project_id = None if row is None else row.production_run_id
        else:
            raise ValueError(f"不支持的审核目标类型：{target_type}")
        if row is None or project_id is None:
            raise RecordNotFoundError(f"审核目标不存在：{target_type}/{target_id}")
        return {"row": row, "project_id": project_id}

    @staticmethod
    def _validate_target_snapshot(
        payload: HumanReviewDraft,
        target: dict[str, Any],
    ) -> None:
        row = target["row"]
        snapshot_hash = target.get("snapshot_hash")
        if snapshot_hash is not None:
            if payload.target_hash is None:
                raise ValueError("分镜审核必须固定当前镜头表内容哈希")
            if payload.target_hash != snapshot_hash:
                raise WorkflowConflictError("分镜内容已变化，请重新审核最新版本")
            return
        if isinstance(row, Asset):
            if payload.target_hash is None:
                raise ValueError("媒体资产审核必须固定内容哈希")
            if payload.target_hash != row.sha256:
                raise WorkflowConflictError("媒体资产内容已变化，请重新审核最新版本")
            return
        current_revision = getattr(row, "revision", None)
        if payload.target_revision is None:
            raise ValueError("业务对象审核必须固定目标版本")
        if current_revision != payload.target_revision:
            raise WorkflowConflictError(
                f"审核目标版本冲突：当前 {current_revision}，提交 {payload.target_revision}"
            )

    @staticmethod
    def _apply_review_to_target(
        session: Session,
        payload: HumanReviewDraft,
        row: Any,
    ) -> None:
        accepted = payload.decision.value in _APPROVING_DECISIONS
        if isinstance(row, StoryBriefRecord):
            return
        if isinstance(row, StoryEventCandidateRecord):
            if accepted:
                row.status = StoryEventCandidateStatus.SELECTED.value
                row.selected_at = datetime.now(UTC)
            return
        if isinstance(row, StoryboardRevision):
            now = datetime.now(UTC)
            if payload.target_type == "storyboard_structure":
                row.status = (
                    StoryboardRevisionStatus.STRUCTURE_APPROVED.value
                    if accepted
                    else StoryboardRevisionStatus.CHANGES_REQUESTED.value
                )
                row.approved_structure_at = now if accepted else None
                session.execute(
                    update(ShotBeat)
                    .where(
                        ShotBeat.storyboard_revision_id == row.id,
                        ShotBeat.status != "superseded",
                    )
                    .values(
                        status="approved" if accepted else "changes_requested",
                        stale_reason=None if accepted else payload.reason,
                    )
                )
                return
            row.status = (
                StoryboardRevisionStatus.PRODUCTION_APPROVED.value
                if accepted
                else StoryboardRevisionStatus.CHANGES_REQUESTED.value
            )
            row.production_package_hash = payload.target_hash if accepted else None
            row.production_approved_at = now if accepted else None
            return
        if isinstance(row, GenerationPlan):
            row.status = (
                GenerationPlanStatus.APPROVED.value
                if accepted
                else GenerationPlanStatus.STALE.value
            )
            row.approved_at = datetime.now(UTC) if accepted else None
            return
        if isinstance(row, StoryRevisionRecord):
            if accepted:
                row.status = StoryRevisionStatus.APPROVED.value
                row.approved_at = datetime.now(UTC)
            return
        if isinstance(row, ShotBeat):
            row.status = "approved" if accepted else "changes_requested"
            row.stale_reason = None if accepted else payload.reason
            return
        if isinstance(row, Asset):
            character_design_metadata = dict(
                dict(row.metadata_json or {}).get("characterDesign") or {}
            )
            if (
                payload.target_type == "character_design"
                and character_design_metadata.get("validationOnly") is True
            ):
                raise WorkflowConflictError(
                    "引用顺序验证候选只能保留审计，不能批准或替换当前生产版本"
                )
            row.status = "approved" if accepted else "rejected"
            if payload.target_type == "character_design":
                binding = session.scalar(
                    select(CharacterDesignAsset)
                    .where(CharacterDesignAsset.asset_id == row.id)
                    .with_for_update()
                )
                if binding is None:
                    raise RecordNotFoundError("角色设计资产绑定不存在")
                revision = session.get(
                    CharacterDesignRevision, binding.character_design_revision_id
                )
                if revision is None or revision.status == "stale":
                    raise WorkflowConflictError("角色设计版本已过期")
                previously_selected_asset_id = session.scalar(
                    select(CharacterDesignAsset.asset_id).where(
                        CharacterDesignAsset.character_design_revision_id == revision.id,
                        CharacterDesignAsset.slot == binding.slot,
                        CharacterDesignAsset.selected.is_(True),
                    )
                )
                if accepted:
                    competing = list(
                        session.scalars(
                            select(CharacterDesignAsset).where(
                                CharacterDesignAsset.character_design_revision_id == revision.id,
                                CharacterDesignAsset.slot == binding.slot,
                                CharacterDesignAsset.id != binding.id,
                            )
                        )
                    )
                    for item in competing:
                        item.selected = False
                        asset = session.get(Asset, item.asset_id)
                        if asset is not None and asset.status != "stale":
                            asset.status = "rejected"
                    binding.selected = True
                else:
                    binding.selected = False
                selected_slots = set(
                    session.scalars(
                        select(CharacterDesignAsset.slot).where(
                            CharacterDesignAsset.character_design_revision_id == revision.id,
                            CharacterDesignAsset.selected.is_(True),
                        )
                    )
                )
                if accepted:
                    selected_slots.add(binding.slot)
                revision.status = (
                    "approved"
                    if selected_slots == {slot.value for slot in CharacterDesignSlot}
                    else "awaiting_review"
                )
                if row.canvas_node_id is not None:
                    node = session.get(CanvasGraphNode, row.canvas_node_id)
                    if node is not None:
                        candidates = [
                            {
                                **item,
                                "status": (
                                    "approved"
                                    if item.get("assetId") == str(row.id) and accepted
                                    else (
                                        "rejected" if accepted else item.get("status", "candidate")
                                    )
                                ),
                            }
                            for item in node.data_json.get("candidates", [])
                        ]
                        node.status = "approved" if accepted else "awaiting_review"
                        node.data_json = {
                            **node.data_json,
                            "status": node.status,
                            "selectedAssetId": str(row.id) if accepted else None,
                            "candidates": candidates,
                        }
                selection_changed = (accepted and previously_selected_asset_id != row.id) or (
                    not accepted and previously_selected_asset_id == row.id
                )
                if selection_changed:
                    SqlAlchemyProductionRecipeRepository._invalidate_current_production_package(
                        session,
                        revision.production_run_id,
                    )
                return
            if row.shot_card_id is not None:
                shot = session.get(ShotCard, row.shot_card_id)
                if shot is not None and row.media_type == "image":
                    if accepted:
                        shot.selected_anchor_asset_id = row.id
                        shot.status = "video_pending"
                    elif shot.selected_anchor_asset_id == row.id:
                        shot.selected_anchor_asset_id = None
                        shot.status = "ready"
                    shot.selected_video_asset_id = None
                    session.execute(
                        update(Asset)
                        .where(
                            Asset.shot_card_id == shot.id,
                            Asset.media_type == "video",
                            Asset.status != "stale",
                        )
                        .values(status="stale")
                    )
                    SqlAlchemyProductionRecipeRepository._invalidate_sequences(
                        session,
                        row.production_run_id,
                    )
                elif shot is not None and row.media_type == "video":
                    if accepted:
                        shot.selected_video_asset_id = row.id
                        shot.status = "approved"
                    elif shot.selected_video_asset_id == row.id:
                        shot.selected_video_asset_id = None
                        shot.status = "video_pending"
                    SqlAlchemyProductionRecipeRepository._invalidate_sequences(
                        session,
                        row.production_run_id,
                    )
            return
        if isinstance(row, VideoSequence):
            row.status = "approved" if accepted else "rejected"
            project = session.get(ProductionRun, row.production_run_id)
            if project is not None:
                project.selected_sequence_id = row.id if accepted else None

    @staticmethod
    def _invalidate_current_production_package(
        session: Session,
        project_id: uuid.UUID,
    ) -> None:
        storyboard = session.scalar(
            select(StoryboardRevision)
            .where(
                StoryboardRevision.production_run_id == project_id,
                StoryboardRevision.status != StoryboardRevisionStatus.SUPERSEDED.value,
            )
            .order_by(StoryboardRevision.revision.desc())
            .limit(1)
        )
        if storyboard is None:
            return
        plan = session.scalar(
            select(GenerationPlan)
            .where(
                GenerationPlan.storyboard_revision_id == storyboard.id,
                GenerationPlan.status.in_(
                    (
                        GenerationPlanStatus.PROPOSED.value,
                        GenerationPlanStatus.APPROVED.value,
                    )
                ),
            )
            .order_by(GenerationPlan.revision.desc())
            .limit(1)
        )
        if plan is None:
            return
        clips = list(
            session.scalars(select(ShotCard).where(ShotCard.generation_plan_id == plan.id))
        )
        clip_ids = [clip.id for clip in clips]
        for clip in clips:
            clip.prompt_id = None
            clip.selected_anchor_asset_id = None
            clip.selected_video_asset_id = None
            clip.status = "ready"
        if clip_ids:
            session.execute(
                update(Asset)
                .where(
                    Asset.shot_card_id.in_(clip_ids),
                    Asset.role.in_(("shot_anchor", "shot_video", "shot_video_edit")),
                    Asset.status != "stale",
                )
                .values(status="stale")
            )
        if storyboard.status == StoryboardRevisionStatus.PRODUCTION_APPROVED.value:
            storyboard.status = StoryboardRevisionStatus.STRUCTURE_APPROVED.value
            storyboard.production_package_hash = None
            storyboard.production_approved_at = None
        project = session.get(ProductionRun, project_id)
        if project is not None:
            project.selected_sequence_id = None

    @staticmethod
    def _invalidate_media_after_upstream_change(
        session: Session,
        project_id: uuid.UUID,
        reason: str,
    ) -> None:
        scene_ids = select(Scene.id).where(
            Scene.production_run_id == project_id,
            Scene.active.is_(True),
        )
        session.execute(
            update(CharacterDesignRevision)
            .where(
                CharacterDesignRevision.production_run_id == project_id,
                CharacterDesignRevision.status != "stale",
            )
            .values(status="stale")
        )
        character_asset_ids = (
            select(CharacterDesignAsset.asset_id)
            .join(
                CharacterDesignRevision,
                CharacterDesignRevision.id == CharacterDesignAsset.character_design_revision_id,
            )
            .where(CharacterDesignRevision.production_run_id == project_id)
        )
        session.execute(
            update(Asset)
            .where(Asset.id.in_(character_asset_ids), Asset.status != "stale")
            .values(status="stale")
        )
        session.execute(
            update(ShotBeat)
            .where(ShotBeat.scene_id.in_(scene_ids), ShotBeat.status != "superseded")
            .values(status="stale", stale_reason=reason)
        )
        session.execute(
            update(Asset)
            .where(Asset.production_run_id == project_id, Asset.scope != "canon")
            .values(status="stale")
        )
        SqlAlchemyProductionRecipeRepository._invalidate_sequences(session, project_id)

    @staticmethod
    def _invalidate_sequences(session: Session, project_id: uuid.UUID) -> None:
        session.execute(
            update(VideoSequence)
            .where(VideoSequence.production_run_id == project_id)
            .values(status="rejected")
        )
        project = session.get(ProductionRun, project_id)
        if project is not None:
            project.selected_sequence_id = None

    @staticmethod
    def _invalidate_downstream(
        session: Session,
        project_id: uuid.UUID,
        recipe_revision: int,
    ) -> None:
        reason = f"配方实例已更新到 revision {recipe_revision}"
        session.execute(
            update(StoryRevisionRecord)
            .where(
                StoryRevisionRecord.production_run_id == project_id,
                StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
            )
            .values(status=StoryRevisionStatus.SUPERSEDED.value)
        )
        SqlAlchemyProductionRecipeRepository._invalidate_media_after_upstream_change(
            session,
            project_id,
            reason,
        )

    @staticmethod
    def _required(
        session: Session,
        model: type[Any],
        object_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> Any:
        statement = select(model).where(model.id == object_id)
        if lock:
            statement = statement.with_for_update()
        row = session.scalar(statement)
        if row is None:
            raise RecordNotFoundError(f"{model.__name__} not found: {object_id}")
        return row


def _review_json(
    row: HumanReviewDecisionRecord,
    *,
    warnings: tuple[CanvasDiagnostic, ...] | list[CanvasDiagnostic] = (),
) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "recipeInstanceId": str(row.production_recipe_instance_id),
        "projectId": str(row.production_run_id),
        "targetType": row.target_type,
        "targetId": str(row.target_id),
        "targetRevision": row.target_revision,
        "targetHash": row.target_hash,
        "decision": row.decision,
        "blockingDiagnosticPresent": row.blocking_diagnostic_present,
        "issues": row.issues_json,
        "warnings": [item.model_dump(mode="json", by_alias=True) for item in warnings],
        "reason": row.reason,
        "createdAt": row.created_at.isoformat(),
    }


def _storyboard_confirmation_json(
    *,
    instance: ProductionRecipeInstance,
    storyboard: StoryboardRevision,
    plan: GenerationPlan,
    confirmation_id: uuid.UUID,
    reviews: list[HumanReviewDecisionRecord],
    warnings: tuple[CanvasDiagnostic, ...] | list[CanvasDiagnostic],
) -> dict[str, Any]:
    """Serialize the durable two-review result of one idempotent human decision."""

    review_ids_by_target = {review.target_type: review.id for review in reviews}
    return {
        "confirmationId": str(confirmation_id),
        "recipeInstanceId": str(instance.id),
        "status": "approved",
        "storyboardRevisionId": str(storyboard.id),
        "storyboardRevision": storyboard.revision,
        "structureHash": storyboard.structure_hash,
        "generationPlanId": str(plan.id),
        "generationPlanRevision": plan.revision,
        "generationPlanHash": plan.input_hash,
        "warnings": [item.model_dump(mode="json", by_alias=True) for item in warnings],
        "reviewIds": [
            str(review_ids_by_target[target_type])
            for target_type in ("storyboard_structure", "generation_plan")
        ],
    }


def _canvas_group_json(session: Session, group: CanvasGroup) -> dict[str, Any]:
    member_ids = list(
        session.scalars(
            select(CanvasGroupMember.canvas_node_id)
            .where(CanvasGroupMember.group_id == group.id)
            .order_by(CanvasGroupMember.sort_order)
        )
    )
    return {
        "id": str(group.id),
        "projectId": str(group.production_run_id),
        "recipeInstanceId": (
            None
            if group.production_recipe_instance_id is None
            else str(group.production_recipe_instance_id)
        ),
        "parentGroupId": (None if group.parent_group_id is None else str(group.parent_group_id)),
        "type": group.group_type,
        "title": group.title,
        "lifecycleStatus": group.lifecycle_status,
        "color": group.color,
        "revision": group.revision,
        "memberNodeIds": [str(item) for item in member_ids],
        "data": group.data_json,
    }


def _character_design_json(
    session: Session,
    revision: CharacterDesignRevision,
) -> dict[str, Any]:
    bindings = list(
        session.scalars(
            select(CharacterDesignAsset)
            .where(CharacterDesignAsset.character_design_revision_id == revision.id)
            .order_by(CharacterDesignAsset.slot, CharacterDesignAsset.candidate_index)
        )
    )
    slots: dict[str, list[dict[str, Any]]] = {slot.value: [] for slot in CharacterDesignSlot}
    for binding in bindings:
        asset = session.get(Asset, binding.asset_id)
        if asset is None:
            continue
        metadata = dict(asset.metadata_json or {})
        character_design = dict(metadata.get("characterDesign") or {})
        slots[binding.slot].append(
            {
                "bindingId": str(binding.id),
                "assetId": str(asset.id),
                "candidateIndex": binding.candidate_index,
                "semanticRole": binding.semantic_role,
                "selected": binding.selected,
                "status": asset.status,
                "sha256": asset.sha256,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "validationOnly": character_design.get("validationOnly") is True,
                "inputHash": metadata.get("generationInputHash"),
                "providerOrderEvidence": metadata.get("providerOrderEvidence"),
            }
        )
    return {
        "id": str(revision.id),
        "revision": revision.revision,
        "status": revision.status,
        "sourceStoryRevisionId": str(revision.source_story_revision_id),
        "slots": slots,
        "createdAt": revision.created_at.isoformat(),
        "updatedAt": revision.updated_at.isoformat(),
    }


def _storyboard_diagnostics(
    beats: list[ShotBeat],
    active_scenes: list[Scene],
) -> list[CanvasDiagnostic]:
    diagnostics = storyboard_quality_diagnostics(beats)
    covered_scene_ids = {beat.scene_id for beat in beats}
    diagnostics.extend(
        CanvasDiagnostic(
            code="storyboard_scene_uncovered",
            severity="warning",
            message=f"旧版场景「{scene.title}」未被当前分镜使用，不影响确认制作方案。",
            targetId=str(scene.id),
        )
        for scene in active_scenes
        if scene.id not in covered_scene_ids
    )
    return diagnostics


def _json_document_hash(document: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validate_generation_clip(beats: list[ShotBeat]) -> None:
    if not beats:
        raise ValueError("真实生成片段至少需要一个导演分镜")
    capability = SEEDANCE_2_0_CAPABILITY
    duration = sum(beat.duration_seconds for beat in beats)
    if duration not in capability.supported_durations:
        raise ValueError(
            f"真实生成片段必须为 {capability.minimum_duration_seconds} 至 "
            f"{capability.maximum_duration_seconds} 秒"
        )
    if len(beats) > capability.maximum_editorial_shots:
        raise ValueError("真实生成片段包含的导演分镜数超过当前模型能力")
    if not capability.supports_multi_shot and len(beats) > 1:
        raise ValueError("当前模型不支持多镜头生成片段")
    for previous, current in zip(beats, beats[1:], strict=False):
        if current.scene_id != previous.scene_id:
            raise ValueError("真实生成片段不能跨场景")
        if current.cut_intent == EditorialCutIntent.HARD_CUT.value:
            raise ValueError("hard_cut 导演分镜必须从新的真实生成片段开始")
        if current.wardrobe_state != previous.wardrobe_state:
            raise ValueError("服装状态变化处必须拆分真实生成片段")
        if current.prop_state != previous.prop_state:
            raise ValueError("关键道具状态变化处必须拆分真实生成片段")


def _editorial_clip_direction(beats: list[ShotBeat]) -> str:
    lines: list[str] = []
    for index, beat in enumerate(beats, 1):
        parts = [f"分镜{index}（{beat.duration_seconds}秒）：{beat.action}"]
        parts.extend(
            f"{label} {value}"
            for label, value in (
                ("儿童", beat.child_action),
                ("猫咪", beat.cat_action),
                ("空间关系", beat.spatial_relation),
                ("运镜", beat.camera),
            )
            if value
        )
        lines.append("；".join(parts))
    return "\n".join(lines)


def _production_package_hash(
    session: Session,
    storyboard: StoryboardRevision,
    plan: GenerationPlan,
    clips: list[ShotCard],
) -> str:
    project = session.get(ProductionRun, storyboard.production_run_id)
    profile = (
        None
        if project is None or project.current_visual_profile_revision_id is None
        else session.get(VisualProfileRevision, project.current_visual_profile_revision_id)
    )
    instance = session.scalar(
        select(ProductionRecipeInstance).where(
            ProductionRecipeInstance.production_run_id == storyboard.production_run_id
        )
    )
    character_revision = (
        None
        if instance is None
        else session.scalar(
            select(CharacterDesignRevision)
            .where(
                CharacterDesignRevision.production_recipe_instance_id == instance.id,
                CharacterDesignRevision.source_story_revision_id == storyboard.story_revision_id,
                CharacterDesignRevision.status == "approved",
            )
            .order_by(CharacterDesignRevision.revision.desc())
            .limit(1)
        )
    )
    character_assets: list[dict[str, Any]] = []
    if character_revision is not None:
        bindings = list(
            session.scalars(
                select(CharacterDesignAsset)
                .where(
                    CharacterDesignAsset.character_design_revision_id == character_revision.id,
                    CharacterDesignAsset.selected.is_(True),
                )
                .order_by(CharacterDesignAsset.slot)
            )
        )
        slot_order = {slot.value: index for index, slot in enumerate(CharacterDesignSlot)}
        bindings.sort(key=lambda item: slot_order.get(item.slot, len(slot_order)))
        for binding in bindings:
            asset = session.get(Asset, binding.asset_id)
            character_assets.append(
                {
                    "slot": binding.slot,
                    "assetId": str(binding.asset_id),
                    "sha256": None if asset is None else asset.sha256,
                }
            )
    scene_ids = list(dict.fromkeys(clip.scene_id for clip in clips))
    scenes = list(
        session.scalars(select(Scene).where(Scene.id.in_(scene_ids)).order_by(Scene.sort_order))
    )
    scene_binding_documents: dict[uuid.UUID, list[dict[str, Any]]] = {}
    scene_asset_ids: list[uuid.UUID] = []
    for scene in scenes:
        bindings = list(dict(scene.look_draft_json or {}).get("referenceBindings") or [])
        scene_binding_documents[scene.id] = bindings
        for binding in bindings:
            asset_id = binding.get("assetId")
            if asset_id:
                scene_asset_ids.append(uuid.UUID(str(asset_id)))
        if scene.selected_look_asset_id is not None:
            scene_asset_ids.append(scene.selected_look_asset_id)
    scene_assets = {
        asset.id: asset
        for asset in session.scalars(
            select(Asset).where(Asset.id.in_(list(dict.fromkeys(scene_asset_ids))))
        )
    }
    scene_packages: list[dict[str, Any]] = []
    for scene in scenes:
        references: list[dict[str, Any]] = []
        for ordinal, binding in enumerate(
            scene_binding_documents.get(scene.id, []),
            1,
        ):
            value = binding.get("assetId")
            if not value:
                continue
            asset_id = uuid.UUID(str(value))
            asset = scene_assets.get(asset_id)
            references.append(
                {
                    "ordinal": ordinal,
                    "id": str(asset_id),
                    "purpose": binding.get("purpose"),
                    "instruction": binding.get("instruction"),
                    "role": None if asset is None else asset.role,
                    "sha256": None if asset is None else asset.sha256,
                }
            )
        selected_look = (
            None
            if scene.selected_look_asset_id is None
            else scene_assets.get(scene.selected_look_asset_id)
        )
        scene_packages.append(
            {
                "id": str(scene.id),
                "lookDraftRevision": scene.look_draft_revision,
                "selectedLookAssetId": (
                    None
                    if scene.selected_look_asset_id is None
                    else str(scene.selected_look_asset_id)
                ),
                "selectedLookSha256": (None if selected_look is None else selected_look.sha256),
                "assets": references,
            }
        )
    prompts = {
        prompt.id: prompt
        for prompt in session.scalars(
            select(PromptRecord).where(
                PromptRecord.id.in_(
                    [clip.prompt_id for clip in clips if clip.prompt_id is not None]
                )
            )
        )
    }
    return _json_document_hash(
        {
            "generationPlanHash": plan.input_hash,
            "storyRevisionId": str(storyboard.story_revision_id),
            "visualProfile": (
                None
                if profile is None
                else {"revision": profile.revision, "hash": profile.profile_hash}
            ),
            "characterDesignRevisionId": (
                None if character_revision is None else str(character_revision.id)
            ),
            "characterAssets": character_assets,
            "scenes": scene_packages,
            "clips": [
                {
                    "id": str(clip.id),
                    "promptId": None if clip.prompt_id is None else str(clip.prompt_id),
                    "promptInputHash": (
                        None
                        if clip.prompt_id is None or clip.prompt_id not in prompts
                        else prompts[clip.prompt_id].input_hash
                    ),
                    "referenceBindings": (
                        []
                        if clip.prompt_id is None or clip.prompt_id not in prompts
                        else list(
                            dict(prompts[clip.prompt_id].structured_response_json or {}).get(
                                "referenceBindings"
                            )
                            or []
                        )
                    ),
                }
                for clip in clips
            ],
        }
    )


def _sequence_candidate_json(
    sequence: VideoSequence,
    rendered_asset: Asset | None,
) -> dict[str, Any]:
    return {
        "id": str(sequence.id),
        "revision": sequence.revision,
        "status": sequence.status,
        "durationMs": sequence.duration_ms,
        "audioPolicy": sequence.audio_policy,
        "renderedAssetId": (
            None if sequence.rendered_asset_id is None else str(sequence.rendered_asset_id)
        ),
        "contentUrl": (
            None if rendered_asset is None else f"/api/v1/assets/{rendered_asset.id}/content"
        ),
        "sha256": None if rendered_asset is None else rendered_asset.sha256,
        "qc": (None if rendered_asset is None else rendered_asset.metadata_json.get("qc")),
    }


def _recipe_story_candidate_json(
    story: StoryRevisionRecord,
    score: StoryScore | None,
    candidate_prompt: PromptRecord | None = None,
) -> dict[str, Any]:
    score_values = (
        []
        if score is None
        else [
            score.opening_hook,
            score.causal_completeness,
            score.subject_necessity,
            score.emotional_arc,
            score.visualizability,
            score.duration_fit,
            score.continuity_risk,
            score.safety,
        ]
    )
    diagnostics: list[dict[str, Any]] = []
    if candidate_prompt is not None:
        structured_response = candidate_prompt.structured_response_json or {}
        stored_diagnostics = structured_response.get("diagnostics")
        if isinstance(stored_diagnostics, list):
            diagnostics = [dict(item) for item in stored_diagnostics if isinstance(item, dict)]
    legacy_details = (
        {
            "scenePlan": story.scene_plan_json,
            "scorecard": (
                None
                if score is None
                else {
                    "average": round(sum(score_values) / len(score_values), 2),
                    "rationale": score.rationale,
                }
            ),
        }
        if story.source_event_candidate_id is not None or story.scene_plan_json or score is not None
        else None
    )
    return {
        "id": str(story.id),
        "revision": story.revision,
        "strategy": story.strategy,
        "status": story.status,
        "sourceEventCandidateId": (
            None
            if story.source_event_candidate_id is None
            else str(story.source_event_candidate_id)
        ),
        "title": story.title,
        "body": story.synopsis,
        "summary": story.logline,
        "source": "ai" if candidate_prompt is not None else "unknown",
        "contractKind": ("legacy_structured" if legacy_details is not None else "creative_text"),
        "warnings": diagnostics,
        "legacyDetails": legacy_details,
        "logline": story.logline,
        "synopsis": story.synopsis,
        "episodeRules": story.episode_rules_json or None,
    }


def _recipe_story_event_json(candidate: StoryEventCandidateRecord) -> dict[str, Any]:
    return {
        "id": str(candidate.id),
        "revision": candidate.revision,
        "batchId": str(candidate.batch_id),
        "candidateIndex": candidate.candidate_index,
        "strategy": candidate.strategy,
        "status": candidate.status,
        "title": candidate.title,
        "premise": candidate.premise,
        "childAction": candidate.child_action,
        "catParticipation": candidate.cat_participation,
        "smallChange": candidate.small_change,
        "warmEnding": candidate.warm_ending,
        "suggestedScenes": candidate.suggested_scenes_json,
        "durationFitSummary": candidate.duration_fit_summary,
        "requiresSceneChange": candidate.requires_scene_change,
        "catBehaviorModeSuggestion": candidate.cat_behavior_mode_suggestion,
        "score": candidate.score_json or None,
        "selectedAt": (
            None if candidate.selected_at is None else candidate.selected_at.isoformat()
        ),
        "createdAt": candidate.created_at.isoformat(),
    }


def _cat_beat_actions(mode: CatBehaviorMode) -> tuple[str, str, str]:
    if mode is CatBehaviorMode.NATURAL:
        return (
            "猫咪保持四足姿态自然观察",
            "猫咪用耳朵、尾巴或轻缓步态回应变化",
            "猫咪自然蜷卧或靠近儿童，不持工具、不直立劳动",
        )
    return (
        "猫咪保持猫科身体结构，佩戴固定小配件观察",
        "猫咪短暂进行简单道具互动，不生成手掌或人形肢体",
        "猫咪恢复自然猫科姿态，与儿童形成温暖收尾",
    )


def _storyboard_revision_json(
    revision: StoryboardRevision,
    beats: list[ShotBeat],
    active_scenes: list[Scene],
) -> dict[str, Any]:
    return {
        "id": str(revision.id),
        "storyRevisionId": str(revision.story_revision_id),
        "revision": revision.revision,
        "status": revision.status,
        "structureHash": revision.structure_hash,
        "approvedStructureAt": (
            None
            if revision.approved_structure_at is None
            else revision.approved_structure_at.isoformat()
        ),
        "productionPackageHash": revision.production_package_hash,
        "productionApprovedAt": (
            None
            if revision.production_approved_at is None
            else revision.production_approved_at.isoformat()
        ),
        "shotCount": len(beats),
        "totalDurationSeconds": sum(beat.duration_seconds for beat in beats),
        "warnings": [
            item.model_dump(mode="json", by_alias=True)
            for item in _storyboard_diagnostics(beats, active_scenes)
        ],
    }


def _editorial_shot_json(
    beat: ShotBeat,
    *,
    generation_clip_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "id": str(beat.id),
        "sceneId": str(beat.scene_id),
        "storyboardRevisionId": (
            None if beat.storyboard_revision_id is None else str(beat.storyboard_revision_id)
        ),
        "generationClipId": (
            None
            if generation_clip_id is None and beat.shot_card_id is None
            else str(generation_clip_id or beat.shot_card_id)
        ),
        "order": beat.sort_order,
        "revision": beat.revision,
        "title": beat.title,
        "durationSeconds": beat.duration_seconds,
        "direction": beat.action,
        "action": beat.action,
        "referenceBindingRevision": beat.reference_binding_revision,
        "referenceBindings": list(beat.reference_bindings_json or []),
        "visualDescription": beat.visual_description,
        "childAction": beat.child_action,
        "catAction": beat.cat_action,
        "spatialRelation": beat.spatial_relation,
        "contactOcclusion": beat.contact_occlusion,
        "shotSize": beat.shot_size,
        "camera": beat.camera,
        "lighting": beat.lighting,
        "dialogue": beat.dialogue,
        "soundEffect": beat.sound_effect,
        "musicIntent": beat.music_intent,
        "wardrobeState": beat.wardrobe_state,
        "propState": beat.prop_state,
        "continuityIn": beat.continuity_in,
        "continuityOut": beat.continuity_out,
        "cutIntent": beat.cut_intent,
        "status": beat.status,
        "staleReason": beat.stale_reason,
        "promptId": None if beat.prompt_id is None else str(beat.prompt_id),
    }


def _generation_plan_json(
    plan: GenerationPlan,
    shots_by_id: dict[uuid.UUID, ShotCard],
    mapped_beats_by_shot: dict[uuid.UUID, list[ShotBeat]],
) -> dict[str, Any]:
    capability = _video_model_capability_json(plan)
    clips = [
        {
            "id": str(shot_id),
            "title": shots_by_id[shot_id].title,
            "durationSeconds": shots_by_id[shot_id].duration_seconds,
            "mode": (
                "multi_shot" if len(mapped_beats_by_shot.get(shot_id, [])) > 1 else "single_shot"
            ),
            "editorialShotIds": [str(beat.id) for beat in mapped_beats_by_shot.get(shot_id, [])],
        }
        for shot_id in mapped_beats_by_shot
        if shot_id in shots_by_id
    ]
    return {
        "id": str(plan.id),
        "storyboardRevisionId": str(plan.storyboard_revision_id),
        "revision": plan.revision,
        "status": plan.status,
        "provider": plan.provider,
        "model": plan.model,
        "capabilityRevision": plan.capability_revision,
        "inputHash": plan.input_hash,
        "estimatedImageCallCount": plan.estimated_image_call_count,
        "estimatedVideoCallCount": plan.estimated_video_call_count,
        "estimatedCostMicros": plan.estimated_cost_micros,
        "warnings": plan.warnings_json,
        "blockers": plan.blockers_json,
        "reason": (
            f"在场景、连续性与 {capability['minimumDurationSeconds']}–"
            f"{capability['maximumDurationSeconds']}秒能力约束内优先减少真实视频调用"
        ),
        "capability": capability,
        "clips": clips,
        "approvedAt": None if plan.approved_at is None else plan.approved_at.isoformat(),
    }


def _video_model_capability_json(plan: GenerationPlan) -> dict[str, Any]:
    if plan.capability_revision == SEEDANCE_2_0_CAPABILITY.capability_revision:
        return SEEDANCE_2_0_CAPABILITY.model_dump(mode="json", by_alias=True)
    return {
        "provider": plan.provider,
        "model": plan.model,
        "capabilityRevision": plan.capability_revision,
        "minimumDurationSeconds": 8,
        "maximumDurationSeconds": 15,
        "supportedDurations": list(range(8, 16)),
        "supportsMultiShot": False,
        "maximumEditorialShots": 1,
        "maximumImageReferences": 9,
        "maximumVideoReferences": 9,
        "maximumAudioReferences": 1,
        "supportsFirstFrame": True,
        "firstFrameExcludesReferences": True,
        "supportsNativeAudio": True,
        "supportedResolutions": ["480p", "720p"],
        "supportedAspectRatios": ["9:16", "16:9", "1:1"],
        "imageCallCostMicros": None,
        "videoCallCostMicros": None,
    }


def _generation_clip_json(
    session: Session,
    shot: ShotCard,
    beats: list[ShotBeat],
    assets: list[Asset],
) -> dict[str, Any]:
    anchor_assets = [asset for asset in assets if asset.role == "shot_anchor"]
    video_assets = [asset for asset in assets if asset.role in {"shot_video", "shot_video_edit"}]
    cursor = 0
    windows: list[dict[str, Any]] = []
    for beat in beats:
        windows.append(
            {
                "beatId": str(beat.id),
                "startSecond": cursor,
                "endSecond": cursor + beat.duration_seconds,
                "title": beat.title,
            }
        )
        cursor += beat.duration_seconds
    return {
        "beatId": None if not beats else str(beats[0].id),
        "shotId": str(shot.id),
        "generationPlanId": (
            None if shot.generation_plan_id is None else str(shot.generation_plan_id)
        ),
        "editorialShotIds": [str(beat.id) for beat in beats],
        "title": shot.title,
        "durationSeconds": shot.duration_seconds,
        "status": shot.status,
        "mode": "multi_shot" if len(beats) > 1 else "single_shot",
        "temporalBeats": windows,
        "promptId": None if shot.prompt_id is None else str(shot.prompt_id),
        "promptCompiled": shot.prompt_id is not None,
        "selectedAnchorAssetId": (
            None if shot.selected_anchor_asset_id is None else str(shot.selected_anchor_asset_id)
        ),
        "selectedVideoAssetId": (
            None if shot.selected_video_asset_id is None else str(shot.selected_video_asset_id)
        ),
        "anchorCandidates": [_recipe_asset_json(session, asset) for asset in anchor_assets],
        "videoCandidates": [_recipe_asset_json(session, asset) for asset in video_assets],
    }


def _recipe_asset_json(session: Session, asset: Asset) -> dict[str, Any]:
    reviews = list(
        session.scalars(
            select(Review).where(Review.asset_id == asset.id).order_by(Review.created_at)
        )
    )
    diagnostics = [
        warning
        for review in reviews
        if review.source in {"ark_visual", "technical"}
        for warning in review.warnings_json
    ]
    visual_review_present = any(review.source == "ark_visual" for review in reviews)
    return {
        "id": str(asset.id),
        "sha256": asset.sha256,
        "status": asset.status,
        "mediaType": asset.media_type,
        "contentUrl": f"/api/v1/assets/{asset.id}/content",
        "qc": asset.metadata_json.get("qc"),
        "diagnosticStatus": (
            "not_run" if not visual_review_present else "failed" if diagnostics else "passed"
        ),
        "diagnostics": diagnostics,
    }


def _healing_clip_direction(
    beats: list[ShotBeat],
    rules: EpisodeRules,
    scene_context: dict[str, Any],
) -> str:
    """Compile ordered director beats into one provider-facing clip direction."""

    cursor = 0
    windows: list[str] = []
    for index, beat in enumerate(beats, 1):
        end_second = cursor + beat.duration_seconds
        windows.append(
            f"分镜{index} {cursor}-{end_second}秒：{beat.visual_description or beat.action}；"
            f"儿童：{beat.child_action or beat.action}；"
            f"猫咪：{beat.cat_action or '保持自然四足行为'}；"
            f"空间关系：{beat.spatial_relation or '保持同场连续'}；"
            f"接触/遮挡：{beat.contact_occlusion or '避免错误穿插'}；"
            f"景别与运镜：{beat.shot_size}，{beat.camera or '固定或轻微移动'}；"
            f"连续性：{beat.continuity_in or '承接上一镜'} → "
            f"{beat.continuity_out or '保留给下一镜'}。"
        )
        cursor = end_second
    continuity = dict(scene_context.get("continuity") or {})
    return (
        f"场景：{continuity.get('location') or rules.main_scene}；"
        f"时间天气：{rules.time_weather}；"
        f"儿童服装：{rules.person_wardrobe}；"
        f"画风：{' 、'.join(rules.style_positive)}。\n"
        + "\n".join(windows)
        + f"\n环境声：{' 、'.join(rules.sound_plan.ambient)}；"
        f"动作声：{' 、'.join(rules.sound_plan.foley)}；"
        f"音乐：{rules.sound_plan.music_mood}；无对白。"
    )


def _scene_continuity_document(scene: Scene) -> dict[str, Any]:
    if not scene.context_note:
        return {}
    try:
        document = json.loads(scene.context_note)
    except (TypeError, json.JSONDecodeError):
        return {}
    return document if isinstance(document, dict) else {}


def _workflow_step_reviews_target(
    step: WorkflowStep,
    payload: HumanReviewDraft,
    *,
    recipe_instance_id: uuid.UUID,
) -> bool:
    """Return whether an awaiting task explicitly declares this immutable target."""

    snapshot = dict(step.input_snapshot_json or {})
    if str(snapshot.get("recipeInstanceId") or "") != str(recipe_instance_id):
        return False
    progress = dict(step.progress_json or {})
    if progress.get("childStepIds"):
        # Media parents are settled only by aggregating their reviewed child tasks.
        return False
    result_summary = progress.get("resultSummary")
    if not isinstance(result_summary, dict):
        return False
    targets = result_summary.get("reviewTargets")
    if (
        payload.target_type == "storyboard_structure"
        and (
            snapshot.get("operationKey") == "recipe:storyboard"
            or result_summary.get("operationKey") == "recipe:storyboard"
        )
    ):
        # The production-plan confirmation validates and records the current
        # immutable structure before reaching this reconciliation boundary.
        # Older storyboard tasks may omit the operation in resultSummary,
        # declare ``storyboard_revision`` instead, or retain the revision they
        # originally generated. The exact recipe and immutable operation in
        # the task snapshot identify the durable task without weakening media
        # or cross-recipe review matching.
        return True
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, dict):
                continue
            if str(target.get("targetType") or "") != payload.target_type:
                continue
            if str(target.get("targetId") or "") != str(payload.target_id):
                continue
            declared_revision = target.get("targetRevision")
            if (
                declared_revision is not None
                and payload.target_revision is not None
                and int(str(declared_revision)) != payload.target_revision
            ):
                continue
            declared_hash = target.get("targetHash")
            if (
                declared_hash is not None
                and payload.target_hash is not None
                and str(declared_hash) != payload.target_hash
            ):
                continue
            return True
    return payload.target_type in {"character_design", "anchor_asset", "video_asset"} and str(
        result_summary.get("assetId") or ""
    ) == str(payload.target_id)


def _workflow_parent_step_id(step: WorkflowStep) -> uuid.UUID | None:
    value = dict(step.input_snapshot_json or {}).get("parentStepId")
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _workflow_child_step_ids(step: WorkflowStep) -> tuple[uuid.UUID, ...]:
    values = dict(step.progress_json or {}).get("childStepIds")
    if not isinstance(values, list):
        return ()
    result: list[uuid.UUID] = []
    for value in values:
        try:
            result.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return tuple(dict.fromkeys(result))


def _aggregate_review_parent_status(
    children: list[WorkflowStep],
) -> StepStatus | None:
    statuses = {child.status for child in children}
    if StepStatus.FAILED.value in statuses:
        return StepStatus.FAILED
    if children and statuses == {StepStatus.SUCCEEDED.value}:
        return StepStatus.SUCCEEDED
    return None


def _apply_review_to_workflow_step(
    step: WorkflowStep,
    *,
    payload: HumanReviewDraft,
    review: HumanReviewDecisionRecord,
) -> None:
    accepted = payload.decision.value in _APPROVING_DECISIONS
    now = datetime.now(UTC)
    result_summary = dict(dict(step.progress_json or {}).get("resultSummary") or {})
    result_summary.update(
        {
            "status": "succeeded" if accepted else "changes_requested",
            "message": "人工审核已通过" if accepted else "人工审核已退回修改",
            "reviewId": str(review.id),
            "reviewDecision": payload.decision.value,
            "reviewTarget": {
                "targetType": payload.target_type,
                "targetId": str(payload.target_id),
                "targetRevision": payload.target_revision,
                "targetHash": payload.target_hash,
            },
        }
    )
    step.status = StepStatus.SUCCEEDED.value if accepted else StepStatus.FAILED.value
    step.error_json = (
        None
        if accepted
        else {
            "code": "human_review_changes_requested",
            "message": payload.reason or "人工审核要求修改",
            "failedStep": "human_review",
            "recoverable": True,
        }
    )
    step.progress_json = {
        **dict(step.progress_json or {}),
        "percent": 100,
        "message": result_summary["message"],
        "resultSummary": result_summary,
    }
    step.completed_at = now
    step.heartbeat_at = now
    step.next_retry_at = None
    step.lease_owner = None
    step.lease_expires_at = None


def _apply_child_aggregate_to_workflow_step(
    parent: WorkflowStep,
    *,
    children: list[WorkflowStep],
    status: StepStatus,
) -> None:
    now = datetime.now(UTC)
    succeeded = sum(child.status == StepStatus.SUCCEEDED.value for child in children)
    failed = sum(child.status == StepStatus.FAILED.value for child in children)
    message = (
        f"全部 {len(children)} 个子任务已通过人工审核"
        if status is StepStatus.SUCCEEDED
        else f"{failed} 个子任务被人工退回"
    )
    result_summary = dict(dict(parent.progress_json or {}).get("resultSummary") or {})
    result_summary.update(
        {
            "status": status.value,
            "message": message,
            "childSucceededCount": succeeded,
            "childFailedCount": failed,
            "childCount": len(children),
        }
    )
    parent.status = status.value
    parent.error_json = (
        None
        if status is StepStatus.SUCCEEDED
        else {
            "code": "child_review_changes_requested",
            "message": message,
            "failedStep": "human_review",
            "recoverable": True,
        }
    )
    parent.progress_json = {
        **dict(parent.progress_json or {}),
        "percent": 100,
        "message": message,
        "resultSummary": result_summary,
    }
    parent.completed_at = now
    parent.heartbeat_at = now
    parent.next_retry_at = None
    parent.lease_owner = None
    parent.lease_expires_at = None


def _workflow_task_json(row: WorkflowStep) -> dict[str, Any]:
    snapshot = dict(row.input_snapshot_json or {})
    progress = dict(row.progress_json or {})
    return {
        "jobId": str(row.id),
        "kind": row.kind,
        "status": row.status,
        "projectId": str(row.production_run_id),
        "shotId": None if row.shot_card_id is None else str(row.shot_card_id),
        "canvasNodeId": snapshot.get("canvasNodeId"),
        "canvasGroupId": snapshot.get("canvasGroupId"),
        "recipeInstanceId": snapshot.get("recipeInstanceId"),
        "creationMode": snapshot.get("creationMode"),
        "parentStepId": snapshot.get("parentStepId"),
        "childStepIds": progress.get("childStepIds", []),
        "workflowStage": snapshot.get("workflowStage"),
        "phase": snapshot.get("phase"),
        "operationKey": row.operation_key,
        "progress": progress,
        "resultSummary": progress.get("resultSummary"),
        "error": row.error_json,
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
        "completedAt": None if row.completed_at is None else row.completed_at.isoformat(),
    }
