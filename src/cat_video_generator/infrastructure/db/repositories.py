"""PostgreSQL repository for V5 projects, scenes, video clips and media versions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from ...application.ports import (
    LandedAsset,
    ProjectReadModel,
    ShotGenerationReadModel,
    StoredAsset,
    StoredProject,
    StoredPrompt,
    StoredReview,
    StoredScene,
    StoredSequence,
    StoredShot,
    StoredStep,
    StoredVisualProfileRevision,
)
from ...application.read_models import project_graph_projection
from ...domain.contracts import (
    CURRENT_CONTRACT_VERSION,
    AcceptedVisualAssetPlan,
    AnchorMode,
    LookReferenceBinding,
    LookReferencePurpose,
    ReferenceBinding,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
    SceneDraft,
    SceneLookDraft,
    SceneLookPlan,
    ShotAssistPatch,
    ShotCardDraft,
    StoryProjectInput,
    VisualProfileDraft,
)
from ...domain.creative_workflow import shot_snapshot_hash, story_source_hash
from ...domain.rendering import ProjectSequencePlan, SequenceStatus
from ...domain.shot_assistance import apply_shot_assist_patch
from ...domain.workflow import (
    PromptPurpose,
    RunStatus,
    SceneStatus,
    ShotStatus,
    StepKind,
    StepStatus,
    transition_step,
    validate_prompt_purpose,
)
from ..ark.runtime import current_execution_snapshot
from .models import (
    Asset,
    CanvasEvent,
    CharacterDesignAsset,
    CharacterDesignRevision,
    GenerationClipShot,
    GenerationPlan,
    ProductionRecipeInstance,
    ProductionRun,
    PromptRecord,
    Review,
    Scene,
    ShotBeat,
    ShotCard,
    StoryboardRevision,
    VideoSequence,
    VisualProfileRevision,
    WorkflowStep,
)


class RecordNotFoundError(LookupError):
    pass


class ContractVersionMismatchError(RuntimeError):
    pass


class WorkflowConflictError(ValueError):
    pass


class SqlAlchemyWorkflowRepository:
    """Owns short transactions and concurrency-safe paid intent creation."""

    def __init__(self, sessions: sessionmaker[Session], *, asset_root: Path) -> None:
        self._sessions = sessions
        self._asset_root = asset_root.expanduser().resolve()

    def create_project(self, source: StoryProjectInput, *, content_date: date) -> StoredProject:
        with self._sessions.begin() as session:
            row = ProductionRun(
                title=source.title,
                content_date=content_date,
                contract_version=CURRENT_CONTRACT_VERSION,
                status=RunStatus.ACTIVE.value,
                default_reference_bindings_json=[],
            )
            session.add(row)
            session.flush()
            profile = _default_visual_profile(session)
            revision = self._create_visual_profile_revision(
                session,
                project=row,
                draft=profile,
            )
            row.current_visual_profile_revision_id = revision.id
            row.default_reference_bindings_json = _project_reference_bindings(profile)
            scene = Scene(
                production_run_id=row.id,
                sort_order=1,
                title=source.first_scene_title,
                source_text=source.first_scene_text,
                story_mode="single",
                target_shot_count=1,
                status=SceneStatus.DRAFT.value,
            )
            session.add(scene)
            session.flush()
            return _project(row)

    def list_projects(self) -> tuple[StoredProject, ...]:
        with self._sessions() as session:
            rows = session.execute(
                select(ProductionRun).order_by(ProductionRun.updated_at.desc())
            ).scalars()
            return tuple(_project_checked(row) for row in rows)

    def update_project(
        self,
        project_id: uuid.UUID,
        *,
        title: str,
        content_date: date,
    ) -> StoredProject:
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("project title cannot be empty")
        if len(normalized_title) > 160:
            raise ValueError("project title cannot exceed 160 characters")
        with self._sessions.begin() as session:
            row = self._require_project(session, project_id)
            row.title = normalized_title
            row.content_date = content_date
            return _project(row)

    def update_project_default_references(
        self,
        project_id: uuid.UUID,
        bindings: list[ReferenceBinding],
    ) -> StoredProject:
        if any(binding.usage is not ReferenceUsage.GENERATION_REFERENCE for binding in bindings):
            raise ValueError("project defaults must be generation_reference bindings")
        with self._sessions.begin() as session:
            row = self._require_project(session, project_id)
            self._validate_reference_bindings(
                session,
                project_id=project_id,
                bindings=bindings,
            )
            serialized = [
                item.model_dump(mode="json", by_alias=True) for item in bindings
            ]
            if row.default_reference_bindings_json != serialized:
                row.default_reference_bindings_json = serialized
                scenes = list(
                    session.scalars(
                        select(Scene).where(
                            Scene.production_run_id == project_id,
                            Scene.active.is_(True),
                        )
                    )
                )
                for scene in scenes:
                    self._invalidate_scene_outputs(session, scene)
            return _project(row)

    def get_visual_profile(self, project_id: uuid.UUID) -> StoredVisualProfileRevision:
        with self._sessions() as session:
            project = self._require_project(session, project_id)
            if project.current_visual_profile_revision_id is None:
                raise RecordNotFoundError(f"project {project_id} has no visual profile")
            row = _required(
                session,
                VisualProfileRevision,
                project.current_visual_profile_revision_id,
            )
            return _visual_profile(row)

    def get_default_visual_profile(self, project_id: uuid.UUID) -> VisualProfileDraft:
        with self._sessions() as session:
            self._require_project(session, project_id)
            return _default_visual_profile(session)

    def get_visual_profile_revision(
        self,
        revision_id: uuid.UUID,
    ) -> StoredVisualProfileRevision:
        with self._sessions() as session:
            return _visual_profile(_required(session, VisualProfileRevision, revision_id))

    def save_visual_profile(
        self,
        project_id: uuid.UUID,
        draft: VisualProfileDraft,
    ) -> StoredVisualProfileRevision:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id)
            previous_profile_id = project.current_visual_profile_revision_id
            normalized_bindings = self._normalize_look_reference_bindings(
                session,
                project_id=project_id,
                bindings=draft.reference_bindings,
                profile_only=True,
            )
            draft = draft.model_copy(update={"reference_bindings": normalized_bindings})
            reference_snapshot = _reference_snapshot(session, normalized_bindings)
            profile_hash = _profile_hash(draft, reference_snapshot=reference_snapshot)
            existing = session.scalar(
                select(VisualProfileRevision).where(
                    VisualProfileRevision.production_run_id == project_id,
                    VisualProfileRevision.profile_hash == profile_hash,
                )
            )
            row = (
                existing
                if existing is not None
                else self._create_visual_profile_revision(
                    session,
                    project=project,
                    draft=draft,
                    reference_snapshot=reference_snapshot,
                )
            )
            project.current_visual_profile_revision_id = row.id
            project.default_reference_bindings_json = _project_reference_bindings(draft)
            if previous_profile_id != row.id:
                scenes = list(
                    session.scalars(
                        select(Scene).where(
                            Scene.production_run_id == project_id,
                            Scene.active.is_(True),
                        )
                    )
                )
                for scene in scenes:
                    self._invalidate_scene_outputs(session, scene)
            return _visual_profile(row)

    def restore_project_canon_references(
        self,
        project_id: uuid.UUID,
        draft: VisualProfileDraft,
    ) -> tuple[StoredVisualProfileRevision, int]:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id)
            previous_profile_id = project.current_visual_profile_revision_id
            normalized_bindings = self._normalize_look_reference_bindings(
                session,
                project_id=project_id,
                bindings=draft.reference_bindings,
                profile_only=True,
            )
            draft = draft.model_copy(update={"reference_bindings": normalized_bindings})
            reference_snapshot = _reference_snapshot(session, normalized_bindings)
            profile_hash = _profile_hash(draft, reference_snapshot=reference_snapshot)
            profile = session.scalar(
                select(VisualProfileRevision).where(
                    VisualProfileRevision.production_run_id == project_id,
                    VisualProfileRevision.profile_hash == profile_hash,
                )
            )
            if profile is None:
                profile = self._create_visual_profile_revision(
                    session,
                    project=project,
                    draft=draft,
                    reference_snapshot=reference_snapshot,
                )
            project.current_visual_profile_revision_id = profile.id
            project.default_reference_bindings_json = _project_reference_bindings(draft)

            scene_look_ids = set(
                session.execute(
                    select(Asset.id).where(
                        Asset.production_run_id == project_id,
                        Asset.role == "scene_look",
                    )
                ).scalars()
            )
            cleaned_shot_count = 0
            shot_rows = session.execute(
                select(ShotCard)
                .join(Scene, Scene.id == ShotCard.scene_id)
                .where(
                    Scene.production_run_id == project_id,
                    Scene.active.is_(True),
                )
            ).scalars()
            for shot_row in shot_rows:
                scene = _required(session, Scene, shot_row.scene_id)
                draft_before = _shot(shot_row, project_id).draft
                selected_scene_look_ids = set(scene_look_ids)
                if scene.selected_look_asset_id is not None:
                    selected_scene_look_ids.add(scene.selected_look_asset_id)
                filtered = [
                    binding
                    for binding in draft_before.reference_bindings
                    if not (
                        binding.asset_id in selected_scene_look_ids
                        and binding.role is ReferenceRole.IDENTITY
                    )
                ]
                if len(filtered) == len(draft_before.reference_bindings):
                    continue
                updated = draft_before.model_copy(update={"reference_bindings": filtered})
                _write_shot_draft(shot_row, updated)
                shot_row.draft_revision += 1
                shot_row.selected_anchor_asset_id = None
                shot_row.selected_video_asset_id = None
                shot_row.status = ShotStatus.READY.value
                cleaned_shot_count += 1
            if cleaned_shot_count:
                self._invalidate_project_sequence(session, project_id)
            if previous_profile_id != profile.id or cleaned_shot_count:
                scenes = list(
                    session.scalars(
                        select(Scene).where(
                            Scene.production_run_id == project_id,
                            Scene.active.is_(True),
                        )
                    )
                )
                for scene in scenes:
                    self._invalidate_scene_outputs(session, scene)
            return _visual_profile(profile), cleaned_shot_count

    def get_project(self, project_id: uuid.UUID) -> StoredProject:
        with self._sessions() as session:
            return _project_checked(_required(session, ProductionRun, project_id))

    def project_read_model(self, project_id: uuid.UUID) -> ProjectReadModel:
        """Load one complete production projection without repository fan-out."""

        with self._sessions() as session:
            project_row = self._require_project(session, project_id)
            if project_row.current_visual_profile_revision_id is None:
                raise RecordNotFoundError("project has no current visual profile")
            profile_row = _required(
                session,
                VisualProfileRevision,
                project_row.current_visual_profile_revision_id,
            )
            scene_rows = tuple(
                session.execute(
                    select(Scene)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order)
                ).scalars()
            )
            scene_ids = [row.id for row in scene_rows]
            shot_rows = (
                tuple(
                    session.execute(
                        select(ShotCard)
                        .where(ShotCard.scene_id.in_(scene_ids))
                        .order_by(ShotCard.scene_id, ShotCard.sort_order)
                    ).scalars()
                )
                if scene_ids
                else ()
            )
            step_rows = tuple(
                session.execute(
                    select(WorkflowStep)
                    .where(WorkflowStep.production_run_id == project_id)
                    .order_by(WorkflowStep.created_at, WorkflowStep.attempt)
                ).scalars()
            )
            step_ids = [row.id for row in step_rows]
            prompt_rows = (
                tuple(
                    session.execute(
                        select(PromptRecord)
                        .where(PromptRecord.step_id.in_(step_ids))
                        .order_by(PromptRecord.created_at)
                    ).scalars()
                )
                if step_ids
                else ()
            )
            review_rows = (
                tuple(
                    session.execute(
                        select(Review)
                        .where(Review.step_id.in_(step_ids))
                        .order_by(Review.created_at)
                    ).scalars()
                )
                if step_ids
                else ()
            )
            asset_rows = tuple(
                session.execute(
                    select(Asset)
                    .where(
                        or_(
                            Asset.production_run_id == project_id,
                            Asset.scope == "canon",
                        )
                    )
                    .order_by(Asset.created_at)
                ).scalars()
            )
            sequence_rows = tuple(
                session.execute(
                    select(VideoSequence)
                    .where(VideoSequence.production_run_id == project_id)
                    .order_by(VideoSequence.revision)
                ).scalars()
            )
            return ProjectReadModel(
                project=_project_checked(project_row),
                visual_profile=_visual_profile(profile_row),
                scenes=tuple(_scene(row) for row in scene_rows),
                shots=tuple(_shot(row, project_id) for row in shot_rows),
                steps=tuple(_step(row) for row in step_rows),
                prompts=tuple(_prompt(row) for row in prompt_rows),
                assets=tuple(_asset(row, self._asset_root) for row in asset_rows),
                reviews=tuple(_review(row) for row in review_rows),
                sequences=tuple(_sequence(row) for row in sequence_rows),
            )

    def shot_generation_read_model(
        self,
        shot_id: uuid.UUID,
    ) -> ShotGenerationReadModel:
        """Load one shot workspace without reading unrelated project history."""

        with self._sessions() as session:
            shot_row = _required(session, ShotCard, shot_id)
            scene_row = _required(session, Scene, shot_row.scene_id)
            project_row = self._require_project(session, scene_row.production_run_id)
            if project_row.current_visual_profile_revision_id is None:
                raise RecordNotFoundError("project has no current visual profile")
            profile_row = _required(
                session,
                VisualProfileRevision,
                project_row.current_visual_profile_revision_id,
            )
            scene_shot_rows = tuple(
                session.execute(
                    select(ShotCard)
                    .where(ShotCard.scene_id == scene_row.id)
                    .order_by(ShotCard.sort_order)
                ).scalars()
            )
            step_rows = tuple(
                session.execute(
                    select(WorkflowStep)
                    .where(WorkflowStep.shot_card_id == shot_id)
                    .order_by(WorkflowStep.created_at, WorkflowStep.attempt)
                ).scalars()
            )
            step_ids = [row.id for row in step_rows]
            prompt_rows = (
                tuple(
                    session.execute(
                        select(PromptRecord)
                        .where(PromptRecord.step_id.in_(step_ids))
                        .order_by(PromptRecord.created_at)
                    ).scalars()
                )
                if step_ids
                else ()
            )
            review_rows = (
                tuple(
                    session.execute(
                        select(Review)
                        .where(Review.step_id.in_(step_ids))
                        .order_by(Review.created_at)
                    ).scalars()
                )
                if step_ids
                else ()
            )
            asset_rows = tuple(
                session.execute(
                    select(Asset)
                    .where(
                        or_(
                            Asset.production_run_id == project_row.id,
                            Asset.scope == "canon",
                        )
                    )
                    .order_by(Asset.created_at)
                ).scalars()
            )
            return ShotGenerationReadModel(
                project=_project_checked(project_row),
                visual_profile=_visual_profile(profile_row),
                scene=_scene(scene_row),
                shot=_shot(shot_row, project_row.id),
                scene_shots=tuple(
                    _shot(row, project_row.id) for row in scene_shot_rows
                ),
                steps=tuple(_step(row) for row in step_rows),
                prompts=tuple(_prompt(row) for row in prompt_rows),
                assets=tuple(_asset(row, self._asset_root) for row in asset_rows),
                reviews=tuple(_review(row) for row in review_rows),
            )

    def add_scene(self, project_id: uuid.UUID, draft: SceneDraft) -> StoredScene:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            order = (
                session.scalar(
                    select(func.coalesce(func.max(Scene.sort_order), 0)).where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
                    )
                )
                + 1
            )
            row = Scene(
                production_run_id=project_id,
                sort_order=order,
                title=draft.title,
                source_text=draft.source_text,
                chapter_label=draft.chapter_label,
                context_note=draft.context_note,
                story_mode=draft.story_mode.value,
                target_shot_count=draft.target_shot_count,
                look_plan_json=(
                    None
                    if draft.look_plan is None
                    else draft.look_plan.model_dump(mode="json", by_alias=True)
                ),
                status=SceneStatus.DRAFT.value,
            )
            session.add(row)
            self._invalidate_project_sequence(session, project_id)
            session.flush()
            return _scene(row)

    def update_scene(self, scene_id: uuid.UUID, draft: SceneDraft) -> StoredScene:
        with self._sessions.begin() as session:
            row = _required(session, Scene, scene_id)
            self._require_project(session, row.production_run_id)
            serialized_look_plan = (
                None
                if draft.look_plan is None
                else draft.look_plan.model_dump(mode="json", by_alias=True)
            )
            look_plan_changed = row.look_plan_json != serialized_look_plan
            changed = (
                row.title,
                row.source_text,
                row.chapter_label,
                row.context_note,
                row.story_mode,
                row.target_shot_count,
                row.look_plan_json,
            ) != (
                draft.title,
                draft.source_text,
                draft.chapter_label,
                draft.context_note,
                draft.story_mode.value,
                draft.target_shot_count,
                serialized_look_plan,
            )
            row.title = draft.title
            row.source_text = draft.source_text
            row.chapter_label = draft.chapter_label
            row.context_note = draft.context_note
            row.story_mode = draft.story_mode.value
            row.target_shot_count = draft.target_shot_count
            row.look_plan_json = serialized_look_plan
            if look_plan_changed and row.look_draft_json and draft.look_plan is not None:
                look_draft = SceneLookDraft.model_validate(row.look_draft_json).model_copy(
                    update={"look_plan": draft.look_plan}
                )
                row.look_draft_json = look_draft.model_dump(mode="json", by_alias=True)
                row.look_draft_revision += 1
            if changed:
                self._invalidate_scene_outputs(session, row)
            return _scene(row)

    def select_scene_look_asset(
        self,
        scene_id: uuid.UUID,
        asset_id: uuid.UUID | None,
    ) -> StoredScene:
        with self._sessions.begin() as session:
            scene = _required(session, Scene, scene_id)
            self._require_project(session, scene.production_run_id)
            if asset_id is not None:
                asset = _required(session, Asset, asset_id)
                if asset.scope != "canon" and asset.production_run_id != scene.production_run_id:
                    raise ValueError("scene look must be Canon or belong to the current project")
                if asset.media_type != "image" or asset.status != "approved":
                    raise ValueError("scene look must be an approved image")
                if not _asset(asset, self._asset_root).content_ready:
                    raise ValueError("scene look content is missing; repair or upload it first")
            if scene.selected_look_asset_id == asset_id:
                return _scene(scene)
            scene.selected_look_asset_id = asset_id
            self._invalidate_scene_outputs(session, scene)
            return _scene(scene)

    def get_scene_look_draft(self, scene_id: uuid.UUID) -> StoredScene:
        return self.get_scene(scene_id)

    def save_scene_look_draft(
        self,
        scene_id: uuid.UUID,
        *,
        expected_revision: int,
        draft: SceneLookDraft,
    ) -> StoredScene:
        with self._sessions.begin() as session:
            scene = _required(session, Scene, scene_id)
            self._require_project(session, scene.production_run_id)
            if scene.look_draft_revision != expected_revision:
                raise WorkflowConflictError(
                    "场景视觉基准草稿已被更新，请重新加载后再保存"
                )
            profile = _required(
                session,
                VisualProfileRevision,
                draft.visual_profile_revision_id,
            )
            if profile.production_run_id != scene.production_run_id:
                raise ValueError("定妆草稿的视觉档案不属于当前项目")
            normalized_bindings = self._normalize_look_reference_bindings(
                session,
                project_id=scene.production_run_id,
                bindings=draft.reference_bindings,
                profile_only=False,
            )
            draft = draft.model_copy(update={"reference_bindings": normalized_bindings})
            scene.look_plan_json = draft.look_plan.model_dump(mode="json", by_alias=True)
            scene.look_draft_json = draft.model_dump(mode="json", by_alias=True)
            scene.look_draft_revision += 1
            scene.selected_look_asset_id = None
            self._invalidate_scene_outputs(session, scene)
            return _scene(scene)

    def delete_scene(self, scene_id: uuid.UUID) -> None:
        with self._sessions.begin() as session:
            row = _required(session, Scene, scene_id)
            self._require_project(session, row.production_run_id)
            if session.scalar(
                select(func.count())
                .select_from(WorkflowStep)
                .where(WorkflowStep.scene_id == scene_id)
            ):
                raise ValueError(
                    "A scene with provider history cannot be deleted; archive it instead"
                )
            project_id = row.production_run_id
            session.delete(row)
            session.flush()
            self._compact_scene_order(session, project_id)
            self._invalidate_project_sequence(session, project_id)

    def reorder_scenes(self, project_id: uuid.UUID, scene_ids: tuple[uuid.UUID, ...]) -> None:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            rows = list(
                session.execute(
                    select(Scene)
                    .where(
                        Scene.production_run_id == project_id,
                        Scene.active.is_(True),
                    )
                    .order_by(Scene.sort_order)
                    .with_for_update()
                ).scalars()
            )
            if set(scene_ids) != {row.id for row in rows} or len(scene_ids) != len(rows):
                raise ValueError("scene order must contain every project scene exactly once")
            by_id = {row.id: row for row in rows}
            for offset, row in enumerate(rows, 1):
                row.sort_order = -offset
            session.flush()
            for order, scene_id in enumerate(scene_ids, 1):
                by_id[scene_id].sort_order = order
            self._invalidate_project_sequence(session, project_id)

    def list_scenes(self, project_id: uuid.UUID) -> tuple[StoredScene, ...]:
        with self._sessions() as session:
            self._require_project(session, project_id)
            rows = session.execute(
                select(Scene)
                .where(
                    Scene.production_run_id == project_id,
                    Scene.active.is_(True),
                )
                .order_by(Scene.sort_order)
            ).scalars()
            return tuple(_scene(row) for row in rows)

    def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
        with self._sessions() as session:
            row = _required(session, Scene, scene_id)
            self._require_project(session, row.production_run_id)
            return _scene(row)

    def storyboard_production_context(self, scene_id: uuid.UUID) -> dict[str, Any]:
        """Return the approved version lineage used by scene asset planning."""

        with self._sessions() as session:
            scene = _required(session, Scene, scene_id)
            storyboard = session.scalar(
                select(StoryboardRevision)
                .where(
                    StoryboardRevision.production_run_id == scene.production_run_id,
                    StoryboardRevision.status.in_(
                        ("structure_approved", "production_approved")
                    ),
                )
                .order_by(StoryboardRevision.revision.desc())
                .limit(1)
            )
            if storyboard is None:
                return {
                    "structureApproved": False,
                    "generationPlanApproved": False,
                }
            plan = session.scalar(
                select(GenerationPlan)
                .where(
                    GenerationPlan.storyboard_revision_id == storyboard.id,
                )
                .order_by(GenerationPlan.revision.desc())
                .limit(1)
            )
            if plan is None:
                return {
                    "storyboardRevisionId": str(storyboard.id),
                    "structureHash": storyboard.structure_hash,
                    "structureApproved": True,
                    "generationPlanApproved": False,
                }
            scene_mapping_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(GenerationClipShot)
                    .join(
                        ShotCard,
                        ShotCard.id == GenerationClipShot.shot_card_id,
                    )
                    .where(
                        GenerationClipShot.generation_plan_id == plan.id,
                        ShotCard.scene_id == scene.id,
                    )
                )
                or 0
            )
            editorial_rows = list(
                session.execute(
                    select(ShotBeat)
                    .where(
                        ShotBeat.storyboard_revision_id == storyboard.id,
                        ShotBeat.scene_id == scene.id,
                    )
                    .order_by(ShotBeat.sort_order)
                ).scalars()
            )
            return {
                "storyboardRevisionId": str(storyboard.id),
                "structureHash": storyboard.structure_hash,
                "structureApproved": True,
                "generationPlanId": str(plan.id),
                "generationPlanHash": plan.input_hash,
                "generationPlanApproved": plan.status == "approved",
                "sceneGenerationClipCount": scene_mapping_count,
                "editorialShots": [
                    {
                        "id": str(row.id),
                        "order": row.sort_order,
                        "title": row.title,
                        "durationSeconds": row.duration_seconds,
                        "visualDescription": row.visual_description,
                        "childAction": row.child_action,
                        "catAction": row.cat_action,
                        "spatialRelation": row.spatial_relation,
                        "camera": row.camera,
                    }
                    for row in editorial_rows
                ],
            }

    def generation_clip_production_context(
        self,
        shot_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Return the immutable production-package lineage for a real clip."""

        with self._sessions() as session:
            shot = _required(session, ShotCard, shot_id)
            scene = _required(session, Scene, shot.scene_id)
            if shot.generation_plan_id is None:
                return {"managedByStoryboard": False}
            plan = _required(session, GenerationPlan, shot.generation_plan_id)
            storyboard = _required(
                session,
                StoryboardRevision,
                plan.storyboard_revision_id,
            )
            latest_storyboard = session.scalar(
                select(StoryboardRevision)
                .where(
                    StoryboardRevision.production_run_id == scene.production_run_id,
                    StoryboardRevision.status != "superseded",
                )
                .order_by(StoryboardRevision.revision.desc())
                .limit(1)
            )
            latest_plan = session.scalar(
                select(GenerationPlan)
                .where(GenerationPlan.storyboard_revision_id == storyboard.id)
                .order_by(GenerationPlan.revision.desc())
                .limit(1)
            )
            prompt = (
                None
                if shot.prompt_id is None
                else session.get(PromptRecord, shot.prompt_id)
            )
            prompt_snapshot = (
                {} if prompt is None else dict(prompt.input_snapshot_json or {})
            )
            legacy_plan = plan.capability_revision.startswith("legacy-")
            legacy_prompt_lineage = bool(
                legacy_plan
                and prompt is not None
                and prompt.business_object_type != "generation_clip"
            )
            project = session.get(ProductionRun, scene.production_run_id)
            reference_bindings = prompt_snapshot.get("referenceBindings")
            reference_lineage_current = isinstance(reference_bindings, list)
            if reference_lineage_current:
                for reference in reference_bindings:
                    if not isinstance(reference, dict) or not reference.get("assetId"):
                        reference_lineage_current = False
                        break
                    try:
                        reference_asset_id = uuid.UUID(str(reference["assetId"]))
                    except ValueError:
                        reference_lineage_current = False
                        break
                    reference_asset = session.get(Asset, reference_asset_id)
                    if (
                        reference_asset is None
                        or reference_asset.status not in {"approved", "ready"}
                        or reference_asset.sha256 != reference.get("sha256")
                    ):
                        reference_lineage_current = False
                        break
            package_inputs_current = bool(
                legacy_prompt_lineage
                or (
                    project is not None
                    and prompt_snapshot.get("storyRevisionId")
                    == str(storyboard.story_revision_id)
                    and prompt_snapshot.get("visualProfileRevisionId")
                    == (
                        None
                        if project.current_visual_profile_revision_id is None
                        else str(project.current_visual_profile_revision_id)
                    )
                    and prompt_snapshot.get("sceneId") == str(scene.id)
                    and prompt_snapshot.get("sceneLookDraftRevision")
                    == scene.look_draft_revision
                    and reference_lineage_current
                )
            )
            lineage_current = bool(
                latest_storyboard is not None
                and latest_storyboard.id == storyboard.id
                and latest_plan is not None
                and latest_plan.id == plan.id
                and plan.status == "approved"
                and storyboard.status == "production_approved"
                and storyboard.production_package_hash
                and prompt is not None
                and prompt.status == "succeeded"
                and (
                    legacy_prompt_lineage
                    or (
                        prompt.business_object_type == "generation_clip"
                        and prompt.business_object_id == shot.id
                        and prompt_snapshot.get("storyboardRevisionId")
                        == str(storyboard.id)
                        and prompt_snapshot.get("structureHash")
                        == storyboard.structure_hash
                        and prompt_snapshot.get("generationPlanId") == str(plan.id)
                        and prompt_snapshot.get("generationPlanHash")
                        == plan.input_hash
                        and package_inputs_current
                    )
                )
            )
            return {
                "managedByStoryboard": True,
                "lineageCurrent": lineage_current,
                "storyboardRevisionId": str(storyboard.id),
                "structureHash": storyboard.structure_hash,
                "generationPlanId": str(plan.id),
                "generationPlanHash": plan.input_hash,
                "generationPlanApproved": plan.status == "approved",
                "legacyPlan": legacy_plan,
                "productionPackageApproved": bool(
                    storyboard.status == "production_approved"
                    and storyboard.production_package_hash
                    and package_inputs_current
                ),
                "productionPackageHash": storyboard.production_package_hash,
                "compiledPromptId": None if prompt is None else str(prompt.id),
                "compiledPromptInputHash": None if prompt is None else prompt.input_hash,
                "compiledPromptHash": None if prompt is None else prompt.sha256,
                "compiledPrompt": None if prompt is None else prompt.prompt_text,
                "compiledShot": prompt_snapshot.get("shot"),
                "referenceBindings": (
                    [] if not isinstance(reference_bindings, list) else reference_bindings
                ),
            }

    def approved_character_design_assets(
        self,
        project_id: uuid.UUID,
    ) -> tuple[StoredAsset, ...]:
        with self._sessions() as session:
            revision = session.scalar(
                select(CharacterDesignRevision)
                .where(
                    CharacterDesignRevision.production_run_id == project_id,
                    CharacterDesignRevision.status == "approved",
                )
                .order_by(CharacterDesignRevision.revision.desc())
                .limit(1)
            )
            if revision is None:
                return ()
            rows = list(
                session.scalars(
                    select(CharacterDesignAsset).where(
                        CharacterDesignAsset.character_design_revision_id == revision.id,
                        CharacterDesignAsset.selected.is_(True),
                    )
                )
            )
            slot_order = {"child": 0, "cat": 1, "pair_scale": 2}
            rows.sort(key=lambda row: slot_order.get(row.slot, 99))
            assets = {
                asset.id: asset
                for asset in session.scalars(
                    select(Asset).where(Asset.id.in_([row.asset_id for row in rows]))
                )
            }
            return tuple(
                _asset(assets[row.asset_id], self._asset_root)
                for row in rows
                if row.asset_id in assets
            )

    def requires_character_design_assets(self, project_id: uuid.UUID) -> bool:
        with self._sessions() as session:
            return bool(
                session.scalar(
                    select(ProductionRecipeInstance.id)
                    .where(
                        ProductionRecipeInstance.production_run_id == project_id,
                        ProductionRecipeInstance.lifecycle_status == "active",
                    )
                    .limit(1)
                )
            )

    def add_shot(self, scene_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot:
        with self._sessions.begin() as session:
            scene = _required(session, Scene, scene_id)
            self._require_project(session, scene.production_run_id)
            self._validate_reference_bindings(
                session,
                project_id=scene.production_run_id,
                bindings=draft.reference_bindings,
            )
            order = (
                session.scalar(
                    select(func.coalesce(func.max(ShotCard.sort_order), 0)).where(
                        ShotCard.scene_id == scene_id
                    )
                )
                + 1
            )
            row = _new_shot(scene_id, order, draft)
            session.add(row)
            self._invalidate_project_sequence(session, scene.production_run_id)
            scene.status = SceneStatus.READY.value
            session.flush()
            return _shot(row, scene.production_run_id)

    def replace_shots(
        self, scene_id: uuid.UUID, drafts: tuple[ShotCardDraft, ...]
    ) -> tuple[StoredShot, ...]:
        if not drafts:
            raise ValueError("AI suggestions must contain at least one shot")
        with self._sessions.begin() as session:
            scene = _required(session, Scene, scene_id)
            self._require_project(session, scene.production_run_id)
            for draft in drafts:
                self._validate_reference_bindings(
                    session,
                    project_id=scene.production_run_id,
                    bindings=draft.reference_bindings,
                )
            rows = self._replace_shots_locked(session, scene=scene, drafts=drafts)
            return tuple(_shot(row, scene.production_run_id) for row in rows)

    def accept_story_diagnosis(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, Any],
    ) -> StoredStep:
        with self._sessions.begin() as session:
            step = _required(session, WorkflowStep, step_id)
            if (
                StepKind(step.kind) is not StepKind.DIRECTOR
                or StepStatus(step.status) is not StepStatus.SUCCEEDED
                or step.operation_key != "director:story-diagnosis"
                or step.scene_id is None
                or step.shot_card_id is not None
            ):
                raise ValueError("step is not a succeeded story diagnosis")
            snapshot = dict(step.input_snapshot_json)
            if "acceptedAt" in snapshot:
                raise WorkflowConflictError("该剧情诊断已经接受过")
            scene = _required(session, Scene, step.scene_id)
            current_hash = story_source_hash(_scene(scene).draft)
            if (
                snapshot.get("sourceHash") != expected_source_hash
                or current_hash != expected_source_hash
            ):
                raise WorkflowConflictError("场景剧情已变化，旧诊断不能再接受")
            snapshot["acceptedOutput"] = accepted_output
            snapshot["acceptedAt"] = datetime.now(timezone.utc).isoformat()
            step.input_snapshot_json = snapshot
            return _step(step)

    def accept_story_rewrite(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, Any],
        rewritten_story: str,
    ) -> StoredScene:
        with self._sessions.begin() as session:
            step = _required(session, WorkflowStep, step_id)
            if (
                StepKind(step.kind) is not StepKind.DIRECTOR
                or StepStatus(step.status) is not StepStatus.SUCCEEDED
                or step.operation_key != "director:story-rewrite"
                or step.scene_id is None
                or step.shot_card_id is not None
            ):
                raise ValueError("step is not a succeeded story rewrite")
            snapshot = dict(step.input_snapshot_json)
            if "acceptedAt" in snapshot:
                raise WorkflowConflictError("该剧情重写已经接受过")
            scene = _required(session, Scene, step.scene_id)
            current_hash = story_source_hash(_scene(scene).draft)
            if (
                snapshot.get("sourceHash") != expected_source_hash
                or current_hash != expected_source_hash
            ):
                raise WorkflowConflictError("场景剧情已变化，旧重写稿不能再接受")
            scene.source_text = rewritten_story
            shots = session.execute(
                select(ShotCard).where(ShotCard.scene_id == scene.id)
            ).scalars()
            for shot in shots:
                shot.selected_anchor_asset_id = None
                shot.selected_video_asset_id = None
                shot.status = ShotStatus.READY.value
            self._invalidate_project_sequence(session, scene.production_run_id)
            snapshot["acceptedOutput"] = accepted_output
            snapshot["acceptedAt"] = datetime.now(timezone.utc).isoformat()
            step.input_snapshot_json = snapshot
            return _scene(scene)

    def accept_story_expansion(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, Any],
        expanded_story: str,
    ) -> StoredScene:
        with self._sessions.begin() as session:
            step = _required(session, WorkflowStep, step_id)
            if (
                StepKind(step.kind) is not StepKind.DIRECTOR
                or StepStatus(step.status) is not StepStatus.SUCCEEDED
                or step.operation_key != "director:story-expansion"
                or step.scene_id is None
                or step.shot_card_id is not None
            ):
                raise ValueError("step is not a succeeded story expansion")
            snapshot = dict(step.input_snapshot_json)
            if "acceptedAt" in snapshot:
                raise WorkflowConflictError("该剧情扩写已经接受过")
            scene = _required(session, Scene, step.scene_id)
            current_hash = story_source_hash(_scene(scene).draft)
            if (
                snapshot.get("sourceHash") != expected_source_hash
                or current_hash != expected_source_hash
            ):
                raise WorkflowConflictError("场景剧情已变化，旧扩写稿不能再接受")
            scene.source_text = expanded_story
            shots = session.execute(
                select(ShotCard).where(ShotCard.scene_id == scene.id)
            ).scalars()
            for shot in shots:
                shot.selected_anchor_asset_id = None
                shot.selected_video_asset_id = None
                shot.status = ShotStatus.READY.value
            self._invalidate_project_sequence(session, scene.production_run_id)
            snapshot["acceptedOutput"] = accepted_output
            snapshot["acceptedAt"] = datetime.now(timezone.utc).isoformat()
            step.input_snapshot_json = snapshot
            return _scene(scene)

    def accept_visual_asset_plan(
        self,
        *,
        step_id: uuid.UUID,
        expected_storyboard_revision_id: uuid.UUID,
        expected_structure_hash: str,
        expected_generation_plan_id: uuid.UUID,
        expected_generation_plan_hash: str,
        accepted_output: AcceptedVisualAssetPlan,
    ) -> StoredStep:
        with self._sessions.begin() as session:
            step = _required(session, WorkflowStep, step_id)
            if (
                StepKind(step.kind) is not StepKind.DIRECTOR
                or StepStatus(step.status) is not StepStatus.SUCCEEDED
                or step.operation_key != "director:visual-asset-plan"
                or step.scene_id is None
                or step.shot_card_id is not None
            ):
                raise ValueError("step is not a succeeded visual asset plan")
            snapshot = dict(step.input_snapshot_json)
            if "acceptedAt" in snapshot:
                raise WorkflowConflictError("该视觉资产规划已经接受过")
            scene = _required(session, Scene, step.scene_id)
            storyboard = session.scalar(
                select(StoryboardRevision)
                .where(
                    StoryboardRevision.id == expected_storyboard_revision_id,
                    StoryboardRevision.production_run_id == scene.production_run_id,
                    StoryboardRevision.status.in_(
                        ("structure_approved", "production_approved")
                    ),
                )
            )
            if (
                storyboard is None
                or storyboard.structure_hash != expected_structure_hash
                or snapshot.get("storyboardRevisionId")
                != str(expected_storyboard_revision_id)
                or snapshot.get("structureHash") != expected_structure_hash
            ):
                raise WorkflowConflictError("分镜结构已经变化，旧视觉资产规划不能再接受")
            plan = session.scalar(
                select(GenerationPlan).where(
                    GenerationPlan.id == expected_generation_plan_id,
                    GenerationPlan.storyboard_revision_id == storyboard.id,
                    GenerationPlan.status == "approved",
                )
            )
            if (
                plan is None
                or plan.input_hash != expected_generation_plan_hash
                or snapshot.get("generationPlanId") != str(expected_generation_plan_id)
                or snapshot.get("generationPlanHash") != expected_generation_plan_hash
            ):
                raise WorkflowConflictError("生成编排已经变化，旧视觉资产规划不能再接受")
            snapshot["acceptedOutput"] = accepted_output.model_dump(
                mode="json", by_alias=True
            )
            snapshot["acceptedAt"] = datetime.now(timezone.utc).isoformat()
            step.input_snapshot_json = snapshot
            if self._apply_visual_asset_plan_bindings(
                session,
                scene=scene,
                accepted_output=accepted_output,
            ):
                self._invalidate_scene_outputs(session, scene)
            return _step(step)

    def revise_visual_asset_plan(
        self,
        *,
        step_id: uuid.UUID,
        expected_revision: int,
        accepted_output: AcceptedVisualAssetPlan,
        note: str,
    ) -> StoredStep:
        with self._sessions.begin() as session:
            source = _required(session, WorkflowStep, step_id)
            if (
                StepKind(source.kind) is not StepKind.DIRECTOR
                or StepStatus(source.status) is not StepStatus.SUCCEEDED
                or source.operation_key != "director:visual-asset-plan"
                or source.scene_id is None
                or source.shot_card_id is not None
                or not isinstance(source.input_snapshot_json.get("acceptedOutput"), dict)
            ):
                raise ValueError("step is not an accepted visual asset plan")
            latest = session.scalar(
                select(WorkflowStep)
                .where(
                    WorkflowStep.scene_id == source.scene_id,
                    WorkflowStep.shot_card_id.is_(None),
                    WorkflowStep.operation_key == "director:visual-asset-plan",
                    WorkflowStep.status == StepStatus.SUCCEEDED.value,
                )
                .order_by(WorkflowStep.attempt.desc())
                .with_for_update()
                .limit(1)
            )
            if (
                source.attempt != expected_revision
                or latest is None
                or latest.id != source.id
            ):
                raise WorkflowConflictError(
                    "视觉资产规划已更新，请基于最新规划版本重新应用修改"
                )
            previous_output = AcceptedVisualAssetPlan.model_validate(
                source.input_snapshot_json["acceptedOutput"]
            )
            previous_json = previous_output.model_dump(mode="json", by_alias=True)
            accepted_json = accepted_output.model_dump(mode="json", by_alias=True)
            if previous_json == accepted_json:
                return _step(source)

            scene = _required(session, Scene, source.scene_id)
            now = datetime.now(timezone.utc)
            attempt = source.attempt + 1
            input_hash = hashlib.sha256(
                json.dumps(
                    accepted_json,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            idempotency_key = hashlib.sha256(
                "|".join(
                    (
                        str(source.production_run_id),
                        str(source.scene_id),
                        source.operation_key,
                        str(attempt),
                        input_hash,
                    )
                ).encode("utf-8")
            ).hexdigest()
            snapshot = {
                **source.input_snapshot_json,
                "source": "manual",
                "manualRevisionOfStepId": str(source.id),
                "manualRevisionNote": note,
                "acceptedOutput": accepted_json,
                "acceptedAt": now.isoformat(),
            }
            revised = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=source.production_run_id,
                scene_id=source.scene_id,
                shot_card_id=None,
                kind=StepKind.DIRECTOR.value,
                status=StepStatus.SUCCEEDED.value,
                attempt=attempt,
                operation_key=source.operation_key,
                idempotency_key=idempotency_key,
                provider="manual",
                model="human-editor",
                input_hash=input_hash,
                input_snapshot_json=snapshot,
                completed_at=now,
            )
            session.add(revised)
            session.flush()
            self._apply_visual_asset_plan_bindings(
                session,
                scene=scene,
                accepted_output=accepted_output,
                previous_output=previous_output,
            )
            self._invalidate_scene_outputs(session, scene)
            return _step(revised)

    def _apply_visual_asset_plan_bindings(
        self,
        session: Session,
        *,
        scene: Scene,
        accepted_output: AcceptedVisualAssetPlan,
        previous_output: AcceptedVisualAssetPlan | None = None,
    ) -> bool:
        existing_selections = [
            selection
            for selection in accepted_output.selections
            if selection.action.value == "existing"
        ]
        previous_existing_ids = {
            selection.existing_asset_id
            for selection in (() if previous_output is None else previous_output.selections)
            if selection.action.value == "existing" and selection.existing_asset_id is not None
        }
        project = self._require_project(session, scene.production_run_id)
        if project.current_visual_profile_revision_id is None:
            raise ValueError("项目尚未建立视觉档案")
        look_draft = (
            SceneLookDraft.model_validate(scene.look_draft_json)
            if scene.look_draft_json
            else SceneLookDraft(
                visualProfileRevisionId=project.current_visual_profile_revision_id,
                lookPlan=SceneLookPlan.model_validate(scene.look_plan_json or {}),
            )
        )
        bindings = [
            binding
            for binding in look_draft.reference_bindings
            if not (
                binding.asset_id in previous_existing_ids
                and (binding.instruction or "").startswith("复用资产规划“")
            )
        ]
        bound_ids = {binding.asset_id for binding in bindings}
        for selection in existing_selections:
            asset_id = selection.existing_asset_id
            if asset_id is None or asset_id in bound_ids:
                continue
            bindings.append(
                LookReferenceBinding(
                    assetId=asset_id,
                    purpose=LookReferencePurpose(selection.purpose.value),
                    instruction=(
                        f"复用资产规划“{selection.display_name}”，只承担"
                        f"{selection.purpose.value}职责，不改写长期身份"
                    ),
                )
            )
            bound_ids.add(asset_id)
        normalized_bindings = self._normalize_look_reference_bindings(
            session,
            project_id=scene.production_run_id,
            bindings=bindings,
            profile_only=False,
        )
        updated_draft = look_draft.model_copy(
            update={
                "visual_profile_revision_id": project.current_visual_profile_revision_id,
                "reference_bindings": normalized_bindings,
            }
        )
        if updated_draft == look_draft:
            return False
        scene.look_plan_json = updated_draft.look_plan.model_dump(
            mode="json", by_alias=True
        )
        scene.look_draft_json = updated_draft.model_dump(
            mode="json", by_alias=True
        )
        scene.look_draft_revision += 1
        scene.selected_look_asset_id = None
        return True

    def accept_scene_suggestions(
        self,
        *,
        step_id: uuid.UUID,
        drafts: tuple[ShotCardDraft, ...],
        look_plan: SceneLookPlan | None,
        accepted_output: dict[str, Any],
        apply_mode: str,
        source_shot_revisions: dict[uuid.UUID, int],
    ) -> tuple[StoredShot, ...]:
        if not drafts:
            raise ValueError("accepted suggestions must contain at least one video clip")
        with self._sessions.begin() as session:
            step = _required(session, WorkflowStep, step_id)
            if (
                StepKind(step.kind) is not StepKind.DIRECTOR
                or StepStatus(step.status) is not StepStatus.SUCCEEDED
                or step.operation_key != "director:shot-suggestions"
                or step.scene_id is None
                or step.shot_card_id is not None
            ):
                raise ValueError("step is not an accepted scene suggestion result")
            scene = _required(session, Scene, step.scene_id)
            snapshot = dict(step.input_snapshot_json)
            if "acceptedAt" in snapshot:
                raise WorkflowConflictError("该分镜建议已经接受过")
            if snapshot.get("sourceHash") != story_source_hash(_scene(scene).draft):
                raise WorkflowConflictError("场景剧情已变化，旧分镜建议不能再接受")
            for draft in drafts:
                self._validate_reference_bindings(
                    session,
                    project_id=scene.production_run_id,
                    bindings=draft.reference_bindings,
                )
            if apply_mode == "replace":
                if source_shot_revisions:
                    raise ValueError("replace mode does not accept source shot revisions")
                rows = self._replace_shots_locked(session, scene=scene, drafts=drafts)
            elif apply_mode == "update_existing":
                rows = list(
                    session.execute(
                        select(ShotCard)
                        .where(ShotCard.scene_id == scene.id)
                        .order_by(ShotCard.sort_order)
                        .with_for_update()
                    ).scalars()
                )
                if len(rows) != len(drafts):
                    raise WorkflowConflictError(
                        "当前片段数量与分镜版本不一致，不能按现有片段更新"
                    )
                expected_ids = {row.id for row in rows}
                if set(source_shot_revisions) != expected_ids:
                    raise WorkflowConflictError(
                        "片段集合已变化，请重新加载后再接受分镜版本"
                    )
                stale = [
                    row
                    for row in rows
                    if source_shot_revisions[row.id] != row.draft_revision
                ]
                if stale:
                    raise WorkflowConflictError(
                        "片段草稿已变化，请重新加载后再接受分镜版本"
                    )
                changed = False
                for row, draft in zip(rows, drafts, strict=True):
                    current = _shot(row, scene.production_run_id).draft
                    if current == draft:
                        continue
                    _write_shot_draft(row, draft)
                    row.draft_revision += 1
                    row.selected_anchor_asset_id = None
                    row.selected_video_asset_id = None
                    row.status = ShotStatus.READY.value
                    changed = True
                if changed:
                    self._invalidate_project_sequence(session, scene.production_run_id)
                    scene.status = SceneStatus.READY.value
                session.flush()
            else:
                raise ValueError(f"unsupported suggestion apply mode: {apply_mode}")
            scene.look_plan_json = (
                None
                if look_plan is None
                else look_plan.model_dump(mode="json", by_alias=True)
            )
            if look_plan is not None:
                if scene.look_draft_json:
                    look_draft = SceneLookDraft.model_validate(
                        scene.look_draft_json
                    ).model_copy(update={"look_plan": look_plan})
                else:
                    project = self._require_project(session, scene.production_run_id)
                    if project.current_visual_profile_revision_id is None:
                        raise ValueError("项目尚未建立视觉档案")
                    profile = _required(
                        session,
                        VisualProfileRevision,
                        project.current_visual_profile_revision_id,
                    )
                    look_draft = SceneLookDraft(
                        visualProfileRevisionId=profile.id,
                        lookPlan=look_plan,
                        referenceBindings=_environment_profile_bindings(
                            session,
                            profile.reference_bindings_json,
                            look_plan,
                        ),
                    )
                scene.look_draft_json = look_draft.model_dump(mode="json", by_alias=True)
                scene.look_draft_revision += 1
            accepted_output = {
                **accepted_output,
                "appliedShotSnapshotHash": shot_snapshot_hash(
                    (row.id, row.draft_revision, _shot(row, scene.production_run_id).draft)
                    for row in rows
                ),
                "appliedShotIds": [str(row.id) for row in rows],
            }
            snapshot["acceptedOutput"] = accepted_output
            snapshot["acceptedAt"] = datetime.now(timezone.utc).isoformat()
            step.input_snapshot_json = snapshot
            return tuple(_shot(row, scene.production_run_id) for row in rows)

    def update_shot(self, shot_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot:
        with self._sessions.begin() as session:
            row = _required(session, ShotCard, shot_id)
            scene = _required(session, Scene, row.scene_id)
            self._require_project(session, scene.production_run_id)
            self._validate_reference_bindings(
                session,
                project_id=scene.production_run_id,
                bindings=draft.reference_bindings,
            )
            changed = _shot(row, scene.production_run_id).draft != draft
            _write_shot_draft(row, draft)
            if changed:
                row.draft_revision += 1
                row.selected_anchor_asset_id = None
                row.selected_video_asset_id = None
                row.status = ShotStatus.READY.value
                self._invalidate_project_sequence(session, scene.production_run_id)
            return _shot(row, scene.production_run_id)

    def accept_shot_assistance(
        self,
        *,
        step_id: uuid.UUID,
        source_draft_revision: int,
        patch: ShotAssistPatch | None,
        accepted_anchor_brief: str | None,
    ) -> StoredShot:
        with self._sessions.begin() as session:
            step = _required(session, WorkflowStep, step_id)
            if (
                StepKind(step.kind) is not StepKind.DIRECTOR
                or StepStatus(step.status) is not StepStatus.SUCCEEDED
                or step.operation_key != "director:shot-assistance"
                or step.shot_card_id is None
            ):
                raise ValueError("step is not a succeeded shot-assistance analysis")
            snapshot = dict(step.input_snapshot_json)
            if patch is not None and (
                snapshot.get("acceptedPatchAt")
                or bool(snapshot.get("acceptedOutput"))
            ):
                raise WorkflowConflictError("该片段字段修改已经接受过")
            if accepted_anchor_brief is not None and (
                snapshot.get("acceptedAnchorBriefAt")
                or bool(snapshot.get("acceptedAnchorBrief"))
            ):
                raise WorkflowConflictError("该开场静态画面稿已经接受过")
            recorded_revision = snapshot.get("sourceDraftRevision")
            if recorded_revision != source_draft_revision:
                raise WorkflowConflictError("接受请求与分析来源版本不一致")
            row = _required(session, ShotCard, step.shot_card_id)
            scene = _required(session, Scene, row.scene_id)
            if row.draft_revision != source_draft_revision:
                raise WorkflowConflictError("片段草稿已更新，旧分析不能再接受")
            current_anchor_brief = next(
                (
                    str(item.input_snapshot_json["acceptedAnchorBrief"]).strip()
                    for item in session.execute(
                        select(WorkflowStep)
                        .where(
                            WorkflowStep.shot_card_id == row.id,
                            WorkflowStep.status == StepStatus.SUCCEEDED.value,
                            WorkflowStep.operation_key.in_(
                                ("editor:anchor-brief", "director:shot-assistance")
                            ),
                        )
                        .order_by(WorkflowStep.created_at.desc())
                    ).scalars()
                    if item.input_snapshot_json.get("acceptedDraftRevision")
                    == source_draft_revision
                    and str(
                        item.input_snapshot_json.get("acceptedAnchorBrief") or ""
                    ).strip()
                ),
                None,
            )
            normalized_anchor_brief = (
                None
                if accepted_anchor_brief is None
                else accepted_anchor_brief.strip()
            )
            anchor_brief_changed = (
                normalized_anchor_brief is not None
                and normalized_anchor_brief != current_anchor_brief
            )
            current = _shot(row, scene.production_run_id).draft
            updated = current if patch is None else apply_shot_assist_patch(current, patch)
            self._validate_reference_bindings(
                session,
                project_id=scene.production_run_id,
                bindings=updated.reference_bindings,
            )
            changed = current != updated
            _write_shot_draft(row, updated)
            if changed:
                row.draft_revision += 1
            if changed or anchor_brief_changed:
                had_selection = (
                    row.selected_anchor_asset_id is not None
                    or row.selected_video_asset_id is not None
                )
                row.selected_anchor_asset_id = None
                row.selected_video_asset_id = None
                row.status = ShotStatus.READY.value
                if had_selection:
                    self._invalidate_project_sequence(session, scene.production_run_id)
            accepted_at = datetime.now(timezone.utc).isoformat()
            if patch is not None:
                snapshot["acceptedOutput"] = patch.model_dump(
                    mode="json", by_alias=True
                )
                snapshot["acceptedPatchAt"] = accepted_at
            if normalized_anchor_brief is not None:
                snapshot["acceptedAnchorBrief"] = normalized_anchor_brief
                snapshot["acceptedAnchorBriefAt"] = accepted_at
            snapshot["acceptedAt"] = accepted_at
            snapshot["acceptedDraftRevision"] = row.draft_revision
            step.input_snapshot_json = snapshot
            return _shot(row, scene.production_run_id)

    def save_manual_anchor_brief(
        self,
        *,
        shot_id: uuid.UUID,
        source_draft_revision: int,
        brief: str,
        input_hash: str,
    ) -> StoredStep:
        """Persist one accepted human-authored opening brief without a provider call."""

        normalized = brief.strip()
        if not normalized:
            raise ValueError("开场静态画面稿不能为空")
        now = datetime.now(timezone.utc)
        operation_key = "editor:anchor-brief"
        with self._sessions.begin() as session:
            shot = _required(session, ShotCard, shot_id)
            scene = _required(session, Scene, shot.scene_id)
            if shot.draft_revision != source_draft_revision:
                raise WorkflowConflictError("片段草稿已更新，请基于最新版本重新保存开场静态画面稿")

            accepted_steps = list(
                session.execute(
                    select(WorkflowStep)
                    .where(
                        WorkflowStep.shot_card_id == shot_id,
                        WorkflowStep.status == StepStatus.SUCCEEDED.value,
                        WorkflowStep.operation_key.in_(
                            (operation_key, "director:shot-assistance")
                        ),
                    )
                    .order_by(WorkflowStep.created_at.desc())
                ).scalars()
            )
            current = next(
                (
                    item
                    for item in accepted_steps
                    if item.input_snapshot_json.get("acceptedDraftRevision")
                    == source_draft_revision
                    and str(
                        item.input_snapshot_json.get("acceptedAnchorBrief") or ""
                    ).strip()
                ),
                None,
            )
            current_brief = (
                None
                if current is None
                else str(current.input_snapshot_json["acceptedAnchorBrief"]).strip()
            )
            if current is not None and current_brief == normalized:
                return _step(current)

            attempt = int(
                session.scalar(
                    select(func.coalesce(func.max(WorkflowStep.attempt), 0)).where(
                        WorkflowStep.shot_card_id == shot_id,
                        WorkflowStep.operation_key == operation_key,
                    )
                )
                or 0
            ) + 1
            idempotency_key = hashlib.sha256(
                "|".join(
                    (
                        str(scene.production_run_id),
                        str(shot_id),
                        operation_key,
                        str(source_draft_revision),
                        input_hash,
                    )
                ).encode("utf-8")
            ).hexdigest()
            step = WorkflowStep(
                id=uuid.uuid4(),
                production_run_id=scene.production_run_id,
                scene_id=scene.id,
                shot_card_id=shot_id,
                kind=StepKind.DIRECTOR.value,
                status=StepStatus.SUCCEEDED.value,
                attempt=attempt,
                operation_key=operation_key,
                idempotency_key=idempotency_key,
                provider="manual",
                model="human-editor",
                input_hash=input_hash,
                input_snapshot_json={
                    "source": "manual",
                    "sourceDraftRevision": source_draft_revision,
                    "acceptedDraftRevision": source_draft_revision,
                    "acceptedAnchorBrief": normalized,
                    "acceptedAnchorBriefAt": now.isoformat(),
                    "acceptedAt": now.isoformat(),
                },
                completed_at=now,
            )
            session.add(step)
            session.flush()
            session.add(
                PromptRecord(
                    step_id=step.id,
                    purpose=PromptPurpose.DIRECTOR.value,
                    model="human-editor",
                    prompt_text=normalized,
                    sha256=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                )
            )

            if current_brief != normalized:
                had_selection = (
                    shot.selected_anchor_asset_id is not None
                    or shot.selected_video_asset_id is not None
                )
                shot.selected_anchor_asset_id = None
                shot.selected_video_asset_id = None
                shot.status = ShotStatus.READY.value
                if had_selection:
                    self._invalidate_project_sequence(session, scene.production_run_id)
            return _step(step)

    def delete_shot(self, shot_id: uuid.UUID) -> None:
        with self._sessions.begin() as session:
            row = _required(session, ShotCard, shot_id)
            if session.scalar(
                select(func.count())
                .select_from(WorkflowStep)
                .where(WorkflowStep.shot_card_id == shot_id)
            ):
                raise ValueError("A shot with provider history cannot be deleted")
            scene_id = row.scene_id
            project_id = session.scalar(select(Scene.production_run_id).where(Scene.id == scene_id))
            session.delete(row)
            session.flush()
            self._compact_shot_order(session, scene_id)
            if project_id is not None:
                self._invalidate_project_sequence(session, project_id)

    def reorder_shots(self, scene_id: uuid.UUID, shot_ids: tuple[uuid.UUID, ...]) -> None:
        with self._sessions.begin() as session:
            rows = list(
                session.execute(
                    select(ShotCard)
                    .where(ShotCard.scene_id == scene_id)
                    .order_by(ShotCard.sort_order)
                    .with_for_update()
                ).scalars()
            )
            if set(shot_ids) != {row.id for row in rows} or len(shot_ids) != len(rows):
                raise ValueError("shot order must contain every scene shot exactly once")
            by_id = {row.id: row for row in rows}
            for offset, row in enumerate(rows, 1):
                row.sort_order = -offset
            session.flush()
            for order, shot_id in enumerate(shot_ids, 1):
                by_id[shot_id].sort_order = order
            scene = _required(session, Scene, scene_id)
            self._invalidate_project_sequence(session, scene.production_run_id)

    def list_shots(self, scene_id: uuid.UUID) -> tuple[StoredShot, ...]:
        with self._sessions() as session:
            scene = _required(session, Scene, scene_id)
            self._require_project(session, scene.production_run_id)
            rows = session.execute(
                select(ShotCard).where(ShotCard.scene_id == scene_id).order_by(ShotCard.sort_order)
            ).scalars()
            return tuple(_shot(row, scene.production_run_id) for row in rows)

    def get_shot(self, shot_id: uuid.UUID) -> StoredShot:
        with self._sessions() as session:
            row = _required(session, ShotCard, shot_id)
            scene = _required(session, Scene, row.scene_id)
            self._require_project(session, scene.production_run_id)
            return _shot(row, scene.production_run_id)

    def next_attempt(self, *, shot_id: uuid.UUID, operation_key: str) -> int:
        with self._sessions() as session:
            value = session.scalar(
                select(func.coalesce(func.max(WorkflowStep.attempt), 0)).where(
                    WorkflowStep.shot_card_id == shot_id,
                    WorkflowStep.operation_key == operation_key,
                )
            )
            return int(value) + 1

    def next_scene_attempt(self, *, scene_id: uuid.UUID, operation_key: str) -> int:
        with self._sessions() as session:
            value = session.scalar(
                select(func.coalesce(func.max(WorkflowStep.attempt), 0)).where(
                    WorkflowStep.scene_id == scene_id,
                    WorkflowStep.shot_card_id.is_(None),
                    WorkflowStep.operation_key == operation_key,
                )
            )
            return int(value) + 1

    def next_project_attempt(self, *, project_id: uuid.UUID, operation_key: str) -> int:
        with self._sessions() as session:
            value = session.scalar(
                select(func.coalesce(func.max(WorkflowStep.attempt), 0)).where(
                    WorkflowStep.production_run_id == project_id,
                    WorkflowStep.scene_id.is_(None),
                    WorkflowStep.shot_card_id.is_(None),
                    WorkflowStep.operation_key == operation_key,
                )
            )
            return int(value) + 1

    def create_step_with_prompt(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None,
        shot_id: uuid.UUID | None,
        kind: StepKind,
        operation_key: str,
        attempt: int,
        provider: str,
        model: str,
        input_hash: str,
        input_snapshot: dict[str, Any],
        purpose: PromptPurpose,
        prompt_text: str,
    ) -> tuple[StoredStep, StoredPrompt]:
        if not prompt_text.strip() or not operation_key.strip():
            raise ValueError("operation key and prompt are required")
        validate_prompt_purpose(kind, purpose, generation_intent=True)
        runtime_snapshot = current_execution_snapshot()
        persisted_snapshot = dict(input_snapshot)
        if runtime_snapshot is not None:
            persisted_snapshot["runtimeConfiguration"] = runtime_snapshot
        prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        key_material = "|".join(
            (
                str(project_id),
                str(shot_id or scene_id or ""),
                operation_key,
                str(attempt),
                input_hash,
                "" if runtime_snapshot is None else str(runtime_snapshot["revision"]),
            )
        )
        idempotency_key = hashlib.sha256(key_material.encode("utf-8")).hexdigest()
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            created_id = session.execute(
                insert(WorkflowStep)
                .values(
                    id=uuid.uuid4(),
                    production_run_id=project_id,
                    scene_id=scene_id,
                    shot_card_id=shot_id,
                    kind=kind.value,
                    status=StepStatus.PENDING.value,
                    attempt=attempt,
                    operation_key=operation_key,
                    idempotency_key=idempotency_key,
                    provider=provider,
                    model=model,
                    input_hash=input_hash,
                    input_snapshot_json=persisted_snapshot,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(WorkflowStep.id)
            ).scalar_one_or_none()
            step_row = session.scalar(
                select(WorkflowStep).where(
                    WorkflowStep.id
                    == (
                        created_id
                        or session.scalar(
                            select(WorkflowStep.id).where(
                                WorkflowStep.idempotency_key == idempotency_key
                            )
                        )
                    )
                )
            )
            assert step_row is not None
            prompt_row = session.scalar(
                select(PromptRecord).where(
                    PromptRecord.step_id == step_row.id,
                    PromptRecord.sha256 == prompt_hash,
                )
            )
            if prompt_row is None:
                prompt_row = PromptRecord(
                    step_id=step_row.id,
                    purpose=purpose.value,
                    model=model,
                    prompt_text=prompt_text,
                    sha256=prompt_hash,
                )
                session.add(prompt_row)
                session.flush()
            return _step(step_row), _prompt(prompt_row)

    def update_step(
        self,
        step_id: uuid.UUID,
        *,
        status: StepStatus,
        task_id: str | None = None,
        error: dict[str, Any] | None = None,
        input_snapshot: dict[str, Any] | None = None,
    ) -> StoredStep:
        with self._sessions.begin() as session:
            row = _required(session, WorkflowStep, step_id)
            transition_step(StepStatus(row.status), status)
            row.status = status.value
            if task_id is not None:
                bound = session.scalar(
                    select(WorkflowStep.id).where(
                        WorkflowStep.provider_task_id == task_id,
                        WorkflowStep.id != step_id,
                    )
                )
                if bound is not None:
                    raise ValueError("provider task is already bound to another step")
                row.provider_task_id = task_id
            if error is not None:
                row.error_json = error
            if input_snapshot is not None:
                row.input_snapshot_json = input_snapshot
            now = datetime.now(timezone.utc)
            if status in {StepStatus.SUBMITTING, StepStatus.QUEUED, StepStatus.RUNNING}:
                row.submitted_at = row.submitted_at or now
            if status in {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED}:
                row.completed_at = now
            return _step(row)

    def get_step(self, step_id: uuid.UUID) -> StoredStep:
        with self._sessions() as session:
            return _step(_required(session, WorkflowStep, step_id))

    def list_steps(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
    ) -> tuple[StoredStep, ...]:
        with self._sessions() as session:
            query = select(WorkflowStep).where(WorkflowStep.production_run_id == project_id)
            if scene_id is not None:
                query = query.where(WorkflowStep.scene_id == scene_id)
            if shot_id is not None:
                query = query.where(WorkflowStep.shot_card_id == shot_id)
            rows = session.execute(query.order_by(WorkflowStep.created_at)).scalars()
            return tuple(_step(row) for row in rows)

    def task_center_steps(self, *, limit: int = 300) -> tuple[StoredStep, ...]:
        """Return recent durable work in one query for the global task center."""

        with self._sessions() as session:
            rows = session.execute(
                select(WorkflowStep)
                .order_by(WorkflowStep.created_at.desc())
                .limit(limit)
            ).scalars()
            return tuple(_step(row) for row in rows)

    def task_center_events(
        self,
        *,
        after_sequence: int,
        limit: int = 200,
    ) -> tuple[dict[str, Any], ...]:
        if after_sequence < 0:
            raise ValueError("after_sequence cannot be negative")
        if limit < 1 or limit > 200:
            raise ValueError("event limit must be between 1 and 200")
        with self._sessions() as session:
            rows = list(
                session.scalars(
                    select(CanvasEvent)
                    .where(CanvasEvent.sequence > after_sequence)
                    .order_by(CanvasEvent.sequence)
                    .limit(limit)
                )
            )
            return tuple(
                {
                    "id": str(row.id),
                    "sequence": row.sequence,
                    "projectId": str(row.production_run_id),
                    "type": row.event_type,
                    "data": {
                        "projectId": str(row.production_run_id),
                        **dict(row.data_json),
                    },
                    "createdAt": row.created_at.isoformat(),
                }
                for row in rows
            )

    def get_prompt(self, step_id: uuid.UUID) -> StoredPrompt | None:
        with self._sessions() as session:
            row = session.scalar(
                select(PromptRecord)
                .where(PromptRecord.step_id == step_id)
                .order_by(PromptRecord.created_at)
                .limit(1)
            )
            return None if row is None else _prompt(row)

    def add_asset(
        self,
        *,
        landed: LandedAsset,
        role: str,
        media_type: str,
        scope: str,
        status: str,
        project_id: uuid.UUID | None,
        scene_id: uuid.UUID | None,
        shot_id: uuid.UUID | None,
        step_id: uuid.UUID | None,
        semantic_key: str | None,
        metadata: dict[str, Any],
    ) -> StoredAsset:
        with self._sessions.begin() as session:
            if project_id is not None:
                self._require_project(session, project_id)
            row = Asset(
                production_run_id=project_id,
                scene_id=scene_id,
                shot_card_id=shot_id,
                producing_step_id=step_id,
                role=role,
                semantic_key=semantic_key,
                scope=scope,
                status=status,
                media_type=media_type,
                storage_key=_storage_key_for(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json=metadata,
            )
            session.add(row)
            session.flush()
            return _asset(row, self._asset_root)

    def get_asset(self, asset_id: uuid.UUID) -> StoredAsset:
        with self._sessions() as session:
            return _asset(_required(session, Asset, asset_id), self._asset_root)

    def repair_canon_assets(
        self,
        repairs: tuple[tuple[uuid.UUID, LandedAsset], ...],
    ) -> tuple[StoredAsset, ...]:
        with self._sessions.begin() as session:
            rows: list[tuple[Asset, LandedAsset]] = []
            seen: set[uuid.UUID] = set()
            for asset_id, landed in repairs:
                if asset_id in seen:
                    raise ValueError("Canon repair contains a duplicate asset ID")
                seen.add(asset_id)
                row = _required(session, Asset, asset_id)
                if row.scope != "canon" or row.status != "approved":
                    raise ValueError("only approved Canon assets can be repaired")
                if row.sha256 != landed.sha256:
                    raise ValueError("Canon repair cannot change the approved asset hash")
                rows.append((row, landed))
            for row, landed in rows:
                row.storage_key = _storage_key_for(landed.path, self._asset_root)
                row.byte_size = landed.byte_size
            return tuple(_asset(row, self._asset_root) for row, _landed in rows)

    def install_canon_asset(
        self,
        *,
        landed: LandedAsset,
        semantic_key: str,
        role: str,
        display_name: str,
        group: str | None,
        recommended_default: bool,
    ) -> StoredAsset:
        """Create one global immutable Canon asset, idempotently by semantic key and hash."""

        with self._sessions.begin() as session:
            rows = list(
                session.scalars(
                    select(Asset).where(
                        Asset.scope == "canon",
                        Asset.semantic_key == semantic_key,
                    )
                )
            )
            if len(rows) > 1:
                raise ValueError(f"duplicate Canon semantic key: {semantic_key}")
            if rows:
                row = rows[0]
                if row.status != "approved" or row.sha256 != landed.sha256:
                    raise ValueError(
                        f"Canon semantic key already exists with different content: {semantic_key}"
                    )
                row.storage_key = _storage_key_for(landed.path, self._asset_root)
                row.byte_size = landed.byte_size
                return _asset(row, self._asset_root)

            row = Asset(
                production_run_id=None,
                scene_id=None,
                shot_card_id=None,
                producing_step_id=None,
                role=role,
                semantic_key=semantic_key,
                scope="canon",
                status="approved",
                media_type="image",
                storage_key=_storage_key_for(landed.path, self._asset_root),
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                metadata_json={
                    "displayName": display_name,
                    "group": group,
                    "recommendedDefault": recommended_default,
                    "providerEligible": not semantic_key.startswith("style_source:"),
                },
            )
            session.add(row)
            session.flush()
            return _asset(row, self._asset_root)

    def list_assets(
        self,
        *,
        project_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        include_canon: bool = False,
    ) -> tuple[StoredAsset, ...]:
        with self._sessions() as session:
            query = select(Asset)
            filters = []
            if project_id is not None:
                filters.append(Asset.production_run_id == project_id)
            if shot_id is not None:
                filters.append(Asset.shot_card_id == shot_id)
            if include_canon:
                if filters:
                    query = query.where(or_(and_(*filters), Asset.scope == "canon"))
                else:
                    query = query.where(Asset.scope == "canon")
            elif filters:
                query = query.where(*filters)
            else:
                query = query.where(Asset.scope == "canon")
            rows = session.execute(query.order_by(Asset.created_at)).scalars()
            return tuple(_asset(row, self._asset_root) for row in rows)

    def select_shot_asset(
        self, shot_id: uuid.UUID, *, kind: str, asset_id: uuid.UUID
    ) -> StoredShot:
        if kind not in {"anchor", "video"}:
            raise ValueError("selected shot asset kind must be anchor or video")
        with self._sessions.begin() as session:
            shot = _required(session, ShotCard, shot_id)
            scene = _required(session, Scene, shot.scene_id)
            asset = _required(session, Asset, asset_id)
            if asset.shot_card_id != shot_id or asset.status not in {"approved", "ready"}:
                raise ValueError("selected asset must be approved and belong to the shot")
            if kind == "anchor":
                if asset.media_type != "image":
                    raise ValueError("anchor must be an image")
                shot.selected_anchor_asset_id = asset_id
                shot.selected_video_asset_id = None
                shot.status = ShotStatus.VIDEO_PENDING.value
                self._invalidate_project_sequence(session, scene.production_run_id)
            else:
                if asset.media_type != "video":
                    raise ValueError("shot version must be a video")
                shot.selected_video_asset_id = asset_id
                shot.status = ShotStatus.APPROVED.value
                self._invalidate_project_sequence(session, scene.production_run_id)
            return _shot(shot, scene.production_run_id)

    def add_review(
        self,
        *,
        step_id: uuid.UUID,
        asset_id: uuid.UUID | None,
        source: str,
        decision: str,
        reason: str | None,
        warnings: tuple[dict[str, Any], ...],
        evidence: dict[str, Any],
    ) -> StoredReview:
        with self._sessions.begin() as session:
            _required(session, WorkflowStep, step_id)
            row = Review(
                step_id=step_id,
                asset_id=asset_id,
                source=source,
                decision=decision,
                reason=reason,
                warnings_json=list(warnings),
                evidence_json=evidence,
            )
            session.add(row)
            session.flush()
            return _review(row)

    def decide_asset(
        self,
        asset_id: uuid.UUID,
        *,
        decision: str,
        reason: str | None,
    ) -> StoredAsset:
        if decision not in {"approved", "rejected"}:
            raise ValueError("asset decision must be approved or rejected")
        with self._sessions.begin() as session:
            asset = _required(session, Asset, asset_id)
            if asset.producing_step_id is None:
                raise ValueError("imported references are selected through reference bindings")
            step = _required(session, WorkflowStep, asset.producing_step_id)
            if StepStatus(step.status) is not StepStatus.AWAITING_REVIEW:
                if asset.status == decision:
                    return _asset(asset, self._asset_root)
                raise ValueError("asset is not awaiting review")
            asset.status = decision
            step.status = (
                StepStatus.SUCCEEDED.value if decision == "approved" else StepStatus.FAILED.value
            )
            step.completed_at = datetime.now(timezone.utc)
            session.add(
                Review(
                    step_id=step.id,
                    asset_id=asset.id,
                    source="human",
                    decision=decision,
                    reason=reason,
                    warnings_json=[],
                    evidence_json={},
                )
            )
            return _asset(asset, self._asset_root)

    def list_reviews(self, step_id: uuid.UUID) -> tuple[StoredReview, ...]:
        with self._sessions() as session:
            rows = session.execute(
                select(Review).where(Review.step_id == step_id).order_by(Review.created_at)
            ).scalars()
            return tuple(_review(row) for row in rows)

    def create_sequence(
        self,
        *,
        project_id: uuid.UUID,
        plan: ProjectSequencePlan,
        parent_sequence_id: uuid.UUID | None,
        rendered_asset_id: uuid.UUID | None,
        status: SequenceStatus,
    ) -> StoredSequence:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            revision = (
                session.scalar(
                    select(func.coalesce(func.max(VideoSequence.revision), 0)).where(
                        VideoSequence.production_run_id == project_id
                    )
                )
                + 1
            )
            row = VideoSequence(
                production_run_id=project_id,
                revision=revision,
                parent_sequence_id=parent_sequence_id,
                rendered_asset_id=rendered_asset_id,
                status=status.value,
                duration_ms=plan.duration_ms,
                audio_policy="native_fades",
                clips_json=[
                    clip.model_dump(mode="json", by_alias=True) for clip in plan.clips
                ],
            )
            session.add(row)
            session.flush()
            return _sequence(row)

    def list_sequences(self, project_id: uuid.UUID) -> tuple[StoredSequence, ...]:
        with self._sessions() as session:
            rows = session.execute(
                select(VideoSequence)
                .where(VideoSequence.production_run_id == project_id)
                .order_by(VideoSequence.revision)
            ).scalars()
            return tuple(_sequence(row) for row in rows)

    def select_sequence(self, project_id: uuid.UUID, sequence_id: uuid.UUID) -> StoredSequence:
        with self._sessions.begin() as session:
            project = self._require_project(session, project_id)
            row = _required(session, VideoSequence, sequence_id)
            if row.production_run_id != project_id or row.status != SequenceStatus.APPROVED.value:
                raise ValueError("only an approved sequence from this project can be selected")
            project.selected_sequence_id = sequence_id
            return _sequence(row)

    def decide_sequence(self, sequence_id: uuid.UUID, *, approved: bool) -> StoredSequence:
        with self._sessions.begin() as session:
            row = _required(session, VideoSequence, sequence_id)
            if row.status != SequenceStatus.CONTENT_REVIEW.value:
                target = SequenceStatus.APPROVED if approved else SequenceStatus.REJECTED
                if row.status == target.value:
                    return _sequence(row)
                raise ValueError("sequence is not awaiting content review")
            row.status = (
                SequenceStatus.APPROVED.value if approved else SequenceStatus.REJECTED.value
            )
            if row.rendered_asset_id is not None:
                asset = _required(session, Asset, row.rendered_asset_id)
                asset.status = "approved" if approved else "rejected"
            return _sequence(row)

    def project_graph(self, project_id: uuid.UUID) -> dict[str, Any]:
        return project_graph_projection(self.project_read_model(project_id))

    def shot_trace(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self.get_shot(shot_id)
        model = self.project_read_model(shot.project_id)
        current = next(item for item in model.shots if item.id == shot_id)
        prompts = {item.step_id: item for item in model.prompts}
        reviews_by_step: dict[uuid.UUID, list[StoredReview]] = {}
        for review in model.reviews:
            reviews_by_step.setdefault(review.step_id, []).append(review)
        steps = [item for item in model.steps if item.shot_card_id == shot_id]
        return {
            **_json_shot(current),
            "assets": [
                _json_asset(item) for item in model.assets if item.shot_card_id == shot_id
            ],
            "attempts": [
                self._step_trace_loaded(
                    step,
                    prompts.get(step.id),
                    tuple(reviews_by_step.get(step.id, [])),
                )
                for step in steps
            ],
        }

    def _step_trace(self, step: StoredStep) -> dict[str, Any]:
        prompt = self.get_prompt(step.id)
        return self._step_trace_loaded(step, prompt, self.list_reviews(step.id))

    @staticmethod
    def _step_trace_loaded(
        step: StoredStep,
        prompt: StoredPrompt | None,
        reviews: tuple[StoredReview, ...],
    ) -> dict[str, Any]:
        return {
            **_json_step(step),
            "prompt": (
                None
                if prompt is None
                else {
                    "id": str(prompt.id),
                    "purpose": prompt.purpose.value,
                    "model": prompt.model,
                    "text": prompt.text,
                    "sha256": prompt.sha256,
                }
            ),
            "reviews": [
                {
                    "id": str(review.id),
                    "source": review.source,
                    "decision": review.decision,
                    "reason": review.reason,
                    "warnings": list(review.warnings),
                    "evidence": review.evidence,
                }
                for review in reviews
            ],
        }

    def _require_project(self, session: Session, project_id: uuid.UUID) -> ProductionRun:
        row = _required(session, ProductionRun, project_id)
        if row.contract_version != CURRENT_CONTRACT_VERSION:
            raise ContractVersionMismatchError(
                f"project contract {row.contract_version} is not V{CURRENT_CONTRACT_VERSION}"
            )
        return row

    def _create_visual_profile_revision(
        self,
        session: Session,
        *,
        project: ProductionRun,
        draft: VisualProfileDraft,
        reference_snapshot: list[dict[str, Any]] | None = None,
    ) -> VisualProfileRevision:
        snapshot = (
            _reference_snapshot(session, draft.reference_bindings)
            if reference_snapshot is None
            else reference_snapshot
        )
        revision = (
            session.scalar(
                select(func.coalesce(func.max(VisualProfileRevision.revision), 0)).where(
                    VisualProfileRevision.production_run_id == project.id
                )
            )
            + 1
        )
        row = VisualProfileRevision(
            production_run_id=project.id,
            revision=revision,
            profile_hash=_profile_hash(draft, reference_snapshot=snapshot),
            source_profile_id="canon-v1-short-hair-gray-cat-sample-style",
            person_identity=draft.person_identity,
            person_hair=draft.person_hair,
            person_body=draft.person_body,
            cat_identity=draft.cat_identity,
            style_positive_json=list(draft.style_positive),
            style_negative_json=list(draft.style_negative),
            reference_bindings_json=[
                item.model_dump(mode="json", by_alias=True)
                for item in draft.reference_bindings
            ],
            reference_snapshot_json=snapshot,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _compact_scene_order(session: Session, project_id: uuid.UUID) -> None:
        rows = list(
            session.execute(
                select(Scene)
                .where(
                    Scene.production_run_id == project_id,
                    Scene.active.is_(True),
                )
                .order_by(Scene.sort_order)
            ).scalars()
        )
        for order, row in enumerate(rows, 1):
            row.sort_order = order

    @staticmethod
    def _compact_shot_order(session: Session, scene_id: uuid.UUID) -> None:
        rows = list(
            session.execute(
                select(ShotCard).where(ShotCard.scene_id == scene_id).order_by(ShotCard.sort_order)
            ).scalars()
        )
        for order, row in enumerate(rows, 1):
            row.sort_order = order

    def _replace_shots_locked(
        self,
        session: Session,
        *,
        scene: Scene,
        drafts: tuple[ShotCardDraft, ...],
    ) -> list[ShotCard]:
        existing_ids = select(ShotCard.id).where(ShotCard.scene_id == scene.id)
        if session.scalar(
            select(func.count())
            .select_from(WorkflowStep)
            .where(WorkflowStep.shot_card_id.in_(existing_ids))
        ):
            raise WorkflowConflictError(
                "已有图片或视频生成历史，不能整批覆盖视频片段"
            )
        session.execute(delete(ShotCard).where(ShotCard.scene_id == scene.id))
        rows = [
            _new_shot(scene.id, order, draft)
            for order, draft in enumerate(drafts, 1)
        ]
        session.add_all(rows)
        self._invalidate_project_sequence(session, scene.production_run_id)
        scene.status = SceneStatus.READY.value
        session.flush()
        return rows

    @staticmethod
    def _invalidate_project_sequence(session: Session, project_id: uuid.UUID) -> None:
        project = _required(session, ProductionRun, project_id)
        project.selected_sequence_id = None

    @staticmethod
    def _invalidate_scene_outputs(session: Session, scene: Scene) -> None:
        storyboard = session.scalar(
            select(StoryboardRevision)
            .where(
                StoryboardRevision.production_run_id == scene.production_run_id,
                StoryboardRevision.status.in_(
                    ("draft", "structure_approved", "production_approved")
                ),
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
                    GenerationPlan.status.in_(("proposed", "approved")),
                )
                .order_by(GenerationPlan.revision.desc())
                .limit(1)
            )
        )
        if storyboard is not None and plan is None:
            shots = ()
        else:
            shot_query = select(ShotCard).where(ShotCard.scene_id == scene.id)
            if plan is not None:
                shot_query = shot_query.where(ShotCard.generation_plan_id == plan.id)
            shots = session.execute(shot_query).scalars()
        current_shots = list(shots)
        current_shot_ids = [shot.id for shot in current_shots]
        for shot in current_shots:
            shot.prompt_id = None
            shot.selected_anchor_asset_id = None
            shot.selected_video_asset_id = None
            shot.status = ShotStatus.READY.value
        if current_shot_ids:
            session.execute(
                update(Asset)
                .where(
                    Asset.shot_card_id.in_(current_shot_ids),
                    Asset.role.in_(("shot_anchor", "shot_video", "shot_video_edit")),
                    Asset.status != "stale",
                )
                .values(status="stale")
            )
        if storyboard is not None and storyboard.status == "production_approved":
            storyboard.status = "structure_approved"
            storyboard.production_package_hash = None
            storyboard.production_approved_at = None
        project = _required(session, ProductionRun, scene.production_run_id)
        project.selected_sequence_id = None

    def _validate_reference_bindings(
        self,
        session: Session,
        *,
        project_id: uuid.UUID,
        bindings: list[ReferenceBinding],
    ) -> None:
        for binding in bindings:
            asset = _required(session, Asset, binding.asset_id)
            if asset.scope != "canon" and asset.production_run_id != project_id:
                raise ValueError("a reference must be Canon or belong to the current project")
            if asset.media_type != "image" or asset.status not in {"ready", "approved"}:
                raise ValueError("a reference must be an available image")
            if not _asset(asset, self._asset_root).content_ready:
                raise ValueError("a reference file is unavailable; repair or upload it first")
            if binding.usage.value == "approved_anchor" and asset.status != "approved":
                raise ValueError("an approved_anchor binding requires an approved image")
            expected_role = _expected_reference_role(asset)
            if expected_role is not None and binding.role is not expected_role:
                raise ValueError(
                    f"asset {asset.id} has fixed reference role {expected_role.value}"
                )
            if (
                asset.role == "shot_tail_frame"
                and binding.usage is not ReferenceUsage.APPROVED_ANCHOR
            ):
                raise ValueError("a shot tail frame can only be used as an approved anchor")

    def _normalize_look_reference_bindings(
        self,
        session: Session,
        *,
        project_id: uuid.UUID,
        bindings: list[LookReferenceBinding],
        profile_only: bool,
    ) -> list[LookReferenceBinding]:
        allowed = {
            LookReferencePurpose.PERSON_IDENTITY,
            LookReferencePurpose.PERSON_BODY,
            LookReferencePurpose.CAT_IDENTITY,
            LookReferencePurpose.STYLE,
        }
        result: list[LookReferenceBinding] = []
        hashes: set[str] = set()
        for binding in bindings:
            if profile_only and binding.purpose not in allowed:
                raise ValueError("项目视觉档案只允许人物、猫咪和画风参考")
            asset = _required(session, Asset, binding.asset_id)
            if asset.scope != "canon" and asset.production_run_id != project_id:
                raise ValueError("定妆参考必须是 Canon 或属于当前项目")
            stored = _asset(asset, self._asset_root)
            if (
                asset.media_type != "image"
                or asset.status not in {"ready", "approved"}
                or not stored.content_ready
            ):
                raise ValueError("定妆参考图片不可用；请先修复或重新上传")
            if asset.sha256 in hashes:
                continue
            expected_purpose = (
                asset.metadata_json.get("referencePurpose")
                if asset.role in {"generated_reference", "external_reference"}
                else None
            )
            if expected_purpose is not None and binding.purpose.value != expected_purpose:
                raise ValueError(
                    f"asset {asset.id} has fixed look purpose {expected_purpose}"
                )
            hashes.add(asset.sha256)
            result.append(binding)
        return result


def _required(session: Session, model: type[Any], record_id: uuid.UUID) -> Any:
    row = session.get(model, record_id)
    if row is None:
        raise RecordNotFoundError(f"{model.__name__} {record_id} was not found")
    return row


def _storage_key_for(path: Path, asset_root: Path) -> str:
    root = asset_root.expanduser().resolve()
    resolved = path.expanduser().resolve()
    try:
        key = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("managed asset path is outside the configured asset root") from exc
    _resolve_storage_key(key, root)
    return key


def _resolve_storage_key(storage_key: str, asset_root: Path) -> Path:
    key = storage_key.strip()
    pure = PurePosixPath(key)
    if (
        not key
        or key.startswith(("/", "\\"))
        or "\\" in key
        or pure.is_absolute()
        or ".." in pure.parts
        or (pure.parts and pure.parts[0].endswith(":"))
        or len(pure.parts) < 3
        or pure.parts[0] not in {"imported", "generated"}
        or pure.parts[1] != "sha256"
    ):
        raise ValueError(f"invalid managed asset storage key: {storage_key!r}")
    root = asset_root.expanduser().resolve()
    resolved = root.joinpath(*pure.parts).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"managed asset storage key escapes asset root: {storage_key!r}")
    return resolved


def _default_visual_profile(session: Session) -> VisualProfileDraft:
    ordered = (
        ("person:headshot", LookReferencePurpose.PERSON_IDENTITY, "锁定人物脸型、五官和短发"),
        (
            "person:fullbody",
            LookReferencePurpose.PERSON_BODY,
            "锁定人物年龄感和头身比例，忽略旧服装",
        ),
        ("cat:front", LookReferencePurpose.CAT_IDENTITY, "锁定猫咪脸部、眼睛和正面毛色分区"),
        ("cat:side", LookReferencePurpose.CAT_IDENTITY, "锁定猫咪体型、侧面虎斑和环纹尾巴"),
        ("style:line_texture", LookReferencePurpose.STYLE, "只参考线条、水彩材质和细节表现"),
        ("style:outdoor", LookReferencePurpose.STYLE, "只参考户外色彩、自然光和空气透视"),
        ("style:indoor", LookReferencePurpose.STYLE, "只参考室内材质、窗光和低对比色彩"),
    )
    keys = [item[0] for item in ordered]
    rows = session.execute(
        select(Asset).where(
            Asset.scope == "canon",
            Asset.status == "approved",
            Asset.semantic_key.in_(keys),
        )
    ).scalars()
    by_key = {row.semantic_key: row for row in rows}
    bindings = [
        LookReferenceBinding(assetId=by_key[key].id, purpose=purpose, instruction=instruction)
        for key, purpose, instruction in ordered
        if key in by_key
    ]
    return VisualProfileDraft(referenceBindings=bindings)


def _expected_reference_role(asset: Asset) -> ReferenceRole | None:
    if asset.role == "scene_look":
        return ReferenceRole.SCENE
    if asset.role in {"generated_reference", "external_reference"}:
        value = asset.metadata_json.get("referenceRole")
        try:
            return ReferenceRole(value) if isinstance(value, str) else None
        except ValueError:
            return None
    semantic_key = asset.semantic_key or ""
    if asset.scope == "canon" and semantic_key.startswith(("person:", "cat:")):
        return ReferenceRole.IDENTITY
    if asset.scope == "canon" and semantic_key.startswith("style:"):
        return ReferenceRole.STYLE
    return None


def _reference_snapshot(
    session: Session,
    bindings: list[LookReferenceBinding],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in bindings:
        asset = _required(session, Asset, item.asset_id)
        result.append(
            {
                **item.model_dump(mode="json", by_alias=True),
                "semanticKey": asset.semantic_key,
                "sha256": asset.sha256,
            }
        )
    return result


def _profile_hash(
    draft: VisualProfileDraft,
    *,
    reference_snapshot: list[dict[str, Any]] | None = None,
) -> str:
    payload = {
        **draft.model_dump(mode="json", by_alias=True),
        "referenceSnapshot": reference_snapshot or [],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _project_reference_bindings(draft: VisualProfileDraft) -> list[dict[str, Any]]:
    return [
        ReferenceBinding(
            assetId=item.asset_id,
            usage=ReferenceUsage.GENERATION_REFERENCE,
            role=(
                ReferenceRole.STYLE
                if item.purpose is LookReferencePurpose.STYLE
                else ReferenceRole.IDENTITY
            ),
            applyTo=ReferenceTarget.BOTH,
        ).model_dump(mode="json", by_alias=True)
        for item in draft.reference_bindings
    ]


def _environment_profile_bindings(
    session: Session,
    raw_bindings: list[dict[str, Any]],
    look_plan: SceneLookPlan,
) -> list[LookReferenceBinding]:
    expected_style_key = f"style:{look_plan.environment_style.value}"
    result: list[LookReferenceBinding] = []
    for raw in raw_bindings:
        binding = LookReferenceBinding.model_validate(raw)
        if binding.purpose is LookReferencePurpose.STYLE:
            asset = _required(session, Asset, binding.asset_id)
            if (
                asset.semantic_key in {"style:outdoor", "style:indoor"}
                and asset.semantic_key != expected_style_key
            ):
                continue
        result.append(binding)
    return result


def _project(row: ProductionRun) -> StoredProject:
    return StoredProject(
        id=row.id,
        title=row.title,
        content_date=row.content_date,
        status=RunStatus(row.status),
        selected_sequence_id=row.selected_sequence_id,
        default_reference_bindings=tuple(
            ReferenceBinding.model_validate(item)
            for item in row.default_reference_bindings_json
        ),
        visual_profile_revision_id=row.current_visual_profile_revision_id,
    )


def _visual_profile(row: VisualProfileRevision) -> StoredVisualProfileRevision:
    return StoredVisualProfileRevision(
        id=row.id,
        project_id=row.production_run_id,
        revision=row.revision,
        profile_hash=row.profile_hash,
        source_profile_id=row.source_profile_id,
        draft=VisualProfileDraft(
            personIdentity=row.person_identity,
            personHair=row.person_hair,
            personBody=row.person_body,
            catIdentity=row.cat_identity,
            stylePositive=row.style_positive_json,
            styleNegative=row.style_negative_json,
            referenceBindings=row.reference_bindings_json,
        ),
        reference_snapshot=tuple(row.reference_snapshot_json),
        created_at=row.created_at,
    )


def _project_checked(row: ProductionRun) -> StoredProject:
    if row.contract_version != CURRENT_CONTRACT_VERSION:
        raise ContractVersionMismatchError(
            f"project {row.id} uses unsupported contract V{row.contract_version}"
        )
    return _project(row)


def _scene(row: Scene) -> StoredScene:
    return StoredScene(
        id=row.id,
        project_id=row.production_run_id,
        order=row.sort_order,
        draft=SceneDraft(
            title=row.title,
            sourceText=row.source_text,
            chapterLabel=row.chapter_label,
            contextNote=row.context_note,
            storyMode=row.story_mode,
            targetShotCount=row.target_shot_count,
            lookPlan=(
                None
                if row.look_plan_json is None
                else SceneLookPlan.model_validate(row.look_plan_json)
            ),
        ),
        status=SceneStatus(row.status),
        selected_look_asset_id=row.selected_look_asset_id,
        look_draft=(
            None
            if not row.look_draft_json
            else SceneLookDraft.model_validate(row.look_draft_json)
        ),
        look_draft_revision=row.look_draft_revision,
    )


def _new_shot(scene_id: uuid.UUID, order: int, draft: ShotCardDraft) -> ShotCard:
    row = ShotCard(
        scene_id=scene_id,
        sort_order=order,
        status=ShotStatus.READY.value,
    )
    _write_shot_draft(row, draft)
    return row


def _write_shot_draft(row: ShotCard, draft: ShotCardDraft) -> None:
    row.title = draft.title
    row.direction = draft.direction
    row.duration_seconds = draft.duration_seconds
    row.anchor_mode = draft.anchor_mode.value
    row.reference_bindings_json = [
        item.model_dump(mode="json", by_alias=True) for item in draft.reference_bindings
    ]
    row.inherit_project_references = draft.inherit_project_references
    row.use_scene_look = draft.use_scene_look
    row.scene_look_usage = draft.scene_look_usage.value


def _shot(row: ShotCard, project_id: uuid.UUID) -> StoredShot:
    return StoredShot(
        id=row.id,
        scene_id=row.scene_id,
        project_id=project_id,
        order=row.sort_order,
        draft=ShotCardDraft(
            title=row.title,
            direction=row.direction,
            durationSeconds=row.duration_seconds,
            anchorMode=AnchorMode(row.anchor_mode),
            referenceBindings=[
                ReferenceBinding.model_validate(item) for item in row.reference_bindings_json
            ],
            inheritProjectReferences=row.inherit_project_references,
            sceneLookUsage=row.scene_look_usage,
        ),
        status=ShotStatus(row.status),
        draft_revision=row.draft_revision,
        selected_anchor_asset_id=row.selected_anchor_asset_id,
        selected_video_asset_id=row.selected_video_asset_id,
    )


def _step(row: WorkflowStep) -> StoredStep:
    return StoredStep(
        id=row.id,
        project_id=row.production_run_id,
        scene_id=row.scene_id,
        shot_card_id=row.shot_card_id,
        kind=StepKind(row.kind),
        status=StepStatus(row.status),
        attempt=row.attempt,
        operation_key=row.operation_key,
        input_snapshot=dict(row.input_snapshot_json),
        provider=row.provider,
        provider_task_id=row.provider_task_id,
        model=row.model,
        error=None if row.error_json is None else dict(row.error_json),
        progress=dict(row.progress_json or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def _prompt(row: PromptRecord) -> StoredPrompt:
    return StoredPrompt(
        id=row.id,
        step_id=row.step_id,
        purpose=PromptPurpose(row.purpose),
        model=row.model,
        text=row.prompt_text,
        sha256=row.sha256,
    )


def _asset(row: Asset, asset_root: Path) -> StoredAsset:
    path = (
        None
        if row.storage_key.startswith("legacy:")
        else _resolve_storage_key(row.storage_key, asset_root)
    )
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
        path=path,
        sha256=row.sha256,
        metadata=dict(row.metadata_json),
        semantic_key=row.semantic_key,
        created_at=getattr(row, "created_at", None),
    )


def _review(row: Review) -> StoredReview:
    return StoredReview(
        id=row.id,
        step_id=row.step_id,
        asset_id=row.asset_id,
        source=row.source,
        decision=row.decision,
        reason=row.reason,
        warnings=tuple(row.warnings_json),
        evidence=dict(row.evidence_json),
    )


def _sequence(row: VideoSequence) -> StoredSequence:
    return StoredSequence(
        id=row.id,
        project_id=row.production_run_id,
        revision=row.revision,
        parent_sequence_id=row.parent_sequence_id,
        rendered_asset_id=row.rendered_asset_id,
        status=SequenceStatus(row.status),
        plan=ProjectSequencePlan(duration_ms=row.duration_ms, clips=row.clips_json),
        created_at=row.created_at,
    )


def _json_shot(row: StoredShot) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "sceneId": str(row.scene_id),
        "order": row.order,
        **row.draft.model_dump(mode="json", by_alias=True),
        "draftRevision": row.draft_revision,
        "useSceneLook": row.draft.use_scene_look,
        "status": row.status.value,
        "selectedAnchorAssetId": None
        if row.selected_anchor_asset_id is None
        else str(row.selected_anchor_asset_id),
        "selectedVideoAssetId": None
        if row.selected_video_asset_id is None
        else str(row.selected_video_asset_id),
    }
def _json_step(row: StoredStep) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "kind": row.kind.value,
        "status": row.status.value,
        "attempt": row.attempt,
        "operationKey": row.operation_key,
        "providerTaskId": row.provider_task_id,
        "provider": row.provider,
        "model": row.model,
        "inputSnapshot": row.input_snapshot,
        "error": row.error,
        "createdAt": None if row.created_at is None else row.created_at.isoformat(),
    }
def _json_asset(row: StoredAsset) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "role": row.role,
        "mediaType": row.media_type,
        "scope": row.scope,
        "status": row.status,
        "projectId": None if row.project_id is None else str(row.project_id),
        "sceneId": None if row.scene_id is None else str(row.scene_id),
        "shotId": None if row.shot_card_id is None else str(row.shot_card_id),
        "producingStepId": None if row.step_id is None else str(row.step_id),
        "sha256": row.sha256,
        "semanticKey": row.semantic_key,
        "metadata": row.metadata,
        "contentReady": row.content_ready,
        "displayName": row.display_name,
        "referencePurpose": row.reference_purpose,
        "visualProfileRevisionId": row.metadata.get("visualProfileRevisionId"),
        "lookDraftRevision": row.metadata.get("lookDraftRevision"),
        "createdAt": None if row.created_at is None else row.created_at.isoformat(),
    }
