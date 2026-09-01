"""PostgreSQL persistence for the typed AIGC canvas.

Business objects and provider attempts are committed independently of canvas
coordinates.  Every method owns one short transaction so a browser disconnect
cannot leave an open transaction or an unrecorded paid intent.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import LandedAsset, StoredAsset, reference_display_name
from ...domain.aigc_canvas import (
    CanvasConnection,
    CanvasDiagnostic,
    CanvasNodeType,
    CanvasPortType,
    CompiledProviderReference,
    CreativeStoryCandidate,
    CreativeStoryCandidateBatch,
    GenerationInputPreview,
    NodeGenerationConfigDraft,
    ProductionFlowNodeKind,
    PromptRunDraft,
    StoryBrief,
    StoryCandidateOutput,
    StoryEventCandidateOutput,
    StoryEventCandidateStatus,
    StoryRevisionStatus,
    StoryScorecard,
    StoryStrategy,
    SubjectCompletionProposal,
    SubjectDraft,
    SubjectRole,
    WorkspaceModuleId,
    WorkspaceStatus,
    approve_story_revision,
    character_design_generation_input,
    creative_brief_canvas_node_id,
    generation_input_hash,
    subject_completion_missing_fields,
)
from ...domain.contracts import VisualProfileDraft
from ...domain.production_recipes import (
    CANON_V3_PROFILE_ID,
    CANON_V3_STYLE_NEGATIVE,
    CANON_V3_STYLE_POSITIVE,
    CANON_V4_PROFILE_ID,
    CANON_V4_STYLE_BOARD_KEY,
    CANON_V4_STYLE_NEGATIVE,
    CANON_V4_STYLE_POSITIVE,
    CANON_V4_STYLE_SOURCE_EXCLUSIONS,
    CANON_V4_STYLE_SOURCE_KEY,
    SEEDANCE_2_0_CAPABILITY,
    SEEDREAM_5_0_CAPABILITY,
    CharacterDesignSlot,
    EditorialCutIntent,
    EditorialShotDescriptor,
    GenerationPlanStatus,
    ProductionRecipeKey,
    RecipeDispatchError,
    StoryboardRevisionStatus,
    VisualPresetKey,
    canon_reference_keys,
    plan_generation_clips,
)
from ...domain.rendering import MediaSource, VideoInputPlan, build_shot_input_plan
from ...domain.universal_canvas import (
    CanvasTemplateKey,
    ProviderEditCapability,
    VideoEditRecipeDraft,
    compile_video_edit_plan,
    list_template_specs,
)
from ...domain.workflow import PromptPurpose, SceneStatus, StepKind, StepStatus
from .models import (
    Asset,
    CanvasEvent,
    CanvasGraphEdge,
    CanvasGraphNode,
    CanvasGroup,
    CanvasGroupMember,
    CanvasLayout,
    CanvasNodeArchive,
    CharacterDesignAsset,
    CharacterDesignRevision,
    GenerationAttempt,
    GenerationClipShot,
    GenerationPlan,
    HumanReviewDecisionRecord,
    MediaGenerationBatch,
    NodeGenerationConfig,
    ProductionRecipeInstance,
    ProductionRun,
    PromptRecord,
    ProviderCapability,
    Review,
    Scene,
    ShotBeat,
    ShotCard,
    ShotSubjectState,
    StoryboardRevision,
    StoryBriefRecord,
    StoryEventCandidateRecord,
    StoryRevisionRecord,
    StoryScore,
    Subject,
    SubjectCompletionRun,
    SubjectReference,
    SubjectRevision,
    VideoEditAnnotation,
    VideoEditRecipe,
    VideoEditReference,
    VideoSequence,
    VisualProfileRevision,
    WorkflowStep,
)
from .repositories import RecordNotFoundError, WorkflowConflictError
from .story_revision_lifecycle import (
    invalidate_story_production_lineage,
    requires_legacy_story_approval_contract,
    story_revision_contract_kind,
)
from .story_scenes import materialize_approved_story_scenes
from .story_scenes import normalized_story_scenes as _normalized_story_scenes
from .story_scenes import scene_look_plan_from_outline as _scene_look_plan_from_outline
from .storyboard_hashing import generation_plan_input_hash, storyboard_structure_hash
from .visual_preset_profiles import (
    CANON_V3_REQUIRED_KEYS,
    CANON_V4_REQUIRED_KEYS,
    ensure_canon_subjects,
    ensure_canon_visual_profile,
    episode_visual_profile_json,
    generation_reference_bindings,
    load_canon_assets,
    visual_preset_profile_json,
    visual_reference_json,
)


@dataclass(frozen=True, slots=True)
class StoredCanvasSubject:
    id: uuid.UUID
    revision_id: uuid.UUID
    revision: int
    draft: SubjectDraft
    status: str


@dataclass(frozen=True, slots=True)
class _GenerationBatchPlan:
    payload: Any
    node: CanvasGraphNode
    batch: MediaGenerationBatch
    steps: tuple[WorkflowStep, ...]
    prompts: tuple[PromptRecord, ...]


class SqlAlchemyAigcCanvasRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        asset_root: Path | None = None,
    ) -> None:
        self._sessions = sessions
        self._asset_root = (
            Path("var/assets").resolve()
            if asset_root is None
            else asset_root.expanduser().resolve()
        )

    def create_child_cat_project(self, payload: Any) -> dict[str, Any]:
        """Create the unpaid one-child-one-cat production foundation atomically."""

        title = payload.title.strip()
        brief_body = payload.brief.body.strip()
        if not title:
            raise ValueError("项目名称不能为空")
        if not brief_body:
            raise ValueError("创作要求不能为空")
        if payload.child_canon_profile_id != CANON_V4_PROFILE_ID:
            raise ValueError("儿童 Canon 必须选择当前的一人一猫 Canon v4")
        if payload.cat_canon_profile_id != CANON_V4_PROFILE_ID:
            raise ValueError("猫咪 Canon 必须选择当前的一人一猫 Canon v4")
        with self._sessions.begin() as session:
            assets_by_key = load_canon_assets(session, CANON_V4_REQUIRED_KEYS)
            style_board = assets_by_key[CANON_V4_STYLE_BOARD_KEY]
            if payload.style_board_asset_id != style_board.id:
                raise ValueError("画风板必须选择已批准的 Canon v4 净化画风板")
            project = ProductionRun(
                id=uuid.uuid4(),
                title=title,
                content_date=payload.content_date or date.today(),
                status="active",
                canvas_v2_enabled=False,
                canvas_template_key="short_drama",
                universal_canvas_enabled=False,
                product_ad_template_enabled=False,
                video_edit_v2_enabled=True,
                default_reference_bindings_json=[],
            )
            session.add(project)
            session.flush()
            brief = StoryBriefRecord(
                id=uuid.uuid4(),
                production_run_id=project.id,
                revision=1,
                theme=brief_body,
                audience="喜欢原创、低压力日常短视频的观众",
                genre="原创一人一猫治愈短片",
                tone="安静、温暖、低对白、动作清楚",
                aspect_ratio=payload.brief.aspect_ratio,
                target_duration_seconds=payload.brief.duration_seconds,
                constraints_json=[
                    "固定同一名 8–9 岁儿童的脸型、五官、短发与身体比例",
                    "固定同一只灰白虎斑猫的毛色、虎斑、四足结构与环纹尾巴",
                    "统一使用已批准的净化画风板",
                    "保持原创角色与原创日常陪伴叙事",
                ],
            )
            session.add(brief)
            scene = Scene(
                id=uuid.uuid4(),
                production_run_id=project.id,
                sort_order=1,
                title="本集场景",
                source_text=brief_body,
                story_mode="single",
                target_shot_count=1,
                status=SceneStatus.DRAFT.value,
            )
            session.add(scene)
            visual_profile = ensure_canon_visual_profile(
                session,
                project_id=project.id,
                canon_profile_id=CANON_V4_PROFILE_ID,
                assets_by_key=assets_by_key,
            )
            subjects = ensure_canon_subjects(
                session,
                project_id=project.id,
                assets_by_key=assets_by_key,
                canon_profile_id=CANON_V4_PROFILE_ID,
            )
            project.current_visual_profile_revision_id = visual_profile.id
            project.default_reference_bindings_json = generation_reference_bindings(
                assets_by_key,
                CANON_V4_REQUIRED_KEYS,
            )
            recipe = ProductionRecipeInstance(
                id=uuid.uuid4(),
                production_run_id=project.id,
                recipe_key=ProductionRecipeKey.HEALING_CHILD_CAT_V1.value,
                recipe_version=1,
                revision=1,
                theme=brief_body,
                inspiration_key=None,
                target_duration_seconds=payload.brief.duration_seconds,
                quality_tier=payload.brief.quality_tier,
                canon_profile_id=CANON_V4_PROFILE_ID,
                lifecycle_status="active",
            )
            session.add(recipe)
            session.flush()
            return {
                "projectId": str(project.id),
                "briefId": str(brief.id),
                "recipeInstanceId": str(recipe.id),
                "subjectIds": {
                    role: str(subject.id) for role, subject in subjects.items()
                },
                "providerCallCount": 0,
            }

    def save_brief(self, project_id: uuid.UUID, payload: StoryBrief) -> dict[str, Any]:
        document = payload.model_dump(mode="json", by_alias=True)
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            project.canvas_v2_enabled = True
            latest = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            if latest is not None and _brief_document(latest) == document:
                return _brief_json(latest)
            row = StoryBriefRecord(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=1 if latest is None else latest.revision + 1,
                theme=payload.theme,
                audience=payload.audience,
                genre=payload.genre,
                tone=payload.tone,
                aspect_ratio=payload.aspect_ratio,
                target_duration_seconds=payload.target_duration_seconds,
                constraints_json=list(payload.constraints),
            )
            session.add(row)
            session.flush()
            self._mark_project_story_stale(session, project_id, "creative brief changed")
            return _brief_json(row)

    def get_current_brief(self, project_id: uuid.UUID) -> tuple[uuid.UUID, StoryBrief]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            row = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            if row is None:
                raise RecordNotFoundError(f"project {project_id} has no story brief")
            return row.id, StoryBrief.model_validate(_brief_document(row))

    def create_subject(self, project_id: uuid.UUID, payload: SubjectDraft) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            self._ensure_subject_name_available(session, project_id, payload.name)
            subject = Subject(
                id=uuid.uuid4(),
                production_run_id=project_id,
                kind=payload.kind.value,
                role=payload.role.value,
                status="draft",
            )
            session.add(subject)
            session.flush()
            revision = self._add_subject_revision(session, subject, payload)
            subject.current_revision_id = revision.id
            session.flush()
            if project.universal_canvas_enabled:
                graph_node = CanvasGraphNode(
                    id=subject.id,
                    production_run_id=project_id,
                    node_type=CanvasNodeType.SUBJECT.value,
                    object_type="subject",
                    object_id=subject.id,
                    status=subject.status,
                    data_json={
                        "title": payload.name,
                        "kind": payload.kind.value,
                        "role": payload.role.value,
                    },
                )
                session.add(graph_node)
                session.flush()
                batch_node = session.scalar(
                    select(CanvasGraphNode).where(
                        CanvasGraphNode.production_run_id == project_id,
                        CanvasGraphNode.node_type == CanvasNodeType.GENERATION_BATCH.value,
                    )
                )
                if batch_node is not None and payload.role is SubjectRole.HERO_PRODUCT:
                    session.add(
                        _graph_edge(
                            project_id,
                            CanvasConnection(
                                sourceNodeId=graph_node.id,
                                sourceNodeType=CanvasNodeType.SUBJECT,
                                sourcePort="product_subject",
                                targetNodeId=batch_node.id,
                                targetNodeType=CanvasNodeType.GENERATION_BATCH,
                                targetPort="product_subject",
                            ),
                        )
                    )
                if batch_node is not None:
                    for reference in payload.references:
                        asset = self._required(session, Asset, reference.asset_id, lock=True)
                        reference_node_id = uuid.uuid5(project_id, f"reference-asset:{asset.id}")
                        reference_node = session.get(CanvasGraphNode, reference_node_id)
                        if reference_node is None:
                            reference_node = CanvasGraphNode(
                                id=reference_node_id,
                                production_run_id=project_id,
                                node_type=CanvasNodeType.REFERENCE_ASSET.value,
                                object_type="asset",
                                object_id=asset.id,
                                status=asset.status,
                                data_json={
                                    "title": asset.semantic_key or asset.role,
                                    "assetId": str(asset.id),
                                    "semanticRole": reference.semantic_role,
                                    "thumbnailUrl": f"/api/v1/assets/{asset.id}/content",
                                },
                            )
                            session.add(reference_node)
                            session.flush()
                            asset.canvas_node_id = reference_node.id
                            asset.scope = "canvas_node"
                            session.add(
                                _graph_edge(
                                    project_id,
                                    CanvasConnection(
                                        sourceNodeId=reference_node.id,
                                        sourceNodeType=CanvasNodeType.REFERENCE_ASSET,
                                        sourcePort="media_reference[]",
                                        targetNodeId=batch_node.id,
                                        targetNodeType=CanvasNodeType.GENERATION_BATCH,
                                        targetPort="media_reference[]",
                                    ),
                                )
                            )
            self._mark_project_story_stale(session, project_id, "subject added")
            return _subject_json(
                session, subject, revision, self._subject_references(session, revision.id)
            )

    def create_subject_revision(
        self,
        subject_id: uuid.UUID,
        payload: SubjectDraft,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            subject = self._required(session, Subject, subject_id, lock=True)
            self._ensure_subject_name_available(
                session,
                subject.production_run_id,
                payload.name,
                excluding_subject_id=subject_id,
            )
            current = (
                None
                if subject.current_revision_id is None
                else self._required(session, SubjectRevision, subject.current_revision_id)
            )
            revision_hash = _subject_hash(payload)
            if current is not None and current.revision_hash == revision_hash:
                return _subject_json(
                    session,
                    subject,
                    current,
                    self._subject_references(session, current.id),
                )
            subject.kind = payload.kind.value
            subject.role = payload.role.value
            revision = self._add_subject_revision(session, subject, payload)
            subject.current_revision_id = revision.id
            session.flush()
            graph_node = session.get(CanvasGraphNode, subject.id)
            if graph_node is not None:
                graph_node.revision += 1
                graph_node.status = "stale"
                graph_node.data_json = {
                    **graph_node.data_json,
                    "title": payload.name,
                    "kind": payload.kind.value,
                    "role": payload.role.value,
                }
            self._mark_subject_downstream_stale(session, subject_id)
            return _subject_json(
                session, subject, revision, self._subject_references(session, revision.id)
            )

    def create_subject_completion_run(
        self,
        project_id: uuid.UUID,
        payload: Any,
        *,
        provider: str,
        model: str,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            existing = session.scalar(
                select(SubjectCompletionRun).where(
                    SubjectCompletionRun.idempotency_key == payload.idempotency_key
                )
            )
            if existing is not None:
                if existing.production_run_id != project_id:
                    raise WorkflowConflictError("主体补全幂等键已被其他项目使用")
                return _subject_completion_json(existing)
            subject = self._required(session, Subject, payload.subject_id, lock=True)
            if subject.production_run_id != project_id:
                raise ValueError("主体不属于当前项目")
            if subject.current_revision_id is None:
                raise WorkflowConflictError("主体没有可分析的当前版本")
            revision = self._required(session, SubjectRevision, subject.current_revision_id)
            source = _subject_draft(
                subject,
                revision,
                self._subject_references(session, revision.id),
            )
            missing_fields = list(subject_completion_missing_fields(source))
            source_snapshot = source.model_dump(mode="json", by_alias=True)
            input_snapshot = {
                "projectId": str(project_id),
                "subjectId": str(subject.id),
                "sourceRevisionId": str(revision.id),
                "subject": source_snapshot,
                "missingFields": missing_fields,
                "instruction": payload.instruction,
            }
            input_hash = _json_hash(input_snapshot)
            step = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=project_id,
                kind=StepKind.DIRECTOR.value,
                status=StepStatus.PENDING.value,
                attempt=1,
                operation_key=f"subject:complete:{subject.id}:{revision.id}",
                idempotency_key=hashlib.sha256(payload.idempotency_key.encode()).hexdigest(),
                provider=provider,
                model=model,
                input_hash=input_hash,
                request_hash=input_hash,
                input_snapshot_json=input_snapshot,
            )
            session.add(step)
            system_prompt = (
                "你是AIGC媒体主体设定分析师。只补全有助于跨镜头一致性和戏剧功能的字段，"
                "不得更换主体身份、类型、角色或凭空删除用户锚点。输出必须可由用户逐项审核。"
            )
            user_prompt = (
                f"待补全字段：{json.dumps(missing_fields, ensure_ascii=False)}\n"
                f"用户要求：{payload.instruction or '无额外要求'}\n"
                f"主体快照：{json.dumps(source_snapshot, ensure_ascii=False, sort_keys=True)}"
            )
            final_prompt = f"{system_prompt}\n\n{user_prompt}"
            prompt = PromptRecord(
                id=uuid.uuid4(),
                step_id=step.id,
                purpose=PromptPurpose.DIRECTOR.value,
                model=model,
                prompt_text=final_prompt,
                sha256=hashlib.sha256(final_prompt.encode()).hexdigest(),
                call_purpose="subject_completion",
                node_id=subject.id,
                business_object_type="subject_completion_run",
                business_object_id=subject.id,
                template_name="subject.completion.v1",
                template_version="1.0.0",
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                final_prompt=final_prompt,
                provider_request_json={
                    "outputName": "SubjectCompletionProposal",
                    "input": input_snapshot,
                },
                input_snapshot_json=input_snapshot,
                parameters_json={},
                status="pending",
                input_hash=input_hash,
            )
            session.add(prompt)
            run = SubjectCompletionRun(
                id=uuid.uuid4(),
                production_run_id=project_id,
                subject_id=subject.id,
                source_revision_id=revision.id,
                workflow_step_id=step.id,
                prompt_id=prompt.id,
                idempotency_key=payload.idempotency_key,
                status="pending",
                model=model,
                missing_fields_json=missing_fields,
            )
            session.add(run)
            graph_node = session.get(CanvasGraphNode, subject.id)
            if graph_node is not None:
                graph_node.data_json = {
                    **graph_node.data_json,
                    "subjectCompletionRunId": str(run.id),
                    "completionStatus": "pending",
                }
            self._record_event(
                session,
                project_id,
                "subject_completion_queued",
                {"runId": str(run.id), "subjectId": str(subject.id), "stepId": str(step.id)},
            )
            return _subject_completion_json(run)

    def subject_completion_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("subject:complete:"):
                raise ValueError("workflow step is not a subject completion")
            run = session.scalar(
                select(SubjectCompletionRun).where(SubjectCompletionRun.workflow_step_id == step.id)
            )
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if run is None or prompt is None or prompt.status != "pending":
                raise WorkflowConflictError("主体补全缺少待执行的精确 Prompt")
            return {"runId": str(run.id), "prompt": prompt.final_prompt or prompt.prompt_text}

    def complete_subject_completion(
        self,
        step_id: uuid.UUID,
        *,
        proposal: dict[str, object],
        raw_response: dict[str, object],
    ) -> str:
        validated = SubjectCompletionProposal.model_validate(proposal)
        proposal_json = validated.model_dump(mode="json", by_alias=True)
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            run = session.scalar(
                select(SubjectCompletionRun)
                .where(SubjectCompletionRun.workflow_step_id == step.id)
                .with_for_update()
            )
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if run is None or prompt is None:
                raise RecordNotFoundError(f"subject completion for step {step_id} was not found")
            if run.status == "awaiting_review":
                return str(run.id)
            if run.status != "pending":
                raise WorkflowConflictError("主体补全运行已不再等待供应商结果")
            run.status = "awaiting_review"
            run.proposal_json = proposal_json
            run.completed_at = now
            prompt.status = "succeeded"
            prompt.raw_response_json = raw_response
            prompt.structured_response_json = proposal_json
            prompt.output_hash = _json_hash(proposal_json)
            prompt.completed_at = now
            graph_node = session.get(CanvasGraphNode, run.subject_id)
            if graph_node is not None:
                graph_node.data_json = {
                    **graph_node.data_json,
                    "subjectCompletionRunId": str(run.id),
                    "completionStatus": "awaiting_review",
                    "completionMissingFields": run.missing_fields_json,
                }
            self._record_event(
                session,
                run.production_run_id,
                "subject_completion_ready",
                {"runId": str(run.id), "subjectId": str(run.subject_id)},
            )
            return str(run.id)

    def get_subject_completion_run(self, run_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            run = self._required(session, SubjectCompletionRun, run_id)
            return _subject_completion_json(run)

    def apply_subject_completion(self, run_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions.begin() as session:
            run = self._required(session, SubjectCompletionRun, run_id, lock=True)
            subject = self._required(session, Subject, run.subject_id, lock=True)
            if run.status == "applied":
                current = self._required(session, SubjectRevision, subject.current_revision_id)
                return {
                    "runId": str(run.id),
                    "status": run.status,
                    "acceptedFields": run.accepted_fields_json or [],
                    **_subject_json(
                        session,
                        subject,
                        current,
                        self._subject_references(session, current.id),
                    ),
                }
            if run.status != "awaiting_review" or run.proposal_json is None:
                raise WorkflowConflictError("主体补全建议尚未就绪，不能应用")
            if subject.current_revision_id != run.source_revision_id:
                raise WorkflowConflictError("主体版本已变化，请基于新版本重新运行补全")
            source_revision = self._required(session, SubjectRevision, run.source_revision_id)
            source = _subject_draft(
                subject,
                source_revision,
                self._subject_references(session, source_revision.id),
            )
            final_draft = payload.final_draft
            accepted = set(payload.accepted_fields)
            fixed_fields = ("name", "kind", "role", "references")
            if any(getattr(final_draft, field) != getattr(source, field) for field in fixed_fields):
                raise ValueError("主体补全不能修改名称、类型、角色或参考素材绑定")
            aliases = {
                "identityAnchors": "identity_anchors",
                "immutableTraits": "immutable_traits",
                "relationshipNotes": "relationship_notes",
                "dramaticFunction": "dramatic_function",
                "visualRisks": "visual_risks",
            }
            for alias, attribute in aliases.items():
                unchanged = getattr(final_draft, attribute) == getattr(source, attribute)
                if alias not in accepted and not unchanged:
                    raise ValueError(f"字段 {alias} 未被接受，不能修改")
            revision = self._add_subject_revision(session, subject, final_draft)
            subject.current_revision_id = revision.id
            run.status = "applied"
            run.accepted_fields_json = list(payload.accepted_fields)
            run.accepted_draft_json = final_draft.model_dump(mode="json", by_alias=True)
            prompt = (
                None
                if run.prompt_id is None
                else self._required(session, PromptRecord, run.prompt_id, lock=True)
            )
            if prompt is not None:
                prompt.accepted_response_json = run.accepted_draft_json
                prompt.response_diff_json = {
                    "acceptedFields": run.accepted_fields_json,
                    "sourceRevisionId": str(run.source_revision_id),
                    "createdRevisionId": str(revision.id),
                }
            graph_node = session.get(CanvasGraphNode, subject.id)
            if graph_node is not None:
                graph_node.revision += 1
                graph_node.status = "stale"
                graph_node.data_json = {
                    **graph_node.data_json,
                    "title": final_draft.name,
                    "completionStatus": "applied",
                    "subjectRevisionId": str(revision.id),
                }
            self._mark_subject_downstream_stale(session, subject.id)
            self._record_event(
                session,
                run.production_run_id,
                "subject_completion_applied",
                {
                    "runId": str(run.id),
                    "subjectId": str(subject.id),
                    "revisionId": str(revision.id),
                },
            )
            return {
                "runId": str(run.id),
                "status": run.status,
                "acceptedFields": run.accepted_fields_json,
                **_subject_json(
                    session,
                    subject,
                    revision,
                    self._subject_references(session, revision.id),
                ),
            }

    def list_project_assets(
        self, project_id: uuid.UUID, *, media_kind: str | None = None
    ) -> list[dict[str, Any]]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            query = select(Asset).where(Asset.production_run_id == project_id)
            if media_kind is not None:
                query = query.where(Asset.media_type == media_kind)
            rows = list(
                session.scalars(query.order_by(Asset.created_at.desc(), Asset.id).limit(200))
            )
            row_ids = [row.id for row in rows]
            design_bindings = list(
                session.scalars(
                    select(CharacterDesignAsset)
                    .where(CharacterDesignAsset.asset_id.in_(row_ids))
                    .order_by(CharacterDesignAsset.created_at, CharacterDesignAsset.id)
                )
            ) if row_ids else []
            design_revisions = {
                revision.id: revision
                for revision in session.scalars(
                    select(CharacterDesignRevision).where(
                        CharacterDesignRevision.id.in_(
                            [binding.character_design_revision_id for binding in design_bindings]
                        )
                    )
                )
            }
            current_design_revision_ids: dict[uuid.UUID, uuid.UUID] = {}
            for revision in session.scalars(
                select(CharacterDesignRevision)
                .where(CharacterDesignRevision.production_run_id == project_id)
                .order_by(
                    CharacterDesignRevision.production_recipe_instance_id,
                    CharacterDesignRevision.revision.desc(),
                    CharacterDesignRevision.id,
                )
            ):
                current_design_revision_ids.setdefault(
                    revision.production_recipe_instance_id,
                    revision.id,
                )
            design_binding_by_asset_id = {binding.asset_id: binding for binding in design_bindings}

            def character_design_document(row: Asset) -> dict[str, Any] | None:
                binding = design_binding_by_asset_id.get(row.id)
                if binding is None:
                    return None
                revision = design_revisions.get(binding.character_design_revision_id)
                if revision is None:
                    return None
                return {
                    "recipeInstanceId": str(revision.production_recipe_instance_id),
                    "revisionId": str(revision.id),
                    "revision": revision.revision,
                    "revisionStatus": revision.status,
                    "isCurrentRevision": (
                        current_design_revision_ids.get(
                            revision.production_recipe_instance_id
                        )
                        == revision.id
                    ),
                    "slot": binding.slot,
                    "candidateIndex": binding.candidate_index,
                    "semanticRole": binding.semantic_role,
                    "selected": binding.selected,
                }

            def review_action(row: Asset, design: dict[str, Any] | None) -> dict[str, Any]:
                if design is not None:
                    character_design_metadata = dict(
                        dict(row.metadata_json or {}).get("characterDesign") or {}
                    )
                    if character_design_metadata.get("validationOnly") is True:
                        return {
                            "executable": False,
                            "route": "readonly",
                            "recipeInstanceId": design["recipeInstanceId"],
                            "targetType": "character_design",
                            "targetId": str(row.id),
                            "targetHash": row.sha256,
                            "disabledReason": (
                                "引用顺序验证候选只用于审计，不能审核或替换生产版本"
                            ),
                        }
                    current = bool(design["isCurrentRevision"])
                    executable = bool(
                        current
                        and row.media_type == "image"
                        and row.status != "stale"
                        and design["revisionStatus"] != "stale"
                    )
                    return {
                        "executable": executable,
                        "route": "recipe_character_design" if executable else "readonly",
                        "recipeInstanceId": design["recipeInstanceId"],
                        "targetType": "character_design",
                        "targetId": str(row.id),
                        "targetHash": row.sha256,
                        **(
                            {}
                            if executable
                            else {"disabledReason": "仅当前角色设计 Revision 可执行审核"}
                        ),
                    }
                legacy_reviewable = bool(
                    row.scope != "canon"
                    and row.producing_step_id is not None
                    and row.media_type in {"image", "video", "audio"}
                    and row.status != "stale"
                )
                return {
                    "executable": legacy_reviewable,
                    "route": "legacy_asset" if legacy_reviewable else "readonly",
                    "targetId": str(row.id),
                    **(
                        {}
                        if legacy_reviewable
                        else {"disabledReason": "导入或不可变资产没有可执行的审核目标"}
                    ),
                }

            return [
                {
                    "id": str(row.id),
                    "projectId": str(project_id),
                    "canvasNodeId": None if row.canvas_node_id is None else str(row.canvas_node_id),
                    "mediaType": row.media_type,
                    "role": row.role,
                    "status": row.status,
                    "semanticKey": row.semantic_key,
                    "sha256": row.sha256,
                    "metadata": row.metadata_json,
                    "contentUrl": f"/api/v1/assets/{row.id}/content",
                    "createdAt": None if row.created_at is None else row.created_at.isoformat(),
                    "characterDesign": (design := character_design_document(row)),
                    "reviewAction": review_action(row, design),
                }
                for row in rows
            ]

    def list_visual_presets(self) -> list[dict[str, Any]]:
        """Return reusable visual evidence packages without copying their assets."""

        with self._sessions() as session:
            return [
                visual_preset_profile_json(
                    session,
                    VisualPresetKey.HEALING_CHILD_CAT_STYLE_BOARD_V4,
                ),
                visual_preset_profile_json(
                    session,
                    VisualPresetKey.HEALING_CHILD_CAT_LINE_TEXTURE,
                ),
            ]

    def apply_visual_preset(
        self,
        project_id: uuid.UUID,
        preset_key: str,
    ) -> dict[str, Any]:
        try:
            resolved_key = VisualPresetKey(preset_key)
        except ValueError as exc:
            raise ValueError(f"不支持的视觉预设：{preset_key}") from exc
        if resolved_key not in {
            VisualPresetKey.HEALING_CHILD_CAT_STYLE_BOARD_V4,
            VisualPresetKey.HEALING_CHILD_CAT_LINE_TEXTURE,
        }:
            raise ValueError(f"不支持的视觉预设：{preset_key}")

        is_v4 = resolved_key is VisualPresetKey.HEALING_CHILD_CAT_STYLE_BOARD_V4
        canon_profile_id = CANON_V4_PROFILE_ID if is_v4 else CANON_V3_PROFILE_ID
        required_keys = CANON_V4_REQUIRED_KEYS if is_v4 else CANON_V3_REQUIRED_KEYS
        style_key = CANON_V4_STYLE_BOARD_KEY if is_v4 else "style:line_texture"
        style_positive = CANON_V4_STYLE_POSITIVE if is_v4 else CANON_V3_STYLE_POSITIVE
        style_negative = CANON_V4_STYLE_NEGATIVE if is_v4 else CANON_V3_STYLE_NEGATIVE

        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            previous_visual_profile_id = project.current_visual_profile_revision_id
            assets_by_key = load_canon_assets(session, required_keys)
            profile = ensure_canon_visual_profile(
                session,
                project_id=project_id,
                canon_profile_id=canon_profile_id,
                assets_by_key=assets_by_key,
            )
            subjects_by_role = ensure_canon_subjects(
                session,
                project_id=project_id,
                assets_by_key=assets_by_key,
                canon_profile_id=canon_profile_id,
            )
            project.canvas_v2_enabled = True
            project.universal_canvas_enabled = True
            recipe = session.scalar(
                select(ProductionRecipeInstance)
                .where(
                    ProductionRecipeInstance.production_run_id == project_id,
                    ProductionRecipeInstance.lifecycle_status == "active",
                )
                .order_by(ProductionRecipeInstance.created_at.desc())
                .limit(1)
                .with_for_update()
            )
            canon_profile_changed = bool(
                recipe is not None and recipe.canon_profile_id != canon_profile_id
            )
            if recipe is not None and canon_profile_changed:
                recipe.canon_profile_id = canon_profile_id
                recipe.revision += 1

            node_id = uuid.uuid5(project_id, f"style-preset:{resolved_key.value}")
            node = session.get(CanvasGraphNode, node_id)
            style_asset = assets_by_key[style_key]
            style_references = [visual_reference_json(style_asset, required=True)]
            if is_v4:
                source_asset = load_canon_assets(session, (CANON_V4_STYLE_SOURCE_KEY,))[
                    CANON_V4_STYLE_SOURCE_KEY
                ]
                style_references.insert(
                    0,
                    visual_reference_json(source_asset, required=False),
                )
            node_data = {
                "title": "原创治愈线条材质画风 v4" if is_v4 else "线条材质画风（历史 v3）",
                "presetKey": resolved_key.value,
                "canonProfileId": canon_profile_id,
                "references": style_references,
                "stylePositive": list(style_positive),
                "styleExcluded": list(style_negative),
                "locked": True,
                "active": True,
            }
            if node is None:
                node = CanvasGraphNode(
                    id=node_id,
                    production_run_id=project_id,
                    node_type=CanvasNodeType.STYLE_PRESET.value,
                    object_type="visual_preset",
                    object_id=style_asset.id,
                    status="approved",
                    data_json=node_data,
                )
                session.add(node)
                session.flush()
            else:
                node.status = "approved"
                if node.data_json != node_data:
                    node.revision += 1
                    node.data_json = node_data

            for historical_style_node in session.scalars(
                select(CanvasGraphNode).where(
                    CanvasGraphNode.production_run_id == project_id,
                    CanvasGraphNode.node_type == CanvasNodeType.STYLE_PRESET.value,
                    CanvasGraphNode.id != node.id,
                )
            ):
                historical_data = dict(historical_style_node.data_json or {})
                if historical_data.get("active") is not False:
                    historical_style_node.data_json = {
                        **historical_data,
                        "active": False,
                        "historical": True,
                    }
                    historical_style_node.revision += 1

            applied_nodes = [node]
            for subject in subjects_by_role.values():
                revision = self._required(
                    session,
                    SubjectRevision,
                    subject.current_revision_id,
                )
                references = self._subject_references(session, revision.id)
                subject_data = {
                    **_subject_json(session, subject, revision, references),
                    "title": revision.name,
                    "canonProfileId": canon_profile_id,
                    "locked": True,
                }
                subject_node = session.get(CanvasGraphNode, subject.id)
                if subject_node is None:
                    subject_node = CanvasGraphNode(
                        id=subject.id,
                        production_run_id=project_id,
                        node_type=CanvasNodeType.SUBJECT.value,
                        object_type="subject",
                        object_id=subject.id,
                        status="ready",
                        data_json=subject_data,
                    )
                    session.add(subject_node)
                    session.flush()
                else:
                    subject_node.status = "ready"
                    if subject_node.data_json != subject_data:
                        subject_node.revision += 1
                        subject_node.data_json = subject_data
                applied_nodes.append(subject_node)

            group = session.scalar(
                select(CanvasGroup)
                .where(
                    CanvasGroup.production_run_id == project_id,
                    CanvasGroup.lifecycle_status == "active",
                    CanvasGroup.group_type == "recipe",
                )
                .order_by(CanvasGroup.created_at.desc())
                .limit(1)
            )
            if group is not None:
                for member in list(
                    session.scalars(
                        select(CanvasGroupMember).where(CanvasGroupMember.group_id == group.id)
                    )
                ):
                    member_node = session.get(CanvasGraphNode, member.canvas_node_id)
                    if (
                        member_node is not None
                        and member_node.node_type == CanvasNodeType.STYLE_PRESET.value
                        and member_node.id != node.id
                    ):
                        session.delete(member)
                max_order = int(
                    session.scalar(
                        select(func.coalesce(func.max(CanvasGroupMember.sort_order), 0)).where(
                            CanvasGroupMember.group_id == group.id
                        )
                    )
                    or 0
                )
                for applied_node in applied_nodes:
                    member = session.scalar(
                        select(CanvasGroupMember).where(
                            CanvasGroupMember.group_id == group.id,
                            CanvasGroupMember.canvas_node_id == applied_node.id,
                        )
                    )
                    if member is None:
                        max_order += 1
                        session.add(
                            CanvasGroupMember(
                                id=uuid.uuid5(group.id, f"member:{applied_node.id}"),
                                group_id=group.id,
                                canvas_node_id=applied_node.id,
                                sort_order=max_order,
                            )
                        )

            style_target_types = {
                CanvasNodeType.CHARACTER_DESIGN.value,
                CanvasNodeType.STORYBOARD_DIRECTOR.value,
                CanvasNodeType.IMAGE_GENERATION.value,
            }
            targets = list(
                session.scalars(
                    select(CanvasGraphNode).where(
                        CanvasGraphNode.production_run_id == project_id,
                        CanvasGraphNode.node_type.in_(
                            style_target_types
                            | {
                                CanvasNodeType.STORY_PLANNER.value,
                            }
                        ),
                    )
                )
            )
            for target in targets:
                if target.node_type in style_target_types:
                    existing_edge = session.scalar(
                        select(CanvasGraphEdge.id).where(
                            CanvasGraphEdge.source_node_id == node.id,
                            CanvasGraphEdge.source_port == CanvasPortType.IMAGE_REFERENCES.value,
                            CanvasGraphEdge.target_node_id == target.id,
                            CanvasGraphEdge.target_port == CanvasPortType.IMAGE_REFERENCES.value,
                        )
                    )
                    if existing_edge is None:
                        session.add(
                            CanvasGraphEdge(
                                id=uuid.uuid5(node.id, f"preset-edge:{target.id}"),
                                production_run_id=project_id,
                                source_node_id=node.id,
                                source_port=CanvasPortType.IMAGE_REFERENCES.value,
                                target_node_id=target.id,
                                target_port=CanvasPortType.IMAGE_REFERENCES.value,
                                relation_type="visual_preset_reference",
                                revision=1,
                            )
                        )
                for role, subject in subjects_by_role.items():
                    if not _preset_subject_targets_node(role, target):
                        continue
                    existing_edge = session.scalar(
                        select(CanvasGraphEdge.id).where(
                            CanvasGraphEdge.source_node_id == subject.id,
                            CanvasGraphEdge.source_port == CanvasPortType.SUBJECTS.value,
                            CanvasGraphEdge.target_node_id == target.id,
                            CanvasGraphEdge.target_port == CanvasPortType.SUBJECTS.value,
                        )
                    )
                    if existing_edge is None:
                        session.add(
                            CanvasGraphEdge(
                                id=uuid.uuid5(subject.id, f"preset-subject-edge:{target.id}"),
                                production_run_id=project_id,
                                source_node_id=subject.id,
                                source_port=CanvasPortType.SUBJECTS.value,
                                target_node_id=target.id,
                                target_port=CanvasPortType.SUBJECTS.value,
                                relation_type="canon_identity_reference",
                                revision=1,
                            )
                        )

            if (
                previous_visual_profile_id is not None
                and previous_visual_profile_id != profile.id
            ) or canon_profile_changed:
                self._invalidate_visual_profile_dependents(session, project_id)

            self._record_event(
                session,
                project_id,
                "canvas_projection_changed",
                {
                    "reason": "visual_preset_applied",
                    "presetKey": resolved_key.value,
                    "canvasNodeId": str(node.id),
                    "canvasNodeIds": [str(item.id) for item in applied_nodes],
                    "visualProfileRevisionId": str(profile.id),
                },
            )
            return {
                "preset": visual_preset_profile_json(session, resolved_key),
                "visualProfile": episode_visual_profile_json(session, profile),
                "canvasNodeId": str(node.id),
                "canvasNodeIds": [str(item.id) for item in applied_nodes],
                "reusedAssetIds": [str(assets_by_key[key].id) for key in required_keys],
            }

    def get_episode_visual_profile(self, project_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            project = self._require_project(session, project_id)
            if project.current_visual_profile_revision_id is None:
                raise WorkflowConflictError("当前项目尚未应用视觉预设")
            profile = self._required(
                session,
                VisualProfileRevision,
                project.current_visual_profile_revision_id,
            )
            return episode_visual_profile_json(session, profile)

    def update_episode_visual_profile(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: VisualProfileDraft,
    ) -> dict[str, Any]:
        document = payload.model_dump(mode="json", by_alias=True)
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            if project.current_visual_profile_revision_id is None:
                raise WorkflowConflictError("当前项目尚未应用视觉预设")
            current = self._required(
                session,
                VisualProfileRevision,
                project.current_visual_profile_revision_id,
                lock=True,
            )
            if current.revision != expected_revision:
                raise WorkflowConflictError(
                    f"本集视觉档案版本冲突：当前 {current.revision}，提交 {expected_revision}"
                )

            current_bindings = {
                (str(item.get("assetId")), str(item.get("purpose")))
                for item in current.reference_bindings_json
            }
            submitted_bindings = {
                (str(item.asset_id), item.purpose.value) for item in payload.reference_bindings
            }
            if submitted_bindings != current_bindings:
                raise WorkflowConflictError(
                    "当前 Canon 必需身份与画风槽位不允许删除、换绑或改变职责"
                )

            profile_document = {
                **document,
                "sourceProfileId": current.source_profile_id,
                "referenceSnapshot": current.reference_snapshot_json,
            }
            profile_hash = hashlib.sha256(
                json.dumps(
                    profile_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            existing = session.scalar(
                select(VisualProfileRevision).where(
                    VisualProfileRevision.production_run_id == project_id,
                    VisualProfileRevision.profile_hash == profile_hash,
                )
            )
            if existing is not None:
                project.current_visual_profile_revision_id = existing.id
                return episode_visual_profile_json(session, existing)

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
            profile = VisualProfileRevision(
                id=uuid.uuid4(),
                production_run_id=project_id,
                revision=revision,
                profile_hash=profile_hash,
                source_profile_id=current.source_profile_id,
                person_identity=payload.person_identity,
                person_hair=payload.person_hair,
                person_body=payload.person_body,
                cat_identity=payload.cat_identity,
                style_positive_json=list(payload.style_positive),
                style_negative_json=list(payload.style_negative),
                reference_bindings_json=document["referenceBindings"],
                reference_snapshot_json=current.reference_snapshot_json,
            )
            session.add(profile)
            session.flush()
            project.current_visual_profile_revision_id = profile.id

            required_keys = canon_reference_keys(current.source_profile_id, "indoor")
            assets_by_key = load_canon_assets(session, required_keys)
            project.default_reference_bindings_json = generation_reference_bindings(
                assets_by_key,
                required_keys,
            )
            self._invalidate_visual_profile_dependents(session, project_id)
            self._record_event(
                session,
                project_id,
                "canvas_projection_changed",
                {
                    "reason": "visual_profile_changed",
                    "visualProfileRevisionId": str(profile.id),
                    "revision": profile.revision,
                },
            )
            return episode_visual_profile_json(session, profile)

    def create_video_filmstrip_run(
        self, asset_id: uuid.UUID, *, frame_count: int
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            asset = self._required(session, Asset, asset_id, lock=True)
            timestamps, filmstrip_key, idempotency_key = _filmstrip_identity(asset, frame_count)
            cached = _filmstrip_frames(session, asset, filmstrip_key)
            existing = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == idempotency_key)
            )
            if len(cached) == frame_count:
                return _filmstrip_json(asset, frame_count, "ready", cached, existing)
            if existing is not None:
                return _filmstrip_json(asset, frame_count, existing.status, cached, existing)
            if asset.media_type != "video":
                raise ValueError("只有视频资产可以生成真实帧带")
            if asset.production_run_id is None:
                raise ValueError("视频资产缺少项目归属，无法创建持久抽帧任务")
            input_snapshot = {
                "assetId": str(asset.id),
                "sourceSha256": asset.sha256,
                "frameCount": frame_count,
                "timestampsMs": list(timestamps),
                "filmstripKey": filmstrip_key,
            }
            step = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=asset.production_run_id,
                kind=StepKind.IMAGE.value,
                status=StepStatus.PENDING.value,
                attempt=1,
                operation_key=f"media:filmstrip:{asset.id}:{frame_count}",
                idempotency_key=idempotency_key,
                provider="local_ffmpeg",
                model="filmstrip-v1",
                input_hash=_json_hash(input_snapshot),
                request_hash=_json_hash(input_snapshot),
                input_snapshot_json=input_snapshot,
            )
            session.add(step)
            self._record_event(
                session,
                asset.production_run_id,
                "video_filmstrip_queued",
                {
                    "assetId": str(asset.id),
                    "stepId": str(step.id),
                    "frameCount": frame_count,
                    "timestampsMs": list(timestamps),
                },
            )
            return _filmstrip_json(asset, frame_count, step.status, cached, step)

    def get_video_filmstrip(self, asset_id: uuid.UUID, *, frame_count: int) -> dict[str, Any]:
        with self._sessions() as session:
            asset = self._required(session, Asset, asset_id)
            _timestamps, filmstrip_key, idempotency_key = _filmstrip_identity(asset, frame_count)
            step = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == idempotency_key)
            )
            frames = _filmstrip_frames(session, asset, filmstrip_key)
            status = (
                "ready"
                if len(frames) == frame_count
                else ("not_requested" if step is None else step.status)
            )
            return _filmstrip_json(asset, frame_count, status, frames, step)

    def filmstrip_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            snapshot = step.input_snapshot_json
            if not step.operation_key.startswith("media:filmstrip:"):
                raise ValueError("workflow step is not a filmstrip extraction")
            asset = self._required(session, Asset, uuid.UUID(str(snapshot["assetId"])))
            return {
                "source": _stored_asset(asset, self._asset_root),
                "timestampsMs": tuple(int(value) for value in snapshot["timestampsMs"]),
                "filmstripKey": str(snapshot["filmstripKey"]),
            }

    def complete_filmstrip(
        self,
        step_id: uuid.UUID,
        *,
        frames: tuple[LandedAsset, ...],
        timestamps_ms: tuple[int, ...],
    ) -> tuple[str, ...]:
        if len(frames) != len(timestamps_ms):
            raise ValueError("抽帧文件与时间点数量不一致")
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            snapshot = step.input_snapshot_json
            source = self._required(session, Asset, uuid.UUID(str(snapshot["assetId"])))
            filmstrip_key = str(snapshot["filmstripKey"])
            existing = {
                int(row.metadata_json["timestampMs"]): row
                for row in _filmstrip_frames(session, source, filmstrip_key)
            }
            asset_ids: list[str] = []
            for timestamp_ms, landed in zip(timestamps_ms, frames, strict=True):
                row = existing.get(timestamp_ms)
                if row is None:
                    row = Asset(
                        id=uuid.uuid4(),
                        production_run_id=source.production_run_id,
                        producing_step_id=step.id,
                        canvas_node_id=source.canvas_node_id,
                        role="filmstrip_frame",
                        semantic_key=f"filmstrip:{source.id}:{timestamp_ms}",
                        scope="project",
                        status="ready",
                        media_type="image",
                        storage_key=_asset_storage_key(landed.path, self._asset_root),
                        sha256=landed.sha256,
                        byte_size=landed.byte_size,
                        metadata_json={
                            "sourceAssetId": str(source.id),
                            "sourceSha256": source.sha256,
                            "filmstripKey": filmstrip_key,
                            "timestampMs": timestamp_ms,
                            "frameCount": len(timestamps_ms),
                        },
                    )
                    session.add(row)
                asset_ids.append(str(row.id))
            if source.production_run_id is not None:
                self._record_event(
                    session,
                    source.production_run_id,
                    "video_filmstrip_ready",
                    {
                        "assetId": str(source.id),
                        "stepId": str(step.id),
                        "frameAssetIds": asset_ids,
                    },
                )
            return tuple(asset_ids)

    def save_node_generation_config(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            node = self._required(session, CanvasGraphNode, node_id, lock=True)
            if node.revision != expected_revision:
                raise WorkflowConflictError(
                    f"生成节点版本冲突：当前 {node.revision}，提交 {expected_revision}"
                )
            preview = self._compile_node_generation_input(session, node, payload)
            document = payload.model_dump(mode="json", by_alias=True)
            document["actualReferences"] = preview["references"]
            document["inputHash"] = preview["inputHash"]
            input_hash = str(preview["inputHash"])
            revision = node.revision + 1
            row = NodeGenerationConfig(
                id=uuid.uuid4(),
                canvas_node_id=node.id,
                revision=revision,
                provider=payload.provider,
                model=payload.model,
                mode=payload.mode,
                config_json=document,
                actual_reference_bindings_json=list(preview["references"]),
                input_hash=input_hash,
            )
            session.add(row)
            node.revision = revision
            node.data_json = {
                **node.data_json,
                "generationConfigId": str(row.id),
                "generationConfig": document,
                "actualReferences": row.actual_reference_bindings_json,
            }
            event = self._record_event(
                session,
                node.production_run_id,
                "node_generation_config_saved",
                {
                    "nodeId": str(node.id),
                    "configId": str(row.id),
                    "revision": revision,
                    "inputHash": input_hash,
                },
            )
            return {
                "id": str(row.id),
                "canvasNodeId": str(node.id),
                "revision": revision,
                "inputHash": input_hash,
                "confirmedEventId": str(event.id),
                **document,
            }

    def preview_generation_input(
        self,
        node_id: uuid.UUID,
        payload: NodeGenerationConfigDraft,
    ) -> dict[str, Any]:
        with self._sessions() as session:
            node = self._required(session, CanvasGraphNode, node_id)
            return self._compile_node_generation_input(session, node, payload)

    def _compile_node_generation_input(
        self,
        session: Session,
        node: CanvasGraphNode,
        payload: NodeGenerationConfigDraft,
    ) -> dict[str, Any]:
        media_kind = (
            "video"
            if node.node_type == CanvasNodeType.VIDEO_GENERATION.value
            else "audio"
            if node.node_type == CanvasNodeType.AUDIO_GENERATION.value
            else "image"
        )
        capability = self._resolve_provider_capability(
            session,
            provider=payload.provider,
            model=payload.model,
            media_kind=media_kind,
        )
        blockers: list[str] = []
        warnings: list[str] = []
        if capability is None:
            blockers.append("当前 Provider、模型和媒体类型没有启用的能力档案")
            capabilities: dict[str, Any] = {}
            capability_revision = "unavailable"
        else:
            capabilities = dict(capability["capabilities"])
            capability_revision = str(
                capabilities.get("capabilityRevision")
                or capability["updatedAt"]
                or capability["id"]
            )
            supported_modes = {
                str(item) for item in capabilities.get("modes", [])
            }
            if supported_modes and payload.mode not in supported_modes:
                blockers.append(
                    f"当前 {payload.model} 不支持 {payload.mode} 生成模式"
                )

        drafts = [item.model_dump(mode="json", by_alias=True) for item in payload.actual_references]
        requested_ids = [uuid.UUID(str(item["assetId"])) for item in drafts]
        assets = {
            asset.id: asset
            for asset in session.scalars(select(Asset).where(Asset.id.in_(requested_ids)))
        }
        maximum = int(
            capabilities.get("maxReferenceImages") or (1 if media_kind == "video" else 14)
        )
        if payload.mode == "image_to_video":
            maximum = min(maximum, 1)
        elif payload.mode == "first_last_frame":
            maximum = min(maximum, 2)
        compiled: list[dict[str, Any]] = []
        seen_ids: set[uuid.UUID] = set()
        seen_hashes: set[str] = set()
        for draft in drafts:
            asset_id = uuid.UUID(str(draft["assetId"]))
            asset = assets.get(asset_id)
            if asset is None:
                blockers.append(f"引用素材 {asset_id} 不存在")
                continue
            if asset.production_run_id not in {None, node.production_run_id}:
                blockers.append(f"引用素材 {asset_id} 不属于当前项目")
                continue
            if asset.media_type != "image":
                blockers.append(f"引用素材 {asset_id} 不是图片")
                continue
            if asset.status not in {"approved", "ready"}:
                blockers.append(f"引用素材 {asset_id} 尚未批准")
                continue
            path = _resolve_asset_path(asset.storage_key, self._asset_root)
            if not path.is_file():
                blockers.append(f"引用素材 {asset_id} 文件不可读取")
                continue
            if asset.id in seen_ids or asset.sha256 in seen_hashes:
                warnings.append(f"引用素材 {asset_id} 与前序素材重复，已按首次出现位置去重")
                continue
            seen_ids.add(asset.id)
            seen_hashes.add(asset.sha256)
            ordinal = len(compiled) + 1
            provider_included = ordinal <= maximum
            omission_reason = None
            if not provider_included:
                omission_reason = f"当前 {payload.model} 最多接受 {maximum} 张参考图"
                if bool(draft.get("locked")):
                    blockers.append(f"锁定引用 @{ordinal} 超出模型参考图上限，不能静默省略")
            provider_slot = (
                "first_frame"
                if provider_included
                and payload.mode in {"image_to_video", "first_last_frame"}
                and ordinal == 1
                else "last_frame"
                if provider_included
                and payload.mode == "first_last_frame"
                and ordinal == 2
                else f"reference_image_{ordinal}"
                if provider_included
                else None
            )
            metadata = dict(asset.metadata_json or {})
            compiled.append(
                CompiledProviderReference(
                    assetId=asset.id,
                    sourceNodeId=draft.get("sourceNodeId"),
                    sourceType=str(draft.get("sourceType") or "canvas"),
                    subjectRevisionId=draft.get("subjectRevisionId"),
                    semanticRole=str(draft.get("semanticRole") or "reference"),
                    purpose=str(draft.get("purpose") or "reference"),
                    instruction=str(draft.get("instruction") or ""),
                    ordinal=ordinal,
                    locked=bool(draft.get("locked", False)),
                    sha256=asset.sha256,
                    providerIncluded=provider_included,
                    providerSlot=provider_slot,
                    omissionReason=omission_reason,
                    origin=str(draft.get("origin") or "canvas_reference"),
                    title=str(
                        metadata.get("displayName")
                        or metadata.get("title")
                        or asset.semantic_key
                        or asset.role
                    ),
                    contentUrl=f"/api/v1/assets/{asset.id}/content",
                    evidenceLevel="frozen",
                ).model_dump(mode="json", by_alias=True)
            )
        input_document = {
            "provider": payload.provider,
            "model": payload.model,
            "mode": payload.mode,
            "capabilityRevision": capability_revision,
            "prompt": payload.draft_prompt,
            "aspectRatio": payload.aspect_ratio,
            "resolution": payload.resolution,
            "durationSeconds": payload.duration_seconds,
            "audioEnabled": payload.audio_enabled,
            "candidateCount": payload.candidate_count,
            "cameraMotion": payload.camera_motion,
            "referenceAnnotations": [
                item.model_dump(mode="json", by_alias=True)
                for item in payload.reference_annotations
            ],
            "references": compiled,
        }
        included_reference_count = sum(
            1 for item in compiled if item.get("providerIncluded") is True
        )
        if payload.mode == "image_to_video" and included_reference_count != 1:
            blockers.append("图生视频必须且只能选择一张已批准开场图")
        if payload.mode == "first_last_frame" and included_reference_count != 2:
            blockers.append("首尾帧模式必须按顺序选择一张开场图和一张结尾图")
        input_image_cost = capabilities.get("inputImageCostMicros")
        output_image_cost = capabilities.get("outputImageCostMicros")
        if media_kind == "image" and input_image_cost is not None and output_image_cost is not None:
            estimated_cost_micros = payload.candidate_count * (
                int(output_image_cost) + included_reference_count * int(input_image_cost)
            )
        else:
            raw_estimate = capabilities.get("estimatedCostMicros")
            estimated_cost_micros = None if raw_estimate is None else int(raw_estimate)
        preview = GenerationInputPreview(
            provider=payload.provider,
            model=payload.model,
            mode=payload.mode,
            capabilityRevision=capability_revision,
            prompt=payload.draft_prompt,
            references=compiled,
            blockers=list(dict.fromkeys(blockers)),
            warnings=list(dict.fromkeys(warnings)),
            estimatedCostMicros=estimated_cost_micros,
            inputHash=generation_input_hash(input_document),
        )
        return preview.model_dump(mode="json", by_alias=True)

    def _resolve_provider_capability(
        self,
        session: Session,
        *,
        provider: str,
        model: str,
        media_kind: str,
    ) -> dict[str, Any] | None:
        row = session.scalar(
            select(ProviderCapability).where(
                ProviderCapability.provider == provider,
                ProviderCapability.model == model,
                ProviderCapability.media_kind == media_kind,
                ProviderCapability.active.is_(True),
            )
        )
        if row is not None:
            return {
                "id": str(row.id),
                "provider": row.provider,
                "model": row.model,
                "mediaKind": row.media_kind,
                "capabilities": dict(row.capabilities_json or {}),
                "active": True,
                "updatedAt": (None if row.updated_at is None else row.updated_at.isoformat()),
            }
        if (
            media_kind == "image"
            and provider == SEEDREAM_5_0_CAPABILITY.provider
            and model == SEEDREAM_5_0_CAPABILITY.model
        ):
            return {
                "id": f"builtin:{SEEDREAM_5_0_CAPABILITY.capability_revision}",
                "provider": provider,
                "model": model,
                "mediaKind": media_kind,
                "capabilities": SEEDREAM_5_0_CAPABILITY.provider_capability_document(),
                "active": True,
                "updatedAt": None,
            }
        return None

    def list_provider_capabilities(self, *, media_kind: str | None = None) -> list[dict[str, Any]]:
        with self._sessions() as session:
            query = select(ProviderCapability).where(ProviderCapability.active.is_(True))
            if media_kind is not None:
                query = query.where(ProviderCapability.media_kind == media_kind)
            rows = session.scalars(
                query.order_by(
                    ProviderCapability.provider,
                    ProviderCapability.model,
                )
            )
            documents: list[dict[str, Any]] = []
            known: set[tuple[str, str, str]] = set()
            for row in rows:
                known.add((row.provider, row.model, row.media_kind))
                capabilities = dict(row.capabilities_json)
                if row.media_kind == "video":
                    capabilities.setdefault(
                        "cameraMotions",
                        [dict(preset) for preset in _CAMERA_MOTION_PRESETS],
                    )
                    capabilities.setdefault(
                        "mediaActions",
                        [dict(action) for action in _VIDEO_ASSET_ACTIONS],
                    )
                documents.append(
                    {
                        "id": str(row.id),
                        "provider": row.provider,
                        "model": row.model,
                        "mediaKind": row.media_kind,
                        "capabilities": capabilities,
                        "active": row.active,
                        "updatedAt": (
                            None if row.updated_at is None else row.updated_at.isoformat()
                        ),
                    }
                )
            builtin_key = (
                SEEDREAM_5_0_CAPABILITY.provider,
                SEEDREAM_5_0_CAPABILITY.model,
                "image",
            )
            if media_kind in {None, "image"} and builtin_key not in known:
                documents.append(
                    {
                        "id": f"builtin:{SEEDREAM_5_0_CAPABILITY.capability_revision}",
                        "provider": builtin_key[0],
                        "model": builtin_key[1],
                        "mediaKind": builtin_key[2],
                        "capabilities": (SEEDREAM_5_0_CAPABILITY.provider_capability_document()),
                        "active": True,
                        "updatedAt": None,
                    }
                )
            return documents

    def list_subjects(self, project_id: uuid.UUID) -> tuple[StoredCanvasSubject, ...]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            rows = list(
                session.execute(
                    select(Subject)
                    .where(Subject.production_run_id == project_id, Subject.status != "archived")
                    .order_by(Subject.created_at)
                ).scalars()
            )
            result: list[StoredCanvasSubject] = []
            for subject in rows:
                if subject.current_revision_id is None:
                    continue
                revision = self._required(
                    session,
                    SubjectRevision,
                    subject.current_revision_id,
                )
                result.append(
                    StoredCanvasSubject(
                        id=subject.id,
                        revision_id=revision.id,
                        revision=revision.revision,
                        draft=_subject_draft(
                            subject,
                            revision,
                            self._subject_references(session, revision.id),
                        ),
                        status=subject.status,
                    )
                )
            return _preferred_stored_subjects(result)

    def begin_generation_attempt(self, **values: object) -> tuple[dict[str, Any], bool]:
        attempt_id = uuid.uuid4()
        with self._sessions.begin() as session:
            self._require_project(session, uuid.UUID(str(values["project_id"])))
            created_id = session.execute(
                insert(GenerationAttempt)
                .values(
                    id=attempt_id,
                    production_run_id=values["project_id"],
                    business_object_type=values["business_object_type"],
                    business_object_id=values["business_object_id"],
                    idempotency_key=values["idempotency_key"],
                    provider=values["provider"],
                    model=values["model"],
                    status="pending",
                    request_json=values["request"],
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(GenerationAttempt.id)
            ).scalar_one_or_none()
            row = session.scalar(
                select(GenerationAttempt).where(
                    GenerationAttempt.idempotency_key == values["idempotency_key"]
                )
            )
            if row is None:
                raise RuntimeError("generation attempt idempotency insert returned no row")
            return _attempt_json(row), created_id is not None

    def finish_generation_attempt(self, attempt_id: str, **values: object) -> None:
        with self._sessions.begin() as session:
            row = self._required(session, GenerationAttempt, uuid.UUID(attempt_id), lock=True)
            next_status = str(values["status"])
            if row.status == "succeeded":
                if next_status == "succeeded":
                    return
                raise WorkflowConflictError("生成尝试已处于成功终态，不允许逆转")
            row.status = next_status
            if "response" in values:
                row.response_json = values["response"]  # type: ignore[assignment]
            if "error" in values:
                row.error_json = values["error"]  # type: ignore[assignment]

    def begin_prompt_run(
        self,
        *,
        project_id: uuid.UUID,
        draft: PromptRunDraft,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        input_hash = _json_hash(draft.input_snapshot)
        prompt_hash = hashlib.sha256(draft.final_prompt.encode("utf-8")).hexdigest()
        operation_key = f"director:{draft.purpose}"
        idempotency_key = hashlib.sha256(
            f"{project_id}:{operation_key}:{input_hash}:{prompt_hash}".encode()
        ).hexdigest()
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            attempt = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(WorkflowStep.attempt), 0)).where(
                            WorkflowStep.production_run_id == project_id,
                            WorkflowStep.operation_key == operation_key,
                        )
                    )
                    or 0
                )
                + 1
            )
            created_step_id = session.execute(
                insert(WorkflowStep)
                .values(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    kind=StepKind.DIRECTOR.value,
                    status=StepStatus.PENDING.value,
                    attempt=attempt,
                    operation_key=operation_key,
                    idempotency_key=idempotency_key,
                    provider=draft.provider,
                    model=draft.model,
                    input_hash=input_hash,
                    request_hash=_json_hash(draft.provider_request_snapshot),
                    input_snapshot_json=draft.input_snapshot,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(WorkflowStep.id)
            ).scalar_one_or_none()
            step = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == idempotency_key)
            )
            if step is None:
                raise RuntimeError("prompt workflow insert returned no row")
            prompt = session.scalar(
                select(PromptRecord).where(
                    PromptRecord.step_id == step.id,
                    PromptRecord.sha256 == prompt_hash,
                )
            )
            if prompt is None:
                prompt = PromptRecord(
                    id=uuid.uuid4(),
                    step_id=step.id,
                    purpose=PromptPurpose.DIRECTOR.value,
                    model=draft.model,
                    prompt_text=draft.final_prompt,
                    sha256=prompt_hash,
                    call_purpose=draft.purpose,
                    node_id=draft.node_id,
                    business_object_type=draft.business_object_type,
                    business_object_id=draft.business_object_id,
                    parent_prompt_id=draft.parent_run_id,
                    template_name=draft.template_name,
                    template_version=draft.template_version,
                    system_prompt=draft.system_prompt,
                    user_prompt=draft.user_prompt,
                    final_prompt=draft.final_prompt,
                    provider_request_json=draft.provider_request_snapshot,
                    provider_internal_transform=draft.provider_internal_transform,
                    input_snapshot_json=draft.input_snapshot,
                    parameters_json=draft.parameters,
                    status="pending",
                    input_hash=input_hash,
                )
                session.add(prompt)
                session.flush()
            if created_step_id is None and prompt.status == "succeeded":
                raise WorkflowConflictError("相同 Prompt 调用已经成功，不允许重复扣费提交")
            return prompt.id, step.id

    def complete_prompt_run(self, prompt_id: uuid.UUID, **values: object) -> None:
        with self._sessions.begin() as session:
            prompt = self._required(session, PromptRecord, prompt_id, lock=True)
            step = self._required(session, WorkflowStep, prompt.step_id, lock=True)
            status = str(values["status"])
            prompt.status = status
            prompt.completed_at = datetime.now(UTC)
            if "raw_response" in values:
                raw_response = values["raw_response"]
                if isinstance(raw_response, dict):
                    raw = dict(raw_response)
                elif isinstance(raw_response, str):
                    raw = {"text": raw_response}
                else:
                    raise TypeError("raw_response must be a dict or string")
                response_id = values.get("provider_response_id")
                if response_id is not None:
                    raw["_providerResponseId"] = response_id
                prompt.raw_response_json = raw
            if "structured_response" in values:
                prompt.structured_response_json = values["structured_response"]  # type: ignore[assignment]
            if "output_hash" in values:
                prompt.output_hash = str(values["output_hash"])
            if "error" in values:
                prompt.error_json = values["error"]  # type: ignore[assignment]
            step.status = (
                StepStatus.SUCCEEDED.value if status == "succeeded" else StepStatus.FAILED.value
            )
            step.completed_at = prompt.completed_at
            step.error_json = prompt.error_json

    def get_succeeded_story_candidate_batch(self, **values: object) -> dict[str, object] | None:
        project_id = uuid.UUID(str(values["project_id"]))
        business_object_id = uuid.UUID(str(values["business_object_id"]))
        with self._sessions() as session:
            prompt = session.scalar(
                select(PromptRecord)
                .join(WorkflowStep, WorkflowStep.id == PromptRecord.step_id)
                .where(
                    WorkflowStep.production_run_id == project_id,
                    PromptRecord.business_object_type == values["business_object_type"],
                    PromptRecord.business_object_id == business_object_id,
                    PromptRecord.call_purpose == values["call_purpose"],
                    PromptRecord.input_hash == values["input_hash"],
                    PromptRecord.status == "succeeded",
                )
                .order_by(PromptRecord.completed_at.desc(), PromptRecord.created_at.desc())
                .limit(1)
            )
            if prompt is None:
                return None
            structured = prompt.structured_response_json
            if not isinstance(structured, dict):
                raise WorkflowConflictError("成功故事 Prompt 缺少结构化恢复结果")
            try:
                batch = CreativeStoryCandidateBatch.model_validate(structured.get("batch"))
                raw_diagnostics = structured.get("diagnostics", [])
                if not isinstance(raw_diagnostics, list):
                    raise ValueError("diagnostics must be a list")
                diagnostics = [CanvasDiagnostic.model_validate(item) for item in raw_diagnostics]
            except (TypeError, ValueError) as exc:
                raise WorkflowConflictError("成功故事 Prompt 的恢复结果无效") from exc
            return {
                "promptId": prompt.id,
                "batch": batch,
                "diagnostics": tuple(diagnostics),
            }

    def get_story_candidates(
        self,
        *,
        project_id: uuid.UUID,
        candidate_ids: Sequence[uuid.UUID],
    ) -> tuple[dict[str, Any], ...]:
        ordered_ids = tuple(candidate_ids)
        if not ordered_ids:
            return ()
        if len(set(ordered_ids)) != len(ordered_ids):
            raise WorkflowConflictError("成功故事生成尝试包含重复的候选版本引用")
        with self._sessions() as session:
            stories = list(
                session.scalars(
                    select(StoryRevisionRecord).where(
                        StoryRevisionRecord.production_run_id == project_id,
                        StoryRevisionRecord.id.in_(ordered_ids),
                    )
                )
            )
            stories_by_id = {story.id: story for story in stories}
            if any(candidate_id not in stories_by_id for candidate_id in ordered_ids):
                raise WorkflowConflictError(
                    "成功故事生成尝试引用的候选版本不存在或不属于当前项目"
                )
            scores_by_story_id = {
                score.story_revision_id: score
                for score in session.scalars(
                    select(StoryScore).where(StoryScore.story_revision_id.in_(ordered_ids))
                )
            }
            prompt_ids = {
                story.candidate_prompt_id
                for story in stories
                if story.candidate_prompt_id is not None
            }
            prompts_by_id = {
                prompt.id: prompt
                for prompt in (
                    session.scalars(select(PromptRecord).where(PromptRecord.id.in_(prompt_ids)))
                    if prompt_ids
                    else ()
                )
            }
            return tuple(
                _story_json(
                    stories_by_id[candidate_id],
                    scores_by_story_id.get(candidate_id),
                    prompts_by_id.get(stories_by_id[candidate_id].candidate_prompt_id),
                )
                for candidate_id in ordered_ids
            )

    def save_story_candidate(self, **values: object) -> dict[str, Any]:
        project_id = uuid.UUID(str(values["project_id"]))
        candidate = values["candidate"]
        scorecard = values.get("scorecard")
        if isinstance(candidate, CreativeStoryCandidate):
            if scorecard is not None:
                raise TypeError("creative story candidates do not accept a legacy scorecard")
            title = candidate.title
            logline = candidate.summary or candidate.title
            synopsis = candidate.body
            scene_plan: list[dict[str, Any]] = []
            critic_prompt_id = None
        elif isinstance(candidate, StoryCandidateOutput):
            if not isinstance(scorecard, StoryScorecard):
                raise TypeError("legacy story candidates require a Canvas V2 scorecard")
            title = candidate.title
            logline = candidate.logline
            synopsis = candidate.synopsis
            scene_plan = [item.model_dump(mode="json", by_alias=True) for item in candidate.scenes]
            critic_prompt_id = values.get("critic_prompt_id")
        else:
            raise TypeError("candidate must use a supported creative or legacy story contract")
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            revision = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(StoryRevisionRecord.revision), 0)).where(
                            StoryRevisionRecord.production_run_id == project_id
                        )
                    )
                    or 0
                )
                + 1
            )
            row = StoryRevisionRecord(
                id=uuid.uuid4(),
                production_run_id=project_id,
                brief_id=values["brief_id"],
                source_event_candidate_id=values.get("source_event_candidate_id"),
                revision=revision,
                strategy=(
                    values["strategy"].value
                    if isinstance(values["strategy"], StoryStrategy)
                    else str(values["strategy"])
                ),
                status=StoryRevisionStatus.CANDIDATE.value,
                title=title,
                logline=logline,
                synopsis=synopsis,
                subject_ids_json=[str(item) for item in values["subject_ids"]],
                scene_plan_json=scene_plan,
                candidate_prompt_id=values["candidate_prompt_id"],
                critic_prompt_id=critic_prompt_id,
            )
            session.add(row)
            session.flush()
            score: StoryScore | None = None
            if isinstance(scorecard, StoryScorecard):
                score = StoryScore(
                    id=uuid.uuid4(),
                    story_revision_id=row.id,
                    opening_hook=scorecard.opening_hook,
                    causal_completeness=scorecard.causal_completeness,
                    subject_necessity=scorecard.subject_necessity,
                    emotional_arc=scorecard.emotional_arc,
                    visualizability=scorecard.visualizability,
                    duration_fit=scorecard.duration_fit,
                    continuity_risk=scorecard.continuity_risk,
                    safety=scorecard.safety,
                    rationale=scorecard.rationale,
                    warnings_json=list(scorecard.warnings),
                )
                session.add(score)
                session.flush()
            prompt = (
                None
                if row.candidate_prompt_id is None
                else session.scalar(
                    select(PromptRecord).where(PromptRecord.id == row.candidate_prompt_id)
                )
            )
            return _story_json(row, score, prompt)

    def save_story_candidate_batch(self, **values: object) -> tuple[dict[str, Any], ...]:
        project_id = uuid.UUID(str(values["project_id"]))
        candidates = tuple(values["candidates"])  # type: ignore[arg-type]
        if not candidates or len(candidates) > 5:
            raise ValueError("creative story candidate batch must contain 1 to 5 candidates")
        if not all(isinstance(candidate, CreativeStoryCandidate) for candidate in candidates):
            raise TypeError("candidate batch must use CreativeStoryCandidate contracts")
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            candidate_prompt_id = uuid.UUID(str(values["candidate_prompt_id"]))
            existing = list(
                session.scalars(
                    select(StoryRevisionRecord)
                    .where(
                        StoryRevisionRecord.production_run_id == project_id,
                        StoryRevisionRecord.candidate_prompt_id == candidate_prompt_id,
                    )
                    .order_by(StoryRevisionRecord.revision)
                    .with_for_update()
                )
            )
            if existing:
                expected_brief_id = uuid.UUID(str(values["brief_id"]))
                expected_strategy = (
                    values["strategy"].value
                    if isinstance(values["strategy"], StoryStrategy)
                    else str(values["strategy"])
                )
                expected_subject_ids = [str(item) for item in values["subject_ids"]]
                content_matches = len(existing) == len(candidates) and all(
                    row.brief_id == expected_brief_id
                    and row.strategy == expected_strategy
                    and row.subject_ids_json == expected_subject_ids
                    and row.parent_revision_id is None
                    and row.source_event_candidate_id is None
                    and row.status == StoryRevisionStatus.CANDIDATE.value
                    and row.title == candidate.title
                    and row.logline == (candidate.summary or candidate.title)
                    and row.synopsis == candidate.body
                    and row.scene_plan_json == []
                    and row.episode_rules_json == {}
                    and row.critic_prompt_id is None
                    and row.approved_at is None
                    for row, candidate in zip(existing, candidates, strict=True)
                )
                if not content_matches:
                    raise WorkflowConflictError("故事候选批次已部分物化或与成功 Prompt 内容不一致")
                prompt = session.scalar(
                    select(PromptRecord).where(PromptRecord.id == candidate_prompt_id)
                )
                return tuple(_story_json(row, None, prompt) for row in existing)
            latest_revision = int(
                session.scalar(
                    select(func.coalesce(func.max(StoryRevisionRecord.revision), 0)).where(
                        StoryRevisionRecord.production_run_id == project_id
                    )
                )
                or 0
            )
            rows: list[StoryRevisionRecord] = []
            for offset, candidate in enumerate(candidates, 1):
                row = StoryRevisionRecord(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    brief_id=values["brief_id"],
                    revision=latest_revision + offset,
                    strategy=(
                        values["strategy"].value
                        if isinstance(values["strategy"], StoryStrategy)
                        else str(values["strategy"])
                    ),
                    status=StoryRevisionStatus.CANDIDATE.value,
                    title=candidate.title,
                    logline=candidate.summary or candidate.title,
                    synopsis=candidate.body,
                    subject_ids_json=[str(item) for item in values["subject_ids"]],
                    scene_plan_json=[],
                    candidate_prompt_id=candidate_prompt_id,
                    critic_prompt_id=None,
                )
                session.add(row)
                rows.append(row)
            session.flush()
            prompt = session.scalar(
                select(PromptRecord).where(
                    PromptRecord.id == uuid.UUID(str(values["candidate_prompt_id"]))
                )
            )
            return tuple(_story_json(row, None, prompt) for row in rows)

    def save_story_revision_edit(self, **values: object) -> dict[str, Any]:
        revision_id = uuid.UUID(str(values["revision_id"]))
        expected_revision = int(values["expected_revision"])
        idempotency_key = str(values["idempotency_key"])
        title = str(values["title"]).strip()
        body = str(values["body"]).strip()
        summary_value = values.get("summary")
        summary = None if summary_value is None else str(summary_value).strip()
        if not title or len(title) > 200:
            raise ValueError("剧情标题必须为 1 至 200 个字符")
        if not body or len(body) > 200_000:
            raise ValueError("剧情正文必须为 1 至 200000 个字符")
        if summary is not None and (not summary or len(summary) > 4_000):
            raise ValueError("剧情摘要必须为 1 至 4000 个字符")
        if not 8 <= len(idempotency_key) <= 96:
            raise ValueError("剧情编辑幂等键必须为 8 至 96 个字符")
        edit_id = uuid.uuid5(revision_id, f"story-document-edit:{idempotency_key}")
        revision_conflict: IntegrityError | None = None
        for _attempt in range(3):
            try:
                with self._sessions.begin() as session:
                    source = self._required(session, StoryRevisionRecord, revision_id)
                    self._required(session, ProductionRun, source.production_run_id, lock=True)
                    source = self._required(
                        session,
                        StoryRevisionRecord,
                        revision_id,
                        lock=True,
                    )
                    if source.revision != expected_revision:
                        raise WorkflowConflictError("剧情版本冲突，请刷新来源版本后重试")
                    existing = session.scalar(
                        select(StoryRevisionRecord).where(StoryRevisionRecord.id == edit_id)
                    )
                    if existing is not None:
                        content_matches = (
                            existing.parent_revision_id == source.id
                            and existing.title == title
                            and existing.synopsis == body
                            and existing.logline == (summary or title)
                        )
                        if not content_matches:
                            raise WorkflowConflictError("剧情编辑幂等键已用于不同内容")
                        return _story_json(existing, None)
                    latest_revision = int(
                        session.scalar(
                            select(func.coalesce(func.max(StoryRevisionRecord.revision), 0)).where(
                                StoryRevisionRecord.production_run_id == source.production_run_id
                            )
                        )
                        or 0
                    )
                    edited = StoryRevisionRecord(
                        id=edit_id,
                        production_run_id=source.production_run_id,
                        brief_id=source.brief_id,
                        parent_revision_id=source.id,
                        source_event_candidate_id=source.source_event_candidate_id,
                        revision=latest_revision + 1,
                        strategy=source.strategy,
                        status=StoryRevisionStatus.CANDIDATE.value,
                        title=title,
                        logline=summary or title,
                        synopsis=body,
                        subject_ids_json=list(source.subject_ids_json),
                        scene_plan_json=[],
                        episode_rules_json=dict(source.episode_rules_json or {}),
                        candidate_prompt_id=None,
                        critic_prompt_id=None,
                    )
                    session.add(edited)
                    session.flush()
                    return _story_json(edited, None)
            except IntegrityError as error:
                revision_conflict = error
        raise WorkflowConflictError("剧情新版本分配冲突，请重试") from revision_conflict

    def save_story_event_candidate(self, **values: object) -> dict[str, Any]:
        project_id = uuid.UUID(str(values["project_id"]))
        recipe_instance_id = uuid.UUID(str(values["recipe_instance_id"]))
        candidate = values["candidate"]
        scorecard = values["scorecard"]
        if not isinstance(candidate, StoryEventCandidateOutput) or not isinstance(
            scorecard, StoryScorecard
        ):
            raise TypeError("event candidate and scorecard must use Canvas V2 contracts")
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            instance = self._required(
                session, ProductionRecipeInstance, recipe_instance_id, lock=True
            )
            if instance.production_run_id != project_id:
                raise WorkflowConflictError("事件方案与组合包不属于同一个项目")
            batch_id = uuid.UUID(str(values["batch_id"]))
            candidate_index = int(values["candidate_index"])
            existing = session.scalar(
                select(StoryEventCandidateRecord).where(
                    StoryEventCandidateRecord.production_recipe_instance_id == recipe_instance_id,
                    StoryEventCandidateRecord.batch_id == batch_id,
                    StoryEventCandidateRecord.candidate_index == candidate_index,
                )
            )
            if existing is not None:
                return _story_event_json(existing)
            row = StoryEventCandidateRecord(
                id=uuid.uuid4(),
                production_run_id=project_id,
                production_recipe_instance_id=recipe_instance_id,
                story_brief_id=uuid.UUID(str(values["brief_id"])),
                batch_id=batch_id,
                candidate_index=candidate_index,
                revision=1,
                strategy=(
                    values["strategy"].value
                    if isinstance(values["strategy"], StoryStrategy)
                    else str(values["strategy"])
                ),
                status=StoryEventCandidateStatus.CANDIDATE.value,
                title=candidate.title,
                premise=candidate.premise,
                child_action=candidate.child_action,
                cat_participation=candidate.cat_participation,
                small_change=candidate.small_change,
                warm_ending=candidate.warm_ending,
                suggested_scenes_json=[
                    scene.model_dump(mode="json", by_alias=True)
                    for scene in candidate.suggested_scenes
                ],
                duration_fit_summary=candidate.duration_fit_summary,
                requires_scene_change=candidate.requires_scene_change,
                cat_behavior_mode_suggestion=candidate.cat_behavior_mode_suggestion,
                score_json={
                    **scorecard.model_dump(mode="json", by_alias=True),
                    "average": scorecard.average,
                },
                generation_prompt_id=uuid.UUID(str(values["generation_prompt_id"])),
            )
            session.add(row)
            session.flush()
            return _story_event_json(row)

    def get_selected_story_event(self, recipe_instance_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            instance = self._required(session, ProductionRecipeInstance, recipe_instance_id)
            latest_candidate = session.scalar(
                select(StoryEventCandidateRecord)
                .where(
                    StoryEventCandidateRecord.production_recipe_instance_id == recipe_instance_id
                )
                .order_by(StoryEventCandidateRecord.created_at.desc())
                .limit(1)
            )
            if latest_candidate is None:
                raise WorkflowConflictError("事件方案尚未生成")
            row = session.scalar(
                select(StoryEventCandidateRecord)
                .where(
                    StoryEventCandidateRecord.production_recipe_instance_id == recipe_instance_id,
                    StoryEventCandidateRecord.batch_id == latest_candidate.batch_id,
                    StoryEventCandidateRecord.status == StoryEventCandidateStatus.SELECTED.value,
                )
                .order_by(StoryEventCandidateRecord.selected_at.desc())
                .limit(1)
            )
            if row is None:
                raise WorkflowConflictError("请先人工选择一个事件方案，再扩写剧情脚本")
            if row.production_run_id != instance.production_run_id:
                raise WorkflowConflictError("所选事件方案不属于当前组合包")
            return _story_event_json(row)

    def approve_story_revision(self, revision_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions.begin() as session:
            row = self._required(session, StoryRevisionRecord, revision_id, lock=True)
            score = session.scalar(
                select(StoryScore).where(StoryScore.story_revision_id == revision_id)
            )
            required_subject_ids = tuple(
                session.scalars(
                    select(Subject.id).where(
                        Subject.production_run_id == row.production_run_id,
                        Subject.status != "archived",
                        Subject.role.in_(
                            (
                                SubjectRole.PROTAGONIST.value,
                                SubjectRole.CO_PROTAGONIST.value,
                                SubjectRole.SUPPORT.value,
                            )
                        ),
                    )
                )
            )
            scorecard = None if score is None else _scorecard(score)
            status = approve_story_revision(
                StoryRevisionStatus(row.status),
                scorecard=scorecard,
                requires_scorecard=requires_legacy_story_approval_contract(row),
                revision_subject_ids=tuple(uuid.UUID(item) for item in row.subject_ids_json),
                required_subject_ids=required_subject_ids,
            )
            session.execute(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == row.production_run_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                    StoryRevisionRecord.id != row.id,
                )
                .with_for_update()
            )
            previous_approved = list(
                session.scalars(
                    select(StoryRevisionRecord).where(
                        StoryRevisionRecord.production_run_id == row.production_run_id,
                        StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                        StoryRevisionRecord.id != row.id,
                    )
                )
            )
            if previous_approved:
                invalidate_story_production_lineage(
                    session,
                    project_id=row.production_run_id,
                    story_ids=tuple(previous.id for previous in previous_approved),
                    reason="当前剧情已切换到新的故事版本",
                )
            for previous in previous_approved:
                previous.status = StoryRevisionStatus.SUPERSEDED.value
            row.status = status.value
            row.approved_at = datetime.now(UTC)
            if row.scene_plan_json:
                materialize_approved_story_scenes(session, row)
            prompt = (
                None
                if row.candidate_prompt_id is None
                else session.scalar(
                    select(PromptRecord).where(PromptRecord.id == row.candidate_prompt_id)
                )
            )
            return _story_json(row, score, prompt)

    def get_storyboard_context(
        self,
        project_id: uuid.UUID,
        *,
        source_story_revision_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            story_query = select(StoryRevisionRecord).where(
                StoryRevisionRecord.production_run_id == project_id,
                StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
            )
            if source_story_revision_id is not None:
                story_query = story_query.where(StoryRevisionRecord.id == source_story_revision_id)
            story = session.scalar(
                story_query.order_by(StoryRevisionRecord.revision.desc()).limit(1)
            )
            if story is None:
                raise ValueError("选择的剧情脚本不存在、未批准或不属于当前项目")
            brief = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            if brief is None:
                raise ValueError("项目尚未保存创意简报")
            score = session.scalar(
                select(StoryScore).where(StoryScore.story_revision_id == story.id)
            )
            candidate_prompt = (
                None
                if story.candidate_prompt_id is None
                else session.scalar(
                    select(PromptRecord).where(PromptRecord.id == story.candidate_prompt_id)
                )
            )
            subjects = _preferred_subject_rows(
                session,
                list(
                    session.scalars(
                        select(Subject)
                        .where(
                            Subject.production_run_id == project_id,
                            Subject.status != "archived",
                        )
                        .order_by(Subject.created_at)
                    )
                ),
            )
            storyboard_revision = session.scalar(
                select(StoryboardRevision)
                .where(
                    StoryboardRevision.production_run_id == project_id,
                    StoryboardRevision.story_revision_id == story.id,
                    StoryboardRevision.status != StoryboardRevisionStatus.SUPERSEDED.value,
                )
                .order_by(StoryboardRevision.revision.desc())
                .limit(1)
            )
            existing = list(
                session.scalars(
                    select(ShotBeat).where(
                        ShotBeat.story_revision_id == story.id,
                        ShotBeat.status != "superseded",
                        *(
                            ()
                            if storyboard_revision is None
                            else (ShotBeat.storyboard_revision_id == storyboard_revision.id,)
                        ),
                    )
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
            return {
                "projectId": str(project_id),
                "storyId": str(story.id),
                "brief": _brief_json(brief),
                "story": _story_json(story, score, candidate_prompt),
                "subjects": [
                    _subject_json(
                        session,
                        subject,
                        self._required(session, SubjectRevision, subject.current_revision_id),
                        self._subject_references(session, subject.current_revision_id),
                    )
                    for subject in subjects
                    if subject.current_revision_id is not None
                ],
                "existing": (
                    None
                    if not existing
                    else {
                        **_storyboard_json(project_id, story.id, existing),
                        "storyboardRevisionId": (
                            None if storyboard_revision is None else str(storyboard_revision.id)
                        ),
                        "structureHash": (
                            None
                            if storyboard_revision is None
                            else storyboard_revision.structure_hash
                        ),
                        "inputBindings": (
                            []
                            if storyboard_revision is None
                            else storyboard_revision.input_bindings_json
                        ),
                        "generationPlanId": (
                            None if generation_plan is None else str(generation_plan.id)
                        ),
                        "generationPlanHash": (
                            None if generation_plan is None else generation_plan.input_hash
                        ),
                    }
                ),
            }

    def get_storyboard_reference_inputs(
        self,
        project_id: uuid.UUID,
        asset_ids: tuple[uuid.UUID, ...],
    ) -> dict[str, Any]:
        """Resolve approved character-design images in deterministic slot order."""

        requested = tuple(dict.fromkeys(asset_ids))
        with self._sessions() as session:
            self._require_project(session, project_id)
            selected = list(
                session.scalars(
                    select(CharacterDesignAsset)
                    .join(
                        CharacterDesignRevision,
                        CharacterDesignRevision.id
                        == CharacterDesignAsset.character_design_revision_id,
                    )
                    .where(
                        CharacterDesignAsset.asset_id.in_(requested),
                        CharacterDesignAsset.selected.is_(True),
                        CharacterDesignRevision.production_run_id == project_id,
                        CharacterDesignRevision.status == "approved",
                    )
                )
            )
            by_asset_id = {item.asset_id: item for item in selected}
            if set(requested) != set(by_asset_id):
                raise WorkflowConflictError(
                    "分镜多模态输入只能使用当前已批准角色设计版本中的选中素材"
                )
            slot_order = {"child": 0, "cat": 1, "pair_scale": 2}
            selected.sort(key=lambda item: slot_order.get(item.slot, 99))
            assets = {
                asset.id: asset
                for asset in session.scalars(
                    select(Asset).where(Asset.id.in_([item.asset_id for item in selected]))
                )
            }
            instructions = {
                "child": "锁定儿童本集造型、儿童身体比例与身份外观，不改写剧情",
                "cat": "锁定猫咪脸部、毛色分区、体型和自然四足结构，不改写剧情",
                "pair_scale": "锁定一人一猫相对比例、空间接触和同框构图，不改写剧情",
            }
            bindings: list[dict[str, Any]] = []
            paths: list[Path] = []
            for ordinal, selected_asset in enumerate(selected, 1):
                asset = assets.get(selected_asset.asset_id)
                if (
                    asset is None
                    or asset.media_type != "image"
                    or asset.status not in {"approved", "ready"}
                ):
                    raise WorkflowConflictError("分镜角色图片缺失、不是图片或尚未批准")
                path = _resolve_asset_path(asset.storage_key, self._asset_root)
                if not path.is_file():
                    raise WorkflowConflictError(f"分镜角色图片文件不可读取：{asset.id}")
                bindings.append(
                    {
                        "assetId": str(asset.id),
                        "sha256": asset.sha256,
                        "ordinal": ordinal,
                        "semanticRole": selected_asset.slot,
                        "purpose": selected_asset.semantic_role,
                        "instruction": instructions.get(
                            selected_asset.slot,
                            "只作为角色或构图参考，不改写批准剧情",
                        ),
                        "sourceType": "approved_character_design",
                        "sourceRevisionId": str(selected_asset.character_design_revision_id),
                    }
                )
                paths.append(path)
            return {"bindings": bindings, "paths": tuple(paths)}

    def save_storyboard_plan(
        self,
        project_id: uuid.UUID,
        *,
        story_id: uuid.UUID,
        plan: Any,
        durations: tuple[int, ...],
        prompt_id: uuid.UUID,
        input_bindings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            story = self._required(session, StoryRevisionRecord, story_id, lock=True)
            if (
                story.production_run_id != project_id
                or story.status != StoryRevisionStatus.APPROVED.value
            ):
                raise WorkflowConflictError("分镜计划所引用的故事已不再是当前批准版本")
            existing = list(
                session.scalars(select(ShotBeat).where(ShotBeat.story_revision_id == story.id))
            )
            if existing:
                return _storyboard_json(project_id, story.id, existing)
            storyboard_revision = StoryboardRevision(
                id=uuid.uuid4(),
                production_run_id=project_id,
                story_revision_id=story.id,
                revision=1,
                status=StoryboardRevisionStatus.DRAFT.value,
                structure_hash="0" * 64,
                source_step_id=(
                    None
                    if (prompt := session.get(PromptRecord, prompt_id)) is None
                    else prompt.step_id
                ),
                input_bindings_json=input_bindings,
            )
            session.add(storyboard_revision)
            session.flush()
            outlines = _normalized_story_scenes(story)
            derived_from_beats = not outlines
            if derived_from_beats:
                duration_by_order = {
                    beat.order: duration
                    for beat, duration in zip(plan.beats, durations, strict=True)
                }
                grouped_beats: dict[int, list[Any]] = {}
                for beat in plan.beats:
                    grouped_beats.setdefault(beat.scene_order, []).append(beat)
                scene_orders = sorted(grouped_beats)
                if scene_orders != list(range(1, len(scene_orders) + 1)):
                    raise ValueError("从完整故事派生的场景必须从 1 开始连续编号")
                outlines = []
                for scene_order in scene_orders:
                    scene_beats = grouped_beats[scene_order]
                    scene_label = next(
                        (
                            beat.scene_label.strip()
                            for beat in scene_beats
                            if beat.scene_label and beat.scene_label.strip()
                        ),
                        f"场景 {scene_order}",
                    )
                    directions = list(
                        dict.fromkeys(
                            (beat.visual_description or beat.action).strip() for beat in scene_beats
                        )
                    )
                    outlines.append(
                        {
                            "sceneKey": f"storyboard-scene-{scene_order:02d}",
                            "title": scene_label,
                            "purpose": "由完整故事正文自然派生",
                            "synopsis": " ".join(directions),
                            "durationWeight": sum(
                                duration_by_order[beat.order] for beat in scene_beats
                            ),
                            "continuity": {
                                "location": "",
                                "environment": "",
                                "timeWeather": "",
                                "decorations": [],
                                "props": [],
                                "transitionReason": "",
                            },
                        }
                    )
            scenes = list(
                session.scalars(
                    select(Scene)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.story_revision_id == story.id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order)
                    .with_for_update()
                )
            )
            paid_media_exists = bool(
                session.scalar(
                    select(func.count())
                    .select_from(WorkflowStep)
                    .where(
                        WorkflowStep.production_run_id == project_id,
                        WorkflowStep.kind.in_((StepKind.IMAGE.value, StepKind.VIDEO.value)),
                    )
                )
            )
            if paid_media_exists and len(scenes) != len(outlines):
                raise WorkflowConflictError(
                    "现有场景已有付费媒体历史；请复制故事方案后局部创建分镜"
                )
            while len(scenes) < len(outlines):
                row = Scene(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    story_revision_id=story.id,
                    scene_key=str(outlines[len(scenes)]["sceneKey"]),
                    active=True,
                    sort_order=len(scenes) + 1,
                    title="待编译场景",
                    source_text="待编译",
                    story_mode="single",
                    target_shot_count=1,
                    look_plan_json={},
                    look_draft_json={},
                    look_draft_revision=0,
                    status=SceneStatus.READY.value,
                )
                session.add(row)
                session.flush()
                scenes.append(row)
            if len(scenes) > len(outlines):
                raise WorkflowConflictError(
                    "现有场景多于新故事规划；为避免删除旧分镜，请复制项目或保留原场景后局部重排"
                )
            beats_by_scene = dict.fromkeys(range(1, len(scenes) + 1), 0)
            for index, (outline, scene) in enumerate(zip(outlines, scenes, strict=True), 1):
                scene.sort_order = index
                scene.title = str(outline["title"])
                scene.source_text = str(outline["synopsis"])
                scene.context_note = json.dumps(
                    {
                        "sceneKey": outline["sceneKey"],
                        "purpose": outline["purpose"],
                        "continuity": outline["continuity"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                next_look_plan = (
                    {} if derived_from_beats else _scene_look_plan_from_outline(outline)
                )
                if scene.look_plan_json != next_look_plan:
                    scene.look_plan_json = next_look_plan
                    scene.look_draft_json = {}
                    scene.look_draft_revision += 1
                    scene.selected_look_asset_id = None
                scene.status = SceneStatus.READY.value
            beats: list[ShotBeat] = []
            diagnostics: list[CanvasDiagnostic] = []
            for beat_plan, duration in zip(plan.beats, durations, strict=True):
                if beat_plan.scene_order > len(scenes):
                    raise ValueError("分镜 Beat 引用了不存在的场景")
                scene = scenes[beat_plan.scene_order - 1]
                beats_by_scene[beat_plan.scene_order] += 1
                beat = ShotBeat(
                    id=uuid.uuid4(),
                    scene_id=scene.id,
                    story_revision_id=story.id,
                    storyboard_revision_id=storyboard_revision.id,
                    prompt_id=prompt_id,
                    sort_order=beats_by_scene[beat_plan.scene_order],
                    revision=1,
                    title=beat_plan.title,
                    action=beat_plan.action,
                    visual_description=beat_plan.visual_description or beat_plan.action,
                    child_action=beat_plan.child_action,
                    cat_action=beat_plan.cat_action,
                    spatial_relation=beat_plan.spatial_relation,
                    contact_occlusion=beat_plan.contact_occlusion,
                    shot_size=beat_plan.shot_size,
                    camera=beat_plan.camera,
                    lighting=beat_plan.lighting,
                    dialogue=beat_plan.dialogue,
                    sound_effect=beat_plan.sound_effect,
                    music_intent=beat_plan.music_intent,
                    wardrobe_state=beat_plan.wardrobe_state,
                    prop_state=beat_plan.prop_state,
                    continuity_in=beat_plan.continuity_in,
                    continuity_out=beat_plan.continuity_out,
                    cut_intent=beat_plan.cut_intent,
                    duration_seconds=duration,
                    status="ready",
                )
                session.add(beat)
                beats.append(beat)
            for index, scene in enumerate(scenes, 1):
                shot_count = beats_by_scene[index]
                if shot_count == 0:
                    diagnostics.append(
                        CanvasDiagnostic(
                            code="storyboard_scene_uncovered",
                            severity="warning",
                            message=f"旧版场景“{scene.title}”未被当前分镜使用。",
                            targetId=str(scene.id),
                        )
                    )
                    continue
                scene.target_shot_count = shot_count
                scene.story_mode = "single" if shot_count == 1 else "multi"
            session.flush()
            storyboard_revision.structure_hash = storyboard_structure_hash(beats)
            generation_plan = _create_generation_plan_for_beats(
                session,
                storyboard=storyboard_revision,
                beats=beats,
            )
            session.flush()
            return {
                **_storyboard_json(project_id, story.id, beats),
                "storyboardRevisionId": str(storyboard_revision.id),
                "revision": storyboard_revision.revision,
                "structureHash": storyboard_revision.structure_hash,
                "inputBindings": storyboard_revision.input_bindings_json,
                "generationPlanId": str(generation_plan.id),
                "generationPlanHash": generation_plan.input_hash,
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
        patch = payload.model_dump(exclude_none=True)
        with self._sessions.begin() as session:
            located_beat = session.get(ShotBeat, beat_id)
            if located_beat is None:
                raise RecordNotFoundError(f"ShotBeat not found: {beat_id}")
            if located_beat.storyboard_revision_id is None:
                raise WorkflowConflictError("旧分镜尚未进入版本聚合，请先完成 0029 迁移")
            source_storyboard = self._required(
                session,
                StoryboardRevision,
                located_beat.storyboard_revision_id,
                lock=True,
            )
            source_plans = list(
                session.scalars(
                    select(GenerationPlan)
                    .where(GenerationPlan.storyboard_revision_id == source_storyboard.id)
                    .order_by(GenerationPlan.revision, GenerationPlan.id)
                    .with_for_update(of=GenerationPlan)
                )
            )
            current = session.scalar(
                select(ShotBeat)
                .where(ShotBeat.id == beat_id)
                .with_for_update(of=ShotBeat)
            )
            if current is None:
                raise RecordNotFoundError(f"ShotBeat not found: {beat_id}")
            if current.revision != expected_revision:
                raise WorkflowConflictError("分镜 Beat 已被更新，请比较版本后重试")
            if current.storyboard_revision_id != source_storyboard.id:
                raise WorkflowConflictError("分镜版本已变化，请刷新后重试")
            source_beats = list(
                session.scalars(
                    select(ShotBeat)
                    .join(Scene, Scene.id == ShotBeat.scene_id)
                    .where(
                        ShotBeat.storyboard_revision_id == source_storyboard.id,
                        ShotBeat.status != "superseded",
                    )
                    .order_by(Scene.sort_order, ShotBeat.sort_order)
                    .with_for_update(of=ShotBeat)
                )
            )
            if current.id not in {beat.id for beat in source_beats}:
                raise WorkflowConflictError("只能编辑当前活动分镜版本")
            next_storyboard = StoryboardRevision(
                id=uuid.uuid4(),
                production_run_id=source_storyboard.production_run_id,
                story_revision_id=source_storyboard.story_revision_id,
                revision=source_storyboard.revision + 1,
                status="draft",
                structure_hash="0" * 64,
                source_step_id=source_storyboard.source_step_id,
                input_bindings_json=list(source_storyboard.input_bindings_json),
            )
            session.add(next_storyboard)
            session.flush()
            source_storyboard.status = "superseded"
            for source_plan in source_plans:
                source_plan.status = GenerationPlanStatus.STALE.value
            source_states = {
                source.id: list(
                    session.scalars(
                        select(ShotSubjectState).where(ShotSubjectState.shot_beat_id == source.id)
                    )
                )
                for source in source_beats
            }
            saved: list[ShotBeat] = []
            edited: ShotBeat | None = None
            for source in source_beats:
                source.status = "superseded"
                values = patch if source.id == current.id else {}
                duration_seconds = int(values.get("duration_seconds", source.duration_seconds))
                row = ShotBeat(
                    id=uuid.uuid4(),
                    scene_id=source.scene_id,
                    shot_card_id=None,
                    story_revision_id=source.story_revision_id,
                    storyboard_revision_id=next_storyboard.id,
                    prompt_id=None,
                    reference_bindings_json=list(source.reference_bindings_json),
                    reference_binding_revision=source.reference_binding_revision,
                    sort_order=source.sort_order,
                    revision=source.revision + 1,
                    title=values.get("title", source.title),
                    action=values.get("action", source.action),
                    visual_description=values.get("visual_description", source.visual_description),
                    child_action=values.get("child_action", source.child_action),
                    cat_action=values.get("cat_action", source.cat_action),
                    spatial_relation=values.get("spatial_relation", source.spatial_relation),
                    contact_occlusion=values.get("contact_occlusion", source.contact_occlusion),
                    shot_size=values.get("shot_size", source.shot_size),
                    camera=values.get("camera", source.camera),
                    lighting=values.get("lighting", source.lighting),
                    dialogue=values.get("dialogue", source.dialogue),
                    sound_effect=values.get("sound_effect", source.sound_effect),
                    music_intent=values.get("music_intent", source.music_intent),
                    wardrobe_state=values.get("wardrobe_state", source.wardrobe_state),
                    prop_state=values.get("prop_state", source.prop_state),
                    continuity_in=values.get("continuity_in", source.continuity_in),
                    continuity_out=values.get("continuity_out", source.continuity_out),
                    cut_intent=values.get("cut_intent", source.cut_intent),
                    duration_seconds=duration_seconds,
                    temporal_beats_json=[
                        {
                            "phase": "editorial",
                            "startSecond": 0,
                            "endSecond": duration_seconds,
                            "childAction": values.get("child_action", source.child_action),
                            "catAction": values.get("cat_action", source.cat_action),
                            "camera": values.get("camera", source.camera),
                        }
                    ],
                    status="ready",
                )
                session.add(row)
                session.flush()
                saved.append(row)
                for state in source_states[source.id]:
                    session.add(
                        ShotSubjectState(
                            id=uuid.uuid4(),
                            shot_beat_id=row.id,
                            subject_revision_id=state.subject_revision_id,
                            start_state_json=state.start_state_json,
                            end_state_json=state.end_state_json,
                            action=state.action,
                            interaction=state.interaction,
                        )
                    )
                if source.id == current.id:
                    edited = row
                    if patch.get("subject_states") is not None:
                        session.execute(
                            ShotSubjectState.__table__.delete().where(
                                ShotSubjectState.shot_beat_id == row.id
                            )
                        )
                        self._save_subject_states(session, row.id, patch["subject_states"])
            next_storyboard.structure_hash = storyboard_structure_hash(saved)
            _create_generation_plan_for_beats(
                session,
                storyboard=next_storyboard,
                beats=saved,
            )
            project = self._required(session, ProductionRun, source_storyboard.production_run_id)
            project.selected_sequence_id = None
            if edited is None:
                raise WorkflowConflictError("编辑目标未能进入新分镜版本")
            return _beat_json(edited)

    def replace_shot_beat_references(
        self,
        beat_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        """Replace optional production references without changing story structure."""

        with self._sessions.begin() as session:
            located_beat = session.get(ShotBeat, beat_id)
            if located_beat is None:
                raise RecordNotFoundError(f"ShotBeat not found: {beat_id}")
            storyboard = None
            locked_plans: list[GenerationPlan] = []
            if located_beat.storyboard_revision_id is not None:
                storyboard = self._required(
                    session,
                    StoryboardRevision,
                    located_beat.storyboard_revision_id,
                    lock=True,
                )
                locked_plans = list(
                    session.scalars(
                        select(GenerationPlan)
                        .where(GenerationPlan.storyboard_revision_id == storyboard.id)
                        .order_by(GenerationPlan.revision, GenerationPlan.id)
                        .with_for_update(of=GenerationPlan)
                    )
                )
            active_beats = (
                []
                if storyboard is None
                else list(
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
            )
            beat = next((item for item in active_beats if item.id == beat_id), None)
            if storyboard is None:
                beat = session.scalar(
                    select(ShotBeat)
                    .where(ShotBeat.id == beat_id)
                    .with_for_update(of=ShotBeat)
                )
            if beat is None:
                raise RecordNotFoundError(f"ShotBeat not found: {beat_id}")
            if storyboard is not None and beat.storyboard_revision_id != storyboard.id:
                raise WorkflowConflictError("镜头所属分镜版本已变化，请刷新后重试")
            if beat.reference_binding_revision != expected_revision:
                raise WorkflowConflictError("镜头引用已经更新，请比较最新引用 revision 后再保存")
            scene = self._required(session, Scene, beat.scene_id)
            normalized: list[dict[str, Any]] = []
            seen_ids: set[uuid.UUID] = set()
            seen_hashes: set[str] = set()
            for item in payload.bindings:
                asset = self._required(session, Asset, item.asset_id)
                if (
                    asset.production_run_id not in {None, scene.production_run_id}
                    or asset.media_type != "image"
                    or asset.status not in {"approved", "ready"}
                ):
                    raise WorkflowConflictError(
                        f"镜头引用 {asset.id} 不属于当前项目、不是图片或尚未批准"
                    )
                path = _resolve_asset_path(asset.storage_key, self._asset_root)
                if not path.is_file():
                    raise WorkflowConflictError(f"镜头引用文件不可读取：{asset.id}")
                if asset.id in seen_ids or asset.sha256 in seen_hashes:
                    continue
                seen_ids.add(asset.id)
                seen_hashes.add(asset.sha256)
                normalized.append(
                    {
                        "assetId": str(asset.id),
                        "sha256": asset.sha256,
                        "semanticRole": item.semantic_role,
                        "purpose": f"shot_{item.semantic_role}",
                        "instruction": item.instruction,
                        "ordinal": len(normalized) + 1,
                        "sourceType": "editorial_shot_reference",
                        "locked": False,
                    }
                )
            plan_ids = [plan.id for plan in locked_plans]
            mappings = list(
                session.scalars(
                    select(GenerationClipShot)
                    .where(
                        *(
                            (GenerationClipShot.shot_beat_id == beat.id,)
                            if not plan_ids
                            else (GenerationClipShot.generation_plan_id.in_(plan_ids),)
                        ),
                    )
                    .order_by(
                        GenerationClipShot.generation_plan_id,
                        GenerationClipShot.shot_card_id,
                        GenerationClipShot.ordinal,
                    )
                    .with_for_update(of=GenerationClipShot)
                )
            )
            clip_ids = sorted({mapping.shot_card_id for mapping in mappings}, key=str)
            locked_clips = (
                []
                if not clip_ids
                else list(
                    session.scalars(
                        select(ShotCard)
                        .where(ShotCard.id.in_(clip_ids))
                        .order_by(
                            ShotCard.generation_plan_id,
                            ShotCard.plan_sort_order,
                            ShotCard.id,
                        )
                        .with_for_update(of=ShotCard)
                    )
                )
            )
            target_clip_ids = [
                mapping.shot_card_id for mapping in mappings if mapping.shot_beat_id == beat.id
            ]
            provider_evidence = (
                None
                if not target_clip_ids
                else session.scalar(
                    select(WorkflowStep.id)
                    .where(
                        WorkflowStep.shot_card_id.in_(target_clip_ids),
                        or_(
                            WorkflowStep.provider_task_id.is_not(None),
                            WorkflowStep.status == "submission_unknown",
                        ),
                    )
                    .limit(1)
                )
            )
            output_asset = (
                None
                if not target_clip_ids
                else session.scalar(
                    select(Asset.id).where(Asset.shot_card_id.in_(target_clip_ids)).limit(1)
                )
            )
            references_changed = normalized != list(beat.reference_bindings_json or [])
            if references_changed and (
                provider_evidence is not None or output_asset is not None
            ):
                raise WorkflowConflictError(
                    "该镜头所属生成片段已有 Provider 提交证据或输出资产；"
                    "必须创建新分镜版本，不能原地改写引用"
                )
            if not references_changed:
                return _beat_json(beat)
            beat.reference_bindings_json = normalized
            beat.reference_binding_revision += 1
            beat.prompt_id = None
            if storyboard is not None:
                storyboard.structure_hash = storyboard_structure_hash(active_beats)
                storyboard.status = StoryboardRevisionStatus.DRAFT.value
                storyboard.approved_structure_at = None
                storyboard.production_package_hash = None
                storyboard.production_approved_at = None
                for active_beat in active_beats:
                    if active_beat.status == "approved":
                        active_beat.status = "ready"
                beats_by_id = {active_beat.id: active_beat for active_beat in active_beats}
                mappings_by_plan: dict[uuid.UUID, list[GenerationClipShot]] = {}
                for mapping in mappings:
                    mappings_by_plan.setdefault(mapping.generation_plan_id, []).append(mapping)
                clips_by_plan: dict[uuid.UUID, list[ShotCard]] = {}
                for clip in locked_clips:
                    if clip.generation_plan_id is not None:
                        clips_by_plan.setdefault(clip.generation_plan_id, []).append(clip)
                for plan in locked_plans:
                    if plan.status == GenerationPlanStatus.STALE.value:
                        continue
                    plan_mappings = mappings_by_plan.get(plan.id, [])
                    mappings_by_clip: dict[uuid.UUID, list[GenerationClipShot]] = {}
                    for mapping in plan_mappings:
                        mappings_by_clip.setdefault(mapping.shot_card_id, []).append(mapping)
                    clip_documents = []
                    for clip in clips_by_plan.get(plan.id, []):
                        clip_mappings = mappings_by_clip.get(clip.id, [])
                        clip_documents.append(
                            {
                                "durationSeconds": clip.duration_seconds,
                                "shotBeatIds": [
                                    str(beats_by_id[mapping.shot_beat_id].id)
                                    for mapping in clip_mappings
                                    if mapping.shot_beat_id in beats_by_id
                                ],
                            }
                        )
                    plan.input_hash = generation_plan_input_hash(
                        structure_hash=storyboard.structure_hash,
                        provider=plan.provider,
                        model=plan.model,
                        capability_revision=plan.capability_revision,
                        clips=clip_documents,
                    )
                    plan.status = GenerationPlanStatus.PROPOSED.value
                    plan.approved_at = None
            target_clip_id_set = set(target_clip_ids)
            for clip in locked_clips:
                if clip.id in target_clip_id_set:
                    clip.prompt_id = None
                    clip.status = "ready"
            self._record_event(
                session,
                scene.production_run_id,
                "shot_reference_bindings_replaced",
                {
                    "shotBeatId": str(beat.id),
                    "referenceBindingRevision": beat.reference_binding_revision,
                    "assetIds": [item["assetId"] for item in normalized],
                    "downstreamStatus": "stale",
                },
            )
            return _beat_json(beat)

    def save_manual_storyboard(
        self,
        project_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            story = session.scalar(
                select(StoryRevisionRecord)
                .where(
                    StoryRevisionRecord.production_run_id == project_id,
                    StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                )
                .order_by(StoryRevisionRecord.revision.desc())
                .limit(1)
                .with_for_update()
            )
            if story is None:
                raise WorkflowConflictError("故事尚未人工批准，不能保存分镜草稿")
            brief = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            source_storyboard = session.scalar(
                select(StoryboardRevision)
                .where(
                    StoryboardRevision.production_run_id == project_id,
                    StoryboardRevision.status != "superseded",
                )
                .order_by(StoryboardRevision.revision.desc())
                .limit(1)
                .with_for_update()
            )
            source_plans = (
                []
                if source_storyboard is None
                else list(
                    session.scalars(
                        select(GenerationPlan)
                        .where(GenerationPlan.storyboard_revision_id == source_storyboard.id)
                        .order_by(GenerationPlan.revision, GenerationPlan.id)
                        .with_for_update(of=GenerationPlan)
                    )
                )
            )
            active_beats = list(
                session.scalars(
                    select(ShotBeat)
                    .where(
                        ShotBeat.story_revision_id == story.id,
                        ShotBeat.status != "superseded",
                        *(
                            ()
                            if source_storyboard is None
                            else (ShotBeat.storyboard_revision_id == source_storyboard.id,)
                        ),
                    )
                    .order_by(ShotBeat.sort_order)
                    .with_for_update(of=ShotBeat)
                )
            )
            current_revision = (
                source_storyboard.revision
                if source_storyboard is not None
                else max((beat.revision for beat in active_beats), default=0)
            )
            if current_revision != expected_revision:
                raise WorkflowConflictError("人工分镜草稿已被更新，请比较最新版本后重试")
            if (
                payload.healing_recipe
                and brief is not None
                and sum(shot.duration_seconds for shot in payload.shots)
                != brief.target_duration_seconds
            ):
                raise ValueError("治愈组合包镜头总时长必须与项目目标时长完全一致")

            active_scenes = list(
                session.scalars(
                    select(Scene)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order)
                    .with_for_update()
                )
            )
            scenes = [scene for scene in active_scenes if scene.story_revision_id == story.id]
            superseded_scenes = [
                scene for scene in active_scenes if scene.story_revision_id != story.id
            ]
            for scene in superseded_scenes:
                scene.active = False
                scene.stale_reason = f"场景规划已切换到剧情 revision {story.revision}"
            if superseded_scenes:
                session.flush()
            if not scenes:
                outlines = _normalized_story_scenes(story) or [
                    {"title": "人工分镜场景", "synopsis": "由人工镜头表建立"}
                ]
                for index, outline in enumerate(outlines, 1):
                    scene = Scene(
                        id=uuid.uuid4(),
                        production_run_id=project_id,
                        story_revision_id=story.id,
                        scene_key=str(outline.get("sceneKey") or f"scene-{index:02d}"),
                        active=True,
                        sort_order=index,
                        title=str(outline.get("title") or f"场景 {index}"),
                        source_text=str(outline.get("synopsis") or "人工分镜"),
                        context_note=(
                            None
                            if "continuity" not in outline
                            else json.dumps(
                                {
                                    "sceneKey": outline.get("sceneKey") or f"scene-{index:02d}",
                                    "purpose": outline.get("purpose") or "人工分镜",
                                    "continuity": outline["continuity"],
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        ),
                        look_plan_json=_scene_look_plan_from_outline(outline),
                        story_mode="single",
                        target_shot_count=1,
                        status=SceneStatus.READY.value,
                    )
                    session.add(scene)
                    scenes.append(scene)
                session.flush()

            active_by_id = {beat.id: beat for beat in active_beats}
            submitted_ids = {draft.id for draft in payload.shots if draft.id is not None}
            omitted_beat_ids = {beat.id for beat in active_beats if beat.id not in submitted_ids}
            source_plan_ids = [plan.id for plan in source_plans]
            locked_mappings = (
                []
                if not source_plan_ids
                else list(
                    session.scalars(
                        select(GenerationClipShot)
                        .where(GenerationClipShot.generation_plan_id.in_(source_plan_ids))
                        .order_by(
                            GenerationClipShot.generation_plan_id,
                            GenerationClipShot.shot_card_id,
                            GenerationClipShot.ordinal,
                        )
                        .with_for_update(of=GenerationClipShot)
                    )
                )
            )
            locked_clip_ids = sorted(
                {mapping.shot_card_id for mapping in locked_mappings},
                key=str,
            )
            if locked_clip_ids:
                list(
                    session.scalars(
                        select(ShotCard)
                        .where(ShotCard.id.in_(locked_clip_ids))
                        .order_by(
                            ShotCard.generation_plan_id,
                            ShotCard.plan_sort_order,
                            ShotCard.id,
                        )
                        .with_for_update(of=ShotCard)
                    )
                )
            omitted_clip_ids = {
                mapping.shot_card_id
                for mapping in locked_mappings
                if mapping.shot_beat_id in omitted_beat_ids
            }
            if not omitted_clip_ids:
                omitted_clip_ids = {
                    beat.shot_card_id
                    for beat in active_beats
                    if beat.id in omitted_beat_ids and beat.shot_card_id is not None
                }
            if omitted_clip_ids:
                paid_step_count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(WorkflowStep)
                        .where(WorkflowStep.shot_card_id.in_(omitted_clip_ids))
                    )
                    or 0
                )
                output_count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(Asset)
                        .where(Asset.shot_card_id.in_(omitted_clip_ids))
                    )
                    or 0
                )
                if paid_step_count or output_count:
                    raise WorkflowConflictError("已有付费任务或输出历史的导演分镜不能删除")
            scenes_by_id = {scene.id: scene for scene in scenes}
            for beat in active_beats:
                beat.status = "superseded"
            if source_storyboard is not None:
                source_storyboard.status = "superseded"
                for source_plan in source_plans:
                    source_plan.status = GenerationPlanStatus.STALE.value
            per_scene_count = {scene.id: 0 for scene in scenes}
            next_revision = current_revision + 1
            storyboard_revision = StoryboardRevision(
                id=uuid.uuid4(),
                production_run_id=project_id,
                story_revision_id=story.id,
                revision=(1 if source_storyboard is None else source_storyboard.revision + 1),
                status="draft",
                structure_hash="0" * 64,
                source_step_id=(
                    None if source_storyboard is None else source_storyboard.source_step_id
                ),
                input_bindings_json=(
                    [] if source_storyboard is None else list(source_storyboard.input_bindings_json)
                ),
            )
            session.add(storyboard_revision)
            session.flush()
            saved: list[ShotBeat] = []
            diagnostics: list[CanvasDiagnostic] = list(payload.diagnostics)
            for index, draft in enumerate(payload.shots):
                source = active_by_id.get(draft.id)
                if draft.scene_id is not None:
                    scene = scenes_by_id.get(draft.scene_id)
                    if scene is None:
                        raise ValueError("镜头所属场景不属于当前项目")
                elif source is not None:
                    scene = scenes_by_id.get(source.scene_id, scenes[0])
                else:
                    scene = scenes[min(index, len(scenes) - 1)]
                per_scene_count[scene.id] += 1
                temporal_beats = [
                    {
                        "phase": "editorial",
                        "startSecond": 0,
                        "endSecond": draft.duration_seconds,
                        "direction": draft.direction,
                        "childAction": draft.child_action,
                        "catAction": draft.cat_action,
                        "camera": draft.camera,
                    }
                ]
                row = ShotBeat(
                    id=uuid.uuid4(),
                    scene_id=scene.id,
                    shot_card_id=None,
                    story_revision_id=story.id,
                    storyboard_revision_id=storyboard_revision.id,
                    prompt_id=None,
                    reference_bindings_json=(
                        [] if source is None else list(source.reference_bindings_json)
                    ),
                    reference_binding_revision=(
                        1 if source is None else source.reference_binding_revision
                    ),
                    sort_order=per_scene_count[scene.id],
                    revision=next_revision,
                    title=draft.title,
                    action=draft.direction,
                    visual_description=draft.visual_description or draft.direction,
                    child_action=draft.child_action,
                    cat_action=draft.cat_action,
                    spatial_relation=draft.spatial_relation,
                    contact_occlusion=draft.contact_occlusion,
                    shot_size=draft.shot_size,
                    camera=draft.camera,
                    lighting=draft.lighting,
                    dialogue=draft.dialogue,
                    sound_effect=draft.sound_effect,
                    music_intent=draft.music_intent,
                    wardrobe_state=draft.wardrobe_state,
                    prop_state=draft.prop_state,
                    continuity_in=draft.continuity_in,
                    continuity_out=draft.continuity_out,
                    cut_intent=draft.cut_intent,
                    duration_seconds=draft.duration_seconds,
                    temporal_beats_json=temporal_beats,
                    status="ready",
                )
                session.add(row)
                saved.append(row)
            for scene in scenes:
                shot_count = per_scene_count[scene.id]
                if shot_count == 0:
                    diagnostics.append(
                        CanvasDiagnostic(
                            code="storyboard_scene_uncovered",
                            severity="warning",
                            message=f"旧版场景“{scene.title}”未被当前分镜使用。",
                            targetId=str(scene.id),
                        )
                    )
                    continue
                scene.target_shot_count = shot_count
                scene.story_mode = "single" if shot_count == 1 else "multi"
            storyboard_revision.structure_hash = storyboard_structure_hash(saved)
            generation_plan = _create_generation_plan_for_beats(
                session,
                storyboard=storyboard_revision,
                beats=saved,
            )
            project.selected_sequence_id = None
            session.flush()
            result = _storyboard_json(project_id, story.id, saved)
            result["revision"] = next_revision
            result["status"] = "draft"
            result["storyboardRevisionId"] = str(storyboard_revision.id)
            result["structureHash"] = storyboard_revision.structure_hash
            result["generationPlanId"] = str(generation_plan.id)
            result["generationPlanHash"] = generation_plan.input_hash
            result["diagnostics"] = [
                item.model_dump(mode="json", by_alias=True) for item in diagnostics
            ]
            return result

    def compile_storyboard_prompts(
        self,
        project_id: uuid.UUID,
        payload: Any,
    ) -> dict[str, Any]:
        """Compile auditable per-shot prompts from approved layered visual evidence."""

        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            story = self._required(
                session,
                StoryRevisionRecord,
                payload.story_revision_id,
                lock=True,
            )
            if (
                story.production_run_id != project_id
                or story.status != StoryRevisionStatus.APPROVED.value
            ):
                raise WorkflowConflictError("剧情脚本已不是当前人工批准版本")
            profile = self._required(
                session,
                VisualProfileRevision,
                payload.visual_profile_revision_id,
                lock=True,
            )
            if (
                profile.production_run_id != project_id
                or project.current_visual_profile_revision_id != profile.id
            ):
                raise WorkflowConflictError("本集视觉档案已更新，请重新准备分镜资产")

            storyboard_revision: StoryboardRevision | None = None
            generation_plan: GenerationPlan | None = None
            plan_beats_by_clip: dict[uuid.UUID, list[ShotBeat]] = {}
            if payload.storyboard_revision_id is not None:
                storyboard_revision = self._required(
                    session,
                    StoryboardRevision,
                    payload.storyboard_revision_id,
                    lock=True,
                )
                if (
                    storyboard_revision.production_run_id != project_id
                    or storyboard_revision.story_revision_id != story.id
                    or storyboard_revision.status
                    not in {"structure_approved", "production_approved"}
                    or storyboard_revision.structure_hash != payload.structure_hash
                ):
                    raise WorkflowConflictError("分镜结构版本或哈希已过期")
                generation_plan = self._required(
                    session,
                    GenerationPlan,
                    payload.generation_plan_id,
                    lock=True,
                )
                if (
                    generation_plan.storyboard_revision_id != storyboard_revision.id
                    or generation_plan.status != "approved"
                    or generation_plan.input_hash != payload.generation_plan_hash
                ):
                    raise WorkflowConflictError("Agent 生成编排尚未批准或已过期")
                mappings = list(
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
                mapped_beats = {
                    beat.id: beat
                    for beat in session.scalars(
                        select(ShotBeat).where(
                            ShotBeat.storyboard_revision_id == storyboard_revision.id,
                            ShotBeat.status != "superseded",
                        )
                    )
                }
                for mapping in mappings:
                    beat = mapped_beats.get(mapping.shot_beat_id)
                    if beat is not None:
                        plan_beats_by_clip.setdefault(mapping.shot_card_id, []).append(beat)
                if set(mapped_beats) != {
                    beat.id for clip_beats in plan_beats_by_clip.values() for beat in clip_beats
                }:
                    raise WorkflowConflictError("生成编排未完整覆盖当前导演分镜")

            recipe = session.scalar(
                select(ProductionRecipeInstance).where(
                    ProductionRecipeInstance.production_run_id == project_id,
                    ProductionRecipeInstance.lifecycle_status == "active",
                )
            )
            if recipe is not None and (storyboard_revision is None or generation_plan is None):
                raise WorkflowConflictError(
                    "配方项目必须从已批准的 StoryboardRevision 与 GenerationPlan 编译 Prompt"
                )
            global_blockers: list[str] = []
            if recipe is not None and profile.source_profile_id != recipe.canon_profile_id:
                global_blockers.append("当前本集视觉档案与配方锁定的 Canon 版本不一致")

            profile_asset_ids = [
                uuid.UUID(str(binding["assetId"]))
                for binding in profile.reference_bindings_json
                if binding.get("assetId")
            ]
            profile_assets = {
                asset.id: asset
                for asset in session.scalars(select(Asset).where(Asset.id.in_(profile_asset_ids)))
            }
            missing_canon = [
                asset_id
                for asset_id in profile_asset_ids
                if (
                    (asset := profile_assets.get(asset_id)) is None
                    or asset.status not in {"approved", "ready"}
                )
            ]
            if missing_canon:
                global_blockers.append("固定儿童、猫咪或画风 Canon 引用缺失/未批准")
            if recipe is not None:
                semantic_keys = {
                    str(asset.semantic_key)
                    for asset in profile_assets.values()
                    if asset.semantic_key and asset.status in {"approved", "ready"}
                }
                recipe_required_keys = canon_reference_keys(recipe.canon_profile_id, "indoor")
                absent_keys = sorted(set(recipe_required_keys).difference(semantic_keys))
                if absent_keys:
                    global_blockers.append("当前 Canon 必需证据槽位缺失：" + "、".join(absent_keys))

            appearance_bindings: list[dict[str, Any]] = []
            if recipe is not None:
                design = session.scalar(
                    select(CharacterDesignRevision)
                    .where(
                        CharacterDesignRevision.production_recipe_instance_id == recipe.id,
                        CharacterDesignRevision.source_story_revision_id == story.id,
                        CharacterDesignRevision.status == "approved",
                    )
                    .order_by(CharacterDesignRevision.revision.desc())
                    .limit(1)
                )
                selected_designs = (
                    []
                    if design is None
                    else list(
                        session.scalars(
                            select(CharacterDesignAsset).where(
                                CharacterDesignAsset.character_design_revision_id == design.id,
                                CharacterDesignAsset.selected.is_(True),
                            )
                        )
                    )
                )
                slot_order = {slot.value: index for index, slot in enumerate(CharacterDesignSlot)}
                selected_designs.sort(key=lambda item: slot_order.get(item.slot, len(slot_order)))
                selected_slots = [item.slot for item in selected_designs]
                if (
                    design is None
                    or len(selected_designs) != 3
                    or set(selected_slots) != {"child", "cat", "pair_scale"}
                ):
                    global_blockers.append("本集儿童、猫咪与同框比例图尚未逐槽批准")
                else:
                    design_assets = {
                        asset.id: asset
                        for asset in session.scalars(
                            select(Asset).where(
                                Asset.id.in_([item.asset_id for item in selected_designs])
                            )
                        )
                    }
                    for item in selected_designs:
                        asset = design_assets.get(item.asset_id)
                        if asset is None or asset.status not in {"approved", "ready"}:
                            global_blockers.append(f"本集角色槽位 {item.slot} 的图片尚未批准")
                            continue
                        appearance_bindings.append(
                            _compiled_reference_binding(
                                asset,
                                role=("composition" if item.slot == "pair_scale" else "appearance"),
                                purpose=item.slot,
                                source="character_design",
                            )
                        )

            scenes = {
                scene.id: scene
                for scene in session.scalars(
                    select(Scene).where(
                        Scene.production_run_id == project_id,
                        Scene.story_revision_id == story.id,
                        Scene.active.is_(True),
                    )
                )
            }
            results: list[dict[str, Any]] = []
            production_inputs_changed = False
            quality_diagnostics = payload.diagnostics
            for shot in payload.shots:
                clip = None
                clip_beats: list[ShotBeat] = []
                if generation_plan is not None:
                    if shot.shot_card_id is None:
                        raise WorkflowConflictError("生产 Prompt 必须指定真实生成片段")
                    clip = self._required(
                        session,
                        ShotCard,
                        shot.shot_card_id,
                        lock=True,
                    )
                    clip_beats = plan_beats_by_clip.get(clip.id, [])
                    if (
                        clip.generation_plan_id != generation_plan.id
                        or not clip_beats
                        or [beat.id for beat in clip_beats] != shot.editorial_shot_ids
                        or clip.duration_seconds != shot.duration_seconds
                    ):
                        raise WorkflowConflictError(f"生成片段 {shot.order} 与已批准编排不一致")
                    if clip.scene_id != shot.scene_id:
                        raise WorkflowConflictError(f"生成片段 {shot.order} 的场景已改变")
                scene = scenes.get(shot.scene_id)
                if scene is None:
                    raise WorkflowConflictError("镜头所属场景不属于当前剧情脚本")
                beat = None
                if clip_beats:
                    beat = clip_beats[0]
                elif shot.beat_id is not None:
                    beat = self._required(session, ShotBeat, shot.beat_id, lock=True)
                    if (
                        beat.story_revision_id != story.id
                        or beat.scene_id != scene.id
                        or beat.status == "superseded"
                        or beat.revision != shot.expected_revision
                    ):
                        raise WorkflowConflictError(
                            f"镜头 {shot.order} 已更新，请基于最新 revision 重新编译"
                        )
                elif shot.expected_revision != 0:
                    raise WorkflowConflictError("新镜头的 expectedRevision 必须为 0")

                blockers = list(global_blockers)
                warnings: list[str] = [
                    item.message
                    for item in quality_diagnostics
                    if item.severity == "warning"
                ]
                scene_bindings, scene_blockers, scene_warnings = _scene_prompt_bindings(
                    session,
                    scene,
                    profile,
                    storyboard_revision=storyboard_revision,
                    generation_plan=generation_plan,
                    scene_look_usage=(
                        "off" if clip is None else str(clip.scene_look_usage)
                    ),
                )
                blockers.extend(scene_blockers)
                warnings.extend(scene_warnings)
                composition_bindings: list[dict[str, Any]] = []
                if shot.composition_asset_ids:
                    composition_assets = {
                        asset.id: asset
                        for asset in session.scalars(
                            select(Asset).where(Asset.id.in_(shot.composition_asset_ids))
                        )
                    }
                    for asset_id in shot.composition_asset_ids:
                        asset = composition_assets.get(asset_id)
                        if (
                            asset is None
                            or asset.production_run_id not in {None, project_id}
                            or asset.media_type != "image"
                            or asset.status not in {"approved", "ready"}
                        ):
                            blockers.append(f"构图参考 {asset_id} 不可用或尚未批准")
                            continue
                        composition_bindings.append(
                            _compiled_reference_binding(
                                asset,
                                role="composition",
                                purpose="shot_composition",
                                source="shot",
                            )
                        )
                else:
                    warnings.append("当前镜头未绑定额外构图参考，将仅使用分镜描述与同框比例图")

                editorial_reference_bindings: list[dict[str, Any]] = []
                for editorial_beat in clip_beats or ([] if beat is None else [beat]):
                    for binding in editorial_beat.reference_bindings_json:
                        if not isinstance(binding, dict) or not binding.get("assetId"):
                            blockers.append(f"导演分镜 {editorial_beat.title} 包含无效引用")
                            continue
                        asset = session.get(Asset, uuid.UUID(str(binding["assetId"])))
                        if (
                            asset is None
                            or asset.production_run_id not in {None, project_id}
                            or asset.media_type != "image"
                            or asset.status not in {"approved", "ready"}
                            or asset.sha256 != binding.get("sha256")
                        ):
                            blockers.append(
                                f"导演分镜 {editorial_beat.title} 的按需引用已缺失或过期"
                            )
                            continue
                        editorial_reference_bindings.append(
                            _compiled_reference_binding(
                                asset,
                                role=str(binding.get("semanticRole") or "composition"),
                                purpose=str(binding.get("purpose") or "shot_reference"),
                                source="editorial_shot",
                            )
                        )

                canon_bindings = [
                    _compiled_reference_binding(
                        asset,
                        role=("style" if str(binding.get("purpose")) == "style" else "identity"),
                        purpose=str(binding.get("purpose") or asset.semantic_key or "identity"),
                        source="canon",
                    )
                    for binding in profile.reference_bindings_json
                    if binding.get("assetId")
                    and (asset := profile_assets.get(uuid.UUID(str(binding["assetId"]))))
                    is not None
                ]
                if profile.source_profile_id == CANON_V4_PROFILE_ID:
                    style_board_bindings = [
                        binding
                        for binding in canon_bindings
                        if binding.get("semanticKey") == CANON_V4_STYLE_BOARD_KEY
                    ]
                    environment_bindings = [
                        binding
                        for binding in scene_bindings
                        if (binding.get("authority") or {}).get("role") == "environment"
                    ]
                    if len(style_board_bindings) != 1:
                        blockers.append("Canon-v4 必须且只能绑定一张可提交 Provider 的纯画风板")
                    if not environment_bindings:
                        blockers.append("当前镜头缺少一张已批准的环境参考")
                    if len(environment_bindings) > 1:
                        warnings.append("当前场景存在多张环境参考；视频请求只使用排序第一张作为空间权威")
                    raw_reference_bindings = [
                        *appearance_bindings,
                        *environment_bindings[:1],
                        *style_board_bindings,
                    ]
                    omitted_special_count = len(composition_bindings) + len(
                        editorial_reference_bindings
                    )
                    if omitted_special_count:
                        warnings.append(
                            "Canon-v4 视频请求已使用职责唯一的五类权威参考；"
                            "额外构图引用仅保留在审计与镜头正文中"
                        )
                else:
                    raw_reference_bindings = [
                        *canon_bindings,
                        *appearance_bindings,
                        *scene_bindings,
                        *composition_bindings,
                        *editorial_reference_bindings,
                    ]
                reference_bindings, reference_blockers, reference_warnings = (
                    _compile_provider_reference_manifest(raw_reference_bindings, maximum=14)
                )
                blockers.extend(reference_blockers)
                warnings.extend(reference_warnings)
                shot_document = shot.model_dump(mode="json", by_alias=True)
                if clip is not None:
                    cursor = 0
                    director_windows: list[dict[str, Any]] = []
                    for index, editorial_beat in enumerate(clip_beats, 1):
                        director_windows.append(
                            {
                                "order": index,
                                "beatId": str(editorial_beat.id),
                                "startSecond": cursor,
                                "endSecond": cursor + editorial_beat.duration_seconds,
                                "title": editorial_beat.title,
                                "direction": editorial_beat.action,
                                "visualDescription": editorial_beat.visual_description,
                                "childAction": editorial_beat.child_action,
                                "catAction": editorial_beat.cat_action,
                                "spatialRelation": editorial_beat.spatial_relation,
                                "contactOcclusion": editorial_beat.contact_occlusion,
                                "shotSize": editorial_beat.shot_size,
                                "camera": editorial_beat.camera,
                                "lighting": editorial_beat.lighting,
                                "soundEffect": editorial_beat.sound_effect,
                                "musicIntent": editorial_beat.music_intent,
                                "continuityIn": editorial_beat.continuity_in,
                                "continuityOut": editorial_beat.continuity_out,
                                "cutIntent": editorial_beat.cut_intent,
                            }
                        )
                        cursor += editorial_beat.duration_seconds
                    current_direction = "\n".join(
                        editorial_beat.action.strip()
                        for editorial_beat in clip_beats
                        if editorial_beat.action.strip()
                    )
                    shot_document.update(
                        {
                            "title": clip.title,
                            # The approved editable ShotBeat rows are the
                            # current creative source. A generation-plan clip
                            # is an execution projection and may retain the
                            # wording it was originally compiled from.
                            "direction": current_direction or clip.direction,
                            "action": current_direction or clip.direction,
                            "durationSeconds": clip.duration_seconds,
                            "camera": "按编号导演分镜与时间窗口执行",
                            "dialogue": "",
                            "directorShots": director_windows,
                        }
                    )
                input_snapshot = {
                    "storyRevisionId": str(story.id),
                    "storyRevision": story.revision,
                    "storyboardRevisionId": (
                        None if storyboard_revision is None else str(storyboard_revision.id)
                    ),
                    "structureHash": payload.structure_hash,
                    "generationPlanId": (
                        None if generation_plan is None else str(generation_plan.id)
                    ),
                    "generationPlanHash": payload.generation_plan_hash,
                    "visualProfileRevisionId": str(profile.id),
                    "visualProfileRevision": profile.revision,
                    "sceneId": str(scene.id),
                    "sceneLookDraftRevision": scene.look_draft_revision,
                    "shot": shot_document,
                    "referenceBindings": reference_bindings,
                    "warnings": warnings,
                    "blockers": blockers,
                }
                compiled_prompt_text = _compile_storyboard_prompt_text(
                    profile=profile,
                    story=story,
                    scene=scene,
                    shot=shot_document,
                    reference_bindings=reference_bindings,
                    healing_recipe=payload.healing_recipe,
                )
                input_snapshot["promptBundle"] = {
                    "creativeText": compiled_prompt_text,
                    "referenceManifest": reference_bindings,
                    "executionParams": {
                        "providerInputMode": "reference_media",
                        "aspectRatio": "9:16",
                        "durationSeconds": shot.duration_seconds,
                        "referenceCount": sum(
                            1
                            for binding in reference_bindings
                            if binding.get("providerIncluded") is True
                        ),
                    },
                    "auditSnapshot": {
                        "storyRevisionId": str(story.id),
                        "storyboardRevisionId": (
                            None if storyboard_revision is None else str(storyboard_revision.id)
                        ),
                        "generationPlanId": (
                            None if generation_plan is None else str(generation_plan.id)
                        ),
                        "visualProfileRevisionId": str(profile.id),
                    },
                }
                input_hash = _json_hash(input_snapshot)
                if clip is not None:
                    current_prompt = (
                        None
                        if clip.prompt_id is None
                        else session.get(PromptRecord, clip.prompt_id)
                    )
                    if current_prompt is None or current_prompt.input_hash != input_hash:
                        production_inputs_changed = True
                result: dict[str, Any] = {
                    "beatId": None if beat is None else str(beat.id),
                    "order": shot.order,
                    "finalPrompt": compiled_prompt_text,
                    "promptBundle": input_snapshot["promptBundle"],
                    "promptId": None,
                    "referenceBindings": reference_bindings,
                    "warnings": warnings,
                    "diagnostics": [
                        item.model_dump(mode="json", by_alias=True)
                        for item in quality_diagnostics
                    ],
                    "blockers": list(dict.fromkeys(blockers)),
                    "estimatedCost": {"currency": "CNY", "amountMicros": 0},
                    "inputHash": input_hash,
                }
                if result["blockers"]:
                    results.append(result)
                    continue

                final_prompt = compiled_prompt_text
                prompt_hash = hashlib.sha256(final_prompt.encode("utf-8")).hexdigest()
                business_object_id = (
                    clip.id
                    if clip is not None
                    else beat.id
                    if beat is not None
                    else uuid.uuid5(
                        project_id,
                        f"storyboard-draft:{story.id}:{scene.id}:{shot.order}",
                    )
                )
                operation_key = f"director:storyboard-prompt:{business_object_id}"
                idempotency_key = hashlib.sha256(
                    f"{project_id}:{operation_key}:{input_hash}:{prompt_hash}".encode()
                ).hexdigest()
                step = session.scalar(
                    select(WorkflowStep).where(WorkflowStep.idempotency_key == idempotency_key)
                )
                if step is None:
                    now = datetime.now(UTC)
                    step = WorkflowStep(
                        id=uuid.uuid4(),
                        production_run_id=project_id,
                        scene_id=scene.id,
                        shot_card_id=(
                            clip.id
                            if clip is not None
                            else None
                            if beat is None
                            else beat.shot_card_id
                        ),
                        kind=StepKind.DIRECTOR.value,
                        status=StepStatus.SUCCEEDED.value,
                        attempt=1,
                        operation_key=operation_key,
                        idempotency_key=idempotency_key,
                        provider="internal",
                        model="layered-storyboard-prompt-compiler-v1",
                        input_hash=input_hash,
                        request_hash=prompt_hash,
                        input_snapshot_json=input_snapshot,
                        completed_at=now,
                    )
                    session.add(step)
                    session.flush()
                prompt = session.scalar(
                    select(PromptRecord).where(
                        PromptRecord.step_id == step.id,
                        PromptRecord.sha256 == prompt_hash,
                    )
                )
                if prompt is None:
                    now = datetime.now(UTC)
                    prompt = PromptRecord(
                        id=uuid.uuid4(),
                        step_id=step.id,
                        purpose=PromptPurpose.DIRECTOR.value,
                        model="layered-storyboard-prompt-compiler-v1",
                        prompt_text=final_prompt,
                        sha256=prompt_hash,
                        call_purpose="storyboard_prompt_compilation",
                        node_id=business_object_id,
                        business_object_type=(
                            "generation_clip"
                            if clip is not None
                            else "shot_beat"
                            if beat is not None
                            else "storyboard_draft_shot"
                        ),
                        business_object_id=business_object_id,
                        template_name="storyboard.layered-shot.v1",
                        template_version="1.0.0",
                        final_prompt=final_prompt,
                        provider_request_json={
                            "mode": "compile_only",
                            "referenceBindings": reference_bindings,
                        },
                        provider_internal_transform="none",
                        input_snapshot_json=input_snapshot,
                        structured_response_json={
                            "referenceBindings": reference_bindings,
                            "warnings": warnings,
                            "blockers": [],
                        },
                        parameters_json={"healingRecipe": payload.healing_recipe},
                        cost_micros=0,
                        status="succeeded",
                        input_hash=input_hash,
                        output_hash=prompt_hash,
                        completed_at=now,
                    )
                    session.add(prompt)
                    session.flush()
                if clip is not None:
                    clip.prompt_id = prompt.id
                    for editorial_beat in clip_beats:
                        editorial_beat.prompt_id = prompt.id
                elif beat is not None and _shot_matches_persisted_beat(shot_document, beat):
                    beat.prompt_id = prompt.id
                result.update(finalPrompt=final_prompt, promptId=str(prompt.id))
                results.append(result)

            if (
                production_inputs_changed
                and storyboard_revision is not None
                and storyboard_revision.status == "production_approved"
            ):
                storyboard_revision.status = "structure_approved"
                storyboard_revision.production_package_hash = None
                storyboard_revision.production_approved_at = None
            return {
                "projectId": str(project_id),
                "storyRevisionId": str(story.id),
                "storyboardRevisionId": (
                    None if storyboard_revision is None else str(storyboard_revision.id)
                ),
                "generationPlanId": (None if generation_plan is None else str(generation_plan.id)),
                "visualProfileRevisionId": str(profile.id),
                "status": ("blocked" if any(item["blockers"] for item in results) else "compiled"),
                "shots": results,
            }

    def create_generation_attempt(self, payload: Any) -> dict[str, Any]:
        attempt, _created = self.begin_generation_attempt(
            project_id=payload.project_id,
            business_object_type=payload.business_object_type,
            business_object_id=payload.business_object_id,
            idempotency_key=payload.idempotency_key,
            provider=payload.provider,
            model=payload.model,
            request=payload.request,
        )
        return attempt

    def retry_generation_attempt(self, attempt_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions() as session:
            source = self._required(session, GenerationAttempt, attempt_id)
            if source.status == "submission_unknown":
                raise WorkflowConflictError(
                    "该任务可能已提交供应商，必须先用供应商任务号或人工对账，禁止直接重试"
                )
            if source.status != "failed":
                raise WorkflowConflictError("只有明确失败的生成尝试可以重试")
            values = {
                "project_id": source.production_run_id,
                "business_object_type": source.business_object_type,
                "business_object_id": source.business_object_id,
                "idempotency_key": payload.idempotency_key,
                "provider": source.provider,
                "model": source.model,
                "request": {**source.request_json, "retryReason": payload.reason},
            }
        attempt, created = self.begin_generation_attempt(**values)
        if created:
            with self._sessions.begin() as session:
                row = self._required(
                    session,
                    GenerationAttempt,
                    uuid.UUID(str(attempt["id"])),
                    lock=True,
                )
                row.retry_of_id = attempt_id
        return attempt

    def review_asset(self, asset_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions.begin() as session:
            asset = self._required(session, Asset, asset_id, lock=True)
            character_design = dict(dict(asset.metadata_json or {}).get("characterDesign") or {})
            if character_design.get("validationOnly") is True:
                raise WorkflowConflictError(
                    "引用顺序验证候选只能保留审计，不能批准、拒绝或替换生产版本"
                )
            next_status = "approved" if payload.decision == "approve" else "rejected"
            if asset.status == next_status:
                return {
                    "assetId": str(asset.id),
                    "decision": payload.decision,
                    "status": asset.status,
                    "reason": payload.reason,
                }
            asset.status = next_status
            if asset.producing_step_id is not None:
                step = self._required(
                    session,
                    WorkflowStep,
                    asset.producing_step_id,
                    lock=True,
                )
                step.status = (
                    StepStatus.SUCCEEDED.value
                    if payload.decision == "approve"
                    else StepStatus.FAILED.value
                )
                step.completed_at = datetime.now(UTC)
                session.add(
                    Review(
                        id=uuid.uuid4(),
                        step_id=asset.producing_step_id,
                        asset_id=asset.id,
                        source="human",
                        decision=payload.decision,
                        reason=payload.reason,
                        warnings_json=[],
                        evidence_json={},
                    )
                )
            if asset.canvas_node_id is not None:
                asset_node = self._required(
                    session,
                    CanvasGraphNode,
                    asset.canvas_node_id,
                    lock=True,
                )
                asset_node.status = next_status
                asset_node.data_json = {
                    **asset_node.data_json,
                    "status": next_status,
                }
                if payload.decision == "approve":
                    review_node = session.scalar(
                        select(CanvasGraphNode)
                        .join(
                            CanvasGraphEdge,
                            CanvasGraphEdge.target_node_id == CanvasGraphNode.id,
                        )
                        .where(
                            CanvasGraphEdge.source_node_id == asset_node.id,
                            CanvasGraphNode.node_type == CanvasNodeType.REVIEW.value,
                        )
                    )
                    timeline_node = session.scalar(
                        select(CanvasGraphNode).where(
                            CanvasGraphNode.production_run_id == asset.production_run_id,
                            CanvasGraphNode.node_type == CanvasNodeType.TIMELINE.value,
                        )
                    )
                    if review_node is not None:
                        review_node.status = "approved"
                        review_node.data_json = {
                            **review_node.data_json,
                            "status": "approved",
                            "approvedAssetId": str(asset.id),
                        }
                    if review_node is not None and timeline_node is not None:
                        existing_edge = session.scalar(
                            select(CanvasGraphEdge.id).where(
                                CanvasGraphEdge.source_node_id == review_node.id,
                                CanvasGraphEdge.target_node_id == timeline_node.id,
                                CanvasGraphEdge.source_port == "approved_asset",
                                CanvasGraphEdge.target_port == "approved_asset",
                            )
                        )
                        if existing_edge is None:
                            session.add(
                                _graph_edge(
                                    asset.production_run_id,
                                    CanvasConnection(
                                        sourceNodeId=review_node.id,
                                        sourceNodeType=CanvasNodeType.REVIEW,
                                        sourcePort="approved_asset",
                                        targetNodeId=timeline_node.id,
                                        targetNodeType=CanvasNodeType.TIMELINE,
                                        targetPort="approved_asset",
                                    ),
                                )
                            )
                        timeline_node.status = "ready"
                        timeline_node.data_json = {
                            **timeline_node.data_json,
                            "status": "ready",
                            "approvedAssetIds": sorted(
                                {
                                    *timeline_node.data_json.get("approvedAssetIds", []),
                                    str(asset.id),
                                }
                            ),
                        }
            if asset.production_run_id is not None:
                self._record_event(
                    session,
                    asset.production_run_id,
                    "asset_reviewed",
                    {
                        "assetId": str(asset.id),
                        "decision": payload.decision,
                        "status": next_status,
                    },
                )
            return {
                "assetId": str(asset.id),
                "decision": payload.decision,
                "status": asset.status,
                "reason": payload.reason,
            }

    def get_prompt_run(self, prompt_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            prompt = self._required(session, PromptRecord, prompt_id)
            step = self._required(session, WorkflowStep, prompt.step_id)
            return _prompt_json(prompt, step)

    def get_asset_generation_lineage(self, asset_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            asset = self._required(session, Asset, asset_id)
            metadata = dict(asset.metadata_json or {})
            batch_id_value = metadata.get("batchId")
            batch = (
                None
                if not batch_id_value
                else session.get(MediaGenerationBatch, uuid.UUID(str(batch_id_value)))
            )
            step = (
                None
                if asset.producing_step_id is None
                else session.get(WorkflowStep, asset.producing_step_id)
            )
            prompt = (
                None
                if step is None
                else session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            )
            references = (
                list(metadata.get("referenceManifest") or [])
                if batch is None
                else list(batch.reference_manifest_json or [])
            )
            reference_ids = [
                uuid.UUID(str(item["assetId"]))
                for item in references
                if isinstance(item, dict) and item.get("assetId")
            ]
            reference_assets = {
                row.id: row
                for row in session.scalars(select(Asset).where(Asset.id.in_(reference_ids)))
            }
            semantic_instructions = {
                "person:headshot": (
                    "所选素材可确认为儿童面部 Canon；历史 Provider 槽位仍以证据等级为准"
                ),
                "person:fullbody": (
                    "所选素材可确认为儿童全身比例 Canon；"
                    "历史 Provider 槽位仍以证据等级为准"
                ),
                "cat:front": (
                    "所选素材可确认为猫咪正面 Canon；历史 Provider 槽位仍以证据等级为准"
                ),
                "cat:side": (
                    "所选素材可确认为猫咪侧面结构 Canon；历史 Provider 槽位仍以证据等级为准"
                ),
                "style:line_texture": (
                    "所选素材可确认为线条材质画风参考；"
                    "历史 Provider 槽位仍以证据等级为准"
                ),
            }
            enriched_references: list[dict[str, Any]] = []
            for item in references:
                if not isinstance(item, dict) or not item.get("assetId"):
                    continue
                document = dict(item)
                reference_asset = reference_assets.get(uuid.UUID(str(item["assetId"])))
                if reference_asset is not None:
                    semantic_key = str(reference_asset.semantic_key or reference_asset.role)
                    asset_metadata = dict(reference_asset.metadata_json or {})
                    document["sha256"] = document.get("sha256") or reference_asset.sha256
                    document["semanticRole"] = (
                        semantic_key
                        if document.get("semanticRole") in {None, "", "reference"}
                        else document["semanticRole"]
                    )
                    document["purpose"] = (
                        semantic_key
                        if document.get("purpose") in {None, "", "reference"}
                        else document["purpose"]
                    )
                    document["instruction"] = document.get(
                        "instruction"
                    ) or semantic_instructions.get(
                        semantic_key,
                        "历史记录可确认选择了该素材，但不能据此补写过去的 Provider 槽位职责",
                    )
                    document["title"] = document.get("title") or str(
                        asset_metadata.get("displayName")
                        or asset_metadata.get("title")
                        or reference_asset.semantic_key
                        or reference_asset.role
                    )
                    document["contentUrl"] = f"/api/v1/assets/{reference_asset.id}/content"
                if document.get("evidenceLevel") != "frozen":
                    document["providerSlot"] = None
                enriched_references.append(document)
            references = enriched_references
            evidence_levels = {
                str(item.get("evidenceLevel") or "unknown")
                for item in references
                if isinstance(item, dict)
            }
            evidence = (
                "frozen"
                if evidence_levels == {"frozen"}
                else "unknown"
                if "unknown" in evidence_levels or not evidence_levels
                else "selected_only"
            )
            return {
                "assetId": str(asset.id),
                "assetSha256": asset.sha256,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "batchId": None if batch is None else str(batch.id),
                "stepId": None if step is None else str(step.id),
                "promptId": None if prompt is None else str(prompt.id),
                "prompt": (None if prompt is None else prompt.final_prompt or prompt.prompt_text),
                "provider": None if batch is None else batch.provider,
                "model": None if batch is None else batch.model,
                "providerTaskId": None if step is None else step.provider_task_id,
                "inputHash": (
                    metadata.get("generationInputHash")
                    if batch is None
                    else batch.reference_manifest_hash or None
                ),
                "providerOrderEvidence": evidence,
                "providerOrderNotice": (
                    None
                    if evidence == "frozen"
                    else "历史任务仅能确认所选素材，Provider 槽位顺序未知"
                    if evidence == "unknown"
                    else "历史任务保存了素材选择，但不能证明图片 Worker 的最终 Provider 槽位顺序"
                ),
                "references": references,
            }

    def list_canvas_templates(self) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json", by_alias=True) for item in list_template_specs()]

    def image_candidate_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("media:image:batch:"):
                raise ValueError("workflow step is not an image batch candidate")
            batch_id = uuid.UUID(str(step.input_snapshot_json["batchId"]))
            batch = self._required(session, MediaGenerationBatch, batch_id)
            if batch.production_run_id != step.production_run_id:
                raise WorkflowConflictError("图片候选任务与生成批次项目不一致")
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None or prompt.status != "pending":
                raise WorkflowConflictError("图片候选缺少待执行的精确 Prompt")
            included = [
                item
                for item in batch.reference_manifest_json
                if isinstance(item, dict) and item.get("providerIncluded") is True
            ]
            if not batch.reference_manifest_hash:
                raise WorkflowConflictError(
                    "历史图片任务没有可证明的冻结输入哈希，不能按新链路提交"
                )
            if included and any(item.get("evidenceLevel") != "frozen" for item in included):
                raise WorkflowConflictError(
                    "历史图片任务无法证明 Provider 引用顺序，不能在未知顺序下提交"
                )
            assets: list[Asset] = []
            for item in sorted(included, key=lambda value: int(value.get("ordinal") or 0)):
                asset = self._required(session, Asset, uuid.UUID(str(item["assetId"])))
                if asset.production_run_id not in {None, batch.production_run_id}:
                    raise WorkflowConflictError("图片生成冻结引用不属于当前项目")
                if asset.media_type != "image":
                    raise WorkflowConflictError("图片生成冻结引用不是图片")
                if item.get("sha256") and asset.sha256 != item.get("sha256"):
                    raise WorkflowConflictError("图片生成冻结引用的 SHA 已变化")
                assets.append(asset)
            return {
                "batchId": str(batch.id),
                "candidateIndex": int(step.input_snapshot_json["candidateIndex"]),
                "prompt": prompt.final_prompt or prompt.prompt_text,
                "referencePaths": tuple(
                    _resolve_asset_path(asset.storage_key, self._asset_root) for asset in assets
                ),
                "referenceManifest": tuple(included),
                "inputHash": batch.reference_manifest_hash,
            }

    def complete_image_candidate(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
    ) -> str:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(select(Asset).where(Asset.producing_step_id == step.id))
            if existing is not None:
                return str(existing.id)
            batch = self._required(
                session,
                MediaGenerationBatch,
                uuid.UUID(str(step.input_snapshot_json["batchId"])),
                lock=True,
            )
            candidate_index = int(step.input_snapshot_json["candidateIndex"])
            character_design = batch.input_json.get("characterDesign")
            validation_only = bool(
                isinstance(character_design, dict)
                and character_design.get("validationOnly") is True
            )
            candidate_index_offset = (
                int(character_design.get("candidateIndexOffset") or 0)
                if isinstance(character_design, dict)
                else 0
            )
            stored_candidate_index = candidate_index_offset + candidate_index
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None:
                raise WorkflowConflictError("图片候选缺少 Prompt 审计记录")
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=batch.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=batch.canvas_node_id,
                role=(
                    f"character_design_{batch.input_json['characterDesign']['slot']}"
                    if isinstance(batch.input_json.get("characterDesign"), dict)
                    else "image_candidate"
                ),
                semantic_key=(
                    "character-design:"
                    f"{batch.input_json['characterDesign']['revisionId']}:"
                    f"{batch.input_json['characterDesign']['slot']}:"
                    f"candidate:{stored_candidate_index}"
                    if isinstance(batch.input_json.get("characterDesign"), dict)
                    else f"batch:{batch.id}:candidate:{candidate_index}"
                ),
                scope="canvas_node",
                status="candidate",
                media_type="image",
                storage_key=_asset_storage_key(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json={
                    "batchId": str(batch.id),
                    "candidateIndex": stored_candidate_index,
                    "batchCandidateIndex": candidate_index,
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "promptId": str(prompt.id),
                    "characterDesign": batch.input_json.get("characterDesign"),
                    "generationInputHash": batch.reference_manifest_hash,
                    "referenceManifest": batch.reference_manifest_json,
                    "providerOrderEvidence": (
                        "frozen"
                        if all(
                            item.get("evidenceLevel") == "frozen"
                            for item in batch.reference_manifest_json
                            if isinstance(item, dict)
                        )
                        else "unknown"
                    ),
                },
            )
            session.add(asset)
            session.flush()
            if isinstance(character_design, dict):
                revision_id = uuid.UUID(str(character_design["revisionId"]))
                revision = self._required(session, CharacterDesignRevision, revision_id, lock=True)
                if revision.production_run_id != batch.production_run_id:
                    raise WorkflowConflictError("角色设计版本与图片生成批次项目不一致")
                session.add(
                    CharacterDesignAsset(
                        id=uuid.uuid4(),
                        character_design_revision_id=revision.id,
                        asset_id=asset.id,
                        slot=str(character_design["slot"]),
                        candidate_index=stored_candidate_index,
                        semantic_role=str(character_design["semanticRole"]),
                        selected=False,
                    )
                )
                session.flush()
                if not validation_only:
                    bindings = list(
                        session.scalars(
                            select(CharacterDesignAsset).where(
                                CharacterDesignAsset.character_design_revision_id == revision.id
                            )
                        )
                    )
                    expected = int(character_design["candidateCount"])
                    counts = {
                        slot: sum(1 for item in bindings if item.slot == slot)
                        for slot in ("child", "cat", "pair_scale")
                    }
                    if all(count >= expected for count in counts.values()):
                        revision.status = "awaiting_review"
            output_ids = [*batch.output_asset_ids_json, str(asset.id)]
            batch.output_asset_ids_json = output_ids
            if len(output_ids) >= batch.candidate_count:
                batch.status = "awaiting_review"
            node = self._required(session, CanvasGraphNode, batch.canvas_node_id, lock=True)
            candidates = [
                *list(node.data_json.get("candidates", [])),
                {
                    "id": str(asset.id),
                    "assetId": str(asset.id),
                    "title": (
                        f"验证候选 {stored_candidate_index}"
                        if validation_only
                        else f"候选 {stored_candidate_index}"
                    ),
                    "thumbnailUrl": f"/api/v1/assets/{asset.id}/content",
                    "promptId": str(prompt.id),
                    "status": "validation_candidate" if validation_only else "candidate",
                    "validationOnly": validation_only,
                    "selected": False,
                    "inputHash": batch.reference_manifest_hash,
                },
            ]
            if validation_only:
                node.data_json = {
                    **node.data_json,
                    "candidates": sorted(
                        candidates,
                        key=lambda item: int(str(item["title"]).split()[-1]),
                    ),
                }
            else:
                node.status = batch.status
                node.data_json = {
                    **node.data_json,
                    "status": batch.status,
                    "candidates": sorted(
                        candidates,
                        key=lambda item: int(str(item["title"]).split()[-1]),
                    ),
                }
            prompt.status = "succeeded"
            prompt.raw_response_json = {"url": provider_url, "model": provider_model}
            prompt.structured_response_json = {"assetId": str(asset.id)}
            prompt.output_hash = landed.sha256
            prompt.completed_at = datetime.now(UTC)
            self._record_event(
                session,
                batch.production_run_id,
                "generation_candidate_ready",
                {
                    "batchId": str(batch.id),
                    "assetId": str(asset.id),
                    "candidateIndex": stored_candidate_index,
                    "validationOnly": validation_only,
                },
            )
            return str(asset.id)

    def video_candidate_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("media:video:batch:"):
                raise ValueError("workflow step is not a video batch candidate")
            batch = self._required(
                session,
                MediaGenerationBatch,
                uuid.UUID(str(step.input_snapshot_json["batchId"])),
            )
            if batch.media_kind != "video" or batch.production_run_id != step.production_run_id:
                raise WorkflowConflictError("视频候选任务与生成批次项目不一致")
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None or prompt.status != "pending":
                raise WorkflowConflictError("视频候选缺少待执行的精确 Prompt")

            input_document = batch.input_json
            config = input_document.get("generationConfig", input_document)
            if not isinstance(config, dict):
                raise ValueError("视频生成配置必须是对象")
            included_references = [
                item
                for item in batch.reference_manifest_json
                if isinstance(item, dict) and item.get("providerIncluded") is True
            ]
            if not batch.reference_manifest_hash:
                raise WorkflowConflictError(
                    "历史视频任务没有可证明的冻结输入哈希，不能按新链路提交"
                )
            if included_references and any(
                item.get("evidenceLevel") != "frozen" for item in included_references
            ):
                raise WorkflowConflictError(
                    "历史视频任务无法证明 Provider 引用顺序，不能在未知顺序下提交"
                )
            included_references.sort(key=lambda value: int(value.get("ordinal") or 0))
            assets: list[Asset] = []
            for item in included_references:
                asset = self._required(session, Asset, uuid.UUID(str(item["assetId"])))
                if asset.production_run_id not in {None, batch.production_run_id}:
                    raise WorkflowConflictError("视频生成引用不属于当前项目")
                if asset.media_type != "image":
                    raise ValueError("首期视频生成只接受图片引用")
                if item.get("sha256") and asset.sha256 != item.get("sha256"):
                    raise WorkflowConflictError("视频生成冻结引用的 SHA 已变化")
                assets.append(asset)
            sources = tuple(
                MediaSource(
                    asset_id=asset.id,
                    semantic_key=asset.semantic_key or f"asset:{asset.id}",
                    media_type=asset.media_type,
                    sha256=asset.sha256,
                    metadata=asset.metadata_json,
                )
                for asset in assets
            )
            mode = str(config.get("mode", "text_to_video"))
            if mode == "image_to_video" and len(sources) != 1:
                raise ValueError("当前 Ark 首帧视频模式必须且只能提交一张实际引用图")
            if mode == "first_last_frame" and len(sources) != 2:
                raise ValueError("当前 Ark 首尾帧模式必须且只能提交两张有序控制图")
            resolution = str(config.get("resolution", "720p")).lower()
            duration_seconds = int(config.get("durationSeconds", 8))
            plan: VideoInputPlan = build_shot_input_plan(
                resolution=resolution,
                duration_seconds=duration_seconds,
                anchor=sources[0] if mode in {"image_to_video", "first_last_frame"} else None,
                last_frame=sources[1] if mode == "first_last_frame" else None,
                references=(() if mode in {"image_to_video", "first_last_frame"} else sources),
            )
            return {
                "batchId": str(batch.id),
                "candidateIndex": int(step.input_snapshot_json["candidateIndex"]),
                "prompt": prompt.final_prompt or prompt.prompt_text,
                "inputPlan": plan,
                "inputSources": tuple(
                    _resolve_asset_path(asset.storage_key, self._asset_root) for asset in assets
                ),
                "providerTaskId": step.provider_task_id,
                "referenceManifest": tuple(included_references),
                "inputHash": batch.reference_manifest_hash,
            }

    def record_video_candidate_submission(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            if not step.operation_key.startswith("media:video:batch:"):
                raise ValueError("workflow step is not a video batch candidate")
            bound = session.scalar(
                select(WorkflowStep.id).where(
                    WorkflowStep.provider_task_id == provider_task_id,
                    WorkflowStep.id != step.id,
                )
            )
            if bound is not None:
                raise WorkflowConflictError("供应商任务号已绑定其他付费意图")
            step.provider_task_id = provider_task_id
            step.status = (
                StepStatus.RUNNING.value
                if provider_status == "running"
                else StepStatus.QUEUED.value
            )
            step.submitted_at = step.submitted_at or datetime.now(UTC)
            step.progress_json = {
                **dict(step.progress_json or {}),
                "providerStatus": provider_status,
                "message": (
                    "Provider 已开始生成，任务会继续跟踪"
                    if provider_status == "running"
                    else "Provider 已接收任务，正在排队"
                ),
            }

    def complete_video_candidate(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
        last_frame_landed: LandedAsset | None = None,
        last_frame_provider_url: str | None = None,
    ) -> str:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_candidate",
                )
            )
            if existing is not None:
                return str(existing.id)
            batch = self._required(
                session,
                MediaGenerationBatch,
                uuid.UUID(str(step.input_snapshot_json["batchId"])),
                lock=True,
            )
            candidate_index = int(step.input_snapshot_json["candidateIndex"])
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None:
                raise WorkflowConflictError("视频候选缺少 Prompt 审计记录")
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=batch.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=batch.canvas_node_id,
                role="video_candidate",
                semantic_key=f"batch:{batch.id}:candidate:{candidate_index}",
                scope="canvas_node",
                status="candidate",
                media_type="video",
                storage_key=_asset_storage_key(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json={
                    "batchId": str(batch.id),
                    "candidateIndex": candidate_index,
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "providerTaskId": step.provider_task_id,
                    "promptId": str(prompt.id),
                    "generationInputHash": batch.reference_manifest_hash,
                    "referenceManifest": batch.reference_manifest_json,
                    "providerOrderEvidence": "frozen",
                },
            )
            session.add(asset)
            session.flush()
            tail_frame: Asset | None = None
            if last_frame_landed is not None and last_frame_provider_url:
                tail_frame = Asset(
                    id=uuid.uuid4(),
                    production_run_id=batch.production_run_id,
                    producing_step_id=step.id,
                    canvas_node_id=batch.canvas_node_id,
                    role="shot_tail_frame",
                    semantic_key=f"batch:{batch.id}:candidate:{candidate_index}:tail",
                    scope="canvas_node",
                    status="approved",
                    media_type="image",
                    storage_key=_asset_storage_key(last_frame_landed.path, self._asset_root),
                    sha256=last_frame_landed.sha256,
                    byte_size=last_frame_landed.byte_size,
                    metadata_json={
                        "batchId": str(batch.id),
                        "candidateIndex": candidate_index,
                        "sourceVideoAssetId": str(asset.id),
                        "sourceVideoSha256": landed.sha256,
                        "providerReturned": True,
                        "providerUrl": last_frame_provider_url,
                        "providerModel": provider_model,
                        "providerTaskId": step.provider_task_id,
                        "generationInputHash": batch.reference_manifest_hash,
                    },
                )
                session.add(tail_frame)
                session.flush()
            output_ids = [*batch.output_asset_ids_json, str(asset.id)]
            batch.output_asset_ids_json = output_ids
            if len(output_ids) >= batch.candidate_count:
                batch.status = "awaiting_review"
            node = self._required(session, CanvasGraphNode, batch.canvas_node_id, lock=True)
            node.status = batch.status
            node.data_json = {
                **node.data_json,
                "status": batch.status,
                "candidates": [
                    *list(node.data_json.get("candidates", [])),
                    {
                        "id": str(asset.id),
                        "assetId": str(asset.id),
                        "title": f"候选 {candidate_index}",
                        "contentUrl": f"/api/v1/assets/{asset.id}/content",
                        "promptId": str(prompt.id),
                        "status": "candidate",
                        "tailFrameAssetId": (
                            str(tail_frame.id) if tail_frame is not None else None
                        ),
                    },
                ],
            }
            prompt.status = "succeeded"
            prompt.raw_response_json = {
                "url": provider_url,
                "model": provider_model,
                "providerTaskId": step.provider_task_id,
                "lastFrameUrl": last_frame_provider_url,
            }
            prompt.structured_response_json = {
                "assetId": str(asset.id),
                "tailFrameAssetId": (
                    str(tail_frame.id) if tail_frame is not None else None
                ),
            }
            prompt.output_hash = landed.sha256
            prompt.completed_at = datetime.now(UTC)
            self._record_event(
                session,
                batch.production_run_id,
                "generation_candidate_ready",
                {
                    "batchId": str(batch.id),
                    "assetId": str(asset.id),
                    "candidateIndex": candidate_index,
                    "mediaKind": "video",
                    "tailFrameAssetId": (
                        str(tail_frame.id) if tail_frame is not None else None
                    ),
                },
            )
            return str(asset.id)

    def video_edit_anchor_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("video:edit-anchor:"):
                raise ValueError("workflow step is not a video edit control anchor")
            recipe = self._required(
                session,
                VideoEditRecipe,
                uuid.UUID(str(step.input_snapshot_json["recipeId"])),
            )
            source = self._required(session, Asset, recipe.source_asset_id)
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None or prompt.status != "pending":
                raise WorkflowConflictError("控制锚点缺少待执行的精确 Prompt")
            references = list(
                session.scalars(
                    select(Asset)
                    .join(VideoEditReference, VideoEditReference.asset_id == Asset.id)
                    .where(VideoEditReference.recipe_id == recipe.id)
                    .order_by(VideoEditReference.ordinal)
                )
            )
            return {
                "recipeId": str(recipe.id),
                "boundary": str(step.input_snapshot_json["boundary"]),
                "timestampMs": int(step.input_snapshot_json["timestampMs"]),
                "source": _stored_asset(source, self._asset_root),
                "prompt": prompt.final_prompt or prompt.prompt_text,
                "referencePaths": tuple(
                    _resolve_asset_path(asset.storage_key, self._asset_root) for asset in references
                ),
            }

    def record_control_anchor_input(
        self,
        step_id: uuid.UUID,
        *,
        boundary_frame: LandedAsset,
    ) -> str:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_edit_boundary",
                )
            )
            if existing is not None:
                return str(existing.id)
            recipe = self._required(
                session,
                VideoEditRecipe,
                uuid.UUID(str(step.input_snapshot_json["recipeId"])),
            )
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=recipe.canvas_node_id,
                role="video_edit_boundary",
                semantic_key=(
                    f"video-edit:{recipe.id}:boundary:{step.input_snapshot_json['boundary']}"
                ),
                scope="canvas_node",
                status="ready",
                media_type="image",
                storage_key=_asset_storage_key(boundary_frame.path, self._asset_root),
                sha256=boundary_frame.sha256,
                byte_size=boundary_frame.byte_size,
                metadata_json={
                    "recipeId": str(recipe.id),
                    "boundary": step.input_snapshot_json["boundary"],
                    "timestampMs": step.input_snapshot_json["timestampMs"],
                },
            )
            session.add(asset)
            session.flush()
            snapshot = {
                **step.input_snapshot_json,
                "boundaryAssetId": str(asset.id),
                "boundarySha256": asset.sha256,
            }
            step.input_snapshot_json = snapshot
            step.input_hash = _json_hash(snapshot)
            step.request_hash = step.input_hash
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is not None:
                prompt.input_snapshot_json = snapshot
                prompt.provider_request_json = snapshot
                prompt.input_hash = step.input_hash
            return str(asset.id)

    def complete_control_anchor(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
    ) -> str:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_edit_control_anchor",
                )
            )
            if existing is not None:
                return str(existing.id)
            recipe = self._required(
                session,
                VideoEditRecipe,
                uuid.UUID(str(step.input_snapshot_json["recipeId"])),
            )
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None:
                raise WorkflowConflictError("控制锚点缺少 Prompt 审计记录")
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=recipe.canvas_node_id,
                role="video_edit_control_anchor",
                semantic_key=(
                    f"video-edit:{recipe.id}:control-anchor:{step.input_snapshot_json['boundary']}"
                ),
                scope="canvas_node",
                status="ready",
                media_type="image",
                storage_key=_asset_storage_key(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json={
                    "recipeId": str(recipe.id),
                    "boundary": step.input_snapshot_json["boundary"],
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "promptId": str(prompt.id),
                },
            )
            session.add(asset)
            session.flush()
            prompt.status = "succeeded"
            prompt.raw_response_json = {"url": provider_url, "model": provider_model}
            prompt.structured_response_json = {"assetId": str(asset.id)}
            prompt.output_hash = landed.sha256
            prompt.completed_at = datetime.now(UTC)
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_control_anchor_ready",
                {
                    "recipeId": str(recipe.id),
                    "boundary": step.input_snapshot_json["boundary"],
                    "assetId": str(asset.id),
                },
            )
            return str(asset.id)

    def video_edit_video_work(self, step_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            step = self._required(session, WorkflowStep, step_id)
            if not step.operation_key.startswith("video:edit-recipe:"):
                raise ValueError("workflow step is not a video edit recipe")
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None:
                raise WorkflowConflictError("视频重编缺少精确 Prompt")
            recipe_id = prompt.business_object_id
            if recipe_id is None:
                raise WorkflowConflictError("视频重编 Prompt 未绑定配方")
            recipe = self._required(session, VideoEditRecipe, recipe_id)
            source = self._required(session, Asset, recipe.source_asset_id)
            anchor_step_ids = [
                uuid.UUID(str(item))
                for item in step.input_snapshot_json.get("controlAnchorStepIds", [])
            ]
            anchors = (
                list(
                    session.scalars(
                        select(Asset).where(
                            Asset.producing_step_id.in_(anchor_step_ids),
                            Asset.role == "video_edit_control_anchor",
                        )
                    )
                )
                if anchor_step_ids
                else []
            )
            anchors.sort(key=lambda item: 0 if item.metadata_json.get("boundary") == "start" else 1)
            references = list(
                session.scalars(
                    select(Asset)
                    .join(VideoEditReference, VideoEditReference.asset_id == Asset.id)
                    .where(VideoEditReference.recipe_id == recipe.id)
                    .order_by(VideoEditReference.ordinal)
                )
            )
            return {
                "ready": len(anchors) == len(anchor_step_ids),
                "recipeId": str(recipe.id),
                "prompt": prompt.final_prompt or prompt.prompt_text,
                "source": _stored_asset(source, self._asset_root),
                "sourceInput": (
                    source.metadata_json.get("providerUrl")
                    if isinstance(source.metadata_json.get("providerUrl"), str)
                    else _resolve_asset_path(source.storage_key, self._asset_root)
                ),
                "anchors": tuple(_stored_asset(item, self._asset_root) for item in anchors),
                "references": tuple(
                    _stored_asset(item, self._asset_root) for item in references
                ),
                "startMs": recipe.start_ms,
                "endMs": recipe.end_ms,
                "providerTaskId": step.provider_task_id,
                "compilation": recipe.compilation_json or {},
            }

    def record_video_edit_inputs(
        self,
        step_id: uuid.UUID,
        *,
        input_plan: dict[str, Any],
        input_assets: tuple[StoredAsset, ...],
    ) -> None:
        """Freeze the exact provider bindings before the paid video request."""

        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None or prompt.business_object_id is None:
                raise WorkflowConflictError("视频重编缺少配方 Prompt")
            recipe = self._required(
                session,
                VideoEditRecipe,
                prompt.business_object_id,
            )
            persisted_assets: list[Asset] = []
            for index, source in enumerate(input_assets):
                asset = session.get(Asset, source.id)
                if asset is None:
                    if index == 0 or source.media_type != "image":
                        raise WorkflowConflictError("视频重编输入素材不存在")
                    landed_path = source.require_path()
                    asset = Asset(
                        id=source.id,
                        production_run_id=recipe.production_run_id,
                        producing_step_id=step.id,
                        canvas_node_id=recipe.canvas_node_id,
                        role="video_edit_boundary",
                        semantic_key=source.semantic_key,
                        scope="canvas_node",
                        status="ready",
                        media_type="image",
                        storage_key=_asset_storage_key(landed_path, self._asset_root),
                        sha256=source.sha256,
                        byte_size=landed_path.stat().st_size,
                        metadata_json={
                            **source.metadata,
                            "recipeId": str(recipe.id),
                            "derivedFromAssetId": str(recipe.source_asset_id),
                        },
                    )
                    session.add(asset)
                persisted_assets.append(asset)
            request = {
                **step.input_snapshot_json,
                "providerInputPlan": input_plan,
                "providerInputAssets": [
                    {
                        "assetId": str(asset.id),
                        "semanticKey": asset.semantic_key,
                        "mediaType": asset.media_type,
                        "sha256": asset.sha256,
                    }
                    for asset in persisted_assets
                ],
            }
            request_hash = _json_hash(request)
            step.input_snapshot_json = request
            step.input_hash = request_hash
            step.request_hash = request_hash
            prompt.provider_request_json = request
            prompt.input_snapshot_json = request
            prompt.input_hash = request_hash
            attempt = session.scalar(
                select(GenerationAttempt).where(GenerationAttempt.workflow_step_id == step.id)
            )
            if attempt is not None:
                attempt.request_json = request

    def record_failed_video_edit_candidate(
        self,
        step_id: uuid.UUID,
        *,
        provider_segment: LandedAsset,
        provider_url: str,
        provider_model: str,
        replacement_qc: dict[str, Any],
    ) -> str:
        """Retain a provider result that failed technical QC without approving it."""

        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_edit_failed_provider_segment",
                )
            )
            if existing is not None:
                return str(existing.id)
            prompt = session.scalar(select(PromptRecord).where(PromptRecord.step_id == step.id))
            if prompt is None or prompt.business_object_id is None:
                raise WorkflowConflictError("视频重编缺少配方 Prompt")
            recipe = self._required(
                session,
                VideoEditRecipe,
                prompt.business_object_id,
                lock=True,
            )
            node = CanvasGraphNode(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                node_type=CanvasNodeType.VIDEO_SEGMENT.value,
                object_type="asset",
                status="failed_qc",
                data_json={},
            )
            session.add(node)
            session.flush()
            asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=node.id,
                role="video_edit_failed_provider_segment",
                semantic_key=f"video-edit:{recipe.id}:failed-provider-segment",
                scope="canvas_node",
                status="rejected",
                media_type="video",
                storage_key=_asset_storage_key(provider_segment.path, self._asset_root),
                sha256=provider_segment.sha256,
                byte_size=provider_segment.byte_size,
                metadata_json={
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "qc": replacement_qc,
                    "recipeId": str(recipe.id),
                    "rejectionReason": "technical_qc_failed",
                },
            )
            session.add(asset)
            session.flush()
            node.object_id = asset.id
            node.data_json = {
                "title": f"失败候选 · Revision {recipe.revision}",
                "assetId": str(asset.id),
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "status": "failed_qc",
                "qc": replacement_qc,
            }
            recipe.status = "failed_qc"
            edit_node = self._required(
                session,
                CanvasGraphNode,
                recipe.canvas_node_id,
                lock=True,
            )
            edit_node.status = "failed_qc"
            edit_node.data_json = {
                **edit_node.data_json,
                "status": "failed_qc",
                "failedCandidateAssetId": str(asset.id),
            }
            session.add(
                _graph_edge(
                    recipe.production_run_id,
                    CanvasConnection(
                        sourceNodeId=recipe.canvas_node_id,
                        sourceNodeType=CanvasNodeType.VIDEO_EDIT,
                        sourcePort="edit_recipe",
                        targetNodeId=node.id,
                        targetNodeType=CanvasNodeType.VIDEO_SEGMENT,
                        targetPort="edit_recipe",
                    ),
                )
            )
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_candidate_failed_qc",
                {
                    "recipeId": str(recipe.id),
                    "assetId": str(asset.id),
                    "qc": replacement_qc,
                },
            )
            return str(asset.id)

    def record_video_edit_submission(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            bound = session.scalar(
                select(WorkflowStep.id).where(
                    WorkflowStep.provider_task_id == provider_task_id,
                    WorkflowStep.id != step.id,
                )
            )
            if bound is not None:
                raise WorkflowConflictError("供应商任务号已绑定其他付费意图")
            step.provider_task_id = provider_task_id
            step.status = (
                StepStatus.RUNNING.value
                if provider_status == "running"
                else StepStatus.QUEUED.value
            )
            step.submitted_at = step.submitted_at or datetime.now(UTC)
            attempt = session.scalar(
                select(GenerationAttempt).where(GenerationAttempt.workflow_step_id == step.id)
            )
            if attempt is not None:
                attempt.provider_task_id = provider_task_id
                attempt.status = step.status

    def complete_video_edit(
        self,
        step_id: uuid.UUID,
        *,
        provider_segment: LandedAsset,
        full_video: LandedAsset,
        provider_url: str,
        provider_model: str,
        replacement_qc: dict[str, Any],
        full_qc: dict[str, Any],
    ) -> dict[str, str]:
        with self._sessions.begin() as session:
            step = self._required(session, WorkflowStep, step_id, lock=True)
            existing = session.scalar(
                select(Asset).where(
                    Asset.producing_step_id == step.id,
                    Asset.role == "video_edit_full_version",
                )
            )
            if existing is not None:
                return {"assetId": str(existing.id)}
            prompt = session.scalar(
                select(PromptRecord).where(PromptRecord.step_id == step.id).with_for_update()
            )
            if prompt is None or prompt.business_object_id is None:
                raise WorkflowConflictError("视频重编缺少配方 Prompt")
            recipe = self._required(session, VideoEditRecipe, prompt.business_object_id, lock=True)
            segment_asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=recipe.canvas_node_id,
                role="video_edit_provider_segment",
                semantic_key=f"video-edit:{recipe.id}:provider-segment",
                scope="canvas_node",
                status="candidate",
                media_type="video",
                storage_key=_asset_storage_key(provider_segment.path, self._asset_root),
                sha256=provider_segment.sha256,
                byte_size=provider_segment.byte_size,
                metadata_json={
                    "providerUrl": provider_url,
                    "providerModel": provider_model,
                    "qc": replacement_qc,
                    "recipeId": str(recipe.id),
                },
            )
            session.add(segment_asset)
            session.flush()
            output_node = CanvasGraphNode(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                node_type=CanvasNodeType.VIDEO_SEGMENT.value,
                object_type="asset",
                status="candidate",
                data_json={},
            )
            session.add(output_node)
            session.flush()
            full_asset = Asset(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                producing_step_id=step.id,
                canvas_node_id=output_node.id,
                role="video_edit_full_version",
                semantic_key=f"video-edit:{recipe.id}:full-version",
                scope="canvas_node",
                status="candidate",
                media_type="video",
                storage_key=_asset_storage_key(full_video.path, self._asset_root),
                sha256=full_video.sha256,
                byte_size=full_video.byte_size,
                metadata_json={
                    "providerSegmentAssetId": str(segment_asset.id),
                    "sourceAssetId": str(recipe.source_asset_id),
                    "recipeId": str(recipe.id),
                    "rangeEdit": {
                        "startMs": recipe.start_ms,
                        "endMs": recipe.end_ms,
                    },
                    "qc": full_qc,
                    "promptId": str(prompt.id),
                },
            )
            session.add(full_asset)
            session.flush()
            output_node.object_id = full_asset.id
            output_node.data_json = {
                "title": f"重编完整视频 · Revision {recipe.revision}",
                "assetId": str(full_asset.id),
                "providerSegmentAssetId": str(segment_asset.id),
                "contentUrl": f"/api/v1/assets/{full_asset.id}/content",
                "durationMs": full_qc.get("durationMs"),
                "promptId": str(prompt.id),
                "status": "candidate",
            }
            session.add(
                _graph_edge(
                    recipe.production_run_id,
                    CanvasConnection(
                        sourceNodeId=recipe.canvas_node_id,
                        sourceNodeType=CanvasNodeType.VIDEO_EDIT,
                        sourcePort="edit_recipe",
                        targetNodeId=output_node.id,
                        targetNodeType=CanvasNodeType.VIDEO_SEGMENT,
                        targetPort="edit_recipe",
                    ),
                )
            )
            review_node = session.scalar(
                select(CanvasGraphNode).where(
                    CanvasGraphNode.production_run_id == recipe.production_run_id,
                    CanvasGraphNode.node_type == CanvasNodeType.REVIEW.value,
                )
            )
            if review_node is not None:
                session.add(
                    _graph_edge(
                        recipe.production_run_id,
                        CanvasConnection(
                            sourceNodeId=output_node.id,
                            sourceNodeType=CanvasNodeType.VIDEO_SEGMENT,
                            sourcePort="video_asset",
                            targetNodeId=review_node.id,
                            targetNodeType=CanvasNodeType.REVIEW,
                            targetPort="video_asset",
                        ),
                    )
                )
            recipe.status = "awaiting_review"
            edit_node = self._required(session, CanvasGraphNode, recipe.canvas_node_id, lock=True)
            edit_node.status = "awaiting_review"
            edit_node.data_json = {
                **edit_node.data_json,
                "status": "awaiting_review",
                "outputAssetId": str(full_asset.id),
            }
            prompt.status = "succeeded"
            prompt.raw_response_json = {
                "providerUrl": provider_url,
                "providerModel": provider_model,
            }
            prompt.structured_response_json = {
                "providerSegmentAssetId": str(segment_asset.id),
                "fullVideoAssetId": str(full_asset.id),
            }
            prompt.output_hash = full_video.sha256
            prompt.completed_at = datetime.now(UTC)
            attempt = session.scalar(
                select(GenerationAttempt).where(GenerationAttempt.workflow_step_id == step.id)
            )
            if attempt is not None:
                attempt.status = "awaiting_review"
                attempt.response_json = prompt.structured_response_json
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_candidate_ready",
                {
                    "recipeId": str(recipe.id),
                    "providerSegmentAssetId": str(segment_asset.id),
                    "fullVideoAssetId": str(full_asset.id),
                },
            )
            return {
                "assetId": str(full_asset.id),
                "providerSegmentAssetId": str(segment_asset.id),
            }

    def instantiate_template(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        template_key = CanvasTemplateKey(payload.template_key)
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            existing_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(CanvasGraphNode)
                    .where(CanvasGraphNode.production_run_id == project_id)
                )
                or 0
            )
            if existing_count and project.canvas_template_key != template_key.value:
                raise WorkflowConflictError("项目已经包含业务节点；请复制项目后再切换画布模板")
            project.canvas_v2_enabled = True
            project.universal_canvas_enabled = True
            project.product_ad_template_enabled = template_key is CanvasTemplateKey.PRODUCT_AD
            project.video_edit_v2_enabled = True
            project.canvas_template_key = template_key.value
            created_node_ids: list[str] = []
            if template_key is CanvasTemplateKey.PRODUCT_AD and existing_count == 0:
                definitions = (
                    (
                        "product-reference",
                        CanvasNodeType.REFERENCE_ASSET,
                        "reference_slot",
                        {"title": "产品主体 / 包装参考", "status": "awaiting_input"},
                    ),
                    (
                        "talent-style-reference",
                        CanvasNodeType.REFERENCE_ASSET,
                        "reference_slot",
                        {"title": "模特 / 风格参考", "status": "awaiting_input"},
                    ),
                    (
                        "image-batch",
                        CanvasNodeType.GENERATION_BATCH,
                        "media_generation_batch",
                        {
                            "title": "产品图生成批次",
                            "candidateCount": 4,
                            "candidateRange": [1, 8],
                            "status": "awaiting_input",
                        },
                    ),
                    (
                        "video-generation",
                        CanvasNodeType.VIDEO_GENERATION,
                        "video_generation_stage",
                        {"title": "图生视频", "status": "awaiting_selection"},
                    ),
                    (
                        "video-edit",
                        CanvasNodeType.VIDEO_EDIT,
                        "video_edit_stage",
                        {"title": "视频局部重编", "status": "awaiting_video"},
                    ),
                    (
                        "review",
                        CanvasNodeType.REVIEW,
                        "asset_review_stage",
                        {"title": "人工审核", "status": "awaiting_asset"},
                    ),
                    (
                        "timeline",
                        CanvasNodeType.TIMELINE,
                        "timeline_stage",
                        {"title": "时间线", "status": "awaiting_approval"},
                    ),
                )
                nodes: dict[str, CanvasGraphNode] = {}
                for key, node_type, object_type, data in definitions:
                    node = CanvasGraphNode(
                        id=uuid.uuid5(project_id, f"product-ad:{key}"),
                        production_run_id=project_id,
                        node_type=node_type.value,
                        object_type=object_type,
                        status=str(data["status"]),
                        data_json=data,
                    )
                    session.add(node)
                    nodes[key] = node
                    created_node_ids.append(str(node.id))
                session.flush()
                for source_key in ("product-reference", "talent-style-reference"):
                    edge = CanvasConnection(
                        sourceNodeId=nodes[source_key].id,
                        sourceNodeType=CanvasNodeType.REFERENCE_ASSET,
                        sourcePort="media_reference[]",
                        targetNodeId=nodes["image-batch"].id,
                        targetNodeType=CanvasNodeType.GENERATION_BATCH,
                        targetPort="media_reference[]",
                    )
                    session.add(_graph_edge(project_id, edge))
            event = self._record_event(
                session,
                project_id,
                "template_instantiated",
                {"templateKey": template_key.value, "nodeIds": created_node_ids},
            )
            return {
                "projectId": str(project_id),
                "templateKey": template_key.value,
                "graphVersion": 1,
                "eventId": str(event.id),
                "nodeIds": created_node_ids,
            }

    def create_canvas_node(self, project_id: uuid.UUID, payload: Any) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id, lock=True)
            project.canvas_v2_enabled = True
            node = CanvasGraphNode(
                id=uuid.uuid4(),
                production_run_id=project_id,
                node_type=payload.node_type.value,
                object_type=payload.object_type,
                object_id=payload.object_id,
                status=str(payload.data.get("status", "ready")),
                data_json=payload.data,
            )
            session.add(node)
            session.flush()
            if payload.object_type == "asset" and payload.object_id is not None:
                asset = self._required(session, Asset, payload.object_id, lock=True)
                if asset.production_run_id != project_id and asset.scope != "canon":
                    raise ValueError("画布资产节点引用的素材不属于当前项目")
                expected_media_type = {
                    CanvasNodeType.IMAGE_ASSET: "image",
                    CanvasNodeType.VIDEO_ASSET: "video",
                    CanvasNodeType.REFERENCE_ASSET: "image",
                }.get(payload.node_type)
                if expected_media_type and asset.media_type != expected_media_type:
                    raise ValueError("画布资产节点类型与实际素材类型不一致")
                asset.canvas_node_id = node.id
                if asset.scope != "canon":
                    asset.scope = "canvas_node"
            self._record_event(
                session,
                project_id,
                "canvas_node_created",
                {"node": _graph_node_json(node)},
            )
            return _graph_node_json(node)

    def bind_canvas_node_assets(
        self,
        node_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            node = self._required(session, CanvasGraphNode, node_id, lock=True)
            if node.node_type != CanvasNodeType.REFERENCE_ASSET.value:
                raise ValueError("素材绑定命令只适用于 ReferenceAssetNode")
            if node.revision != expected_revision:
                raise WorkflowConflictError(
                    f"参考素材节点版本冲突：当前 {node.revision}，提交 {expected_revision}"
                )
            requested = {item.asset_id: item for item in payload.bindings}
            if len(requested) != len(payload.bindings):
                raise ValueError("同一素材不能在一个参考节点中重复绑定")
            current_assets = list(
                session.scalars(select(Asset).where(Asset.canvas_node_id == node.id))
            )
            affected_node_ids = {node.id}
            for asset in current_assets:
                if asset.id not in requested:
                    asset.canvas_node_id = None
                    if asset.scope == "canvas_node":
                        asset.scope = "project"
            for binding in payload.bindings:
                asset = self._required(session, Asset, binding.asset_id, lock=True)
                if asset.production_run_id != node.production_run_id and asset.scope != "canon":
                    raise ValueError("参考素材不属于当前项目")
                if asset.canvas_node_id not in {None, node.id}:
                    if not payload.allow_move:
                        raise WorkflowConflictError(
                            f"素材已绑定到节点 {asset.canvas_node_id}；确认移动后重试"
                        )
                    affected_node_ids.add(asset.canvas_node_id)
                asset.canvas_node_id = node.id
                if asset.scope != "canon":
                    asset.scope = "canvas_node"
                asset.metadata_json = {
                    **asset.metadata_json,
                    "canvasSemanticRole": binding.semantic_role,
                }
            session.flush()
            for affected_node_id in affected_node_ids:
                affected = session.get(CanvasGraphNode, affected_node_id)
                if affected is not None:
                    self._refresh_asset_node_projection(session, affected)
                    if affected.id != node.id:
                        affected.revision += 1
                        self._mark_graph_downstream_stale(session, affected.id)
            node.revision += 1
            self._mark_graph_downstream_stale(session, node.id)
            self._record_event(
                session,
                node.production_run_id,
                "canvas_asset_bindings_changed",
                {
                    "nodeId": str(node.id),
                    "revision": node.revision,
                    "assetIds": [str(asset_id) for asset_id in requested],
                },
            )
            session.flush()
            return _graph_node_json(node)

    def create_canvas_edge(
        self, project_id: uuid.UUID, payload: CanvasConnection
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            source = self._required(session, CanvasGraphNode, payload.source_node_id)
            target = self._required(session, CanvasGraphNode, payload.target_node_id)
            if source.production_run_id != project_id or target.production_run_id != project_id:
                raise ValueError("画布业务连接的两个节点必须属于同一项目")
            if (
                source.node_type != payload.source_node_type.value
                or target.node_type != payload.target_node_type.value
            ):
                raise WorkflowConflictError("节点类型已变化，请刷新画布后重新连接")
            existing = session.scalar(
                select(CanvasGraphEdge).where(
                    CanvasGraphEdge.source_node_id == source.id,
                    CanvasGraphEdge.source_port == payload.source_port.value,
                    CanvasGraphEdge.target_node_id == target.id,
                    CanvasGraphEdge.target_port == payload.target_port.value,
                )
            )
            if existing is not None:
                return _graph_edge_json(existing, source, target)
            edge = _graph_edge(project_id, payload)
            session.add(edge)
            if target.status not in {"awaiting_input", "draft"}:
                target.status = "stale"
            target.revision += 1
            session.flush()
            self._record_event(
                session,
                project_id,
                "canvas_edge_created",
                {"edge": _graph_edge_json(edge, source, target)},
            )
            return _graph_edge_json(edge, source, target)

    def delete_canvas_edge(self, edge_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions.begin() as session:
            edge = self._required(session, CanvasGraphEdge, edge_id, lock=True)
            disconnect_enabled, disabled_reason = _edge_disconnect_policy(
                source_port=edge.source_port,
                target_port=edge.target_port,
                relation_type=edge.relation_type,
            )
            if not disconnect_enabled:
                raise WorkflowConflictError(
                    disabled_reason or "该连接由系统工作流管理，不能直接剪断"
                )
            target = self._required(session, CanvasGraphNode, edge.target_node_id, lock=True)
            project_id = edge.production_run_id
            target.status = "stale"
            target.revision += 1
            session.delete(edge)
            self._record_event(
                session,
                project_id,
                "canvas_edge_deleted",
                {"edgeId": str(edge_id), "targetNodeId": str(target.id)},
            )
            return {"id": str(edge_id), "deleted": True, "targetStatus": target.status}

    def create_generation_batch(self, payload: Any) -> dict[str, Any]:
        """Persist one media batch without ever exposing a dangling step foreign key."""

        with self._sessions.begin() as session:
            plan, existing = self._plan_generation_batch(session, payload)
            if existing is not None:
                return _generation_batch_json(session, existing)
            if plan is None:  # pragma: no cover - guarded by the mutually exclusive return
                raise RuntimeError("generation batch planning returned no result")
            self._persist_generation_batch_plans(session, (plan,))
            return _generation_batch_json(session, plan.batch)

    def create_generation_batches(
        self,
        payloads: Sequence[Any],
        *,
        parent_step_id: uuid.UUID,
    ) -> tuple[dict[str, Any], ...]:
        """Atomically schedule one explicit character-design generation stage.

        The method owns validation, persistence ordering, parent/child linkage, and
        rollback semantics.  Worker steps are flushed before batches reference them;
        therefore a worker can only observe the complete requested stage.  Canon v4
        uses ``child + cat`` followed by ``pair_scale``; legacy runs may still submit
        all three slots in one stage.
        """

        normalized = tuple(payloads)
        if not normalized:
            raise ValueError("角色设计阶段至少需要一个图片批次")
        slots: list[str] = []
        revision_ids: set[str] = set()
        validation_modes: set[bool] = set()
        project_ids = {payload.project_id for payload in normalized}
        for payload in normalized:
            character_design = payload.input.get("characterDesign")
            if not isinstance(character_design, dict):
                raise ValueError("角色设计批次缺少 characterDesign 输入快照")
            slots.append(str(character_design.get("slot") or ""))
            revision_ids.add(str(character_design.get("revisionId") or ""))
            validation_modes.add(character_design.get("validationOnly") is True)
        slot_set = set(slots)
        allowed_slot_sets = {
            frozenset({"child", "cat"}),
            frozenset({"pair_scale"}),
            frozenset({"child", "cat", "pair_scale"}),
        }
        if len(slot_set) != len(slots) or frozenset(slot_set) not in allowed_slot_sets:
            raise ValueError(
                "角色设计阶段必须是 child+cat、pair_scale，或历史兼容的完整三槽位"
            )
        if (
            len(project_ids) != 1
            or len(revision_ids) != 1
            or len(validation_modes) != 1
            or "" in revision_ids
        ):
            raise ValueError("同一角色设计阶段的批次必须属于同一项目和角色设计版本")

        dispatch_context = {
            "parentStepId": str(parent_step_id),
            "projectId": str(normalized[0].project_id),
            "characterDesignRevisionId": next(iter(revision_ids)),
            "slots": slots,
        }
        try:
            with self._sessions.begin() as session:
                parent = self._required(session, WorkflowStep, parent_step_id, lock=True)
                if parent.production_run_id != normalized[0].project_id:
                    raise ValueError("角色设计父任务与图片批次不属于同一项目")
                if parent.operation_key not in {
                    "recipe:character_design",
                    "recipe:character_design_validation",
                    "canvas-group:run",
                }:
                    raise ValueError("角色设计批次只能绑定角色设计或整组执行父任务")
                validation_only = next(iter(validation_modes))
                if validation_only and slot_set != {"child", "cat", "pair_scale"}:
                    raise ValueError("引用顺序验证必须完整覆盖三个角色设计槽位")
                if (
                    parent.operation_key == "recipe:character_design_validation"
                ) != validation_only:
                    raise ValueError("角色设计父任务与三槽位批次的验证模式不一致")
                recipe_instance_id = parent.input_snapshot_json.get("recipeInstanceId")
                if not recipe_instance_id:
                    raise ValueError("角色设计父任务缺少 recipeInstanceId 输入快照")
                revision = self._required(
                    session,
                    CharacterDesignRevision,
                    uuid.UUID(next(iter(revision_ids))),
                    lock=True,
                )
                if revision.production_run_id != normalized[
                    0
                ].project_id or revision.production_recipe_instance_id != uuid.UUID(
                    str(recipe_instance_id)
                ):
                    raise ValueError("角色设计版本、配方和父任务上下文不一致")

                plans: list[_GenerationBatchPlan] = []
                existing_batches: list[MediaGenerationBatch] = []
                for payload in normalized:
                    plan, existing = self._plan_generation_batch(session, payload)
                    if existing is not None:
                        self._validate_existing_character_batch(session, existing, payload)
                        existing_batches.append(existing)
                    elif plan is not None:
                        plans.append(plan)
                if existing_batches and plans:
                    for batch in existing_batches:
                        self._require_pristine_generation_batch(session, batch)
                if plans:
                    self._persist_generation_batch_plans(session, tuple(plans))

                ordered_batches: list[MediaGenerationBatch] = []
                by_key = {batch.idempotency_key: batch for batch in existing_batches}
                by_key.update({plan.batch.idempotency_key: plan.batch for plan in plans})
                child_steps: list[WorkflowStep] = []
                for payload in normalized:
                    batch = by_key[payload.idempotency_key]
                    ordered_batches.append(batch)
                    child_steps.extend(self._generation_batch_steps(session, batch))
                self._link_generation_children(session, parent, child_steps)
                return tuple(_generation_batch_json(session, batch) for batch in ordered_batches)
        except RecipeDispatchError:
            raise
        except SQLAlchemyError as exc:
            raise RecipeDispatchError(
                "角色设计图片批次调度失败；数据库事务已回滚，供应商尚未提交",
                context=dispatch_context,
            ) from exc

    def _plan_generation_batch(
        self,
        session: Session,
        payload: Any,
    ) -> tuple[_GenerationBatchPlan | None, MediaGenerationBatch | None]:
        project = self._require_project(session, payload.project_id, lock=True)
        project.universal_canvas_enabled = True
        if project.canvas_template_key == CanvasTemplateKey.PRODUCT_AD.value:
            project.product_ad_template_enabled = True
        node = self._required(session, CanvasGraphNode, payload.canvas_node_id, lock=True)
        allowed_node_types = {
            "image": {
                CanvasNodeType.GENERATION_BATCH.value,
                CanvasNodeType.IMAGE_GENERATION.value,
                CanvasNodeType.CHARACTER_DESIGN.value,
            },
            "video": {CanvasNodeType.VIDEO_GENERATION.value},
        }
        if (
            node.production_run_id != payload.project_id
            or node.node_type not in allowed_node_types[payload.media_kind]
        ):
            raise ValueError(f"{payload.media_kind}生成批次与画布节点类型不匹配")
        existing = session.scalar(
            select(MediaGenerationBatch).where(
                MediaGenerationBatch.idempotency_key == payload.idempotency_key
            )
        )
        if existing is not None:
            return None, existing

        preview = self._compile_generation_batch_input(session, node, payload)
        expected_input_hash = payload.expected_input_hash
        if not expected_input_hash:
            raise WorkflowConflictError("付费生成必须先读取服务端输入预览并提交 expectedInputHash")
        if expected_input_hash != preview["inputHash"]:
            raise WorkflowConflictError("生成引用、Prompt、模型或费用已变化，请重新预览后确认")
        if preview["blockers"]:
            raise WorkflowConflictError("；".join(preview["blockers"]))
        frozen_input = {
            **payload.input,
            "referenceManifest": preview["references"],
            "referenceManifestHash": preview["inputHash"],
            "inputHash": preview["inputHash"],
        }
        batch = MediaGenerationBatch(
            id=uuid.uuid4(),
            production_run_id=payload.project_id,
            canvas_node_id=node.id,
            media_kind=payload.media_kind,
            candidate_count=payload.candidate_count,
            provider=payload.provider,
            model=payload.model,
            status="pending",
            idempotency_key=payload.idempotency_key,
            input_json=frozen_input,
            reference_manifest_json=preview["references"],
            reference_manifest_hash=preview["inputHash"],
        )
        input_document = payload.model_dump(mode="json", by_alias=True)
        input_document["input"] = frozen_input
        base_prompt = str(
            payload.input.get("prompt") or json.dumps(payload.input, ensure_ascii=False)
        )
        steps: list[WorkflowStep] = []
        prompts: list[PromptRecord] = []
        for candidate_index in range(1, payload.candidate_count + 1):
            candidate_snapshot = {
                **input_document,
                "batchId": str(batch.id),
                "candidateIndex": candidate_index,
            }
            input_hash = _json_hash(candidate_snapshot)
            step = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=payload.project_id,
                kind=(
                    StepKind.IMAGE.value if payload.media_kind == "image" else StepKind.VIDEO.value
                ),
                status=StepStatus.PENDING.value,
                attempt=1,
                operation_key=(
                    f"media:{payload.media_kind}:batch:{batch.id}:candidate:{candidate_index}"
                ),
                idempotency_key=hashlib.sha256(
                    f"{payload.idempotency_key}:{candidate_index}".encode("utf-8")
                ).hexdigest(),
                provider=payload.provider,
                model=payload.model,
                input_hash=input_hash,
                request_hash=input_hash,
                input_snapshot_json=candidate_snapshot,
            )
            final_prompt = (
                f"{base_prompt}\n\n候选 {candidate_index}/{payload.candidate_count}："
                "保持全部主体身份锚点，提供与其他候选不同的构图或机位。"
            )
            steps.append(step)
            prompts.append(
                PromptRecord(
                    id=uuid.uuid4(),
                    step_id=step.id,
                    purpose=(
                        PromptPurpose.IMAGE.value
                        if payload.media_kind == "image"
                        else PromptPurpose.VIDEO.value
                    ),
                    model=payload.model,
                    prompt_text=final_prompt,
                    sha256=hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
                    call_purpose=f"{payload.media_kind}_generation_candidate_{candidate_index}",
                    node_id=node.id,
                    business_object_type="media_generation_batch",
                    business_object_id=batch.id,
                    template_name=f"media.{payload.media_kind}.candidate.v1",
                    template_version="1.0.0",
                    user_prompt=base_prompt,
                    final_prompt=final_prompt,
                    provider_request_json={
                        "candidateIndex": candidate_index,
                        "candidateCount": payload.candidate_count,
                        "input": frozen_input,
                        "referenceManifest": preview["references"],
                        "referenceManifestHash": preview["inputHash"],
                    },
                    input_snapshot_json=candidate_snapshot,
                    status="pending",
                    input_hash=input_hash,
                )
            )
        batch.workflow_step_id = steps[0].id
        return _GenerationBatchPlan(payload, node, batch, tuple(steps), tuple(prompts)), None

    def _compile_generation_batch_input(
        self,
        session: Session,
        node: CanvasGraphNode,
        payload: Any,
    ) -> dict[str, Any]:
        character_design = payload.input.get("characterDesign")
        if isinstance(character_design, dict):
            capability = self._resolve_provider_capability(
                session,
                provider=str(payload.provider),
                model=str(payload.model),
                media_kind="image",
            )
            if capability is None:
                raise WorkflowConflictError("角色设计图片模型没有启用的 Provider 能力档案")
            capabilities = dict(capability["capabilities"])
            current_capability_revision = str(
                capabilities.get("capabilityRevision")
                or capability["updatedAt"]
                or capability["id"]
            )
            expected_capability_revision = str(payload.input.get("capabilityRevision") or "")
            if expected_capability_revision != current_capability_revision:
                raise WorkflowConflictError(
                    "角色设计模型能力 revision 已变化，请重新查看输入与费用"
                )
            references = list(payload.input.get("referenceManifest") or [])
            if not references:
                raise WorkflowConflictError("角色设计批次缺少服务端有序引用清单")
            asset_ids = [uuid.UUID(str(item["assetId"])) for item in references]
            assets = {
                asset.id: asset
                for asset in session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
            }
            blockers: list[str] = []
            compiled: list[dict[str, Any]] = []
            seen_ids: set[uuid.UUID] = set()
            seen_hashes: set[str] = set()
            for item in references:
                asset_id = uuid.UUID(str(item["assetId"]))
                asset = assets.get(asset_id)
                if asset is None:
                    blockers.append(f"角色设计引用 {asset_id} 不存在")
                    continue
                path = _resolve_asset_path(asset.storage_key, self._asset_root)
                if (
                    asset.production_run_id not in {None, payload.project_id}
                    or asset.media_type != "image"
                    or asset.status not in {"approved", "ready"}
                    or not path.is_file()
                ):
                    blockers.append(f"角色设计引用 {asset_id} 不可用或尚未批准")
                    continue
                if asset.id in seen_ids or asset.sha256 in seen_hashes:
                    continue
                seen_ids.add(asset.id)
                seen_hashes.add(asset.sha256)
                ordinal = len(compiled) + 1
                compiled.append(
                    {
                        **item,
                        "assetId": str(asset.id),
                        "ordinal": ordinal,
                        "sha256": asset.sha256,
                        "providerIncluded": True,
                        "providerSlot": f"reference_image_{ordinal}",
                        "omissionReason": None,
                        "evidenceLevel": "frozen",
                    }
                )
            exact_input = character_design_generation_input(
                provider=str(payload.provider),
                model=str(payload.model),
                candidate_count=payload.candidate_count,
                prompt=str(payload.input.get("prompt") or ""),
                references=compiled,
                capability_revision=current_capability_revision,
            )
            input_image_cost = capabilities.get("inputImageCostMicros")
            output_image_cost = capabilities.get("outputImageCostMicros")
            if input_image_cost is None or output_image_cost is None:
                estimated_cost_micros = None
            else:
                estimated_cost_micros = payload.candidate_count * (
                    int(output_image_cost) + len(compiled) * int(input_image_cost)
                )
            return {
                "provider": payload.provider,
                "model": payload.model,
                "mode": "all_reference",
                "capabilityRevision": current_capability_revision,
                "prompt": str(payload.input.get("prompt") or ""),
                "references": compiled,
                "warnings": [],
                "blockers": blockers,
                "estimatedCostMicros": estimated_cost_micros,
                "inputHash": generation_input_hash(exact_input),
            }

        config_document = payload.input.get("generationConfig")
        if not isinstance(config_document, dict):
            raise WorkflowConflictError("通用生成批次缺少 generationConfig 输入快照")
        normalized = {
            **config_document,
            "provider": payload.provider,
            "model": payload.model,
            "candidateCount": payload.candidate_count,
            "draftPrompt": str(
                payload.input.get("prompt") or config_document.get("draftPrompt") or ""
            ),
        }
        config = NodeGenerationConfigDraft.model_validate(normalized)
        return self._compile_node_generation_input(session, node, config)

    def _persist_generation_batch_plans(
        self,
        session: Session,
        plans: Sequence[_GenerationBatchPlan],
    ) -> None:
        # The ordering is a database invariant: referenced workflow rows must exist
        # before media_generation_batches is flushed.
        all_steps = [step for plan in plans for step in plan.steps]
        session.add_all(all_steps)
        session.flush()
        session.add_all([plan.batch for plan in plans])
        session.flush()
        session.add_all([prompt for plan in plans for prompt in plan.prompts])
        for plan in plans:
            candidate_step_ids = [str(step.id) for step in plan.steps]
            character_design = plan.payload.input.get("characterDesign")
            validation_only = bool(
                isinstance(character_design, dict)
                and character_design.get("validationOnly") is True
            )
            if not validation_only:
                plan.node.status = "pending"
                plan.node.data_json = {
                    **plan.node.data_json,
                    "batchId": str(plan.batch.id),
                    "candidateCount": plan.payload.candidate_count,
                    "candidateStepIds": candidate_step_ids,
                    "status": "pending",
                }
            self._record_event(
                session,
                plan.payload.project_id,
                "generation_batch_queued",
                {
                    "batchId": str(plan.batch.id),
                    "nodeId": str(plan.node.id),
                    "validationOnly": validation_only,
                },
            )

    @staticmethod
    def _generation_batch_steps(
        session: Session,
        batch: MediaGenerationBatch,
    ) -> list[WorkflowStep]:
        prefix = f"media:{batch.media_kind}:batch:{batch.id}:candidate:"
        return list(
            session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.operation_key.startswith(prefix))
                .order_by(WorkflowStep.operation_key)
            )
        )

    def _validate_existing_character_batch(
        self,
        session: Session,
        batch: MediaGenerationBatch,
        payload: Any,
    ) -> None:
        if (
            batch.production_run_id != payload.project_id
            or batch.canvas_node_id != payload.canvas_node_id
            or batch.media_kind != payload.media_kind
            or batch.candidate_count != payload.candidate_count
            or batch.provider != payload.provider
            or batch.model != payload.model
            or (
                payload.expected_input_hash
                and batch.reference_manifest_hash
                and batch.reference_manifest_hash != payload.expected_input_hash
            )
            or any(
                batch.input_json.get(key) != value
                for key, value in payload.input.items()
                if key != "referenceManifest"
            )
        ):
            raise WorkflowConflictError("已有角色设计批次与当前固定输入不一致")
        steps = self._generation_batch_steps(session, batch)
        if len(steps) != payload.candidate_count:
            self._require_pristine_generation_batch(session, batch)
            raise WorkflowConflictError(
                "已有角色设计批次缺少完整候选子任务；未检测到 Provider 证据，"
                "但该遗留状态不能被静默复用"
            )

    def _require_pristine_generation_batch(
        self,
        session: Session,
        batch: MediaGenerationBatch,
    ) -> None:
        steps = self._generation_batch_steps(session, batch)
        step_ids = [step.id for step in steps]
        has_attempt = bool(
            step_ids
            and session.scalar(
                select(GenerationAttempt.id)
                .where(GenerationAttempt.workflow_step_id.in_(step_ids))
                .limit(1)
            )
        )
        has_provider_evidence = any(
            step.provider_task_id or step.submitted_at is not None for step in steps
        )
        has_output_asset = bool(batch.output_asset_ids_json) or bool(
            step_ids
            and session.scalar(
                select(Asset.id).where(Asset.producing_step_id.in_(step_ids)).limit(1)
            )
        )
        if has_attempt or has_provider_evidence or has_output_asset:
            raise WorkflowConflictError(
                "角色设计批次已存在 Provider 提交、生成尝试或输出资产，不能自动补齐"
            )

    @staticmethod
    def _link_generation_children(
        session: Session,
        parent: WorkflowStep,
        children: Sequence[WorkflowStep],
    ) -> None:
        ordered_ids = list(dict.fromkeys(str(child.id) for child in children))
        for child in children:
            child.input_snapshot_json = {
                **dict(child.input_snapshot_json or {}),
                "parentStepId": str(parent.id),
            }
        parent.progress_json = {
            **dict(parent.progress_json or {}),
            "childStepIds": ordered_ids,
            "message": (
                "三个引用顺序验证批次已原子调度，等待子任务顺序执行"
                if parent.operation_key == "recipe:character_design_validation"
                else "三个角色设计图片批次已原子调度，等待子任务顺序执行"
            ),
        }

    def create_video_edit_recipe(self, payload: VideoEditRecipeDraft) -> dict[str, Any]:
        with self._sessions.begin() as session:
            project = self._require_project(session, payload.project_id, lock=True)
            project.universal_canvas_enabled = True
            project.video_edit_v2_enabled = True
            source = self._required(session, Asset, payload.source_asset_id)
            self._validate_video_source(source, payload.project_id)
            node = CanvasGraphNode(
                id=uuid.uuid4(),
                production_run_id=payload.project_id,
                node_type=CanvasNodeType.VIDEO_EDIT.value,
                object_type="video_edit_recipe",
                status="draft",
                data_json={"title": "视频局部重编", "sourceAssetId": str(source.id)},
            )
            session.add(node)
            session.flush()
            recipe = self._add_video_edit_revision(
                session,
                node=node,
                draft=payload,
                revision=1,
                parent_recipe_id=None,
            )
            node.object_id = recipe.id
            if source.canvas_node_id is not None:
                source_node = self._required(session, CanvasGraphNode, source.canvas_node_id)
                if source_node.node_type == CanvasNodeType.VIDEO_ASSET.value:
                    connection = CanvasConnection(
                        sourceNodeId=source_node.id,
                        sourceNodeType=CanvasNodeType.VIDEO_ASSET,
                        sourcePort="video_asset",
                        targetNodeId=node.id,
                        targetNodeType=CanvasNodeType.VIDEO_EDIT,
                        targetPort="video_asset",
                    )
                    session.add(_graph_edge(payload.project_id, connection))
            self._record_event(
                session,
                payload.project_id,
                "video_edit_recipe_created",
                {"recipeId": str(recipe.id), "nodeId": str(node.id)},
            )
            return _video_edit_recipe_json(session, recipe)

    def update_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            current = self._required(session, VideoEditRecipe, recipe_id, lock=True)
            if current.revision != expected_revision:
                raise WorkflowConflictError(
                    f"视频重编版本冲突：当前 {current.revision}，提交 {expected_revision}"
                )
            document = _video_edit_draft_json(session, current)
            document.update(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
            draft = VideoEditRecipeDraft.model_validate(document)
            next_recipe = self._create_video_edit_revision(session, current, draft)
            return _video_edit_recipe_json(session, next_recipe)

    def replace_video_edit_annotations(
        self,
        recipe_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            current = self._required(session, VideoEditRecipe, recipe_id, lock=True)
            if current.revision != expected_revision:
                raise WorkflowConflictError(
                    f"视频重编版本冲突：当前 {current.revision}，提交 {expected_revision}"
                )
            document = _video_edit_draft_json(session, current)
            document["annotations"] = [
                item.model_dump(mode="json", by_alias=True) for item in payload.annotations
            ]
            draft = VideoEditRecipeDraft.model_validate(document)
            next_recipe = self._create_video_edit_revision(session, current, draft)
            return _video_edit_recipe_json(session, next_recipe)

    def compile_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        capability: ProviderEditCapability,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            recipe = self._required(session, VideoEditRecipe, recipe_id, lock=True)
            if recipe.status not in {"draft", "compiled"}:
                raise WorkflowConflictError("只有草稿或已编译配方可以重新编译")
            draft = VideoEditRecipeDraft.model_validate(_video_edit_draft_json(session, recipe))
            plan = compile_video_edit_plan(draft, capability)
            capability_row = session.scalar(
                select(ProviderCapability).where(
                    ProviderCapability.provider == capability.provider,
                    ProviderCapability.model == capability.model,
                    ProviderCapability.media_kind == "video_edit",
                )
            )
            if capability_row is None:
                capability_row = ProviderCapability(
                    id=uuid.uuid4(),
                    provider=capability.provider,
                    model=capability.model,
                    media_kind="video_edit",
                    capabilities_json=capability.model_dump(mode="json", by_alias=True),
                    active=True,
                )
                session.add(capability_row)
            else:
                capability_row.media_kind = "video_edit"
                capability_row.capabilities_json = capability.model_dump(mode="json", by_alias=True)
                capability_row.active = True
            references = list(
                session.scalars(
                    select(VideoEditReference)
                    .where(VideoEditReference.recipe_id == recipe.id)
                    .order_by(VideoEditReference.ordinal)
                )
            )
            reference_assets = {
                asset.id: asset
                for asset in session.scalars(
                    select(Asset).where(
                        Asset.id.in_([reference.asset_id for reference in references])
                    )
                )
            }
            reference_stage = "video" if plan.mode == "direct" else "control_anchor"
            actual_references = [
                {
                    "assetId": str(reference.asset_id),
                    "subjectRevisionId": reference_assets[reference.asset_id].metadata_json.get(
                        "subjectRevisionId"
                    ),
                    "semanticRole": reference.semantic_role,
                    "providerIncluded": True,
                    "providerSlot": (
                        f"reference_image_{reference.ordinal + 2}"
                        if plan.mode == "direct"
                        else f"{reference_stage}_reference_{reference.ordinal}"
                    ),
                    "omissionReason": None,
                }
                for reference in references
            ]
            compilation_input_hash = _json_hash(
                {
                    "recipe": draft.model_dump(mode="json", by_alias=True),
                    "provider": capability.provider,
                    "model": capability.model,
                    "actualReferences": actual_references,
                }
            )
            recipe.compilation_json = {
                **plan.model_dump(mode="json", by_alias=True),
                "provider": capability.provider,
                "model": capability.model,
                "providerCapabilityId": str(capability_row.id),
                "inputHash": compilation_input_hash,
                "actualReferences": actual_references,
            }
            recipe.estimated_cost_micros = plan.estimated_cost_micros
            recipe.status = "compiled"
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_recipe_compiled",
                {"recipeId": str(recipe.id), "plan": recipe.compilation_json},
            )
            return {"recipeId": str(recipe.id), **recipe.compilation_json}

    def submit_video_edit_recipe(
        self,
        recipe_id: uuid.UUID,
        payload: Any,
        *,
        image_provider: str,
        image_model: str,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            recipe = self._required(session, VideoEditRecipe, recipe_id, lock=True)
            if recipe.status == "queued":
                existing_step = session.scalar(
                    select(WorkflowStep).where(
                        WorkflowStep.production_run_id == recipe.production_run_id,
                        WorkflowStep.operation_key == f"video:edit-recipe:{recipe.id}",
                    )
                )
                if existing_step is not None:
                    return {
                        "recipeId": str(recipe.id),
                        "jobId": str(existing_step.id),
                        "status": "queued",
                        "idempotencyKey": payload.idempotency_key,
                    }
            if recipe.status != "compiled" or recipe.compilation_json is None:
                raise WorkflowConflictError("提交前必须先编译视频重编能力计划")
            if recipe.estimated_cost_micros != payload.accept_estimated_cost_micros:
                raise WorkflowConflictError("预计费用已经变化，请重新确认后提交")
            draft_document = _video_edit_draft_json(session, recipe)
            anchor_step_ids: list[str] = []
            if recipe.compilation_json["mode"] == "two_stage":
                for boundary, timestamp_ms in (
                    ("start", recipe.start_ms),
                    ("end", recipe.end_ms),
                ):
                    anchor_snapshot = {
                        "recipeId": str(recipe.id),
                        "boundary": boundary,
                        "timestampMs": timestamp_ms,
                        "sourceAssetId": str(recipe.source_asset_id),
                        "referenceAssetIds": draft_document["referenceAssetIds"],
                        "annotations": draft_document["annotations"],
                    }
                    anchor_hash = _json_hash(anchor_snapshot)
                    anchor_step = WorkflowStep(
                        id=uuid.uuid4(),
                        production_run_id=recipe.production_run_id,
                        kind=StepKind.IMAGE.value,
                        status=StepStatus.PENDING.value,
                        attempt=1,
                        operation_key=f"video:edit-anchor:{recipe.id}:{boundary}",
                        idempotency_key=hashlib.sha256(
                            f"{payload.idempotency_key}:anchor:{boundary}".encode()
                        ).hexdigest(),
                        provider=image_provider,
                        model=image_model,
                        input_hash=anchor_hash,
                        request_hash=anchor_hash,
                        input_snapshot_json=anchor_snapshot,
                    )
                    session.add(anchor_step)
                    anchor_prompt = _control_anchor_prompt(draft_document, boundary)
                    session.add(
                        PromptRecord(
                            id=uuid.uuid4(),
                            step_id=anchor_step.id,
                            purpose=PromptPurpose.IMAGE.value,
                            model=image_model,
                            prompt_text=anchor_prompt,
                            sha256=hashlib.sha256(anchor_prompt.encode("utf-8")).hexdigest(),
                            call_purpose=f"video_edit_control_anchor_{boundary}",
                            node_id=recipe.canvas_node_id,
                            business_object_type="video_edit_recipe",
                            business_object_id=recipe.id,
                            template_name="video.edit.control-anchor.v1",
                            template_version="1.0.0",
                            user_prompt=recipe.instruction,
                            final_prompt=anchor_prompt,
                            provider_request_json=anchor_snapshot,
                            input_snapshot_json=anchor_snapshot,
                            status="pending",
                            input_hash=anchor_hash,
                        )
                    )
                    anchor_step_ids.append(str(anchor_step.id))
            request_document = {
                "recipe": draft_document,
                "compilation": recipe.compilation_json,
                "controlAnchorStepIds": anchor_step_ids,
            }
            input_hash = _json_hash(request_document)
            step_key = hashlib.sha256(payload.idempotency_key.encode("utf-8")).hexdigest()
            existing = session.scalar(
                select(WorkflowStep).where(WorkflowStep.idempotency_key == step_key)
            )
            if existing is not None:
                return {
                    "recipeId": str(recipe.id),
                    "jobId": str(existing.id),
                    "status": existing.status,
                    "idempotencyKey": payload.idempotency_key,
                }
            provider = str(recipe.compilation_json["provider"])
            model = str(recipe.compilation_json["model"])
            step = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=recipe.production_run_id,
                kind=StepKind.VIDEO.value,
                status=StepStatus.PENDING.value,
                attempt=1,
                operation_key=f"video:edit-recipe:{recipe.id}",
                idempotency_key=step_key,
                provider=provider,
                model=model,
                input_hash=input_hash,
                request_hash=input_hash,
                input_snapshot_json=request_document,
            )
            session.add(step)
            final_prompt = _video_edit_prompt(draft_document)
            session.add(
                PromptRecord(
                    id=uuid.uuid4(),
                    step_id=step.id,
                    purpose=PromptPurpose.VIDEO.value,
                    model=model,
                    prompt_text=final_prompt,
                    sha256=hashlib.sha256(final_prompt.encode("utf-8")).hexdigest(),
                    call_purpose="video_edit_recipe",
                    node_id=recipe.canvas_node_id,
                    business_object_type="video_edit_recipe",
                    business_object_id=recipe.id,
                    template_name="video.edit.recipe.v2",
                    template_version="2.0.0",
                    user_prompt=recipe.instruction,
                    final_prompt=final_prompt,
                    provider_request_json=request_document,
                    input_snapshot_json=request_document,
                    parameters_json={
                        "startMs": recipe.start_ms,
                        "endMs": recipe.end_ms,
                        "mode": recipe.compilation_json["mode"],
                    },
                    cost_micros=recipe.estimated_cost_micros,
                    status="pending",
                    input_hash=input_hash,
                )
            )
            session.add(
                GenerationAttempt(
                    id=uuid.uuid4(),
                    production_run_id=recipe.production_run_id,
                    workflow_step_id=step.id,
                    business_object_type="video_edit_recipe",
                    business_object_id=recipe.id,
                    idempotency_key=payload.idempotency_key,
                    provider=provider,
                    model=model,
                    status="pending",
                    request_json=request_document,
                    cost_micros=recipe.estimated_cost_micros,
                )
            )
            recipe.status = "queued"
            node = self._required(session, CanvasGraphNode, recipe.canvas_node_id, lock=True)
            node.status = "pending"
            node.data_json = {**node.data_json, "recipeId": str(recipe.id), "status": "pending"}
            self._record_event(
                session,
                recipe.production_run_id,
                "video_edit_recipe_queued",
                {"recipeId": str(recipe.id), "stepId": str(step.id)},
            )
            return {
                "recipeId": str(recipe.id),
                "jobId": str(step.id),
                "status": "queued",
                "idempotencyKey": payload.idempotency_key,
            }

    def events(
        self, project_id: uuid.UUID, *, after_sequence: int = 0
    ) -> tuple[dict[str, Any], ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        with self._sessions() as session:
            self._require_project(session, project_id)
            query = (
                select(CanvasEvent)
                .where(
                    CanvasEvent.production_run_id == project_id,
                    CanvasEvent.sequence > after_sequence,
                )
                .order_by(CanvasEvent.sequence)
                .limit(200)
            )
            return tuple(
                {
                    "id": str(event.id),
                    "sequence": event.sequence,
                    "type": event.event_type,
                    "data": event.data_json,
                    "createdAt": event.created_at.isoformat(),
                }
                for event in session.scalars(query)
            )

    def get_canvas(self, project_id: uuid.UUID) -> dict[str, Any]:
        with self._sessions() as session:
            project = self._require_project(session, project_id)
            layout = session.scalar(
                select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
            )
            brief = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
                .limit(1)
            )
            subjects = _preferred_subject_rows(
                session,
                list(
                    session.scalars(
                        select(Subject)
                        .where(
                            Subject.production_run_id == project_id,
                            Subject.status != "archived",
                        )
                        .order_by(Subject.created_at)
                    )
                ),
            )
            stories = list(
                session.scalars(
                    select(StoryRevisionRecord)
                    .where(StoryRevisionRecord.production_run_id == project_id)
                    .order_by(StoryRevisionRecord.revision)
                )
            )
            story_events = list(
                session.scalars(
                    select(StoryEventCandidateRecord)
                    .where(StoryEventCandidateRecord.production_run_id == project_id)
                    .order_by(
                        StoryEventCandidateRecord.created_at,
                        StoryEventCandidateRecord.candidate_index,
                    )
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
            beats = list(
                session.scalars(
                    select(ShotBeat)
                    .join(Scene, Scene.id == ShotBeat.scene_id)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
                        ShotBeat.status != "superseded",
                    )
                    .order_by(Scene.sort_order, ShotBeat.sort_order)
                )
            )
            approved_story = next(
                (
                    story
                    for story in reversed(stories)
                    if story.status == StoryRevisionStatus.APPROVED.value
                ),
                None,
            )
            active_storyboard_revision = (
                None
                if approved_story is None
                else session.scalar(
                    select(StoryboardRevision)
                    .where(
                        StoryboardRevision.production_run_id == project_id,
                        StoryboardRevision.story_revision_id == approved_story.id,
                        StoryboardRevision.status != StoryboardRevisionStatus.SUPERSEDED.value,
                    )
                    .order_by(StoryboardRevision.revision.desc())
                    .limit(1)
                )
            )
            has_active_storyboard = bool(
                active_storyboard_revision is not None
                and any(
                    beat.storyboard_revision_id == active_storyboard_revision.id
                    for beat in beats
                )
            )
            workflow_steps = list(
                session.scalars(
                    select(WorkflowStep)
                    .where(WorkflowStep.production_run_id == project_id)
                    .order_by(WorkflowStep.created_at.desc(), WorkflowStep.id.desc())
                    .limit(300)
                )
            )
            storyboard_prompt = (
                None
                if approved_story is None or has_active_storyboard
                else session.scalar(
                    select(PromptRecord)
                    .join(WorkflowStep, WorkflowStep.id == PromptRecord.step_id)
                    .where(
                        WorkflowStep.production_run_id == project_id,
                        PromptRecord.call_purpose == "storyboard_director",
                        PromptRecord.business_object_type == "story_revision",
                        PromptRecord.business_object_id == approved_story.id,
                        PromptRecord.status == "succeeded",
                    )
                    .order_by(
                        PromptRecord.completed_at.desc(),
                        PromptRecord.created_at.desc(),
                    )
                    .limit(1)
                )
            )
            result = _canvas_json(
                project_id,
                layout=layout,
                brief=brief,
                subjects=subjects,
                story_events=story_events,
                stories=stories,
                scenes=scenes,
                beats=beats,
                session=session,
                enabled=project.canvas_v2_enabled,
                storyboard_prompt=storyboard_prompt,
                include_narrative_projection=(
                    project.canvas_template_key != CanvasTemplateKey.PRODUCT_AD.value
                    or any((brief, stories, scenes, beats))
                ),
            )
            graph_nodes = list(
                session.scalars(
                    select(CanvasGraphNode)
                    .where(
                        CanvasGraphNode.production_run_id == project_id,
                        CanvasGraphNode.node_type != CanvasNodeType.RECIPE_GROUP.value,
                    )
                    .order_by(CanvasGraphNode.created_at, CanvasGraphNode.id)
                )
            )
            stable_brief_node_id = creative_brief_canvas_node_id(project_id)
            preferred_subject_node_ids = {subject.id for subject in subjects}
            graph_nodes = [
                node
                for node in graph_nodes
                if (
                    node.node_type != CanvasNodeType.BRIEF.value
                    or node.id == stable_brief_node_id
                )
                and (
                    node.node_type != CanvasNodeType.SUBJECT.value
                    or node.id in preferred_subject_node_ids
                )
            ]
            graph_edges = list(
                session.scalars(
                    select(CanvasGraphEdge)
                    .where(CanvasGraphEdge.production_run_id == project_id)
                    .order_by(CanvasGraphEdge.created_at, CanvasGraphEdge.id)
                )
            )
            legacy_assets = list(
                session.scalars(
                    select(Asset)
                    .where(
                        Asset.production_run_id == project_id,
                        Asset.canvas_node_id.is_(None),
                        Asset.media_type.in_(("image", "video")),
                    )
                    .order_by(Asset.created_at)
                )
            )
            _merge_universal_graph(
                result,
                layout=layout,
                graph_nodes=graph_nodes,
                graph_edges=graph_edges,
                legacy_assets=legacy_assets,
            )
            _apply_shot_reference_edge_projection(result, beats)
            groups = list(
                session.scalars(
                    select(CanvasGroup)
                    .where(
                        CanvasGroup.production_run_id == project_id,
                        CanvasGroup.lifecycle_status == "active",
                    )
                    .order_by(CanvasGroup.created_at, CanvasGroup.id)
                )
            )
            group_ids = [group.id for group in groups]
            group_members = (
                []
                if not group_ids
                else list(
                    session.scalars(
                        select(CanvasGroupMember)
                        .where(CanvasGroupMember.group_id.in_(group_ids))
                        .order_by(
                            CanvasGroupMember.group_id,
                            CanvasGroupMember.sort_order,
                        )
                    )
                )
            )
            group_states = {
                group.id: _recipe_canvas_group_state(session, group)
                for group in groups
                if group.production_recipe_instance_id is not None
            }
            _merge_canvas_groups(
                result,
                groups=groups,
                members=group_members,
                states=group_states,
            )
            _apply_canvas_workflow_step_projection(result, workflow_steps)
            _apply_canvas_layout_hints(
                result,
                positioned_node_ids={
                    str(item.get("nodeId"))
                    for item in (layout.nodes_json if layout else [])
                    if item.get("nodeId")
                },
            )
            active_archive_ids = {
                str(node_id)
                for node_id in session.scalars(
                    select(CanvasNodeArchive.canvas_node_id).where(
                        CanvasNodeArchive.production_run_id == project_id,
                        CanvasNodeArchive.restored_at.is_(None),
                    )
                )
            }
            _apply_canvas_node_archive_projection(result, active_archive_ids)
            result["templateKey"] = project.canvas_template_key
            result["featureFlags"] = {
                "UNIVERSAL_CANVAS": project.universal_canvas_enabled,
                "PRODUCT_AD_TEMPLATE": project.product_ad_template_enabled,
                "VIDEO_EDIT_V2": project.video_edit_v2_enabled,
            }
            return result

    def get_workspace_shell(self, project_id: uuid.UUID) -> dict[str, Any]:
        """Build the three product workspaces directly from persisted production facts."""

        with self._sessions() as session:
            project = self._require_project(session, project_id)
            facts = _workspace_facts(session, project)
            modules = _workspace_modules(facts)
            latest_task = facts["tasks"][0] if facts["tasks"] else None
            return {
                "project": {
                    "id": str(project.id),
                    "title": project.title,
                    "status": project.status,
                    "updatedAt": project.updated_at.isoformat(),
                },
                "modules": modules,
                "recommendedModuleId": _recommended_workspace_module(modules),
                "activeTaskSummary": {
                    "activeCount": sum(
                        task.status in _DIRECTOR_ACTIVE_TASK_STATUSES
                        for task in facts["tasks"]
                    ),
                    "attentionCount": sum(
                        task.status in _DIRECTOR_ATTENTION_TASK_STATUSES
                        for task in facts["tasks"]
                    ),
                    "latestTaskId": None if latest_task is None else str(latest_task.id),
                    "latestStatus": None if latest_task is None else latest_task.status,
                },
            }

    def get_script_workspace(self, project_id: uuid.UUID) -> dict[str, Any]:
        """Read editable story documents without loading any graph projection."""

        with self._sessions() as session:
            self._require_project(session, project_id)
            recipe_instance = session.scalar(
                select(ProductionRecipeInstance)
                .where(
                    ProductionRecipeInstance.production_run_id == project_id,
                    ProductionRecipeInstance.lifecycle_status == "active",
                )
                .order_by(ProductionRecipeInstance.revision.desc())
                .limit(1)
            )
            brief = session.scalar(
                select(StoryBriefRecord)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc(), StoryBriefRecord.id.desc())
                .limit(1)
            )
            rows = list(
                session.scalars(
                    select(StoryRevisionRecord)
                    .where(StoryRevisionRecord.production_run_id == project_id)
                    .order_by(StoryRevisionRecord.revision.desc(), StoryRevisionRecord.id.desc())
                    .limit(5)
                )
            )
            current = next(
                (
                    row
                    for row in rows
                    if row.status == StoryRevisionStatus.APPROVED.value
                ),
                None,
            )
            if current is None:
                current = session.scalar(
                    select(StoryRevisionRecord)
                    .where(
                        StoryRevisionRecord.production_run_id == project_id,
                        StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
                    )
                    .order_by(StoryRevisionRecord.revision.desc())
                    .limit(1)
                )
                if current is not None and all(row.id != current.id for row in rows):
                    rows = [current, *rows[:4]]
            prompt_ids = {
                row.candidate_prompt_id for row in rows if row.candidate_prompt_id is not None
            }
            prompts = {
                prompt.id: prompt
                for prompt in (
                    session.scalars(
                        select(PromptRecord).where(PromptRecord.id.in_(prompt_ids))
                    )
                    if prompt_ids
                    else ()
                )
            }
            documents = []
            for row in rows:
                document = _story_json(row, None, prompts.get(row.candidate_prompt_id))
                documents.append(
                    {
                        "id": document["id"],
                        "title": document["title"],
                        "body": document["body"],
                        "summary": document["summary"],
                        "revision": document["revision"],
                        "status": document["status"],
                        "source": document["source"],
                        "warnings": document["warnings"],
                    }
                )
            return {
                "brief": None if brief is None else _brief_json(brief),
                "documents": documents,
                "currentStoryId": None if current is None else str(current.id),
                "recipeInstanceId": (
                    None if recipe_instance is None else str(recipe_instance.id)
                ),
            }

    def get_production_flow(self, project_id: uuid.UUID) -> dict[str, Any]:
        """Return the six stable creator artifacts used by the Toonflow-style canvas."""

        with self._sessions() as session:
            project = self._require_project(session, project_id)
            facts = _workspace_facts(session, project)
            layout = session.scalar(
                select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
            )
            nodes = _production_flow_nodes(project_id, facts, layout)
            node_ids = {node["kind"]: node["id"] for node in nodes}
            edge_kinds = (
                (ProductionFlowNodeKind.SCRIPT.value, ProductionFlowNodeKind.DIRECTOR_PLAN.value),
                (ProductionFlowNodeKind.DIRECTOR_PLAN.value, ProductionFlowNodeKind.ASSETS.value),
                (
                    ProductionFlowNodeKind.ASSETS.value,
                    ProductionFlowNodeKind.STORYBOARD_TABLE.value,
                ),
                (
                    ProductionFlowNodeKind.STORYBOARD_TABLE.value,
                    ProductionFlowNodeKind.STORYBOARD.value,
                ),
                (ProductionFlowNodeKind.STORYBOARD.value, ProductionFlowNodeKind.WORKBENCH.value),
            )
            active_storyboard = facts["storyboard"]
            active_track = _active_track_id(facts["shot_cards"])
            return {
                "revision": 0 if layout is None else layout.version,
                "nodes": nodes,
                "edges": [
                    {
                        "id": f"{source}:{target}",
                        "source": node_ids[source],
                        "target": node_ids[target],
                    }
                    for source, target in edge_kinds
                ],
                "viewport": _production_flow_viewport(layout),
                "activeStoryboardRevisionId": (
                    None if active_storyboard is None else str(active_storyboard.id)
                ),
                "activeTrackId": active_track,
                "shotOrder": [
                    str(shot.id)
                    for shot in (facts["beats"] or facts["shot_cards"])
                ],
            }

    def save_production_flow_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: Any,
    ) -> dict[str, Any]:
        """Persist the six product positions without erasing historical Canvas audit layout."""

        allowed_node_ids = {
            str(uuid.uuid5(project_id, f"production-flow:{kind.value}"))
            for kind in ProductionFlowNodeKind
        }
        document = payload.model_dump(mode="json", by_alias=True)
        submitted_node_ids = {str(item.get("nodeId")) for item in document["nodes"]}
        if submitted_node_ids - allowed_node_ids:
            raise WorkflowConflictError("生产画布布局包含未知产物节点")
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            row = session.scalar(
                select(CanvasLayout)
                .where(CanvasLayout.production_run_id == project_id)
                .with_for_update()
            )
            current_version = 0 if row is None else row.version
            rebased_from: int | None = None
            if current_version != expected_version:
                operation_types = {
                    str(operation.get("type", ""))
                    for operation in document["operations"]
                }
                replayable = bool(operation_types) and operation_types <= {
                    "move_node",
                    "auto_layout",
                    "viewport",
                }
                if row is None or not replayable:
                    raise WorkflowConflictError(
                        "生产画布布局版本冲突："
                        f"当前 {current_version}，提交 {expected_version}"
                    )
                rebased_from = expected_version

            preserved_nodes = (
                {} if row is None else {
                    str(item.get("nodeId")): dict(item)
                    for item in row.nodes_json
                }
            )
            for item in document["nodes"]:
                preserved_nodes[str(item.get("nodeId"))] = item

            if row is None:
                row = CanvasLayout(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    version=1,
                    edges_json=[],
                )
                session.add(row)
            else:
                row.version += 1
            row.nodes_json = list(preserved_nodes.values())
            row.viewport_json = document["viewport"]
            row.operations_json = [
                *(row.operations_json or []),
                *document["operations"],
            ][-500:]
            row.sync_status = "saved"
            session.flush()
            return {
                "projectId": str(project_id),
                "layoutVersion": row.version,
                "viewport": row.viewport_json,
                "syncStatus": row.sync_status,
                "rebasedFromVersion": rebased_from,
            }

    def get_video_workbench(self, project_id: uuid.UUID) -> dict[str, Any]:
        """Aggregate the exact first-screen video context without materializing Canvas."""

        with self._sessions() as session:
            project = self._require_project(session, project_id)
            facts = _workspace_facts(session, project)
            assets = list(
                session.scalars(
                    select(Asset)
                    .where(Asset.production_run_id == project_id)
                    .order_by(Asset.created_at, Asset.id)
                )
            )
            assets_by_id = {asset.id: asset for asset in assets}
            active_track_id = _active_track_id(facts["shot_cards"])
            tracks = [
                _video_workbench_track(
                    session,
                    project=project,
                    shot=shot,
                    assets=assets,
                    assets_by_id=assets_by_id,
                    tasks=facts["tasks"],
                )
                for shot in facts["shot_cards"]
            ]
            if active_track_id is not None:
                tracks.sort(key=lambda track: track["id"] != active_track_id)
            selected_sequence = facts["selected_sequence"]
            rendered_asset = (
                None
                if selected_sequence is None or selected_sequence.rendered_asset_id is None
                else assets_by_id.get(selected_sequence.rendered_asset_id)
            )
            approved_references = _approved_project_references(project, assets_by_id)
            return {
                "activeTrackId": active_track_id,
                "tracks": tracks,
                "approvedReferences": approved_references,
                "timeline": None
                if selected_sequence is None
                else {
                    "id": str(selected_sequence.id),
                    "revision": selected_sequence.revision,
                    "status": selected_sequence.status,
                    "durationMs": selected_sequence.duration_ms,
                    "clips": selected_sequence.clips_json,
                },
                "exportSummary": None
                if selected_sequence is None
                else {
                    "sequenceId": str(selected_sequence.id),
                    "status": selected_sequence.status,
                    "renderedAssetId": (
                        None if rendered_asset is None else str(rendered_asset.id)
                    ),
                    "contentUrl": (
                        None
                        if rendered_asset is None
                        else f"/api/v1/assets/{rendered_asset.id}/content"
                    ),
                },
            }

    def archive_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
        reason: str | None,
    ) -> dict[str, Any]:
        projected = self.get_canvas(project_id)
        target = next(
            (node for node in projected["nodes"] if str(node["id"]) == str(node_id)),
            None,
        )
        if target is None:
            with self._sessions() as session:
                existing = session.scalar(
                    select(CanvasNodeArchive).where(
                        CanvasNodeArchive.production_run_id == project_id,
                        CanvasNodeArchive.canvas_node_id == node_id,
                        CanvasNodeArchive.restored_at.is_(None),
                    )
                )
                if existing is None:
                    raise RecordNotFoundError(f"Canvas node {node_id} was not found")
                layout = session.scalar(
                    select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
                )
                return {
                    "projectId": str(project_id),
                    "nodeId": str(node_id),
                    "archived": True,
                    "layoutVersion": 0 if layout is None else layout.version,
                }
        archive_action = next(
            (
                action
                for action in target.get("availableActions", [])
                if action.get("key") == "archive_node"
            ),
            None,
        )
        if not archive_action or not archive_action.get("enabled"):
            disabled_reason = (
                None if archive_action is None else archive_action.get("disabledReason")
            )
            raise WorkflowConflictError(
                str(disabled_reason or "该节点受当前工作流保护，不能从画布移除")
            )
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            existing = session.scalar(
                select(CanvasNodeArchive)
                .where(
                    CanvasNodeArchive.production_run_id == project_id,
                    CanvasNodeArchive.canvas_node_id == node_id,
                )
                .with_for_update()
            )
            if existing is not None and existing.restored_at is None:
                layout = session.scalar(
                    select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
                )
                return {
                    "projectId": str(project_id),
                    "nodeId": str(node_id),
                    "archived": True,
                    "layoutVersion": 0 if layout is None else layout.version,
                }
            layout = self._advance_canvas_layout_version(
                session,
                project_id,
                expected_version=expected_version,
            )
            object_id = target.get("objectId")
            if existing is None:
                existing = CanvasNodeArchive(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    canvas_node_id=node_id,
                    node_type=str(target["type"]),
                    object_type=str(target.get("objectType") or target["type"]),
                    object_id=None if not object_id else uuid.UUID(str(object_id)),
                    reason=reason,
                    archived_at=now,
                )
                session.add(existing)
            else:
                existing.node_type = str(target["type"])
                existing.object_type = str(target.get("objectType") or target["type"])
                existing.object_id = None if not object_id else uuid.UUID(str(object_id))
                existing.reason = reason
                existing.archived_at = now
                existing.restored_at = None
                existing.revision += 1
            self._record_event(
                session,
                project_id,
                "canvas_projection_changed",
                {"canvasNodeId": str(node_id), "change": "archived"},
            )
            return {
                "projectId": str(project_id),
                "nodeId": str(node_id),
                "archived": True,
                "layoutVersion": layout.version,
            }

    def restore_canvas_node(
        self,
        project_id: uuid.UUID,
        node_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id, lock=True)
            existing = session.scalar(
                select(CanvasNodeArchive)
                .where(
                    CanvasNodeArchive.production_run_id == project_id,
                    CanvasNodeArchive.canvas_node_id == node_id,
                )
                .with_for_update()
            )
            if existing is None or existing.restored_at is not None:
                layout = session.scalar(
                    select(CanvasLayout).where(CanvasLayout.production_run_id == project_id)
                )
                return {
                    "projectId": str(project_id),
                    "nodeId": str(node_id),
                    "archived": False,
                    "layoutVersion": 0 if layout is None else layout.version,
                }
            layout = self._advance_canvas_layout_version(
                session,
                project_id,
                expected_version=expected_version,
            )
            existing.restored_at = datetime.now(UTC)
            existing.revision += 1
            self._record_event(
                session,
                project_id,
                "canvas_projection_changed",
                {"canvasNodeId": str(node_id), "change": "restored"},
            )
            return {
                "projectId": str(project_id),
                "nodeId": str(node_id),
                "archived": False,
                "layoutVersion": layout.version,
            }

    def save_canvas_layout(
        self,
        project_id: uuid.UUID,
        *,
        expected_version: int,
        payload: Any,
    ) -> dict[str, Any]:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            row = session.scalar(
                select(CanvasLayout)
                .where(CanvasLayout.production_run_id == project_id)
                .with_for_update()
            )
            current_version = 0 if row is None else row.version
            document = payload.model_dump(mode="json", by_alias=True)
            rebased_from: int | None = None
            if current_version != expected_version:
                operation_types = {
                    str(operation.get("type", "")) for operation in document["operations"]
                }
                replayable = bool(operation_types) and operation_types <= {
                    "move_node",
                    "auto_layout",
                    "collapse_node",
                    "viewport",
                }
                if row is None or not replayable:
                    raise WorkflowConflictError(
                        f"画布布局版本冲突：当前 {current_version}，提交 {expected_version}"
                    )
                rebased_from = expected_version
                current_nodes = {str(item.get("nodeId")): dict(item) for item in row.nodes_json}
                for item in document["nodes"]:
                    current_nodes[str(item.get("nodeId"))] = item
                document["nodes"] = list(current_nodes.values())
            if row is None:
                row = CanvasLayout(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    version=1,
                )
                session.add(row)
            else:
                row.version += 1
            row.nodes_json = document["nodes"]
            row.viewport_json = document["viewport"]
            row.operations_json = [
                *(row.operations_json or []),
                *document["operations"],
            ][-500:]
            row.sync_status = "saved"
            session.flush()
            return {
                "projectId": str(project_id),
                "layoutVersion": row.version,
                "viewport": row.viewport_json,
                "syncStatus": row.sync_status,
                "rebasedFromVersion": rebased_from,
            }

    @staticmethod
    def _advance_canvas_layout_version(
        session: Session,
        project_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> CanvasLayout:
        layout = session.scalar(
            select(CanvasLayout)
            .where(CanvasLayout.production_run_id == project_id)
            .with_for_update()
        )
        current_version = 0 if layout is None else layout.version
        if current_version != expected_version:
            raise WorkflowConflictError(
                f"画布布局版本冲突：当前 {current_version}，提交 {expected_version}"
            )
        if layout is None:
            layout = CanvasLayout(
                id=uuid.uuid4(),
                production_run_id=project_id,
                version=1,
                nodes_json=[],
                edges_json=[],
                viewport_json={"x": 0, "y": 0, "zoom": 1},
                operations_json=[],
            )
            session.add(layout)
        else:
            layout.version += 1
        session.flush()
        return layout

    @staticmethod
    def _record_event(
        session: Session,
        project_id: uuid.UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> CanvasEvent:
        event = CanvasEvent(
            id=uuid.uuid4(),
            production_run_id=project_id,
            event_type=event_type,
            data_json=data,
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def _validate_video_source(source: Asset, project_id: uuid.UUID) -> None:
        if source.media_type != "video":
            raise ValueError("视频局部重编的源资产必须是视频")
        if source.status not in {"approved", "ready"}:
            raise ValueError("视频局部重编的源资产尚不可用")
        if source.production_run_id != project_id:
            raise ValueError("视频局部重编的源资产不属于当前项目")

    def _add_video_edit_revision(
        self,
        session: Session,
        *,
        node: CanvasGraphNode,
        draft: VideoEditRecipeDraft,
        revision: int,
        parent_recipe_id: uuid.UUID | None,
    ) -> VideoEditRecipe:
        source = self._required(session, Asset, draft.source_asset_id)
        self._validate_video_source(source, draft.project_id)
        row = VideoEditRecipe(
            id=uuid.uuid4(),
            production_run_id=draft.project_id,
            canvas_node_id=node.id,
            source_asset_id=draft.source_asset_id,
            parent_recipe_id=parent_recipe_id,
            revision=revision,
            start_ms=draft.start_ms,
            end_ms=draft.end_ms,
            instruction=draft.instruction,
            status="draft",
        )
        session.add(row)
        session.flush()
        for ordinal, annotation in enumerate(draft.annotations, 1):
            session.add(
                VideoEditAnnotation(
                    id=uuid.uuid4(),
                    recipe_id=row.id,
                    ordinal=ordinal,
                    frame_timestamp_ms=annotation.frame_timestamp_ms,
                    tool=annotation.tool.value,
                    points_json=[point.model_dump(mode="json") for point in annotation.points],
                    label=annotation.label,
                )
            )
        for ordinal, asset_id in enumerate(draft.reference_asset_ids, 1):
            asset = self._required(session, Asset, asset_id)
            if asset.media_type != "image" or asset.status not in {"approved", "ready"}:
                raise ValueError("视频重编参考必须是可用图片")
            if asset.scope != "canon" and asset.production_run_id != draft.project_id:
                raise ValueError("视频重编参考不属于当前项目")
            session.add(
                VideoEditReference(
                    id=uuid.uuid4(),
                    recipe_id=row.id,
                    asset_id=asset.id,
                    ordinal=ordinal,
                    semantic_role=asset.semantic_key or asset.role,
                    provider_included=True,
                )
            )
        self._sync_video_edit_reference_edges(
            session,
            node=node,
            reference_asset_ids=draft.reference_asset_ids,
        )
        session.flush()
        return row

    def _sync_video_edit_reference_edges(
        self,
        session: Session,
        *,
        node: CanvasGraphNode,
        reference_asset_ids: list[uuid.UUID],
    ) -> None:
        desired_sources: dict[uuid.UUID, CanvasGraphNode] = {}
        for asset_id in reference_asset_ids:
            asset = self._required(session, Asset, asset_id)
            if asset.canvas_node_id is None:
                continue
            source_node = self._required(session, CanvasGraphNode, asset.canvas_node_id)
            if (
                source_node.production_run_id == node.production_run_id
                and source_node.node_type
                in {
                    CanvasNodeType.REFERENCE_ASSET.value,
                    CanvasNodeType.IMAGE_ASSET.value,
                }
            ):
                desired_sources[source_node.id] = source_node

        current_edges = list(
            session.scalars(
                select(CanvasGraphEdge).where(
                    CanvasGraphEdge.target_node_id == node.id,
                    CanvasGraphEdge.source_port == CanvasPortType.MEDIA_REFERENCES.value,
                    CanvasGraphEdge.target_port == CanvasPortType.MEDIA_REFERENCES.value,
                )
            )
        )
        current_source_ids = {edge.source_node_id for edge in current_edges}
        for edge in current_edges:
            if edge.source_node_id not in desired_sources:
                session.delete(edge)
        for source_id, source_node in desired_sources.items():
            if source_id in current_source_ids:
                continue
            connection = CanvasConnection(
                sourceNodeId=source_node.id,
                sourceNodeType=CanvasNodeType(source_node.node_type),
                sourcePort=CanvasPortType.MEDIA_REFERENCES,
                targetNodeId=node.id,
                targetNodeType=CanvasNodeType.VIDEO_EDIT,
                targetPort=CanvasPortType.MEDIA_REFERENCES,
            )
            session.add(_graph_edge(node.production_run_id, connection))

        self._record_event(
            session,
            node.production_run_id,
            "video_edit_reference_edges_synced",
            {
                "nodeId": str(node.id),
                "sourceNodeIds": [str(source_id) for source_id in desired_sources],
            },
        )

    def _create_video_edit_revision(
        self,
        session: Session,
        current: VideoEditRecipe,
        draft: VideoEditRecipeDraft,
    ) -> VideoEditRecipe:
        if current.status == "queued":
            raise WorkflowConflictError("已提交的配方不可修改；请从该版本创建新的编辑节点")
        node = self._required(session, CanvasGraphNode, current.canvas_node_id, lock=True)
        current.status = "superseded"
        next_recipe = self._add_video_edit_revision(
            session,
            node=node,
            draft=draft,
            revision=current.revision + 1,
            parent_recipe_id=current.id,
        )
        node.object_id = next_recipe.id
        node.status = "draft"
        node.revision += 1
        node.data_json = {
            **node.data_json,
            "recipeId": str(next_recipe.id),
            "revision": next_recipe.revision,
            "status": "draft",
        }
        self._record_event(
            session,
            current.production_run_id,
            "video_edit_recipe_revised",
            {
                "recipeId": str(next_recipe.id),
                "parentRecipeId": str(current.id),
                "revision": next_recipe.revision,
            },
        )
        return next_recipe

    def _add_subject_revision(
        self,
        session: Session,
        subject: Subject,
        payload: SubjectDraft,
    ) -> SubjectRevision:
        revision = (
            int(
                session.scalar(
                    select(func.coalesce(func.max(SubjectRevision.revision), 0)).where(
                        SubjectRevision.subject_id == subject.id
                    )
                )
                or 0
            )
            + 1
        )
        row = SubjectRevision(
            id=uuid.uuid4(),
            subject_id=subject.id,
            revision=revision,
            name=payload.name,
            identity_anchors_json=list(payload.identity_anchors),
            immutable_traits_json=list(payload.immutable_traits),
            relationship_notes=payload.relationship_notes,
            dramatic_function=payload.dramatic_function,
            visual_risks_json=list(payload.visual_risks),
            revision_hash=_subject_hash(payload),
            approval_status="draft",
        )
        session.add(row)
        session.flush()
        for order, reference in enumerate(payload.references, 1):
            asset = self._required(session, Asset, reference.asset_id)
            if asset.media_type != "image" or asset.status not in {"approved", "ready"}:
                raise ValueError("主体参考必须是可用图片")
            if asset.scope != "canon" and asset.production_run_id != subject.production_run_id:
                raise ValueError("主体参考不属于当前项目")
            session.add(
                SubjectReference(
                    id=uuid.uuid4(),
                    subject_revision_id=row.id,
                    asset_id=asset.id,
                    semantic_role=reference.semantic_role,
                    sort_order=order,
                    instruction=reference.instruction,
                )
            )
        return row

    @staticmethod
    def _save_subject_states(
        session: Session,
        beat_id: uuid.UUID,
        states: list[dict[str, Any]],
    ) -> None:
        seen: set[uuid.UUID] = set()
        for state in states:
            revision_id = uuid.UUID(str(state["subjectRevisionId"]))
            if revision_id in seen:
                raise ValueError("同一主体在一个 Beat 中只能有一份状态")
            seen.add(revision_id)
            session.add(
                ShotSubjectState(
                    id=uuid.uuid4(),
                    shot_beat_id=beat_id,
                    subject_revision_id=revision_id,
                    start_state_json=state.get("startState", {}),
                    end_state_json=state.get("endState", {}),
                    action=str(state.get("action", "")),
                    interaction=str(state.get("interaction", "")),
                )
            )

    @staticmethod
    def _subject_references(
        session: Session,
        revision_id: uuid.UUID,
    ) -> tuple[SubjectReference, ...]:
        return tuple(
            session.scalars(
                select(SubjectReference)
                .where(SubjectReference.subject_revision_id == revision_id)
                .order_by(SubjectReference.sort_order)
            )
        )

    @staticmethod
    def _require_project(
        session: Session,
        project_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> ProductionRun:
        query = select(ProductionRun).where(ProductionRun.id == project_id)
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise RecordNotFoundError(f"ProductionRun {project_id} was not found")
        return row

    @staticmethod
    def _required(
        session: Session,
        model: type[Any],
        record_id: uuid.UUID,
        *,
        lock: bool = False,
    ) -> Any:
        query = select(model).where(model.id == record_id)
        if lock:
            query = query.with_for_update()
        row = session.scalar(query)
        if row is None:
            raise RecordNotFoundError(f"{model.__name__} {record_id} was not found")
        return row

    @staticmethod
    def _ensure_subject_name_available(
        session: Session,
        project_id: uuid.UUID,
        name: str,
        *,
        excluding_subject_id: uuid.UUID | None = None,
    ) -> None:
        query = (
            select(Subject.id)
            .join(SubjectRevision, SubjectRevision.id == Subject.current_revision_id)
            .where(
                Subject.production_run_id == project_id,
                func.lower(SubjectRevision.name) == name.casefold(),
                Subject.status != "archived",
            )
        )
        if excluding_subject_id is not None:
            query = query.where(Subject.id != excluding_subject_id)
        if session.scalar(query) is not None:
            raise ValueError("同一项目内主体名称不能重复")

    @staticmethod
    def _mark_project_story_stale(session: Session, project_id: uuid.UUID, reason: str) -> None:
        session.execute(
            GenerationAttempt.__table__.update()
            .where(
                GenerationAttempt.production_run_id == project_id,
                GenerationAttempt.business_object_type.in_(
                    ("project_story_strategy", "story_revision", "shot_beat")
                ),
                GenerationAttempt.status == "succeeded",
            )
            .values(status="stale", error_json={"reason": reason})
        )

    @staticmethod
    def _mark_subject_downstream_stale(session: Session, subject_id: uuid.UUID) -> None:
        subject = session.scalar(select(Subject).where(Subject.id == subject_id))
        if subject is not None:
            SqlAlchemyAigcCanvasRepository._mark_project_story_stale(
                session,
                subject.production_run_id,
                f"subject {subject_id} revision changed",
            )

    @staticmethod
    def _refresh_asset_node_projection(session: Session, node: CanvasGraphNode) -> None:
        assets = list(
            session.scalars(
                select(Asset)
                .where(Asset.canvas_node_id == node.id)
                .order_by(Asset.created_at, Asset.id)
            )
        )
        documents = [
            {
                "assetId": str(asset.id),
                "mediaType": asset.media_type,
                "semanticRole": asset.metadata_json.get("canvasSemanticRole")
                or asset.semantic_key
                or asset.role,
                "status": asset.status,
                "sha256": asset.sha256,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "thumbnailUrl": (
                    f"/api/v1/assets/{asset.id}/content" if asset.media_type == "image" else None
                ),
            }
            for asset in assets
        ]
        node.data_json = {
            **node.data_json,
            "assets": documents,
            "assetId": documents[0]["assetId"] if len(documents) == 1 else None,
            "thumbnailUrl": next(
                (item["thumbnailUrl"] for item in documents if item["thumbnailUrl"]),
                None,
            ),
            "semanticRole": documents[0]["semanticRole"] if len(documents) == 1 else None,
        }
        node.status = "ready" if assets else "awaiting_input"

    @staticmethod
    def _mark_graph_downstream_stale(session: Session, source_node_id: uuid.UUID) -> None:
        frontier = {source_node_id}
        visited = {source_node_id}
        while frontier:
            edges = list(
                session.scalars(
                    select(CanvasGraphEdge).where(CanvasGraphEdge.source_node_id.in_(frontier))
                )
            )
            frontier = set()
            for edge in edges:
                if edge.target_node_id in visited:
                    continue
                visited.add(edge.target_node_id)
                frontier.add(edge.target_node_id)
                target = session.get(CanvasGraphNode, edge.target_node_id)
                if target is not None:
                    target.status = "stale"
                    target.revision += 1

    def _invalidate_visual_profile_dependents(
        self,
        session: Session,
        project_id: uuid.UUID,
    ) -> None:
        """Preserve historical media while revoking selections built from an older Canon."""

        character_asset_ids = select(CharacterDesignAsset.asset_id).join(
            CharacterDesignRevision,
            CharacterDesignRevision.id == CharacterDesignAsset.character_design_revision_id,
        ).where(CharacterDesignRevision.production_run_id == project_id)
        session.execute(
            CharacterDesignRevision.__table__.update()
            .where(
                CharacterDesignRevision.production_run_id == project_id,
                CharacterDesignRevision.status != "stale",
            )
            .values(status="stale")
        )
        session.execute(
            Asset.__table__.update()
            .where(
                Asset.status != "stale",
                or_(
                    Asset.id.in_(character_asset_ids),
                    (
                        (Asset.production_run_id == project_id)
                        & (Asset.scope != "canon")
                        & (
                            Asset.role.like("character_design_%")
                            | Asset.role.in_(
                                (
                                    "scene_look",
                                    "generated_reference",
                                    "shot_anchor",
                                    "shot_tail_frame",
                                    "video_candidate",
                                    "shot_video",
                                    "shot_video_edit",
                                    "project_sequence",
                                )
                            )
                        )
                    ),
                ),
            )
            .values(status="stale")
        )
        scene_ids = select(Scene.id).where(Scene.production_run_id == project_id)
        session.execute(
            ShotCard.__table__.update()
            .where(ShotCard.scene_id.in_(scene_ids))
            .values(
                prompt_id=None,
                selected_anchor_asset_id=None,
                selected_video_asset_id=None,
                status="ready",
            )
        )
        session.execute(
            StoryboardRevision.__table__.update()
            .where(
                StoryboardRevision.production_run_id == project_id,
                StoryboardRevision.status
                == StoryboardRevisionStatus.PRODUCTION_APPROVED.value,
            )
            .values(
                status=StoryboardRevisionStatus.STRUCTURE_APPROVED.value,
                production_package_hash=None,
                production_approved_at=None,
            )
        )
        session.execute(
            VideoSequence.__table__.update()
            .where(
                VideoSequence.production_run_id == project_id,
                VideoSequence.status != "rejected",
            )
            .values(status="rejected")
        )
        project = session.get(ProductionRun, project_id)
        if project is not None:
            project.selected_sequence_id = None

        sources = list(
            session.scalars(
                select(CanvasGraphNode).where(
                    CanvasGraphNode.production_run_id == project_id,
                    CanvasGraphNode.node_type.in_(
                        (CanvasNodeType.SUBJECT.value, CanvasNodeType.STYLE_PRESET.value)
                    ),
                )
            )
        )
        for source in sources:
            self._mark_graph_downstream_stale(session, source.id)


def _graph_edge(project_id: uuid.UUID, connection: CanvasConnection) -> CanvasGraphEdge:
    return CanvasGraphEdge(
        id=uuid.uuid4(),
        production_run_id=project_id,
        source_node_id=connection.source_node_id,
        source_port=connection.source_port.value,
        target_node_id=connection.target_node_id,
        target_port=connection.target_port.value,
        relation_type=f"{connection.source_port.value}->{connection.target_port.value}",
        revision=1,
    )


def _filmstrip_identity(asset: Asset, frame_count: int) -> tuple[tuple[int, ...], str, str]:
    if not 4 <= frame_count <= 12:
        raise ValueError("视频帧带数量必须在4至12之间")
    qc = asset.metadata_json.get("qc")
    duration_value = qc.get("durationMs") if isinstance(qc, dict) else None
    if duration_value is None:
        duration_value = asset.metadata_json.get("durationMs")
    if not isinstance(duration_value, int) or duration_value <= 0:
        raise ValueError("视频资产缺少可用于真实抽帧的durationMs")
    # Seeking at duration-1ms can land after the final decodable frame on CFR videos.
    # Keep a small tail margin so FFmpeg can always return the last visible frame.
    last_timestamp = max(0, duration_value - min(100, duration_value))
    timestamps = tuple(
        round(index * last_timestamp / (frame_count - 1)) for index in range(frame_count)
    )
    identity = {
        "sourceSha256": asset.sha256,
        "frameCount": frame_count,
        "timestampsMs": timestamps,
    }
    filmstrip_key = _json_hash(identity)
    idempotency_key = hashlib.sha256(f"filmstrip:{filmstrip_key}".encode()).hexdigest()
    return timestamps, filmstrip_key, idempotency_key


def _filmstrip_frames(session: Session, source: Asset, filmstrip_key: str) -> tuple[Asset, ...]:
    if source.production_run_id is None:
        return ()
    rows = session.scalars(
        select(Asset).where(
            Asset.production_run_id == source.production_run_id,
            Asset.role == "filmstrip_frame",
            Asset.status == "ready",
        )
    )
    matching = [
        row
        for row in rows
        if row.metadata_json.get("sourceAssetId") == str(source.id)
        and row.metadata_json.get("filmstripKey") == filmstrip_key
    ]
    return tuple(sorted(matching, key=lambda row: int(row.metadata_json["timestampMs"])))


def _filmstrip_json(
    source: Asset,
    frame_count: int,
    status: str,
    frames: tuple[Asset, ...],
    step: WorkflowStep | None,
) -> dict[str, Any]:
    return {
        "assetId": str(source.id),
        "frameCount": frame_count,
        "status": status,
        "stepId": None if step is None else str(step.id),
        "error": None if step is None else step.error_json,
        "frames": [
            {
                "assetId": str(frame.id),
                "timestampMs": int(frame.metadata_json["timestampMs"]),
                "contentUrl": f"/api/v1/assets/{frame.id}/content",
                "sha256": frame.sha256,
            }
            for frame in frames
        ],
    }


def _asset_storage_key(path: Path, asset_root: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        key = resolved.relative_to(asset_root).as_posix()
    except ValueError as exc:
        raise ValueError("managed asset path is outside the configured asset root") from exc
    _resolve_asset_path(key, asset_root)
    return key


def _stored_asset(row: Asset, asset_root: Path) -> StoredAsset:
    return StoredAsset(
        id=row.id,
        project_id=row.production_run_id,
        scene_id=row.scene_id,
        shot_card_id=row.shot_card_id,
        step_id=row.producing_step_id,
        role=row.role,
        media_type=row.media_type,
        scope=row.scope,
        status=row.status,
        path=_resolve_asset_path(row.storage_key, asset_root),
        sha256=row.sha256,
        metadata=row.metadata_json,
        semantic_key=row.semantic_key,
        created_at=row.created_at,
    )


def _resolve_asset_path(storage_key: str, asset_root: Path) -> Path:
    key = storage_key.strip()
    pure = PurePosixPath(key)
    if not key or pure.is_absolute() or ".." in pure.parts:
        raise ValueError("asset storage key is invalid")
    resolved = asset_root.joinpath(*pure.parts).resolve()
    try:
        resolved.relative_to(asset_root)
    except ValueError as exc:
        raise ValueError("asset storage key escapes the configured root") from exc
    if not resolved.is_file():
        raise ValueError(f"asset content is unavailable: {storage_key}")
    return resolved


_CAMERA_MOTION_PRESETS: tuple[dict[str, Any], ...] = (
    {"value": "static", "label": "固定镜头", "enabled": True},
    {"value": "follow", "label": "跟随拍摄", "enabled": True},
    {"value": "push_in", "label": "缓慢推进", "enabled": True},
    {"value": "pull_out", "label": "缓慢拉远", "enabled": True},
    {"value": "pan_left", "label": "镜头左摇", "enabled": True},
    {"value": "pan_right", "label": "镜头右摇", "enabled": True},
    {"value": "tilt_up", "label": "镜头上摇", "enabled": True},
    {"value": "tilt_down", "label": "镜头下摇", "enabled": True},
    {"value": "crane_up", "label": "升降上升", "enabled": True},
    {"value": "crane_down", "label": "升降下降", "enabled": True},
    {"value": "dolly_left", "label": "镜头左移", "enabled": True},
    {"value": "dolly_right", "label": "镜头右移", "enabled": True},
    {"value": "zoom_in", "label": "变焦推近", "enabled": True},
    {"value": "zoom_out", "label": "变焦拉远", "enabled": True},
    {"value": "orbit", "label": "环绕主体", "enabled": True},
    {"value": "handheld", "label": "手持跟拍", "enabled": True},
    {"value": "drone", "label": "航拍运镜", "enabled": True},
)


_VIDEO_ASSET_ACTIONS: tuple[dict[str, Any], ...] = (
    {"key": "edit", "label": "编辑", "enabled": True, "execution": "client"},
    {
        "key": "segment_reshoot",
        "label": "片段重拍",
        "enabled": True,
        "execution": "provider",
    },
    {
        "key": "crop",
        "label": "裁剪",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前版本尚未配置非破坏裁剪执行器",
    },
    {
        "key": "upscale",
        "label": "高清",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前 Ark ProviderCapability 尚未配置视频高清执行器",
    },
    {
        "key": "frame_interpolation",
        "label": "插帧",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前版本尚未配置逐帧插值执行器",
    },
    {
        "key": "extend",
        "label": "智能续写",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前 Ark ProviderCapability 尚未配置视频续写执行器",
    },
    {
        "key": "subtitles",
        "label": "智能字幕",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前版本尚未配置字幕识别与回填执行器",
    },
    {
        "key": "audio_separation",
        "label": "音频分离",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "当前版本尚未配置音轨分离执行器",
    },
    {
        "key": "image_edit",
        "label": "画面编辑",
        "enabled": False,
        "execution": "unavailable",
        "disabledReason": "请使用片段重拍；通用画面编辑执行器尚未配置",
    },
    {"key": "download", "label": "下载", "enabled": True, "execution": "client"},
    {"key": "fullscreen", "label": "全屏", "enabled": True, "execution": "client"},
)


def _graph_node_json(row: CanvasGraphNode) -> dict[str, Any]:
    data = dict(row.data_json)
    if row.node_type == CanvasNodeType.VIDEO_ASSET.value and row.object_id is not None:
        data.setdefault("assetId", str(row.object_id))
        data.setdefault("contentUrl", f"/api/v1/assets/{row.object_id}/content")
    contract = _canvas_node_contract(row.node_type, row.status, data)
    return {
        "id": str(row.id),
        "type": row.node_type,
        "objectType": row.object_type,
        "objectId": None if row.object_id is None else str(row.object_id),
        "revision": row.revision,
        "status": row.status,
        "data": data,
        **contract,
    }


def _preset_subject_targets_node(role: str, target: CanvasGraphNode) -> bool:
    """Keep identity edges aligned with the target's actual semantic slot."""

    if target.node_type == CanvasNodeType.CHARACTER_DESIGN.value:
        slot = str(target.data_json.get("slot") or "")
        if slot == "child":
            return role == "protagonist"
        if slot == "cat":
            return role == "co_protagonist"
        return slot == "pair_scale"
    return target.node_type in {
        CanvasNodeType.STORY_PLANNER.value,
        CanvasNodeType.STORYBOARD_DIRECTOR.value,
        CanvasNodeType.IMAGE_GENERATION.value,
    }


def _canvas_action(
    key: str,
    label: str,
    *,
    enabled: bool = True,
    execution: str = "client",
    disabled_reason: str | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "key": key,
        "label": label,
        "enabled": enabled,
        "execution": execution if enabled else "unavailable",
    }
    if disabled_reason:
        action["disabledReason"] = disabled_reason
    return action


def _canvas_node_contract(
    node_type: str,
    status: str | None,
    data: dict[str, Any],
) -> dict[str, Any]:
    actions_by_type: dict[str, list[dict[str, Any]]] = {
        "RecipeGroupNode": [
            _canvas_action("recipe_primary", str(data.get("primaryAction") or "继续制作")),
            _canvas_action("toggle_children", "展开子节点"),
        ],
        "BriefNode": [
            _canvas_action("review_creative", "批准当前创作要求"),
            _canvas_action("complete_creative", "AI 补全创意", execution="provider"),
            _canvas_action("edit_brief", "编辑创意简报"),
        ],
        "SubjectNode": [
            _canvas_action("open_asset_library", "打开角色素材库"),
            _canvas_action("edit_episode_visual_profile", "编辑本集视觉档案"),
            _canvas_action("edit_subject", "编辑主体"),
            _canvas_action("assist_subject", "AI 补全主体", execution="provider"),
        ],
        "StylePresetNode": [
            _canvas_action("open_asset_library", "打开风格素材库"),
            _canvas_action("apply_visual_preset", "应用视觉预设"),
            _canvas_action("edit_episode_visual_profile", "编辑本集视觉档案"),
        ],
        "CharacterDesignNode": [
            _canvas_action(
                "validate_character_design_references",
                "验证引用顺序（3 张）",
                enabled=bool(data.get("candidates")),
                execution="provider",
                disabled_reason="请先完成并批准三个角色设计槽位",
            ),
            _canvas_action(
                "generate_character_design",
                "生成角色设计" if not data.get("candidates") else "重新生成",
                execution="provider",
            ),
            _canvas_action(
                "review_character_design",
                "选择并审核候选",
                enabled=bool(data.get("candidates")),
                disabled_reason="角色设计候选尚未生成",
            ),
        ],
        "StoryPlannerNode": [
            _canvas_action(
                "inspect_prompt"
                if data.get("objectType") == "story_event_planner"
                else "generate_stories",
                "查看历史事件流程"
                if data.get("objectType") == "story_event_planner"
                else "生成完整故事候选",
                execution=(
                    "client" if data.get("objectType") == "story_event_planner" else "provider"
                ),
            ),
        ],
        "StoryEventNode": [],
        "StoryScriptNode": [],
        "StoryCandidateNode": [
            _canvas_action(
                "approve_story",
                "选择为当前剧情" if status != "approved" else "当前剧情",
                enabled=status != "approved",
                disabled_reason="该故事版本已经批准",
            ),
        ],
        "StoryCriticNode": [_canvas_action("inspect_prompt", "查看评审记录")],
        "ApprovalGateNode": [
            _canvas_action(
                (
                    "inspect_prompt"
                    if data.get("objectType") == "story_event_selection"
                    else (
                        "review_creative"
                        if data.get("phase") == "creative"
                        else (
                            "review_character_design"
                            if data.get("phase") == "character_design"
                            else "review_story"
                        )
                    )
                ),
                (
                    "查看历史事件流程"
                    if data.get("objectType") == "story_event_selection"
                    else (
                        "审核创意简报"
                        if data.get("phase") == "creative"
                        else (
                            "审核角色设计"
                            if data.get("phase") == "character_design"
                            else "审核剧情脚本"
                        )
                    )
                ),
                enabled=True,
            )
        ],
        "StoryboardDirectorNode": [
            _canvas_action(
                "storyboard_from_story",
                "剧本生成分镜脚本",
                enabled=not bool(data.get("shotCount")),
                execution="provider",
                disabled_reason="当前已有导演分镜，请在画布中编辑或明确建立新版本",
            ),
            _canvas_action(
                "storyboard_from_characters",
                "基于固定角色补充分镜",
                enabled=not bool(data.get("shotCount")),
                execution="provider",
                disabled_reason="当前已有导演分镜，请在画布中编辑或明确建立新版本",
            ),
            _canvas_action(
                "storyboard_manual",
                "在画布中编辑分镜",
                enabled=bool(data.get("shotCount")),
                disabled_reason="请先从已批准剧情生成首个导演分镜版本",
            ),
            _canvas_action(
                "review_storyboard",
                "继续分镜生产审核" if not data.get("storyboardApproved") else "生产分镜包已批准",
                enabled=bool(data.get("shotCount")) and not bool(data.get("storyboardApproved")),
                disabled_reason=(
                    "分镜结构、生成编排与生产分镜包均已批准"
                    if data.get("storyboardApproved")
                    else ("请先生成并保存导演分镜" if not data.get("shotCount") else None)
                ),
            ),
        ],
        "SceneNode": [_canvas_action("open_scene", "查看场景与镜头")],
        "ShotBeatNode": [
            _canvas_action("edit_shot", "编辑镜头"),
            _canvas_action("move_shot_up", "上移"),
            _canvas_action("move_shot_down", "下移"),
            _canvas_action("delete_shot", "删除分镜"),
        ],
        "GenerationPlanNode": [
            _canvas_action("approve_generation_plan", "批准生成编排"),
        ],
        "ImageGenerationNode": [
            _canvas_action("open_generator", "打开图片生成器"),
            _canvas_action("select_references", "选择参考"),
        ],
        "VideoGenerationNode": [
            _canvas_action("open_generator", "打开视频生成器"),
            _canvas_action("select_references", "选择参考"),
        ],
        "AudioGenerationNode": [_canvas_action("open_generator", "打开音频生成器")],
        "GenerationBatchNode": [_canvas_action("open_generator", "打开批量生成器")],
        "ReviewNode": [_canvas_action("review_asset", "审核候选资产")],
        "TimelineNode": [
            _canvas_action("compose_sequence", "合成最终音画", execution="local_worker"),
            _canvas_action(
                "export_sequence",
                "导出",
                enabled=bool(data.get("contentUrl")),
                disabled_reason="最终成片尚未批准，暂不能导出",
            ),
        ],
        "ReferenceAssetNode": [
            _canvas_action("upload_reference", "上传参考"),
            _canvas_action("select_history", "从历史选择"),
            _canvas_action("create_subject", "创建主体并绑定"),
        ],
        "ImageAssetNode": [
            _canvas_action("inspect_asset", "查看图片"),
            _canvas_action("download", "下载"),
        ],
        "VideoEditNode": [_canvas_action("edit", "打开视频编辑")],
        "VideoSegmentNode": [_canvas_action("inspect_asset", "查看重拍片段")],
        "PromptArtifactNode": [_canvas_action("inspect_prompt", "查看 Prompt 审计")],
    }
    actions = actions_by_type.get(node_type, [])
    object_type = str(data.get("objectType") or "")
    if (
        node_type == "ApprovalGateNode"
        and data.get("phase") == "character_design"
        and object_type not in {"storyboard_structure", "storyboard_package"}
    ):
        actions = [
            *actions,
            _canvas_action(
                "validate_character_design_references",
                "验证引用顺序（3 张）",
                enabled=status == "approved" or bool(data.get("approved")),
                execution="provider",
                disabled_reason="三个角色设计槽位尚未全部批准",
            ),
        ]
    if node_type == "ApprovalGateNode" and object_type == "storyboard_structure":
        actions = [
            _canvas_action(
                "approve_storyboard_structure",
                "分镜结构已批准" if data.get("approved") else "批准分镜结构",
                enabled=not bool(data.get("approved")),
                disabled_reason="当前结构已锁定" if data.get("approved") else None,
            )
        ]
    elif node_type == "ApprovalGateNode" and object_type == "storyboard_package":
        prompts_ready = bool(data.get("requiredPromptCount")) and (
            data.get("compiledPromptCount") == data.get("requiredPromptCount")
        )
        actions = [
            _canvas_action(
                "approve_storyboard_package",
                "生产分镜包已批准" if data.get("approved") else "批准生产分镜包",
                enabled=prompts_ready and not bool(data.get("approved")),
                disabled_reason=(
                    "生产分镜包已锁定"
                    if data.get("approved")
                    else None
                    if prompts_ready
                    else "请先完成全部生成片段 Prompt 编译"
                ),
            )
        ]
    elif node_type == "GenerationPlanNode":
        blocked = bool(data.get("blockers"))
        structure_approved = bool(data.get("structureApproved"))
        actions = [
            _canvas_action(
                "approve_generation_plan",
                "生成编排已批准" if data.get("approved") else "批准生成编排",
                enabled=(structure_approved and not blocked and not bool(data.get("approved"))),
                disabled_reason=(
                    "当前编排已批准"
                    if data.get("approved")
                    else "请先批准分镜结构"
                    if not structure_approved
                    else "当前编排存在能力阻断"
                    if blocked
                    else None
                ),
            ),
        ]
    elif node_type == "VideoSegmentNode" and object_type == "generation_clip":
        actions = [
            _canvas_action("inspect_generation_clip", "查看片段编排"),
            _canvas_action("split_generation_clip", "拆分片段"),
            _canvas_action("merge_generation_clips", "合并相邻片段"),
        ]
    elif node_type == "PromptArtifactNode" and object_type == "generation_clip_prompt":
        compiled = bool(data.get("compiled"))
        prompt_ready = bool(data.get("generationPlanApproved")) and bool(
            data.get("sceneAssetsReady")
        )
        scene_asset_blockers = data.get("sceneAssetBlockers")
        first_scene_asset_blocker = (
            str(scene_asset_blockers[0])
            if isinstance(scene_asset_blockers, list) and scene_asset_blockers
            else None
        )
        actions = [
            _canvas_action(
                "compile_generation_prompt",
                "查看已编译 Prompt" if compiled else "编译生产 Prompt",
                enabled=compiled or prompt_ready,
                disabled_reason=(
                    None
                    if compiled or prompt_ready
                    else "请先批准当前生成编排"
                    if not data.get("generationPlanApproved")
                    else first_scene_asset_blocker
                    or "请先批准生成编排并完成当前启用的场景资产"
                ),
            )
        ]
    elif node_type == "ImageGenerationNode" and object_type == "visual_anchor_generation":
        actions = [
            _canvas_action(
                "generate_anchor",
                "生成视觉锚点",
                execution="provider",
                enabled=bool(data.get("packageApproved")) and not bool(data.get("selectedAssetId")),
                disabled_reason=(
                    "视觉锚点已批准"
                    if data.get("selectedAssetId")
                    else None
                    if data.get("packageApproved")
                    else "请先批准生产分镜包"
                ),
            )
        ]
    elif node_type == "VideoGenerationNode" and object_type == "video_generation_clip":
        anchor_ready = not bool(data.get("anchorRequired")) or bool(
            data.get("anchorApproved")
        )
        video_ready = bool(data.get("packageApproved")) and anchor_ready
        actions = [
            _canvas_action(
                "generate_video",
                "生成视频",
                execution="provider",
                enabled=video_ready,
                disabled_reason=(
                    None
                    if video_ready
                    else "请先批准生产分镜包"
                    if not data.get("packageApproved")
                    else "当前已选择精确开场模式，请先批准开场锚点"
                ),
            )
        ]
    if node_type == "VideoAssetNode":
        actions = [dict(action) for action in _VIDEO_ASSET_ACTIONS]
    if data.get("promptId") and not any(action["key"] == "inspect_prompt" for action in actions):
        actions.append(_canvas_action("inspect_prompt", "查看 Prompt 审计"))
    if not actions:
        actions = [
            _canvas_action(
                "unavailable",
                "暂无可执行操作",
                enabled=False,
                disabled_reason="该节点尚未配置可执行处理器",
            )
        ]
    operation_keys = [
        str(action["key"])
        for action in actions
        if action.get("key") not in {"unavailable", "archive_node", "restore_node"}
    ]
    return {
        "availableActions": actions,
        "executionScope": {
            "kind": "business_object" if data.get("businessObjectId") else "canvas_node",
            "objectType": str(data.get("objectType") or node_type),
            "recipeInstanceId": data.get("recipeInstanceId"),
            "canvasGroupId": data.get("canvasGroupId"),
            "businessObjectId": data.get("businessObjectId"),
            "sceneId": data.get("sceneId"),
            "shotId": data.get("shotId"),
            "operationKeys": operation_keys,
            "phases": [str(data["phase"])] if data.get("phase") else [],
            "includeChildTasks": node_type
            in {
                "StoryPlannerNode",
                "StoryScriptNode",
                "CharacterDesignNode",
                "StoryboardDirectorNode",
                "ImageGenerationNode",
                "VideoGenerationNode",
                "TimelineNode",
            },
        },
        "workflowSteps": list(data.get("workflowSteps") or []),
        "blocker": data.get("blocker"),
        "outputs": list(data.get("outputs") or []),
    }


_PROTECTED_RECIPE_SKELETON_NODE_TYPES = {
    "ApprovalGateNode",
    "BriefNode",
    "CharacterDesignNode",
    "ImageGenerationNode",
    "ReviewNode",
    "SceneNode",
    "ShotBeatNode",
    "GenerationPlanNode",
    "StoryboardDirectorNode",
    "StoryPlannerNode",
    "StylePresetNode",
    "SubjectNode",
    "TimelineNode",
    "VideoGenerationNode",
}


def _canvas_node_archive_blocker(
    node: dict[str, Any],
    *,
    active_group_member_ids: set[str],
    outgoing_node_ids: set[str],
) -> str | None:
    node_id = str(node["id"])
    node_type = str(node["type"])
    data = node.get("data") or {}
    status = str(node.get("status") or data.get("status") or "")
    if node_type == "SubjectNode":
        return "固定儿童与猫咪 Canon 是身份来源，不能从画布移除"
    if node_type == "StoryCandidateNode" and status == "approved":
        return "当前批准故事已被下游流程使用，不能从画布移除"
    if node_id in active_group_member_ids and node_type in _PROTECTED_RECIPE_SKELETON_NODE_TYPES:
        return "该节点属于活跃六阶段流程骨架，不能从画布移除"
    if (
        node_type in {"ImageAssetNode", "VideoAssetNode"}
        and status == "approved"
        and node_id in outgoing_node_ids
    ):
        return "该批准资产已被下游节点引用，不能从画布移除"
    return None


def _apply_canvas_node_archive_projection(
    canvas: dict[str, Any],
    active_archive_ids: set[str],
) -> None:
    active_group_member_ids = {
        str(node_id)
        for group in canvas.get("groups", [])
        if str(group.get("lifecycleStatus") or "active") == "active"
        for node_id in group.get("memberNodeIds", [])
    }
    outgoing_node_ids = {str(edge["sourceNodeId"]) for edge in canvas.get("edges", [])}
    for node in canvas.get("nodes", []):
        blocker = _canvas_node_archive_blocker(
            node,
            active_group_member_ids=active_group_member_ids,
            outgoing_node_ids=outgoing_node_ids,
        )
        actions = [
            action
            for action in node.get("availableActions", [])
            if action.get("key") not in {"archive_node", "restore_node"}
        ]
        actions.append(
            _canvas_action(
                "archive_node",
                "从画布移除",
                enabled=blocker is None,
                disabled_reason=blocker,
            )
        )
        node["availableActions"] = actions
    if not active_archive_ids:
        return
    canvas["nodes"] = [
        node for node in canvas.get("nodes", []) if str(node["id"]) not in active_archive_ids
    ]
    canvas["edges"] = [
        edge
        for edge in canvas.get("edges", [])
        if str(edge["sourceNodeId"]) not in active_archive_ids
        and str(edge["targetNodeId"]) not in active_archive_ids
    ]
    for group in canvas.get("groups", []):
        group["memberNodeIds"] = [
            node_id
            for node_id in group.get("memberNodeIds", [])
            if str(node_id) not in active_archive_ids
        ]


def _apply_shot_reference_edge_projection(canvas: dict[str, Any], beats: list[ShotBeat]) -> None:
    """Project persisted shot-level bindings as explicit, removable creative inputs."""

    source_by_asset_id: dict[str, dict[str, Any]] = {}
    for node in canvas.get("nodes", []):
        data = node.get("data") or {}
        candidate_documents: list[dict[str, Any]] = []
        if isinstance(data.get("assets"), list):
            candidate_documents.extend(item for item in data["assets"] if isinstance(item, dict))
        if isinstance(data.get("candidates"), list):
            candidate_documents.extend(
                item for item in data["candidates"] if isinstance(item, dict)
            )
        if isinstance(data.get("references"), list):
            candidate_documents.extend(
                item for item in data["references"] if isinstance(item, dict)
            )
        direct_asset_id = data.get("assetId")
        if direct_asset_id:
            source_by_asset_id.setdefault(str(direct_asset_id), node)
        if node.get("type") in {"ImageAssetNode", "ReferenceAssetNode"} and node.get("objectId"):
            source_by_asset_id.setdefault(str(node["objectId"]), node)
        for document in candidate_documents:
            asset_id = document.get("assetId") or document.get("id")
            if asset_id:
                source_by_asset_id.setdefault(str(asset_id), node)

    projected = canvas.setdefault("edges", [])
    existing_ids = {str(edge.get("id")) for edge in projected}
    for beat in beats:
        for binding in beat.reference_bindings_json:
            if not isinstance(binding, dict) or not binding.get("assetId"):
                continue
            asset_id = str(binding["assetId"])
            source = source_by_asset_id.get(asset_id)
            if source is None:
                continue
            edge_id = f"shot-reference:{beat.id}:{asset_id}"
            if edge_id in existing_ids:
                continue
            existing_ids.add(edge_id)
            projected.append(
                {
                    "id": edge_id,
                    "sourceNodeId": str(source["id"]),
                    "sourceNodeType": str(source["type"]),
                    "sourcePort": "image_asset",
                    "targetNodeId": str(beat.id),
                    "targetNodeType": "ShotBeatNode",
                    "targetPort": "image_reference[]",
                    "relationType": "shot_reference",
                    "revision": beat.reference_binding_revision,
                    "systemManaged": False,
                    "presentationKind": "user_reference",
                    "defaultVisible": True,
                    "authority": "user",
                    "referenceBinding": binding,
                    "availableActions": [
                        {
                            "key": "disconnect_edge",
                            "label": "移除镜头引用",
                            "enabled": True,
                            "disabledReason": None,
                        }
                    ],
                }
            )


def _graph_edge_json(
    row: CanvasGraphEdge,
    source: CanvasGraphNode,
    target: CanvasGraphNode,
) -> dict[str, Any]:
    disconnect_enabled, disabled_reason = _edge_disconnect_policy(
        source_port=row.source_port,
        target_port=row.target_port,
        relation_type=row.relation_type,
    )
    presentation_kind, default_visible, authority = _edge_presentation(
        source_type=source.node_type,
        target_type=target.node_type,
        source_port=row.source_port,
        target_port=row.target_port,
        relation_type=row.relation_type,
        user_managed=disconnect_enabled,
    )
    return {
        "id": str(row.id),
        "sourceNodeId": str(row.source_node_id),
        "sourceNodeType": source.node_type,
        "sourcePort": row.source_port,
        "targetNodeId": str(row.target_node_id),
        "targetNodeType": target.node_type,
        "targetPort": row.target_port,
        "relationType": row.relation_type,
        "revision": row.revision,
        "systemManaged": not disconnect_enabled,
        "presentationKind": presentation_kind,
        "defaultVisible": default_visible,
        "authority": authority,
        "availableActions": [
            {
                "key": "disconnect_edge",
                "label": "剪断连接",
                "enabled": disconnect_enabled,
                "disabledReason": disabled_reason,
            }
        ],
    }


def _edge_presentation(
    *,
    source_type: str,
    target_type: str,
    source_port: str,
    target_port: str,
    relation_type: str,
    user_managed: bool,
) -> tuple[str, bool, str]:
    """Separate provider/user inputs from system lifecycle dependencies.

    The graph remains authoritative for workflow gating, but the everyday canvas only
    renders edges that represent an explicit creative input.  This prevents approval
    gates and Canon inheritance from turning the canvas into a mandatory long chain.
    """

    reference_ports = {
        CanvasPortType.IMAGE_REFERENCES.value,
        CanvasPortType.MEDIA_REFERENCES.value,
        CanvasPortType.IMAGE_ASSET.value,
        CanvasPortType.IMAGE_ASSETS.value,
    }
    if user_managed and (source_port in reference_ports or target_port in reference_ports):
        return "user_reference", True, "user"
    if (
        source_type in {"StoryScriptNode", "StoryCandidateNode"}
        and target_type == "StoryboardDirectorNode"
    ) or relation_type in {"storyboard_source", "generation_clip_shot"}:
        return "artifact_flow", True, "business"
    return "derived_dependency", False, "system"


_SYSTEM_MANAGED_EDGE_RELATIONS = {
    "anchor_to_video",
    "approved_design",
    "approved_input",
    "approved_story",
    "canon_identity_reference",
    "creative_review",
    "design_review",
    "identity_source",
    "story_subject",
    "storyboard_to_anchor",
    "style_source",
    "video_review",
    "video_to_sequence",
    "visual_preset_reference",
}


def _edge_disconnect_policy(
    *,
    source_port: str,
    target_port: str,
    relation_type: str,
) -> tuple[bool, str | None]:
    """Return the authoritative disconnect capability for persisted graph edges."""

    if relation_type in _SYSTEM_MANAGED_EDGE_RELATIONS:
        if relation_type in {"canon_identity_reference", "identity_source"}:
            return False, "该连线由 Canon 身份规则派生，需修改视觉档案，不能直接剪断"
        if relation_type in {"style_source", "visual_preset_reference"}:
            return False, "该连线由固定画风预设派生，需修改本集视觉档案，不能直接剪断"
        if relation_type in {"creative_review", "design_review", "video_review"}:
            return False, "该连线属于人工审核门，不能绕过审核流程直接剪断"
        return False, "该连线属于一人一猫六阶段主流程，不能直接剪断"
    editable_ports = {
        CanvasPortType.IMAGE_REFERENCES.value,
        CanvasPortType.MEDIA_REFERENCES.value,
        CanvasPortType.IMAGE_ASSET.value,
        CanvasPortType.IMAGE_ASSETS.value,
    }
    if source_port in editable_ports or target_port in editable_ports:
        return True, None
    return False, "该连接承担业务血缘，不支持从画布直接剪断"


def _generation_batch_json(session: Session, row: MediaGenerationBatch) -> dict[str, Any]:
    candidate_steps = list(
        session.scalars(
            select(WorkflowStep)
            .where(
                WorkflowStep.production_run_id == row.production_run_id,
                WorkflowStep.operation_key.startswith(
                    f"media:{row.media_kind}:batch:{row.id}:candidate:"
                ),
            )
            .order_by(WorkflowStep.created_at, WorkflowStep.id)
        )
    )
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "canvasNodeId": str(row.canvas_node_id),
        "workflowStepId": (None if row.workflow_step_id is None else str(row.workflow_step_id)),
        "mediaKind": row.media_kind,
        "candidateCount": row.candidate_count,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "referenceManifest": row.reference_manifest_json,
        "referenceManifestHash": row.reference_manifest_hash or None,
        "outputAssetIds": row.output_asset_ids_json,
        "candidateStepIds": [str(step.id) for step in candidate_steps],
        "candidateSteps": [
            {
                "stepId": str(step.id),
                "status": step.status,
                "providerTaskId": step.provider_task_id,
                "error": step.error_json,
            }
            for step in candidate_steps
        ],
    }


def _video_edit_draft_json(session: Session, row: VideoEditRecipe) -> dict[str, Any]:
    annotations = list(
        session.scalars(
            select(VideoEditAnnotation)
            .where(VideoEditAnnotation.recipe_id == row.id)
            .order_by(VideoEditAnnotation.ordinal)
        )
    )
    references = list(
        session.scalars(
            select(VideoEditReference)
            .where(VideoEditReference.recipe_id == row.id)
            .order_by(VideoEditReference.ordinal)
        )
    )
    return {
        "projectId": str(row.production_run_id),
        "sourceAssetId": str(row.source_asset_id),
        "startMs": row.start_ms,
        "endMs": row.end_ms,
        "instruction": row.instruction,
        "referenceAssetIds": [str(item.asset_id) for item in references],
        "annotations": [
            {
                "frameTimestampMs": item.frame_timestamp_ms,
                "coordinateSpace": "source_normalized",
                "tool": item.tool,
                "points": item.points_json,
                "label": item.label,
            }
            for item in annotations
        ],
    }


def _video_edit_recipe_json(session: Session, row: VideoEditRecipe) -> dict[str, Any]:
    draft = _video_edit_draft_json(session, row)
    references = list(
        session.scalars(
            select(VideoEditReference)
            .where(VideoEditReference.recipe_id == row.id)
            .order_by(VideoEditReference.ordinal)
        )
    )
    compiled_references = {
        str(item.get("assetId")): item
        for item in (row.compilation_json or {}).get("actualReferences", [])
        if isinstance(item, dict) and item.get("assetId")
    }
    return {
        "id": str(row.id),
        "canvasNodeId": str(row.canvas_node_id),
        "parentRecipeId": (None if row.parent_recipe_id is None else str(row.parent_recipe_id)),
        "revision": row.revision,
        "status": row.status,
        "compilation": row.compilation_json,
        "estimatedCostMicros": row.estimated_cost_micros,
        **draft,
        "references": [
            {
                "assetId": str(item.asset_id),
                "semanticRole": item.semantic_role,
                "providerIncluded": compiled_references.get(str(item.asset_id), {}).get(
                    "providerIncluded", item.provider_included
                ),
                "providerSlot": compiled_references.get(str(item.asset_id), {}).get("providerSlot"),
                "omissionReason": compiled_references.get(str(item.asset_id), {}).get(
                    "omissionReason"
                ),
            }
            for item in references
        ],
    }


def _video_edit_prompt(draft: dict[str, Any]) -> str:
    annotation_instructions = [
        {
            "timeMs": item["frameTimestampMs"],
            "tool": item["tool"],
            "points": item["points"],
            "label": item["label"],
        }
        for item in draft["annotations"]
    ]
    return (
        "仅重编源视频的指定区间，保持区间外画面和原音轨不变。\n"
        f"区间：{draft['startMs']}ms - {draft['endMs']}ms\n"
        f"编辑要求：{draft['instruction']}\n"
        f"参考素材ID：{json.dumps(draft['referenceAssetIds'], ensure_ascii=False)}\n"
        f"归一化视觉标注：{json.dumps(annotation_instructions, ensure_ascii=False)}"
    )


def _control_anchor_prompt(draft: dict[str, Any], boundary: str) -> str:
    label = "起始" if boundary == "start" else "结束"
    return (
        f"生成视频局部重编区间的{label}控制锚点。保持未标注区域、人物和产品身份不变，"
        "只执行标注与文字要求；输出干净画面，不保留标注图形。\n"
        f"编辑要求：{draft['instruction']}\n"
        f"参考素材ID：{json.dumps(draft['referenceAssetIds'], ensure_ascii=False)}\n"
        f"归一化标注：{json.dumps(draft['annotations'], ensure_ascii=False)}"
    )


def _merge_universal_graph(
    canvas: dict[str, Any],
    *,
    layout: CanvasLayout | None,
    graph_nodes: list[CanvasGraphNode],
    graph_edges: list[CanvasGraphEdge],
    legacy_assets: list[Asset],
) -> None:
    positions = {str(item.get("nodeId")): item for item in (layout.nodes_json if layout else [])}
    existing_ids = {str(item["id"]) for item in canvas["nodes"]}
    stage_by_type = {
        CanvasNodeType.REFERENCE_ASSET.value: 0,
        CanvasNodeType.GENERATION_BATCH.value: 1,
        CanvasNodeType.IMAGE_ASSET.value: 2,
        CanvasNodeType.VIDEO_GENERATION.value: 3,
        CanvasNodeType.VIDEO_ASSET.value: 4,
        CanvasNodeType.VIDEO_EDIT.value: 5,
        CanvasNodeType.VIDEO_SEGMENT.value: 6,
        CanvasNodeType.REVIEW.value: 7,
        CanvasNodeType.TIMELINE.value: 8,
    }
    stage_rows: dict[int, int] = {}
    node_by_id: dict[uuid.UUID, CanvasGraphNode] = {}
    for row in graph_nodes:
        node_by_id[row.id] = row
        if str(row.id) in existing_ids:
            existing = next(item for item in canvas["nodes"] if str(item["id"]) == str(row.id))
            existing["data"] = {**row.data_json, **dict(existing.get("data") or {})}
            existing["status"] = existing.get("status") or row.status
            existing.update(
                _canvas_node_contract(
                    row.node_type,
                    str(existing.get("status") or row.status),
                    existing["data"],
                )
            )
            continue
        document = _graph_node_json(row)
        stage = stage_by_type.get(row.node_type, 9)
        row_index = stage_rows.get(stage, 0)
        stage_rows[stage] = row_index + 1
        stored = positions.get(str(row.id), {})
        document["position"] = {
            "x": stored.get("x", 80 + stage * 320),
            "y": stored.get("y", 80 + row_index * 240),
        }
        canvas["nodes"].append(document)
        existing_ids.add(str(row.id))
    for asset in legacy_assets:
        if str(asset.id) in existing_ids:
            continue
        node_type = (
            CanvasNodeType.VIDEO_ASSET.value
            if asset.media_type == "video"
            else CanvasNodeType.IMAGE_ASSET.value
        )
        stage = stage_by_type[node_type]
        row_index = stage_rows.get(stage, 0)
        stage_rows[stage] = row_index + 1
        stored = positions.get(str(asset.id), {})
        projected_asset = {
            "id": str(asset.id),
            "type": node_type,
            "objectType": "legacy_asset",
            "objectId": str(asset.id),
            "revision": int(asset.metadata_json.get("version", 1)),
            "status": asset.status,
            "data": {
                "title": asset.semantic_key or asset.role,
                "assetId": str(asset.id),
                "mediaType": asset.media_type,
                "status": asset.status,
                "metadata": asset.metadata_json,
                "legacyProjection": True,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "posterUrl": asset.metadata_json.get("posterUrl"),
            },
            "position": {
                "x": stored.get("x", 80 + stage * 320),
                "y": stored.get("y", 80 + row_index * 240),
            },
        }
        projected_asset.update(
            _canvas_node_contract(node_type, asset.status, projected_asset["data"])
        )
        canvas["nodes"].append(projected_asset)
        existing_ids.add(str(asset.id))
    existing_edge_ids = {str(item["id"]) for item in canvas["edges"]}
    for edge in graph_edges:
        source = node_by_id.get(edge.source_node_id)
        target = node_by_id.get(edge.target_node_id)
        if source is None or target is None or str(edge.id) in existing_edge_ids:
            continue
        canvas["edges"].append(_graph_edge_json(edge, source, target))


def _merge_canvas_groups(
    canvas: dict[str, Any],
    *,
    groups: list[CanvasGroup],
    members: list[CanvasGroupMember],
    states: dict[uuid.UUID, dict[str, Any]],
) -> None:
    members_by_group: dict[uuid.UUID, list[str]] = {}
    for member in members:
        members_by_group.setdefault(member.group_id, []).append(str(member.canvas_node_id))
    all_node_ids = [str(node["id"]) for node in canvas["nodes"]]
    projected_groups: list[dict[str, Any]] = []
    for group in groups:
        state = states.get(group.id, {})
        storyboard_ready = bool(state.get("storyboardApproved"))
        complete = state.get("phase") == "complete"
        projected_groups.append(
            {
                "id": str(group.id),
                "projectId": str(group.production_run_id),
                "recipeInstanceId": (
                    None
                    if group.production_recipe_instance_id is None
                    else str(group.production_recipe_instance_id)
                ),
                "parentGroupId": (
                    None if group.parent_group_id is None else str(group.parent_group_id)
                ),
                "type": group.group_type,
                "title": group.title,
                "lifecycleStatus": group.lifecycle_status,
                "color": group.color,
                "revision": group.revision,
                "memberNodeIds": (
                    all_node_ids
                    if group.group_type == "recipe"
                    else members_by_group.get(group.id, [])
                ),
                "phase": state.get("phase"),
                "phaseProgress": list(state.get("phaseProgress", [])),
                "blocker": state.get("blocker"),
                "availableActions": [
                    {
                        "key": "run_group",
                        "label": "整组执行",
                        "enabled": not complete,
                        "disabledReason": "六阶段已全部完成" if complete else None,
                    },
                    {"key": "save_group_template", "label": "添加到工具箱", "enabled": True},
                    {
                        "key": "convert_shot_groups",
                        "label": "转分镜组",
                        "enabled": storyboard_ready,
                        "disabledReason": None if storyboard_ready else "分镜人工批准后才能转换",
                    },
                    {"key": "ungroup", "label": "解组", "enabled": True},
                    {"key": "download_group", "label": "批量下载", "enabled": True},
                ],
                "data": {**group.data_json, **state},
            }
        )
    canvas["groups"] = projected_groups


_CANVAS_LAYOUT_LANE_ORDER = {
    "canon": 0,
    "creative": 1,
    "story": 2,
    "character_scene": 3,
    "storyboard": 4,
    "render": 5,
    "export": 6,
}


_DIRECTOR_ACTIVE_TASK_STATUSES = {
    StepStatus.PENDING.value,
    StepStatus.SUBMITTING.value,
    StepStatus.CANCELLING.value,
    StepStatus.QUEUED.value,
    StepStatus.RUNNING.value,
}
_DIRECTOR_ATTENTION_TASK_STATUSES = {
    StepStatus.SUBMISSION_UNKNOWN.value,
    StepStatus.CANCELLATION_UNKNOWN.value,
    StepStatus.AWAITING_REVIEW.value,
    StepStatus.FAILED.value,
    StepStatus.EXPIRED.value,
}

_WORKSPACE_MODULE_ORDER = (
    WorkspaceModuleId.SCRIPT,
    WorkspaceModuleId.ASSETS,
    WorkspaceModuleId.PRODUCTION,
)


def _workspace_facts(session: Session, project: ProductionRun) -> dict[str, Any]:
    project_id = project.id
    brief = session.scalar(
        select(StoryBriefRecord)
        .where(StoryBriefRecord.production_run_id == project_id)
        .order_by(StoryBriefRecord.revision.desc(), StoryBriefRecord.id.desc())
        .limit(1)
    )
    current_story = session.scalar(
        select(StoryRevisionRecord)
        .where(
            StoryRevisionRecord.production_run_id == project_id,
            StoryRevisionRecord.status == StoryRevisionStatus.APPROVED.value,
        )
        .order_by(StoryRevisionRecord.revision.desc(), StoryRevisionRecord.id.desc())
        .limit(1)
    )
    latest_story = session.scalar(
        select(StoryRevisionRecord)
        .where(StoryRevisionRecord.production_run_id == project_id)
        .order_by(StoryRevisionRecord.revision.desc(), StoryRevisionRecord.id.desc())
        .limit(1)
    )
    subjects = _preferred_subject_rows(
        session,
        list(
            session.scalars(
                select(Subject)
                .where(
                    Subject.production_run_id == project_id,
                    Subject.status != "archived",
                )
                .order_by(Subject.created_at, Subject.id)
            )
        ),
    )
    assets = list(
        session.scalars(
            select(Asset)
            .where(Asset.production_run_id == project_id)
            .order_by(Asset.created_at, Asset.id)
        )
    )
    storyboard = session.scalar(
        select(StoryboardRevision)
        .where(StoryboardRevision.production_run_id == project_id)
        .order_by(StoryboardRevision.revision.desc(), StoryboardRevision.id.desc())
        .limit(1)
    )
    recipe = session.scalar(
        select(ProductionRecipeInstance)
        .where(ProductionRecipeInstance.production_run_id == project_id)
        .order_by(ProductionRecipeInstance.created_at.desc(), ProductionRecipeInstance.id.desc())
        .limit(1)
    )
    beats = list(
        session.scalars(
            select(ShotBeat)
            .join(Scene, Scene.id == ShotBeat.scene_id)
            .where(
                Scene.production_run_id == project_id,
                Scene.active.is_(True),
                ShotBeat.status != "superseded",
            )
            .order_by(Scene.sort_order, ShotBeat.sort_order, ShotBeat.id)
        )
    )
    scenes = list(
        session.scalars(
            select(Scene)
            .where(
                Scene.production_run_id == project_id,
                Scene.active.is_(True),
            )
            .order_by(Scene.sort_order, Scene.id)
        )
    )
    shot_cards = list(
        session.scalars(
            select(ShotCard)
            .join(Scene, Scene.id == ShotCard.scene_id)
            .where(
                Scene.production_run_id == project_id,
                Scene.active.is_(True),
            )
            .order_by(Scene.sort_order, ShotCard.sort_order, ShotCard.id)
        )
    )
    tasks = list(
        session.scalars(
            select(WorkflowStep)
            .where(WorkflowStep.production_run_id == project_id)
            .order_by(WorkflowStep.updated_at.desc(), WorkflowStep.id.desc())
            .limit(300)
        )
    )
    selected_sequence = (
        None
        if project.selected_sequence_id is None
        else session.get(VideoSequence, project.selected_sequence_id)
    )
    return {
        "brief": brief,
        "current_story": current_story,
        "latest_story": latest_story,
        "subjects": subjects,
        "assets": assets,
        "storyboard": storyboard,
        "recipe": recipe,
        "scenes": scenes,
        "beats": beats,
        "shot_cards": shot_cards,
        "tasks": tasks,
        "selected_sequence": selected_sequence,
    }


def _workspace_modules(facts: dict[str, Any]) -> list[dict[str, Any]]:
    brief = facts["brief"]
    current_story = facts["current_story"]
    latest_story = facts["latest_story"]
    subjects: list[Subject] = facts["subjects"]
    assets: list[Asset] = facts["assets"]
    storyboard = facts["storyboard"]
    beats: list[ShotBeat] = facts["beats"]
    shot_cards: list[ShotCard] = facts["shot_cards"]
    tasks: list[WorkflowStep] = facts["tasks"]
    selected_sequence = facts["selected_sequence"]

    active_tasks = [task for task in tasks if task.status in _DIRECTOR_ACTIVE_TASK_STATUSES]
    attention_tasks = [task for task in tasks if task.status in _DIRECTOR_ATTENTION_TASK_STATUSES]
    identity_kinds = {
        subject.kind
        for subject in subjects
        if subject.current_revision_id is not None and subject.kind in {"person", "animal"}
    }
    style_boards = [asset for asset in assets if _is_provider_style_board(asset)]
    candidate_assets = [asset for asset in assets if asset.status == "candidate"]
    stale_assets = [asset for asset in assets if asset.status == "stale"]
    stale_beats = [beat for beat in beats if beat.status == "stale" or beat.stale_reason]
    selected_video_ids = {
        shot.selected_video_asset_id for shot in shot_cards if shot.selected_video_asset_id
    }

    if current_story is not None:
        script_status = WorkspaceStatus.COMPLETE
        script_progress = 100
    elif latest_story is not None:
        script_status = WorkspaceStatus.NEEDS_REVIEW
        script_progress = 70
    elif brief is not None:
        script_status = WorkspaceStatus.ACTIVE
        script_progress = 25
    else:
        script_status = WorkspaceStatus.READY
        script_progress = 0

    missing_identity = {"person", "animal"} - identity_kinds
    if stale_assets:
        asset_status = WorkspaceStatus.STALE
        asset_progress = 70
        asset_blocker = "人物、猫咪或画风参考已有失效版本"
    elif candidate_assets:
        asset_status = WorkspaceStatus.NEEDS_REVIEW
        asset_progress = 75
        asset_blocker = None
    elif not missing_identity and style_boards:
        asset_status = WorkspaceStatus.COMPLETE
        asset_progress = 100
        asset_blocker = None
    elif subjects or assets:
        asset_status = WorkspaceStatus.ACTIVE
        asset_progress = 55
        missing_labels = [
            label
            for key, label in (("person", "儿童身份"), ("animal", "猫咪身份"))
            if key in missing_identity
        ]
        if not style_boards:
            missing_labels.append("可提交 Provider 的净化画风板")
        asset_blocker = None if not missing_labels else f"仍需完成：{'、'.join(missing_labels)}"
    else:
        asset_status = WorkspaceStatus.READY
        asset_progress = 0
        asset_blocker = None

    if current_story is None:
        production_status = WorkspaceStatus.BLOCKED
        production_progress = 0
        production_blocker = "请先设定当前剧情"
    elif missing_identity:
        production_status = WorkspaceStatus.BLOCKED
        production_progress = 10
        production_blocker = "请先完成儿童和猫咪身份 Canon"
    elif stale_beats or stale_assets:
        production_status = WorkspaceStatus.STALE
        production_progress = 55
        production_blocker = "分镜或参考素材已失效，需要重新确认制作输入"
    elif attention_tasks:
        production_status = WorkspaceStatus.NEEDS_REVIEW
        production_progress = 75
        production_blocker = None
    elif active_tasks:
        production_status = WorkspaceStatus.ACTIVE
        production_progress = 80
        production_blocker = None
    elif selected_sequence is not None or selected_video_ids:
        production_status = WorkspaceStatus.COMPLETE
        production_progress = 100
        production_blocker = None
    elif storyboard is not None or beats or shot_cards:
        production_status = WorkspaceStatus.ACTIVE
        production_progress = 55
        production_blocker = None
    else:
        production_status = WorkspaceStatus.READY
        production_progress = 20
        production_blocker = None

    modules = [
        {
            "id": WorkspaceModuleId.SCRIPT.value,
            "title": "剧本",
            "order": 1,
            "status": script_status.value,
            "progress": script_progress,
            "attentionCount": 1 if script_status == WorkspaceStatus.NEEDS_REVIEW else 0,
            "primaryArtifactId": (
                str((current_story or latest_story).id)
                if current_story is not None or latest_story is not None
                else (None if brief is None else str(brief.id))
            ),
            "blocker": None,
            "nextAction": _workspace_next_action(WorkspaceModuleId.SCRIPT, script_status),
        },
        {
            "id": WorkspaceModuleId.ASSETS.value,
            "title": "角色资产",
            "order": 2,
            "status": asset_status.value,
            "progress": asset_progress,
            "attentionCount": len(candidate_assets) + len(stale_assets),
            "primaryArtifactId": None if not assets else str(assets[-1].id),
            "blocker": asset_blocker,
            "nextAction": _workspace_next_action(WorkspaceModuleId.ASSETS, asset_status),
        },
        {
            "id": WorkspaceModuleId.PRODUCTION.value,
            "title": "生产画布",
            "order": 3,
            "status": production_status.value,
            "progress": production_progress,
            "attentionCount": len(attention_tasks) + len(stale_beats),
            "primaryArtifactId": (
                str(storyboard.id)
                if storyboard is not None
                else (None if not shot_cards else str(shot_cards[0].id))
            ),
            "blocker": production_blocker,
            "nextAction": _workspace_next_action(
                WorkspaceModuleId.PRODUCTION, production_status
            ),
        },
    ]
    return modules


def _workspace_next_action(
    module_id: WorkspaceModuleId,
    status: WorkspaceStatus,
) -> dict[str, str]:
    labels = {
        WorkspaceStatus.BLOCKED: "查看阻塞",
        WorkspaceStatus.STALE: "更新失效内容",
        WorkspaceStatus.NEEDS_REVIEW: "继续审核",
        WorkspaceStatus.ACTIVE: "继续制作",
        WorkspaceStatus.COMPLETE: "查看成果",
        WorkspaceStatus.READY: "开始",
    }
    return {"label": labels[status], "moduleId": module_id.value}


def _recommended_workspace_module(modules: Sequence[dict[str, Any]]) -> str:
    precedence = (
        WorkspaceStatus.NEEDS_REVIEW.value,
        WorkspaceStatus.BLOCKED.value,
        WorkspaceStatus.STALE.value,
        WorkspaceStatus.ACTIVE.value,
        WorkspaceStatus.READY.value,
    )
    for status in precedence:
        candidate = next((module for module in modules if module["status"] == status), None)
        if candidate is not None:
            return str(candidate["id"])
    return WorkspaceModuleId.PRODUCTION.value


def _is_provider_style_board(asset: Asset) -> bool:
    semantic_key = (asset.semantic_key or "").lower()
    metadata = asset.metadata_json or {}
    authority = metadata.get("referenceAuthority") or metadata.get("authority") or {}
    role = str(authority.get("role") or metadata.get("purpose") or "").lower()
    provider_eligible = authority.get("providerEligible", metadata.get("providerEligible", True))
    return (
        asset.status == "approved"
        and bool(provider_eligible)
        and (
            role == "style_board"
            or semantic_key.startswith("style:")
            or "style_board" in semantic_key
        )
        and "style_source" not in semantic_key
    )


def _production_flow_nodes(
    project_id: uuid.UUID,
    facts: dict[str, Any],
    layout: CanvasLayout | None,
) -> list[dict[str, Any]]:
    modules = {module["id"]: module for module in _workspace_modules(facts)}
    current_story = facts["current_story"] or facts["latest_story"]
    storyboard = facts["storyboard"]
    recipe = facts["recipe"]
    beats: list[ShotBeat] = facts["beats"]
    scene_titles = {scene.id: scene.title for scene in facts["scenes"]}
    shot_cards: list[ShotCard] = facts["shot_cards"]
    assets: list[Asset] = facts["assets"]
    tasks: list[WorkflowStep] = facts["tasks"]
    selected_sequence = facts["selected_sequence"]
    positions = _production_flow_positions(project_id, layout)
    duration = sum(beat.duration_seconds for beat in beats) or sum(
        shot.duration_seconds for shot in shot_cards
    )
    approved_assets = [asset for asset in assets if asset.status == "approved"]
    storyboard_images = [
        asset
        for asset in assets
        if asset.media_type == "image"
        and asset.role in {"shot_anchor", "storyboard_frame", "scene_look"}
    ]
    selected_videos = [shot for shot in shot_cards if shot.selected_video_asset_id]
    active_tasks = [task for task in tasks if task.status in _DIRECTOR_ACTIVE_TASK_STATUSES]

    rows = (
        (
            ProductionFlowNodeKind.SCRIPT,
            "剧本",
            "尚未设定当前剧情"
            if current_story is None
            else f"R{current_story.revision} · {current_story.title}",
            modules[WorkspaceModuleId.SCRIPT.value]["status"],
            {
                "revision": None if current_story is None else current_story.revision,
                "storyId": None if current_story is None else str(current_story.id),
                "summary": None if current_story is None else current_story.logline,
                "action": "open_script",
            },
        ),
        (
            ProductionFlowNodeKind.DIRECTOR_PLAN,
            "导演计划",
            f"{len(beats) or len(shot_cards)} 镜 · {duration} 秒",
            (
                WorkspaceStatus.COMPLETE.value
                if storyboard is not None and (beats or shot_cards)
                else WorkspaceStatus.READY.value
            ),
            {
                "recipeInstanceId": None if recipe is None else str(recipe.id),
                "storyboardRevisionId": None if storyboard is None else str(storyboard.id),
                "shotCount": len(beats) or len(shot_cards),
                "durationSeconds": duration,
                "shots": [
                    _beat_json(beat, scene_title=scene_titles.get(beat.scene_id))
                    for beat in beats
                ],
                "action": "open_storyboard_editor",
            },
        ),
        (
            ProductionFlowNodeKind.ASSETS,
            "角色与素材",
            f"{len(approved_assets)} 项已批准素材",
            modules[WorkspaceModuleId.ASSETS.value]["status"],
            {
                "approvedCount": len(approved_assets),
                "previewUrls": [
                    f"/api/v1/assets/{asset.id}/content" for asset in approved_assets[-4:]
                ],
                "action": "open_assets",
            },
        ),
        (
            ProductionFlowNodeKind.STORYBOARD_TABLE,
            "分镜表",
            f"{len(beats)} 条分镜 · {duration} 秒",
            (
                WorkspaceStatus.COMPLETE.value
                if beats
                else WorkspaceStatus.BLOCKED.value
            ),
            {
                "recipeInstanceId": None if recipe is None else str(recipe.id),
                "storyboardRevisionId": None if storyboard is None else str(storyboard.id),
                "storyboardRevision": None if storyboard is None else storyboard.revision,
                "shotCount": len(beats),
                "durationSeconds": duration,
                "shots": [
                    _beat_json(beat, scene_title=scene_titles.get(beat.scene_id))
                    for beat in beats
                ],
                "action": "open_storyboard_editor",
            },
        ),
        (
            ProductionFlowNodeKind.STORYBOARD,
            "分镜画面",
            f"{len(storyboard_images)} 项画面参考",
            (
                WorkspaceStatus.NEEDS_REVIEW.value
                if any(asset.status == "candidate" for asset in storyboard_images)
                else (
                    WorkspaceStatus.COMPLETE.value
                    if storyboard_images
                    else WorkspaceStatus.READY.value
                )
            ),
            {
                "assetCount": len(storyboard_images),
                "previewUrls": [
                    f"/api/v1/assets/{asset.id}/content" for asset in storyboard_images[-4:]
                ],
                "action": "open_preview",
            },
        ),
        (
            ProductionFlowNodeKind.WORKBENCH,
            "视频工作台",
            (
                f"{len(selected_videos)} 个已选视频版本"
                if selected_videos
                else (
                    f"{len(active_tasks)} 个任务进行中"
                    if active_tasks
                    else "生成、审核、剪辑与交付"
                )
            ),
            modules[WorkspaceModuleId.PRODUCTION.value]["status"],
            {
                "selectedVideoCount": len(selected_videos),
                "activeTaskCount": len(active_tasks),
                "sequenceId": None if selected_sequence is None else str(selected_sequence.id),
                "action": "open_video_workbench",
            },
        ),
    )
    return [
        {
            "id": str(uuid.uuid5(project_id, f"production-flow:{kind.value}")),
            "kind": kind.value,
            "title": title,
            "subtitle": subtitle,
            "status": status,
            "position": positions[kind.value],
            "data": data,
        }
        for kind, title, subtitle, status, data in rows
    ]


def _production_flow_positions(
    project_id: uuid.UUID,
    layout: CanvasLayout | None,
) -> dict[str, dict[str, float]]:
    defaults = {
        ProductionFlowNodeKind.SCRIPT.value: {"x": 80.0, "y": 90.0},
        ProductionFlowNodeKind.DIRECTOR_PLAN.value: {"x": 420.0, "y": 90.0},
        ProductionFlowNodeKind.ASSETS.value: {"x": 760.0, "y": 90.0},
        ProductionFlowNodeKind.STORYBOARD_TABLE.value: {"x": 760.0, "y": 390.0},
        ProductionFlowNodeKind.STORYBOARD.value: {"x": 420.0, "y": 390.0},
        ProductionFlowNodeKind.WORKBENCH.value: {"x": 80.0, "y": 390.0},
    }
    if layout is None:
        return defaults
    kind_by_id = {
        str(uuid.uuid5(project_id, f"production-flow:{kind.value}")): kind.value
        for kind in ProductionFlowNodeKind
    }
    for item in layout.nodes_json or []:
        kind = kind_by_id.get(str(item.get("nodeId")))
        if kind is None:
            continue
        x = item.get("x")
        y = item.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            defaults[kind] = {"x": float(x), "y": float(y)}
    return defaults


def _production_flow_viewport(layout: CanvasLayout | None) -> dict[str, float]:
    if layout is None:
        return {"x": 0.0, "y": 0.0, "zoom": 0.78}
    viewport = layout.viewport_json or {}
    return {
        "x": float(viewport.get("x", 0)),
        "y": float(viewport.get("y", 0)),
        "zoom": float(viewport.get("zoom", 0.78)),
    }


def _active_track_id(shots: Sequence[ShotCard]) -> str | None:
    selected = next((shot for shot in shots if shot.selected_video_asset_id is not None), None)
    if selected is not None:
        return str(selected.id)
    return None if not shots else str(shots[0].id)


def _reference_provider_eligible(binding: dict[str, Any], asset: Asset) -> bool:
    authority = binding.get("authority") or asset.metadata_json.get("referenceAuthority") or {}
    semantic_key = (asset.semantic_key or "").lower()
    if str(authority.get("role") or "").lower() == "style_source":
        return False
    if "style_source" in semantic_key:
        return False
    return bool(authority.get("providerEligible", True))


def _reference_title(binding: dict[str, Any], asset: Asset) -> str:
    binding_title = binding.get("title")
    metadata = {
        **(asset.metadata_json or {}),
        **(
            {"title": binding_title}
            if isinstance(binding_title, str) and binding_title.strip()
            else {}
        ),
    }
    return reference_display_name(
        semantic_key=asset.semantic_key,
        role=asset.role,
        metadata=metadata,
    )


def _approved_project_references(
    project: ProductionRun,
    assets_by_id: dict[uuid.UUID, Asset],
) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    for ordinal, binding in enumerate(project.default_reference_bindings_json or [], start=1):
        raw_asset_id = binding.get("assetId") or binding.get("asset_id")
        try:
            asset_id = uuid.UUID(str(raw_asset_id))
        except (TypeError, ValueError):
            continue
        asset = assets_by_id.get(asset_id)
        if asset is None or asset.status != "approved":
            continue
        provider_eligible = _reference_provider_eligible(binding, asset)
        if not provider_eligible:
            continue
        references.append(
            {
                "assetId": str(asset.id),
                "title": _reference_title(binding, asset),
                "semanticRole": str(
                    binding.get("semanticRole") or binding.get("role") or asset.role
                ),
                "ordinal": ordinal,
                "providerEligible": True,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "sourceRevision": binding.get("revision"),
            }
        )
    return references


def _video_workbench_track(
    session: Session,
    *,
    project: ProductionRun,
    shot: ShotCard,
    assets: Sequence[Asset],
    assets_by_id: dict[uuid.UUID, Asset],
    tasks: Sequence[WorkflowStep],
) -> dict[str, Any]:
    bindings = shot.reference_bindings_json or project.default_reference_bindings_json or []
    ordered_references: list[dict[str, Any]] = []
    for ordinal, binding in enumerate(bindings, start=1):
        raw_asset_id = binding.get("assetId") or binding.get("asset_id")
        try:
            asset_id = uuid.UUID(str(raw_asset_id))
        except (TypeError, ValueError):
            continue
        asset = assets_by_id.get(asset_id)
        if asset is None or asset.status not in {"approved", "ready"}:
            continue
        if not _reference_provider_eligible(binding, asset):
            continue
        ordered_references.append(
            {
                "assetId": str(asset.id),
                "title": _reference_title(binding, asset),
                "semanticRole": str(
                    binding.get("semanticRole") or binding.get("role") or asset.role
                ),
                "ordinal": ordinal,
                "providerEligible": True,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "sourceRevision": binding.get("revision"),
            }
        )
    prompt = None if shot.prompt_id is None else session.get(PromptRecord, shot.prompt_id)
    shot_tasks = [task for task in tasks if task.shot_card_id == shot.id]
    latest_task = shot_tasks[0] if shot_tasks else None
    video_assets = [
        asset
        for asset in assets
        if asset.shot_card_id == shot.id
        and asset.media_type == "video"
        and asset.role in {"shot_video", "shot_video_edit"}
    ]
    snapshot = {} if latest_task is None else latest_task.input_snapshot_json or {}
    provider_config = {
        "provider": None if latest_task is None else latest_task.provider,
        "model": None if latest_task is None else latest_task.model,
        "mode": snapshot.get("providerInputMode", "reference_media"),
        "durationSeconds": shot.duration_seconds,
        "aspectRatio": snapshot.get("aspectRatio", "9:16"),
        "resolution": snapshot.get("resolution", "720p"),
        "inputHash": None if latest_task is None else latest_task.input_hash,
    }
    task_json = (
        None
        if latest_task is None
        else {
            "id": str(latest_task.id),
            "status": latest_task.status,
            "provider": latest_task.provider,
            "providerTaskId": latest_task.provider_task_id,
            "submittedAt": latest_task.submitted_at,
            "updatedAt": latest_task.updated_at,
            "progress": latest_task.progress_json,
            "error": latest_task.error_json,
        }
    )
    return {
        "id": str(shot.id),
        "shotIds": [str(shot.id)],
        "title": shot.title,
        "durationSeconds": shot.duration_seconds,
        "orderedReferences": ordered_references,
        "prompt": "" if prompt is None else str(prompt.final_prompt or prompt.prompt_text),
        "providerConfig": provider_config,
        "task": task_json,
        "versions": [
            {
                "assetId": str(asset.id),
                "status": asset.status,
                "contentUrl": f"/api/v1/assets/{asset.id}/content",
                "createdAt": asset.created_at,
                "selected": asset.id == shot.selected_video_asset_id,
            }
            for asset in video_assets
        ],
        "selectedVersionId": (
            None if shot.selected_video_asset_id is None else str(shot.selected_video_asset_id)
        ),
    }

def _canvas_node_layout_hint(
    node: dict[str, Any],
    *,
    positioned: bool,
) -> dict[str, Any]:
    """Assign one semantic lane and stable item order to every projected node."""

    node_type = str(node.get("type") or "")
    object_type = str(node.get("objectType") or "")
    data = dict(node.get("data") or {})
    phase = str(data.get("phase") or "")
    lane = "render"
    item_order = int(data.get("order") or data.get("sortOrder") or 0)
    stack_key: str | None = None

    if node_type == CanvasNodeType.SUBJECT.value:
        lane = "canon"
        role = str(data.get("role") or "")
        item_order = {"protagonist": 0, "co_protagonist": 1}.get(role, 10)
        stack_key = "canon_identity"
    elif node_type == CanvasNodeType.STYLE_PRESET.value:
        lane, item_order, stack_key = "canon", 2, "canon_style"
    elif node_type == CanvasNodeType.BRIEF.value:
        lane, item_order = "creative", 0
    elif node_type == CanvasNodeType.STORY_PLANNER.value:
        lane, item_order = "story", 0
    elif node_type == CanvasNodeType.STORY_EVENT.value:
        lane = "story"
        item_order = 10 + int(data.get("candidateIndex") or 0)
        if data.get("isHistoryBranch"):
            item_order += 20
        stack_key = "story_events"
    elif node_type == CanvasNodeType.STORY_SCRIPT.value:
        lane = "story"
        if object_type == "story_script_expander":
            item_order = 60
        else:
            item_order = 70 + int(data.get("revision") or 0)
            stack_key = "story_scripts"
    elif node_type == CanvasNodeType.STORY_CANDIDATE.value:
        lane = "story"
        item_order = 70 + int(data.get("revision") or 0)
        stack_key = "legacy_story_candidates"
    elif node_type == CanvasNodeType.STORY_CRITIC.value:
        lane, item_order = "story", 70
    elif node_type == CanvasNodeType.APPROVAL_GATE.value:
        if phase == "creative" or "creative" in object_type:
            lane, item_order = "creative", 90
        elif object_type == "story_event_selection":
            lane, item_order = "story", 50
        elif phase == "character_design" or "character_design" in object_type:
            lane, item_order = "character_scene", 90
        elif phase == "storyboard" or "storyboard" in object_type:
            lane, item_order = "storyboard", 90
        elif phase in {"export", "complete"} or "final" in object_type:
            lane, item_order = "export", 90
        else:
            lane, item_order = "story", 90
    elif node_type == CanvasNodeType.CHARACTER_DESIGN.value:
        lane = "character_scene"
        item_order = {"child": 0, "cat": 10, "pair_scale": 20}.get(str(data.get("slot") or ""), 30)
        stack_key = "character_design"
    elif node_type == CanvasNodeType.SCENE.value:
        lane = "character_scene"
        item_order = 40 + int(data.get("order") or 0)
        stack_key = "story_scenes"
    elif node_type == CanvasNodeType.STORYBOARD_DIRECTOR.value:
        lane, item_order = "storyboard", 0
    elif node_type == CanvasNodeType.SHOT_BEAT.value:
        lane = "storyboard"
        item_order = 10 + int(data.get("order") or 0)
        stack_key = "shot_beats"
    elif node_type == CanvasNodeType.GENERATION_PLAN.value:
        lane, item_order = "storyboard", 100
    elif node_type == CanvasNodeType.PROMPT_ARTIFACT.value:
        lane, item_order = "storyboard", 80
    elif node_type in {
        CanvasNodeType.IMAGE_GENERATION.value,
        CanvasNodeType.GENERATION_BATCH.value,
    }:
        lane, item_order = "render", 0
    elif node_type in {CanvasNodeType.IMAGE_ASSET.value, CanvasNodeType.REFERENCE_ASSET.value}:
        lane = "render"
        item_order = 20 + int(data.get("order") or 0)
        stack_key = "render_images"
    elif node_type == CanvasNodeType.VIDEO_GENERATION.value:
        lane, item_order = "render", 100
    elif node_type in {
        CanvasNodeType.VIDEO_ASSET.value,
        CanvasNodeType.VIDEO_EDIT.value,
        CanvasNodeType.VIDEO_SEGMENT.value,
    }:
        lane = "render"
        item_order = 120 + int(data.get("order") or 0)
        stack_key = "render_videos"
    elif node_type == CanvasNodeType.REVIEW.value:
        if phase == "character_design":
            lane, item_order = "character_scene", 90
        elif phase == "storyboard":
            lane, item_order = "storyboard", 90
        elif phase in {"export", "complete"} or "final" in object_type:
            lane, item_order = "export", 90
        else:
            lane, item_order = "render", 190
    elif node_type in {CanvasNodeType.TIMELINE.value, CanvasNodeType.AUDIO_GENERATION.value}:
        lane, item_order = "export", 0
    elif phase in _CANVAS_LAYOUT_LANE_ORDER:
        lane = "character_scene" if phase == "character_design" else phase

    hint: dict[str, Any] = {
        "lane": lane,
        "laneOrder": _CANVAS_LAYOUT_LANE_ORDER[lane],
        "itemOrder": item_order,
        "positioned": positioned,
    }
    if stack_key:
        hint["stackKey"] = stack_key
    return hint


def _apply_canvas_layout_hints(
    canvas: dict[str, Any],
    *,
    positioned_node_ids: set[str],
) -> None:
    """Publish layout semantics and give only unpositioned nodes deterministic defaults."""

    nodes = list(canvas.get("nodes", []))
    for node in nodes:
        node["layoutHint"] = _canvas_node_layout_hint(
            node,
            positioned=str(node["id"]) in positioned_node_ids,
        )
    lane_rows: dict[int, int] = {}
    for node in sorted(
        nodes,
        key=lambda item: (
            int(item["layoutHint"]["laneOrder"]),
            int(item["layoutHint"]["itemOrder"]),
            str(item["id"]),
        ),
    ):
        lane_order = int(node["layoutHint"]["laneOrder"])
        row = lane_rows.get(lane_order, 0)
        lane_rows[lane_order] = row + 1
        if str(node["id"]) not in positioned_node_ids:
            node["position"] = {"x": 90 + lane_order * 430, "y": 110 + row * 250}


def _recipe_canvas_group_state(
    session: Session,
    group: CanvasGroup,
) -> dict[str, Any]:
    instance = session.get(
        ProductionRecipeInstance,
        group.production_recipe_instance_id,
    )
    if instance is None:
        return {}
    brief = session.scalar(
        select(StoryBriefRecord)
        .where(StoryBriefRecord.production_run_id == group.production_run_id)
        .order_by(StoryBriefRecord.revision.desc())
        .limit(1)
    )
    creative_decision = (
        None
        if brief is None
        else session.scalar(
            select(HumanReviewDecisionRecord)
            .where(
                HumanReviewDecisionRecord.production_recipe_instance_id == instance.id,
                HumanReviewDecisionRecord.target_type == "creative_brief",
                HumanReviewDecisionRecord.target_id == brief.id,
                HumanReviewDecisionRecord.decision.in_(("approve", "override")),
            )
            .order_by(HumanReviewDecisionRecord.created_at.desc())
            .limit(1)
        )
    )
    story = session.scalar(
        select(StoryRevisionRecord)
        .where(
            StoryRevisionRecord.production_run_id == group.production_run_id,
            StoryRevisionRecord.status == "approved",
        )
        .order_by(StoryRevisionRecord.revision.desc())
        .limit(1)
    )
    character_design = session.scalar(
        select(CharacterDesignRevision)
        .where(CharacterDesignRevision.production_recipe_instance_id == instance.id)
        .order_by(CharacterDesignRevision.revision.desc())
        .limit(1)
    )
    storyboard_revision = session.scalar(
        select(StoryboardRevision)
        .where(
            StoryboardRevision.production_run_id == group.production_run_id,
            StoryboardRevision.status != StoryboardRevisionStatus.SUPERSEDED.value,
        )
        .order_by(StoryboardRevision.revision.desc())
        .limit(1)
    )
    generation_plan = (
        None
        if storyboard_revision is None
        else session.scalar(
            select(GenerationPlan)
            .where(
                GenerationPlan.storyboard_revision_id == storyboard_revision.id,
                GenerationPlan.status == GenerationPlanStatus.APPROVED.value,
            )
            .order_by(GenerationPlan.revision.desc())
            .limit(1)
        )
    )
    storyboard_approved = bool(
        storyboard_revision is not None
        and storyboard_revision.status == StoryboardRevisionStatus.PRODUCTION_APPROVED.value
    )
    shot_ids = (
        []
        if generation_plan is None
        else list(
            session.scalars(
                select(ShotCard.id)
                .where(ShotCard.generation_plan_id == generation_plan.id)
                .order_by(ShotCard.plan_sort_order)
            )
        )
    )
    shots = list(session.scalars(select(ShotCard).where(ShotCard.id.in_(shot_ids))))
    render_approved = (
        bool(shots)
        and len(shots) == len(shot_ids)
        and all(
            shot.selected_anchor_asset_id is not None and shot.selected_video_asset_id is not None
            for shot in shots
        )
    )
    sequence = session.scalar(
        select(VideoSequence)
        .where(VideoSequence.production_run_id == group.production_run_id)
        .order_by(VideoSequence.revision.desc())
        .limit(1)
    )
    complete_by_key = {
        "creative": creative_decision is not None,
        "story": story is not None,
        "character_design": bool(
            character_design is not None and character_design.status == "approved"
        ),
        "storyboard": storyboard_approved,
        "render": render_approved,
        "export": bool(sequence is not None and sequence.status == "approved"),
    }
    labels = {
        "creative": "补全创意输入",
        "story": "AI剧情生成",
        "character_design": "角色设计",
        "storyboard": "分镜生成",
        "render": "视频渲染",
        "export": "成品导出",
    }
    first_incomplete = next(
        (key for key, value in complete_by_key.items() if not value),
        "complete",
    )
    return {
        "phase": first_incomplete,
        "storyboardApproved": storyboard_approved,
        "blocker": (
            None
            if first_incomplete == "complete"
            else f"{labels[first_incomplete]}尚未完成并通过人工审核"
        ),
        "phaseProgress": [
            {
                "key": key,
                "label": labels[key],
                "status": (
                    "complete" if complete else "current" if key == first_incomplete else "blocked"
                ),
            }
            for key, complete in complete_by_key.items()
        ],
    }


def _subject_hash(payload: SubjectDraft) -> str:
    return _json_hash(payload.model_dump(mode="json", by_alias=True))


def _json_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _brief_document(row: StoryBriefRecord) -> dict[str, Any]:
    return {
        "theme": row.theme,
        "audience": row.audience,
        "genre": row.genre,
        "tone": row.tone,
        "aspectRatio": row.aspect_ratio,
        "targetDurationSeconds": row.target_duration_seconds,
        "constraints": row.constraints_json,
    }


def _brief_json(row: StoryBriefRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "revision": row.revision,
        **_brief_document(row),
        "createdAt": None if row.created_at is None else row.created_at.isoformat(),
    }


def _preferred_stored_subjects(
    subjects: list[StoredCanvasSubject],
) -> tuple[StoredCanvasSubject, ...]:
    """Collapse legacy duplicate names at the creative-input compatibility boundary."""

    winners: dict[str, tuple[tuple[int, int], int, StoredCanvasSubject]] = {}
    for index, subject in enumerate(subjects):
        key = subject.draft.name.strip().casefold()
        rank = (
            1 if subject.status in {"ready", "approved"} else 0,
            subject.revision,
        )
        current = winners.get(key)
        if current is None or rank > current[0]:
            winners[key] = (rank, index, subject)
    return tuple(item[2] for item in sorted(winners.values(), key=lambda item: item[1]))


def _preferred_subject_rows(
    session: Session,
    subjects: list[Subject],
) -> list[Subject]:
    """Keep one visible subject per current name, preferring approved Canon evidence."""

    winners: dict[str, tuple[tuple[int, int, int], int, Subject]] = {}
    for index, subject in enumerate(subjects):
        revision = (
            None
            if subject.current_revision_id is None
            else session.get(SubjectRevision, subject.current_revision_id)
        )
        if revision is None:
            continue
        key = revision.name.strip().casefold()
        rank = (
            1 if revision.approval_status == "approved" else 0,
            1 if subject.status in {"ready", "approved"} else 0,
            revision.revision,
        )
        current = winners.get(key)
        if current is None or rank > current[0]:
            winners[key] = (rank, index, subject)
    return [item[2] for item in sorted(winners.values(), key=lambda item: item[1])]


def _subject_draft(
    subject: Subject,
    revision: SubjectRevision,
    references: tuple[SubjectReference, ...],
) -> SubjectDraft:
    return SubjectDraft(
        name=revision.name,
        kind=subject.kind,
        role=subject.role,
        identityAnchors=revision.identity_anchors_json,
        immutableTraits=revision.immutable_traits_json,
        relationshipNotes=revision.relationship_notes,
        dramaticFunction=revision.dramatic_function,
        visualRisks=revision.visual_risks_json,
        references=[
            {
                "assetId": item.asset_id,
                "semanticRole": item.semantic_role,
                "instruction": item.instruction,
            }
            for item in references
        ],
    )


def _subject_json(
    session: Session,
    subject: Subject,
    revision: SubjectRevision,
    references: tuple[SubjectReference, ...],
) -> dict[str, Any]:
    asset_ids = [item.asset_id for item in references]
    assets_by_id = {
        asset.id: asset for asset in session.scalars(select(Asset).where(Asset.id.in_(asset_ids)))
    }
    draft = _subject_draft(subject, revision, references).model_dump(
        mode="json", by_alias=True, exclude={"references"}
    )
    return {
        "id": str(subject.id),
        "projectId": str(subject.production_run_id),
        "revisionId": str(revision.id),
        "revision": revision.revision,
        "status": subject.status,
        "approvalStatus": revision.approval_status,
        **draft,
        "references": [
            {
                "assetId": str(item.asset_id),
                "semanticRole": item.semantic_role,
                "instruction": item.instruction,
                "semanticKey": (
                    None
                    if assets_by_id.get(item.asset_id) is None
                    else assets_by_id[item.asset_id].semantic_key
                ),
                "title": (
                    "视觉参考"
                    if assets_by_id.get(item.asset_id) is None
                    else str(
                        assets_by_id[item.asset_id].metadata_json.get("title")
                        or assets_by_id[item.asset_id].semantic_key
                        or assets_by_id[item.asset_id].role
                    )
                ),
                "contentUrl": f"/api/v1/assets/{item.asset_id}/content",
                "thumbnailUrl": f"/api/v1/assets/{item.asset_id}/content",
                "approvalStatus": (
                    "missing"
                    if assets_by_id.get(item.asset_id) is None
                    else assets_by_id[item.asset_id].status
                ),
                "sha256": (
                    None
                    if assets_by_id.get(item.asset_id) is None
                    else assets_by_id[item.asset_id].sha256
                ),
                "required": bool(
                    assets_by_id.get(item.asset_id) is not None
                    and assets_by_id[item.asset_id].semantic_key
                    in {"person:headshot", "person:fullbody", "cat:front", "cat:side"}
                ),
            }
            for item in references
        ],
    }


def _subject_completion_json(row: SubjectCompletionRun) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "subjectId": str(row.subject_id),
        "sourceRevisionId": str(row.source_revision_id),
        "workflowStepId": (None if row.workflow_step_id is None else str(row.workflow_step_id)),
        "promptId": None if row.prompt_id is None else str(row.prompt_id),
        "status": row.status,
        "model": row.model,
        "missingFields": row.missing_fields_json,
        "proposal": row.proposal_json,
        "acceptedFields": row.accepted_fields_json,
        "acceptedDraft": row.accepted_draft_json,
        "error": row.error_json,
        "createdAt": None if row.created_at is None else row.created_at.isoformat(),
        "completedAt": None if row.completed_at is None else row.completed_at.isoformat(),
    }


def _compiled_reference_binding(
    asset: Asset,
    *,
    role: str,
    purpose: str,
    source: str,
) -> dict[str, Any]:
    metadata = asset.metadata_json or {}
    provider_eligible = bool(metadata.get("providerEligible", True))
    if asset.semantic_key and asset.semantic_key.startswith("style_source:"):
        authority_role = "style_source"
        provider_eligible = False
        priority = 10
        locked_traits: list[str] = []
        mutable_traits: list[str] = []
        forbidden_transfer = list(CANON_V4_STYLE_SOURCE_EXCLUSIONS)
    elif asset.semantic_key and asset.semantic_key.startswith("style:"):
        authority_role = "style_board"
        priority = 50
        locked_traits = ["轮廓线", "材质", "色阶", "光影"]
        mutable_traits = ["与剧情一致的场景颜色和物体"]
        forbidden_transfer = ["具体人物或动物身份", "具体物体与构图"]
    elif source == "character_design" and purpose == "pair_scale":
        authority_role = "pair_scale"
        priority = 80
        locked_traits = ["一人一猫相对比例", "自然接触尺度"]
        mutable_traits = ["姿态", "镜头朝向"]
        forbidden_transfer = ["重新设计人物或猫咪身份", "背景"]
    elif source == "character_design":
        authority_role = "episode_appearance"
        priority = 100
        locked_traits = ["当前本集身份与造型", "本集服装或配件"]
        mutable_traits = ["表情", "动作", "朝向"]
        forbidden_transfer = ["参考图背景", "另一主体身份"]
    elif source == "scene":
        authority_role = "environment"
        priority = 70
        locked_traits = ["当前空间陈设", "布局", "环境光"]
        mutable_traits = ["镜头构图", "与动作一致的局部状态"]
        forbidden_transfer = ["人物或猫咪身份"]
    else:
        authority_role = "identity"
        priority = 100
        locked_traits = ["长期角色身份与基础结构"]
        mutable_traits = ["本集造型", "表情", "动作", "朝向"]
        forbidden_transfer = ["参考图背景", "未批准服装或配件"]
    return {
        "assetId": str(asset.id),
        "role": role,
        "purpose": purpose,
        "source": source,
        "semanticKey": asset.semantic_key,
        "title": reference_display_name(
            semantic_key=asset.semantic_key,
            role=asset.role,
            metadata=metadata,
        ),
        "sha256": asset.sha256,
        "authority": {
            "role": authority_role,
            "providerEligible": provider_eligible,
            "priority": priority,
            "lockedTraits": locked_traits,
            "mutableTraits": mutable_traits,
            "forbiddenTransfer": forbidden_transfer,
        },
    }


def _compile_provider_reference_manifest(
    references: list[dict[str, Any]],
    *,
    maximum: int,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Compile stable provider slots while never silently dropping required evidence."""

    compiled: list[dict[str, Any]] = []
    blockers: list[str] = []
    warnings: list[str] = []
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    provider_count = 0
    episode_authorities: dict[str, list[str]] = {}
    for reference in references:
        asset_id = str(reference.get("assetId") or "")
        sha256 = str(reference.get("sha256") or "")
        if not asset_id or not sha256:
            blockers.append("生产引用缺少素材 ID 或 SHA，不能冻结供应商输入")
            continue
        if asset_id in seen_ids or sha256 in seen_hashes:
            continue
        seen_ids.add(asset_id)
        seen_hashes.add(sha256)
        ordinal = len(compiled) + 1
        authority = reference.get("authority") or {}
        provider_eligible = bool(authority.get("providerEligible", True))
        if authority.get("role") == "episode_appearance":
            purpose = str(reference.get("purpose") or "")
            subject_key = "cat" if "cat" in purpose else "child" if "child" in purpose else purpose
            episode_authorities.setdefault(subject_key, []).append(asset_id)
        optional = bool(
            reference.get("source") == "scene"
            and reference.get("purpose") in {"prop", "decoration", "optional_prop"}
        )
        provider_included = provider_eligible and provider_count < maximum
        omission_reason = None
        if not provider_eligible:
            omission_reason = "该素材仅用于画风提炼或审计血缘，不允许提交给日常 Provider"
        elif not provider_included:
            omission_reason = f"Seedream 最多接受 {maximum} 张参考图"
            if optional:
                warnings.append(
                    f"可选场景参考“{reference.get('title') or asset_id}”因模型上限未提交"
                )
            else:
                blockers.append(
                    f"必需引用“{reference.get('title') or asset_id}”超出模型上限，不能静默省略"
                )
        if provider_included:
            provider_count += 1
        compiled.append(
            {
                **reference,
                "sourceNodeId": reference.get("sourceNodeId"),
                "sourceType": reference.get("source") or "production_package",
                "semanticRole": reference.get("role") or "reference",
                "instruction": reference.get("instruction") or "",
                "ordinal": ordinal,
                "locked": not optional,
                "providerIncluded": provider_included,
                "providerSlot": (
                    f"reference_image_{provider_count}" if provider_included else None
                ),
                "omissionReason": omission_reason,
                "origin": reference.get("source") or "production_package",
                "contentUrl": f"/api/v1/assets/{asset_id}/content",
                "evidenceLevel": "frozen",
            }
        )
    for subject_key, asset_ids in episode_authorities.items():
        if len(set(asset_ids)) > 1:
            blockers.append(
                f"{subject_key} 同时存在多个本集身份权威参考；请在费用确认前只保留一个"
            )
    return compiled, blockers, warnings


def _scene_prompt_bindings(
    session: Session,
    scene: Scene,
    profile: VisualProfileRevision,
    *,
    storyboard_revision: StoryboardRevision | None = None,
    generation_plan: GenerationPlan | None = None,
    scene_look_usage: str = "off",
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    plan_steps = list(
        session.scalars(
            select(WorkflowStep)
            .where(
                WorkflowStep.production_run_id == scene.production_run_id,
                WorkflowStep.scene_id == scene.id,
                WorkflowStep.operation_key == "director:visual-asset-plan",
                WorkflowStep.status == StepStatus.SUCCEEDED.value,
            )
            .order_by(
                WorkflowStep.completed_at.desc().nullslast(),
                WorkflowStep.created_at.desc(),
            )
        )
    )
    accepted_plan_step = next(
        (
            step
            for step in plan_steps
            if isinstance(step.input_snapshot_json.get("acceptedOutput"), dict)
            and (
                storyboard_revision is None
                or generation_plan is None
                or (
                    step.input_snapshot_json.get("storyboardRevisionId")
                    == str(storyboard_revision.id)
                    and step.input_snapshot_json.get("structureHash")
                    == storyboard_revision.structure_hash
                    and step.input_snapshot_json.get("generationPlanId") == str(generation_plan.id)
                    and step.input_snapshot_json.get("generationPlanHash")
                    == generation_plan.input_hash
                )
            )
        ),
        None,
    )
    accepted_plan = (
        None
        if accepted_plan_step is None
        else accepted_plan_step.input_snapshot_json.get("acceptedOutput")
    )
    if not isinstance(accepted_plan, dict):
        blockers.append("尚未人工接受当前分镜结构与生成编排对应的视觉资产规划")
    selections = (
        accepted_plan.get("selections", [])
        if isinstance(accepted_plan, dict)
        else []
    )
    explicit_selections = isinstance(selections, list) and bool(selections)

    look_draft = scene.look_draft_json or {}
    if not look_draft:
        blockers.append("当前场景尚未准备视觉档案与素材引用")
    elif str(look_draft.get("visualProfileRevisionId")) != str(profile.id):
        blockers.append("场景视觉档案已过期，请基于当前本集视觉档案重新准备")

    raw_bindings = look_draft.get("referenceBindings", [])
    if not isinstance(raw_bindings, list):
        raw_bindings = []
        blockers.append("场景参考绑定格式无效")
    bound_ids: list[uuid.UUID] = []
    binding_by_id: dict[uuid.UUID, dict[str, Any]] = {}
    for document in raw_bindings:
        if not isinstance(document, dict) or not document.get("assetId"):
            continue
        try:
            asset_id = uuid.UUID(str(document["assetId"]))
        except ValueError:
            blockers.append("场景参考中包含无效素材标识")
            continue
        bound_ids.append(asset_id)
        binding_by_id[asset_id] = document
    assets_by_id = {
        asset.id: asset for asset in session.scalars(select(Asset).where(Asset.id.in_(bound_ids)))
    }
    result: list[dict[str, Any]] = []
    ready_purposes: set[str] = set()
    ready_prop_titles: list[str] = []
    purpose_roles = {
        "wardrobe": "appearance",
        "environment": "environment",
        "prop": "prop",
        "composition": "composition",
    }
    for asset_id in bound_ids:
        document = binding_by_id[asset_id]
        purpose = str(document.get("purpose") or "")
        if purpose not in purpose_roles:
            continue
        asset = assets_by_id.get(asset_id)
        if (
            asset is None
            or asset.production_run_id not in {None, scene.production_run_id}
            or asset.media_type != "image"
            or asset.status not in {"approved", "ready"}
        ):
            blockers.append(f"场景素材 {asset_id} 不可用或尚未批准")
            continue
        ready_purposes.add(purpose)
        binding = _compiled_reference_binding(
            asset,
            role=purpose_roles[purpose],
            purpose=purpose,
            source="scene",
        )
        result.append(binding)
        if purpose == "prop":
            ready_prop_titles.append(str(binding["title"]))

    if explicit_selections:
        for selection in selections:
            if not isinstance(selection, dict) or selection.get("action") == "skip":
                continue
            purpose = str(selection.get("purpose") or "")
            if purpose and purpose not in ready_purposes:
                blockers.append(
                    f"缺少已批准并绑定的场景素材：{selection.get('displayName') or purpose}"
                )
    else:
        if "wardrobe" not in ready_purposes:
            blockers.append("缺少已批准并绑定的本集服饰/配件素材")
        if "environment" not in ready_purposes:
            blockers.append("缺少已批准并绑定的当前场景环境素材")

    continuity: dict[str, Any] = {}
    if scene.context_note:
        try:
            context = json.loads(scene.context_note)
        except (TypeError, json.JSONDecodeError):
            context = {}
        if isinstance(context, dict) and isinstance(context.get("continuity"), dict):
            continuity = context["continuity"]
    required_objects: list[str] = []
    for field in ("decorations", "props"):
        values = continuity.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            label = str(value).strip()
            if label and label not in required_objects:
                required_objects.append(label)
    normalized_prop_titles = ["".join(item.lower().split()) for item in ready_prop_titles]
    for label in required_objects if not explicit_selections else []:
        normalized_label = "".join(label.lower().split())
        if not any(
            normalized_label in title or title in normalized_label
            for title in normalized_prop_titles
            if title
        ):
            blockers.append(f"缺少已批准并绑定的场景道具/装饰：{label}")
    if not required_objects and not explicit_selections:
        warnings.append("剧情未声明必需装饰或道具，本镜头仅使用场景环境与文本约束")

    selected_look = (
        None
        if scene.selected_look_asset_id is None
        else session.get(Asset, scene.selected_look_asset_id)
    )
    if scene_look_usage != "off" and selected_look is None:
        blockers.append("已启用 Scene Look，但尚未选择已批准的场景视觉基准")
    elif scene_look_usage != "off" and selected_look is not None:
        look_revision = (selected_look.metadata_json or {}).get("lookDraftRevision")
        if (
            selected_look.production_run_id not in {None, scene.production_run_id}
            or selected_look.media_type != "image"
            or selected_look.status != "approved"
            or look_revision not in {None, scene.look_draft_revision}
        ):
            blockers.append("已选择的场景视觉基准不可用或已过期")
        elif selected_look.id not in {uuid.UUID(item["assetId"]) for item in result}:
            result.append(
                _compiled_reference_binding(
                    selected_look,
                    role="environment",
                    purpose="scene_look",
                    source="scene",
                )
            )

    return result, list(dict.fromkeys(blockers)), list(dict.fromkeys(warnings))


def _compile_storyboard_prompt_text(
    *,
    profile: VisualProfileRevision,
    story: StoryRevisionRecord,
    scene: Scene,
    shot: dict[str, Any],
    reference_bindings: list[dict[str, Any]],
    healing_recipe: bool,
) -> str:
    episode_rules = story.episode_rules_json or {}
    continuity: dict[str, Any] = {}
    if scene.context_note:
        try:
            context = json.loads(scene.context_note)
        except (TypeError, json.JSONDecodeError):
            context = {}
        if isinstance(context, dict) and isinstance(context.get("continuity"), dict):
            continuity = context["continuity"]
    temporal_beats = shot.get("temporalBeats") or []
    director_shots = shot.get("directorShots") or []
    direction = str(shot.get("direction") or shot.get("action") or "").strip()
    camera = str(shot.get("camera") or "").strip()
    lighting = str(shot.get("lighting") or "").strip()
    shot_size = str(shot.get("shotSize") or "").strip()
    sound_effect = str(shot.get("soundEffect") or "").strip()
    dialogue = str(shot.get("dialogue") or "").strip()
    optional_visual_parameters = "；".join(
        item
        for item in (
            f"景别：{shot_size}" if shot_size else "",
            f"光影：{lighting}" if lighting else "",
            f"运镜：{camera}" if camera else "",
        )
        if item
    )
    optional_sound_parameters = "；".join(
        item
        for item in (
            f"声音：{sound_effect}" if sound_effect else "",
            f"对白：{dialogue}" if dialogue else "",
        )
        if item
    )
    provider_references = [
        item for item in reference_bindings if item.get("providerIncluded", True)
    ]
    reference_lines: list[str] = []
    for index, item in enumerate(provider_references, 1):
        authority = item.get("authority") or {}
        authority_role = str(authority.get("role") or item.get("role") or "reference")
        purpose = str(item.get("purpose") or "")
        display_title = {
            "child": "本集儿童设计",
            "cat": "本集猫咪设计",
            "pair_scale": "一人一猫同框比例",
        }.get(purpose, str(item.get("title") or purpose or "参考"))
        responsibility = {
            "episode_appearance": "当前唯一身份与本集造型来源",
            "pair_scale": "只锁定一人一猫相对比例与自然接触尺度",
            "environment": "只锁定当前空间陈设、布局与环境光，不改变角色",
            "style_board": "只锁定轮廓线、材质、色阶与渲染语言，不添加具体内容",
            "identity": "只锁定长期身份与基础身体结构",
        }.get(authority_role, str(item.get("instruction") or "只承担已声明的参考职责"))
        reference_lines.append(
            f"- @图片{index}「{display_title}」："
            f"{responsibility}。"
        )

    time_beat_lines: list[str] = []
    for index, beat in enumerate(director_shots or temporal_beats, 1):
        if not isinstance(beat, dict):
            continue
        start = beat.get("startSecond", beat.get("startSeconds"))
        end = beat.get("endSecond", beat.get("endSeconds"))
        label = f"{start}–{end} 秒" if start is not None and end is not None else f"节拍 {index}"
        beat_title = str(beat.get("title") or "").strip()
        beat_direction = str(beat.get("direction") or "").strip()
        if beat_direction:
            # A saved director direction is the complete editable creative
            # document for this window. Advanced fields are legacy/optional
            # projections and must not repeat or contradict that document.
            details = [value for value in (beat_title, beat_direction) if value]
        else:
            details = [
                str(value).strip()
                for key in (
                    "title",
                    "visualDescription",
                    "childAction",
                    "catAction",
                    "spatialRelation",
                    "camera",
                )
                if (value := beat.get(key)) and str(value).strip()
            ]
        if details:
            time_beat_lines.append(f"- {label}：{'；'.join(dict.fromkeys(details))}。")

    continuity_values = [
        str(value).strip()
        for value in continuity.values()
        if isinstance(value, (str, int, float)) and str(value).strip()
    ]
    person_wardrobe = str(
        episode_rules.get("personWardrobe") or "沿用已批准的本集儿童设计"
    )
    time_weather = str(episode_rules.get("timeWeather") or "沿用当前场景")
    main_scene = str(episode_rules.get("mainScene") or scene.title)
    core_props = [str(item) for item in episode_rules.get("coreProps") or [] if str(item)]
    sections = [
        f"任务：生成一个 9:16、{shot['durationSeconds']} 秒的原创二维治愈生活短片。",
        "参考职责：\n"
        + ("\n".join(reference_lines) if reference_lines else "当前没有可提交的视觉参考。"),
        "身份连续性：\n"
        f"- 儿童始终是同一个 8–9 岁儿童。{profile.person_identity}；"
        f"{profile.person_hair}；{profile.person_body}。"
        "本片段保持已批准本集儿童设计中的服装。\n"
        f"- 猫咪始终是同一只灰白虎斑猫。{profile.cat_identity}。始终保持真实四足猫科结构。\n"
        "参考冲突时的优先级：儿童/猫咪本集身份 > 人猫同框比例 > 当前环境空间 > "
        "画风板渲染语言 > 镜头可变动作。",
        "画风：\n"
        f"{'；'.join(profile.style_positive_json)}。画风板只控制渲染语言，不自动添加其中不存在的物体、颜色或构图。",
        "本集与场景：\n"
        f"本集造型：{person_wardrobe}。时间与天气：{time_weather}。主要空间：{main_scene}。\n"
        f"当前场景《{scene.title}》：{scene.source_text}。"
        + (f"核心道具：{'、'.join(core_props)}。" if core_props else "")
        + (
            f"连续性要求：{'；'.join(dict.fromkeys(continuity_values))}。"
            if continuity_values
            else ""
        ),
        "镜头正文：\n"
        f"镜头 {shot['order']}《{shot['title']}》。\n"
        + (
            f"完整镜头方向：{direction}。\n"
            if direction and not time_beat_lines
            else ""
        )
        + (f"可选导演参数：{optional_visual_parameters}。\n" if optional_visual_parameters else "")
        + ("时间节拍：\n" + "\n".join(time_beat_lines) if time_beat_lines else ""),
        "运动与声音：\n"
        + (optional_sound_parameters or "未提供额外声音或对白要求。")
        + ("\n保持短时、低对白、日常陪伴的原创治愈叙事节奏。" if healing_recipe else ""),
        "连续性与排除项：\n"
        + "、".join(
            [
                *profile.style_negative_json,
                "儿童身份漂移或年龄变化",
                "猫咪毛色、脸型或身体结构变化",
                "猫咪出现人手、人形肢体或未批准的直立劳动",
                "从画风板无理由复制具体物体、颜色或构图",
                "跨场景环境与道具串用",
                "额外人物、额外猫咪、文字、水印",
            ]
        ),
    ]
    return "\n\n".join(sections)


def _shot_matches_persisted_beat(shot: dict[str, Any], beat: ShotBeat) -> bool:
    return bool(
        int(shot["order"]) == beat.sort_order
        and str(shot["title"]).strip() == beat.title.strip()
        and str(shot.get("direction") or shot.get("action") or "").strip()
        == beat.action.strip()
        and str(shot.get("camera") or "").strip() == (beat.camera or "").strip()
        and str(shot.get("dialogue") or "").strip() == (beat.dialogue or "").strip()
        and int(shot["durationSeconds"]) == beat.duration_seconds
    )


def _create_generation_plan_for_beats(
    session: Session,
    *,
    storyboard: StoryboardRevision,
    beats: list[ShotBeat],
) -> GenerationPlan:
    capability = SEEDANCE_2_0_CAPABILITY
    blockers: list[str] = []
    try:
        proposals = plan_generation_clips(
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
        proposals = ()
        blockers.append(str(exc))
    clip_documents = [
            {
                "durationSeconds": proposal.duration_seconds,
                "shotBeatIds": [str(item) for item in proposal.shot_beat_ids],
            }
            for proposal in proposals
        ]
    plan = GenerationPlan(
        id=uuid.uuid4(),
        storyboard_revision_id=storyboard.id,
        revision=1,
        status=GenerationPlanStatus.PROPOSED.value,
        provider=capability.provider,
        model=capability.model,
        capability_revision=capability.capability_revision,
        input_hash=generation_plan_input_hash(
            structure_hash=storyboard.structure_hash,
            provider=capability.provider,
            model=capability.model,
            capability_revision=capability.capability_revision,
            clips=clip_documents,
        ),
        estimated_image_call_count=len(proposals),
        estimated_video_call_count=len(proposals),
        estimated_cost_micros=None,
        warnings_json=[f"Agent 将 {len(beats)} 个导演分镜编排为 {len(proposals)} 个生成片段"],
        blockers_json=blockers,
    )
    session.add(plan)
    session.flush()
    beats_by_id = {beat.id: beat for beat in beats}
    clip_order_by_scene: dict[uuid.UUID, int] = {}
    for plan_order, proposal in enumerate(proposals, 1):
        clip_beats = [beats_by_id[beat_id] for beat_id in proposal.shot_beat_ids]
        scene_id = clip_beats[0].scene_id
        clip_order_by_scene[scene_id] = clip_order_by_scene.get(scene_id, 0) + 1
        direction_lines: list[str] = []
        for index, beat in enumerate(clip_beats, 1):
            parts = [f"分镜{index}：{beat.visual_description or beat.action}"]
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
            direction_lines.append("；".join(parts))
        direction = "\n".join(direction_lines)
        clip = ShotCard(
            id=uuid.uuid4(),
            scene_id=scene_id,
            sort_order=clip_order_by_scene[scene_id],
            plan_sort_order=plan_order,
            title=(
                clip_beats[0].title
                if len(clip_beats) == 1
                else f"{clip_beats[0].title}—{clip_beats[-1].title}"
            )[:100],
            direction=direction,
            duration_seconds=proposal.duration_seconds,
            generation_plan_id=plan.id,
            anchor_mode="text_only",
            reference_bindings_json=[],
            inherit_project_references=True,
            use_scene_look=False,
            scene_look_usage="off",
            draft_revision=1,
            status="ready",
        )
        session.add(clip)
        session.flush()
        cursor = 0
        for ordinal, beat in enumerate(clip_beats, 1):
            beat.shot_card_id = clip.id
            session.add(
                GenerationClipShot(
                    id=uuid.uuid4(),
                    generation_plan_id=plan.id,
                    shot_card_id=clip.id,
                    shot_beat_id=beat.id,
                    ordinal=ordinal,
                    start_second=cursor,
                    end_second=cursor + beat.duration_seconds,
                    transition_in=beat.cut_intent,
                )
            )
            cursor += beat.duration_seconds
    return plan


def _scorecard(row: StoryScore) -> StoryScorecard:
    return StoryScorecard(
        openingHook=row.opening_hook,
        causalCompleteness=row.causal_completeness,
        subjectNecessity=row.subject_necessity,
        emotionalArc=row.emotional_arc,
        visualizability=row.visualizability,
        durationFit=row.duration_fit,
        continuityRisk=row.continuity_risk,
        safety=row.safety,
        rationale=row.rationale,
        warnings=row.warnings_json,
    )


def _story_json(
    row: StoryRevisionRecord,
    score: StoryScore | None,
    candidate_prompt: PromptRecord | None = None,
) -> dict[str, Any]:
    is_approved = row.status == StoryRevisionStatus.APPROVED.value
    scenes = _normalized_story_scenes(row)
    scorecard = (
        None
        if score is None
        else {
            **_scorecard(score).model_dump(mode="json", by_alias=True),
            "average": _scorecard(score).average,
        }
    )
    diagnostics: list[dict[str, Any]] = []
    if candidate_prompt is not None:
        structured_response = candidate_prompt.structured_response_json or {}
        stored_diagnostics = structured_response.get("diagnostics")
        if isinstance(stored_diagnostics, list):
            diagnostics = [dict(item) for item in stored_diagnostics if isinstance(item, dict)]
    source = (
        "ai"
        if candidate_prompt is not None
        else "manual"
        if row.parent_revision_id is not None or row.strategy == StoryStrategy.LEGACY_IMPORT.value
        else "unknown"
    )
    contract_kind = story_revision_contract_kind(
        row,
        legacy_score_present=scorecard is not None,
    )
    legacy_details = (
        None if not scenes and scorecard is None else {"scenes": scenes, "scorecard": scorecard}
    )
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "briefId": None if row.brief_id is None else str(row.brief_id),
        "parentRevisionId": (
            None if row.parent_revision_id is None else str(row.parent_revision_id)
        ),
        "sourceEventCandidateId": (
            None if row.source_event_candidate_id is None else str(row.source_event_candidate_id)
        ),
        "revision": row.revision,
        "strategy": row.strategy,
        "status": row.status,
        "artifactLabel": "剧情脚本定稿" if is_approved else "剧情候选",
        "isCanonicalStory": is_approved,
        "title": row.title,
        "body": row.synopsis,
        "summary": row.logline,
        "source": source,
        "contractKind": contract_kind,
        "warnings": diagnostics,
        "legacyDetails": legacy_details,
        "logline": row.logline,
        "synopsis": row.synopsis,
        "subjectIds": row.subject_ids_json,
        "scenes": scenes,
        "episodeRules": row.episode_rules_json or None,
        "candidatePromptId": (
            None if row.candidate_prompt_id is None else str(row.candidate_prompt_id)
        ),
        "criticPromptId": (None if row.critic_prompt_id is None else str(row.critic_prompt_id)),
        "scorecard": scorecard,
        "approvedAt": None if row.approved_at is None else row.approved_at.isoformat(),
    }


def _story_event_json(row: StoryEventCandidateRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "recipeInstanceId": str(row.production_recipe_instance_id),
        "storyBriefId": str(row.story_brief_id),
        "batchId": str(row.batch_id),
        "candidateIndex": row.candidate_index,
        "revision": row.revision,
        "strategy": row.strategy,
        "status": row.status,
        "title": row.title,
        "premise": row.premise,
        "childAction": row.child_action,
        "catParticipation": row.cat_participation,
        "smallChange": row.small_change,
        "warmEnding": row.warm_ending,
        "suggestedScenes": row.suggested_scenes_json,
        "durationFitSummary": row.duration_fit_summary,
        "requiresSceneChange": row.requires_scene_change,
        "catBehaviorModeSuggestion": row.cat_behavior_mode_suggestion,
        "scorecard": row.score_json,
        "generationPromptId": (
            None if row.generation_prompt_id is None else str(row.generation_prompt_id)
        ),
        "selectedAt": None if row.selected_at is None else row.selected_at.isoformat(),
        "createdAt": row.created_at.isoformat(),
    }


def _attempt_json(row: GenerationAttempt) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "projectId": str(row.production_run_id),
        "businessObjectType": row.business_object_type,
        "businessObjectId": str(row.business_object_id),
        "idempotencyKey": row.idempotency_key,
        "provider": row.provider,
        "model": row.model,
        "status": row.status,
        "providerTaskId": row.provider_task_id,
        "request": row.request_json,
        "response": row.response_json,
        "error": row.error_json,
        "retryOfId": None if row.retry_of_id is None else str(row.retry_of_id),
    }


def _beat_json(
    row: ShotBeat,
    prompt: PromptRecord | None = None,
    *,
    generation_clip_id: uuid.UUID | None = None,
    thumbnail_asset: Asset | None = None,
    scene_title: str | None = None,
) -> dict[str, Any]:
    document = {
        "id": str(row.id),
        "sceneId": str(row.scene_id),
        "sceneTitle": scene_title,
        "storyRevisionId": (None if row.story_revision_id is None else str(row.story_revision_id)),
        "storyboardRevisionId": (
            None if row.storyboard_revision_id is None else str(row.storyboard_revision_id)
        ),
        "generationClipId": (
            None
            if generation_clip_id is None and row.shot_card_id is None
            else str(generation_clip_id or row.shot_card_id)
        ),
        "promptId": None if row.prompt_id is None else str(row.prompt_id),
        "order": row.sort_order,
        "revision": row.revision,
        "referenceBindings": row.reference_bindings_json,
        "referenceBindingRevision": row.reference_binding_revision,
        "title": row.title,
        "direction": row.action,
        "action": row.action,
        "visualDescription": row.visual_description,
        "childAction": row.child_action,
        "catAction": row.cat_action,
        "spatialRelation": row.spatial_relation,
        "contactOcclusion": row.contact_occlusion,
        "shotSize": row.shot_size,
        "camera": row.camera,
        "lighting": row.lighting,
        "dialogue": row.dialogue,
        "soundEffect": row.sound_effect,
        "musicIntent": row.music_intent,
        "wardrobeState": row.wardrobe_state,
        "propState": row.prop_state,
        "continuityIn": row.continuity_in,
        "continuityOut": row.continuity_out,
        "cutIntent": row.cut_intent,
        "durationSeconds": row.duration_seconds,
        "temporalBeats": row.temporal_beats_json,
        "status": row.status,
        "staleReason": row.stale_reason,
        "thumbnailUrl": (
            None if thumbnail_asset is None else f"/api/v1/assets/{thumbnail_asset.id}/content"
        ),
        "thumbnailSource": (
            None
            if thumbnail_asset is None
            else "video_frame"
            if thumbnail_asset.role == "editorial_thumbnail"
            else "approved_anchor"
        ),
    }
    if prompt is not None:
        structured = prompt.structured_response_json or {}
        document.update(
            {
                "finalPrompt": prompt.final_prompt or prompt.prompt_text,
                "promptInputHash": prompt.input_hash,
                "referenceBindings": structured.get("referenceBindings", []),
                "promptWarnings": structured.get("warnings", []),
                "promptBlockers": structured.get("blockers", []),
            }
        )
    return document


def _storyboard_json(
    project_id: uuid.UUID,
    story_revision_id: uuid.UUID,
    beats: list[ShotBeat],
) -> dict[str, Any]:
    return {
        "projectId": str(project_id),
        "storyRevisionId": str(story_revision_id),
        "status": "ready",
        "targetDurationSeconds": sum(item.duration_seconds for item in beats),
        "beats": [_beat_json(item) for item in beats],
    }


def _storyboard_prompt_projection(prompt: PromptRecord | None) -> dict[str, Any]:
    """Restore tolerant storyboard output from the durable Prompt audit boundary."""

    if prompt is None or not isinstance(prompt.structured_response_json, dict):
        return {}
    structured = prompt.structured_response_json
    status = structured.get("status")
    if status not in {"ready", "needs_structuring"}:
        return {}
    diagnostics: list[dict[str, Any]] = []
    for item in structured.get("diagnostics", []):
        try:
            diagnostic = CanvasDiagnostic.model_validate(item)
        except (TypeError, ValueError):
            continue
        diagnostics.append(diagnostic.model_dump(mode="json", by_alias=True))
    projection: dict[str, Any] = {
        "storyboardDraftStatus": status,
        "storyboardPromptId": str(prompt.id),
        "diagnostics": diagnostics,
    }
    if status == "needs_structuring":
        raw_text = structured.get("rawText")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raw_response = prompt.raw_response_json or {}
            raw_text = raw_response.get("text") if isinstance(raw_response, dict) else None
        projection["rawStoryboardText"] = raw_text or ""
        projection["blocker"] = next(
            (
                item["message"]
                for item in diagnostics
                if item["severity"] == "blocker"
            ),
            "分镜原文需要整理为可执行镜头",
        )
    return projection


def _prompt_json(prompt: PromptRecord, step: WorkflowStep) -> dict[str, Any]:
    return {
        "id": str(prompt.id),
        "stepId": str(step.id),
        "purpose": prompt.call_purpose or prompt.purpose,
        "nodeId": None if prompt.node_id is None else str(prompt.node_id),
        "businessObjectType": prompt.business_object_type,
        "businessObjectId": (
            None if prompt.business_object_id is None else str(prompt.business_object_id)
        ),
        "parentRunId": (None if prompt.parent_prompt_id is None else str(prompt.parent_prompt_id)),
        "templateName": prompt.template_name or "legacy_unavailable",
        "templateVersion": prompt.template_version or "legacy_unavailable",
        "systemPrompt": prompt.system_prompt,
        "userPrompt": prompt.user_prompt,
        "finalPrompt": prompt.final_prompt or prompt.prompt_text,
        "providerInternalTransform": prompt.provider_internal_transform,
        "providerRequestSnapshot": prompt.provider_request_json,
        "inputSnapshot": prompt.input_snapshot_json,
        "provider": step.provider,
        "model": prompt.model,
        "parameters": prompt.parameters_json,
        "rawResponse": prompt.raw_response_json,
        "structuredResponse": prompt.structured_response_json,
        "acceptedResponse": prompt.accepted_response_json,
        "responseDiff": prompt.response_diff_json,
        "tokenUsage": prompt.token_usage_json,
        "costMicros": prompt.cost_micros,
        "durationMs": prompt.duration_ms,
        "status": prompt.status,
        "error": prompt.error_json,
        "inputHash": prompt.input_hash,
        "outputHash": prompt.output_hash,
        "retryChain": step.retry_chain_json,
        "createdAt": prompt.created_at.isoformat(),
        "completedAt": (None if prompt.completed_at is None else prompt.completed_at.isoformat()),
    }


def _canvas_json(
    project_id: uuid.UUID,
    *,
    layout: CanvasLayout | None,
    brief: StoryBriefRecord | None,
    subjects: list[Subject],
    story_events: list[StoryEventCandidateRecord],
    stories: list[StoryRevisionRecord],
    scenes: list[Scene],
    beats: list[ShotBeat],
    session: Session,
    enabled: bool,
    include_narrative_projection: bool = True,
    storyboard_prompt: PromptRecord | None = None,
) -> dict[str, Any]:
    brief_node_id = creative_brief_canvas_node_id(project_id)
    planner_id = uuid.uuid5(project_id, "story-planner")
    event_selection_id = uuid.uuid5(project_id, "story-event-selection")
    script_expander_id = uuid.uuid5(project_id, "story-script-expander")
    approval_id = uuid.uuid5(project_id, "story-approval")
    storyboard_id = uuid.uuid5(project_id, "storyboard-director")
    approved_story = next(
        (
            story
            for story in reversed(stories)
            if story.status == StoryRevisionStatus.APPROVED.value
        ),
        None,
    )
    recipe_instance = session.scalar(
        select(ProductionRecipeInstance).where(
            ProductionRecipeInstance.production_run_id == project_id,
            ProductionRecipeInstance.lifecycle_status == "active",
        )
    )
    storyboard_revision = session.scalar(
        select(StoryboardRevision)
        .where(
            StoryboardRevision.production_run_id == project_id,
            StoryboardRevision.status != "superseded",
            *(
                ()
                if approved_story is None
                else (StoryboardRevision.story_revision_id == approved_story.id,)
            ),
        )
        .order_by(StoryboardRevision.revision.desc())
        .limit(1)
    )
    if storyboard_revision is not None:
        beats = [beat for beat in beats if beat.storyboard_revision_id == storyboard_revision.id]
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
    clip_ids = list(dict.fromkeys(item.shot_card_id for item in clip_mappings))
    generation_clips = list(
        session.scalars(
            select(ShotCard).where(ShotCard.id.in_(clip_ids)).order_by(ShotCard.plan_sort_order)
        )
    )
    clips_by_id = {clip.id: clip for clip in generation_clips}
    clip_assets = list(
        session.scalars(
            select(Asset).where(Asset.shot_card_id.in_(clip_ids)).order_by(Asset.created_at)
        )
    )
    assets_by_clip: dict[uuid.UUID, list[Asset]] = {}
    for asset in clip_assets:
        if asset.shot_card_id is not None:
            assets_by_clip.setdefault(asset.shot_card_id, []).append(asset)
    selected_anchor_by_clip = {
        clip.id: next(
            (
                asset
                for asset in assets_by_clip.get(clip.id, [])
                if asset.id == clip.selected_anchor_asset_id
                and asset.status in {"approved", "ready"}
            ),
            None,
        )
        for clip in generation_clips
    }
    editorial_thumbnail_by_beat: dict[uuid.UUID, Asset] = {}
    for asset in clip_assets:
        if asset.role != "editorial_thumbnail" or asset.status not in {"approved", "ready"}:
            continue
        raw_beat_id = (asset.metadata_json or {}).get("shotBeatId")
        try:
            beat_id = uuid.UUID(str(raw_beat_id))
        except (TypeError, ValueError):
            continue
        editorial_thumbnail_by_beat[beat_id] = asset
    clip_steps = list(
        session.scalars(
            select(WorkflowStep)
            .where(WorkflowStep.shot_card_id.in_(clip_ids))
            .order_by(WorkflowStep.created_at)
        )
    )
    steps_by_clip: dict[uuid.UUID, list[WorkflowStep]] = {}
    for step in clip_steps:
        if step.shot_card_id is not None:
            steps_by_clip.setdefault(step.shot_card_id, []).append(step)
    beat_ids_by_clip: dict[uuid.UUID, list[uuid.UUID]] = {}
    clip_id_by_beat: dict[uuid.UUID, uuid.UUID] = {}
    for mapping in clip_mappings:
        beat_ids_by_clip.setdefault(mapping.shot_card_id, []).append(mapping.shot_beat_id)
        clip_id_by_beat[mapping.shot_beat_id] = mapping.shot_card_id
    structure_approved = bool(
        storyboard_revision is not None
        and storyboard_revision.status in {"structure_approved", "production_approved"}
    )
    generation_plan_approved = bool(
        generation_plan is not None and generation_plan.status == "approved"
    )
    package_approved = bool(
        storyboard_revision is not None
        and storyboard_revision.status == "production_approved"
        and storyboard_revision.production_package_hash
    )
    project_row = session.get(ProductionRun, project_id)
    current_profile = (
        None
        if project_row is None or project_row.current_visual_profile_revision_id is None
        else session.get(
            VisualProfileRevision,
            project_row.current_visual_profile_revision_id,
        )
    )
    scene_prompt_ready: dict[uuid.UUID, bool] = {}
    scene_prompt_blockers: dict[uuid.UUID, list[str]] = {}
    if (
        storyboard_revision is not None
        and generation_plan is not None
        and generation_plan_approved
        and current_profile is not None
    ):
        for scene in scenes:
            _bindings, blockers, _warnings = _scene_prompt_bindings(
                session,
                scene,
                current_profile,
                storyboard_revision=storyboard_revision,
                generation_plan=generation_plan,
                scene_look_usage=next(
                    (
                        str(clip.scene_look_usage)
                        for clip in generation_clips
                        if clip.scene_id == scene.id and clip.scene_look_usage != "off"
                    ),
                    "off",
                ),
            )
            scene_prompt_ready[scene.id] = not blockers
            scene_prompt_blockers[scene.id] = blockers
    recipe_instance_id = None if recipe_instance is None else str(recipe_instance.id)
    shows_legacy_event_history = bool(
        recipe_instance is not None
        and recipe_instance.recipe_key == ProductionRecipeKey.HEALING_CHILD_CAT_V1.value
        and story_events
    )
    has_approved_story = any(
        story.status == StoryRevisionStatus.APPROVED.value for story in stories
    )
    has_full_text_story_candidate = any(
        story.source_event_candidate_id is None for story in stories
    )
    uses_event_story_flow = bool(
        shows_legacy_event_history
        and not has_approved_story
        and not has_full_text_story_candidate
    )
    latest_event_batch_id = story_events[-1].batch_id if story_events else None
    current_story_events = [
        event for event in story_events if event.batch_id == latest_event_batch_id
    ]
    selected_story_event = next(
        (
            event
            for event in current_story_events
            if event.status == StoryEventCandidateStatus.SELECTED.value
        ),
        None,
    )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    subject_revisions = {
        subject.id: (
            None
            if subject.current_revision_id is None
            else session.get(SubjectRevision, subject.current_revision_id)
        )
        for subject in subjects
    }
    planner_canon_dependencies = [
        {
            "subjectId": str(subject.id),
            "name": revision.name,
            "role": subject.role,
            "status": subject.status,
        }
        for subject in subjects
        if (revision := subject_revisions.get(subject.id)) is not None
    ]
    if brief is not None:
        nodes.append(
            {
                "id": str(brief_node_id),
                "type": "BriefNode",
                "objectType": "story_brief",
                "objectId": str(brief.id),
                "data": _brief_json(brief),
            }
        )
    if include_narrative_projection:
        nodes.extend(
            (
                {
                    "id": str(planner_id),
                    "type": "StoryPlannerNode",
                    "objectType": (
                        "story_event_planner" if uses_event_story_flow else "story_planner"
                    ),
                    "objectId": str(project_id),
                    "data": {
                        "title": ("历史事件方案" if uses_event_story_flow else "生成完整故事候选"),
                        "phase": "story",
                        "recipeInstanceId": recipe_instance_id,
                        "briefSummary": None if brief is None else brief.theme,
                        "canonDependencies": planner_canon_dependencies,
                        "candidateCount": 3,
                        "storyWorkflowStep": (1 if not stories else 2)
                        if not uses_event_story_flow
                        else 1
                        if not current_story_events
                        else 2,
                        "storyWorkflowTotalSteps": 4 if uses_event_story_flow else 2,
                        "candidateRules": [
                            "一次返回 1–5 个可编辑的完整长文本候选",
                            "候选在事件、人物行动和情绪落点上保持差异",
                            "固定一人一猫身份关系，不复制独特角色或画风",
                        ],
                    },
                },
                *(
                    (
                        {
                            "id": str(event_selection_id),
                            "type": "ApprovalGateNode",
                            "objectType": "story_event_selection",
                            "objectId": str(project_id),
                            "data": {
                                "title": "选择一个事件方案",
                                "phase": "story",
                                "recipeInstanceId": recipe_instance_id,
                                "candidateCount": len(current_story_events),
                                "selectedEventId": (
                                    None
                                    if selected_story_event is None
                                    else str(selected_story_event.id)
                                ),
                                "storyWorkflowStep": 2,
                                "storyWorkflowTotalSteps": 4,
                            },
                        },
                        {
                            "id": str(script_expander_id),
                            "type": "StoryScriptNode",
                            "objectType": "story_script_expander",
                            "objectId": str(project_id),
                            "data": {
                                "title": "扩写完整剧情脚本",
                                "phase": "story",
                                "recipeInstanceId": recipe_instance_id,
                                "selectedEventId": (
                                    None
                                    if selected_story_event is None
                                    else str(selected_story_event.id)
                                ),
                                "storyWorkflowStep": 3,
                                "storyWorkflowTotalSteps": 4,
                            },
                        },
                    )
                    if uses_event_story_flow
                    else ()
                ),
                {
                    "id": str(approval_id),
                    "type": "ApprovalGateNode",
                    "objectType": "story_approval",
                    "objectId": str(project_id),
                    "data": {
                        "title": "选择为当前剧情",
                        "phase": "story",
                        "recipeInstanceId": recipe_instance_id,
                        "storyWorkflowStep": 4 if uses_event_story_flow else 2,
                        "storyWorkflowTotalSteps": 4 if uses_event_story_flow else 2,
                    },
                },
                {
                    "id": str(storyboard_id),
                    "type": "StoryboardDirectorNode",
                    "objectType": "storyboard_director",
                    "objectId": str(project_id),
                    "data": {
                        "title": "分镜编译",
                        "phase": "storyboard",
                        "recipeInstanceId": recipe_instance_id,
                        "shotCount": len(beats),
                        "generationClipCount": len(generation_clips),
                        "storyboardRevisionId": (
                            None if storyboard_revision is None else str(storyboard_revision.id)
                        ),
                        "storyboardRevision": (
                            0 if storyboard_revision is None else storyboard_revision.revision
                        ),
                        "structureHash": (
                            None
                            if storyboard_revision is None
                            else storyboard_revision.structure_hash
                        ),
                        "storyboardStructureApproved": structure_approved,
                        "generationPlanApproved": generation_plan_approved,
                        "storyboardPackageApproved": package_approved,
                        "storyboardApproved": package_approved,
                        **_storyboard_prompt_projection(storyboard_prompt),
                    },
                },
            )
        )
    if brief is not None:
        edges.append(
            _edge(
                brief_node_id,
                "BriefNode",
                "brief",
                planner_id,
                "StoryPlannerNode",
                "brief",
            )
        )
    for subject in subjects:
        revision = subject_revisions.get(subject.id)
        if revision is None:
            continue
        references = tuple(
            session.scalars(
                select(SubjectReference).where(SubjectReference.subject_revision_id == revision.id)
            )
        )
        nodes.append(
            {
                "id": str(subject.id),
                "type": "SubjectNode",
                "objectType": "subject",
                "objectId": str(subject.id),
                "data": _subject_json(session, subject, revision, references),
            }
        )
        if include_narrative_projection:
            edges.append(
                _edge(
                    subject.id,
                    "SubjectNode",
                    "subject[]",
                    planner_id,
                    "StoryPlannerNode",
                    "subject[]",
                )
            )
    if shows_legacy_event_history:
        for event in story_events:
            event_data = {
                **_story_event_json(event),
                "phase": "story",
                "isCurrentBatch": event.batch_id == latest_event_batch_id,
                "isHistoryBranch": event.batch_id != latest_event_batch_id
                or event.status == StoryEventCandidateStatus.SUPERSEDED.value,
            }
            nodes.append(
                {
                    "id": str(event.id),
                    "type": "StoryEventNode",
                    "objectType": "story_event",
                    "objectId": str(event.id),
                    "status": event.status,
                    "data": event_data,
                }
            )
            edges.append(
                _edge(
                    planner_id,
                    "StoryPlannerNode",
                    "story_event",
                    event.id,
                    "StoryEventNode",
                    "story_event",
                )
            )
            if uses_event_story_flow and event.batch_id == latest_event_batch_id:
                edges.append(
                    _edge(
                        event.id,
                        "StoryEventNode",
                        "story_event",
                        event_selection_id,
                        "ApprovalGateNode",
                        "story_event",
                    )
                )
        if uses_event_story_flow and selected_story_event is not None:
            edges.append(
                _edge(
                    event_selection_id,
                    "ApprovalGateNode",
                    "story_event",
                    script_expander_id,
                    "StoryScriptNode",
                    "story_event",
                )
            )
    story_ids = [story.id for story in stories]
    scores_by_story_id = {
        score.story_revision_id: score
        for score in (
            session.scalars(select(StoryScore).where(StoryScore.story_revision_id.in_(story_ids)))
            if story_ids
            else ()
        )
    }
    candidate_prompt_ids = [
        story.candidate_prompt_id for story in stories if story.candidate_prompt_id is not None
    ]
    candidate_prompts_by_id = {
        prompt.id: prompt
        for prompt in (
            session.scalars(select(PromptRecord).where(PromptRecord.id.in_(candidate_prompt_ids)))
            if candidate_prompt_ids
            else ()
        )
    }
    for story in stories:
        score = scores_by_story_id.get(story.id)
        candidate_prompt = candidate_prompts_by_id.get(story.candidate_prompt_id)
        is_event_script = story.source_event_candidate_id is not None
        story_node_type = "StoryScriptNode" if is_event_script else "StoryCandidateNode"
        story_document = _story_json(story, score, candidate_prompt)
        story_document.pop("scenes", None)
        story_document.pop("scorecard", None)
        story_data = {
            **story_document,
            "phase": "story",
            "recipeInstanceId": recipe_instance_id,
            "legacyCompatibility": uses_event_story_flow and not is_event_script,
        }
        if uses_event_story_flow and not is_event_script:
            story_data["artifactLabel"] = "旧版剧情直出"
        nodes.append(
            {
                "id": str(story.id),
                "type": story_node_type,
                "objectType": "story_revision",
                "objectId": str(story.id),
                "status": story.status,
                "data": story_data,
            }
        )
        if is_event_script:
            if story.source_event_candidate_id is not None:
                edges.append(
                    _edge(
                        story.source_event_candidate_id,
                        "StoryEventNode",
                        "story_event",
                        story.id,
                        story_node_type,
                        "story_event",
                    )
                )
            if (
                uses_event_story_flow
                and selected_story_event is not None
                and story.source_event_candidate_id == selected_story_event.id
            ):
                edges.extend(
                    (
                        _edge(
                            script_expander_id,
                            "StoryScriptNode",
                            "story_revision",
                            story.id,
                            story_node_type,
                            "story_revision",
                        ),
                        _edge(
                            story.id,
                            story_node_type,
                            "story_revision",
                            approval_id,
                            "ApprovalGateNode",
                            "story_revision",
                        ),
                    )
                )
        else:
            edges.extend(
                (
                    _edge(
                        planner_id,
                        "StoryPlannerNode",
                        "story_revision",
                        story.id,
                        story_node_type,
                        "story_revision",
                    ),
                    _edge(
                        story.id,
                        story_node_type,
                        "story_revision",
                        approval_id,
                        "ApprovalGateNode",
                        "story_revision",
                    ),
                )
            )
        if story.status == StoryRevisionStatus.APPROVED.value:
            edges.append(
                _edge(
                    story.id,
                    story_node_type,
                    "story_revision",
                    storyboard_id,
                    "StoryboardDirectorNode",
                    "story_revision",
                )
            )
    scene_by_id = {scene.id: scene for scene in scenes}
    for scene in scenes:
        scene_beats = [beat for beat in beats if beat.scene_id == scene.id]
        scene_data: dict[str, Any] = {
            "title": scene.title,
            "order": scene.sort_order,
            "storyRevisionId": (
                None if scene.story_revision_id is None else str(scene.story_revision_id)
            ),
            "sceneKey": scene.scene_key,
            "active": scene.active,
            "staleReason": scene.stale_reason,
            "storyboardShotCount": len(scene_beats),
            "totalDurationSeconds": sum(beat.duration_seconds for beat in scene_beats),
            "collapsedStoryboardShots": [
                {
                    "id": str(beat.id),
                    "order": beat.sort_order,
                    "title": beat.title,
                    "durationSeconds": beat.duration_seconds,
                }
                for beat in scene_beats
            ],
        }
        if scene.context_note:
            try:
                context_document = json.loads(scene.context_note)
            except (TypeError, json.JSONDecodeError):
                context_document = None
            if isinstance(context_document, dict):
                scene_data.update(
                    {
                        "sceneKey": scene.scene_key or context_document.get("sceneKey"),
                        "purpose": context_document.get("purpose"),
                        "continuity": context_document.get("continuity"),
                    }
                )
        nodes.append(
            {
                "id": str(scene.id),
                "type": "SceneNode",
                "objectType": "scene",
                "objectId": str(scene.id),
                "data": scene_data,
            }
        )
        if approved_story is not None:
            approved_story_type = (
                "StoryScriptNode"
                if approved_story.source_event_candidate_id is not None
                else "StoryCandidateNode"
            )
            edges.append(
                _edge(
                    approved_story.id,
                    approved_story_type,
                    "scene_plan",
                    scene.id,
                    "SceneNode",
                    "scene_plan",
                )
            )
    for beat in beats:
        generation_clip_id = clip_id_by_beat.get(beat.id)
        first_clip_beat = bool(
            generation_clip_id is not None
            and beat_ids_by_clip.get(generation_clip_id, [None])[0] == beat.id
        )
        nodes.append(
            {
                "id": str(beat.id),
                "type": "ShotBeatNode",
                "objectType": "shot_beat",
                "objectId": str(beat.id),
                "data": _beat_json(
                    beat,
                    None if beat.prompt_id is None else session.get(PromptRecord, beat.prompt_id),
                    generation_clip_id=generation_clip_id,
                    thumbnail_asset=(
                        selected_anchor_by_clip.get(generation_clip_id)
                        if first_clip_beat and generation_clip_id is not None
                        else editorial_thumbnail_by_beat.get(beat.id)
                    ),
                ),
            }
        )
        if beat.scene_id in scene_by_id:
            edges.append(
                _edge(
                    beat.scene_id,
                    "SceneNode",
                    "storyboard_shot[]",
                    beat.id,
                    "ShotBeatNode",
                    "storyboard_shot[]",
                )
            )
    if storyboard_revision is not None:
        structure_gate_id = uuid.uuid5(storyboard_revision.id, "storyboard-structure-approval")
        package_gate_id = uuid.uuid5(storyboard_revision.id, "storyboard-package-approval")
        nodes.append(
            {
                "id": str(structure_gate_id),
                "type": "ApprovalGateNode",
                "objectType": "storyboard_structure",
                "objectId": str(storyboard_revision.id),
                "status": storyboard_revision.status,
                "data": {
                    "title": "批准分镜结构并建立生产版本",
                    "phase": "storyboard",
                    "recipeInstanceId": recipe_instance_id,
                    "storyboardRevisionId": str(storyboard_revision.id),
                    "revision": storyboard_revision.revision,
                    "shotCount": len(beats),
                    "totalDurationSeconds": sum(beat.duration_seconds for beat in beats),
                    "structureHash": storyboard_revision.structure_hash,
                    "approved": structure_approved,
                },
            }
        )
        for beat in beats:
            edges.append(
                _edge(
                    beat.id,
                    "ShotBeatNode",
                    "storyboard_shot[]",
                    structure_gate_id,
                    "ApprovalGateNode",
                    "shot_sequence",
                )
            )
        if generation_plan is not None:
            nodes.append(
                {
                    "id": str(generation_plan.id),
                    "type": "GenerationPlanNode",
                    "objectType": "generation_plan",
                    "objectId": str(generation_plan.id),
                    "status": generation_plan.status,
                    "data": {
                        "title": "Agent 生成编排",
                        "phase": "storyboard",
                        "recipeInstanceId": recipe_instance_id,
                        "storyboardRevisionId": str(storyboard_revision.id),
                        "revision": generation_plan.revision,
                        "status": generation_plan.status,
                        "provider": generation_plan.provider,
                        "model": generation_plan.model,
                        "capabilityRevision": generation_plan.capability_revision,
                        "inputHash": generation_plan.input_hash,
                        "editorialShotCount": len(beats),
                        "generationClipCount": len(generation_clips),
                        "estimatedImageCallCount": generation_plan.estimated_image_call_count,
                        "estimatedVideoCallCount": generation_plan.estimated_video_call_count,
                        "estimatedCostMicros": generation_plan.estimated_cost_micros,
                        "warnings": generation_plan.warnings_json,
                        "blockers": generation_plan.blockers_json,
                        "structureApproved": structure_approved,
                        "approved": generation_plan_approved,
                        "clips": [
                            {
                                "id": str(clip.id),
                                "title": clip.title,
                                "durationSeconds": clip.duration_seconds,
                                "editorialShotIds": [
                                    str(item) for item in beat_ids_by_clip.get(clip.id, [])
                                ],
                                "mode": (
                                    "multi_shot"
                                    if len(beat_ids_by_clip.get(clip.id, [])) > 1
                                    else "single_shot"
                                ),
                            }
                            for clip in generation_clips
                        ],
                    },
                }
            )
            edges.append(
                _edge(
                    structure_gate_id,
                    "ApprovalGateNode",
                    "shot_sequence",
                    generation_plan.id,
                    "GenerationPlanNode",
                    "shot_sequence",
                )
            )
            for clip_id in clip_ids:
                clip = clips_by_id.get(clip_id)
                if clip is None:
                    continue
                anchor_required = clip.anchor_mode != "text_only"
                prompt_node_id = uuid.uuid5(clip.id, "compiled-prompt")
                anchor_node_id = uuid.uuid5(clip.id, "anchor-generation")
                review_node_id = uuid.uuid5(clip.id, "anchor-review")
                video_node_id = uuid.uuid5(clip.id, "video-generation")
                anchor_assets = [
                    asset
                    for asset in assets_by_clip.get(clip.id, [])
                    if asset.role == "shot_anchor"
                ]
                video_assets = [
                    asset
                    for asset in assets_by_clip.get(clip.id, [])
                    if asset.role in {"shot_video", "shot_video_edit"}
                ]
                provider_steps = [
                    step for step in steps_by_clip.get(clip.id, []) if step.provider_task_id
                ]
                anchor_provider_task_id = next(
                    (
                        step.provider_task_id
                        for step in reversed(provider_steps)
                        if "anchor" in step.operation_key
                    ),
                    None,
                )
                video_provider_task_id = next(
                    (
                        step.provider_task_id
                        for step in reversed(provider_steps)
                        if "video" in step.operation_key
                    ),
                    None,
                )
                nodes.extend(
                    (
                        {
                            "id": str(clip.id),
                            "type": "VideoSegmentNode",
                            "objectType": "generation_clip",
                            "objectId": str(clip.id),
                            "status": clip.status,
                            "data": {
                                "title": clip.title,
                                "phase": "storyboard",
                                "recipeInstanceId": recipe_instance_id,
                                "shotId": str(clip.id),
                                "generationPlanId": str(generation_plan.id),
                                "order": clip.plan_sort_order or clip.sort_order,
                                "durationSeconds": clip.duration_seconds,
                                "mode": (
                                    "multi_shot"
                                    if len(beat_ids_by_clip.get(clip.id, [])) > 1
                                    else "single_shot"
                                ),
                                "editorialShotIds": [
                                    str(item) for item in beat_ids_by_clip.get(clip.id, [])
                                ],
                                "thumbnailUrl": (
                                    None
                                    if selected_anchor_by_clip.get(clip.id) is None
                                    else (
                                        "/api/v1/assets/"
                                        f"{selected_anchor_by_clip[clip.id].id}/content"
                                    )
                                ),
                                "thumbnailSource": (
                                    None
                                    if selected_anchor_by_clip.get(clip.id) is None
                                    else "approved_anchor"
                                ),
                            },
                        },
                        {
                            "id": str(prompt_node_id),
                            "type": "PromptArtifactNode",
                            "objectType": "generation_clip_prompt",
                            "objectId": str(clip.id),
                            "status": (
                                "compiled"
                                if clip.prompt_id
                                else "ready"
                                if scene_prompt_ready.get(clip.scene_id, False)
                                else "blocked"
                            ),
                            "data": {
                                "title": f"{clip.title} · 生产 Prompt",
                                "phase": "storyboard",
                                "recipeInstanceId": recipe_instance_id,
                                "shotId": str(clip.id),
                                "sceneId": str(clip.scene_id),
                                "generationPlanApproved": generation_plan_approved,
                                "sceneAssetsReady": scene_prompt_ready.get(clip.scene_id, False),
                                "sceneAssetBlockers": scene_prompt_blockers.get(
                                    clip.scene_id,
                                    [],
                                ),
                                "promptId": (
                                    None if clip.prompt_id is None else str(clip.prompt_id)
                                ),
                                "compiled": clip.prompt_id is not None,
                            },
                        },
                        {
                            "id": str(anchor_node_id),
                            "type": "ImageGenerationNode",
                            "objectType": "visual_anchor_generation",
                            "objectId": str(clip.id),
                            "status": (
                                "approved"
                                if clip.selected_anchor_asset_id is not None
                                else "ready"
                                if package_approved
                                else "blocked"
                            ),
                            "data": {
                                "title": f"{clip.title} · 可选开场视觉锚点",
                                "phase": "render",
                                "recipeInstanceId": recipe_instance_id,
                                "shotId": str(clip.id),
                                "packageApproved": package_approved,
                                "optional": not anchor_required,
                                "selectedAssetId": (
                                    None
                                    if clip.selected_anchor_asset_id is None
                                    else str(clip.selected_anchor_asset_id)
                                ),
                                "candidateCount": len(anchor_assets),
                                "providerTaskId": anchor_provider_task_id,
                            },
                        },
                        {
                            "id": str(review_node_id),
                            "type": "ReviewNode",
                            "objectType": "visual_anchor_review",
                            "objectId": str(clip.id),
                            "status": (
                                "approved"
                                if clip.selected_anchor_asset_id is not None
                                else "awaiting_asset"
                            ),
                            "data": {
                                "title": f"{clip.title} · 锚点审核",
                                "phase": "render",
                                "recipeInstanceId": recipe_instance_id,
                                "shotId": str(clip.id),
                                "approved": clip.selected_anchor_asset_id is not None,
                                "candidateCount": len(anchor_assets),
                            },
                        },
                        {
                            "id": str(video_node_id),
                            "type": "VideoGenerationNode",
                            "objectType": "video_generation_clip",
                            "objectId": str(clip.id),
                            "status": (
                                "approved"
                                if clip.selected_video_asset_id is not None
                                else "ready"
                                if package_approved
                                and (
                                    not anchor_required
                                    or clip.selected_anchor_asset_id is not None
                                )
                                else "blocked"
                            ),
                            "data": {
                                "title": f"{clip.title} · 生成视频",
                                "phase": "render",
                                "recipeInstanceId": recipe_instance_id,
                                "shotId": str(clip.id),
                                "packageApproved": package_approved,
                                "anchorRequired": anchor_required,
                                "anchorApproved": clip.selected_anchor_asset_id is not None,
                                "selectedAssetId": (
                                    None
                                    if clip.selected_video_asset_id is None
                                    else str(clip.selected_video_asset_id)
                                ),
                                "providerInputMode": (
                                    "first_frame"
                                    if clip.selected_anchor_asset_id is not None
                                    else "reference_media"
                                ),
                                "videoCandidateCount": len(video_assets),
                                "providerTaskId": video_provider_task_id,
                            },
                        },
                    )
                )
                edges.extend(
                    (
                        _edge(
                            generation_plan.id,
                            "GenerationPlanNode",
                            "generation_plan",
                            clip.id,
                            "VideoSegmentNode",
                            "generation_plan",
                        ),
                        _edge(
                            clip.id,
                            "VideoSegmentNode",
                            "video_segment[]",
                            prompt_node_id,
                            "PromptArtifactNode",
                            "video_segment[]",
                        ),
                        _edge(
                            prompt_node_id,
                            "PromptArtifactNode",
                            "compiled_prompt",
                            package_gate_id,
                            "ApprovalGateNode",
                            "compiled_prompt",
                        ),
                        _edge(
                            package_gate_id,
                            "ApprovalGateNode",
                            "compiled_prompt",
                            anchor_node_id,
                            "ImageGenerationNode",
                            "compiled_prompt",
                        ),
                        _edge(
                            anchor_node_id,
                            "ImageGenerationNode",
                            "approved_anchor",
                            review_node_id,
                            "ReviewNode",
                            "approved_anchor",
                        ),
                        _edge(
                            review_node_id,
                            "ReviewNode",
                            "approved_anchor",
                            video_node_id,
                            "VideoGenerationNode",
                            "approved_anchor",
                        ),
                    )
                )
                for beat_id in beat_ids_by_clip.get(clip.id, []):
                    edges.append(
                        _edge(
                            beat_id,
                            "ShotBeatNode",
                            "storyboard_shot[]",
                            clip.id,
                            "VideoSegmentNode",
                            "video_segment[]",
                            relation_type="generation_clip_shot",
                        )
                    )
            nodes.append(
                {
                    "id": str(package_gate_id),
                    "type": "ApprovalGateNode",
                    "objectType": "storyboard_package",
                    "objectId": str(storyboard_revision.id),
                    "status": storyboard_revision.status,
                    "data": {
                        "title": "批准生产分镜包",
                        "phase": "storyboard",
                        "recipeInstanceId": recipe_instance_id,
                        "storyboardRevisionId": str(storyboard_revision.id),
                        "revision": storyboard_revision.revision,
                        "productionPackageHash": storyboard_revision.production_package_hash,
                        "compiledPromptCount": sum(
                            clip.prompt_id is not None for clip in generation_clips
                        ),
                        "requiredPromptCount": len(generation_clips),
                        "approved": package_approved,
                    },
                }
            )
    positions = {str(item.get("nodeId")): item for item in (layout.nodes_json if layout else [])}
    if brief is not None and str(brief_node_id) not in positions:
        brief_version_ids = list(
            session.scalars(
                select(StoryBriefRecord.id)
                .where(StoryBriefRecord.production_run_id == project_id)
                .order_by(StoryBriefRecord.revision.desc())
            )
        )
        inherited_position = next(
            (
                positions[str(version_id)]
                for version_id in brief_version_ids
                if str(version_id) in positions
            ),
            None,
        )
        if inherited_position is not None:
            positions[str(brief_node_id)] = inherited_position
    stage_by_type = {
        "BriefNode": 0,
        "SubjectNode": 0,
        "StoryPlannerNode": 1,
        "StoryEventNode": 2,
        "StoryScriptNode": 3,
        "StoryCandidateNode": 3,
        "ApprovalGateNode": 4,
        "StoryboardDirectorNode": 5,
        "SceneNode": 6,
        "ShotBeatNode": 7,
        "GenerationPlanNode": 9,
        "VideoSegmentNode": 10,
        "PromptArtifactNode": 11,
        "ImageGenerationNode": 13,
        "ReviewNode": 14,
        "VideoGenerationNode": 15,
    }
    row_by_stage: dict[int, int] = {}
    for node in nodes:
        stored = positions.get(node["id"])
        stage = (
            8
            if node["type"] == "ApprovalGateNode"
            and node.get("objectType") == "storyboard_structure"
            else 12
            if node["type"] == "ApprovalGateNode" and node.get("objectType") == "storyboard_package"
            else stage_by_type.get(node["type"], 7)
        )
        row = row_by_stage.get(stage, 0)
        row_by_stage[stage] = row + 1
        node["position"] = (
            {"x": stored.get("x", 80 + stage * 320), "y": stored.get("y", 80 + row * 220)}
            if stored is not None
            else {"x": 80 + stage * 320, "y": 80 + row * 220}
        )
        node["data"].setdefault("objectType", node.get("objectType") or node["type"])
        node["data"].setdefault("businessObjectId", node.get("objectId"))
        if recipe_instance_id is not None:
            node["data"].setdefault("recipeInstanceId", recipe_instance_id)
        node.update(
            _canvas_node_contract(
                str(node["type"]),
                str(node.get("status") or node["data"].get("status") or ""),
                node["data"],
            )
        )
        if node["type"] == "StoryPlannerNode" and (brief is None or len(subjects) < 2):
            reason = "请先完成创意简报并准备至少两个叙事主体"
            node["blocker"] = reason
            node["availableActions"][0].update(
                enabled=False,
                execution="unavailable",
                disabledReason=reason,
            )
        if node["type"] == "StoryboardDirectorNode":
            approved = any(story.status == StoryRevisionStatus.APPROVED.value for story in stories)
            if not approved:
                reason = "请先人工批准一个故事版本"
                node["blocker"] = reason
                node["availableActions"][0].update(
                    enabled=False,
                    execution="unavailable",
                    disabledReason=reason,
                )
    return {
        "projectId": str(project_id),
        "canvasV2Enabled": enabled,
        "layoutVersion": 0 if layout is None else layout.version,
        "nodes": nodes,
        "edges": edges,
        "viewport": ({"x": 0, "y": 0, "zoom": 1} if layout is None else layout.viewport_json),
        "syncStatus": "saved" if layout is None else layout.sync_status,
    }


def _apply_canvas_workflow_step_projection(
    canvas: dict[str, Any],
    workflow_steps: list[WorkflowStep],
) -> None:
    """Project durable workflow state onto the exact canvas node that owns it."""

    for node in canvas.get("nodes", []):
        scope = node.get("executionScope") or {}
        node_id = str(node.get("id") or "")
        business_object_id = str(scope.get("businessObjectId") or "")
        scene_id = str(scope.get("sceneId") or "")
        shot_id = str(scope.get("shotId") or "")
        recipe_instance_id = str(scope.get("recipeInstanceId") or "")
        canvas_group_id = str(scope.get("canvasGroupId") or "")
        operation_keys = set(scope.get("operationKeys") or [])
        phases = set(scope.get("phases") or [])

        matched: list[WorkflowStep] = []
        for step in workflow_steps:
            snapshot = dict(step.input_snapshot_json or {})
            task_node_id = str(snapshot.get("canvasNodeId") or "")
            task_business_object_id = str(snapshot.get("businessObjectId") or "")
            task_scene_id = str(step.scene_id or snapshot.get("sceneId") or "")
            task_shot_id = str(step.shot_card_id or snapshot.get("shotId") or "")

            if task_node_id and node_id:
                step_matches = task_node_id == node_id
            elif task_business_object_id and business_object_id:
                step_matches = task_business_object_id == business_object_id
            elif task_scene_id and scene_id:
                step_matches = task_scene_id == scene_id
            elif task_shot_id and shot_id:
                step_matches = task_shot_id == shot_id
            else:
                task_phase = snapshot.get("phase") or snapshot.get("workflowStage")
                operation_matches = step.operation_key in operation_keys
                phase_matches = not phases or task_phase in phases
                recipe_matches = bool(
                    recipe_instance_id and snapshot.get("recipeInstanceId") == recipe_instance_id
                )
                group_matches = bool(
                    canvas_group_id and snapshot.get("canvasGroupId") == canvas_group_id
                )
                step_matches = bool(
                    operation_matches and phase_matches and (recipe_matches or group_matches)
                )

            if step_matches:
                matched.append(step)

        if not matched:
            continue

        projected_steps: list[dict[str, Any]] = []
        for step in matched[:20]:
            progress = dict(step.progress_json or {})
            message = str(progress.get("message") or step.operation_key)
            percent = progress.get("percent")
            detail = message if percent is None else f"{message} · {percent}%"
            projected_steps.append(
                {
                    "key": str(step.id),
                    "label": _canvas_workflow_operation_label(step.operation_key),
                    "status": step.status,
                    "detail": detail,
                }
            )
        node["workflowSteps"] = projected_steps

        latest = matched[0]
        latest_progress = dict(latest.progress_json or {})
        result_summary = latest_progress.get("resultSummary")
        if isinstance(result_summary, dict):
            node["outputs"] = [result_summary]


def _canvas_workflow_operation_label(operation_key: str) -> str:
    return {
        "recipe:story_events": "生成完整故事候选（兼容入口）",
        "recipe:story_script": "扩写剧情脚本",
        "recipe:creative_brief": "补全创意简报",
        "recipe:character_design": "生成本集角色造型",
        "recipe:character_design_validation": "验证三槽位引用顺序",
        "recipe:storyboard": "生成文本分镜",
        "recipe:anchor": "生成视觉锚点",
        "recipe:video": "生成逐镜视频",
        "recipe:sequence": "合成最终音画",
    }.get(operation_key, operation_key)


def _edge(
    source_id: uuid.UUID,
    source_type: str,
    source_port: str,
    target_id: uuid.UUID,
    target_type: str,
    target_port: str,
    *,
    relation_type: str = "derived_flow",
) -> dict[str, Any]:
    presentation_kind, default_visible, authority = _edge_presentation(
        source_type=source_type,
        target_type=target_type,
        source_port=source_port,
        target_port=target_port,
        relation_type=relation_type,
        user_managed=False,
    )
    return {
        "id": str(
            uuid.uuid5(
                source_id,
                f"{source_port}:{target_id}:{target_port}"
                if relation_type == "derived_flow"
                else f"{source_port}:{target_id}:{target_port}:{relation_type}",
            )
        ),
        "sourceNodeId": str(source_id),
        "sourceNodeType": source_type,
        "sourcePort": source_port,
        "targetNodeId": str(target_id),
        "targetNodeType": target_type,
        "targetPort": target_port,
        "relationType": relation_type,
        "revision": 1,
        "systemManaged": True,
        "presentationKind": presentation_kind,
        "defaultVisible": default_visible,
        "authority": authority,
        "availableActions": [
            {
                "key": "disconnect_edge",
                "label": "剪断连接",
                "enabled": False,
                "disabledReason": "该连线由故事、审核或六阶段流程派生，不能直接剪断",
            }
        ],
    }
