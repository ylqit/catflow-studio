"""Offline V5 creation-flow data-chain smoke test.

The script uses in-memory workflow state, deterministic provider substitutes
and local FFmpeg fixtures.  It never reads ARK_API_KEY and never sends a
network request.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from cat_video_generator.application.ports import (
    DirectorResult,
    GatewayError,
    ImageResult,
    LandedAsset,
    StoredAsset,
    StoredProject,
    StoredPrompt,
    StoredReview,
    StoredScene,
    StoredSequence,
    StoredShot,
    StoredStep,
    StoredVisualProfileRevision,
    VideoDiagnosticResult,
    VideoTaskResult,
)
from cat_video_generator.application.shot_queue import (
    ProjectEditingService,
    RevisionConflictError,
    SequenceService,
    ShotProductionService,
)
from cat_video_generator.bootstrap import build_runtime_container
from cat_video_generator.config import load_local_env
from cat_video_generator.domain.contracts import (
    AnchorMode,
    LookReferenceBinding,
    ReferenceBinding,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
    SceneDraft,
    SceneLookDraft,
    SceneLookPlan,
    SceneLookUsage,
    ShotAssistPatch,
    ShotCardDraft,
    StoryMode,
    StoryProjectInput,
    VisualProfileDraft,
)
from cat_video_generator.domain.creative_workflow import shot_snapshot_hash
from cat_video_generator.domain.rendering import (
    ProjectSequencePlan,
    RenderOperation,
    SequenceClip,
    SequenceStatus,
    VideoInputPlan,
)
from cat_video_generator.domain.shot_assistance import apply_shot_assist_patch
from cat_video_generator.domain.workflow import (
    PromptPurpose,
    RunStatus,
    SceneStatus,
    ShotStatus,
    StepKind,
    StepStatus,
    transition_step,
)
from cat_video_generator.infrastructure.media.qc import (
    FfmpegFrameExtractor,
    FfprobeMediaProbe,
)
from cat_video_generator.infrastructure.media.storage import LocalAssetStore
from cat_video_generator.interfaces.api import create_app
from cat_video_generator.interfaces.jobs import JobRegistry


class MemoryStore:
    def __init__(self) -> None:
        self.projects: dict[uuid.UUID, StoredProject] = {}
        self.scenes: dict[uuid.UUID, StoredScene] = {}
        self.shots: dict[uuid.UUID, StoredShot] = {}
        self.steps: dict[uuid.UUID, StoredStep] = {}
        self.prompts: dict[uuid.UUID, StoredPrompt] = {}
        self.assets: dict[uuid.UUID, StoredAsset] = {}
        self.reviews: dict[uuid.UUID, StoredReview] = {}
        self.sequences: dict[uuid.UUID, StoredSequence] = {}
        self.visual_profiles: dict[uuid.UUID, StoredVisualProfileRevision] = {}
        self._idempotency: dict[str, uuid.UUID] = {}

    def create_project(self, source: StoryProjectInput, *, content_date: date) -> StoredProject:
        project_id = uuid.uuid4()
        profile = self._new_visual_profile(project_id, VisualProfileDraft())
        project = StoredProject(
            project_id,
            source.title,
            content_date,
            RunStatus.ACTIVE,
            visual_profile_revision_id=profile.id,
        )
        self.projects[project.id] = project
        scene = StoredScene(
            uuid.uuid4(),
            project.id,
            1,
            SceneDraft(title=source.first_scene_title, sourceText=source.first_scene_text),
            SceneStatus.DRAFT,
        )
        self.scenes[scene.id] = scene
        return project

    def list_projects(self) -> tuple[StoredProject, ...]:
        return tuple(self.projects.values())

    def update_project(
        self,
        project_id: uuid.UUID,
        *,
        title: str,
        content_date: date,
    ) -> StoredProject:
        self.projects[project_id] = replace(
            self.projects[project_id],
            title=title,
            content_date=content_date,
        )
        return self.projects[project_id]

    def get_project(self, project_id: uuid.UUID) -> StoredProject:
        return self.projects[project_id]

    def update_project_default_references(
        self,
        project_id: uuid.UUID,
        references: list[ReferenceBinding] | tuple[ReferenceBinding, ...],
    ) -> StoredProject:
        self.projects[project_id] = replace(
            self.projects[project_id],
            default_reference_bindings=tuple(references),
        )
        return self.projects[project_id]

    def get_visual_profile(self, project_id: uuid.UUID) -> StoredVisualProfileRevision:
        revision_id = self.projects[project_id].visual_profile_revision_id
        if revision_id is None:
            raise ValueError("project has no visual profile")
        return self.visual_profiles[revision_id]

    def get_default_visual_profile(self, project_id: uuid.UUID) -> VisualProfileDraft:
        del project_id
        return VisualProfileDraft()

    def get_visual_profile_revision(
        self,
        revision_id: uuid.UUID,
    ) -> StoredVisualProfileRevision:
        return self.visual_profiles[revision_id]

    def save_visual_profile(
        self,
        project_id: uuid.UUID,
        draft: VisualProfileDraft,
    ) -> StoredVisualProfileRevision:
        encoded = json.dumps(
            draft.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
        )
        profile_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        existing = next(
            (
                item
                for item in self.visual_profiles.values()
                if item.project_id == project_id and item.profile_hash == profile_hash
            ),
            None,
        )
        profile = existing or self._new_visual_profile(project_id, draft)
        self.projects[project_id] = replace(
            self.projects[project_id],
            visual_profile_revision_id=profile.id,
            default_reference_bindings=tuple(
                ReferenceBinding(
                    assetId=item.asset_id,
                    usage=ReferenceUsage.GENERATION_REFERENCE,
                    role=(
                        ReferenceRole.STYLE
                        if item.purpose.value == "style"
                        else ReferenceRole.IDENTITY
                    ),
                    applyTo=ReferenceTarget.BOTH,
                )
                for item in draft.reference_bindings
            ),
        )
        return profile

    def restore_project_canon_references(
        self,
        project_id: uuid.UUID,
        draft: VisualProfileDraft,
    ) -> tuple[StoredVisualProfileRevision, int]:
        profile = self.save_visual_profile(project_id, draft)
        scene_look_ids = {
            item.id
            for item in self.assets.values()
            if item.project_id == project_id and item.role == "scene_look"
        }
        cleaned = 0
        for shot in tuple(self.shots.values()):
            if shot.project_id != project_id:
                continue
            filtered = [
                binding
                for binding in shot.draft.reference_bindings
                if not (
                    binding.asset_id in scene_look_ids
                    and binding.role is ReferenceRole.IDENTITY
                )
            ]
            if len(filtered) == len(shot.draft.reference_bindings):
                continue
            self.update_shot(
                shot.id,
                shot.draft.model_copy(update={"reference_bindings": filtered}),
            )
            cleaned += 1
        return profile, cleaned

    def _new_visual_profile(
        self,
        project_id: uuid.UUID,
        draft: VisualProfileDraft,
    ) -> StoredVisualProfileRevision:
        revisions = [
            item.revision
            for item in self.visual_profiles.values()
            if item.project_id == project_id
        ]
        encoded = json.dumps(
            draft.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            sort_keys=True,
        )
        profile = StoredVisualProfileRevision(
            id=uuid.uuid4(),
            project_id=project_id,
            revision=max(revisions, default=0) + 1,
            profile_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            source_profile_id="fixture-canon",
            draft=draft,
            reference_snapshot=tuple(
                {
                    **item.model_dump(mode="json", by_alias=True),
                    "sha256": self.assets[item.asset_id].sha256,
                }
                for item in draft.reference_bindings
                if item.asset_id in self.assets
            ),
            created_at=datetime.now(UTC),
        )
        self.visual_profiles[profile.id] = profile
        return profile

    def add_scene(self, project_id: uuid.UUID, draft: SceneDraft) -> StoredScene:
        scene = StoredScene(
            uuid.uuid4(),
            project_id,
            len(self.list_scenes(project_id)) + 1,
            draft,
            SceneStatus.DRAFT,
        )
        self.scenes[scene.id] = scene
        return scene

    def update_scene(self, scene_id: uuid.UUID, draft: SceneDraft) -> StoredScene:
        self.scenes[scene_id] = replace(self.scenes[scene_id], draft=draft)
        return self.scenes[scene_id]

    def delete_scene(self, scene_id: uuid.UUID) -> None:
        del self.scenes[scene_id]

    def reorder_scenes(self, project_id: uuid.UUID, scene_ids: tuple[uuid.UUID, ...]) -> None:
        for order, scene_id in enumerate(scene_ids, 1):
            self.scenes[scene_id] = replace(self.scenes[scene_id], order=order)

    def list_scenes(self, project_id: uuid.UUID) -> tuple[StoredScene, ...]:
        return tuple(
            sorted(
                (item for item in self.scenes.values() if item.project_id == project_id),
                key=lambda item: item.order,
            )
        )

    def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
        return self.scenes[scene_id]

    def select_scene_look_asset(
        self,
        scene_id: uuid.UUID,
        asset_id: uuid.UUID | None,
    ) -> StoredScene:
        self.scenes[scene_id] = replace(
            self.scenes[scene_id],
            selected_look_asset_id=asset_id,
        )
        return self.scenes[scene_id]

    def get_scene_look_draft(self, scene_id: uuid.UUID) -> StoredScene:
        return self.scenes[scene_id]

    def save_scene_look_draft(
        self,
        scene_id: uuid.UUID,
        *,
        expected_revision: int,
        draft: SceneLookDraft,
    ) -> StoredScene:
        scene = self.scenes[scene_id]
        if scene.look_draft_revision != expected_revision:
            raise ValueError("stale look draft")
        self.scenes[scene_id] = replace(
            scene,
            draft=scene.draft.model_copy(update={"look_plan": draft.look_plan}),
            look_draft=draft,
            look_draft_revision=expected_revision + 1,
        )
        return self.scenes[scene_id]

    def add_shot(self, scene_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot:
        scene = self.scenes[scene_id]
        shot = StoredShot(
            uuid.uuid4(),
            scene_id,
            scene.project_id,
            len(self.list_shots(scene_id)) + 1,
            draft,
            ShotStatus.READY,
        )
        self.shots[shot.id] = shot
        return shot

    def replace_shots(
        self, scene_id: uuid.UUID, drafts: tuple[ShotCardDraft, ...]
    ) -> tuple[StoredShot, ...]:
        for shot in self.list_shots(scene_id):
            del self.shots[shot.id]
        return tuple(self.add_shot(scene_id, draft) for draft in drafts)

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
        step = self.steps[step_id]
        if step.scene_id is None:
            raise ValueError("suggestion step is not bound to a scene")
        scene = self.scenes[step.scene_id]
        current = self.list_shots(scene.id)
        if apply_mode == "replace":
            if source_shot_revisions:
                raise ValueError("replace mode does not accept source shot revisions")
            shots = self.replace_shots(scene.id, drafts)
        elif apply_mode == "update_existing":
            if len(current) != len(drafts):
                raise RevisionConflictError("shot count changed")
            expected = {shot.id: shot.draft_revision for shot in current}
            if source_shot_revisions != expected:
                raise RevisionConflictError("shot revisions changed")
            shots = tuple(
                self.update_shot(shot.id, draft)
                for shot, draft in zip(current, drafts, strict=True)
            )
        else:
            raise ValueError(f"unsupported suggestion apply mode: {apply_mode}")
        accepted_output = {
            **accepted_output,
            "appliedShotSnapshotHash": shot_snapshot_hash(
                (shot.id, shot.draft_revision, shot.draft) for shot in shots
            ),
            "appliedShotIds": [str(shot.id) for shot in shots],
        }
        self.scenes[scene.id] = replace(
            scene,
            draft=scene.draft.model_copy(update={"look_plan": look_plan}),
            look_draft=(
                scene.look_draft.model_copy(update={"look_plan": look_plan})
                if scene.look_draft is not None
                else SceneLookDraft(
                    visualProfileRevisionId=self.get_visual_profile(scene.project_id).id,
                    lookPlan=look_plan or SceneLookPlan(),
                    referenceBindings=self.get_visual_profile(
                        scene.project_id
                    ).draft.reference_bindings,
                )
            ),
            look_draft_revision=scene.look_draft_revision + 1,
        )
        self.steps[step_id] = replace(
            step,
            input_snapshot={
                **step.input_snapshot,
                "acceptedOutput": accepted_output,
                "acceptedAt": datetime.now(UTC).isoformat(),
            },
        )
        return shots

    def accept_story_diagnosis(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, Any],
    ) -> StoredStep:
        step = self.steps[step_id]
        if step.input_snapshot.get("sourceHash") != expected_source_hash:
            raise RevisionConflictError("stale story diagnosis")
        updated = replace(
            step,
            input_snapshot={
                **step.input_snapshot,
                "acceptedOutput": accepted_output,
                "acceptedAt": datetime.now(UTC).isoformat(),
            },
        )
        self.steps[step_id] = updated
        return updated

    def accept_story_rewrite(
        self,
        *,
        step_id: uuid.UUID,
        expected_source_hash: str,
        accepted_output: dict[str, Any],
        rewritten_story: str,
    ) -> StoredScene:
        step = self.steps[step_id]
        if step.input_snapshot.get("sourceHash") != expected_source_hash or step.scene_id is None:
            raise RevisionConflictError("stale story rewrite")
        scene = self.scenes[step.scene_id]
        self.scenes[scene.id] = replace(
            scene,
            draft=scene.draft.model_copy(update={"source_text": rewritten_story}),
        )
        self.steps[step_id] = replace(
            step,
            input_snapshot={
                **step.input_snapshot,
                "acceptedOutput": accepted_output,
                "acceptedAt": datetime.now(UTC).isoformat(),
            },
        )
        return self.scenes[scene.id]

    def update_shot(self, shot_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot:
        current = self.shots[shot_id]
        self.shots[shot_id] = replace(
            current,
            draft=draft,
            draft_revision=current.draft_revision + (1 if current.draft != draft else 0),
            selected_anchor_asset_id=None,
            selected_video_asset_id=None,
            status=ShotStatus.READY,
        )
        return self.shots[shot_id]

    def accept_shot_assistance(
        self,
        *,
        step_id: uuid.UUID,
        source_draft_revision: int,
        patch: ShotAssistPatch,
    ) -> StoredShot:
        step = self.steps[step_id]
        if step.shot_card_id is None:
            raise ValueError("shot assistance is not bound to a shot")
        current = self.shots[step.shot_card_id]
        if current.draft_revision != source_draft_revision:
            raise RevisionConflictError("stale shot assistance")
        updated_draft = apply_shot_assist_patch(current.draft, patch)
        updated = replace(
            current,
            draft=updated_draft,
            draft_revision=current.draft_revision + (1 if updated_draft != current.draft else 0),
            selected_anchor_asset_id=None,
            selected_video_asset_id=None,
            status=ShotStatus.READY,
        )
        self.shots[current.id] = updated
        self.steps[step_id] = replace(
            step,
            input_snapshot={
                **step.input_snapshot,
                "acceptedOutput": patch.model_dump(mode="json", by_alias=True),
                "acceptedAt": datetime.now(UTC).isoformat(),
                "acceptedDraftRevision": updated.draft_revision,
            },
        )
        return updated

    def delete_shot(self, shot_id: uuid.UUID) -> None:
        del self.shots[shot_id]

    def reorder_shots(self, scene_id: uuid.UUID, shot_ids: tuple[uuid.UUID, ...]) -> None:
        for order, shot_id in enumerate(shot_ids, 1):
            self.shots[shot_id] = replace(self.shots[shot_id], order=order)

    def list_shots(self, scene_id: uuid.UUID) -> tuple[StoredShot, ...]:
        return tuple(
            sorted(
                (item for item in self.shots.values() if item.scene_id == scene_id),
                key=lambda item: item.order,
            )
        )

    def get_shot(self, shot_id: uuid.UUID) -> StoredShot:
        return self.shots[shot_id]

    def next_attempt(self, *, shot_id: uuid.UUID, operation_key: str) -> int:
        attempts = [
            item.attempt
            for item in self.steps.values()
            if item.shot_card_id == shot_id and item.operation_key == operation_key
        ]
        return max(attempts, default=0) + 1

    def next_scene_attempt(self, *, scene_id: uuid.UUID, operation_key: str) -> int:
        attempts = [
            item.attempt
            for item in self.steps.values()
            if item.scene_id == scene_id
            and item.shot_card_id is None
            and item.operation_key == operation_key
        ]
        return max(attempts, default=0) + 1

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
        del provider
        key = f"{project_id}:{shot_id or scene_id}:{operation_key}:{attempt}:{input_hash}"
        if key in self._idempotency:
            step = self.steps[self._idempotency[key]]
            return step, self.prompts[step.id]
        step = StoredStep(
            uuid.uuid4(),
            project_id,
            scene_id,
            shot_id,
            kind,
            StepStatus.PENDING,
            attempt,
            operation_key,
            input_snapshot,
            model=model,
            created_at=datetime.now(UTC),
        )
        prompt = StoredPrompt(
            uuid.uuid4(),
            step.id,
            purpose,
            model,
            prompt_text,
            hashlib.sha256(prompt_text.encode()).hexdigest(),
        )
        self.steps[step.id] = step
        self.prompts[step.id] = prompt
        self._idempotency[key] = step.id
        return step, prompt

    def update_step(
        self,
        step_id: uuid.UUID,
        *,
        status: StepStatus,
        task_id: str | None = None,
        error: dict[str, Any] | None = None,
        input_snapshot: dict[str, Any] | None = None,
    ) -> StoredStep:
        current = self.steps[step_id]
        transition_step(current.status, status)
        if task_id is not None and any(
            item.id != step_id and item.provider_task_id == task_id for item in self.steps.values()
        ):
            raise ValueError("provider task is already bound")
        self.steps[step_id] = replace(
            current,
            status=status,
            provider_task_id=task_id or current.provider_task_id,
            error=error if error is not None else current.error,
            input_snapshot=input_snapshot or current.input_snapshot,
        )
        return self.steps[step_id]

    def get_step(self, step_id: uuid.UUID) -> StoredStep:
        return self.steps[step_id]

    def list_steps(
        self,
        *,
        project_id: uuid.UUID,
        scene_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
    ) -> tuple[StoredStep, ...]:
        return tuple(
            item
            for item in self.steps.values()
            if item.project_id == project_id
            and (scene_id is None or item.scene_id == scene_id)
            and (shot_id is None or item.shot_card_id == shot_id)
        )

    def get_prompt(self, step_id: uuid.UUID) -> StoredPrompt | None:
        return self.prompts.get(step_id)

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
        asset = StoredAsset(
            uuid.uuid4(),
            project_id,
            scene_id,
            shot_id,
            step_id,
            role,
            media_type,
            scope,
            status,
            landed.path,
            landed.sha256,
            metadata,
            semantic_key,
        )
        self.assets[asset.id] = asset
        return asset

    def get_asset(self, asset_id: uuid.UUID) -> StoredAsset:
        return self.assets[asset_id]

    def list_assets(
        self,
        *,
        project_id: uuid.UUID | None = None,
        shot_id: uuid.UUID | None = None,
        include_canon: bool = False,
    ) -> tuple[StoredAsset, ...]:
        return tuple(
            item
            for item in self.assets.values()
            if (
                project_id is None
                or item.project_id == project_id
                or (include_canon and item.scope == "canon")
            )
            and (shot_id is None or item.shot_card_id == shot_id)
        )

    def select_shot_asset(
        self, shot_id: uuid.UUID, *, kind: str, asset_id: uuid.UUID
    ) -> StoredShot:
        shot = self.shots[shot_id]
        asset = self.assets[asset_id]
        if asset.status not in {"approved", "ready"}:
            raise ValueError("asset is not approved")
        update = (
            {"selected_anchor_asset_id": asset_id, "status": ShotStatus.VIDEO_PENDING}
            if kind == "anchor"
            else {"selected_video_asset_id": asset_id, "status": ShotStatus.APPROVED}
        )
        self.shots[shot_id] = replace(shot, **update)
        return self.shots[shot_id]

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
        review = StoredReview(
            uuid.uuid4(), step_id, asset_id, source, decision, reason, warnings, evidence
        )
        self.reviews[review.id] = review
        return review

    def decide_asset(
        self, asset_id: uuid.UUID, *, decision: str, reason: str | None
    ) -> StoredAsset:
        asset = self.assets[asset_id]
        if asset.step_id is None:
            raise ValueError("imported references are not reviewed")
        step = self.steps[asset.step_id]
        if step.status is not StepStatus.AWAITING_REVIEW:
            if asset.status == decision:
                return asset
            raise ValueError("asset is not awaiting review")
        self.assets[asset_id] = replace(asset, status=decision)
        self.steps[step.id] = replace(
            step,
            status=StepStatus.SUCCEEDED if decision == "approved" else StepStatus.FAILED,
        )
        self.add_review(
            step_id=step.id,
            asset_id=asset.id,
            source="human",
            decision=decision,
            reason=reason,
            warnings=(),
            evidence={},
        )
        return self.assets[asset_id]

    def list_reviews(self, step_id: uuid.UUID) -> tuple[StoredReview, ...]:
        return tuple(item for item in self.reviews.values() if item.step_id == step_id)

    def create_sequence(
        self,
        *,
        project_id: uuid.UUID,
        plan: ProjectSequencePlan,
        parent_sequence_id: uuid.UUID | None,
        rendered_asset_id: uuid.UUID | None,
        status: SequenceStatus,
    ) -> StoredSequence:
        sequence = StoredSequence(
            uuid.uuid4(),
            project_id,
            len(self.list_sequences(project_id)) + 1,
            parent_sequence_id,
            rendered_asset_id,
            status,
            plan,
            datetime.now(UTC),
        )
        self.sequences[sequence.id] = sequence
        return sequence

    def list_sequences(self, project_id: uuid.UUID) -> tuple[StoredSequence, ...]:
        return tuple(item for item in self.sequences.values() if item.project_id == project_id)

    def select_sequence(self, project_id: uuid.UUID, sequence_id: uuid.UUID) -> StoredSequence:
        sequence = self.sequences[sequence_id]
        if sequence.status is not SequenceStatus.APPROVED:
            raise ValueError("sequence is not approved")
        self.projects[project_id] = replace(
            self.projects[project_id], selected_sequence_id=sequence_id
        )
        return sequence

    def decide_sequence(self, sequence_id: uuid.UUID, *, approved: bool) -> StoredSequence:
        sequence = self.sequences[sequence_id]
        self.sequences[sequence_id] = replace(
            sequence,
            status=SequenceStatus.APPROVED if approved else SequenceStatus.REJECTED,
        )
        return self.sequences[sequence_id]

    def shot_trace(self, shot_id: uuid.UUID) -> dict[str, Any]:
        shot = self.get_shot(shot_id)
        attempts = self.list_steps(project_id=shot.project_id, shot_id=shot.id)
        return {
            "id": str(shot.id),
            "title": shot.draft.title,
            "direction": shot.draft.direction,
            "assets": [
                {"id": str(item.id), "role": item.role, "status": item.status}
                for item in self.list_assets(shot_id=shot.id)
            ],
            "attempts": [
                {
                    "id": str(item.id),
                    "status": item.status.value,
                    "prompt": self.prompts[item.id].text,
                    "reviews": [review.decision for review in self.list_reviews(item.id)],
                }
                for item in attempts
            ],
        }

    def project_graph(self, project_id: uuid.UUID) -> dict[str, Any]:
        return {
            "project": {"id": str(project_id), "title": self.projects[project_id].title},
            "scenes": [
                {
                    "id": str(scene.id),
                    "title": scene.draft.title,
                    "shots": [self.shot_trace(shot.id) for shot in self.list_shots(scene.id)],
                }
                for scene in self.list_scenes(project_id)
            ],
            "sequences": [
                {
                    "id": str(item.id),
                    "revision": item.revision,
                    "status": item.status.value,
                }
                for item in self.list_sequences(project_id)
            ],
        }


class FixtureDirector:
    model = "fixture-director"
    analysis_model = model

    def __init__(self) -> None:
        self.planning_calls: list[str] = []
        self.analysis_calls = 0
        self.analysis_image_paths: list[tuple[Path, ...]] = []
        self.fail_analysis_once = False

    def generate_structured(
        self, *, prompt: str, schema: dict[str, Any], output_name: str
    ) -> DirectorResult:
        del schema
        self.planning_calls.append(output_name)
        if output_name == "StoryDiagnosisOutput":
            return DirectorResult(
                payload={
                    "overallAssessment": (
                        "故事核心明确，但需要统一动作起点、道具流向和人猫因果互动。"
                    ),
                    "issues": [
                        {
                            "category": "physical_feasibility",
                            "evidence": "原稿没有完整交代部分道具的初始状态与移动路径。",
                            "impact": "视频生成可能出现状态跳变。",
                            "suggestion": "在重写稿中统一起点、路径和完成结果。",
                        }
                    ],
                    "rewriteOptions": [
                        {
                            "strategy": "conservative",
                            "title": "保守修订",
                            "summary": "只修正连续性",
                            "tradeoffs": "变化较少",
                        },
                        {
                            "strategy": "balanced",
                            "title": "平衡优化",
                            "summary": "调整动作顺序",
                            "tradeoffs": "会改写部分动作",
                        },
                        {
                            "strategy": "creative",
                            "title": "创作增强",
                            "summary": "增强人猫因果互动",
                            "tradeoffs": "变化最大",
                        },
                    ],
                },
                response_id="fixture-diagnosis",
                model=self.model,
                request_hash="fixture-diagnosis-request",
            )
        if output_name == "StoryRewriteOutput":
            return DirectorResult(
                payload={
                    "rewrittenStory": (
                        "灰白猫先观察准备中的道具，孩子完成必要的手部操作并回应猫咪，"
                        "随后两者自然过渡到出门状态。"
                    ),
                    "changeSummary": ["统一动作起点与道具流向", "补充人猫因果互动"],
                    "unresolvedQuestions": [],
                },
                response_id="fixture-rewrite",
                model=self.model,
                request_hash="fixture-rewrite-request",
            )
        match = re.search(r"严格输出(\d+)个视频片段", prompt)
        count = int(match.group(1)) if match else 1
        suggestions = [
            {
                "title": f"猫咪主导的生活片段{index}",
                "direction": (
                    "1. 中景固定机位，猫咪位于人物前侧先观察目标，人物保持在后方准备。\n"
                    "2. 近景轻微跟随猫咪自然四足靠近，人物用手拿取所需道具并配合。\n"
                    "3. 中景固定收尾，人猫完成同一微事件的因果互动，环境声与接触声同步。"
                ),
                "suggestedDurationSeconds": 8 + index,
            }
            for index in range(1, count + 1)
        ]
        return DirectorResult(
            payload={
                "sceneTitle": "池塘边的小发现",
                "lookPlan": {
                    "personWardrobe": "浅色户外外套",
                    "personAccessories": "帆布包",
                    "catAppearance": "保持Canon外观且不增加服饰",
                    "keyProps": "鱼竿与小水桶",
                    "environmentStyle": "outdoor",
                    "personPose": "站在猫咪后侧并稳稳拿住鱼竿",
                    "catPose": "自然四足站立并看向浮标",
                    "composition": "人物与猫咪保持前后可读关系，完整展示鱼竿和水桶",
                    "additionalInstructions": "竹篮与鱼竿尺寸自然",
                    "imageRecommended": True,
                    "recommendationReason": "多片段复用服装和关键道具",
                },
                "shots": suggestions,
            },
            response_id="fixture-response",
            model=self.model,
            request_hash="fixture-request",
        )

    def analyze_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
        image_paths: tuple[Path, ...],
    ) -> DirectorResult:
        del schema, output_name
        self.analysis_calls += 1
        self.analysis_image_paths.append(image_paths)
        if self.fail_analysis_once:
            self.fail_analysis_once = False
            raise GatewayError(
                "fixture LLM timeout",
                code="fixture_analysis_timeout",
                retryable=True,
                timed_out=True,
            )
        _require("上一片段" in prompt and "下一片段" in prompt, "adjacent shots missing")
        return DirectorResult(
            payload={
                "actionDensityAssessment": "当前12秒内容可压缩为两个连续子镜头",
                "pacingPlan": {
                    "recommendedDurationSeconds": 11,
                    "rationale": "删除重复建立，保留猫咪观察和人物配合",
                    "beats": [
                        {"ordinal": 1, "description": "简洁建立人猫位置", "rhythm": "brief"},
                        {"ordinal": 2, "description": "展开互动并稳定收尾", "rhythm": "expanded"},
                    ],
                },
                "recommendedSceneLookUsage": "appearance_only",
                "recommendedAnchorMode": "text_only",
                "referenceDecisions": [],
                "continuity": {
                    "previousIssues": [],
                    "nextIssues": ["下一片段不要重复收起同一件道具"],
                    "recommendation": "以猫咪回看人物作为稳定交接状态",
                },
                "promptRisks": ["原稿动作密度偏高"],
                "assetCompatibilityAssessment": (
                    "实际图片只适合继承共同造型，不应覆盖当前动作起点。"
                ),
                "creativeBody": (
                    "1. 中景固定，猫咪先观察目标，人物保持在后方准备。\n"
                    "2. 近景，人物完成必要操作，猫咪给出反馈并稳定收尾。"
                ),
                "creativeAlternatives": [
                    {
                        "label": "stable",
                        "body": "1. 固定中景建立人猫位置。\n2. 固定近景完成互动并停稳。",
                        "rationale": "减少运镜和状态跳变",
                    }
                ],
                "patch": {
                    "title": "LLM建议：猫咪完成观察",
                    "durationSeconds": 11,
                    "sceneLookUsage": "appearance_only",
                },
            },
            response_id=f"fixture-analysis-{self.analysis_calls}",
            model=self.analysis_model,
            request_hash=f"analysis-{self.analysis_calls:02d}",
        )


class FixtureGateway:
    image_model = "fixture-image"
    video_model = "fixture-video"
    review_model = "fixture-review"

    def __init__(self) -> None:
        self.submissions: list[VideoInputPlan] = []
        self.image_submissions = 0
        self.image_prompts: list[str] = []
        self.image_references: list[tuple[Path, ...]] = []
        self.video_prompts: list[str] = []
        self.fail_unknown_once = False

    def generate_image(self, *, prompt: str, reference_paths: tuple[Path, ...]) -> ImageResult:
        self.image_submissions += 1
        self.image_prompts.append(prompt)
        self.image_references.append(reference_paths)
        url = (
            "https://fixture.local/scene-look.png"
            if "场景视觉基准图" in prompt
            else "https://fixture.local/anchor.png"
        )
        return ImageResult(url, self.image_model)

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
    ) -> VideoTaskResult:
        del input_sources
        self.video_prompts.append(prompt)
        self.submissions.append(input_plan)
        if self.fail_unknown_once:
            self.fail_unknown_once = False
            raise GatewayError(
                "fixture submission outcome is unknown",
                code="fixture_timeout",
                retryable=False,
                submission_unknown=True,
            )
        suffix = "edit.mp4" if input_plan.operation is RenderOperation.EDIT else "shot.mp4"
        return VideoTaskResult(
            task_id=f"fixture-task-{len(self.submissions)}",
            status="succeeded",
            video_url=f"https://fixture.local/{suffix}",
            duration_seconds=input_plan.duration_seconds,
            resolution=input_plan.resolution,
        )

    def get_video_task(self, task_id: str) -> VideoTaskResult:
        return VideoTaskResult(task_id, "succeeded", "https://fixture.local/shot.mp4")

    def list_video_tasks(self, *, model: str, page_size: int = 100) -> tuple[VideoTaskResult, ...]:
        del model, page_size
        return ()

    def diagnose_video_frames(
        self, *, prompt: str, frame_paths: tuple[Path, ...]
    ) -> VideoDiagnosticResult:
        del prompt
        return VideoDiagnosticResult(
            identity_ok=True,
            style_ok=True,
            constraints_ok=True,
            narrative_order_ok=True,
            confidence=0.91,
            violations=(),
            evidence=tuple(
                {
                    "timestamp": f"{index}s",
                    "object": "shot",
                    "observation": "fixture frame available",
                    "relationError": None,
                }
                for index, _path in enumerate(frame_paths)
            ),
            shot_boundaries_seconds=(0.0, 4.0, 8.0),
            response_id="fixture-review",
            model=self.review_model,
            request_hash="fixture-review-request",
        )


class FixtureAssetStore:
    def __init__(self, local: LocalAssetStore, fixtures: dict[str, Path]) -> None:
        self._local = local
        self._fixtures = fixtures

    def download(self, url: str, *, suffix: str) -> LandedAsset:
        del suffix
        return self._local.import_local(self._fixtures[url])

    def import_local(self, path: Path) -> LandedAsset:
        return self._local.import_local(path)

    def compose_sequence(
        self,
        paths: tuple[Path, ...],
        plan: ProjectSequencePlan,
    ) -> LandedAsset:
        return self._local.compose_sequence(paths, plan)

    def render_range_replacement(
        self,
        *,
        base_path: Path,
        replacement_path: Path,
        replacement_duration_ms: int,
        start_ms: int,
        end_ms: int,
    ) -> LandedAsset:
        return self._local.render_range_replacement(
            base_path=base_path,
            replacement_path=replacement_path,
            replacement_duration_ms=replacement_duration_ms,
            start_ms=start_ms,
            end_ms=end_ms,
        )


def main() -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("local smoke requires ffmpeg and ffprobe")
    sample_path = (Path(__file__).parents[1] / "docs" / "采茶叶.mp4").resolve()
    if not sample_path.is_file():
        raise RuntimeError(f"local sample is missing: {sample_path}")
    database_canon = _verify_database_canon()
    task_started = threading.Event()
    task_release = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        task_registry = JobRegistry(executor=executor)

        def _blocked_fixture_task() -> dict[str, bool]:
            task_started.set()
            if not task_release.wait(timeout=5):
                raise TimeoutError("offline background task was not released")
            return {"completed": True}

        submitted_at = time.monotonic()
        submitted_task = task_registry.submit(
            kind="generate_video",
            dedup_key="offline:nonblocking",
            fn=_blocked_fixture_task,
            context={"projectId": database_canon["projectId"]},
        )
        submit_elapsed_ms = round((time.monotonic() - submitted_at) * 1000)
        _require(
            submit_elapsed_ms < 500,
            "background task submission blocked the request thread",
        )
        _require(task_started.wait(timeout=1), "background task did not start")
        _require(
            submitted_task.status in {"queued", "running"},
            "background task was not exposed before completion",
        )
        task_release.set()
    _require(submitted_task.status == "succeeded", "background task did not complete")
    with tempfile.TemporaryDirectory(prefix="cvg-v5-smoke-") as temporary:
        root = Path(temporary)
        fixtures = _fixtures(root, ffmpeg)
        repository = MemoryStore()
        gateway = FixtureGateway()
        local_store = LocalAssetStore(
            work_root=root / "work",
            asset_root=root / "assets",
            ffmpeg_path=Path(ffmpeg),
        )
        store = FixtureAssetStore(local_store, fixtures)
        probe = FfprobeMediaProbe(Path(ffprobe))
        extractor = FfmpegFrameExtractor(ffmpeg_path=Path(ffmpeg), work_root=root / "frames")
        sample_probe = _probe_json(ffprobe, sample_path)
        _require(
            any(item.get("codec_type") == "video" for item in sample_probe.get("streams", [])),
            "采茶叶.mp4 has no readable video stream",
        )
        normalized_sample = _normalize_sample(ffmpeg, sample_path, root / "采茶叶-normalized.mp4")
        sample_duration_ms = int(float(sample_probe["format"]["duration"]) * 1000)
        sample_plan = ProjectSequencePlan(
            duration_ms=(sample_duration_ms * 4) - 300,
            clips=[
                SequenceClip(
                    order=1,
                    shot_card_id=uuid.uuid4(),
                    source_asset_id=uuid.uuid4(),
                    source_start_ms=0,
                    source_end_ms=sample_duration_ms,
                    timeline_start_ms=0,
                    timeline_end_ms=sample_duration_ms,
                ),
                SequenceClip(
                    order=2,
                    shot_card_id=uuid.uuid4(),
                    source_asset_id=uuid.uuid4(),
                    source_start_ms=0,
                    source_end_ms=sample_duration_ms,
                    timeline_start_ms=sample_duration_ms,
                    timeline_end_ms=sample_duration_ms * 2,
                    transitionFromPrevious={"type": "cut", "durationMs": 0},
                ),
                SequenceClip(
                    order=3,
                    shot_card_id=uuid.uuid4(),
                    source_asset_id=uuid.uuid4(),
                    source_start_ms=0,
                    source_end_ms=sample_duration_ms,
                    timeline_start_ms=sample_duration_ms * 2,
                    timeline_end_ms=sample_duration_ms * 3,
                    transitionFromPrevious={
                        "type": "fade_black",
                        "durationMs": 300,
                    },
                ),
                SequenceClip(
                    order=4,
                    shot_card_id=uuid.uuid4(),
                    source_asset_id=uuid.uuid4(),
                    source_start_ms=0,
                    source_end_ms=sample_duration_ms,
                    timeline_start_ms=(sample_duration_ms * 3) - 300,
                    timeline_end_ms=(sample_duration_ms * 4) - 300,
                    transitionFromPrevious={
                        "type": "cross_dissolve",
                        "durationMs": 300,
                    },
                ),
            ],
        )
        sample_master = local_store.compose_sequence(
            (normalized_sample,) * 4,
            sample_plan,
        )
        _require(sample_master.path.is_file(), "采茶叶.mp4 local composition was not landed")
        composed_probe = _probe_json(ffprobe, sample_master.path)
        _require(
            any(item.get("codec_type") == "video" for item in composed_probe.get("streams", [])),
            "composed sample asset is unreadable",
        )
        director = FixtureDirector()
        editing = ProjectEditingService(
            repository=repository,
            director=director,
            provider_name="fixture",
        )
        production = ShotProductionService(
            repository=repository,
            gateway=gateway,
            asset_store=store,
            media_probe=probe,
            frame_extractor=extractor,
            provider_name="fixture",
            resolution="480p",
        )
        sequences = SequenceService(
            repository=repository,
            asset_store=store,
            media_probe=probe,
            resolution="480p",
        )

        created = editing.create_project(
            StoryProjectInput(
                title="离线钓鱼镜头队列",
                firstSceneTitle="池塘边",
                firstSceneText="灰白猫先发现浮标轻晃，人物随后稳定持竿回应，最后一起观察水面。",
            ),
            content_date=date(2026, 8, 12),
        )
        project_id = uuid.UUID(created["projectId"])
        repository.update_project(
            project_id,
            title="离线钓鱼片段项目",
            content_date=date(2026, 8, 13),
        )
        _require(
            repository.get_project(project_id).content_date == date(2026, 8, 13),
            "project settings did not flow through storage",
        )
        fixture_canon: dict[str, StoredAsset] = {}
        for key, fixture_url in (
            ("person:headshot", "https://fixture.local/person.png"),
            ("cat:front", "https://fixture.local/cat.png"),
            ("style:line_texture", "https://fixture.local/style.png"),
        ):
            fixture_canon[key] = repository.add_asset(
                landed=store.import_local(fixtures[fixture_url]),
                role="canon",
                media_type="image",
                scope="canon",
                status="approved",
                project_id=None,
                scene_id=None,
                shot_id=None,
                step_id=None,
                semantic_key=key,
                metadata={"displayName": key},
            )
        person_reference = fixture_canon["person:headshot"]
        cat_reference = fixture_canon["cat:front"]
        style_reference = fixture_canon["style:line_texture"]
        visual_profile = repository.save_visual_profile(
            project_id,
            VisualProfileDraft(
                referenceBindings=[
                    LookReferenceBinding(
                        assetId=person_reference.id,
                        purpose="person_identity",
                        instruction="只锁定人物脸型、五官和短发",
                    ),
                    LookReferenceBinding(
                        assetId=cat_reference.id,
                        purpose="cat_identity",
                        instruction="只锁定猫咪毛色、纹路、眼睛和体型",
                    ),
                    LookReferenceBinding(
                        assetId=style_reference.id,
                        purpose="style",
                        instruction="只锁定二维水彩、自然柔光和色彩",
                    ),
                ]
            ),
        )
        _require(visual_profile.revision == 2, "visual profile revision was not created")
        scene = repository.list_scenes(project_id)[0]
        scene = repository.update_scene(
            scene.id,
            scene.draft.model_copy(
                update={"story_mode": StoryMode.MULTI, "target_shot_count": 2}
            ),
        )
        _require(
            not repository.list_steps(project_id=project_id), "project creation created a step"
        )

        diagnosis = editing.diagnose_story(scene.id, allow_paid_generation=True)
        edited_diagnosis = diagnosis.output.model_copy(
            update={"overall_assessment": "人工确认：先统一连续性，再进入完整剧情重写。"}
        )
        editing.accept_story_diagnosis(
            diagnosis.step_id,
            diagnosis=edited_diagnosis,
            selected_strategy="balanced",
            additional_instructions="保持猫咪主导观察、人物完成手部操作",
            preserve_original=False,
        )
        rewrite = editing.rewrite_story(
            scene.id,
            diagnosis_step_id=diagnosis.step_id,
            allow_paid_generation=True,
        )
        edited_rewrite = rewrite.output.model_copy(
            update={
                "rewritten_story": (
                    "灰白猫先观察已经归拢好的准备物品，孩子按合理顺序完成必要的手部操作；"
                    "孩子回应猫咪的观察，两者在物品状态明确后自然准备出门。"
                )
            }
        )
        scene = editing.accept_story_rewrite(rewrite.step_id, rewrite=edited_rewrite)
        _require(
            repository.get_step(diagnosis.step_id).input_snapshot["providerOutput"]
            != repository.get_step(diagnosis.step_id).input_snapshot["acceptedOutput"]["diagnosis"],
            "story diagnosis provider and accepted drafts were not retained",
        )
        _require(
            repository.get_step(rewrite.step_id).input_snapshot["providerOutput"]
            != repository.get_step(rewrite.step_id).input_snapshot["acceptedOutput"],
            "story rewrite provider and accepted drafts were not retained",
        )
        suggestion = editing.suggest_shots(scene.id, allow_paid_generation=True)
        edited_look = suggestion.output.look_plan.model_copy(
            update={"person_wardrobe": "人工调整后的米白外套"}
        )
        edited_suggestions = tuple(
            item.model_copy(
                update={
                    "title": f"人工编辑：{item.title}",
                    "direction": f"{item.direction}\n人工收尾：猫咪回看人物后稳定结束。",
                    "suggested_duration_seconds": 10 + index,
                }
            )
            for index, item in enumerate(suggestion.output.shots)
        )
        shots = list(
            editing.accept_suggestions(
                suggestion.step_id,
                look_plan=edited_look,
                shots=edited_suggestions,
            )
        )
        _require(len(shots) == 2, "fixture suggestions were not accepted")
        accepted_step = repository.get_step(suggestion.step_id)
        _require(
            "providerOutput" in accepted_step.input_snapshot,
            "provider output was overwritten",
        )
        _require(
            "acceptedOutput" in accepted_step.input_snapshot,
            "accepted output was not audited",
        )
        _require("acceptedAt" in accepted_step.input_snapshot, "acceptance timestamp is missing")
        _require(
            accepted_step.input_snapshot["providerOutput"]
            != accepted_step.input_snapshot["acceptedOutput"],
            "edited suggestion was not distinct from provider output",
        )
        first_storyboard_ids = tuple(shot.id for shot in shots)
        second_suggestion = editing.suggest_shots(scene.id, allow_paid_generation=True)
        second_edited = tuple(
            item.model_copy(
                update={
                    "title": f"第二版：{item.title}",
                    "direction": (
                        "1. 中景固定，{{人物}}整理装备，{{猫咪}}在脚边观察。\n"
                        "2. 近景自然跟随，两者完成同一生活微事件并稳定收尾。"
                    ),
                }
            )
            for item in second_suggestion.output.shots
        )
        shots = list(
            editing.accept_suggestions(
                second_suggestion.step_id,
                look_plan=second_suggestion.output.look_plan,
                shots=second_edited,
                apply_mode="replace",
            )
        )
        _require(
            tuple(shot.id for shot in shots) != first_storyboard_ids,
            "replacement storyboard did not create the current clip set",
        )
        workflow = editing.creative_workflow(scene.id)
        _require(
            len(workflow["stages"]["storyboard"]) == 2
            and workflow["stages"]["storyboard"][0]["acceptedOutput"][
                "appliedShotSnapshotHash"
            ]
            == workflow["currentShotSnapshotHash"],
            "storyboard versions were not retained or synchronized",
        )

        # A manual save is authoritative.  A failed paid analysis records its own
        # failure without rolling back the saved revision; the next attempt may
        # then succeed and only the user-selected field is applied.
        saved_for_assist = repository.update_shot(
            shots[0].id,
            shots[0].draft.model_copy(
                update={
                    "duration_seconds": 12,
                    "scene_look_usage": SceneLookUsage.FULL_REFERENCE,
                }
            ),
        )
        shots[0] = saved_for_assist
        analysis_calls_before = director.analysis_calls
        try:
            editing.assist_shot(
                saved_for_assist.id,
                source_draft_revision=saved_for_assist.draft_revision,
                candidate_asset_ids=(person_reference.id,),
                allow_paid_generation=False,
            )
        except ValueError as exc:
            _require("explicit" in str(exc), "missing paid analysis gate")
        else:
            raise AssertionError("LLM analysis ran without explicit payment permission")
        _require(
            director.analysis_calls == analysis_calls_before,
            "unconfirmed analysis reached the fake gateway",
        )
        director.fail_analysis_once = True
        try:
            editing.assist_shot(
                saved_for_assist.id,
                source_draft_revision=saved_for_assist.draft_revision,
                candidate_asset_ids=(person_reference.id, cat_reference.id),
                allow_paid_generation=True,
            )
        except GatewayError:
            pass
        else:
            raise AssertionError("fixture LLM failure was not surfaced")
        _require(
            repository.get_shot(saved_for_assist.id).draft_revision
            == saved_for_assist.draft_revision,
            "failed LLM analysis rolled back the saved shot",
        )
        assistance = editing.assist_shot(
            saved_for_assist.id,
            source_draft_revision=saved_for_assist.draft_revision,
            candidate_asset_ids=(
                person_reference.id,
                cat_reference.id,
                style_reference.id,
                person_reference.id,
            ),
            allow_paid_generation=True,
        )
        _require(
            len(director.analysis_image_paths[-1]) == 3,
            "multimodal analysis did not deduplicate candidate images",
        )
        accepted_assistance = editing.accept_shot_assistance(
            assistance.step_id,
            source_draft_revision=saved_for_assist.draft_revision,
            patch=ShotAssistPatch(durationSeconds=11),
        )
        _require(
            accepted_assistance.draft.duration_seconds == 11
            and accepted_assistance.draft.title == saved_for_assist.draft.title
            and accepted_assistance.draft.scene_look_usage
            is SceneLookUsage.FULL_REFERENCE,
            "field-level LLM acceptance changed unselected fields",
        )
        shots[0] = accepted_assistance
        single_scene = repository.add_scene(
            project_id,
            SceneDraft(
                title="收束片段",
                sourceText="猫咪回到人物脚边，人物放下鱼竿并轻摸猫咪头顶。",
                storyMode="single",
                targetShotCount=1,
            ),
        )
        single_diagnosis = editing.diagnose_story(
            single_scene.id,
            allow_paid_generation=True,
        )
        editing.accept_story_diagnosis(
            single_diagnosis.step_id,
            diagnosis=single_diagnosis.output,
            selected_strategy=None,
            additional_instructions="",
            preserve_original=True,
        )
        single_suggestion = editing.suggest_shots(
            single_scene.id,
            allow_paid_generation=True,
        )
        single_shots = editing.accept_suggestions(
            single_suggestion.step_id,
            look_plan=single_suggestion.output.look_plan,
            shots=single_suggestion.output.shots,
        )
        _require(len(single_shots) == 1, "single mode did not create exactly one clip")
        saved_single_scene = repository.get_scene(single_scene.id)
        _require(
            saved_single_scene.look_draft is not None,
            "accepted look plan did not initialize a scene look draft",
        )
        look_draft = saved_single_scene.look_draft.model_copy(
            update={
                "look_plan": saved_single_scene.look_draft.look_plan.model_copy(
                    update={
                        "person_wardrobe": "人工编辑后的浅色采茶服",
                        "key_props": "竹篮",
                        "composition": "人物站在猫咪后侧，稳定展示服装与竹篮",
                    }
                )
            }
        )
        saved_single_scene = repository.save_scene_look_draft(
            single_scene.id,
            expected_revision=saved_single_scene.look_draft_revision,
            draft=look_draft,
        )
        look_preview = production.preview_scene_look_prompt(single_scene.id)
        _require(not look_preview["warnings"], "scene look prompt preview has warnings")
        _require(
            [item["purpose"] for item in look_preview["references"]]
            == ["person_identity", "cat_identity", "style"],
            "scene look references were not ordered by responsibility",
        )
        image_calls_before_look = gateway.image_submissions
        first_look_result = production.generate_scene_look(
            single_scene.id,
            allow_paid_generation=True,
            draft_revision=saved_single_scene.look_draft_revision,
        )
        _require(
            gateway.image_submissions == image_calls_before_look + 1,
            "fake Seedream did not receive the scene look request",
        )
        _require(
            tuple(path.read_bytes() for path in gateway.image_references[-1])
            == tuple(
                item.require_path().read_bytes()
                for item in (person_reference, cat_reference, style_reference)
            ),
            "fake Seedream received the wrong reference order",
        )
        second_look_result = production.generate_scene_look(
            single_scene.id,
            allow_paid_generation=True,
            draft_revision=saved_single_scene.look_draft_revision,
            regenerate=True,
            reason="只调整共同环境亮度，保留角色身份和服装",
        )
        _require(
            gateway.image_submissions == image_calls_before_look + 2,
            "scene-look retry did not create a new fake Seedream request",
        )
        first_look_asset_id = uuid.UUID(first_look_result["assetId"])
        look_asset_id = uuid.UUID(second_look_result["assetId"])
        api_container = SimpleNamespace(
            repository=repository,
            editing=editing,
            production=production,
            sequences=sequences,
            runtime_settings=SimpleNamespace(
                work_root=root.resolve(),
                asset_root=(root / "assets").resolve(),
            ),
        )
        with TestClient(
            create_app(
                api_container,  # type: ignore[arg-type]
                job_registry=JobRegistry(inline=True),
            )
        ) as api_client:
            versions_response = api_client.get(
                f"/api/v1/scenes/{single_scene.id}/look-versions"
            )
            _require(versions_response.status_code == 200, "look versions API failed")
            versions = versions_response.json()
            _require(
                len(versions) == 2
                and versions[0]["id"] == str(look_asset_id)
                and versions[1]["id"] == str(first_look_asset_id),
                "scene-look retry versions were absent or out of order",
            )
            for asset_id in (first_look_asset_id, look_asset_id):
                content_response = api_client.get(f"/api/v1/assets/{asset_id}/content")
                _require(
                    content_response.status_code == 200 and content_response.content,
                    "look candidate content was not readable through the API",
                )
                review_response = api_client.post(
                    f"/api/v1/assets/{asset_id}/review",
                    json={
                        "decision": "approved",
                        "reason": "offline Web/API fixture",
                        "select": True,
                    },
                )
                _require(review_response.status_code == 200, "look review API failed")
            for asset_id in (first_look_asset_id, look_asset_id):
                selection_response = api_client.put(
                    f"/api/v1/scenes/{single_scene.id}/look-asset",
                    json={"assetId": str(asset_id)},
                )
                _require(
                    selection_response.status_code == 200,
                    "approved scene-look history could not be reselected",
                )
            tasks_response = api_client.get(f"/api/v1/projects/{project_id}/tasks")
            _require(tasks_response.status_code == 200, "persistent task API failed")
            _require(
                len(
                    [
                        item
                        for item in tasks_response.json()
                        if item["operationKey"] == "image:scene-look"
                    ]
                )
                == 2,
                "persistent task API did not expose both scene-look attempts",
            )
            stale_acceptance = api_client.post(
                f"/api/v1/steps/{assistance.step_id}/accept-shot-assistance",
                json={
                    "sourceDraftRevision": saved_for_assist.draft_revision,
                    "patch": {"durationSeconds": 11},
                },
            )
            _require(
                stale_acceptance.status_code == 409,
                "stale shot-assistance acceptance did not return HTTP 409",
            )
        _require(
            repository.get_scene(single_scene.id).selected_look_asset_id == look_asset_id,
            "approved look was not selected through the API",
        )
        appearance_preview = production.preview_shot_prompt(single_shots[0].id)
        full_reference_shot = repository.update_shot(
            single_shots[0].id,
            single_shots[0].draft.model_copy(
                update={"scene_look_usage": SceneLookUsage.FULL_REFERENCE}
            ),
        )
        full_reference_preview = production.preview_shot_prompt(full_reference_shot.id)
        off_shot = repository.update_shot(
            full_reference_shot.id,
            full_reference_shot.draft.model_copy(
                update={"scene_look_usage": SceneLookUsage.OFF}
            ),
        )
        off_preview = production.preview_shot_prompt(off_shot.id)
        third = repository.update_shot(
            off_shot.id,
            off_shot.draft.model_copy(
                update={
                    "anchor_mode": AnchorMode.GENERATE,
                    "scene_look_usage": SceneLookUsage.DERIVE_ANCHOR,
                }
            ),
        )
        derive_anchor_preview = production.preview_shot_prompt(
            third.id,
            target=ReferenceTarget.ANCHOR,
        )
        derive_video_preview = production.preview_shot_prompt(
            third.id,
            target=ReferenceTarget.VIDEO,
        )
        _require(
            [len(item["references"]) for item in (
                appearance_preview,
                full_reference_preview,
                off_preview,
                derive_video_preview,
            )]
            == [4, 4, 3, 3],
            "four scene-look strategies did not produce the expected video inputs",
        )
        _require(
            "忽略基准图中的姿态" in str(appearance_preview["prompt"])
            and "完整参考本场服装" in str(full_reference_preview["prompt"]),
            "appearance-only and full-reference responsibilities were not distinct",
        )
        _require(
            derive_anchor_preview["target"] == "anchor"
            and derive_anchor_preview["ready"] is True
            and any(
                item["sourceLayer"] == "scene_look"
                for item in derive_anchor_preview["references"]
            ),
            "derive-anchor preview did not include the scene visual baseline",
        )
        _require(
            derive_video_preview["target"] == "video"
            and derive_video_preview["ready"] is False
            and derive_video_preview["blockers"]
            and all(
                item["sourceLayer"] != "scene_look"
                for item in derive_video_preview["references"]
            ),
            "derive-anchor video preview did not preflight the missing anchor correctly",
        )
        shots.append(third)

        approved_anchor = production.import_reference(
            project_id=project_id,
            path=fixtures["https://fixture.local/anchor.png"],
            usage="approved_anchor",
            role="composition",
        )
        existing_draft = shots[1].draft.model_copy(
            update={
                "anchor_mode": AnchorMode.EXISTING,
                "reference_bindings": [
                    ReferenceBinding(
                        assetId=approved_anchor.id,
                        usage=ReferenceUsage.APPROVED_ANCHOR,
                        role=ReferenceRole.COMPOSITION,
                        applyTo=ReferenceTarget.VIDEO,
                    )
                ],
            }
        )
        shots[1] = repository.update_shot(shots[1].id, existing_draft)
        generated_draft = shots[2].draft.model_copy(
            update={
                "reference_bindings": [
                    ReferenceBinding(
                        assetId=person_reference.id,
                        usage=ReferenceUsage.GENERATION_REFERENCE,
                        role=ReferenceRole.IDENTITY,
                        applyTo=ReferenceTarget.BOTH,
                    )
                ]
            }
        )
        shots[2] = repository.update_shot(shots[2].id, generated_draft)

        derive_anchor_preview = production.preview_shot_prompt(
            shots[2].id,
            target=ReferenceTarget.ANCHOR,
        )
        anchor_result = production.generate_anchor(shots[2].id, allow_paid_generation=True)
        anchor_step = repository.get_step(uuid.UUID(anchor_result["stepId"]))
        _require(
            anchor_step.input_snapshot["inputHash"] == derive_anchor_preview["inputHash"],
            "anchor preview and paid request did not use the same input hash",
        )
        _require(
            tuple(path.read_bytes() for path in gateway.image_references[-1])
            == tuple(
                item.require_path().read_bytes()
                for item in (
                    person_reference,
                    repository.get_asset(look_asset_id),
                    cat_reference,
                    style_reference,
                )
            ),
            "derive-anchor did not use custom, scene-look, then project references",
        )
        production.decide_asset(
            uuid.UUID(anchor_result["assetId"]),
            decision="approved",
            reason="offline fixture",
            select=True,
        )
        ready_derive_video_preview = production.preview_shot_prompt(
            shots[2].id,
            target=ReferenceTarget.VIDEO,
        )
        _require(
            ready_derive_video_preview["ready"] is True
            and ready_derive_video_preview["references"][0]["sourceLayer"] == "shot"
            and all(
                item["sourceLayer"] != "scene_look"
                for item in ready_derive_video_preview["references"]
            ),
            "approved anchor did not replace the scene baseline in video inputs",
        )

        approved_videos: list[uuid.UUID] = []
        for shot in shots:
            preview = production.preview_shot_prompt(
                shot.id,
                target=ReferenceTarget.VIDEO,
            )
            _require(preview["prompt"].strip(), "compiled prompt is empty")
            result = production.generate_video(shot.id, allow_paid_generation=True)
            generated_step = repository.get_step(uuid.UUID(result["stepId"]))
            _require(
                generated_step.input_snapshot["inputHash"] == preview["inputHash"],
                "video preview and paid request did not use the same input hash",
            )
            asset_id = uuid.UUID(result["assetId"])
            production.decide_asset(
                asset_id,
                decision="approved",
                reason="offline fixture",
                select=True,
            )
            approved_videos.append(asset_id)

        modes = [item.operation for item in gateway.submissions[:3]]
        _require(modes == [RenderOperation.SHOT] * 3, "shot requests used a wrong operation")
        _require(
            len(gateway.submissions[0].bindings) == 3,
            "project visual identity/style references were not inherited",
        )
        _require(
            len(gateway.submissions[1].bindings) == 4,
            "existing anchor and project visual references were not all sent",
        )
        _require(
            len(gateway.submissions[2].bindings) == 4,
            "derive-anchor repeated the scene look in the video request",
        )
        for prompt, submission in zip(
            gateway.video_prompts,
            gateway.submissions,
            strict=True,
        ):
            _require("{{" not in prompt, "internal semantic marker leaked to Seedance")
            aliases = {int(value) for value in re.findall(r"@图片(\d+)", prompt)}
            _require(
                not aliases or max(aliases) <= len(submission.bindings),
                "compiled prompt contains a ghost image alias",
            )

        first_tail = production.tail_frame_status(shots[1].id)
        _require(first_tail["available"] is True, "approved video tail was not extracted")
        shots[1] = production.adopt_previous_tail_anchor(shots[1].id)
        _require(
            len(
                [
                    item
                    for item in shots[1].draft.reference_bindings
                    if item.usage is ReferenceUsage.APPROVED_ANCHOR
                ]
            )
            == 1,
            "previous tail was not installed as the unique anchor",
        )
        repository.select_shot_asset(
            shots[1].id,
            kind="video",
            asset_id=approved_videos[1],
        )

        regenerated = production.generate_video(
            shots[0].id,
            allow_paid_generation=True,
            regenerate=True,
            reason="offline explicit regeneration",
        )
        regenerated_id = uuid.UUID(regenerated["assetId"])
        production.decide_asset(
            regenerated_id,
            decision="approved",
            reason="offline regenerated version",
            select=True,
        )
        stale_tail = production.tail_frame_status(shots[1].id)
        _require(stale_tail["stale"] is True, "old tail did not become stale")
        shots[1] = production.adopt_previous_tail_anchor(shots[1].id)
        refreshed_tail = production.tail_frame_status(shots[1].id)
        _require(
            refreshed_tail["available"] is True
            and refreshed_tail["boundAssetId"] != stale_tail["boundAssetId"],
            "tail anchor was not refreshed from the new approved video",
        )
        repository.select_shot_asset(
            shots[1].id,
            kind="video",
            asset_id=approved_videos[1],
        )
        video_attempts = [
            item
            for item in repository.list_steps(project_id=project_id, shot_id=shots[0].id)
            if item.operation_key == "video:shot"
        ]
        _require([item.attempt for item in video_attempts] == [1, 2], "attempt history was lost")
        reused = production.generate_video(shots[0].id, allow_paid_generation=True)
        _require(reused.get("reused") is True, "same input was charged again")

        sequence = sequences.build_project_sequence(project_id)
        _require(sequence.plan.duration_ms >= 23_000, "project sequence is too short")
        repository.decide_sequence(sequence.id, approved=True)
        repository.select_sequence(project_id, sequence.id)

        source = repository.get_asset(regenerated_id)
        edit_result = production.range_edit(
            shots[0].id,
            source_asset_id=source.id,
            start_ms=1500,
            end_ms=3500,
            instruction="保持人物和猫咪不变，只修复钓线连接方向",
            allow_paid_generation=True,
        )
        edited_id = uuid.UUID(edit_result["assetId"])
        edited = repository.get_asset(edited_id)
        _require(edited.metadata.get("rangeEdit") is not None, "range revision metadata missing")
        production.decide_asset(
            edited_id,
            decision="approved",
            reason="offline range edit",
            select=True,
        )
        _require(repository.get_asset(source.id).path.is_file(), "range edit overwrote source")
        revised_sequence = sequences.build_project_sequence(project_id)
        _require(
            revised_sequence.parent_sequence_id == sequence.id,
            "sequence revision did not retain its parent",
        )
        repository.decide_sequence(revised_sequence.id, approved=True)
        repository.select_sequence(project_id, revised_sequence.id)
        repository.select_sequence(project_id, sequence.id)
        _require(
            repository.get_project(project_id).selected_sequence_id == sequence.id,
            "approved project sequence could not be rolled back",
        )

        failure_shot = repository.add_shot(
            scene.id,
            ShotCardDraft(
                title="未知提交保护",
                direction="固定中景，灰白猫安静观察水面，人物保持鱼竿稳定，镜头在浮标静止时结束。",
            ),
        )
        gateway.fail_unknown_once = True
        before = len(gateway.submissions)
        try:
            production.generate_video(failure_shot.id, allow_paid_generation=True)
        except GatewayError:
            pass
        else:
            raise AssertionError("submission_unknown fixture did not fail")
        try:
            production.generate_video(
                failure_shot.id,
                allow_paid_generation=True,
                regenerate=True,
                reason="must reconcile first",
            )
        except ValueError as exc:
            _require("submission_unknown" in str(exc), "wrong unknown-submit error")
        else:
            raise AssertionError("submission_unknown created a paid retry")
        _require(len(gateway.submissions) == before + 1, "unknown submit was posted twice")

        limit_sources: list[Path] = []
        for index in range(15):
            source = root / f"limit-{index:02d}.png"
            Image.new(
                "RGB",
                (64, 64),
                ((index * 13) % 255, (index * 29) % 255, (index * 47) % 255),
            ).save(source)
            limit_sources.append(source)
        limit_assets = tuple(
            production.import_reference(
                project_id=project_id,
                path=source,
                usage="generation_reference",
                role="prop",
            )
            for source in limit_sources
        )
        limit_scene = repository.add_scene(
            project_id,
            SceneDraft(title="引用上限预检", sourceText="只验证本地引用数量，不提交任务。"),
        )
        too_many_anchor = repository.add_shot(
            limit_scene.id,
            ShotCardDraft(
                title="图片引用上限",
                direction="1. 固定中景，猫咪观察人物整理道具并稳定结束。",
                anchorMode=AnchorMode.GENERATE,
                inheritProjectReferences=False,
                referenceBindings=[
                    ReferenceBinding(
                        assetId=item.id,
                        usage=ReferenceUsage.GENERATION_REFERENCE,
                        role=ReferenceRole.PROP,
                        applyTo=ReferenceTarget.ANCHOR,
                    )
                    for item in limit_assets
                ],
            ),
        )
        image_calls_before = gateway.image_submissions
        try:
            production.generate_anchor(too_many_anchor.id, allow_paid_generation=True)
        except ValueError as exc:
            _require("14" in str(exc), "wrong Seedream reference limit error")
        else:
            raise AssertionError("15 image references reached the paid gateway")
        _require(gateway.image_submissions == image_calls_before, "image limit failed after submit")

        too_many_video = repository.add_shot(
            limit_scene.id,
            ShotCardDraft(
                title="视频引用上限",
                direction="1. 固定中景，猫咪观察人物整理道具并稳定结束。",
                inheritProjectReferences=False,
                referenceBindings=[
                    ReferenceBinding(
                        assetId=item.id,
                        usage=ReferenceUsage.GENERATION_REFERENCE,
                        role=ReferenceRole.PROP,
                        applyTo=ReferenceTarget.VIDEO,
                    )
                    for item in limit_assets[:10]
                ],
            ),
        )
        video_calls_before = len(gateway.submissions)
        try:
            production.generate_video(too_many_video.id, allow_paid_generation=True)
        except ValueError as exc:
            _require("9" in str(exc), "wrong Seedance reference limit error")
        else:
            raise AssertionError("10 video references reached the paid gateway")
        _require(len(gateway.submissions) == video_calls_before, "video limit failed after submit")

        advisory_reviews = [
            item for item in repository.reviews.values() if item.source == "ark_visual"
        ]
        _require(advisory_reviews, "multi-frame advisory review was not recorded")
        _require(
            all(item.decision == "pending" for item in advisory_reviews),
            "AI advice automatically decided media",
        )
        _require(
            all(not hasattr(item, "slot") for item in repository.steps.values()),
            "fixed three-slot data leaked into V5",
        )
        graph = repository.project_graph(project_id)
        traced_shots = [shot for scene_item in graph["scenes"] for shot in scene_item["shots"]]
        _require(traced_shots, "project graph did not expose shot cards")
        _require(
            any(shot["attempts"] for shot in traced_shots),
            "project graph did not expose prompts and attempts",
        )
        _require(
            len(graph["sequences"]) == 2,
            "project graph did not expose both sequence revisions",
        )

        report = {
                "projectId": str(project_id),
                "sceneCount": len(repository.list_scenes(project_id)),
                "shotCount": len(repository.list_shots(scene.id)),
                "providerSubmissions": len(gateway.submissions),
                "videoVersions": len(
                    [item for item in repository.assets.values() if item.media_type == "video"]
                ),
                "sequenceRevisions": len(repository.list_sequences(project_id)),
                "singleAndMultiSuggestions": True,
                "stagedCreativeWorkflow": {
                    "storyDiagnosis": True,
                    "storyRewrite": True,
                    "storyboardDirector": True,
                    "visualPromptReview": True,
                    "plannerOutputSchemas": director.planning_calls,
                    "storyboardVersions": 2,
                },
                "providerAndAcceptedOutputRetained": True,
                "visualProfileRevision": visual_profile.revision,
                "sceneLookDraftRevision": saved_single_scene.look_draft_revision,
                "sceneLookPromptPreviewNoArk": True,
                "lookCandidateWebApiReadReviewSelect": True,
                "sceneLookVersionHistory": {
                    "versions": 2,
                    "retryPreservedHistory": True,
                    "approvedHistoryReselectable": True,
                },
                "nonblockingTaskSubmission": {
                    "submitElapsedMs": submit_elapsed_ms,
                    "completedAfterRelease": True,
                    "persistentWorkflowTasksVisible": True,
                },
                "shotAssistance": {
                    "saveBeforeAnalysis": True,
                    "failedAnalysisPreservedDraft": True,
                    "multimodalCalls": director.analysis_calls,
                    "deduplicatedImageCount": len(director.analysis_image_paths[-1]),
                    "fieldLevelAcceptance": True,
                    "staleAcceptanceHttpStatus": 409,
                },
                "sceneLookUsageStrategies": [
                    "off",
                    "appearance_only",
                    "full_reference",
                    "derive_anchor",
                ],
                "deriveAnchorDoesNotRepeatSceneLookInVideo": True,
                "dualTargetPromptPreview": {
                    "anchorIncludesSceneLook": True,
                    "videoBlocksBeforeApprovedAnchor": True,
                    "previewAndSubmissionHashesMatch": True,
                },
                "tailFrame": {
                    "automaticExtraction": True,
                    "adoptedByNextShot": True,
                    "oldSourceDetectedStale": True,
                    "refreshedFromNewApprovedVideo": True,
                },
                "fakeSeedreamReferenceOrder": [
                    "person_identity",
                    "cat_identity",
                    "style",
                ],
                "referencePrecedenceAndLimits": True,
                "sampleInput": str(Path("docs") / sample_path.name),
                "sampleCompositeSha256": sample_master.sha256,
                "localTransitions": ["cut", "fade_black", "cross_dissolve"],
                "semanticAutoLink": True,
                "databaseCanon": database_canon,
                "rangeEditPreservedSource": True,
                "submissionUnknownFrozen": True,
                "previousSmokeFailuresResolved": [
                    "migration/runtime visual-profile JSON canonicalization mismatch",
                    "legacy four-image Seedance input cap conflicting with V5 nine-image contract",
                ],
                "realArkNewGenerationCalls": 0,
        }
        diagnostics = Path(__file__).parents[1] / "var" / "diagnostics"
        diagnostics.mkdir(parents=True, exist_ok=True)
        report_path = diagnostics / "v5-staged-creative-workflow-local-dataflow.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps({**report, "reportPath": str(report_path)}, ensure_ascii=False))


def _verify_database_canon() -> dict[str, Any]:
    load_local_env()
    recommended_keys = {
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        "style:line_texture",
    }
    container = build_runtime_container()
    try:
        assets = container.repository.list_assets()
        _require(len(assets) == 11, "database does not expose exactly 11 Canon assets")
        for asset in assets:
            _require(asset.scope == "canon", "global Canon query leaked project media")
            _require(asset.status == "approved", f"Canon is not approved: {asset.semantic_key}")
            _require(asset.content_ready, f"Canon content is missing: {asset.semantic_key}")
            digest = hashlib.sha256(asset.require_path().read_bytes()).hexdigest()
            _require(digest == asset.sha256, f"Canon content hash drifted: {asset.semantic_key}")
        available_keys = {asset.semantic_key for asset in assets}
        _require(
            recommended_keys <= available_keys,
            "recommended Canon identity/style set is incomplete",
        )
        projects = container.repository.list_projects()
        _require(projects, "local data-chain verification requires one project")
        project = next(
            (item for item in projects if item.title in {"湖泊钓鱼", "湖泊的鱼"}),
            None,
        )
        _require(project is not None, "the 湖泊钓鱼 project is missing")
        assert project is not None
        profile = container.repository.get_visual_profile(project.id)
        _require(
            len(profile.draft.reference_bindings) >= 3,
            "湖泊钓鱼 visual profile has no complete identity/style references",
        )
        scenes = container.repository.list_scenes(project.id)
        _require(scenes, "湖泊钓鱼 has no scene")
        scene = sorted(scenes, key=lambda item: item.order)[0]
        project_assets = container.repository.list_assets(project_id=project.id)
        scene_looks = [
            asset
            for asset in project_assets
            if asset.scene_id == scene.id and asset.role == "scene_look"
        ]
        _require(
            len(scene_looks) >= 2,
            "湖泊钓鱼 does not expose both scene visual baseline versions",
        )
        _require(
            scene.selected_look_asset_id is not None
            and any(asset.id == scene.selected_look_asset_id for asset in scene_looks),
            "湖泊钓鱼 selected scene visual baseline is missing from its history",
        )
        shots = sorted(container.repository.list_shots(scene.id), key=lambda item: item.order)
        _require(len(shots) == 4, "湖泊钓鱼 does not contain the expected four clips")
        dual_previews: list[dict[str, Any]] = []
        for shot in shots:
            anchor_preview = container.production.preview_shot_prompt(
                shot.id,
                target=ReferenceTarget.ANCHOR,
            )
            video_preview = container.production.preview_shot_prompt(
                shot.id,
                target=ReferenceTarget.VIDEO,
            )
            _require(
                anchor_preview["inputHash"] and video_preview["inputHash"],
                "湖泊钓鱼 dual-target preview has no input hash",
            )
            dual_previews.append(
                {
                    "shotId": str(shot.id),
                    "order": shot.order,
                    "anchorMode": shot.draft.anchor_mode.value,
                    "sceneLookUsage": shot.draft.scene_look_usage.value,
                    "anchorReady": anchor_preview["ready"],
                    "videoReady": video_preview["ready"],
                    "anchorReferenceCount": len(anchor_preview["references"]),
                    "videoReferenceCount": len(video_preview["references"]),
                    "videoBlockers": video_preview["blockers"],
                }
            )
        first_anchor_preview = container.production.preview_shot_prompt(
            shots[0].id,
            target=ReferenceTarget.ANCHOR,
        )
        first_video_preview = container.production.preview_shot_prompt(
            shots[0].id,
            target=ReferenceTarget.VIDEO,
        )
        if shots[0].draft.scene_look_usage is SceneLookUsage.DERIVE_ANCHOR:
            _require(
                any(
                    item["sourceLayer"] == "scene_look"
                    for item in first_anchor_preview["references"]
                )
                and all(
                    item["sourceLayer"] != "scene_look"
                    for item in first_video_preview["references"]
                ),
                "湖泊钓鱼 clip 1 repeats its scene baseline in the video target",
            )
        first_video_assets = [
            asset
            for asset in container.repository.list_assets(shot_id=shots[0].id)
            if asset.media_type == "video"
        ]
        _require(first_video_assets, "湖泊钓鱼 clip 1 video history is empty")
        second_video_steps = [
            step
            for step in container.repository.list_steps(
                project_id=project.id,
                shot_id=shots[1].id,
            )
            if step.operation_key == "video:shot"
        ]
        _require(
            second_video_steps
            and any(step.provider_task_id for step in second_video_steps),
            "湖泊钓鱼 clip 2 has no recoverable Provider task",
        )
        first_video_step_ids = {
            asset.step_id for asset in first_video_assets if asset.step_id is not None
        }
        first_video_steps = [
            step
            for step in container.repository.list_steps(
                project_id=project.id,
                shot_id=shots[0].id,
            )
            if step.id in first_video_step_ids
        ]
        return {
            "assetsReadable": len(assets),
            "recommendedDefaultsPresent": len(recommended_keys),
            "visualProfileRevision": profile.revision,
            "sceneLookDraftRevision": scene.look_draft_revision,
            "projectId": str(project.id),
            "sceneVisualBaselineVersions": len(scene_looks),
            "selectedSceneVisualBaselineId": str(scene.selected_look_asset_id),
            "dualTargetPreviews": dual_previews,
            "clip1VideoVersions": len(first_video_assets),
            "clip1HasOldInputVersion": any(
                step.input_snapshot.get("sourceRevisionHash")
                != first_video_preview["sourceRevisionHash"]
                for step in first_video_steps
            ),
            "clip2ProviderTasks": [
                {
                    "status": step.status.value,
                    "recoverable": bool(step.provider_task_id),
                }
                for step in second_video_steps
            ],
            "readOnlyInspection": True,
        }
    finally:
        container.close()


def _probe_json(ffprobe: str, path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise AssertionError(f"ffprobe returned a non-object for {path}")
    return payload


def _normalize_sample(ffmpeg: str, source: Path, output: Path) -> Path:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            "scale=480:854:force_original_aspect_ratio=decrease,"
            "pad=480:854:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        check=True,
        timeout=1800,
    )
    return output


def _fixtures(root: Path, ffmpeg: str) -> dict[str, Path]:
    anchor = root / "anchor.png"
    person = root / "person.png"
    cat = root / "cat.png"
    style = root / "style.png"
    scene_look = root / "scene-look.png"
    Image.new("RGB", (480, 854), (205, 222, 198)).save(anchor)
    Image.new("RGB", (480, 854), (214, 190, 164)).save(person)
    Image.new("RGB", (480, 854), (155, 165, 173)).save(cat)
    Image.new("RGB", (480, 854), (129, 175, 121)).save(style)
    Image.new("RGB", (480, 854), (194, 205, 151)).save(scene_look)
    shot = root / "shot.mp4"
    edit = root / "edit.mp4"
    # The edited fixture suggestions span 9–11 seconds; a 10-second substitute
    # stays within the production QC tolerance for every accepted clip.
    _video_fixture(ffmpeg, shot, duration=10, color="0x8fb7a0", frequency=440)
    _video_fixture(ffmpeg, edit, duration=4, color="0xd4aa78", frequency=520)
    return {
        "https://fixture.local/anchor.png": anchor,
        "https://fixture.local/person.png": person,
        "https://fixture.local/cat.png": cat,
        "https://fixture.local/style.png": style,
        "https://fixture.local/scene-look.png": scene_look,
        "https://fixture.local/shot.mp4": shot,
        "https://fixture.local/edit.mp4": edit,
    }


def _video_fixture(ffmpeg: str, path: Path, *, duration: int, color: str, frequency: int) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=480x854:r=24:d={duration}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(path),
        ],
        check=True,
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


if __name__ == "__main__":
    main()
