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
from catflow.domain.validation import (
    ValidationCallKind,
    ValidationLimitError,
    first_release_manifest,
)

from .provider_config import ProviderRuntime


class StudioConflictError(ValueError):
    pass


class StudioNotFoundError(LookupError):
    pass


class ValidationRunCreateCommand(ContractModel):
    expected_manifest_hash: str = Field(
        alias="expectedManifestHash", pattern=r"^[a-f0-9]{64}$"
    )
    paid_call_acknowledged: Literal[True] = Field(alias="paidCallAcknowledged")


class ValidationCanonReferenceDto(ContractModel):
    role: Literal["episode_child", "episode_cat", "pair_scale", "style_board"]
    asset_id: uuid.UUID = Field(alias="assetId")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ValidationCanonSnapshotDto(ContractModel):
    profile_id: uuid.UUID = Field(alias="profileId")
    version: int
    profile_hash: str = Field(alias="profileHash", pattern=r"^[a-f0-9]{64}$")
    child_age: Literal["6-7"] = Field(alias="childAge")
    child_height_cm: Literal[120] = Field(alias="childHeightCm")
    references: tuple[ValidationCanonReferenceDto, ...]


class ValidationRunPreviewDto(ContractModel):
    manifest_hash: str = Field(alias="manifestHash")
    topics: tuple[str, ...]
    duration_seconds: int = Field(alias="durationSeconds")
    resolution: str
    aspect_ratio: str = Field(alias="aspectRatio")
    target_budget_cny: int = Field(alias="targetBudgetCny")
    call_limits: dict[ValidationCallKind, int] = Field(alias="callLimits")
    total_call_limit: int = Field(alias="totalCallLimit")
    maximum_video_calls: int = Field(alias="maximumVideoCalls")
    provider: str
    models: dict[str, str]
    capability_revision: str = Field(alias="capabilityRevision")
    cost_estimate_status: Literal["priced", "unmetered_paid"] = Field(
        alias="costEstimateStatus"
    )
    canon: ValidationCanonSnapshotDto


class ValidationRunDto(ValidationRunPreviewDto):
    canon: ValidationCanonSnapshotDto | None = None
    id: uuid.UUID
    status: Literal["draft", "authorized", "paused", "completed", "cancelled"]
    usage: dict[ValidationCallKind, int]
    created_at: datetime = Field(alias="createdAt")
    authorized_at: datetime | None = Field(alias="authorizedAt", default=None)


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
    validation_run_id: uuid.UUID | None = Field(alias="validationRunId", default=None)
    paid_call_acknowledged: bool = Field(alias="paidCallAcknowledged", default=False)


class GenerationCommand(ContractModel):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None, ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    validation_run_id: uuid.UUID | None = Field(alias="validationRunId", default=None)
    paid_call_acknowledged: bool = Field(alias="paidCallAcknowledged", default=False)


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
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None, ge=0)
    validation_run_id: uuid.UUID | None = Field(alias="validationRunId", default=None)
    paid_call_acknowledged: bool = Field(alias="paidCallAcknowledged", default=False)


class VideoDiagnosisCommand(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None, ge=0)
    validation_run_id: uuid.UUID | None = Field(alias="validationRunId", default=None)
    paid_call_acknowledged: bool = Field(alias="paidCallAcknowledged", default=False)


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


JobStatus = Literal[
    "queued",
    "submitting",
    "submitted",
    "polling",
    "storing",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
    "submission_unknown",
]


class PlannerJobDto(ContractModel):
    id: uuid.UUID
    status: JobStatus
    provider: str | None = None
    model: str | None = None
    provider_task_id: str | None = Field(alias="providerTaskId", default=None)
    error: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


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
    latest_job: PlannerJobDto | None = Field(alias="latestJob", default=None)


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
    canon_profile_id: uuid.UUID | None = Field(alias="canonProfileId", default=None)
    producing_job_id: uuid.UUID | None = Field(alias="producingJobId", default=None)
    candidate_index: int | None = Field(alias="candidateIndex", default=None)
    role: str
    media_type: Literal["image", "video", "audio"] = Field(alias="mediaType")
    sha256: str
    byte_size: int = Field(alias="byteSize")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(alias="createdAt")


class StoredAssetDto(AssetDto):
    """Internal persistence projection; never use as an HTTP response model."""

    storage_key: str = Field(alias="storageKey")


class EnvironmentPresetDto(ContractModel):
    id: uuid.UUID
    source_project_id: uuid.UUID = Field(alias="sourceProjectId")
    asset: AssetDto
    active: bool
    created_at: datetime = Field(alias="createdAt")


FixedCanonRole = Literal["episode_child", "episode_cat", "pair_scale", "style_board"]
FIXED_CANON_ROLES: tuple[FixedCanonRole, ...] = (
    "episode_child",
    "episode_cat",
    "pair_scale",
    "style_board",
)


class CanonRevisionCreateCommand(ContractModel):
    fixed_assets: dict[FixedCanonRole, uuid.UUID] = Field(alias="fixedAssets")

    @model_validator(mode="after")
    def require_all_roles(self) -> CanonRevisionCreateCommand:
        if set(self.fixed_assets) != set(FIXED_CANON_ROLES):
            raise ValueError("all four fixed Canon roles are required")
        return self


class CanonProfileDto(ContractModel):
    id: uuid.UUID
    version: int
    spec_version: Literal[4] = Field(alias="specVersion", default=4)
    active: bool
    profile_hash: str = Field(alias="profileHash")
    profile: dict[str, Any]
    fixed_assets: dict[FixedCanonRole, AssetDto] = Field(alias="fixedAssets")
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
        "plan_story",
        "generate_image",
        "diagnose_image",
        "generate_video",
        "diagnose_video",
        "render_export",
    ]
    status: JobStatus
    input_hash: str = Field(alias="inputHash")
    idempotency_key: str = Field(alias="idempotencyKey")
    provider: str | None = None
    model: str | None = None
    provider_task_id: str | None = Field(alias="providerTaskId", default=None)
    validation_run_id: uuid.UUID | None = Field(alias="validationRunId", default=None)
    parent_job_id: uuid.UUID | None = Field(alias="parentJobId", default=None)
    provider_submission_started_at: datetime | None = Field(
        alias="providerSubmissionStartedAt", default=None
    )
    provider_result: dict[str, Any] | None = Field(alias="providerResult", default=None)
    actual_usage: dict[str, Any] | None = Field(alias="actualUsage", default=None)
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
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None)
    cost_estimate_status: Literal["priced", "unmetered_paid"] = Field(
        alias="costEstimateStatus"
    )
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
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None)
    cost_estimate_status: Literal["priced", "unmetered_paid"] = Field(
        alias="costEstimateStatus"
    )
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

    def current_canon_profile(self) -> CanonProfileDto: ...

    def register_canon_asset(
        self,
        *,
        role: FixedCanonRole,
        sha256: str,
        storage_key: str,
        byte_size: int,
    ) -> StoredAssetDto: ...

    def publish_canon_revision(
        self, command: CanonRevisionCreateCommand
    ) -> CanonProfileDto: ...

    def create_validation_run(self, preview: ValidationRunPreviewDto) -> ValidationRunDto: ...

    def get_validation_run(self, run_id: uuid.UUID) -> ValidationRunDto | None: ...

    def latest_validation_run(self) -> ValidationRunDto | None: ...

    def set_validation_run_status(
        self, run_id: uuid.UUID, status: Literal["authorized", "paused", "cancelled"]
    ) -> ValidationRunDto: ...

    def reserve_validation_call(
        self, run_id: uuid.UUID, kind: ValidationCallKind
    ) -> ValidationRunDto: ...

    def create_project(
        self, draft: ProjectCreate, *, canon_profile_id: uuid.UUID
    ) -> ProjectDto: ...

    def list_projects(self) -> list[ProjectDto]: ...

    def get_project(self, project_id: uuid.UUID) -> ProjectDto | None: ...

    def update_project(self, project_id: uuid.UUID, patch: ProjectPatch) -> ProjectDto: ...

    def planner_snapshot(self, project_id: uuid.UUID) -> PlannerSnapshotDto: ...

    def enqueue_planner_message(
        self, project_id: uuid.UUID, command: PlannerMessageCommand, *, job: JobDto
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
    ) -> StoredAssetDto: ...

    def select_asset(
        self,
        project_id: uuid.UUID,
        *,
        slot: str,
        asset_id: uuid.UUID,
        decision: Literal["selected", "approved"] = "selected",
    ) -> ProjectSelectionDto: ...

    def current_selections(self, project_id: uuid.UUID) -> dict[str, AssetDto]: ...

    def environment_presets(self) -> list[EnvironmentPresetDto]: ...

    def list_assets(self, project_id: uuid.UUID) -> list[AssetDto]: ...

    def get_asset(self, asset_id: uuid.UUID) -> StoredAssetDto | None: ...

    def create_job(self, job: JobDto) -> JobDto: ...

    def get_job(self, job_id: uuid.UUID) -> JobDto | None: ...

    def latest_job(self, project_id: uuid.UUID, *, kind: str) -> JobDto | None: ...

    def resume_job_storage(self, job_id: uuid.UUID) -> JobDto: ...

    def cancel_job(self, job_id: uuid.UUID) -> JobDto: ...

    def list_job_events(self, *, after_event_id: int, limit: int = 100) -> list[JobEventDto]: ...

    def latest_job_event_id(self) -> int: ...

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
    def __init__(
        self,
        repository: StudioRepository,
        *,
        provider_runtime: ProviderRuntime | None = None,
    ) -> None:
        self._repository = repository
        self._provider_runtime = provider_runtime or ProviderRuntime.fake()

    def preview_validation_run(self) -> ValidationRunPreviewDto:
        manifest = first_release_manifest()
        canon = _validation_canon_snapshot(self._repository.current_canon_profile())
        models = {
            "planning": self._provider_runtime.planning_model,
            "image": self._provider_runtime.image_model,
            "diagnostic": self._provider_runtime.diagnostic_model,
            "video": self._provider_runtime.video_model,
        }
        document = {
            "topics": manifest.topics,
            "durationSeconds": manifest.duration_seconds,
            "resolution": manifest.resolution,
            "aspectRatio": manifest.aspect_ratio,
            "targetBudgetCny": manifest.target_budget_cny,
            "callLimits": {
                kind.value: limit for kind, limit in manifest.call_limits.items()
            },
            "provider": self._provider_runtime.provider,
            "models": models,
            "capabilityRevision": self._provider_runtime.capability_revision,
            "canon": canon.model_dump(mode="json", by_alias=True),
        }
        return ValidationRunPreviewDto(
            manifestHash=_hash_document(document),
            topics=manifest.topics,
            durationSeconds=manifest.duration_seconds,
            resolution=manifest.resolution,
            aspectRatio=manifest.aspect_ratio,
            targetBudgetCny=manifest.target_budget_cny,
            callLimits=dict(manifest.call_limits),
            totalCallLimit=manifest.total_call_limit,
            maximumVideoCalls=manifest.call_limits[ValidationCallKind.GENERATE_VIDEO],
            provider=self._provider_runtime.provider,
            models=models,
            capabilityRevision=self._provider_runtime.capability_revision,
            costEstimateStatus="unmetered_paid",
            canon=canon,
        )

    @property
    def provider_runtime(self) -> ProviderRuntime:
        return self._provider_runtime

    def authorize_validation_run(
        self, command: ValidationRunCreateCommand
    ) -> ValidationRunDto:
        preview = self.preview_validation_run()
        if not self._provider_runtime.paid_calls_enabled:
            raise StudioConflictError("paid provider calls are disabled")
        if command.expected_manifest_hash != preview.manifest_hash:
            raise StudioConflictError("validation manifest changed")
        return self._repository.create_validation_run(preview)

    def get_validation_run(self, run_id: uuid.UUID) -> ValidationRunDto:
        run = self._repository.get_validation_run(run_id)
        if run is None:
            raise StudioNotFoundError("validation run not found")
        return run

    def current_validation_run(self) -> ValidationRunDto | None:
        return self._repository.latest_validation_run()

    def pause_validation_run(self, run_id: uuid.UUID) -> ValidationRunDto:
        return self._repository.set_validation_run_status(run_id, "paused")

    def reserve_validation_call(
        self, run_id: uuid.UUID, kind: ValidationCallKind
    ) -> ValidationRunDto:
        try:
            return self._repository.reserve_validation_call(run_id, kind)
        except ValidationLimitError as exc:
            raise StudioConflictError(str(exc)) from exc

    def create_project(self, draft: ProjectCreate) -> ProjectDto:
        return self._repository.create_project(
            draft,
            canon_profile_id=self._repository.active_canon_profile_id(),
        )

    def current_canon_profile_id(self) -> uuid.UUID:
        return self._repository.active_canon_profile_id()

    def current_canon(self) -> CanonProfileDto:
        return self._repository.current_canon_profile()

    def register_canon_asset(
        self,
        *,
        role: FixedCanonRole,
        sha256: str,
        storage_key: str,
        byte_size: int,
    ) -> AssetDto:
        return self._repository.register_canon_asset(
            role=role,
            sha256=sha256,
            storage_key=storage_key,
            byte_size=byte_size,
        )

    def publish_canon_revision(
        self, command: CanonRevisionCreateCommand
    ) -> CanonProfileDto:
        return self._repository.publish_canon_revision(command)

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
        project = self._require_project(project_id)
        snapshot = self._repository.planner_snapshot(project_id)
        if command.expected_context_revision != snapshot.context_revision:
            raise StudioConflictError("planner context revision changed")
        self._require_paid_authorization(
            project, command.validation_run_id, command.paid_call_acknowledged
        )
        prompt = _planner_prompt(project, command.text)
        output_schema = _planner_output_schema()
        input_hash = _hash_document(
            {
                "projectId": str(project_id),
                "contextRevision": snapshot.context_revision,
                "text": command.text,
                "provider": self._provider_runtime.provider,
                "model": self._provider_runtime.planning_model,
                "capabilityRevision": self._provider_runtime.capability_revision,
                "prompt": prompt,
                "outputSchema": output_schema,
            }
        )
        now = datetime.now(UTC)
        job = JobDto(
            id=uuid.uuid4(),
            projectId=project_id,
            kind="plan_story",
            status="queued",
            inputHash=input_hash,
            idempotencyKey=command.idempotency_key,
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.planning_model,
            validationRunId=command.validation_run_id,
            expectedCostMicros=(
                None if self._provider_runtime.provider == "ark" else 0
            ),
            frozenInput={
                "text": command.text,
                "contextRevision": snapshot.context_revision,
                "sessionId": str(snapshot.session_id),
                "targetDurationSeconds": project.target_duration_seconds,
                "prompt": prompt,
                "outputSchema": output_schema,
                "capabilityRevision": self._provider_runtime.capability_revision,
            },
            resultAssetIds=[],
            createdAt=now,
            updatedAt=now,
        )
        try:
            return self._repository.enqueue_planner_message(project_id, command, job=job)
        except ValidationLimitError as exc:
            raise StudioConflictError(str(exc)) from exc

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

    def get_stored_asset(self, asset_id: uuid.UUID) -> StoredAssetDto:
        asset = self._repository.get_asset(asset_id)
        if asset is None:
            raise StudioNotFoundError("asset not found")
        return asset

    def workspace(self, project_id: uuid.UUID) -> dict[str, Any]:
        event_cursor = self._repository.latest_job_event_id()
        project = self._require_project(project_id)
        stories = self.list_stories(project_id)
        plans = self.list_shot_plans(project_id)
        selections = self._repository.current_selections(project_id)
        production_slots = {
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        }
        latest_video_job = self._repository.latest_job(project_id, kind="generate_video")
        return {
            "eventCursor": event_cursor,
            "project": project.model_dump(mode="json", by_alias=True),
            "steps": [
                {"id": "planner", "ready": bool(stories)},
                {"id": "assets", "ready": production_slots <= selections.keys()},
                {"id": "storyboard", "ready": bool(plans)},
                {"id": "generation", "ready": "video" in selections},
                {"id": "delivery", "ready": "final" in selections},
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
            "latestVideoJob": (
                latest_video_job.model_dump(mode="json", by_alias=True)
                if latest_video_job is not None
                else None
            ),
        }

    def current_selections(self, project_id: uuid.UUID) -> dict[str, AssetDto]:
        self._require_project(project_id)
        return self._repository.current_selections(project_id)

    def environment_presets(self) -> list[EnvironmentPresetDto]:
        return self._repository.environment_presets()

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
        self, project_id: uuid.UUID, *, maximum_references: int | None = None
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
            maximum_references=(
                self._provider_runtime.maximum_video_references
                if maximum_references is None
                else maximum_references
            ),
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
            "provider": self._provider_runtime.provider,
            "model": self._provider_runtime.video_model,
            "capabilityRevision": self._provider_runtime.capability_revision,
            "durationSeconds": project.target_duration_seconds,
            "resolution": "480p",
            "aspectRatio": "9:16",
        }
        return GenerationPreviewDto(
            inputHash=_hash_document(document),
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.video_model,
            capabilityRevision=self._provider_runtime.capability_revision,
            prompt=prompt,
            negativePrompt=(
                "真实摄影，3D塑料质感，叶片微距摄影污染，儿童年龄、发型、脸型漂移，"
                "猫咪毛色和虎斑分区漂移，额外肢体，融脸，断尾，错误四足，文字，Logo，"
                "水印，背景严重跳变，原地互看，静止停帧，循环动作填充时长，"
                "禁止8岁以上的修长儿童比例，禁止青少年或成人脸型，禁止过长四肢，"
                "禁止身体比例超过约5头身，禁止儿童身高与猫咪比例失真"
            ),
            references=compiled.references,
            expectedCostMicros=(None if self._provider_runtime.provider == "ark" else 0),
            costEstimateStatus=(
                "unmetered_paid" if self._provider_runtime.provider == "ark" else "priced"
            ),
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
            "provider": self._provider_runtime.provider,
            "model": self._provider_runtime.image_model,
            "capabilityRevision": self._provider_runtime.capability_revision,
        }
        return AssetGenerationPreviewDto(
            inputHash=_hash_document(document),
            kind=command.kind,
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.image_model,
            capabilityRevision=self._provider_runtime.capability_revision,
            prompt=prompt,
            negativePrompt=(
                "摄影写实，3D塑料质感，额外肢体，融脸，文字，Logo，水印，"
                "叶片、枝条、露珠、绿色微距摄影，禁止8岁以上的修长儿童比例，"
                "禁止青少年或成人脸型，禁止过长四肢，禁止身体比例超过约5头身，"
                "禁止儿童身高与猫咪比例失真"
            ),
            references=compiled.references,
            expectedCostMicros=(None if self._provider_runtime.provider == "ark" else 0),
            costEstimateStatus=(
                "unmetered_paid" if self._provider_runtime.provider == "ark" else "priced"
            ),
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
        if self._provider_runtime.provider == "ark" and command.kind != "environment":
            raise StudioConflictError(
                "the first validation run only authorizes shared environment generation"
            )
        self._require_paid_authorization(
            self._require_project(project_id),
            command.validation_run_id,
            command.paid_call_acknowledged,
        )
        now = datetime.now(UTC)
        return self._create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="generate_image",
                status="queued",
                inputHash=preview.input_hash,
                idempotencyKey=command.idempotency_key,
                provider=preview.provider,
                model=preview.model,
                validationRunId=command.validation_run_id,
                expectedCostMicros=preview.expected_cost_micros,
                frozenInput={
                    "role": preview.kind,
                    "prompt": preview.prompt,
                    "negativePrompt": preview.negative_prompt,
                    "references": [
                        item.model_dump(mode="json", by_alias=True) for item in preview.references
                    ],
                    "capabilityRevision": preview.capability_revision,
                    "referenceAssetIds": [
                        str(item.asset_id) for item in preview.references if item.included
                    ],
                },
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def create_image_diagnosis_job(
        self, project_id: uuid.UUID, command: ImageDiagnosisCommand
    ) -> JobDto:
        project = self._require_project(project_id)
        self._require_paid_authorization(
            project, command.validation_run_id, command.paid_call_acknowledged
        )
        candidate = self.get_asset(command.asset_id)
        selections = self._repository.current_selections(project_id)
        selected_asset_ids = {asset.id for asset in selections.values()}
        if candidate.media_type != "image" or (
            candidate.project_id != project_id and candidate.id not in selected_asset_ids
        ):
            raise StudioConflictError(
                "diagnosis candidate must belong to the project or its inherited Canon"
            )
        if self._provider_runtime.provider == "ark" and candidate.role != "environment":
            raise StudioConflictError(
                "the first validation run only authorizes environment image diagnosis"
            )
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
            "referenceAssetIds": [reference["assetId"] for reference in references],
            "prompt": (
                "依据带标签的 Canon 身份、同框比例与净化画风板对照候选图片，"
                "返回身份、画风、结构和技术质量建议；AI 建议不得自动批准或拒绝。"
            ),
            "outputSchema": _diagnostic_output_schema(),
        }
        now = datetime.now(UTC)
        return self._create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="diagnose_image",
                status="queued",
                inputHash=_hash_document(frozen_input),
                idempotencyKey=command.idempotency_key,
                provider=self._provider_runtime.provider,
                model=self._provider_runtime.diagnostic_model,
                validationRunId=command.validation_run_id,
                expectedCostMicros=(
                    None if self._provider_runtime.provider == "ark" else 0
                ),
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
        self._require_paid_authorization(
            self._require_project(project_id),
            command.validation_run_id,
            command.paid_call_acknowledged,
        )
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
            validationRunId=command.validation_run_id,
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
                "referenceRoles": [item.role for item in included],
                "capabilityRevision": preview.capability_revision,
                "durationSeconds": 12,
                "resolution": "480p",
                "aspectRatio": "9:16",
            },
            resultAssetIds=[],
            createdAt=now,
            updatedAt=now,
        )
        return self._create_job(job)

    def create_video_diagnosis_job(
        self, project_id: uuid.UUID, command: VideoDiagnosisCommand
    ) -> JobDto:
        project = self._require_project(project_id)
        self._require_paid_authorization(
            project, command.validation_run_id, command.paid_call_acknowledged
        )
        if self._provider_runtime.provider == "ark" and project.theme != "雨天擦爪":
            raise StudioConflictError(
                "the first validation run authorizes video diagnosis only for 雨天擦爪"
            )
        video = self.get_asset(command.asset_id)
        if video.project_id != project_id or video.media_type != "video":
            raise StudioConflictError("video diagnosis target must be a project video")
        selections = self._repository.current_selections(project_id)
        roles = ("episode_child", "episode_cat", "pair_scale", "environment", "style_board")
        missing = [role for role in roles if role not in selections]
        if missing:
            raise StudioConflictError(
                f"missing video diagnosis references: {', '.join(missing)}"
            )
        frozen_input = {
            "videoAssetId": str(video.id),
            "videoSha256": video.sha256,
            "timestampsSeconds": [0.5, 3, 6, 9, 11.5],
            "referenceAssetIds": [str(selections[role].id) for role in roles],
            "referenceRoles": list(roles),
            "prompt": (
                "按儿童身份、猫咪身份、人猫比例、画风一致性、肢体与结构、技术质量、"
                "因果链与主动结尾七项检查五个固定时间点抽帧。只返回建议，不替代人工判定。"
            ),
            "outputSchema": _video_diagnostic_output_schema(),
        }
        now = datetime.now(UTC)
        return self._create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="diagnose_video",
                status="queued",
                inputHash=_hash_document(frozen_input),
                idempotencyKey=command.idempotency_key,
                provider=self._provider_runtime.provider,
                model=self._provider_runtime.diagnostic_model,
                validationRunId=command.validation_run_id,
                parentJobId=video.producing_job_id,
                expectedCostMicros=(
                    None if self._provider_runtime.provider == "ark" else 0
                ),
                frozenInput=frozen_input,
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def get_job(self, job_id: uuid.UUID) -> JobDto:
        job = self._repository.get_job(job_id)
        if job is None:
            raise StudioNotFoundError("job not found")
        return job

    def resume_job_storage(self, job_id: uuid.UUID) -> JobDto:
        return self._repository.resume_job_storage(job_id)

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
        return self._create_job(
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

    def _require_paid_authorization(
        self,
        project: ProjectDto,
        validation_run_id: uuid.UUID | None,
        paid_call_acknowledged: bool,
    ) -> None:
        if self._provider_runtime.provider != "ark":
            return
        if not self._provider_runtime.paid_calls_enabled:
            raise StudioConflictError("paid provider calls are disabled")
        if validation_run_id is None:
            raise StudioConflictError("authorized validation run is required")
        if not paid_call_acknowledged:
            raise StudioConflictError("paid call must be acknowledged")
        run = self._repository.get_validation_run(validation_run_id)
        if run is None or run.status != "authorized":
            raise StudioConflictError("validation run is not authorized")
        if run.canon is None or project.canon_profile_id != run.canon.profile_id:
            raise StudioConflictError(
                "project Canon does not match the authorized validation manifest"
            )
        selected = self._repository.current_selections(project.id)
        for reference in run.canon.references:
            asset = selected.get(reference.role)
            if (
                asset is None
                or asset.id != reference.asset_id
                or asset.sha256 != reference.sha256
            ):
                raise StudioConflictError(
                    "project Canon references changed after validation authorization"
                )
        if (
            project.theme not in run.topics
            or project.target_duration_seconds != run.duration_seconds
        ):
            raise StudioConflictError(
                "project theme and duration must match the authorized validation manifest"
            )
        expected_models = {
            "planning": self._provider_runtime.planning_model,
            "image": self._provider_runtime.image_model,
            "diagnostic": self._provider_runtime.diagnostic_model,
            "video": self._provider_runtime.video_model,
        }
        if (
            run.provider != self._provider_runtime.provider
            or run.models != expected_models
            or run.capability_revision != self._provider_runtime.capability_revision
        ):
            raise StudioConflictError("validation run provider manifest changed")

    def _create_job(self, job: JobDto) -> JobDto:
        try:
            return self._repository.create_job(job)
        except ValidationLimitError as exc:
            raise StudioConflictError(str(exc)) from exc


def _hash_document(document: object) -> str:
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validation_canon_snapshot(profile: CanonProfileDto) -> ValidationCanonSnapshotDto:
    if set(profile.fixed_assets) != set(FIXED_CANON_ROLES):
        raise StudioConflictError(
            "publish all four production Canon assets before previewing a validation run"
        )
    child = profile.profile.get("child")
    if not isinstance(child, dict):
        raise StudioConflictError("the active Canon profile has no child authority")
    if child.get("age") != "6-7" or child.get("heightCm") != 120:
        raise StudioConflictError(
            "the active Canon must define a 6-7 year-old child at 120 cm"
        )
    return ValidationCanonSnapshotDto(
        profileId=profile.id,
        version=profile.version,
        profileHash=profile.profile_hash,
        childAge="6-7",
        childHeightCm=120,
        references=tuple(
            ValidationCanonReferenceDto(
                role=role,
                assetId=profile.fixed_assets[role].id,
                sha256=profile.fixed_assets[role].sha256,
            )
            for role in FIXED_CANON_ROLES
        ),
    )


def _planner_output_schema() -> dict[str, Any]:
    required = [
        "title",
        "summary",
        "body",
        "trigger",
        "childAction",
        "catResponse",
        "visibleChange",
        "warmEnding",
        "targetDurationSeconds",
        "dialoguePolicy",
        "environmentIntent",
    ]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            **{
                field: {"type": "string"}
                for field in required
                if field not in {"targetDurationSeconds", "dialoguePolicy"}
            },
            "targetDurationSeconds": {"type": "integer", "const": 12},
            "dialoguePolicy": {"type": "string", "enum": ["none", "minimal"]},
        },
    }


def _planner_prompt(project: ProjectDto, user_text: str) -> str:
    fixed_chains = {
        "雨天擦爪": (
            "触发：猫咪进门留下湿爪印；孩子：蹲下用软毛巾逐只擦干猫爪；"
            "猫咪：配合抬爪并主动迈到干燥脚垫；变化：湿爪和地面水印明显减少；"
            "结尾：孩子拿起并折好毛巾，猫咪沿脚垫向室内走两步，尾巴自然摆动。"
        ),
        "浇花": (
            "触发：花盆表土干燥；孩子：控制水壶水流浇入花盆；"
            "猫咪：跟随移动水光，主动挪步避开最后一滴水；"
            "变化：土壤明显变深且托盘接住最后一滴；"
            "结尾：孩子放回水壶并轻推托盘归位，猫咪绕花盆走一小步、尾巴轻摆。"
        ),
        "寻找滚落线团": (
            "触发：线团从桌边滚落；孩子：弯腰伸手追线团；"
            "猫咪：用前爪轻拍使线团改变方向；变化：线团滚回收纳篮旁；"
            "结尾：孩子将线团放进篮子并提起篮子，猫咪跟着向前走两步。"
        ),
    }
    chain = fixed_chains.get(project.theme, "按主题建立一个清晰可见的单一因果链。")
    return (
        f"为原创一人一猫生活短片《{project.title}》生成一条结构化提案。"
        f"用户主题：{user_text}。目标严格为{project.target_duration_seconds}秒、9:16、"
        "三个约4秒镜头、无对白或极少对白。只允许一个主要生活事件，并清楚表达"
        f"触发、孩子动作、猫咪反应、可见变化和温暖结尾。指定因果链：{chain}"
        "结尾必须继续发生清晰、"
        "自然、可观察的小动作；不得让儿童和猫咪原地互看，不得用静止停帧、"
        "重复呼吸、无意义慢镜头或循环动作填充时长。保持原创，不复制任何现有IP。"
    )


def _diagnostic_output_schema() -> dict[str, Any]:
    verdict = {"type": "string", "enum": ["pass", "warning", "fail"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["identity", "style", "anatomy", "technical", "warnings"],
        "properties": {
            "identity": {"type": "object", "additionalProperties": verdict},
            "style": verdict,
            "anatomy": verdict,
            "technical": verdict,
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["code", "message"],
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            },
        },
    }


def _video_diagnostic_output_schema() -> dict[str, Any]:
    verdict = {"type": "string", "enum": ["pass", "warning", "fail"]}
    required = [
        "childIdentity",
        "catIdentity",
        "pairScale",
        "styleConsistency",
        "anatomy",
        "technical",
        "causalChainAndActiveEnding",
        "warnings",
    ]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {
            **dict.fromkeys(required[:-1], verdict),
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["timestampSeconds", "code", "message"],
                    "properties": {
                        "timestampSeconds": {"type": "number"},
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            },
        },
    }


def _video_prompt(
    project: ProjectDto, story: StoryVersionDto, shot_plan: ShotPlanVersionDto
) -> str:
    direction_parts: list[str] = []
    for shot in shot_plan.shots:
        child_action = (
            shot.child_action
            if shot.child_action.startswith("孩子")
            else f"孩子{shot.child_action}"
        )
        cat_action = (
            shot.cat_action
            if shot.cat_action.startswith("猫咪")
            else f"猫咪{shot.cat_action}"
        )
        direction_parts.append(
            f"镜头{shot.order}（{shot.duration_seconds}秒，{shot.framing}）："
            f"{child_action}，{cat_action}，{shot.environment_change}"
        )
    directions = "；".join(direction_parts)
    active_endings = {
        "雨天擦爪": (
            "孩子拿起并折好毛巾，猫咪沿脚垫向室内走两步，尾巴自然摆动；"
            "禁止原地互看"
        ),
        "浇花": (
            "孩子将水壶放回一侧并轻推托盘归位，猫咪绕花盆走一小步、尾巴轻摆；"
            "植物必须是柔和数字插画，不能有真实叶片摄影质感"
        ),
        "寻找滚落线团": (
            "孩子将线团放进篮子并提起篮子，猫咪跟着向前走两步；"
            "禁止用静止凝视补足时长"
        ),
    }
    active_ending = active_endings.get(project.theme, story.micro_event.warm_ending)
    structured_event = (
        f"触发：{story.micro_event.trigger}；"
        f"孩子动作：{story.micro_event.child_action}；"
        f"猫咪回应：{story.micro_event.cat_response}；"
        f"可见变化：{story.micro_event.visible_change}；"
        f"温暖结尾：{story.micro_event.warm_ending}"
    )
    return (
        f"原创一人一猫生活短片《{project.title}》，9:16，{project.target_duration_seconds}秒。"
        "固定同一位6至7岁儿童，身高约1.2米，齐下颌短发，保持圆润儿童脸型和"
        "约4.5至5头身的低龄儿童比例；不得生成8岁以上的修长四肢、青少年脸型、"
        "成人化身体比例，不得改变脸型、发型、年龄感和身体结构；"
        "固定同一只灰白虎斑猫，保持毛色分区、"
        "眼睛、鼻口、环纹尾巴和正常四足结构。二维柔和数字插画，暖灰细轮廓线，"
        "哑光材质，轻微纸感颗粒，柔和漫射暖光。"
        f"结构化生活事件：{structured_event}。{directions}。主动结尾：{active_ending}。"
        "结尾必须继续发生一个清晰、自然、可观察的小动作，不得让儿童和猫咪"
        "原地互看，不得使用完全静止、重复呼吸、无意义慢镜头或停帧来填充剩余时长。"
        "无文字、无Logo、无水印，不复制任何画风来源中的叶片、露珠或摄影构图。"
    )


def _asset_prompt(project: ProjectDto, kind: AssetGenerationKind) -> str:
    responsibilities = {
        "episode_child": (
            "生成本集儿童设计：固定同一位6至7岁儿童，身高约1.2米，齐下颌短发，"
            "保持圆润儿童脸型和约4.5至5头身的低龄儿童比例"
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
