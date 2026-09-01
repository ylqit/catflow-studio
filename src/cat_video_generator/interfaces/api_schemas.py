"""HTTP request contracts for the V5 video-clip workflow API."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..domain.contracts import (
    AcceptedVisualAssetPlan,
    ReferenceBinding,
    ReferenceImageDraft,
    SceneDraft,
    SceneLookDraft,
    SceneLookPlan,
    ShotAssistPatch,
    ShotCardDraft,
    ShotSuggestion,
    StoryDiagnosisOutput,
    StoryExpansionOutput,
    StoryProjectInput,
    StoryRewriteOutput,
    StoryRewriteStrategy,
    VisualAssetPlanSelection,
    VisualProfileDraft,
)
from ..domain.rendering import SequenceTransition


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class UpdateRuntimeSettingsRequest(ApiModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    planning_model: str = Field(alias="planningModel", min_length=1, max_length=200)
    image_model: str = Field(alias="imageModel", min_length=1, max_length=200)
    video_model: str = Field(alias="videoModel", min_length=1, max_length=200)
    review_model: str = Field(alias="reviewModel", min_length=1, max_length=200)
    video_resolution: Literal["480p", "720p"] = Field(alias="videoResolution")
    semantic_review_enabled: bool = Field(alias="semanticReviewEnabled")


class CreateProjectRequest(ApiModel):
    project: StoryProjectInput
    content_date: date | None = Field(default=None, alias="contentDate")


class UpdateProjectRequest(ApiModel):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    content_date: date = Field(alias="contentDate")


class OrderRequest(ApiModel):
    ids: list[UUID] = Field(min_length=1)


class SceneRequest(SceneDraft):
    pass


class ShotRequest(ShotCardDraft):
    pass


class SuggestShotsRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class DiagnoseStoryRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class ExpandStoryRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class AcceptStoryExpansionRequest(ApiModel):
    expansion: StoryExpansionOutput


class PlanVisualAssetsRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")
    storyboard_revision_id: UUID = Field(alias="storyboardRevisionId")
    structure_hash: str = Field(
        alias="structureHash", pattern=r"^[0-9a-f]{64}$"
    )
    generation_plan_id: UUID = Field(alias="generationPlanId")
    generation_plan_hash: str = Field(
        alias="generationPlanHash", pattern=r"^[0-9a-f]{64}$"
    )


class AcceptStoryDiagnosisRequest(ApiModel):
    diagnosis: StoryDiagnosisOutput
    selected_strategy: StoryRewriteStrategy | None = Field(
        default=None,
        alias="selectedStrategy",
    )
    additional_instructions: Annotated[
        str,
        Field(alias="additionalInstructions", max_length=4_000),
    ] = ""
    preserve_original: bool = Field(default=False, alias="preserveOriginal")


class RewriteStoryRequest(ApiModel):
    diagnosis_step_id: UUID = Field(alias="diagnosisStepId")
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class AcceptStoryRewriteRequest(ApiModel):
    rewrite: StoryRewriteOutput


class AcceptVisualAssetPlanRequest(ApiModel):
    plan: AcceptedVisualAssetPlan


class ReviseVisualAssetPlanRequest(ApiModel):
    selections: list[VisualAssetPlanSelection] = Field(max_length=12)
    note: str = Field(default="", max_length=2_000)


class AssistShotRequest(ApiModel):
    source_draft_revision: int = Field(alias="sourceDraftRevision", ge=1)
    candidate_asset_ids: list[UUID] = Field(
        default_factory=list,
        alias="candidateAssetIds",
        max_length=32,
    )
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class AcceptShotAssistanceRequest(ApiModel):
    source_draft_revision: int = Field(alias="sourceDraftRevision", ge=1)
    patch: ShotAssistPatch | None = None
    accepted_anchor_brief: Annotated[
        str | None,
        Field(default=None, alias="acceptedAnchorBrief", min_length=1, max_length=4_000),
    ]


class SaveAnchorBriefRequest(ApiModel):
    source_draft_revision: int = Field(alias="sourceDraftRevision", ge=1)
    brief: Annotated[str, Field(min_length=1, max_length=4_000)]


class AcceptSuggestionsRequest(ApiModel):
    look_plan: SceneLookPlan | None = Field(alias="lookPlan")
    shots: list[ShotSuggestion] = Field(min_length=1, max_length=6)
    apply_mode: Literal["replace", "update_existing"] = Field(
        default="replace",
        alias="applyMode",
    )
    source_shot_revisions: dict[UUID, Annotated[int, Field(ge=1)]] = Field(
        default_factory=dict,
        alias="sourceShotRevisions",
    )


class SequenceTransitionRequest(ApiModel):
    after_shot_id: UUID = Field(alias="afterShotId")
    transition: SequenceTransition


class BuildSequenceRequest(ApiModel):
    transitions: list[SequenceTransitionRequest] = Field(
        default_factory=list,
        max_length=500,
    )
    intro_transition: SequenceTransition | None = Field(
        default=None,
        alias="introTransition",
    )
    outro_transition: SequenceTransition | None = Field(
        default=None,
        alias="outroTransition",
    )


class ReferencesRequest(ApiModel):
    references: list[ReferenceBinding]


class VisualProfileRequest(VisualProfileDraft):
    pass


class SelectSceneLookRequest(ApiModel):
    asset_id: UUID | None = Field(alias="assetId")


class SaveSceneLookDraftRequest(ApiModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    draft: SceneLookDraft


class GenerateRequest(ApiModel):
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")
    regenerate: bool = False
    reason: Annotated[str | None, Field(max_length=1000)] = None
    expected_input_hash: Annotated[
        str | None,
        Field(default=None, alias="expectedInputHash", min_length=64, max_length=64),
    ] = None

    @property
    def retry_reason(self) -> str | None:
        if not self.regenerate:
            return None
        return self.reason or "用户在镜头节点显式重新生成"


class GenerateSceneLookRequest(GenerateRequest):
    draft_revision: int = Field(alias="draftRevision", ge=1)


class GenerateReferenceImageRequest(GenerateRequest):
    draft: ReferenceImageDraft


class PreviewReferenceImageRequest(ApiModel):
    draft: ReferenceImageDraft
    regenerate: bool = False
    reason: Annotated[str | None, Field(max_length=1000)] = None


class ReviewRequest(ApiModel):
    decision: Literal["approved", "rejected"]
    reason: Annotated[str | None, Field(max_length=2000)] = None
    select: bool = True


class RangeEditRequest(ApiModel):
    source_asset_id: UUID = Field(alias="sourceAssetId")
    start_ms: int = Field(alias="startMs", ge=0)
    end_ms: int = Field(alias="endMs", gt=0)
    instruction: Annotated[str, Field(min_length=1, max_length=4000)]
    allow_paid_generation: bool = Field(alias="allowPaidGeneration")


class ReconcileRequest(ApiModel):
    provider_task_id: Annotated[str, Field(alias="providerTaskId", min_length=3, max_length=200)]


class CancelTaskRequest(ApiModel):
    expected_status: Annotated[str, Field(alias="expectedStatus", min_length=1, max_length=32)]
    expected_provider_task_id: Annotated[
        str | None,
        Field(default=None, alias="expectedProviderTaskId", min_length=1, max_length=200),
    ] = None
    reason: Annotated[str | None, Field(default=None, max_length=1000)] = None


class SelectSequenceRequest(ApiModel):
    approve: bool = True
