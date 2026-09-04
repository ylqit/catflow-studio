from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from catflow.domain.billing import RateCardItem
from catflow.domain.contract import ContractModel
from catflow.domain.director_results import (
    DirectorNormalizationResult,
    director_provider_output_schema,
    normalize_director_result,
)
from catflow.domain.models import (
    DirectorPlanPayload,
    DirectorStoryTreatment,
    LifeClipSpec,
    LifeStoryProposalDraft,
    MicroEvent,
    ProfessionalShotPlanDraft,
    ShotPlanDraft,
    ShotSpec,
)
from catflow.domain.references import CompiledReference, ProviderReference, compile_references
from catflow.domain.video_repairs import (
    MAX_ISSUE_FRAMES,
    MIN_ISSUE_FRAMES,
    EditDecisionListV2,
    EditTransitionV2,
    FrameRange,
    RationalFrameRate,
    build_base_timeline,
    expand_generation_window,
    splice_repair_candidate,
    validate_issue_range,
)

from .continuity import (
    EpisodeContinuityConfirmCommand,
    EpisodeContinuityDto,
    EpisodeContinuityKeyframesCommand,
    EpisodeContinuityResetCommand,
    EpisodeContinuitySnapshotDto,
    SeriesAssetBindingDto,
    SeriesAssetBindingsPatchCommand,
)
from .image_generation import compile_provider_image_prompt
from .project_library import (
    ProjectCollectionCreate,
    ProjectCollectionDto,
    ProjectCollectionPatch,
    ProjectLibraryBatchActionCommand,
    ProjectLibraryBatchResultDto,
    ProjectLibraryItemDto,
    ProjectLibraryPageDto,
    ProjectLibraryQuery,
    ProjectLibraryRepository,
    ProjectOrganizationCommand,
)
from .provider_config import ProviderRuntime
from .series import (
    ProjectSeriesContextDto,
    SeriesCreateCommand,
    SeriesEpisodeDto,
    SeriesEpisodeMaterializeCommand,
    SeriesEpisodeStoryGenerationCommand,
    SeriesEpisodeStoryPreviewDto,
    SeriesPatchCommand,
    SeriesPlanActivationCommand,
    SeriesPlanDraft,
    SeriesPlanGenerationCommand,
    SeriesPlanMaterializeCommand,
    SeriesPlanPreviewDto,
    SeriesPlanVersionDto,
    SeriesValidationIssueDto,
    StorySeriesDto,
    compile_series_episode_story_preview,
    compile_series_plan_preview,
)
from .story_imports import (
    StoryImportAnalysisDraft,
    StoryImportAnalysisJobDto,
    StoryImportConfirmCommand,
    StoryImportCreateCommand,
    StoryImportCreateResultDto,
    StoryImportMaterializationDto,
    StoryImportPreviewCommand,
    StoryImportPreviewDto,
    StoryImportReanalyzeCommand,
    StorySourceDocumentDto,
    compile_story_import_preview,
)
from .video_generation import (
    VIDEO_PROMPT_COMPILER_REVISION,
    GenerationPromptSectionDto,
    compile_provider_video_prompt,
    compile_video_generation_prompt,
    synchronize_professional_shot_summaries,
)


class StudioConflictError(ValueError):
    pass


class StudioIdempotencyInputConflictError(StudioConflictError):
    code = "idempotency_input_conflict"
    retryable = False
    user_message = "当前生成输入已经变化，旧请求标识不能继续使用。"


class StudioInputChangedError(StudioConflictError):
    def __init__(self, message: str, latest_preview: SegmentRepairPreviewDto) -> None:
        super().__init__(message)
        self.latest_preview = latest_preview


class StudioValidationError(ValueError):
    pass


class StudioNotFoundError(LookupError):
    pass


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


class ValidationRepairSnapshotDto(ContractModel):
    topic: Literal["雨天擦爪"]
    issue_range: FrameRange = Field(alias="issueRange")
    prompt: str


class ValidationRunPreviewDto(ContractModel):
    manifest_hash: str = Field(alias="manifestHash")
    topics: tuple[str, ...]
    duration_seconds: int = Field(alias="durationSeconds")
    resolution: str
    aspect_ratio: str = Field(alias="aspectRatio")
    target_budget_cny: int = Field(alias="targetBudgetCny")
    call_limits: dict[str, int] = Field(alias="callLimits")
    total_call_limit: int = Field(alias="totalCallLimit")
    maximum_video_calls: int = Field(alias="maximumVideoCalls")
    provider: str
    models: dict[str, str]
    capability_revision: str = Field(alias="capabilityRevision")
    cost_estimate_status: Literal["priced", "unmetered_paid"] = Field(alias="costEstimateStatus")
    authorization_ready: bool = Field(alias="authorizationReady", default=True)
    blocking_reasons: tuple[str, ...] = Field(alias="blockingReasons", default=())
    canon: ValidationCanonSnapshotDto
    repair: ValidationRepairSnapshotDto


class ValidationRunDto(ValidationRunPreviewDto):
    canon: ValidationCanonSnapshotDto | None = None
    id: uuid.UUID
    status: Literal["draft", "authorized", "paused", "completed", "cancelled"]
    usage: dict[str, int]
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


class GenerationPreviewCommand(ContractModel):
    include_previous_episode_video: bool = Field(
        alias="includePreviousEpisodeVideo", default=False
    )


class GenerationCommand(GenerationPreviewCommand):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
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


class VideoDiagnosisCommand(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class CandidateQualityReportDto(ContractModel):
    identity: dict[str, Literal["pass", "warning", "fail"]]
    style: Literal["pass", "warning", "fail"]
    anatomy: Literal["pass", "warning", "fail"]
    technical: Literal["pass", "warning", "fail"]
    warnings: list[dict[str, str]] = Field(default_factory=list)


class EnvironmentQualityReportDto(ContractModel):
    intent_match: Literal["pass", "warning", "fail"] = Field(alias="intentMatch")
    character_free: Literal["pass", "warning", "fail"] = Field(alias="characterFree")
    style_match: Literal["pass", "warning", "fail"] = Field(alias="styleMatch")
    staging_space: Literal["pass", "warning", "fail"] = Field(alias="stagingSpace")
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


BillingStatus = Literal["pending", "usage_reported", "calculated", "unpriced", "provider_adjusted"]


class PlannerJobDto(ContractModel):
    id: uuid.UUID
    status: JobStatus
    provider: str | None = None
    model: str | None = None
    provider_task_id: str | None = Field(alias="providerTaskId", default=None)
    actual_usage: dict[str, Any] | None = Field(alias="actualUsage", default=None)
    actual_cost_micros: int | None = Field(alias="actualCostMicros", default=None)
    currency: Literal["CNY"] = "CNY"
    billing_status: BillingStatus = Field(alias="billingStatus", default="pending")
    rate_card_revision: str | None = Field(alias="rateCardRevision", default=None)
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


class EpisodeContinuityFramesDto(ContractModel):
    episode_id: uuid.UUID = Field(alias="episodeId")
    source_video_asset_id: uuid.UUID | None = Field(alias="sourceVideoAssetId", default=None)
    last_frame: AssetDto | None = Field(alias="lastFrame", default=None)
    candidates: list[AssetDto] = Field(default_factory=list)
    selected_keyframes: list[AssetDto] = Field(alias="selectedKeyframes", default_factory=list)


class StoredAssetDto(AssetDto):
    """Internal persistence projection; never use as an HTTP response model."""

    storage_key: str = Field(alias="storageKey")


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
    director_treatment: DirectorStoryTreatment | None = Field(
        alias="directorTreatment", default=None
    )
    director_prompt_revision: str | None = Field(alias="directorPromptRevision", default=None)
    director_model: str | None = Field(alias="directorModel", default=None)
    director_input_hash: str | None = Field(alias="directorInputHash", default=None)
    review_status: Literal["accepted", "candidate", "rejected", "superseded"] = Field(
        alias="reviewStatus", default="accepted"
    )
    producing_job_id: uuid.UUID | None = Field(alias="producingJobId", default=None)
    base_shot_plan_version_id: uuid.UUID | None = Field(alias="baseShotPlanVersionId", default=None)
    decided_at: datetime | None = Field(alias="decidedAt", default=None)
    active: bool
    outdated: bool = False
    created_at: datetime = Field(alias="createdAt")


class ShotPlanGenerationCommand(ContractModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class ShotPlanGenerationRecoveryCommand(ContractModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class ShotPlanGenerationMaterializeCommand(ContractModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    payload: DirectorPlanPayload


class ShotPlanActivationCommand(ContractModel):
    expected_active_shot_plan_version_id: uuid.UUID | None = Field(
        alias="expectedActiveShotPlanVersionId", default=None
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class ShotPlanGenerationAttemptErrorDto(ContractModel):
    code: str
    message: str
    incomplete_reason: str | None = Field(alias="incompleteReason", default=None)
    request_id: str | None = Field(alias="requestId", default=None)
    retryable: bool = False
    submission_unknown: bool = Field(alias="submissionUnknown", default=False)


class DirectorValidationIssueDto(ContractModel):
    code: str
    severity: Literal["fatal", "blocking", "warning"]
    path: str
    message: str
    suggested_action: str | None = Field(alias="suggestedAction", default=None)
    provider_value: Any | None = Field(alias="providerValue", default=None)


class DirectorPlanDraftDto(ContractModel):
    target_duration_seconds: int | None = Field(alias="targetDurationSeconds", default=None)
    director_treatment: dict[str, Any] | None = Field(alias="directorTreatment", default=None)
    shots: list[dict[str, Any]] = Field(default_factory=list)


class ShotPlanGenerationResultDto(ContractModel):
    disposition: Literal["candidate_ready", "needs_input", "invalid"]
    result_shot_plan_version_id: uuid.UUID | None = Field(
        alias="resultShotPlanVersionId", default=None
    )
    recoverable: bool
    draft: DirectorPlanDraftDto | None = None
    issues: list[DirectorValidationIssueDto] = Field(default_factory=list)


class ShotPlanGenerationAttemptDto(ContractModel):
    job_id: uuid.UUID = Field(alias="jobId")
    status: JobStatus
    story_version_id: uuid.UUID = Field(alias="storyVersionId")
    base_shot_plan_version_id: uuid.UUID | None = Field(alias="baseShotPlanVersionId", default=None)
    result_shot_plan_version_id: uuid.UUID | None = Field(
        alias="resultShotPlanVersionId", default=None
    )
    provider: str | None = None
    model: str | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    actual_usage: dict[str, Any] | None = Field(alias="actualUsage", default=None)
    actual_cost_micros: int | None = Field(alias="actualCostMicros", default=None)
    billing_status: BillingStatus = Field(alias="billingStatus")
    error: ShotPlanGenerationAttemptErrorDto | None = None
    result: ShotPlanGenerationResultDto | None = None


class JobPublicationDto(ContractModel):
    id: uuid.UUID
    state: Literal["uploading", "ready", "delete_pending", "deleted", "failed"]
    public_host: str = Field(alias="publicHost")
    signed_url_expires_at: datetime | None = Field(alias="signedUrlExpiresAt", default=None)
    delete_after: datetime = Field(alias="deleteAfter")


class GenerationInputReferenceDto(ContractModel):
    asset_id: uuid.UUID | None = Field(alias="assetId", default=None)
    role: str
    priority: int = Field(ge=1)
    included: bool = True
    omitted_reason: str | None = Field(alias="omittedReason", default=None)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    derived: bool = False


class GenerationInputVideoReferenceDto(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    role: Literal["previous_episode_video"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    duration_seconds: float | None = Field(alias="durationSeconds", default=None, gt=0)
    included: bool


class GenerationVideoSpecDto(ContractModel):
    duration_seconds: int = Field(alias="durationSeconds", ge=4, le=15)
    resolution: Literal["480p"]
    aspect_ratio: Literal["9:16"] = Field(alias="aspectRatio")
    frame_rate: Literal[24] = Field(alias="frameRate")


class GenerationInputSourceDto(ContractModel):
    story_version_id: uuid.UUID | None = Field(alias="storyVersionId", default=None)
    shot_plan_version_id: uuid.UUID | None = Field(alias="shotPlanVersionId", default=None)
    selection_hash: str | None = Field(alias="selectionHash", default=None)
    base_video_asset_id: uuid.UUID | None = Field(alias="baseVideoAssetId", default=None)
    base_timeline_hash: str | None = Field(alias="baseTimelineHash", default=None)


class SegmentEditInputDto(ContractModel):
    instruction: str
    issue_range: FrameRange = Field(alias="issueRange")
    generation_range: FrameRange = Field(alias="generationRange")
    candidate_core_range: FrameRange = Field(alias="candidateCoreRange")


class GenerationInputSnapshotDto(ContractModel):
    schema_version: Literal[1, 2] = Field(alias="schemaVersion")
    kind: Literal["whole_video", "segment_edit"]
    state: Literal["preview", "submitted"]
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    prompt: str
    negative_prompt: str = Field(alias="negativePrompt")
    prompt_summary: str | None = Field(alias="promptSummary", default=None)
    prompt_sections: list[GenerationPromptSectionDto] = Field(
        alias="promptSections", default_factory=list
    )
    references: list[GenerationInputReferenceDto]
    video_references: list[GenerationInputVideoReferenceDto] = Field(
        alias="videoReferences", default_factory=list
    )
    video: GenerationVideoSpecDto
    source: GenerationInputSourceDto
    segment_edit: SegmentEditInputDto | None = Field(alias="segmentEdit", default=None)
    prompt_compiler_revision: str | None = Field(alias="promptCompilerRevision", default=None)
    created_at: datetime = Field(alias="createdAt")


class ImageGenerationInputSnapshotDto(ContractModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    state: Literal["preview", "submitted"]
    kind: Literal["environment"]
    subject_policy: Literal["empty_scene"] = Field(alias="subjectPolicy")
    source_story_version_id: uuid.UUID = Field(alias="sourceStoryVersionId")
    environment_intent: str = Field(alias="environmentIntent")
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    prompt: str
    negative_prompt: str = Field(alias="negativePrompt")
    references: list[GenerationInputReferenceDto]
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    prompt_compiler_revision: str = Field(alias="promptCompilerRevision")
    created_at: datetime = Field(alias="createdAt")


class JobDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID | None = Field(alias="projectId", default=None)
    series_id: uuid.UUID | None = Field(alias="seriesId", default=None)
    story_source_document_id: uuid.UUID | None = Field(alias="storySourceDocumentId", default=None)
    kind: Literal[
        "plan_story",
        "plan_shots",
        "plan_series",
        "plan_series_episode",
        "analyze_story_source",
        "extract_continuity_frames",
        "generate_image",
        "diagnose_image",
        "generate_video",
        "diagnose_video",
        "regenerate_video_segment",
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
    video_repair_id: uuid.UUID | None = Field(alias="videoRepairId", default=None)
    provider_submission_started_at: datetime | None = Field(
        alias="providerSubmissionStartedAt", default=None
    )
    provider_result: dict[str, Any] | None = Field(alias="providerResult", default=None)
    publication: JobPublicationDto | None = None
    actual_usage: dict[str, Any] | None = Field(alias="actualUsage", default=None)
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None)
    actual_cost_micros: int | None = Field(alias="actualCostMicros", default=None)
    currency: Literal["CNY"] = "CNY"
    billing_status: BillingStatus = Field(alias="billingStatus", default="pending")
    rate_card_revision: str | None = Field(alias="rateCardRevision", default=None)
    pricing_snapshot: dict[str, Any] | None = Field(alias="pricingSnapshot", default=None)
    provider_request_id: str | None = Field(alias="providerRequestId", default=None)
    input_snapshot: GenerationInputSnapshotDto | None = Field(alias="inputSnapshot", default=None)
    image_input_snapshot: ImageGenerationInputSnapshotDto | None = Field(
        alias="imageInputSnapshot", default=None
    )
    frozen_input: dict[str, Any] = Field(alias="frozenInput")
    result_asset_ids: list[uuid.UUID] = Field(alias="resultAssetIds", default_factory=list)
    supersedes_job_id: uuid.UUID | None = Field(alias="supersedesJobId", default=None)
    error: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class JobUsageDto(ContractModel):
    job_id: uuid.UUID = Field(alias="jobId")
    provider: str
    model: str
    input_tokens: int | None = Field(alias="inputTokens", default=None)
    output_tokens: int | None = Field(alias="outputTokens", default=None)
    completion_tokens: int | None = Field(alias="completionTokens", default=None)
    total_tokens: int | None = Field(alias="totalTokens", default=None)
    generated_images: int | None = Field(alias="generatedImages", default=None)
    generated_video_seconds: int | None = Field(alias="generatedVideoSeconds", default=None)
    provider_usage: dict[str, int] = Field(alias="providerUsage")
    billing_status: BillingStatus = Field(alias="billingStatus")
    calculated_cost_micros: int | None = Field(alias="calculatedCostMicros", default=None)
    currency: Literal["CNY"] = "CNY"
    rate_card_revision: str | None = Field(alias="rateCardRevision", default=None)
    price_source: str | None = Field(alias="priceSource", default=None)


class RateCardRevisionCreateCommand(ContractModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    revision: str = Field(min_length=1, max_length=80)
    source_url: str | None = Field(alias="sourceUrl", default=None, max_length=2_000)
    effective_from: datetime = Field(alias="effectiveFrom")
    rates: tuple[RateCardItem, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_metrics_are_unique(self) -> RateCardRevisionCreateCommand:
        metrics = [rate.metric for rate in self.rates]
        if len(metrics) != len(set(metrics)):
            raise ValueError("a rate-card revision cannot price the same metric twice")
        return self


class RateCardRevisionDto(RateCardRevisionCreateCommand):
    active: bool = True
    created_at: datetime = Field(alias="createdAt")


class ProjectUsageSummaryDto(ContractModel):
    project_id: uuid.UUID = Field(alias="projectId")
    jobs: list[JobUsageDto]
    totals: dict[str, int]
    calculated_cost_micros: int = Field(alias="calculatedCostMicros")
    unpriced_job_count: int = Field(alias="unpricedJobCount")
    currency: Literal["CNY"] = "CNY"


class JobEventDto(ContractModel):
    id: int
    job_id: uuid.UUID = Field(alias="jobId")
    project_id: uuid.UUID | None = Field(alias="projectId", default=None)
    series_id: uuid.UUID | None = Field(alias="seriesId", default=None)
    story_source_document_id: uuid.UUID | None = Field(alias="storySourceDocumentId", default=None)
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
    prompt_summary: str = Field(alias="promptSummary")
    prompt_sections: list[GenerationPromptSectionDto] = Field(alias="promptSections")
    references: list[CompiledReference]
    video_references: list[GenerationInputVideoReferenceDto] = Field(
        alias="videoReferences", default_factory=list
    )
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None)
    cost_estimate_status: Literal["priced", "unmetered_paid"] = Field(alias="costEstimateStatus")
    story_version_id: uuid.UUID = Field(alias="storyVersionId")
    shot_plan_version_id: uuid.UUID = Field(alias="shotPlanVersionId")
    selection_hash: str = Field(alias="selectionHash")
    duration_seconds: int = Field(alias="durationSeconds", ge=4, le=15)
    input_snapshot: GenerationInputSnapshotDto | None = Field(alias="inputSnapshot", default=None)
    series_episode_id: uuid.UUID | None = Field(alias="seriesEpisodeId", default=None)
    continuity_snapshot_id: uuid.UUID | None = Field(
        alias="continuitySnapshotId", default=None
    )
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
    cost_estimate_status: Literal["priced", "unmetered_paid"] = Field(alias="costEstimateStatus")
    image_input_snapshot: ImageGenerationInputSnapshotDto | None = Field(
        alias="imageInputSnapshot", default=None
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


EditDecisionListContract = EditDecisionListDto | EditDecisionListV2


class EditCreateCommand(ContractModel):
    edl: EditDecisionListDto


class EditVersionDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    revision: int
    source_selection_hash: str = Field(alias="sourceSelectionHash")
    edl: EditDecisionListContract
    status: Literal["draft", "rendered", "approved"]
    rendered_asset_id: uuid.UUID | None = Field(alias="renderedAssetId", default=None)
    parent_edit_version_id: uuid.UUID | None = Field(alias="parentEditVersionId", default=None)
    format_version: Literal[1, 2] = Field(alias="formatVersion", default=1)
    active: bool = False
    timeline_hash: str | None = Field(alias="timelineHash", default=None)
    created_at: datetime = Field(alias="createdAt")


SegmentReferenceRole = Literal[
    "anchor_in",
    "anchor_out",
    "episode_child",
    "episode_cat",
    "pair_scale",
    "environment",
    "style_board",
]


class SegmentRepairImageReferenceDto(ContractModel):
    role: SegmentReferenceRole
    asset_id: uuid.UUID | None = Field(alias="assetId", default=None)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    frame_number: int | None = Field(alias="frameNumber", default=None, ge=0)
    derived: bool = False


class SegmentRepairVideoReferenceDto(ContractModel):
    role: Literal["reference_video"]
    asset_id: uuid.UUID = Field(alias="assetId")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    range: FrameRange


VideoRepairStatus = Literal[
    "draft",
    "generating",
    "candidate_ready",
    "failed",
    "approved",
    "rejected",
    "outdated",
    "cancelled",
]


class SegmentRepairPreviewCommand(ContractModel):
    base_video_asset_id: uuid.UUID = Field(alias="baseVideoAssetId")
    base_edit_version_id: uuid.UUID | None = Field(alias="baseEditVersionId", default=None)
    issue_range: FrameRange = Field(alias="issueRange")
    instruction: str = Field(min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def require_supported_issue_duration(self) -> SegmentRepairPreviewCommand:
        if self.issue_range.duration_frames < MIN_ISSUE_FRAMES:
            raise ValueError("issueRange must be at least 4 seconds (96 frames)")
        if self.issue_range.duration_frames > MAX_ISSUE_FRAMES:
            raise ValueError("issueRange must not exceed 15 seconds (360 frames)")
        return self


class SegmentRepairPreviewDto(ContractModel):
    project_id: uuid.UUID = Field(alias="projectId")
    base_video_asset_id: uuid.UUID = Field(alias="baseVideoAssetId")
    base_edit_version_id: uuid.UUID | None = Field(alias="baseEditVersionId", default=None)
    base_timeline_hash: str = Field(alias="baseTimelineHash", pattern=r"^[a-f0-9]{64}$")
    frame_rate: RationalFrameRate = Field(alias="frameRate")
    issue_range: FrameRange = Field(alias="issueRange")
    generation_range: FrameRange = Field(alias="generationRange")
    candidate_core_range: FrameRange = Field(alias="candidateCoreRange")
    provider_duration_seconds: int = Field(alias="providerDurationSeconds", ge=4, le=15)
    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    instruction: str
    prompt: str
    negative_prompt: str = Field(alias="negativePrompt")
    image_references: list[SegmentRepairImageReferenceDto] = Field(alias="imageReferences")
    video_reference: SegmentRepairVideoReferenceDto = Field(alias="videoReference")
    expected_cost_micros: int | None = Field(alias="expectedCostMicros", default=None)
    cost_estimate_status: Literal["priced", "unmetered_paid"] = Field(alias="costEstimateStatus")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    input_snapshot: GenerationInputSnapshotDto | None = Field(alias="inputSnapshot", default=None)


class VideoRepairDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    base_video_asset_id: uuid.UUID = Field(alias="baseVideoAssetId")
    base_edit_version_id: uuid.UUID | None = Field(alias="baseEditVersionId", default=None)
    base_timeline_hash: str = Field(alias="baseTimelineHash")
    frame_rate: RationalFrameRate = Field(alias="frameRate")
    issue_range: FrameRange = Field(alias="issueRange")
    generation_range: FrameRange = Field(alias="generationRange")
    candidate_core_range: FrameRange = Field(alias="candidateCoreRange")
    provider_duration_seconds: int = Field(alias="providerDurationSeconds")
    selection_policy_version: int = Field(alias="selectionPolicyVersion", ge=1, default=2)
    legacy_edit_intent: Literal["action", "character", "object", "environment", "style"] | None = (
        Field(alias="legacyEditIntent", default=None)
    )
    instruction: str
    prompt: str
    negative_prompt: str = Field(alias="negativePrompt")
    input_hash: str = Field(alias="inputHash")
    status: VideoRepairStatus
    candidate_asset_id: uuid.UUID | None = Field(alias="candidateAssetId", default=None)
    approved_candidate_asset_id: uuid.UUID | None = Field(
        alias="approvedCandidateAssetId", default=None
    )
    approved_edit_version_id: uuid.UUID | None = Field(alias="approvedEditVersionId", default=None)
    approval_idempotency_key: str | None = Field(alias="approvalIdempotencyKey", default=None)
    preview: SegmentRepairPreviewDto
    created_at: datetime = Field(alias="createdAt")
    approved_at: datetime | None = Field(alias="approvedAt", default=None)


class SegmentRepairCreateCommand(ContractModel):
    base_video_asset_id: uuid.UUID = Field(alias="baseVideoAssetId")
    base_edit_version_id: uuid.UUID | None = Field(alias="baseEditVersionId", default=None)
    issue_range: FrameRange = Field(alias="issueRange")
    instruction: str = Field(min_length=1, max_length=4_000)
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)

    @model_validator(mode="after")
    def require_supported_issue_duration(self) -> SegmentRepairCreateCommand:
        if self.issue_range.duration_frames < MIN_ISSUE_FRAMES:
            raise ValueError("issueRange must be at least 4 seconds (96 frames)")
        if self.issue_range.duration_frames > MAX_ISSUE_FRAMES:
            raise ValueError("issueRange must not exceed 15 seconds (360 frames)")
        return self


class SegmentRepairTransitionCommand(ContractModel):
    type: Literal["cut", "dissolve"]
    duration_frames: Literal[0, 2, 4, 6] = Field(alias="durationFrames")


class SegmentRepairApproveCommand(ContractModel):
    candidate_asset_id: uuid.UUID = Field(alias="candidateAssetId")
    candidate_source_range: FrameRange = Field(alias="candidateSourceRange")
    transition: SegmentRepairTransitionCommand
    expected_base_timeline_hash: str = Field(
        alias="expectedBaseTimelineHash", pattern=r"^[a-f0-9]{64}$"
    )
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    quality_checks: dict[str, Literal["pass", "warning", "fail"]] = Field(alias="qualityChecks")
    seam_checks: dict[str, Literal["pass", "warning", "fail"]] = Field(alias="seamChecks")


class ExportCommand(ContractModel):
    edit_version_id: uuid.UUID = Field(alias="editVersionId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class FinalSelectionCommand(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")


class StudioRepository(Protocol):
    def active_canon_profile_id(self) -> uuid.UUID: ...

    def publish_rate_card(self, command: RateCardRevisionCreateCommand) -> RateCardRevisionDto: ...

    def list_rate_cards(self) -> list[RateCardRevisionDto]: ...

    def current_canon_profile(self) -> CanonProfileDto: ...

    def register_canon_asset(
        self,
        *,
        role: FixedCanonRole,
        sha256: str,
        storage_key: str,
        byte_size: int,
    ) -> StoredAssetDto: ...

    def publish_canon_revision(self, command: CanonRevisionCreateCommand) -> CanonProfileDto: ...

    def get_validation_run(self, run_id: uuid.UUID) -> ValidationRunDto | None: ...

    def latest_validation_run(self) -> ValidationRunDto | None: ...

    def create_project(
        self, draft: ProjectCreate, *, canon_profile_id: uuid.UUID
    ) -> ProjectDto: ...

    def list_projects(self) -> list[ProjectDto]: ...

    def get_project(self, project_id: uuid.UUID) -> ProjectDto | None: ...

    def create_story_series(
        self, command: SeriesCreateCommand, *, canon_profile_id: uuid.UUID
    ) -> StorySeriesDto: ...

    def list_story_series(self) -> list[StorySeriesDto]: ...

    def get_story_series(self, series_id: uuid.UUID) -> StorySeriesDto | None: ...

    def update_story_series(
        self, series_id: uuid.UUID, command: SeriesPatchCommand
    ) -> StorySeriesDto: ...

    def create_series_plan_version(
        self,
        series_id: uuid.UUID,
        *,
        plan: SeriesPlanDraft,
        input_hash: str,
        prompt_revision: str,
        producing_job_id: uuid.UUID,
        validation_issues: list[SeriesValidationIssueDto] | None = None,
    ) -> SeriesPlanVersionDto: ...

    def materialize_series_plan_version(
        self,
        series_id: uuid.UUID,
        *,
        base_plan_version_id: uuid.UUID,
        plan: SeriesPlanDraft,
        idempotency_key: str,
    ) -> SeriesPlanVersionDto: ...

    def list_series_plan_versions(self, series_id: uuid.UUID) -> list[SeriesPlanVersionDto]: ...

    def activate_series_plan_version(
        self,
        series_id: uuid.UUID,
        plan_version_id: uuid.UUID,
        *,
        expected_active_plan_version_id: uuid.UUID | None,
        idempotency_key: str,
    ) -> SeriesPlanVersionDto: ...

    def reject_series_plan_version(
        self, series_id: uuid.UUID, plan_version_id: uuid.UUID
    ) -> SeriesPlanVersionDto: ...

    def list_series_episodes(self, series_id: uuid.UUID) -> list[SeriesEpisodeDto]: ...

    def list_series_jobs(self, series_id: uuid.UUID) -> list[JobDto]: ...

    def materialize_series_episode(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> ProjectDto: ...

    def list_episode_continuity(
        self, episode_id: uuid.UUID
    ) -> list[EpisodeContinuitySnapshotDto]: ...

    def confirm_episode_continuity(
        self, episode_id: uuid.UUID, command: EpisodeContinuityConfirmCommand
    ) -> EpisodeContinuitySnapshotDto: ...

    def reset_episode_continuity(
        self, episode_id: uuid.UUID, command: EpisodeContinuityResetCommand
    ) -> EpisodeContinuitySnapshotDto: ...

    def series_episode_for_project(self, project_id: uuid.UUID) -> SeriesEpisodeDto | None: ...

    def list_series_asset_bindings(
        self, series_id: uuid.UUID
    ) -> list[SeriesAssetBindingDto]: ...

    def replace_series_asset_bindings(
        self, series_id: uuid.UUID, command: SeriesAssetBindingsPatchCommand
    ) -> list[SeriesAssetBindingDto]: ...

    def replace_continuity_keyframes(
        self, project_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> list[AssetDto]: ...

    def save_episode_reference_manifest(
        self,
        episode_id: uuid.UUID,
        job_id: uuid.UUID,
        continuity_snapshot_id: uuid.UUID | None,
        references: list[dict[str, Any]],
    ) -> None: ...

    def find_story_source_document(self, *, content_hash: str) -> StorySourceDocumentDto | None: ...

    def list_story_source_documents(self) -> list[StorySourceDocumentDto]: ...

    def get_story_source_document(
        self, document_id: uuid.UUID
    ) -> StorySourceDocumentDto | None: ...

    def create_story_source_document(
        self,
        command: StoryImportCreateCommand,
        *,
        document_id: uuid.UUID,
        content_hash: str,
        job: JobDto,
    ) -> StorySourceDocumentDto: ...

    def complete_story_source_analysis(
        self, job_id: uuid.UUID, analysis: StoryImportAnalysisDraft
    ) -> StorySourceDocumentDto: ...

    def restart_story_source_analysis(
        self, document_id: uuid.UUID, job: JobDto
    ) -> JobDto: ...

    def confirm_story_source(
        self, document_id: uuid.UUID, command: StoryImportConfirmCommand
    ) -> StoryImportMaterializationDto: ...

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
        self,
        project_id: uuid.UUID,
        draft: ShotPlanDraft,
        *,
        active: bool = True,
        review_status: Literal["accepted", "candidate", "rejected", "superseded"] = "accepted",
        producing_job_id: uuid.UUID | None = None,
        base_shot_plan_version_id: uuid.UUID | None = None,
    ) -> ShotPlanVersionDto: ...

    def active_shot_plan(self, project_id: uuid.UUID) -> ShotPlanVersionDto | None: ...

    def list_shot_plans(self, project_id: uuid.UUID) -> list[ShotPlanVersionDto]: ...

    def activate_shot_plan(
        self,
        project_id: uuid.UUID,
        shot_plan_id: uuid.UUID,
        *,
        expected_active_shot_plan_version_id: uuid.UUID | None,
    ) -> ShotPlanVersionDto: ...

    def reject_shot_plan(
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
        metadata: dict[str, Any] | None = None,
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

    def list_assets(self, project_id: uuid.UUID) -> list[AssetDto]: ...

    def get_asset(self, asset_id: uuid.UUID) -> StoredAssetDto | None: ...

    def create_job(self, job: JobDto) -> JobDto: ...

    def get_job(self, job_id: uuid.UUID) -> JobDto | None: ...

    def record_director_validation(
        self, job_id: uuid.UUID, validation: dict[str, object]
    ) -> JobDto: ...

    def record_series_plan_validation(
        self, job_id: uuid.UUID, validation: dict[str, object]
    ) -> JobDto: ...

    def list_project_jobs(self, project_id: uuid.UUID) -> list[JobDto]: ...

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

    def active_edit(self, project_id: uuid.UUID) -> EditVersionDto | None: ...

    def create_video_repair(self, repair: VideoRepairDto) -> VideoRepairDto: ...

    def create_video_repair_job(self, repair: VideoRepairDto, job: JobDto) -> JobDto: ...

    def get_video_repair(self, repair_id: uuid.UUID) -> VideoRepairDto | None: ...

    def list_video_repairs(self, project_id: uuid.UUID) -> list[VideoRepairDto]: ...

    def set_video_repair_status(
        self,
        repair_id: uuid.UUID,
        *,
        status: VideoRepairStatus,
        candidate_asset_id: uuid.UUID | None = None,
    ) -> VideoRepairDto: ...

    def approve_video_repair(
        self,
        repair_id: uuid.UUID,
        *,
        edl: EditDecisionListV2,
        source_selection_hash: str,
        parent_edit_version_id: uuid.UUID | None,
        candidate_asset_id: uuid.UUID,
        candidate_source_range: FrameRange,
        idempotency_key: str,
    ) -> EditVersionDto: ...

    def list_edits(self, project_id: uuid.UUID) -> list[EditVersionDto]: ...

    def get_edit(self, edit_id: uuid.UUID) -> EditVersionDto | None: ...


class StudioService:
    def __init__(
        self,
        repository: StudioRepository,
        *,
        provider_runtime: ProviderRuntime | None = None,
        project_library_repository: ProjectLibraryRepository | None = None,
    ) -> None:
        self._repository = repository
        self._provider_runtime = provider_runtime or ProviderRuntime.from_env(
            segment_reference_publishing_ready=False
        )
        if project_library_repository is not None:
            self._project_library_repository: ProjectLibraryRepository | None = (
                project_library_repository
            )
        elif isinstance(repository, ProjectLibraryRepository):
            self._project_library_repository = repository
        else:
            self._project_library_repository = None

    @property
    def provider_runtime(self) -> ProviderRuntime:
        return self._provider_runtime

    def list_rate_cards(self) -> list[RateCardRevisionDto]:
        return self._repository.list_rate_cards()

    def publish_rate_card(self, command: RateCardRevisionCreateCommand) -> RateCardRevisionDto:
        return self._repository.publish_rate_card(command)

    def get_validation_run(self, run_id: uuid.UUID) -> ValidationRunDto:
        run = self._repository.get_validation_run(run_id)
        if run is None:
            raise StudioNotFoundError("validation run not found")
        return run

    def current_validation_run(self) -> ValidationRunDto | None:
        return self._repository.latest_validation_run()

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

    def publish_canon_revision(self, command: CanonRevisionCreateCommand) -> CanonProfileDto:
        return self._repository.publish_canon_revision(command)

    def list_projects(self) -> list[ProjectDto]:
        return self._repository.list_projects()

    def create_story_series(self, command: SeriesCreateCommand) -> StorySeriesDto:
        return self._repository.create_story_series(
            command, canon_profile_id=self._repository.active_canon_profile_id()
        )

    def list_story_series(self) -> list[StorySeriesDto]:
        return self._repository.list_story_series()

    def get_story_series(self, series_id: uuid.UUID) -> StorySeriesDto:
        series = self._repository.get_story_series(series_id)
        if series is None:
            raise StudioNotFoundError("story series not found")
        return series

    def update_story_series(
        self, series_id: uuid.UUID, command: SeriesPatchCommand
    ) -> StorySeriesDto:
        self.get_story_series(series_id)
        return self._repository.update_story_series(series_id, command)

    def preview_series_plan(self, series_id: uuid.UUID) -> SeriesPlanPreviewDto:
        series = self.get_story_series(series_id)
        canon = self._repository.current_canon_profile()
        if canon.id != series.canon_profile_id:
            raise StudioConflictError("series Canon changed")
        return compile_series_plan_preview(
            series,
            canon_profile_hash=canon.profile_hash,
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.planning_model,
            capability_revision=self._provider_runtime.capability_revision,
        )

    def create_series_plan_job(
        self, series_id: uuid.UUID, command: SeriesPlanGenerationCommand
    ) -> JobDto:
        preview = self.preview_series_plan(series_id)
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("series planning input changed")
        self._require_paid_calls_enabled()
        now = datetime.now(UTC)
        return self._create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=None,
                seriesId=series_id,
                kind="plan_series",
                status="queued",
                inputHash=preview.input_hash,
                idempotencyKey=command.idempotency_key,
                provider=preview.provider,
                model=preview.model,
                frozenInput={
                    "seriesId": str(series_id),
                    "canonProfileId": str(self.get_story_series(series_id).canon_profile_id),
                    "plannedEpisodeCount": preview.planned_episode_count,
                    "defaultEpisodeDurationSeconds": preview.default_episode_duration_seconds,
                    "prompt": preview.prompt,
                    "outputSchema": preview.output_schema,
                    "seriesPlannerPromptRevision": preview.prompt_revision,
                    "capabilityRevision": preview.capability_revision,
                },
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def complete_series_plan_job(
        self,
        job_id: uuid.UUID,
        plan: SeriesPlanDraft,
        *,
        validation_issues: list[SeriesValidationIssueDto] | None = None,
    ) -> SeriesPlanVersionDto:
        job = self.get_job(job_id)
        if job.kind != "plan_series" or job.series_id is None:
            raise StudioConflictError("job is not a series planning job")
        existing = next(
            (
                version
                for version in self._repository.list_series_plan_versions(job.series_id)
                if version.producing_job_id == job_id
            ),
            None,
        )
        if existing is not None:
            return existing
        return self._repository.create_series_plan_version(
            job.series_id,
            plan=plan,
            input_hash=job.input_hash,
            prompt_revision=str(job.frozen_input.get("seriesPlannerPromptRevision", "")),
            producing_job_id=job.id,
            validation_issues=validation_issues,
        )

    def record_series_plan_validation(
        self, job_id: uuid.UUID, validation: dict[str, object]
    ) -> JobDto:
        job = self.get_job(job_id)
        if job.kind != "plan_series":
            raise StudioConflictError("job is not a series planning job")
        return self._repository.record_series_plan_validation(job_id, validation)

    def list_series_plan_versions(self, series_id: uuid.UUID) -> list[SeriesPlanVersionDto]:
        self.get_story_series(series_id)
        return self._repository.list_series_plan_versions(series_id)

    def materialize_series_plan(
        self,
        series_id: uuid.UUID,
        plan_version_id: uuid.UUID,
        command: SeriesPlanMaterializeCommand,
    ) -> SeriesPlanVersionDto:
        self.get_story_series(series_id)
        if command.base_plan_version_id != plan_version_id:
            raise StudioConflictError("base series plan version changed")
        return self._repository.materialize_series_plan_version(
            series_id,
            base_plan_version_id=plan_version_id,
            plan=command.plan,
            idempotency_key=command.idempotency_key,
        )

    def activate_series_plan(
        self,
        series_id: uuid.UUID,
        plan_version_id: uuid.UUID,
        command: SeriesPlanActivationCommand,
    ) -> SeriesPlanVersionDto:
        self.get_story_series(series_id)
        return self._repository.activate_series_plan_version(
            series_id,
            plan_version_id,
            expected_active_plan_version_id=command.expected_active_plan_version_id,
            idempotency_key=command.idempotency_key,
        )

    def reject_series_plan(
        self, series_id: uuid.UUID, plan_version_id: uuid.UUID
    ) -> SeriesPlanVersionDto:
        self.get_story_series(series_id)
        return self._repository.reject_series_plan_version(series_id, plan_version_id)

    def list_series_episodes(self, series_id: uuid.UUID) -> list[SeriesEpisodeDto]:
        self.get_story_series(series_id)
        return self._repository.list_series_episodes(series_id)

    def project_series_context(self, project_id: uuid.UUID) -> ProjectSeriesContextDto | None:
        self._require_project(project_id)
        episode = self._repository.series_episode_for_project(project_id)
        if episode is None:
            return None
        series = self.get_story_series(episode.series_id)
        return ProjectSeriesContextDto(
            series=series,
            episode=episode,
            episodes=self._repository.list_series_episodes(series.id),
        )

    def list_series_jobs(self, series_id: uuid.UUID) -> list[JobDto]:
        self.get_story_series(series_id)
        return self._repository.list_series_jobs(series_id)

    def list_project_jobs(self, project_id: uuid.UUID) -> list[JobDto]:
        self._require_project(project_id)
        return self._repository.list_project_jobs(project_id)

    def materialize_series_episode(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        command: SeriesEpisodeMaterializeCommand,
    ) -> ProjectDto:
        self.get_story_series(series_id)
        return self._repository.materialize_series_episode(
            series_id,
            episode_id,
            idempotency_key=command.idempotency_key,
        )

    def preview_series_episode_story(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        *,
        additional_notes: str | None = None,
    ) -> SeriesEpisodeStoryPreviewDto:
        series = self.get_story_series(series_id)
        if series.active_plan_version_id is None:
            raise StudioConflictError("series plan must be adopted before episode planning")
        active_plan = next(
            (
                item
                for item in self._repository.list_series_plan_versions(series_id)
                if item.id == series.active_plan_version_id and item.active
            ),
            None,
        )
        episode = next(
            (
                item
                for item in self._repository.list_series_episodes(series_id)
                if item.id == episode_id
            ),
            None,
        )
        if active_plan is None or episode is None:
            raise StudioNotFoundError("series episode or active plan not found")
        if episode.project_id is None:
            raise StudioConflictError("start episode production before planning its story")
        previous = next(
            (
                item
                for item in self._repository.list_series_episodes(series_id)
                if item.order == episode.order - 1
            ),
            None,
        )
        incoming = None
        if previous is not None:
            carryover = "；".join(previous.outline.continuity_carryover)
            incoming = previous.outline.ending_state
            if carryover:
                incoming = f"{incoming}；需要承接：{carryover}"
        canon = self._repository.current_canon_profile()
        if canon.id != series.canon_profile_id:
            raise StudioConflictError("series Canon changed")
        return compile_series_episode_story_preview(
            series=series,
            active_plan=active_plan,
            episode=episode,
            incoming_continuity=incoming,
            additional_notes=additional_notes,
            canon_profile_hash=canon.profile_hash,
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.planning_model,
            capability_revision=self._provider_runtime.capability_revision,
        )

    def create_series_episode_story_job(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        command: SeriesEpisodeStoryGenerationCommand,
    ) -> JobDto:
        preview = self.preview_series_episode_story(
            series_id,
            episode_id,
            additional_notes=command.additional_notes,
        )
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("series episode story input changed")
        self._require_paid_calls_enabled()
        now = datetime.now(UTC)
        return self._create_job(
            self._with_pricing_snapshot(
                JobDto(
                    id=uuid.uuid4(),
                    projectId=preview.project_id,
                    kind="plan_series_episode",
                    status="queued",
                    inputHash=preview.input_hash,
                    idempotencyKey=command.idempotency_key,
                    provider=preview.provider,
                    model=preview.model,
                    frozenInput={
                        "seriesId": str(preview.series_id),
                        "seriesPlanVersionId": str(preview.series_plan_version_id),
                        "seriesEpisodeId": str(preview.series_episode_id),
                        "episodeOutlineVersionId": str(preview.episode_outline_version_id),
                        "incomingContinuity": preview.incoming_continuity,
                        "additionalNotes": command.additional_notes,
                        "prompt": preview.prompt,
                        "outputSchema": preview.output_schema,
                        "seriesEpisodePlannerPromptRevision": preview.prompt_revision,
                        "capabilityRevision": preview.capability_revision,
                    },
                    resultAssetIds=[],
                    createdAt=now,
                    updatedAt=now,
                )
            )
        )

    def complete_series_episode_story_job(
        self, job_id: uuid.UUID, proposal: LifeStoryProposalDraft
    ) -> LifeStoryProposalDto:
        job = self.get_job(job_id)
        if job.kind != "plan_series_episode":
            raise StudioConflictError("job is not a series episode planning job")
        return self._repository.complete_planner_job(job_id, proposal)

    def get_series_episode_continuity(
        self, series_id: uuid.UUID, episode_id: uuid.UUID
    ) -> EpisodeContinuityDto:
        episodes = self.list_series_episodes(series_id)
        episode = next((item for item in episodes if item.id == episode_id), None)
        if episode is None:
            raise StudioNotFoundError("series episode not found")
        previous = next(
            (item for item in episodes if item.order == episode.order - 1), None
        )
        snapshots = self._repository.list_episode_continuity(episode_id)
        return EpisodeContinuityDto(
            episodeId=episode_id,
            previousEpisodeId=previous.id if previous is not None else None,
            incoming=next(
                (
                    item
                    for item in snapshots
                    if item.direction == "incoming" and item.active
                ),
                None,
            ),
            outgoing=next(
                (
                    item
                    for item in snapshots
                    if item.direction == "outgoing" and item.active
                ),
                None,
            ),
        )

    def confirm_series_episode_continuity(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        command: EpisodeContinuityConfirmCommand,
    ) -> EpisodeContinuitySnapshotDto:
        self.get_series_episode_continuity(series_id, episode_id)
        return self._repository.confirm_episode_continuity(episode_id, command)

    def reset_series_episode_continuity(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        command: EpisodeContinuityResetCommand,
    ) -> EpisodeContinuitySnapshotDto:
        self.get_series_episode_continuity(series_id, episode_id)
        return self._repository.reset_episode_continuity(episode_id, command)

    def list_series_assets(self, series_id: uuid.UUID) -> list[SeriesAssetBindingDto]:
        self.get_story_series(series_id)
        return self._repository.list_series_asset_bindings(series_id)

    def update_series_assets(
        self, series_id: uuid.UUID, command: SeriesAssetBindingsPatchCommand
    ) -> list[SeriesAssetBindingDto]:
        self.get_story_series(series_id)
        for binding in command.bindings:
            self.get_asset(binding.asset_id)
        return self._repository.replace_series_asset_bindings(series_id, command)

    def get_episode_continuity_frames(
        self, series_id: uuid.UUID, episode_id: uuid.UUID
    ) -> EpisodeContinuityFramesDto:
        episode = next(
            (item for item in self.list_series_episodes(series_id) if item.id == episode_id),
            None,
        )
        if episode is None:
            raise StudioNotFoundError("series episode not found")
        if episode.project_id is None:
            return EpisodeContinuityFramesDto(episodeId=episode.id)
        selections = self._repository.current_selections(episode.project_id)
        final = selections.get("final")
        if final is None:
            return EpisodeContinuityFramesDto(episodeId=episode.id)
        matching = [
            asset
            for asset in self._repository.list_assets(episode.project_id)
            if asset.media_type == "image"
            and asset.metadata.get("seriesEpisodeId") == str(episode.id)
            and asset.metadata.get("sourceVideoAssetId") == str(final.id)
        ]
        last_frame = next(
            (asset for asset in matching if asset.role == "episode_last_frame"),
            None,
        )
        candidates = [asset for asset in matching if asset.role == "episode_keyframe"]
        selected_keyframes = [
            selections[slot]
            for slot in ("continuity_keyframe_1", "continuity_keyframe_2")
            if slot in selections
            and selections[slot].id in {asset.id for asset in candidates}
        ]
        return EpisodeContinuityFramesDto(
            episodeId=episode.id,
            sourceVideoAssetId=final.id,
            lastFrame=last_frame,
            candidates=candidates,
            selectedKeyframes=selected_keyframes,
        )

    def select_episode_continuity_keyframes(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        command: EpisodeContinuityKeyframesCommand,
    ) -> list[AssetDto]:
        frames = self.get_episode_continuity_frames(series_id, episode_id)
        episode = next(
            item for item in self.list_series_episodes(series_id) if item.id == episode_id
        )
        if episode.project_id is None or frames.source_video_asset_id is None:
            raise StudioConflictError("episode has no selected final video")
        candidates = {asset.id: asset for asset in frames.candidates}
        if any(asset_id not in candidates for asset_id in command.asset_ids):
            raise StudioConflictError("continuity keyframe is not a candidate for this final video")
        return self._repository.replace_continuity_keyframes(
            episode.project_id, command.asset_ids
        )

    def preview_story_import(self, command: StoryImportPreviewCommand) -> StoryImportPreviewDto:
        provisional = compile_story_import_preview(
            command,
            duplicate_document_id=None,
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.planning_model,
            capability_revision=self._provider_runtime.capability_revision,
        )
        duplicate = self._repository.find_story_source_document(
            content_hash=provisional.content_hash
        )
        return provisional.model_copy(
            update={"duplicate_document_id": duplicate.id if duplicate is not None else None}
        )

    def create_story_import(self, command: StoryImportCreateCommand) -> StoryImportCreateResultDto:
        preview = self.preview_story_import(
            StoryImportPreviewCommand(
                rawText=command.raw_text,
                sourceFormat=command.source_format,
                fileName=command.file_name,
            )
        )
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("story import input changed")
        if preview.duplicate_document_id is not None:
            document = self.get_story_import(preview.duplicate_document_id)
            job = (
                self.get_job(document.analysis_job_id)
                if document.analysis_job_id is not None
                else None
            )
            return StoryImportCreateResultDto(
                document=document,
                analysisJob=_story_import_job(job) if job is not None else None,
                reused=True,
            )
        self._require_paid_calls_enabled()
        document_id = uuid.uuid4()
        job = self._build_story_import_analysis_job(
            document_id=document_id,
            preview=preview,
            source_format=command.source_format,
            file_name=command.file_name,
            idempotency_key=command.idempotency_key,
        )
        document = self._repository.create_story_source_document(
            command,
            document_id=document_id,
            content_hash=preview.content_hash,
            job=job,
        )
        persisted_job = (
            self.get_job(document.analysis_job_id)
            if document.analysis_job_id is not None
            else None
        )
        return StoryImportCreateResultDto(
            document=document,
            analysisJob=(
                _story_import_job(persisted_job) if persisted_job is not None else None
            ),
            reused=document.id != document_id,
        )

    def list_story_imports(self) -> list[StorySourceDocumentDto]:
        return self._repository.list_story_source_documents()

    def get_story_import(self, document_id: uuid.UUID) -> StorySourceDocumentDto:
        document = self._repository.get_story_source_document(document_id)
        if document is None:
            raise StudioNotFoundError("story source document not found")
        return document

    def reanalyze_story_import(
        self, document_id: uuid.UUID, command: StoryImportReanalyzeCommand
    ) -> JobDto:
        document = self.get_story_import(document_id)
        preview = self.preview_story_import(
            StoryImportPreviewCommand(
                rawText=document.raw_text,
                sourceFormat=document.source_format,
                fileName=document.file_name,
            )
        )
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("story import input changed")
        self._require_paid_calls_enabled()
        job = self._build_story_import_analysis_job(
            document_id=document.id,
            preview=preview,
            source_format=document.source_format,
            file_name=document.file_name,
            idempotency_key=command.idempotency_key,
        )
        return self._repository.restart_story_source_analysis(document.id, job)

    def _build_story_import_analysis_job(
        self,
        *,
        document_id: uuid.UUID,
        preview: StoryImportPreviewDto,
        source_format: str,
        file_name: str | None,
        idempotency_key: str,
    ) -> JobDto:
        now = datetime.now(UTC)
        return self._with_pricing_snapshot(
            JobDto(
                id=uuid.uuid4(),
                projectId=None,
                storySourceDocumentId=document_id,
                kind="analyze_story_source",
                status="queued",
                inputHash=preview.input_hash,
                idempotencyKey=idempotency_key,
                provider=self._provider_runtime.provider,
                model=self._provider_runtime.planning_model,
                frozenInput={
                    "storySourceDocumentId": str(document_id),
                    "contentHash": preview.content_hash,
                    "sourceFormat": source_format,
                    "fileName": file_name,
                    "prompt": preview.prompt,
                    "outputSchema": preview.output_schema,
                    "storySourceAnalyzerPromptRevision": preview.prompt_revision,
                    "capabilityRevision": self._provider_runtime.capability_revision,
                },
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def complete_story_import_analysis(
        self, job_id: uuid.UUID, analysis: StoryImportAnalysisDraft
    ) -> StorySourceDocumentDto:
        job = self.get_job(job_id)
        if job.kind != "analyze_story_source" or job.story_source_document_id is None:
            raise StudioConflictError("job is not a story source analysis job")
        return self._repository.complete_story_source_analysis(job_id, analysis)

    def confirm_story_import(
        self, document_id: uuid.UUID, command: StoryImportConfirmCommand
    ) -> StoryImportMaterializationDto:
        self.get_story_import(document_id)
        return self._repository.confirm_story_source(document_id, command)

    def project_library(self, query: ProjectLibraryQuery) -> ProjectLibraryPageDto:
        return self._require_project_library_repository().list_project_library(query)

    def list_project_collections(self) -> list[ProjectCollectionDto]:
        return self._require_project_library_repository().list_project_collections()

    def create_project_collection(self, command: ProjectCollectionCreate) -> ProjectCollectionDto:
        try:
            return self._require_project_library_repository().create_project_collection(command)
        except StudioConflictError:
            raise
        except ValueError as exc:
            raise StudioValidationError(str(exc)) from exc

    def update_project_collection(
        self, collection_id: uuid.UUID, command: ProjectCollectionPatch
    ) -> ProjectCollectionDto:
        try:
            return self._require_project_library_repository().update_project_collection(
                collection_id, command
            )
        except StudioConflictError:
            raise
        except ValueError as exc:
            raise StudioValidationError(str(exc)) from exc

    def archive_project_collection(
        self, collection_id: uuid.UUID, *, archived: bool
    ) -> ProjectCollectionDto:
        return self._require_project_library_repository().set_project_collection_archived(
            collection_id, archived=archived
        )

    def list_project_tags(self, *, query: str | None = None) -> list[dict[str, object]]:
        try:
            return self._require_project_library_repository().list_project_tags(query=query)
        except ValueError as exc:
            raise StudioValidationError(str(exc)) from exc

    def organize_project(
        self, project_id: uuid.UUID, command: ProjectOrganizationCommand
    ) -> ProjectLibraryItemDto:
        self._require_project(project_id)
        try:
            return self._require_project_library_repository().organize_project(project_id, command)
        except StudioConflictError:
            raise
        except ValueError as exc:
            raise StudioValidationError(str(exc)) from exc

    def apply_project_library_action(
        self, command: ProjectLibraryBatchActionCommand
    ) -> ProjectLibraryBatchResultDto:
        try:
            return self._require_project_library_repository().apply_project_library_action(command)
        except StudioConflictError:
            raise
        except ValueError as exc:
            raise StudioValidationError(str(exc)) from exc

    def _require_project_library_repository(self) -> ProjectLibraryRepository:
        if self._project_library_repository is None:
            raise RuntimeError("project library repository is not configured")
        return self._project_library_repository

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
        self._require_paid_calls_enabled()
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
                "plannerPromptRevision": "catflow-life-planner-v2",
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
            expectedCostMicros=None,
            frozenInput={
                "text": command.text,
                "contextRevision": snapshot.context_revision,
                "sessionId": str(snapshot.session_id),
                "targetDurationSeconds": project.target_duration_seconds,
                "prompt": prompt,
                "outputSchema": output_schema,
                "plannerPromptRevision": "catflow-life-planner-v2",
                "capabilityRevision": self._provider_runtime.capability_revision,
            },
            resultAssetIds=[],
            createdAt=now,
            updatedAt=now,
        )
        job = self._with_pricing_snapshot(job)
        return self._repository.enqueue_planner_message(project_id, command, job=job)

    def complete_planner_job(
        self, job_id: uuid.UUID, proposal: LifeStoryProposalDraft
    ) -> LifeStoryProposalDto:
        return self._repository.complete_planner_job(job_id, proposal)

    def complete_shot_plan_job(
        self, job_id: uuid.UUID, payload: DirectorPlanPayload
    ) -> ShotPlanVersionDto:
        job = self.get_job(job_id)
        if job.kind != "plan_shots":
            raise StudioConflictError("job is not a director planning job")
        project_id = job.project_id
        story_id = uuid.UUID(str(job.frozen_input.get("storyVersionId", "")))
        selection_hash = str(job.frozen_input.get("selectionHash", ""))
        clip = LifeClipSpec.model_validate(job.frozen_input.get("clip"))
        if payload.target_duration_seconds != clip.duration_seconds:
            raise StudioConflictError("director output duration changed")
        existing = next(
            (
                plan
                for plan in self._repository.list_shot_plans(project_id)
                if plan.producing_job_id == job_id
            ),
            None,
        )
        if existing is not None:
            return existing
        base_value = job.frozen_input.get("baseShotPlanVersionId")
        base_shot_plan_version_id = uuid.UUID(str(base_value)) if base_value else None
        draft = ProfessionalShotPlanDraft(
            sourceStoryVersionId=story_id,
            sourceSelectionHash=selection_hash,
            clip=clip,
            shots=[synchronize_professional_shot_summaries(shot) for shot in payload.shots],
            directorTreatment=payload.director_treatment,
            directorPromptRevision=str(job.frozen_input.get("directorPromptRevision", "")),
            directorModel=job.model or "unknown",
            directorInputHash=job.input_hash,
        )
        current_story = self._repository.active_story(project_id)
        current_plan = self._repository.active_shot_plan(project_id)
        current_plan_id = current_plan.id if current_plan is not None else None
        current_inputs_match = (
            current_story is not None
            and current_story.id == story_id
            and self.current_selection_hash(project_id) == selection_hash
            and current_plan_id == base_shot_plan_version_id
        )
        return self._repository.create_shot_plan(
            project_id,
            draft,
            active=False,
            review_status="candidate" if current_inputs_match else "superseded",
            producing_job_id=job_id,
            base_shot_plan_version_id=base_shot_plan_version_id,
        )

    def record_shot_plan_generation_validation(
        self, job_id: uuid.UUID, result: DirectorNormalizationResult
    ) -> JobDto:
        job = self.get_job(job_id)
        if job.kind != "plan_shots":
            raise StudioConflictError("job is not a director planning job")
        return self._repository.record_director_validation(job_id, result.validation_document())

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
        active_plan = self._repository.active_shot_plan(project_id)
        active_plan_id = active_plan.id if active_plan is not None else None
        if (
            draft.expected_active_shot_plan_version_id is not None
            and draft.expected_active_shot_plan_version_id != active_plan_id
        ):
            raise StudioConflictError("active shot plan version changed")
        if draft.base_shot_plan_version_id is not None and not any(
            plan.id == draft.base_shot_plan_version_id
            for plan in self._repository.list_shot_plans(project_id)
        ):
            raise StudioNotFoundError("base shot plan version not found")
        synchronized_draft = draft.model_copy(
            update={
                "shots": [
                    synchronize_professional_shot_summaries(shot) for shot in draft.shots
                ]
            }
        )
        return self._repository.create_shot_plan(
            project_id,
            synchronized_draft,
            active=True,
            review_status="accepted",
            base_shot_plan_version_id=draft.base_shot_plan_version_id,
        )

    def create_shot_plan_generation_job(
        self, project_id: uuid.UUID, command: ShotPlanGenerationCommand
    ) -> JobDto:
        project = self._require_project(project_id)
        story = self._repository.active_story(project_id)
        if story is None:
            raise StudioConflictError("active story is required")
        selections = self._repository.current_selections(project_id)
        reference_roles = (
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        )
        missing = [role for role in reference_roles if role not in selections]
        if missing:
            raise StudioConflictError(f"missing asset selections: {', '.join(missing)}")
        self._require_paid_calls_enabled()
        clip = LifeClipSpec(
            durationSeconds=story.target_duration_seconds,
            aspectRatio="9:16",
            microEvent=story.title,
            childAction=story.micro_event.child_action,
            catActionOrObservation=story.micro_event.cat_response,
            visibleCauseAndEffect=story.micro_event.visible_change,
            warmEnding=story.micro_event.warm_ending,
            dialoguePolicy=story.dialogue_policy,
            environmentIntent=story.environment_intent,
        )
        prompt = _director_prompt(project, story)
        output_schema = director_provider_output_schema()
        selection_hash = self.current_selection_hash(project_id)
        base_shot_plan = self._repository.active_shot_plan(project_id)
        document = {
            "projectId": str(project_id),
            "storyVersionId": str(story.id),
            "selectionHash": selection_hash,
            "baseShotPlanVersionId": (
                str(base_shot_plan.id) if base_shot_plan is not None else None
            ),
            "canonProfileId": str(project.canon_profile_id),
            "referenceAssetIds": [str(selections[role].id) for role in reference_roles],
            "referenceRoles": list(reference_roles),
            "referenceSha256": [selections[role].sha256 for role in reference_roles],
            "targetDurationSeconds": project.target_duration_seconds,
            "aspectRatio": "9:16",
            "frameRate": 24,
            "directorPromptRevision": "catflow-director-v3",
            "provider": self._provider_runtime.provider,
            "model": self._provider_runtime.planning_model,
            "capabilityRevision": self._provider_runtime.capability_revision,
            "prompt": prompt,
            "outputSchema": output_schema,
        }
        input_hash = _hash_document(document)
        running = next(
            (
                item
                for item in self._repository.list_project_jobs(project_id)
                if item.kind == "plan_shots"
                and item.status
                in {
                    "queued",
                    "submitting",
                    "submitted",
                    "polling",
                    "storing",
                    "cancel_requested",
                    "submission_unknown",
                }
            ),
            None,
        )
        if running is not None:
            if running.idempotency_key == command.idempotency_key:
                if running.input_hash != input_hash:
                    raise StudioIdempotencyInputConflictError(
                        "idempotency key already belongs to different input"
                    )
                return running
            raise StudioConflictError("a shot plan generation job is already running")
        now = datetime.now(UTC)
        return self._create_job(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="plan_shots",
                status="queued",
                inputHash=input_hash,
                idempotencyKey=command.idempotency_key,
                provider=self._provider_runtime.provider,
                model=self._provider_runtime.planning_model,
                expectedCostMicros=None,
                frozenInput={
                    **document,
                    "clip": clip.model_dump(mode="json", by_alias=True),
                    "outputSchema": output_schema,
                },
                createdAt=now,
                updatedAt=now,
            )
        )

    def list_shot_plans(self, project_id: uuid.UUID) -> list[ShotPlanVersionDto]:
        self._require_project(project_id)
        selection_hash = self.current_selection_hash(project_id)
        story = self._repository.active_story(project_id)
        active_plan = self._repository.active_shot_plan(project_id)
        active_plan_id = active_plan.id if active_plan is not None else None
        return [
            plan.model_copy(
                update={
                    "outdated": story is None
                    or plan.source_story_version_id != story.id
                    or plan.source_selection_hash != selection_hash
                    or (
                        plan.review_status in {"candidate", "superseded"}
                        and plan.base_shot_plan_version_id != active_plan_id
                        and not plan.active
                    )
                }
            )
            for plan in self._repository.list_shot_plans(project_id)
        ]

    def activate_shot_plan(
        self,
        project_id: uuid.UUID,
        shot_plan_id: uuid.UUID,
        command: ShotPlanActivationCommand,
    ) -> ShotPlanVersionDto:
        self._require_project(project_id)
        plan = next(
            (item for item in self.list_shot_plans(project_id) if item.id == shot_plan_id),
            None,
        )
        if plan is None:
            raise StudioNotFoundError("shot plan version not found")
        if plan.outdated:
            raise StudioConflictError("shot plan inputs have changed")
        return self._repository.activate_shot_plan(
            project_id,
            shot_plan_id,
            expected_active_shot_plan_version_id=(command.expected_active_shot_plan_version_id),
        )

    def reject_shot_plan(
        self, project_id: uuid.UUID, shot_plan_id: uuid.UUID
    ) -> ShotPlanVersionDto:
        self._require_project(project_id)
        return self._repository.reject_shot_plan(project_id, shot_plan_id)

    def list_shot_plan_generation_attempts(
        self, project_id: uuid.UUID, *, limit: int = 20
    ) -> list[ShotPlanGenerationAttemptDto]:
        self._require_project(project_id)
        plans_by_job = {
            plan.producing_job_id: plan.id
            for plan in self._repository.list_shot_plans(project_id)
            if plan.producing_job_id is not None
        }
        attempts: list[ShotPlanGenerationAttemptDto] = []
        for job in self._repository.list_project_jobs(project_id):
            if job.kind != "plan_shots":
                continue
            story_value = job.frozen_input.get("storyVersionId")
            if story_value is None:
                continue
            base_value = job.frozen_input.get("baseShotPlanVersionId")
            error = job.error or {}
            provider_payload = (
                job.provider_result.get("payload")
                if isinstance(job.provider_result, dict)
                else None
            )
            normalization = (
                normalize_director_result(provider_payload)
                if provider_payload is not None
                else None
            )
            result_plan_id = plans_by_job.get(job.id)
            generation_result = None
            if normalization is not None:
                normalized_payload = normalization.normalized_payload or {}
                treatment_value = normalized_payload.get("directorTreatment")
                shots_value = normalized_payload.get("shots")
                generation_result = ShotPlanGenerationResultDto(
                    disposition=normalization.disposition,
                    resultShotPlanVersionId=result_plan_id,
                    recoverable=normalization.recoverable,
                    draft=(
                        DirectorPlanDraftDto(
                            targetDurationSeconds=(
                                int(normalized_payload["targetDurationSeconds"])
                                if isinstance(normalized_payload.get("targetDurationSeconds"), int)
                                else None
                            ),
                            directorTreatment=(
                                treatment_value if isinstance(treatment_value, dict) else None
                            ),
                            shots=(
                                [item for item in shots_value if isinstance(item, dict)]
                                if isinstance(shots_value, list)
                                else []
                            ),
                        )
                        if normalization.normalized_payload is not None
                        else None
                    ),
                    issues=[
                        DirectorValidationIssueDto(
                            code=issue.code,
                            severity=issue.severity,
                            path=issue.path,
                            message=issue.message,
                            suggestedAction=issue.suggested_action,
                            providerValue=issue.provider_value,
                        )
                        for issue in normalization.issues
                    ],
                )
            attempts.append(
                ShotPlanGenerationAttemptDto(
                    jobId=job.id,
                    status=job.status,
                    storyVersionId=uuid.UUID(str(story_value)),
                    baseShotPlanVersionId=(uuid.UUID(str(base_value)) if base_value else None),
                    resultShotPlanVersionId=result_plan_id,
                    provider=job.provider,
                    model=job.model,
                    createdAt=job.created_at,
                    updatedAt=job.updated_at,
                    actualUsage=job.actual_usage,
                    actualCostMicros=job.actual_cost_micros,
                    billingStatus=job.billing_status,
                    error=(
                        ShotPlanGenerationAttemptErrorDto(
                            code=str(error.get("code", "provider_error")),
                            message=str(error.get("message", "分镜生成失败")),
                            incompleteReason=(
                                str(error["incompleteReason"])
                                if error.get("incompleteReason")
                                else None
                            ),
                            requestId=(str(error["requestId"]) if error.get("requestId") else None),
                            retryable=bool(error.get("retryable", False)),
                            submissionUnknown=bool(error.get("submissionUnknown", False)),
                        )
                        if error
                        else None
                    ),
                    result=generation_result,
                )
            )
            if len(attempts) >= max(1, min(limit, 100)):
                break
        return attempts

    def recover_shot_plan_generation_result(
        self,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
        _command: ShotPlanGenerationRecoveryCommand,
    ) -> ShotPlanVersionDto:
        self._require_project(project_id)
        job = self.get_job(job_id)
        if job.project_id != project_id or job.kind != "plan_shots":
            raise StudioNotFoundError("shot plan generation result not found")
        provider_payload = (
            job.provider_result.get("payload") if isinstance(job.provider_result, dict) else None
        )
        normalized = normalize_director_result(provider_payload)
        self.record_shot_plan_generation_validation(job_id, normalized)
        existing = next(
            (
                plan
                for plan in self._repository.list_shot_plans(project_id)
                if plan.producing_job_id == job_id
            ),
            None,
        )
        if existing is not None:
            return existing
        pending_candidate = next(
            (
                plan
                for plan in self._repository.list_shot_plans(project_id)
                if plan.review_status == "candidate"
            ),
            None,
        )
        if pending_candidate is not None:
            raise StudioConflictError("another shot plan candidate is waiting for review")
        if normalized.disposition == "invalid":
            raise StudioValidationError("director result cannot be read")
        if normalized.disposition == "needs_input" or normalized.plan is None:
            raise StudioConflictError("director result requires input before materialization")
        return self.complete_shot_plan_job(job_id, normalized.plan)

    def materialize_shot_plan_generation_result(
        self,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
        command: ShotPlanGenerationMaterializeCommand,
    ) -> ShotPlanVersionDto:
        self._require_project(project_id)
        job = self.get_job(job_id)
        if job.project_id != project_id or job.kind != "plan_shots":
            raise StudioNotFoundError("shot plan generation result not found")
        if not isinstance(job.provider_result, dict) or not isinstance(
            job.provider_result.get("payload"), dict
        ):
            raise StudioValidationError("director result payload is missing")
        existing = next(
            (
                plan
                for plan in self._repository.list_shot_plans(project_id)
                if plan.producing_job_id == job_id
            ),
            None,
        )
        if existing is not None:
            return existing
        if any(
            plan.review_status == "candidate"
            for plan in self._repository.list_shot_plans(project_id)
        ):
            raise StudioConflictError("another shot plan candidate is waiting for review")
        return self.complete_shot_plan_job(job_id, command.payload)

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
        metadata: dict[str, Any] | None = None,
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
            metadata=metadata,
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
        latest_director_job = self._repository.latest_job(project_id, kind="plan_shots")
        latest_repair_job = self._repository.latest_job(project_id, kind="regenerate_video_segment")
        latest_asset_job = max(
            (
                job
                for job in self._repository.list_project_jobs(project_id)
                if job.kind in {"generate_image", "diagnose_image"}
            ),
            key=lambda job: (job.created_at, job.id.hex),
            default=None,
        )
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
                slot: asset.model_dump(mode="json", by_alias=True, exclude={"storage_key"})
                for slot, asset in selections.items()
            },
            "selectionHash": self.current_selection_hash(project_id),
            "latestVideoJob": (
                latest_video_job.model_dump(mode="json", by_alias=True)
                if latest_video_job is not None
                else None
            ),
            "latestDirectorJob": (
                latest_director_job.model_dump(mode="json", by_alias=True)
                if latest_director_job is not None
                else None
            ),
            "latestRepairJob": (
                latest_repair_job.model_dump(mode="json", by_alias=True)
                if latest_repair_job is not None
                else None
            ),
            "latestAssetJob": (
                latest_asset_job.model_dump(mode="json", by_alias=True)
                if latest_asset_job is not None
                else None
            ),
        }

    def current_selections(self, project_id: uuid.UUID) -> dict[str, AssetDto]:
        self._require_project(project_id)
        return self._repository.current_selections(project_id)

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
                if slot != "final" and not slot.startswith("continuity_keyframe_")
            }
        )

    def preview_video_generation(
        self,
        project_id: uuid.UUID,
        *,
        maximum_references: int | None = None,
        include_previous_episode_video: bool = False,
    ) -> GenerationPreviewDto:
        project = self._require_project(project_id)
        series_episode = self._repository.series_episode_for_project(project_id)
        previous_episode: SeriesEpisodeDto | None = None
        confirmed_continuity: EpisodeContinuitySnapshotDto | None = None
        if series_episode is not None and series_episode.order > 1:
            previous_episode = next(
                (
                    item
                    for item in self._repository.list_series_episodes(series_episode.series_id)
                    if item.order == series_episode.order - 1
                ),
                None,
            )
            confirmed_continuity = next(
                (
                    item
                    for item in self._repository.list_episode_continuity(series_episode.id)
                    if item.direction == "incoming" and item.active and item.confirmed
                ),
                None,
            )
            if confirmed_continuity is None:
                raise StudioConflictError(
                    "confirm the episode's incoming continuity before video generation"
                )
        elif include_previous_episode_video:
            raise StudioConflictError(
                "the first episode does not have a previous episode video to reference"
            )
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

        provider_references = [
            ProviderReference(
                assetId=selections[slot].id,
                role=slot,  # type: ignore[arg-type]
                sha256=selections[slot].sha256,
            )
            for slot in required_slots
        ]
        previous_video: AssetDto | None = None
        if previous_episode is not None and previous_episode.project_id is not None:
            previous_video = self._repository.current_selections(
                previous_episode.project_id
            ).get("final")
            if previous_video is not None and previous_video.media_type != "video":
                previous_video = None
        if include_previous_episode_video:
            if previous_video is None:
                raise StudioConflictError(
                    "the previous episode needs a selected final video before it can be referenced"
                )
            if self._provider_runtime.maximum_video_input_references < 1:
                raise StudioValidationError(
                    "the current video capability does not accept a reference video"
                )
            if not self._provider_runtime.segment_reference_publishing_ready:
                raise StudioConflictError(
                    "the managed HTTPS publisher is required for a previous episode video reference"
                )
        video_references = (
            [
                GenerationInputVideoReferenceDto(
                    assetId=previous_video.id,
                    role="previous_episode_video",
                    sha256=previous_video.sha256,
                    durationSeconds=(
                        float(previous_video.metadata["durationMs"]) / 1_000
                        if previous_video.metadata.get("durationMs") is not None
                        else (
                            float(previous_video.metadata["durationSeconds"])
                            if previous_video.metadata.get("durationSeconds") is not None
                            else None
                        )
                    ),
                    included=include_previous_episode_video,
                )
            ]
            if previous_video is not None
            else []
        )
        role_order: tuple[Any, ...] = required_slots
        if previous_episode is not None:
            frames = self.get_episode_continuity_frames(
                previous_episode.series_id, previous_episode.id
            )
            if frames is not None:
                continuity_assets: list[tuple[str, AssetDto]] = []
                if frames.last_frame is not None:
                    continuity_assets.append(
                        ("previous_episode_last_frame", frames.last_frame)
                    )
                continuity_assets.extend(
                    (f"previous_episode_keyframe_{index}", asset)
                    for index, asset in enumerate(frames.selected_keyframes, start=1)
                )
                provider_references.extend(
                    ProviderReference(assetId=asset.id, role=role, sha256=asset.sha256)  # type: ignore[arg-type]
                    for role, asset in continuity_assets
                )
                role_order = (
                    *required_slots,
                    "previous_episode_last_frame",
                    "previous_episode_keyframe_1",
                    "previous_episode_keyframe_2",
                )
        reference_limit = (
            self._provider_runtime.maximum_video_references
            if maximum_references is None
            else maximum_references
        )
        if len(provider_references) > reference_limit:
            raise StudioValidationError(
                f"current video capability accepts {reference_limit} image references, "
                f"but this episode requires {len(provider_references)}"
            )
        compiled = compile_references(
            provider_references,
            maximum_references=reference_limit,
            role_order=role_order,
        )
        continuity_constraints: list[str] = []
        if confirmed_continuity is not None:
            continuity_constraints.append(
                "跨集连续性：必须承接已确认的上一集结束状态："
                + json.dumps(
                    confirmed_continuity.state.model_dump(mode="json", by_alias=True),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            if any(
                reference.role.startswith("previous_episode_") and reference.included
                for reference in compiled.references
            ):
                continuity_constraints.append(
                    "上一集尾帧与连续性关键帧只负责服装、道具位置、时间、光线和接镜状态；"
                    "不得取代儿童、猫咪、人猫比例、当前环境或固定画风板。"
                )
        if include_previous_episode_video:
            continuity_constraints.append(
                "用户已明确启用上一集完整成片作为高级连续性参考。"
                "该视频只负责服装、道具、位置、时间、光线与直接接镜；"
                "不得复制上一集的镜头调度、动作节奏或构图，也不得取代固定五张图片参考。"
            )
        compiled_prompt = compile_video_generation_prompt(
            project_title=project.title,
            target_duration_seconds=project.target_duration_seconds,
            shots=shot_plan.shots,
            director_treatment=shot_plan.director_treatment,
            continuity_constraints=tuple(continuity_constraints),
        )
        prompt = compiled_prompt.prompt
        negative_prompt = compiled_prompt.negative_prompt
        compiled_provider_prompt = compile_provider_video_prompt(
            prompt=prompt,
            negative_prompt=negative_prompt,
        )
        document = {
            "projectId": str(project_id),
            "storyVersionId": str(story.id),
            "shotPlanVersionId": str(shot_plan.id),
            "selectionHash": selection_hash,
            "prompt": prompt,
            "negativePrompt": negative_prompt,
            "promptSummary": compiled_prompt.prompt_summary,
            "promptSections": [
                section.model_dump(mode="json", by_alias=True)
                for section in compiled_prompt.prompt_sections
            ],
            "compiledProviderPrompt": compiled_provider_prompt,
            "promptCompilerRevision": VIDEO_PROMPT_COMPILER_REVISION,
            "references": [
                reference.model_dump(mode="json", by_alias=True)
                for reference in compiled.references
            ],
            "videoReferences": [
                reference.model_dump(mode="json", by_alias=True)
                for reference in video_references
                if reference.included
            ],
            "provider": self._provider_runtime.provider,
            "model": self._provider_runtime.video_model,
            "capabilityRevision": self._provider_runtime.capability_revision,
            "durationSeconds": project.target_duration_seconds,
            "resolution": "480p",
            "aspectRatio": "9:16",
            "seriesEpisodeId": (
                str(series_episode.id) if series_episode is not None else None
            ),
            "continuitySnapshotId": (
                str(confirmed_continuity.id) if confirmed_continuity is not None else None
            ),
        }
        input_hash = _hash_document(document)
        preview = GenerationPreviewDto(
            inputHash=input_hash,
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.video_model,
            capabilityRevision=self._provider_runtime.capability_revision,
            prompt=prompt,
            negativePrompt=negative_prompt,
            promptSummary=compiled_prompt.prompt_summary,
            promptSections=list(compiled_prompt.prompt_sections),
            references=compiled.references,
            videoReferences=video_references,
            expectedCostMicros=None,
            costEstimateStatus="unmetered_paid",
            storyVersionId=story.id,
            shotPlanVersionId=shot_plan.id,
            selectionHash=selection_hash,
            durationSeconds=project.target_duration_seconds,
            seriesEpisodeId=series_episode.id if series_episode is not None else None,
            continuitySnapshotId=(
                confirmed_continuity.id if confirmed_continuity is not None else None
            ),
            warnings=[
                {"code": risk.code, "message": risk.message}
                for shot in shot_plan.shots
                for risk in shot.generation_risks
            ],
        )
        return preview.model_copy(
            update={
                "input_snapshot": GenerationInputSnapshotDto.model_validate(
                    _whole_generation_input_snapshot(
                        preview, created_at=datetime.now(UTC), state="preview"
                    )
                )
            }
        )

    def preview_asset_generation(
        self, project_id: uuid.UUID, command: AssetGenerationPreviewCommand
    ) -> AssetGenerationPreviewDto:
        project = self._require_project(project_id)
        selections = self._repository.current_selections(project_id)
        story = self._repository.active_story(project_id)
        reference_roles: dict[str, tuple[str, ...]] = {
            "episode_child": ("episode_child", "style_board"),
            "episode_cat": ("episode_cat", "style_board"),
            "pair_scale": ("episode_child", "episode_cat", "pair_scale", "style_board"),
            "environment": ("style_board", "episode_child", "episode_cat"),
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
        if command.kind == "environment":
            if story is None:
                raise StudioValidationError("active story is required for environment generation")
            missing = [
                role
                for role in ("style_board", "episode_child", "episode_cat")
                if role not in selections
            ]
            if missing:
                raise StudioValidationError(
                    "fixed character and style references are incomplete: " + ", ".join(missing)
                )
            compiled = compile_references(
                references,
                maximum_references=3,
                role_order=("style_board", "episode_child", "episode_cat"),
            )
            prompt = _environment_asset_prompt(project, story)
            negative_prompt = _environment_negative_prompt()
        else:
            compiled = compile_references(references, maximum_references=4)
            prompt = _asset_prompt(project, command.kind)
            negative_prompt = _default_asset_negative_prompt()
        document = {
            "projectId": str(project_id),
            "canonProfileId": str(project.canon_profile_id),
            "kind": command.kind,
            "prompt": prompt,
            "negativePrompt": negative_prompt,
            "references": [
                item.model_dump(mode="json", by_alias=True) for item in compiled.references
            ],
            "provider": self._provider_runtime.provider,
            "model": self._provider_runtime.image_model,
            "capabilityRevision": self._provider_runtime.capability_revision,
        }
        if command.kind == "environment" and story is not None:
            document.update(
                {
                    "sourceStoryVersionId": str(story.id),
                    "environmentIntent": story.environment_intent,
                    "subjectPolicy": "empty_scene",
                    "promptCompilerRevision": "catflow-environment-v2",
                }
            )
        input_hash = _hash_document(document)
        preview = AssetGenerationPreviewDto(
            inputHash=input_hash,
            kind=command.kind,
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.image_model,
            capabilityRevision=self._provider_runtime.capability_revision,
            prompt=prompt,
            negativePrompt=negative_prompt,
            references=compiled.references,
            expectedCostMicros=None,
            costEstimateStatus="unmetered_paid",
        )
        if command.kind != "environment" or story is None:
            return preview
        return preview.model_copy(
            update={
                "image_input_snapshot": _image_generation_input_snapshot(
                    preview,
                    story=story,
                    state="preview",
                    created_at=datetime.now(UTC),
                )
            }
        )

    def create_asset_generation_job(
        self, project_id: uuid.UUID, command: AssetGenerationCommand
    ) -> JobDto:
        preview = self.preview_asset_generation(
            project_id, AssetGenerationPreviewCommand(kind=command.kind)
        )
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("generation input hash changed")
        self._require_paid_calls_enabled()
        now = datetime.now(UTC)
        story = self._repository.active_story(project_id)
        image_input_snapshot = (
            _image_generation_input_snapshot(
                preview,
                story=story,
                state="submitted",
                created_at=now,
            )
            if preview.kind == "environment" and story is not None
            else None
        )
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
                expectedCostMicros=preview.expected_cost_micros,
                imageInputSnapshot=image_input_snapshot,
                frozenInput={
                    "role": preview.kind,
                    "prompt": preview.prompt,
                    "negativePrompt": preview.negative_prompt,
                    "compiledProviderPrompt": compile_provider_image_prompt(
                        prompt=preview.prompt,
                        negative_prompt=preview.negative_prompt,
                    ),
                    "references": [
                        item.model_dump(mode="json", by_alias=True) for item in preview.references
                    ],
                    "capabilityRevision": preview.capability_revision,
                    "referenceAssetIds": [
                        str(item.asset_id) for item in preview.references if item.included
                    ],
                    "referenceRoles": [item.role for item in preview.references if item.included],
                    "imageInputSnapshot": (
                        image_input_snapshot.model_dump(mode="json", by_alias=True)
                        if image_input_snapshot is not None
                        else None
                    ),
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
        self._require_paid_calls_enabled()
        candidate = self.get_asset(command.asset_id)
        selections = self._repository.current_selections(project_id)
        selected_asset_ids = {asset.id for asset in selections.values()}
        if candidate.media_type != "image" or (
            candidate.project_id != project_id and candidate.id not in selected_asset_ids
        ):
            raise StudioConflictError(
                "diagnosis candidate must belong to the project or its inherited Canon"
            )
        reference_roles: dict[str, tuple[str, ...]] = {
            "episode_child": ("episode_child", "style_board"),
            "episode_cat": ("episode_cat", "style_board"),
            "pair_scale": ("episode_child", "episode_cat", "pair_scale", "style_board"),
            "environment": ("style_board", "episode_child", "episode_cat"),
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
        frozen_input: dict[str, Any] = {
            "candidateAssetId": str(candidate.id),
            "candidateSha256": candidate.sha256,
            "candidateRole": candidate.role,
            "references": references,
            "canonProfileId": str(self.current_canon_profile_id()),
            "referenceAssetIds": [reference["assetId"] for reference in references],
        }
        if candidate.role == "environment":
            story = self._repository.active_story(project_id)
            if story is None:
                raise StudioValidationError("active story is required for environment diagnosis")
            frozen_input.update(
                {
                    "diagnosticSchema": "environment-quality-report-v2",
                    "subjectPolicy": "empty_scene",
                    "sourceStoryVersionId": str(story.id),
                    "environmentIntent": story.environment_intent,
                    "prompt": (
                        f"检查环境候选是否符合环境意图“{story.environment_intent}”。"
                        "候选应是空场景，不应出现儿童、成年人、猫咪、其他动物、身体局部或倒影。"
                        "图一画风板用于比较线条、材质、色彩和光线；图二儿童与图三猫咪只用于"
                        "比较整套插画渲染语言，并帮助识别候选中不应出现的角色。"
                        "同时判断空间和道具尺度是否能容纳约1.2米儿童与固定比例猫咪活动。"
                        "返回环境吻合、无角色、画风一致、活动空间和技术质量建议；"
                        "建议不得自动批准或拒绝。"
                    ),
                    "outputSchema": _environment_diagnostic_output_schema(),
                }
            )
        else:
            frozen_input.update(
                {
                    "diagnosticSchema": "candidate-quality-report-v1",
                    "prompt": (
                        "依据带标签的 Canon 身份、同框比例与净化画风板对照候选图片，"
                        "返回身份、画风、结构和技术质量建议；AI 建议不得自动批准或拒绝。"
                    ),
                    "outputSchema": _diagnostic_output_schema(),
                }
            )
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
                expectedCostMicros=None,
                frozenInput=frozen_input,
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def create_video_job(self, project_id: uuid.UUID, command: GenerationCommand) -> JobDto:
        preview = self.preview_video_generation(
            project_id,
            include_previous_episode_video=command.include_previous_episode_video,
        )
        if preview.input_hash != command.expected_input_hash:
            raise StudioConflictError("generation input hash changed")
        self._require_paid_calls_enabled()
        now = datetime.now(UTC)
        included = [reference for reference in preview.references if reference.included]
        input_snapshot = _whole_generation_input_snapshot(
            preview, created_at=now, state="submitted"
        )
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
            inputSnapshot=input_snapshot,
            frozenInput={
                "inputSnapshot": input_snapshot,
                "storyVersionId": str(preview.story_version_id),
                "shotPlanVersionId": str(preview.shot_plan_version_id),
                "selectionHash": preview.selection_hash,
                "prompt": preview.prompt,
                "negativePrompt": preview.negative_prompt,
                "compiledProviderPrompt": compile_provider_video_prompt(
                    prompt=preview.prompt,
                    negative_prompt=preview.negative_prompt,
                ),
                "references": [
                    item.model_dump(mode="json", by_alias=True) for item in preview.references
                ],
                "referenceAssetIds": [str(item.asset_id) for item in included],
                "referenceRoles": [item.role for item in included],
                "videoReferences": [
                    item.model_dump(mode="json", by_alias=True)
                    for item in preview.video_references
                    if item.included
                ],
                "previousEpisodeVideoAssetId": next(
                    (
                        str(item.asset_id)
                        for item in preview.video_references
                        if item.included
                    ),
                    None,
                ),
                "previousEpisodeVideoSha256": next(
                    (item.sha256 for item in preview.video_references if item.included),
                    None,
                ),
                "capabilityRevision": preview.capability_revision,
                "durationSeconds": preview.duration_seconds,
                "resolution": "480p",
                "aspectRatio": "9:16",
                "seriesEpisodeId": (
                    str(preview.series_episode_id)
                    if preview.series_episode_id is not None
                    else None
                ),
                "continuitySnapshotId": (
                    str(preview.continuity_snapshot_id)
                    if preview.continuity_snapshot_id is not None
                    else None
                ),
            },
            resultAssetIds=[],
            createdAt=now,
            updatedAt=now,
        )
        persisted = self._create_job(job)
        episode_value = persisted.frozen_input.get("seriesEpisodeId")
        if episode_value:
            continuity_value = persisted.frozen_input.get("continuitySnapshotId")
            self._repository.save_episode_reference_manifest(
                uuid.UUID(str(episode_value)),
                persisted.id,
                uuid.UUID(str(continuity_value)) if continuity_value else None,
                [
                    item.model_dump(mode="json", by_alias=True)
                    for item in preview.references
                    if item.included
                ]
                + [
                    item.model_dump(mode="json", by_alias=True)
                    for item in preview.video_references
                    if item.included
                ],
            )
        return persisted

    def create_video_diagnosis_job(
        self, project_id: uuid.UUID, command: VideoDiagnosisCommand
    ) -> JobDto:
        self._require_project(project_id)
        self._require_paid_calls_enabled()
        video = self.get_asset(command.asset_id)
        if video.project_id != project_id or video.media_type != "video":
            raise StudioConflictError("video diagnosis target must be a project video")
        selections = self._repository.current_selections(project_id)
        roles = ("episode_child", "episode_cat", "pair_scale", "environment", "style_board")
        missing = [role for role in roles if role not in selections]
        if missing:
            raise StudioConflictError(f"missing video diagnosis references: {', '.join(missing)}")
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
                parentJobId=video.producing_job_id,
                expectedCostMicros=None,
                frozenInput=frozen_input,
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )

    def preview_video_repair(
        self, project_id: uuid.UUID, command: SegmentRepairPreviewCommand
    ) -> SegmentRepairPreviewDto:
        self._require_project(project_id)
        if reason := self._provider_runtime.segment_repair_block_reason:
            raise StudioConflictError(reason)
        base_video, active_edit, timeline, timeline_hash = self._repair_base_timeline(
            project_id,
            base_video_asset_id=command.base_video_asset_id,
            expected_edit_version_id=command.base_edit_version_id,
        )
        frame_rate = timeline.frame_rate
        if frame_rate.numerator != 24 or frame_rate.denominator != 1:
            raise StudioConflictError("video repairs require a 24 fps editing timeline")
        try:
            validate_issue_range(command.issue_range, total_frames=timeline.total_frames)
            window = expand_generation_window(
                command.issue_range,
                total_frames=timeline.total_frames,
                frame_rate=frame_rate,
            )
        except ValueError as exc:
            raise StudioValidationError(str(exc)) from exc

        selections = self._repository.current_selections(project_id)
        canon_roles = ("episode_child", "episode_cat", "pair_scale", "environment", "style_board")
        missing = [role for role in canon_roles if role not in selections]
        if missing:
            raise StudioConflictError(f"missing segment repair references: {', '.join(missing)}")
        anchor_in_sha = _hash_document(
            {"sourceSha256": base_video.sha256, "frame": command.issue_range.start_frame}
        )
        anchor_out_sha = _hash_document(
            {"sourceSha256": base_video.sha256, "frame": command.issue_range.end_frame - 1}
        )
        image_references = [
            SegmentRepairImageReferenceDto(
                role="anchor_in",
                sha256=anchor_in_sha,
                frameNumber=command.issue_range.start_frame,
                derived=True,
            ),
            SegmentRepairImageReferenceDto(
                role="anchor_out",
                sha256=anchor_out_sha,
                frameNumber=command.issue_range.end_frame - 1,
                derived=True,
            ),
            *[
                SegmentRepairImageReferenceDto(
                    role=role, assetId=selections[role].id, sha256=selections[role].sha256
                )
                for role in canon_roles
            ],
        ]
        video_reference = SegmentRepairVideoReferenceDto(
            role="reference_video",
            assetId=base_video.id,
            sha256=base_video.sha256,
            range=window.generation_range,
        )
        negative_prompt = (
            "真实摄影，3D塑料质感，身份漂移，儿童年龄或发型变化，猫咪毛色或虎斑变化，"
            "额外肢体，融脸，断尾，错误四足，动作双影，背景或光线跳变，文字，Logo，水印，"
            "静止停帧，原地互看，循环动作填充时长，叶片微距摄影污染"
        )
        prompt = _segment_edit_prompt(
            instruction=command.instruction,
            issue_range=command.issue_range,
            generation_range=window.generation_range,
            frame_rate=frame_rate,
        )
        document = {
            "projectId": str(project_id),
            "baseVideoAssetId": str(base_video.id),
            "baseVideoSha256": base_video.sha256,
            "baseEditVersionId": str(active_edit.id) if active_edit is not None else None,
            "baseTimelineHash": timeline_hash,
            "frameRate": frame_rate.model_dump(mode="json", by_alias=True),
            "issueRange": command.issue_range.model_dump(mode="json", by_alias=True),
            "generationRange": window.generation_range.model_dump(mode="json", by_alias=True),
            "candidateCoreRange": window.candidate_core_range.model_dump(
                mode="json", by_alias=True
            ),
            "providerDurationSeconds": window.provider_duration_seconds,
            "instruction": command.instruction,
            "prompt": prompt,
            "negativePrompt": negative_prompt,
            "imageReferences": [
                item.model_dump(mode="json", by_alias=True) for item in image_references
            ],
            "videoReference": video_reference.model_dump(mode="json", by_alias=True),
            "provider": self._provider_runtime.provider,
            "model": self._provider_runtime.video_model,
            "capabilityRevision": self._provider_runtime.capability_revision,
        }
        preview = SegmentRepairPreviewDto(
            projectId=project_id,
            baseVideoAssetId=base_video.id,
            baseEditVersionId=active_edit.id if active_edit is not None else None,
            baseTimelineHash=timeline_hash,
            frameRate=frame_rate,
            issueRange=command.issue_range,
            generationRange=window.generation_range,
            candidateCoreRange=window.candidate_core_range,
            providerDurationSeconds=window.provider_duration_seconds,
            provider=self._provider_runtime.provider,
            model=self._provider_runtime.video_model,
            capabilityRevision=self._provider_runtime.capability_revision,
            instruction=command.instruction,
            prompt=prompt,
            negativePrompt=negative_prompt,
            imageReferences=image_references,
            videoReference=video_reference,
            expectedCostMicros=None,
            costEstimateStatus="unmetered_paid",
            inputHash=_hash_document(document),
        )
        return preview.model_copy(
            update={
                "input_snapshot": GenerationInputSnapshotDto.model_validate(
                    _segment_generation_input_snapshot(
                        preview, created_at=datetime.now(UTC), state="preview"
                    )
                )
            }
        )

    def create_video_repair_job(
        self, project_id: uuid.UUID, command: SegmentRepairCreateCommand
    ) -> JobDto:
        self._require_project(project_id)
        preview = self.preview_video_repair(
            project_id,
            SegmentRepairPreviewCommand(
                baseVideoAssetId=command.base_video_asset_id,
                baseEditVersionId=command.base_edit_version_id,
                issueRange=command.issue_range,
                instruction=command.instruction,
            ),
        )
        if preview.input_hash != command.expected_input_hash:
            raise StudioInputChangedError("segment repair input hash changed", preview)
        self._require_paid_calls_enabled()
        now = datetime.now(UTC)
        repair_id = uuid.uuid4()
        repair = VideoRepairDto(
            id=repair_id,
            projectId=project_id,
            baseVideoAssetId=preview.base_video_asset_id,
            baseEditVersionId=preview.base_edit_version_id,
            baseTimelineHash=preview.base_timeline_hash,
            frameRate=preview.frame_rate,
            issueRange=preview.issue_range,
            generationRange=preview.generation_range,
            candidateCoreRange=preview.candidate_core_range,
            providerDurationSeconds=preview.provider_duration_seconds,
            selectionPolicyVersion=2,
            instruction=preview.instruction,
            prompt=preview.prompt,
            negativePrompt=preview.negative_prompt,
            inputHash=preview.input_hash,
            status="generating",
            preview=preview,
            createdAt=now,
        )
        image_references = preview.image_references
        input_snapshot = _segment_generation_input_snapshot(
            preview, created_at=now, state="submitted"
        )
        job = self._with_pricing_snapshot(
            JobDto(
                id=uuid.uuid4(),
                projectId=project_id,
                kind="regenerate_video_segment",
                status="queued",
                inputHash=preview.input_hash,
                idempotencyKey=command.idempotency_key,
                provider=preview.provider,
                model=preview.model,
                videoRepairId=repair_id,
                expectedCostMicros=preview.expected_cost_micros,
                inputSnapshot=input_snapshot,
                frozenInput={
                    "inputSnapshot": input_snapshot,
                    "baseVideoAssetId": str(preview.base_video_asset_id),
                    "baseEditVersionId": (
                        str(preview.base_edit_version_id)
                        if preview.base_edit_version_id is not None
                        else None
                    ),
                    "baseTimelineHash": preview.base_timeline_hash,
                    "issueRange": preview.issue_range.model_dump(mode="json", by_alias=True),
                    "generationRange": preview.generation_range.model_dump(
                        mode="json", by_alias=True
                    ),
                    "candidateCoreRange": preview.candidate_core_range.model_dump(
                        mode="json", by_alias=True
                    ),
                    "providerDurationSeconds": preview.provider_duration_seconds,
                    "instruction": preview.instruction,
                    "prompt": preview.prompt,
                    "negativePrompt": preview.negative_prompt,
                    "imageReferences": [
                        item.model_dump(mode="json", by_alias=True) for item in image_references
                    ],
                    "videoReference": preview.video_reference.model_dump(
                        mode="json", by_alias=True
                    ),
                    "referenceAssetIds": [
                        str(item.asset_id) for item in image_references if item.asset_id is not None
                    ],
                    "referenceRoles": [item.role for item in image_references],
                    "capabilityRevision": preview.capability_revision,
                    "durationSeconds": preview.provider_duration_seconds,
                    "resolution": "480p",
                    "aspectRatio": "9:16",
                },
                resultAssetIds=[],
                createdAt=now,
                updatedAt=now,
            )
        )
        return self._repository.create_video_repair_job(repair, job)

    def mark_video_repair_candidate_ready(
        self, repair_id: uuid.UUID, candidate_asset_id: uuid.UUID
    ) -> VideoRepairDto:
        repair = self.get_video_repair(repair_id)
        candidate = self.get_asset(candidate_asset_id)
        if (
            candidate.project_id != repair.project_id
            or candidate.role != "repair_candidate"
            or candidate.media_type != "video"
        ):
            raise StudioConflictError("repair candidate does not belong to the repair project")
        job = (
            self._repository.get_job(candidate.producing_job_id)
            if candidate.producing_job_id is not None
            else None
        )
        if job is None or job.video_repair_id != repair_id:
            raise StudioConflictError("repair candidate is not produced by this repair job")
        return self._repository.set_video_repair_status(
            repair_id, status="candidate_ready", candidate_asset_id=candidate_asset_id
        )

    def list_video_repairs(self, project_id: uuid.UUID) -> list[VideoRepairDto]:
        self._require_project(project_id)
        return self._repository.list_video_repairs(project_id)

    def get_video_repair(self, repair_id: uuid.UUID) -> VideoRepairDto:
        repair = self._repository.get_video_repair(repair_id)
        if repair is None:
            raise StudioNotFoundError("video repair not found")
        return repair

    def approve_video_repair(
        self,
        project_id: uuid.UUID,
        repair_id: uuid.UUID,
        command: SegmentRepairApproveCommand,
    ) -> EditVersionDto:
        self._require_project(project_id)
        repair = self.get_video_repair(repair_id)
        if repair.project_id != project_id:
            raise StudioNotFoundError("video repair not found")
        _, active_edit, timeline, current_hash = self._repair_base_timeline(
            project_id,
            base_video_asset_id=repair.base_video_asset_id,
            expected_edit_version_id=repair.base_edit_version_id,
        )
        if (
            command.expected_base_timeline_hash != repair.base_timeline_hash
            or current_hash != repair.base_timeline_hash
        ):
            self._repository.set_video_repair_status(repair_id, status="outdated")
            raise StudioConflictError("base timeline changed")
        if repair.status != "candidate_ready" or repair.candidate_asset_id is None:
            raise StudioConflictError("video repair has no candidate ready for approval")
        required_quality = {
            "child_identity",
            "cat_identity",
            "pair_scale",
            "style",
            "structure",
            "motion_continuity",
            "causal_chain",
        }
        if set(command.quality_checks) != required_quality or any(
            value != "pass" for value in command.quality_checks.values()
        ):
            raise StudioConflictError("all seven quality checks must pass")
        if set(command.seam_checks) != {"in", "out"} or any(
            value != "pass" for value in command.seam_checks.values()
        ):
            raise StudioConflictError("both seam checks must pass")
        if command.candidate_asset_id != repair.candidate_asset_id:
            raise StudioConflictError("approved candidate changed")
        candidate = self.get_asset(command.candidate_asset_id)
        total_candidate_frames = candidate.metadata.get("durationFrames")
        if not isinstance(total_candidate_frames, int):
            raise StudioConflictError("repair candidate has no frame metadata")
        if command.candidate_source_range.end_frame > total_candidate_frames:
            raise StudioConflictError("candidate source range exceeds the candidate video")
        handle_frames = command.transition.duration_frames
        if handle_frames and (
            command.candidate_source_range.start_frame < handle_frames
            or total_candidate_frames - command.candidate_source_range.end_frame < handle_frames
            or repair.issue_range.start_frame < handle_frames
            or timeline.total_frames - repair.issue_range.end_frame < handle_frames
        ):
            raise StudioConflictError(
                "candidate or original video has insufficient dissolve handles"
            )
        transition = EditTransitionV2(
            afterSegmentIndex=0,
            type=command.transition.type,
            durationFrames=command.transition.duration_frames,
        )
        try:
            repaired_timeline = splice_repair_candidate(
                timeline,
                issue_range=repair.issue_range,
                candidate_asset_id=candidate.id,
                candidate_sha256=candidate.sha256,
                candidate_source_range=command.candidate_source_range,
                repair_id=repair.id,
                transition=transition,
            )
        except ValueError as exc:
            raise StudioConflictError(str(exc)) from exc
        return self._repository.approve_video_repair(
            repair.id,
            edl=repaired_timeline,
            source_selection_hash=self.current_delivery_selection_hash(project_id),
            parent_edit_version_id=active_edit.id if active_edit is not None else None,
            candidate_asset_id=candidate.id,
            candidate_source_range=command.candidate_source_range,
            idempotency_key=command.idempotency_key,
        )

    def reject_video_repair(self, project_id: uuid.UUID, repair_id: uuid.UUID) -> VideoRepairDto:
        repair = self.get_video_repair(repair_id)
        if repair.project_id != project_id:
            raise StudioNotFoundError("video repair not found")
        return self._repository.set_video_repair_status(repair_id, status="rejected")

    def get_job(self, job_id: uuid.UUID) -> JobDto:
        job = self._repository.get_job(job_id)
        if job is None:
            raise StudioNotFoundError("job not found")
        return job

    def get_job_usage(self, job_id: uuid.UUID) -> JobUsageDto:
        return _job_usage(self.get_job(job_id))

    def project_usage_summary(self, project_id: uuid.UUID) -> ProjectUsageSummaryDto:
        self._require_project(project_id)
        usages = [
            _job_usage(job)
            for job in self._repository.list_project_jobs(project_id)
            if job.provider is not None and job.model is not None
        ]
        totals: dict[str, int] = {}
        for item in usages:
            for metric, quantity in item.provider_usage.items():
                totals[metric] = totals.get(metric, 0) + quantity
        return ProjectUsageSummaryDto(
            projectId=project_id,
            jobs=usages,
            totals=totals,
            calculatedCostMicros=sum(item.calculated_cost_micros or 0 for item in usages),
            unpricedJobCount=sum(item.billing_status == "unpriced" for item in usages),
        )

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

    def _repair_base_timeline(
        self,
        project_id: uuid.UUID,
        *,
        base_video_asset_id: uuid.UUID,
        expected_edit_version_id: uuid.UUID | None,
    ) -> tuple[AssetDto, EditVersionDto | None, EditDecisionListV2, str]:
        selections = self._repository.current_selections(project_id)
        selected_video = selections.get("video")
        if selected_video is None or selected_video.id != base_video_asset_id:
            raise StudioConflictError("base timeline changed")
        total_frames = selected_video.metadata.get("durationFrames")
        if not isinstance(total_frames, int) or total_frames <= 0:
            raise StudioConflictError("selected video has no valid frame metadata")
        active_edit = self._repository.active_edit(project_id)
        if expected_edit_version_id is not None and (
            active_edit is None or active_edit.id != expected_edit_version_id
        ):
            raise StudioConflictError("base timeline changed")
        if active_edit is not None and active_edit.format_version == 2:
            if not isinstance(active_edit.edl, EditDecisionListV2):
                raise StudioConflictError("active edit has an invalid v2 timeline")
            timeline = active_edit.edl
        else:
            timeline = build_base_timeline(
                asset_id=selected_video.id,
                sha256=selected_video.sha256,
                total_frames=total_frames,
            )
        timeline_hash = _hash_document(
            {
                "selectedVideoAssetId": str(selected_video.id),
                "selectedVideoSha256": selected_video.sha256,
                "activeEditVersionId": str(active_edit.id) if active_edit is not None else None,
                "timeline": timeline.model_dump(mode="json", by_alias=True),
            }
        )
        return selected_video, active_edit, timeline, timeline_hash

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
                model=f"ffmpeg-edl-v{edit.format_version}",
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
        selection = self.select_asset(
            project_id,
            slot="final",
            asset_id=command.asset_id,
            decision="approved",
        )
        episode = self._repository.series_episode_for_project(project_id)
        if episode is not None:
            duration_frames = asset.metadata.get("durationFrames")
            if not isinstance(duration_frames, int) or duration_frames <= 0:
                duration_ms = asset.metadata.get("durationMs")
                duration_frames = (
                    round(float(duration_ms) * 24 / 1000)
                    if isinstance(duration_ms, (int, float)) and duration_ms > 0
                    else episode.target_duration_seconds * 24
                )
            duration_seconds = duration_frames / 24
            now = datetime.now(UTC)
            frozen_input = {
                "seriesId": str(episode.series_id),
                "seriesEpisodeId": str(episode.id),
                "sourceVideoAssetId": str(asset.id),
                "sourceVideoSha256": asset.sha256,
                "keyframeSeconds": [
                    round(duration_seconds * 0.25, 3),
                    round(duration_seconds * 0.75, 3),
                ],
                "extractLastFrame": True,
            }
            self._create_job(
                JobDto(
                    id=uuid.uuid4(),
                    projectId=project_id,
                    kind="extract_continuity_frames",
                    status="queued",
                    inputHash=_hash_document(frozen_input),
                    idempotencyKey=f"continuity-frames:{episode.id}:{asset.id}:{asset.sha256}",
                    provider="local_ffmpeg",
                    model="ffmpeg-continuity-frames-v1",
                    expectedCostMicros=0,
                    frozenInput=frozen_input,
                    resultAssetIds=[],
                    createdAt=now,
                    updatedAt=now,
                )
            )
        return selection

    def _require_project(self, project_id: uuid.UUID) -> ProjectDto:
        project = self._repository.get_project(project_id)
        if project is None:
            raise StudioNotFoundError("project not found")
        return project

    def _require_paid_calls_enabled(self) -> None:
        if not self._provider_runtime.paid_calls_enabled:
            raise StudioConflictError("paid provider calls are disabled")

    def _create_job(self, job: JobDto) -> JobDto:
        job = self._with_pricing_snapshot(job)
        return self._repository.create_job(job)

    def _with_pricing_snapshot(self, job: JobDto) -> JobDto:
        if job.provider is not None and job.model is not None and job.pricing_snapshot is None:
            now = datetime.now(UTC)
            card = next(
                (
                    item
                    for item in self._repository.list_rate_cards()
                    if item.active
                    and item.provider == job.provider
                    and item.model == job.model
                    and item.effective_from <= now
                ),
                None,
            )
            if card is not None:
                job = job.model_copy(
                    update={
                        "rate_card_revision": card.revision,
                        "pricing_snapshot": {
                            "revision": card.revision,
                            "sourceUrl": card.source_url,
                            "effectiveFrom": card.effective_from.isoformat(),
                            "rates": [
                                rate.model_dump(mode="json", by_alias=True) for rate in card.rates
                            ],
                        },
                    }
                )
        return job


def _job_usage(job: JobDto) -> JobUsageDto:
    if job.provider is None or job.model is None:
        raise StudioConflictError("local jobs do not have provider usage")
    provider_usage = {
        key: value
        for key, value in (job.actual_usage or {}).items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    price_source = None
    if isinstance(job.pricing_snapshot, dict):
        source = job.pricing_snapshot.get("sourceUrl")
        if isinstance(source, str):
            price_source = source
    return JobUsageDto(
        jobId=job.id,
        provider=job.provider,
        model=job.model,
        inputTokens=provider_usage.get("inputTokens"),
        outputTokens=provider_usage.get("outputTokens"),
        completionTokens=provider_usage.get("completionTokens"),
        totalTokens=provider_usage.get("totalTokens"),
        generatedImages=provider_usage.get("generatedImages"),
        generatedVideoSeconds=provider_usage.get("generatedVideoSeconds"),
        providerUsage=provider_usage,
        billingStatus=job.billing_status,
        calculatedCostMicros=job.actual_cost_micros,
        currency=job.currency,
        rateCardRevision=job.rate_card_revision,
        priceSource=price_source,
    )


def _story_import_job(job: JobDto) -> StoryImportAnalysisJobDto:
    return StoryImportAnalysisJobDto(
        id=job.id,
        status=job.status,
        provider=job.provider,
        model=job.model,
        actualUsage=job.actual_usage,
        actualCostMicros=job.actual_cost_micros,
        billingStatus=job.billing_status,
        error=job.error,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
    )


def _hash_document(document: object) -> str:
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
                field: {"type": "string", "minLength": 1}
                for field in required
                if field not in {"targetDurationSeconds", "dialoguePolicy"}
            },
            "title": {"type": "string", "minLength": 4, "maxLength": 12},
            "summary": {"type": "string", "minLength": 1, "maxLength": 60},
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
        "标题使用4至12个汉字，摘要不超过60个汉字；标题、摘要与触发字段不得整句重复，"
        "不得复述用户原文。禁止使用‘围绕……展开’、‘通过……呈现’、‘营造……氛围’、"
        "‘体现治愈感’等空泛套话；每个字段优先描述儿童、猫咪、道具或环境具体、可观察的"
        "动作与状态变化。environmentIntent只描述空间、天气、家具、道具、构图和光线，"
        "不得包含儿童、猫咪或其他角色的动作；角色行为必须写入对应的动作字段。"
    )


def _director_prompt(project: ProjectDto, story: StoryVersionDto) -> str:
    event = story.micro_event
    return (
        f"你是CatFlow专业短片导演。把已采用故事《{story.title}》设计为"
        f"{project.target_duration_seconds}秒、24fps、9:16的一人一猫生活短片。"
        "只允许1至4个镜头，单镜头至少2秒，总帧数必须精确等于目标秒数乘24。"
        "shots数组只能包含最终采用且内容完整的镜头；不得输出空占位镜头、备用镜头或修订镜头，"
        "不得在数组末尾追加用于解释、自我纠正或替换前文的条目。"
        f"唯一因果链：触发“{event.trigger}”；孩子动作“{event.child_action}”；"
        f"猫咪回应“{event.cat_response}”；可见变化“{event.visible_change}”；"
        f"主动结尾“{event.warm_ending}”。"
        "每个镜头必须同时提供默认镜头卡和详细导演执行设计：焦距、机位高度与角度、"
        "前中后景构图、视线与运动方向、人物和猫咪的初始状态—运动路径—结束状态、"
        "可见物理状态变化、前后镜头连续性、最终帧、光线、环境声、物件声、动作声、"
        "导演意图与生成风险。每个角色每镜头最多三个有意义微动作。"
        "结尾必须继续发生自然动作，不得原地互看、停帧、重复呼吸或循环填时长。"
        "固定儿童为6至7岁、约1.2米、约4.5至5头身、齐下颌短发；动作符合低龄儿童"
        "能力，禁止8岁以上修长比例、青少年脸型、成人化身体或成人化表情。"
        "固定同一只灰白虎斑猫，保持正确四足、尾巴、毛色分区和可信人猫比例。"
        "不得复述故事原文，不使用‘围绕……展开’、‘通过……呈现’、‘营造……氛围’、"
        "‘电影感’、‘高级感’等没有对应可见动作的套话。每句话优先说明角色或物件的"
        "初始状态、变化过程和结束状态。"
        "只返回符合Schema的JSON，不生成多冲突、多转折或依赖对白解释的长剧结构。"
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


def _environment_diagnostic_output_schema() -> dict[str, Any]:
    verdict = {"type": "string", "enum": ["pass", "warning", "fail"]}
    required = [
        "intentMatch",
        "characterFree",
        "styleMatch",
        "stagingSpace",
        "technical",
        "warnings",
    ]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": {name: verdict for name in required if name != "warnings"}
        | {
            "warnings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "message"],
                    "properties": {
                        "code": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
            }
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


def _segment_edit_prompt(
    *,
    instruction: str,
    issue_range: FrameRange,
    generation_range: FrameRange,
    frame_rate: RationalFrameRate,
) -> str:
    fps = frame_rate.numerator / frame_rate.denominator
    issue_start = issue_range.start_frame / fps
    issue_end = issue_range.end_frame / fps
    generation_start = generation_range.start_frame / fps
    generation_end = generation_range.end_frame / fps
    return (
        f"对参考视频执行时间区间语义编辑。本区间修改目标：{instruction}"
        f"问题区间为第{issue_range.start_frame}帧（{issue_start:.3f}秒）至"
        f"第{issue_range.end_frame}帧（{issue_end:.3f}秒），结束帧不包含；"
        f"上下文生成区间为第{generation_range.start_frame}帧（{generation_start:.3f}秒）至"
        f"第{generation_range.end_frame}帧（{generation_end:.3f}秒）。"
        "reference_video只负责原有机位、动作节奏、构图、光线和前后连续性。"
        "只修改问题区间对应的目标问题，其他上下文保持稳定。"
        "入点锚帧定义修改段开始状态，出点锚帧定义修改段结束状态。"
        "角色动作必须明确表现初始状态—运动路径—结束状态，并在结束状态形成可观察的"
        "物理闭合；不得静止、原地互看或循环动作填充时长。"
    )


def _whole_generation_input_snapshot(
    preview: GenerationPreviewDto,
    *,
    created_at: datetime,
    state: Literal["preview", "submitted"],
) -> dict[str, Any]:
    snapshot = GenerationInputSnapshotDto(
        schemaVersion=2,
        kind="whole_video",
        state=state,
        provider=preview.provider,
        model=preview.model,
        capabilityRevision=preview.capability_revision,
        inputHash=preview.input_hash,
        prompt=preview.prompt,
        negativePrompt=preview.negative_prompt,
        promptSummary=preview.prompt_summary,
        promptSections=preview.prompt_sections,
        references=[
            GenerationInputReferenceDto(
                assetId=item.asset_id,
                role=item.role,
                priority=item.priority,
                included=item.included,
                omittedReason=item.omitted_reason,
                sha256=item.sha256,
            )
            for item in preview.references
        ],
        videoReferences=[
            item for item in preview.video_references if item.included
        ],
        video={
            "durationSeconds": preview.duration_seconds,
            "resolution": "480p",
            "aspectRatio": "9:16",
            "frameRate": 24,
        },
        source={
            "storyVersionId": preview.story_version_id,
            "shotPlanVersionId": preview.shot_plan_version_id,
            "selectionHash": preview.selection_hash,
        },
        promptCompilerRevision=VIDEO_PROMPT_COMPILER_REVISION,
        createdAt=created_at,
    )
    return snapshot.model_dump(mode="json", by_alias=True)


def _segment_generation_input_snapshot(
    preview: SegmentRepairPreviewDto,
    *,
    created_at: datetime,
    state: Literal["preview", "submitted"],
) -> dict[str, Any]:
    references = [
        GenerationInputReferenceDto(
            assetId=item.asset_id,
            role=item.role,
            priority=index,
            sha256=item.sha256,
            derived=item.derived,
        )
        for index, item in enumerate(preview.image_references, start=1)
    ]
    snapshot = GenerationInputSnapshotDto(
        schemaVersion=1,
        kind="segment_edit",
        state=state,
        provider=preview.provider,
        model=preview.model,
        capabilityRevision=preview.capability_revision,
        inputHash=preview.input_hash,
        prompt=preview.prompt,
        negativePrompt=preview.negative_prompt,
        references=references,
        video={
            "durationSeconds": preview.provider_duration_seconds,
            "resolution": "480p",
            "aspectRatio": "9:16",
            "frameRate": 24,
        },
        source={
            "baseVideoAssetId": preview.base_video_asset_id,
            "baseTimelineHash": preview.base_timeline_hash,
        },
        segmentEdit={
            "instruction": preview.instruction,
            "issueRange": preview.issue_range,
            "generationRange": preview.generation_range,
            "candidateCoreRange": preview.candidate_core_range,
        },
        promptCompilerRevision="segment-edit-v2",
        createdAt=created_at,
    )
    return snapshot.model_dump(mode="json", by_alias=True)


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


def _environment_asset_prompt(project: ProjectDto, story: StoryVersionDto) -> str:
    environment_intent = story.environment_intent.rstrip("。！？!?；; \t\r\n")
    return (
        f"为《{project.title}》生成一张9:16、2K PNG的空场景环境设计图。"
        f"环境意图：{environment_intent}。"
        "只提取环境意图中的空间、天气、家具、道具、构图和光线；"
        "即使原文提到儿童、猫咪或动作，也不得在画面中绘制人物、动物、身体局部或倒影。"
        "为后续一位约1.2米高的6至7岁儿童和一只灰白虎斑猫预留清楚的前景、中景、"
        "落脚位置与动作空间，但不要把角色画入环境板。"
        "图一是固定画风板，只负责色彩、柔和漫射光、哑光材质、轻微纸感颗粒和暖灰细轮廓线；"
        "图二是固定儿童设计，只用于匹配整套插画的轮廓精细度、柔和程度和渲染语言，不得复制儿童主体；"
        "图三是固定猫咪设计，只用于匹配毛发、轮廓和整体插画语言，不得复制猫咪主体。"
        "自然暖色但不过度橙黄，空间与道具比例可信，保持原创二维柔和数字插画。"
    )


def _environment_negative_prompt() -> str:
    return (
        "儿童、成年人、任何人物、人物局部、猫咪、其他动物、人物或动物倒影，"
        "真实摄影、照片质感、3D塑料质感、叶片微距摄影、枝条露珠素材污染，"
        "文字、Logo、水印、过度橙黄、错误透视、无法容纳角色活动的拥挤空间"
    )


def _default_asset_negative_prompt() -> str:
    return (
        "摄影写实，3D塑料质感，额外肢体，融脸，文字，Logo，水印，"
        "叶片、枝条、露珠、绿色微距摄影，禁止8岁以上的修长儿童比例，"
        "禁止青少年或成人脸型，禁止过长四肢，禁止身体比例超过约5头身，"
        "禁止儿童身高与猫咪比例失真"
    )


def _image_generation_input_snapshot(
    preview: AssetGenerationPreviewDto,
    *,
    story: StoryVersionDto,
    state: Literal["preview", "submitted"],
    created_at: datetime,
) -> ImageGenerationInputSnapshotDto:
    if preview.kind != "environment":
        raise ValueError("only environment generation has an image input snapshot")
    return ImageGenerationInputSnapshotDto(
        schemaVersion=1,
        state=state,
        kind="environment",
        subjectPolicy="empty_scene",
        sourceStoryVersionId=story.id,
        environmentIntent=story.environment_intent,
        provider=preview.provider,
        model=preview.model,
        capabilityRevision=preview.capability_revision,
        prompt=preview.prompt,
        negativePrompt=preview.negative_prompt,
        references=[
            GenerationInputReferenceDto.model_validate(
                reference.model_dump(mode="json", by_alias=True)
            )
            for reference in preview.references
        ],
        inputHash=preview.input_hash,
        promptCompilerRevision="catflow-environment-v2",
        createdAt=created_at,
    )
