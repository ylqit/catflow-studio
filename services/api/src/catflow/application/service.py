from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from catflow.domain.contract import ContractModel
from catflow.domain.models import LifeStoryProposalDraft, MicroEvent, ShotPlanDraft, ShotSpec
from catflow.domain.references import CompiledReference, ProviderReference, compile_references


class StudioConflictError(ValueError):
    pass


class StudioNotFoundError(LookupError):
    pass


class ProjectCreate(ContractModel):
    title: str = Field(min_length=1, max_length=160)
    theme: str = Field(min_length=1, max_length=2_000)
    target_duration_seconds: int = Field(alias="targetDurationSeconds", ge=8, le=15)


class ProjectPatch(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    theme: str | None = Field(default=None, min_length=1, max_length=2_000)
    target_duration_seconds: int | None = Field(
        alias="targetDurationSeconds", default=None, ge=8, le=15
    )

    @model_validator(mode="after")
    def require_change(self) -> ProjectPatch:
        if self.title is None and self.theme is None and self.target_duration_seconds is None:
            raise ValueError("at least one project field is required")
        return self


class ProjectDto(ContractModel):
    id: uuid.UUID
    title: str
    theme: str
    target_duration_seconds: int = Field(alias="targetDurationSeconds")
    aspect_ratio: str = Field(alias="aspectRatio")
    canon_profile_id: uuid.UUID = Field(alias="canonProfileId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class PlannerMessageCommand(ContractModel):
    text: str = Field(min_length=1, max_length=4_000)
    expected_context_revision: int = Field(alias="expectedContextRevision", ge=1)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class GenerationCommand(ContractModel):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    expected_cost_micros: int = Field(alias="expectedCostMicros", ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


AssetGenerationKind = Literal[
    "episode_child", "episode_cat", "pair_scale", "environment", "style_board"
]


class AssetGenerationPreviewCommand(ContractModel):
    kind: AssetGenerationKind


class AssetGenerationCommand(GenerationCommand):
    kind: AssetGenerationKind


class ImageDiagnosisCommand(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    expected_cost_micros: Literal[0] = Field(alias="expectedCostMicros", default=0)


class CandidateQualityReportDto(ContractModel):
    identity: dict[str, Literal["pass", "warning", "fail"]]
    style: Literal["pass", "warning", "fail"]
    anatomy: Literal["pass", "warning", "fail"]
    technical: Literal["pass", "warning", "fail"]
    warnings: list[dict[str, str]] = Field(default_factory=list)


class PlannerMessageDto(ContractModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    ordinal: int
    created_at: datetime = Field(alias="createdAt")


class LifeStoryProposalDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    status: Literal["draft", "adopted", "outdated"]
    title: str
    summary: str
    body: str
    micro_event: MicroEvent = Field(alias="microEvent")
    target_duration_seconds: int = Field(alias="targetDurationSeconds")
    dialogue_policy: Literal["none", "minimal"] = Field(alias="dialoguePolicy")
    environment_intent: str = Field(alias="environmentIntent")
    prop_intent: str | None = Field(alias="propIntent", default=None)
    context_hash: str = Field(alias="contextHash")
    warnings: list[dict[str, str]] = Field(default_factory=list)


class PlannerSnapshotDto(ContractModel):
    session_id: uuid.UUID = Field(alias="sessionId")
    project_id: uuid.UUID = Field(alias="projectId")
    context_revision: int = Field(alias="contextRevision")
    messages: list[PlannerMessageDto]
    proposals: list[LifeStoryProposalDto]


class StoryVersionDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    revision: int
    source_proposal_id: uuid.UUID | None = Field(alias="sourceProposalId", default=None)
    title: str
    body: str
    micro_event: MicroEvent = Field(alias="microEvent")
    target_duration_seconds: int = Field(alias="targetDurationSeconds")
    dialogue_policy: Literal["none", "minimal"] = Field(alias="dialoguePolicy")
    environment_intent: str = Field(alias="environmentIntent")
    active: bool
    created_at: datetime = Field(alias="createdAt")


class StoryCreateCommand(ContractModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4_000)
    micro_event: MicroEvent = Field(alias="microEvent")
    target_duration_seconds: int = Field(alias="targetDurationSeconds", ge=8, le=15)
    dialogue_policy: Literal["none", "minimal"] = Field(alias="dialoguePolicy")
    environment_intent: str = Field(alias="environmentIntent", min_length=1, max_length=500)


class AssetDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID | None = Field(alias="projectId", default=None)
    producing_job_id: uuid.UUID | None = Field(alias="producingJobId", default=None)
    candidate_index: int | None = Field(alias="candidateIndex", default=None)
    role: str
    media_type: Literal["image", "video", "audio"] = Field(alias="mediaType")
    storage_key: str = Field(alias="storageKey")
    sha256: str
    byte_size: int = Field(alias="byteSize")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(alias="createdAt")


class ProjectSelectionDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    asset_id: uuid.UUID = Field(alias="assetId")
    slot: str
    decision: Literal["selected", "rejected", "approved"]
    source_hash: str = Field(alias="sourceHash")
    created_at: datetime = Field(alias="createdAt")


class ShotPlanVersionDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    revision: int
    source_story_version_id: uuid.UUID = Field(alias="sourceStoryVersionId")
    source_selection_hash: str = Field(alias="sourceSelectionHash")
    clip: dict[str, Any]
    shots: list[ShotSpec]
    total_duration_seconds: int = Field(alias="totalDurationSeconds")
    active: bool
    outdated: bool = False
    created_at: datetime = Field(alias="createdAt")


class JobDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    kind: Literal[
        "plan_story", "generate_image", "diagnose_image", "generate_video", "render_export"
    ]
    status: Literal[
        "queued",
        "submitting",
        "submitted",
        "polling",
        "storing",
        "succeeded",
        "failed",
        "cancel_requested",
        "cancelled",
    ]
    input_hash: str = Field(alias="inputHash")
    idempotency_key: str = Field(alias="idempotencyKey")
    provider: str | None = None
    model: str | None = None
    provider_task_id: str | None = Field(alias="providerTaskId", default=None)
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None)
    frozen_input: dict[str, Any] = Field(alias="frozenInput")
    result_asset_ids: list[uuid.UUID] = Field(alias="resultAssetIds", default_factory=list)
    supersedes_job_id: uuid.UUID | None = Field(alias="supersedesJobId", default=None)
    error: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class JobEventDto(ContractModel):
    id: int
    job_id: uuid.UUID = Field(alias="jobId")
    project_id: uuid.UUID = Field(alias="projectId")
    event_type: str = Field(alias="eventType")
    payload: dict[str, Any]
    created_at: datetime = Field(alias="createdAt")


class GenerationPreviewDto(ContractModel):
    input_hash: str = Field(alias="inputHash")
    kind: Literal["video"] = "video"
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    prompt: str
    negative_prompt: str = Field(alias="negativePrompt")
    references: list[CompiledReference]
    expected_cost_micros: int = Field(alias="expectedCostMicros")
    story_version_id: uuid.UUID = Field(alias="storyVersionId")
    shot_plan_version_id: uuid.UUID = Field(alias="shotPlanVersionId")
    selection_hash: str = Field(alias="selectionHash")
    warnings: list[dict[str, str]] = Field(default_factory=list)


class AssetGenerationPreviewDto(ContractModel):
    input_hash: str = Field(alias="inputHash")
    kind: AssetGenerationKind
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    prompt: str
    negative_prompt: str = Field(alias="negativePrompt")
    references: list[CompiledReference]
    expected_cost_micros: int = Field(alias="expectedCostMicros")
    warnings: list[dict[str, str]] = Field(default_factory=list)


class EditSourceDto(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    start_ms: int = Field(alias="startMs", ge=0)
    end_ms: int = Field(alias="endMs", gt=0)

    @model_validator(mode="after")
    def validate_interval(self) -> EditSourceDto:
        if self.end_ms <= self.start_ms:
            raise ValueError("edit source endMs must be greater than startMs")
        return self


class EditTransitionDto(ContractModel):
    after_clip_index: int = Field(alias="afterClipIndex", ge=0)
    type: Literal["none", "fade", "crossfade"]
    duration_ms: int = Field(alias="durationMs", ge=0, le=2_000)


class EditOutputDto(ContractModel):
    aspect_ratio: Literal["9:16"] = Field(alias="aspectRatio")
    width: Literal[720]
    height: Literal[1280]
    format: Literal["mp4"]


class EditDecisionListDto(ContractModel):
    source_video_selections: list[EditSourceDto] = Field(
        alias="sourceVideoSelections", min_length=1, max_length=4
    )
    transitions: list[EditTransitionDto] = Field(default_factory=list)
    audio_policy: Literal["native", "mute", "native_fades"] = Field(alias="audioPolicy")
    output: EditOutputDto

    @model_validator(mode="after")
    def validate_timeline(self) -> EditDecisionListDto:
        duration_ms = sum(
            source.end_ms - source.start_ms for source in self.source_video_selections
        )
        if not 8_000 <= duration_ms <= 15_000:
            raise ValueError("edited duration must be between 8 and 15 seconds")
        maximum_index = len(self.source_video_selections) - 1
        if any(item.after_clip_index > maximum_index for item in self.transitions):
            raise ValueError("transition refers to a missing clip")
        return self


class EditCreateCommand(ContractModel):
    edl: EditDecisionListDto


class EditVersionDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    revision: int
    source_selection_hash: str = Field(alias="sourceSelectionHash")
    edl: EditDecisionListDto
    status: Literal["draft", "rendered", "approved"]
    rendered_asset_id: uuid.UUID | None = Field(alias="renderedAssetId", default=None)
    created_at: datetime = Field(alias="createdAt")


class ExportCommand(ContractModel):
    edit_version_id: uuid.UUID = Field(alias="editVersionId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class FinalSelectionCommand(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")


class StudioRepository(Protocol):
    def active_canon_profile_id(self) -> uuid.UUID: ...

    def create_project(
        self, draft: ProjectCreate, *, canon_profile_id: uuid.UUID
    ) -> ProjectDto: ...

    def list_projects(self) -> list[ProjectDto]: ...

    def get_project(self, project_id: uuid.UUID) -> ProjectDto | None: ...

    def update_project(self, project_id: uuid.UUID, patch: ProjectPatch) -> ProjectDto: ...

    def planner_snapshot(self, project_id: uuid.UUID) -> PlannerSnapshotDto: ...

    def enqueue_planner_message(
        self, project_id: uuid.UUID, command: PlannerMessageCommand, *, input_hash: str
    ) -> JobDto: ...

    def complete_planner_job(
        self, job_id: uuid.UUID, proposal: LifeStoryProposalDraft
    ) -> LifeStoryProposalDto: ...

    def adopt_proposal(self, project_id: uuid.UUID, proposal_id: uuid.UUID) -> StoryVersionDto: ...

    def active_story(self, project_id: uuid.UUID) -> StoryVersionDto | None: ...

    def list_stories(self, project_id: uuid.UUID) -> list[StoryVersionDto]: ...

    def create_story(
        self, project_id: uuid.UUID, command: StoryCreateCommand
    ) -> StoryVersionDto: ...

    def activate_story(self, project_id: uuid.UUID, story_id: uuid.UUID) -> StoryVersionDto: ...

    def create_shot_plan(
        self, project_id: uuid.UUID, draft: ShotPlanDraft
    ) -> ShotPlanVersionDto: ...

    def active_shot_plan(self, project_id: uuid.UUID) -> ShotPlanVersionDto | None: ...

    def list_shot_plans(self, project_id: uuid.UUID) -> list[ShotPlanVersionDto]: ...

    def activate_shot_plan(
        self, project_id: uuid.UUID, shot_plan_id: uuid.UUID
    ) -> ShotPlanVersionDto: ...

    def register_asset(
        self,
        project_id: uuid.UUID,
        *,
        role: str,
        sha256: str,
        media_type: str,
        storage_key: str,
        byte_size: int,
        producing_job_id: uuid.UUID | None,
    ) -> AssetDto: ...

    def select_asset(
        self,
        project_id: uuid.UUID,
        *,
        slot: str,
        asset_id: uuid.UUID,
        decision: Literal["selected", "approved"] = "selected",
    ) -> ProjectSelectionDto: ...

    def current_selections(self, project_id: uuid.UUID) -> dict[str, AssetDto]: ...

    def list_assets(self, project_id: uuid.UUID) -> list[AssetDto]: ...

    def get_asset(self, asset_id: uuid.UUID) -> AssetDto | None: ...

    def create_job(self, job: JobDto) -> JobDto: ...

    def get_job(self, job_id: uuid.UUID) -> JobDto | None: ...

    def cancel_job(self, job_id: uuid.UUID) -> JobDto: ...

    def list_job_events(self, *, after_event_id: int, limit: int = 100) -> list[JobEventDto]: ...

    def create_edit(
        self,
        project_id: uuid.UUID,
        *,
        source_selection_hash: str,
        edl: EditDecisionListDto,
    ) -> EditVersionDto: ...

    def list_edits(self, project_id: uuid.UUID) -> list[EditVersionDto]: ...

    def get_edit(self, edit_id: uuid.UUID) -> EditVersionDto | None: ...


class StudioService:
    def __init__(self, repository: StudioRepository) -> None:
        self._repository = repository

    def create_project(self, draft: ProjectCreate) -> ProjectDto:
        return self._repository.create_project(
            draft,
            canon_profile_id=self._repository.active_canon_profile_id(),
        )

    def current_canon_profile_id(self) -> uuid.UUID:
        return self._repository.active_canon_profile_id()

    def list_projects(self) -> list[ProjectDto]:
        return self._repository.list_projects()

    def get_project(self, project_id: uuid.UUID) -> ProjectDto | None:
        return self._repository.get_project(project_id)

    def update_project(self, project_id: uuid.UUID, patch: ProjectPatch) -> ProjectDto:
        self._require_project(project_id)
        return self._repository.update_project(project_id, patch)

    def get_planner(self, project_id: uuid.UUID) -> PlannerSnapshotDto:
        self._require_project(project_id)
        return self._repository.planner_snapshot(project_id)

    def enqueue_planner_message(
        self, project_id: uuid.UUID, command: PlannerMessageCommand
    ) -> JobDto:
        self._require_project(project_id)
        snapshot = self._repository.planner_snapshot(project_id)
        if command.expected_context_revision != snapshot.context_revision:
            raise StudioConflictError("planner context revision changed")
        input_hash = _hash_document(
            {
                "projectId": str(project_id),
                "contextRevision": snapshot.context_revision,
                "text": command.text,
            }
        )
        return self._repository.enqueue_planner_message(project_id, command, input_hash=input_hash)

    def complete_planner_job(
        self, job_id: uuid.UUID, proposal: LifeStoryProposalDraft
    ) -> LifeStoryProposalDto:
        return self._repository.complete_planner_job(job_id, proposal)

    def adopt_proposal(self, project_id: uuid.UUID, proposal_id: uuid.UUID) -> StoryVersionDto:
        self._require_project(project_id)
        return self._repository.adopt_proposal(project_id, proposal_id)

    def list_stories(self, project_id: uuid.UUID) -> list[StoryVersionDto]:
        self._require_project(project_id)
        return self._repository.list_stories(project_id)

    def create_story(self, project_id: uuid.UUID, command: StoryCreateCommand) -> StoryVersionDto:
        self._require_project(project_id)
        return self._repository.create_story(project_id, command)

    def activate_story(self, project_id: uuid.UUID, story_id: uuid.UUID) -> StoryVersionDto:
        self._require_project(project_id)
        return self._repository.activate_story(project_id, story_id)

    def create_shot_plan(self, project_id: uuid.UUID, draft: ShotPlanDraft) -> ShotPlanVersionDto:
        self._require_project(project_id)
        story = self._repository.active_story(project_id)
        if story is None or story.id != draft.source_story_version_id:
            raise StudioConflictError("active story version changed")
        if self.current_selection_hash(project_id) != draft.source_selection_hash:
            raise StudioConflictError("asset selection changed")
        return self._repository.create_shot_plan(project_id, draft)

    def list_shot_plans(self, project_id: uuid.UUID) -> list[ShotPlanVersionDto]:
        self._require_project(project_id)
        selection_hash = self.current_selection_hash(project_id)
        story = self._repository.active_story(project_id)
        return [
            plan.model_copy(
                update={
                    "outdated": story is None
                    or plan.source_story_version_id != story.id
                    or plan.source_selection_hash != selection_hash
                }
            )
            for plan in self._repository.list_shot_plans(project_id)
        ]

    def activate_shot_plan(
        self, project_id: uuid.UUID, shot_plan_id: uuid.UUID
    ) -> ShotPlanVersionDto:
        self._require_project(project_id)
        return self._repository.activate_shot_plan(project_id, shot_plan_id)

    def register_asset(
        self,
        project_id: uuid.UUID,
        *,
        role: str,
        sha256: str,
        media_type: str = "image",
        storage_key: str | None = None,
        byte_size: int = 1,
        producing_job_id: uuid.UUID | None = None,
    ) -> AssetDto:
        self._require_project(project_id)
        return self._repository.register_asset(
            project_id,
            role=role,
            sha256=sha256,
            media_type=media_type,
            storage_key=storage_key or f"test/{sha256}",
            byte_size=byte_size,
            producing_job_id=producing_job_id,
        )

    def select_asset(
        self,
        project_id: uuid.UUID,
        *,
        slot: str,
        asset_id: uuid.UUID,
        decision: Literal["selected", "approved"] = "selected",
    ) -> ProjectSelectionDto:
        self._require_project(project_id)
        return self._repository.select_asset(
            project_id, slot=slot, asset_id=asset_id, decision=decision
        )

    def list_assets(self, project_id: uuid.UUID) -> list[AssetDto]:
        self._require_project(project_id)
        return self._repository.list_assets(project_id)

    def get_asset(self, asset_id: uuid.UUID) -> AssetDto:
        asset = self._repository.get_asset(asset_id)
        if asset is None:
            raise StudioNotFoundError("asset not found")
        return asset

    def workspace(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self._require_project(project_id)
        stories = self.list_stories(project_id)
        plans = self.list_shot_plans(project_id)
        selections = self._repository.current_selections(project_id)
        return {
            "project": project.model_dump(mode="json", by_alias=True),
            "steps": [
                {"id": "planner", "ready": True},
                {"id": "assets", "ready": bool(selections)},
                {"id": "storyboard", "ready": bool(stories)},
                {"id": "generation", "ready": bool(plans) and len(selections) >= 5},
                {"id": "delivery", "ready": "video" in selections},
            ],
            "activeStory": next(
                (story.model_dump(mode="json", by_alias=True) for story in stories if story.active),
                None,
            ),
            "activeShotPlan": next(
                (plan.model_dump(mode="json", by_alias=True) for plan in plans if plan.active),
                None,
            ),
            "selections": {
                slot: asset.model_dump(mode="json", by_alias=True)
                for slot, asset in selections.items()
            },
            "selectionHash": self.current_selection_hash(project_id),
        }

    def current_selection_hash(self, project_id: uuid.UUID) -> str:
        selections = self._repository.current_selections(project_id)
        production_slots = {
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        }
        return _hash_document(
            {
                slot: {"assetId": str(asset.id), "sha256": asset.sha256}
                for slot, asset in sorted(selections.items())
                if slot in production_slots
            }
        )

    def current_delivery_selection_hash(self, project_id: uuid.UUID) -> str:
        selections = self._repository.current_selections(project_id)
        return _hash_document(
            {
                slot: {"assetId": str(asset.id), "sha256": asset.sha256}
                for slot, asset in sorted(selections.items())
                if slot != "final"
            }
        )

    def preview_video_generation(
        self, project_id: uuid.UUID, *, maximum_references: int = 4
    ) -> GenerationPreviewDto:
        project = self._require_project(project_id)
        story = self._repository.active_story(project_id)
        shot_plan = self._repository.active_shot_plan(project_id)
        if story is None:
            raise StudioConflictError("active story is required")
        if shot_plan is None:
            raise StudioConflictError("active shot plan is required")
        selections = self._repository.current_selections(project_id)
        required_slots = (
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        )
        missing = [slot for slot in required_slots if slot not in selections]
        if missing:
            raise StudioConflictError(f"missing asset selections: {', '.join(missing)}")
        selection_hash = self.current_selection_hash(project_id)
        if shot_plan.source_story_version_id != story.id:
            raise StudioConflictError("shot plan story is outdated")
        if shot_plan.source_selection_hash != selection_hash:
            raise StudioConflictError("shot plan asset selection is outdated")

        compiled = compile_references(
            [
                ProviderReference(
                    assetId=selections[slot].id,
                    role=slot,  # type: ignore[arg-type]
                    sha256=selections[slot].sha256,
                )
                for slot in required_slots
            ],
            maximum_references=maximum_references,
        )
        prompt = _video_prompt(project, story, shot_plan)
        document = {
            "projectId": str(project_id),
            "storyVersionId": str(story.id),
            "shotPlanVersionId": str(shot_plan.id),
            "selectionHash": selection_hash,
            "prompt": prompt,
            "references": [
                reference.model_dump(mode="json", by_alias=True)
                for reference in compiled.references
            ],
            "provider": "fake",
            "model": "catflow-fake-video-v1",
            "capabilityRevision": "fake-video-v1",
        }
        return GenerationPreviewDto(
            inputHash=_hash_document(document),
            provider="fake",
            model="catflow-fake-video-v1",
            capabilityRevision="fake-video-v1",
            prompt=prompt,
            negativePrompt=(
                "摄影写实，3D塑料质感，角色身份漂移，额外肢体，融脸，文字，Logo，水印，"
                "叶片微距摄影，绿色主导污染"
            ),
            references=compiled.references,
            expectedCostMicros=0,
            storyVersionId=story.id,
            shotPlanVersionId=shot_plan.id,
            selectionHash=selection_hash,
        )

    def preview_asset_generation(
        self, project_id: uuid.UUID, command: AssetGenerationPreviewCommand
    ) -> AssetGenerationPreviewDto:
        project = self._require_project(project_id)
        selections = self._repository.current_selections(project_id)
        reference_roles: dict[str, tuple[str, ...]] = {
            "episode_child": ("episode_child", "style_board"),
            "episode_cat": ("episode_cat", "style_board"),
            "pair_scale": ("episode_child", "episode_cat", "pair_scale", "style_board"),
            "environment": ("environment", "style_board"),
            "style_board": ("style_board",),
        }
        references = [
            ProviderReference(
                assetId=selections[role].id,
                role=role,  # type: ignore[arg-type]
                sha256=selections[role].sha256,
            )
            for role in reference_roles[command.kind]
            if role in selections
        ]
        compiled = compile_references(references, maximum_references=4)
        prompt = _asset_prompt(project, command.kind)
        document = {
            "projectId": str(project_id),
            "kind": command.kind,
            "prompt": prompt,
            "references": [
                item.model_dump(mode="json", by_alias=True) for item in compiled.references
            ],
            "provider": "fake",
            "model": "catflow-fake-image-v1",
            "capabilityRevision": "fake-image-v1",
        }
        return AssetGenerationPreviewDto(
            inputHash=_hash_document(document),
            kind=command.kind,
            provider="fake",
            model="catflow-fake-image-v1",
            capabilityRevision="fake-image-v1",
            prompt=prompt,
            negativePrompt=(
                "摄影写实，3D塑料质感，额外肢体，融脸，文字，Logo，水印，"
                "叶片、枝条、露珠、绿色微距摄影"
            ),
            references=compiled.references,
            expectedCostMicros=0,
        )

    def create_asset_generation_job(
        self, project_id: uuid.UUID, command: AssetGenerationCommand
    ) -> JobDto:
        preview = self.preview_asset_generation(
            project_id, AssetGenerationPreviewCommand(kind=command.kind)
        )
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("generation input hash changed")
        if preview.expected_cost_micros != command.expected_cost_micros:
            raise StudioConflictError("generation expected cost changed")
        now = datetime.now(UTC)
        return self._repository.create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="generate_image",
                status="queued",
                inputHash=preview.input_hash,
                idempotencyKey=command.idempotency_key,
                provider=preview.provider,
                model=preview.model,
                expectedCostMicros=preview.expected_cost_micros,
                frozenInput={
                    "role": preview.kind,
                    "prompt": preview.prompt,
                    "negativePrompt": preview.negative_prompt,
                    "references": [
                        item.model_dump(mode="json", by_alias=True) for item in preview.references
                    ],
                    "capabilityRevision": preview.capability_revision,
                },
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def create_image_diagnosis_job(
        self, project_id: uuid.UUID, command: ImageDiagnosisCommand
    ) -> JobDto:
        self._require_project(project_id)
        candidate = self.get_asset(command.asset_id)
        if candidate.project_id != project_id or candidate.media_type != "image":
            raise StudioConflictError("diagnosis candidate must be a project image")
        selections = self._repository.current_selections(project_id)
        reference_roles: dict[str, tuple[str, ...]] = {
            "episode_child": ("episode_child", "style_board"),
            "episode_cat": ("episode_cat", "style_board"),
            "pair_scale": ("episode_child", "episode_cat", "pair_scale", "style_board"),
            "environment": ("environment", "style_board"),
            "style_board": ("style_board",),
        }
        labels = {
            "episode_child": "本集儿童设计",
            "episode_cat": "本集猫咪设计",
            "pair_scale": "人猫同框比例",
            "environment": "当前环境参考",
            "style_board": "Canon v4 净化画风板",
        }
        roles = reference_roles.get(candidate.role, ("style_board",))
        references = [
            {
                "assetId": str(selections[role].id),
                "sha256": selections[role].sha256,
                "role": role,
                "label": labels[role],
            }
            for role in roles
            if role in selections
        ]
        frozen_input = {
            "candidateAssetId": str(candidate.id),
            "candidateSha256": candidate.sha256,
            "candidateRole": candidate.role,
            "references": references,
            "canonProfileId": str(self.current_canon_profile_id()),
            "diagnosticSchema": "candidate-quality-report-v1",
        }
        now = datetime.now(UTC)
        return self._repository.create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="diagnose_image",
                status="queued",
                inputHash=_hash_document(frozen_input),
                idempotencyKey=command.idempotency_key,
                provider="fake",
                model="catflow-fake-image-diagnostic-v1",
                expectedCostMicros=0,
                frozenInput=frozen_input,
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def create_video_job(self, project_id: uuid.UUID, command: GenerationCommand) -> JobDto:
        preview = self.preview_video_generation(project_id)
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("generation input hash changed")
        if preview.expected_cost_micros != command.expected_cost_micros:
            raise StudioConflictError("generation expected cost changed")
        now = datetime.now(UTC)
        included = [reference for reference in preview.references if reference.included]
        job = JobDto(
            id=uuid.uuid4(),
            projectId=project_id,
            kind="generate_video",
            status="queued",
            inputHash=preview.input_hash,
            idempotencyKey=command.idempotency_key,
            provider=preview.provider,
            model=preview.model,
            expectedCostMicros=preview.expected_cost_micros,
            frozenInput={
                "storyVersionId": str(preview.story_version_id),
                "shotPlanVersionId": str(preview.shot_plan_version_id),
                "selectionHash": preview.selection_hash,
                "prompt": preview.prompt,
                "negativePrompt": preview.negative_prompt,
                "references": [
                    item.model_dump(mode="json", by_alias=True) for item in preview.references
                ],
                "referenceAssetIds": [str(item.asset_id) for item in included],
                "capabilityRevision": preview.capability_revision,
            },
            resultAssetIds=[],
            createdAt=now,
            updatedAt=now,
        )
        return self._repository.create_job(job)

    def get_job(self, job_id: uuid.UUID) -> JobDto:
        job = self._repository.get_job(job_id)
        if job is None:
            raise StudioNotFoundError("job not found")
        return job

    def cancel_job(self, job_id: uuid.UUID) -> JobDto:
        return self._repository.cancel_job(job_id)

    def list_job_events(self, *, after_event_id: int) -> list[JobEventDto]:
        return self._repository.list_job_events(after_event_id=after_event_id)

    def create_edit(self, project_id: uuid.UUID, command: EditCreateCommand) -> EditVersionDto:
        self._require_project(project_id)
        selections = self._repository.current_selections(project_id)
        selected_video = selections.get("video")
        if selected_video is None:
            raise StudioConflictError("selected video is required")
        for source in command.edl.source_video_selections:
            asset = self._repository.get_asset(source.asset_id)
            if asset is None or asset.project_id != project_id or asset.media_type != "video":
                raise StudioNotFoundError("edit source video not found")
            if source.sha256 != asset.sha256:
                raise StudioConflictError("edit source hash changed")
            if source.asset_id != selected_video.id:
                raise StudioConflictError("edit source is not the current video selection")
        return self._repository.create_edit(
            project_id,
            source_selection_hash=self.current_delivery_selection_hash(project_id),
            edl=command.edl,
        )

    def list_edits(self, project_id: uuid.UUID) -> list[EditVersionDto]:
        self._require_project(project_id)
        return self._repository.list_edits(project_id)

    def create_export_job(self, project_id: uuid.UUID, command: ExportCommand) -> JobDto:
        self._require_project(project_id)
        edit = self._repository.get_edit(command.edit_version_id)
        if edit is None or edit.project_id != project_id:
            raise StudioNotFoundError("edit version not found")
        if edit.source_selection_hash != self.current_delivery_selection_hash(project_id):
            raise StudioConflictError("edit version is outdated")
        input_hash = _hash_document(
            {
                "editVersionId": str(edit.id),
                "sourceSelectionHash": edit.source_selection_hash,
                "edl": edit.edl.model_dump(mode="json", by_alias=True),
            }
        )
        now = datetime.now(UTC)
        return self._repository.create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="render_export",
                status="queued",
                inputHash=input_hash,
                idempotencyKey=command.idempotency_key,
                provider="local_ffmpeg",
                model="ffmpeg-edl-v1",
                expectedCostMicros=0,
                frozenInput={
                    "editVersionId": str(edit.id),
                    "sourceSelectionHash": edit.source_selection_hash,
                    "edl": edit.edl.model_dump(mode="json", by_alias=True),
                },
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def approve_final(
        self, project_id: uuid.UUID, command: FinalSelectionCommand
    ) -> ProjectSelectionDto:
        asset = self.get_asset(command.asset_id)
        if asset.project_id != project_id or asset.role != "final" or asset.media_type != "video":
            raise StudioConflictError("only a project final video can be approved")
        return self.select_asset(
            project_id,
            slot="final",
            asset_id=command.asset_id,
            decision="approved",
        )

    def _require_project(self, project_id: uuid.UUID) -> ProjectDto:
        project = self._repository.get_project(project_id)
        if project is None:
            raise StudioNotFoundError("project not found")
        return project


def _hash_document(document: object) -> str:
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _video_prompt(
    project: ProjectDto, story: StoryVersionDto, shot_plan: ShotPlanVersionDto
) -> str:
    directions = "；".join(
        f"镜头{shot.order}（{shot.duration_seconds}秒，{shot.framing}）："
        f"孩子{shot.child_action}，猫咪{shot.cat_action}，{shot.environment_change}"
        for shot in shot_plan.shots
    )
    return (
        f"原创一人一猫生活短片《{project.title}》，9:16，{project.target_duration_seconds}秒。"
        "固定同一位8至9岁齐下颌短发儿童；固定同一只灰白虎斑猫，保持毛色分区、"
        "眼睛、鼻口、环纹尾巴和正常四足结构。二维柔和数字插画，暖灰细轮廓线，"
        "哑光材质，轻微纸感颗粒，柔和漫射暖光。"
        f"故事：{story.body}。{directions}。结尾：{story.micro_event.warm_ending}。"
        "无文字、无Logo、无水印，不复制任何画风来源中的叶片、露珠或摄影构图。"
    )


def _asset_prompt(project: ProjectDto, kind: AssetGenerationKind) -> str:
    responsibilities = {
        "episode_child": (
            "生成本集儿童设计：固定同一位8至9岁儿童，齐下颌短发，稳定脸型、年龄感和身体比例"
        ),
        "episode_cat": (
            "生成本集猫咪设计：固定同一只灰白虎斑猫，稳定灰白毛色分区、"
            "眼鼻口、环纹尾巴和正常四足结构"
        ),
        "pair_scale": "生成一人一猫同框比例参考，角色身份不变，人猫尺寸与站位可信",
        "environment": f"生成《{project.title}》的当前生活环境，只控制空间结构与柔和暖光",
        "style_board": (
            "生成净化后的Canon v4画风板：二维柔和数字插画、暖灰细轮廓、"
            "哑光材质、轻微纸感颗粒和柔和漫射暖光"
        ),
    }
    return (
        f"{responsibilities[kind]}。9:16，原创猫咪IP，主题：{project.theme}。"
        "不得出现摄影写实、文字、水印、叶片微距摄影或角色身份漂移。"
    )
