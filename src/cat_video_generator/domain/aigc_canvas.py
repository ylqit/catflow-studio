"""Canvas V2 contracts and deterministic production rules.

The canvas is a projection of these typed domain objects.  Coordinates and
edges may help the editor, but they never replace the business relationships
validated here.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .contract_base import StrictModel
from .production_recipes import ReferenceAuthorityDto


class SubjectKind(StrEnum):
    PERSON = "person"
    ANIMAL = "animal"
    PRODUCT = "product"
    OBJECT = "object"
    LOCATION = "location"
    STYLE = "style"


class SubjectRole(StrEnum):
    PROTAGONIST = "protagonist"
    CO_PROTAGONIST = "co_protagonist"
    SUPPORT = "support"
    HERO_PRODUCT = "hero_product"
    PROP = "prop"
    ENVIRONMENT = "environment"


class StoryRevisionStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class StoryEventCandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    SUPERSEDED = "superseded"


def creative_brief_canvas_node_id(project_id: uuid.UUID) -> uuid.UUID:
    """Return the stable canvas identity for the project's versioned brief."""

    return uuid.uuid5(project_id, "creative-brief")


class StoryStrategy(StrEnum):
    RELATIONSHIP = "relationship"
    PROBLEM_SOLVING = "problem_solving"
    TWIST_HOOK = "twist_hook"
    COMBINED = "combined"
    LEGACY_IMPORT = "legacy_import"


class WorkspaceStatus(StrEnum):
    """User-facing readiness derived directly from production facts."""

    BLOCKED = "blocked"
    STALE = "stale"
    NEEDS_REVIEW = "needs_review"
    ACTIVE = "active"
    COMPLETE = "complete"
    READY = "ready"


class WorkspaceModuleId(StrEnum):
    SCRIPT = "script"
    ASSETS = "assets"
    PRODUCTION = "production"


class WorkspaceModuleDto(StrictModel):
    id: WorkspaceModuleId
    title: str
    order: int = Field(ge=1, le=3)
    status: WorkspaceStatus
    progress: int | None = Field(default=None, ge=0, le=100)
    attention_count: int = Field(alias="attentionCount", ge=0)
    primary_artifact_id: str | None = Field(alias="primaryArtifactId", default=None)
    blocker: str | None = None
    next_action: dict[str, str] | None = Field(alias="nextAction", default=None)


class ProjectWorkspaceShellDto(StrictModel):
    project: "WorkspaceProjectSummaryDto"
    modules: tuple[WorkspaceModuleDto, ...]
    recommended_module_id: WorkspaceModuleId = Field(alias="recommendedModuleId")
    active_task_summary: "WorkspaceActiveTaskSummaryDto" = Field(alias="activeTaskSummary")


class StoryDocumentDto(StrictModel):
    id: uuid.UUID
    title: str
    body: str
    summary: str | None = None
    revision: int = Field(ge=1)
    status: str
    source: Literal["ai", "manual", "unknown"]
    warnings: tuple[dict[str, Any], ...] = ()


class ScriptWorkspaceDto(StrictModel):
    brief: dict[str, Any] | None = None
    documents: tuple[StoryDocumentDto, ...]
    current_story_id: uuid.UUID | None = Field(alias="currentStoryId", default=None)
    recipe_instance_id: uuid.UUID | None = Field(alias="recipeInstanceId", default=None)


class ProductionFlowNodeKind(StrEnum):
    SCRIPT = "script"
    DIRECTOR_PLAN = "director_plan"
    ASSETS = "assets"
    STORYBOARD_TABLE = "storyboard_table"
    STORYBOARD = "storyboard"
    WORKBENCH = "workbench"


class ProductionFlowNodeDto(StrictModel):
    id: str
    kind: ProductionFlowNodeKind
    title: str
    subtitle: str = ""
    status: WorkspaceStatus
    position: dict[str, float]
    data: dict[str, Any] = Field(default_factory=dict)


class ProductionFlowEdgeDto(StrictModel):
    id: str
    source: str
    target: str


class ProductionFlowDto(StrictModel):
    revision: int = Field(ge=0)
    nodes: tuple[ProductionFlowNodeDto, ...]
    edges: tuple[ProductionFlowEdgeDto, ...]
    viewport: dict[str, float]
    active_storyboard_revision_id: uuid.UUID | None = Field(
        alias="activeStoryboardRevisionId", default=None
    )
    active_track_id: str | None = Field(alias="activeTrackId", default=None)
    shot_order: tuple[str, ...] = Field(alias="shotOrder")


class VideoWorkbenchReferenceDto(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    title: str
    semantic_role: str = Field(alias="semanticRole")
    ordinal: int = Field(ge=1)
    provider_eligible: bool = Field(alias="providerEligible")
    content_url: str | None = Field(alias="contentUrl", default=None)
    source_revision: int | None = Field(alias="sourceRevision", default=None)


class VideoWorkbenchVersionDto(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    status: str
    content_url: str | None = Field(alias="contentUrl", default=None)
    created_at: datetime = Field(alias="createdAt")
    selected: bool = False


class VideoWorkbenchTrackDto(StrictModel):
    id: str
    shot_ids: tuple[uuid.UUID, ...] = Field(alias="shotIds")
    title: str
    duration_seconds: int = Field(alias="durationSeconds", ge=1)
    ordered_references: tuple[VideoWorkbenchReferenceDto, ...] = Field(
        alias="orderedReferences"
    )
    prompt: str
    provider_config: dict[str, Any] = Field(alias="providerConfig")
    task: dict[str, Any] | None = None
    versions: tuple[VideoWorkbenchVersionDto, ...]
    selected_version_id: uuid.UUID | None = Field(alias="selectedVersionId", default=None)


class VideoWorkbenchDto(StrictModel):
    active_track_id: str | None = Field(alias="activeTrackId", default=None)
    tracks: tuple[VideoWorkbenchTrackDto, ...]
    approved_references: tuple[VideoWorkbenchReferenceDto, ...] = Field(
        alias="approvedReferences"
    )
    timeline: dict[str, Any] | None = None
    export_summary: dict[str, Any] | None = Field(alias="exportSummary", default=None)


class WorkspaceProjectSummaryDto(StrictModel):
    id: uuid.UUID
    title: str
    status: str
    updated_at: datetime = Field(alias="updatedAt")


class WorkspaceActiveTaskSummaryDto(StrictModel):
    active_count: int = Field(alias="activeCount", ge=0)
    attention_count: int = Field(alias="attentionCount", ge=0)
    latest_task_id: uuid.UUID | None = Field(alias="latestTaskId", default=None)
    latest_status: str | None = Field(alias="latestStatus", default=None)


class CanvasNodeType(StrEnum):
    RECIPE_GROUP = "RecipeGroupNode"
    BRIEF = "BriefNode"
    SUBJECT = "SubjectNode"
    STYLE_PRESET = "StylePresetNode"
    CHARACTER_DESIGN = "CharacterDesignNode"
    STORY_PLANNER = "StoryPlannerNode"
    STORY_EVENT = "StoryEventNode"
    STORY_SCRIPT = "StoryScriptNode"
    STORY_CANDIDATE = "StoryCandidateNode"
    STORY_CRITIC = "StoryCriticNode"
    APPROVAL_GATE = "ApprovalGateNode"
    STORYBOARD_DIRECTOR = "StoryboardDirectorNode"
    SCENE = "SceneNode"
    SHOT_BEAT = "ShotBeatNode"
    GENERATION_PLAN = "GenerationPlanNode"
    IMAGE_GENERATION = "ImageGenerationNode"
    VIDEO_GENERATION = "VideoGenerationNode"
    REVIEW = "ReviewNode"
    TIMELINE = "TimelineNode"
    REFERENCE_ASSET = "ReferenceAssetNode"
    GENERATION_BATCH = "GenerationBatchNode"
    IMAGE_ASSET = "ImageAssetNode"
    VIDEO_ASSET = "VideoAssetNode"
    VIDEO_EDIT = "VideoEditNode"
    VIDEO_SEGMENT = "VideoSegmentNode"
    PROMPT_ARTIFACT = "PromptArtifactNode"
    AUDIO_GENERATION = "AudioGenerationNode"


class CanvasPortType(StrEnum):
    BRIEF = "brief"
    SUBJECTS = "subject[]"
    CHARACTER_DESIGN = "character_design"
    STORY_EVENT = "story_event"
    STORY_REVISION = "story_revision"
    SCENE_PLAN = "scene_plan"
    SHOT_BEATS = "shot_beat[]"
    STORYBOARD_SHOTS = "storyboard_shot[]"
    SHOT_SEQUENCE = "shot_sequence"
    GENERATION_PLAN = "generation_plan"
    VIDEO_SEGMENTS = "video_segment[]"
    COMPILED_PROMPT = "compiled_prompt"
    APPROVED_ANCHOR = "approved_anchor"
    IMAGE_REFERENCES = "image_reference[]"
    IMAGE_ASSET = "image_asset"
    VIDEO_ASSET = "video_asset"
    APPROVED_ASSET = "approved_asset"
    MEDIA_REFERENCES = "media_reference[]"
    PRODUCT_SUBJECT = "product_subject"
    IMAGE_ASSETS = "image_asset[]"
    EDIT_RECIPE = "edit_recipe"
    PROMPT = "prompt"
    AUDIO_ASSET = "audio_asset"


class StoryBrief(StrictModel):
    theme: str = Field(min_length=1, max_length=2_000)
    audience: str = Field(min_length=1, max_length=300)
    genre: str = Field(min_length=1, max_length=200)
    tone: str = Field(min_length=1, max_length=300)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = Field(alias="aspectRatio")
    target_duration_seconds: int = Field(
        alias="targetDurationSeconds",
        ge=8,
        le=600,
    )
    constraints: list[str] = Field(default_factory=list, max_length=30)


class SubjectReferenceDraft(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    semantic_role: Literal[
        "front",
        "side",
        "back",
        "turnaround",
        "expression",
        "full_body",
        "outfit",
        "packshot_front",
        "label_detail",
        "material",
        "size_scale",
        "usage_scene",
        "other",
    ] = Field(alias="semanticRole")
    instruction: str = Field(default="", max_length=1_000)


class SubjectDraft(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: SubjectKind
    role: SubjectRole
    identity_anchors: list[str] = Field(
        alias="identityAnchors",
        min_length=1,
        max_length=30,
    )
    immutable_traits: list[str] = Field(
        alias="immutableTraits",
        default_factory=list,
        max_length=30,
    )
    relationship_notes: str = Field(alias="relationshipNotes", default="", max_length=2_000)
    dramatic_function: str = Field(alias="dramaticFunction", default="", max_length=1_000)
    visual_risks: list[str] = Field(alias="visualRisks", default_factory=list, max_length=30)
    references: list[SubjectReferenceDraft] = Field(default_factory=list, max_length=30)


SubjectCompletionField = Literal[
    "identityAnchors",
    "immutableTraits",
    "relationshipNotes",
    "dramaticFunction",
    "visualRisks",
]


class SubjectCompletionProposal(StrictModel):
    """A reviewable suggestion; it never becomes a subject revision by itself."""

    identity_anchors: list[str] = Field(alias="identityAnchors", min_length=1, max_length=30)
    immutable_traits: list[str] = Field(
        alias="immutableTraits", default_factory=list, max_length=30
    )
    relationship_notes: str = Field(alias="relationshipNotes", default="", max_length=2_000)
    dramatic_function: str = Field(alias="dramaticFunction", default="", max_length=1_000)
    visual_risks: list[str] = Field(alias="visualRisks", default_factory=list, max_length=30)
    rationale: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class GenerationReferenceBindingDraft(StrictModel):
    """An ordered reference intention before provider capability compilation."""

    asset_id: uuid.UUID = Field(alias="assetId")
    source_node_id: uuid.UUID | None = Field(alias="sourceNodeId", default=None)
    source_type: str = Field(alias="sourceType", default="canvas", max_length=80)
    subject_revision_id: uuid.UUID | None = Field(alias="subjectRevisionId", default=None)
    semantic_role: str = Field(alias="semanticRole", min_length=1, max_length=80)
    purpose: str = Field(default="reference", min_length=1, max_length=120)
    instruction: str = Field(default="", max_length=2_000)
    ordinal: int = Field(default=1, ge=1, le=30)
    locked: bool = False
    authority: ReferenceAuthorityDto | None = None


class CompiledProviderReference(GenerationReferenceBindingDraft):
    """The exact immutable provider-facing result of reference compilation."""

    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    provider_included: bool = Field(alias="providerIncluded")
    omission_reason: str | None = Field(alias="omissionReason", default=None, max_length=1_000)
    provider_slot: str | None = Field(alias="providerSlot", default=None, max_length=120)
    origin: str = Field(default="canvas", min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=240)
    content_url: str | None = Field(alias="contentUrl", default=None, max_length=1_000)
    evidence_level: Literal["frozen", "selected_only", "unknown"] = Field(
        alias="evidenceLevel",
        default="frozen",
    )

    @model_validator(mode="after")
    def require_omission_reason(self) -> CompiledProviderReference:
        if not self.provider_included and not (self.omission_reason or "").strip():
            raise ValueError("omissionReason is required when a reference is omitted")
        return self


class ActualReferenceBinding(CompiledProviderReference):
    """Compatibility name for persisted V2 generation configs.

    New code should use ``CompiledProviderReference``.  This class remains a real
    schema boundary because older canvas documents use the ``actualReferences``
    field name and still need strict validation.
    """


class GenerationInputPreview(StrictModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    mode: str = Field(min_length=1, max_length=80)
    capability_revision: str = Field(alias="capabilityRevision", min_length=1, max_length=120)
    prompt: str = Field(max_length=20_000)
    references: list[CompiledProviderReference] = Field(default_factory=list, max_length=30)
    blockers: list[str] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    estimated_cost_micros: int | None = Field(alias="estimatedCostMicros", default=None, ge=0)
    input_hash: str = Field(alias="inputHash", min_length=64, max_length=64)


def generation_input_hash(document: dict[str, Any]) -> str:
    """Hash the exact provider input preview using the repository-wide JSON rules."""

    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def character_design_generation_input(
    *,
    provider: str,
    model: str,
    candidate_count: int,
    prompt: str,
    references: list[dict[str, Any]],
    capability_revision: str,
) -> dict[str, Any]:
    """Return the shared exact-input document for fixed character design batches."""

    return {
        "provider": provider,
        "model": model,
        "mediaKind": "image",
        "mode": "all_reference",
        "capabilityRevision": capability_revision,
        "candidateCount": candidate_count,
        "prompt": prompt,
        "references": references,
    }


class NormalizedPoint(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class GenerationReferenceAnnotation(StrictModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    tool: Literal["rectangle", "brush", "arrow", "text", "marker", "eraser"]
    points: list[NormalizedPoint] = Field(min_length=1, max_length=500)
    label: str = Field(default="", max_length=500)


class NodeGenerationConfigDraft(StrictModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    mode: Literal[
        "text_to_image",
        "text_to_video",
        "image_to_video",
        "first_last_frame",
        "all_reference",
        "audio",
    ]
    aspect_ratio: Literal["auto", "16:9", "4:3", "1:1", "3:4", "9:16", "21:9"] = (
        Field(alias="aspectRatio")
    )
    resolution: Literal["480p", "720p", "1080p", "4k"]
    duration_seconds: int = Field(alias="durationSeconds", ge=1, le=300)
    audio_enabled: bool = Field(alias="audioEnabled", default=False)
    candidate_count: int = Field(alias="candidateCount", ge=1, le=8)
    auto_validate: bool = Field(alias="autoValidate", default=True)
    auto_link: bool = Field(alias="autoLink", default=True)
    draft_prompt: str = Field(alias="draftPrompt", default="", max_length=20_000)
    camera_motion: str = Field(alias="cameraMotion", default="static", max_length=120)
    reference_annotations: list[GenerationReferenceAnnotation] = Field(
        alias="referenceAnnotations", default_factory=list, max_length=100
    )
    actual_references: list[CompiledProviderReference] = Field(
        alias="actualReferences", default_factory=list, max_length=30
    )


_COMPLETION_FIELD_ATTRIBUTES: dict[str, str] = {
    "identityAnchors": "identity_anchors",
    "immutableTraits": "immutable_traits",
    "relationshipNotes": "relationship_notes",
    "dramaticFunction": "dramatic_function",
    "visualRisks": "visual_risks",
}


def subject_completion_missing_fields(subject: SubjectDraft) -> tuple[SubjectCompletionField, ...]:
    missing: list[SubjectCompletionField] = []
    for alias, attribute in _COMPLETION_FIELD_ATTRIBUTES.items():
        value = getattr(subject, attribute)
        if value == "" or value == []:
            missing.append(alias)  # type: ignore[arg-type]
    return tuple(missing)


def merge_subject_completion(
    source: SubjectDraft,
    proposal: SubjectCompletionProposal,
    *,
    accepted_fields: tuple[str, ...],
) -> SubjectDraft:
    unknown = set(accepted_fields) - set(_COMPLETION_FIELD_ATTRIBUTES)
    if unknown:
        raise ValueError(f"不支持的主体补全字段：{', '.join(sorted(unknown))}")
    updates = {
        attribute: getattr(proposal, attribute)
        for alias, attribute in _COMPLETION_FIELD_ATTRIBUTES.items()
        if alias in accepted_fields
    }
    return source.model_copy(update=updates, deep=True)


class StoryScorecard(StrictModel):
    opening_hook: int = Field(alias="openingHook", ge=0, le=10)
    causal_completeness: int = Field(alias="causalCompleteness", ge=0, le=10)
    subject_necessity: int = Field(alias="subjectNecessity", ge=0, le=10)
    emotional_arc: int = Field(alias="emotionalArc", ge=0, le=10)
    visualizability: int = Field(ge=0, le=10)
    duration_fit: int = Field(alias="durationFit", ge=0, le=10)
    continuity_risk: int = Field(alias="continuityRisk", ge=0, le=10)
    safety: int = Field(ge=0, le=10)
    rationale: str = Field(min_length=1, max_length=4_000)
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @property
    def average(self) -> float:
        values = (
            self.opening_hook,
            self.causal_completeness,
            self.subject_necessity,
            self.emotional_arc,
            self.visualizability,
            self.duration_fit,
            self.continuity_risk,
            self.safety,
        )
        return round(sum(values) / len(values), 2)


class SceneContinuityRules(StrictModel):
    """Scene-local facts that must not leak into another story scene."""

    location: str = Field(min_length=1, max_length=300)
    environment: Literal["indoor", "outdoor"]
    time_weather: str = Field(alias="timeWeather", min_length=1, max_length=300)
    decorations: list[str] = Field(default_factory=list, max_length=20)
    props: list[str] = Field(default_factory=list, max_length=20)
    transition_reason: str = Field(alias="transitionReason", default="", max_length=1_000)


class StorySceneOutline(StrictModel):
    scene_key: str = Field(
        alias="sceneKey",
        pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$",
    )
    title: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=1_000)
    synopsis: str = Field(min_length=1, max_length=4_000)
    duration_weight: int = Field(alias="durationWeight", ge=1, le=100)
    continuity: SceneContinuityRules


class StoryEventSceneSuggestion(StrictModel):
    scene_key: str = Field(
        alias="sceneKey",
        pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$",
    )
    title: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=1_000)
    location: str = Field(min_length=1, max_length=300)
    environment: Literal["indoor", "outdoor"]
    time_weather: str = Field(alias="timeWeather", min_length=1, max_length=300)
    transition_reason: str = Field(alias="transitionReason", default="", max_length=1_000)


class StoryEventCandidateOutput(StrictModel):
    """A concise, reviewable event direction rather than a complete screenplay."""

    title: str = Field(min_length=1, max_length=200)
    premise: str = Field(min_length=1, max_length=2_000)
    child_action: str = Field(alias="childAction", min_length=1, max_length=2_000)
    cat_participation: str = Field(
        alias="catParticipation", min_length=1, max_length=2_000
    )
    small_change: str = Field(alias="smallChange", min_length=1, max_length=2_000)
    warm_ending: str = Field(alias="warmEnding", min_length=1, max_length=2_000)
    suggested_scenes: list[StoryEventSceneSuggestion] = Field(
        alias="suggestedScenes", min_length=1, max_length=4
    )
    duration_fit_summary: str = Field(
        alias="durationFitSummary", min_length=1, max_length=1_000
    )
    requires_scene_change: bool = Field(alias="requiresSceneChange")
    cat_behavior_mode_suggestion: Literal["natural", "light_anthropomorphic"] = Field(
        alias="catBehaviorModeSuggestion"
    )


def validate_story_event_candidate(
    candidate: StoryEventCandidateOutput,
    *,
    target_duration_seconds: int,
) -> None:
    maximum_scene_count = 1 if target_duration_seconds <= 15 else math.ceil(
        target_duration_seconds / 15
    )
    if len(candidate.suggested_scenes) > maximum_scene_count:
        raise ValueError(
            f"{target_duration_seconds}秒事件最多建议{maximum_scene_count}个场景"
        )
    scene_keys = [scene.scene_key for scene in candidate.suggested_scenes]
    if len(set(scene_keys)) != len(scene_keys):
        raise ValueError("事件方案的 sceneKey 必须稳定且唯一")
    if target_duration_seconds <= 15 and candidate.requires_scene_change:
        raise ValueError("8至15秒事件必须在单一场景内完成")
    if candidate.requires_scene_change and len(candidate.suggested_scenes) < 2:
        raise ValueError("需要换场的事件方案必须提供至少两个场景建议")
    if not candidate.requires_scene_change and len(candidate.suggested_scenes) != 1:
        raise ValueError("无需换场的事件方案必须只提供一个场景建议")
    missing_transition = [
        scene.scene_key
        for scene in candidate.suggested_scenes[1:]
        if not scene.transition_reason.strip()
    ]
    if missing_transition:
        raise ValueError("事件换场必须说明叙事目的：" + ", ".join(missing_transition))


class StoryCandidateOutput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    logline: str = Field(min_length=1, max_length=2_000)
    synopsis: str = Field(min_length=1, max_length=12_000)
    scenes: list[StorySceneOutline] = Field(min_length=1, max_length=30)


class CreativeStoryCandidate(BaseModel):
    """Minimal, tolerant contract for an LLM's editable story text."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    summary: str | None = None

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_blank_summary(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class CreativeStoryCandidateBatch(BaseModel):
    """Tolerant batch envelope used only at the LLM creative-output boundary."""

    model_config = ConfigDict(extra="ignore")

    candidates: list[CreativeStoryCandidate] = Field(min_length=1, max_length=5)


class CanvasDiagnostic(StrictModel):
    code: str = Field(min_length=1, max_length=120)
    severity: Literal["warning", "blocker"]
    message: str = Field(min_length=1, max_length=2_000)
    target_id: str | None = Field(alias="targetId", default=None, max_length=200)


class CreativeStoryCandidateParseResult(StrictModel):
    batch: CreativeStoryCandidateBatch
    diagnostics: list[CanvasDiagnostic] = Field(default_factory=list)


def parse_llm_story_candidate_output(output: object) -> CreativeStoryCandidateParseResult:
    """Normalize provider creativity while keeping downstream contracts strict."""

    diagnostics: list[CanvasDiagnostic] = []

    if isinstance(output, str):
        body = output.strip()
        if not body:
            raise ValueError("LLM 创作输出不能为空")
        batch = CreativeStoryCandidateBatch(
            candidates=[
                CreativeStoryCandidate(title="未命名故事候选", body=body)
            ]
        )
        diagnostics.append(
            CanvasDiagnostic(
                code="story_candidate_unstructured",
                severity="warning",
                message="LLM 返回了非结构化文本，已保留为可编辑故事候选。",
            )
        )
    elif isinstance(output, dict) and "candidates" in output:
        if output["candidates"] == []:
            raise ValueError("LLM 创作候选批次至少包含 1 个候选")
        try:
            batch = CreativeStoryCandidateBatch.model_validate(output)
        except ValidationError as exc:
            raise ValueError(f"LLM 创作候选批次无效：{exc}") from exc
    elif isinstance(output, dict):
        body: str | None = None
        for field_name in ("body", "synopsis", "premise"):
            value = output.get(field_name)
            if isinstance(value, str) and value.strip():
                body = value
                break
        if body is None:
            raise ValueError("LLM 创作候选必须提供非空正文")

        summary: str | None = None
        for field_name in ("summary", "logline"):
            value = output.get(field_name)
            if isinstance(value, str) and value.strip():
                summary = value
                break

        try:
            batch = CreativeStoryCandidateBatch(
                candidates=[
                    CreativeStoryCandidate(
                        title=output.get("title"),
                        body=body,
                        summary=summary,
                    )
                ]
            )
        except ValidationError as exc:
            raise ValueError(f"LLM 创作候选无效：{exc}") from exc
    else:
        raise ValueError("LLM 创作输出必须是候选对象、候选批次或非空文本")

    candidate_count = len(batch.candidates)
    if candidate_count != 3:
        diagnostics.append(
            CanvasDiagnostic(
                code="story_candidate_count",
                severity="warning",
                message=f"LLM 返回了 {candidate_count} 个故事候选，预期数量为 3。",
            )
        )

    return CreativeStoryCandidateParseResult(
        batch=batch,
        diagnostics=diagnostics,
    )


def validate_story_scene_plan(
    candidate: StoryCandidateOutput,
    *,
    target_duration_seconds: int,
) -> None:
    """Validate story-driven scene changes against the supplier shot envelope."""

    maximum_scene_count = 1 if target_duration_seconds <= 15 else math.ceil(
        target_duration_seconds / 15
    )
    if len(candidate.scenes) > maximum_scene_count:
        raise ValueError(
            f"{target_duration_seconds}秒故事最多允许{maximum_scene_count}个场景，"
            "请合并没有独立叙事目的的换场"
        )
    scene_keys = [scene.scene_key for scene in candidate.scenes]
    if len(set(scene_keys)) != len(scene_keys):
        raise ValueError("故事场景 sceneKey 必须稳定且唯一")
    missing_transition = [
        scene.scene_key
        for scene in candidate.scenes[1:]
        if not scene.continuity.transition_reason.strip()
    ]
    if missing_transition:
        raise ValueError(
            "换场必须填写叙事目的：" + ", ".join(missing_transition)
        )


class StoryboardBeatOutput(BaseModel):
    """Minimal editable shot returned by a creative model.

    This is deliberately tolerant only at the LLM boundary.  The normalized
    object is subsequently checked against the approved story and execution
    constraints before it can create media work.
    """

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        str_strip_whitespace=True,
    )

    order: int = Field(ge=1, le=200)
    scene_order: int = Field(alias="sceneOrder", default=1, ge=1, le=30)
    scene_label: str | None = Field(alias="sceneLabel", default=None, max_length=160)
    title: str = Field(min_length=1, max_length=160)
    direction: str = Field(min_length=1, max_length=6_000)
    duration_seconds: int | None = Field(
        alias="durationSeconds", default=None, ge=1, le=60
    )
    duration_weight: int | None = Field(
        alias="durationWeight", default=None, ge=1, le=100
    )
    camera: str = Field(default="", max_length=2_000)
    dialogue: str = Field(default="", max_length=4_000)
    visual_description: str = Field(alias="visualDescription", default="", max_length=4_000)
    child_action: str = Field(alias="childAction", default="", max_length=2_000)
    cat_action: str = Field(alias="catAction", default="", max_length=2_000)
    spatial_relation: str = Field(alias="spatialRelation", default="", max_length=2_000)
    contact_occlusion: str = Field(alias="contactOcclusion", default="", max_length=2_000)
    shot_size: str = Field(alias="shotSize", default="", max_length=200)
    lighting: str = Field(default="", max_length=1_000)
    sound_effect: str = Field(alias="soundEffect", default="", max_length=1_000)
    music_intent: str = Field(alias="musicIntent", default="", max_length=1_000)
    wardrobe_state: str = Field(alias="wardrobeState", default="", max_length=1_000)
    prop_state: str = Field(alias="propState", default="", max_length=1_000)
    continuity_in: str = Field(alias="continuityIn", default="", max_length=2_000)
    continuity_out: str = Field(alias="continuityOut", default="", max_length=2_000)
    cut_intent: Literal["continuous", "soft_cut", "hard_cut"] = Field(
        alias="cutIntent", default="continuous"
    )

    @model_validator(mode="after")
    def require_duration(self) -> StoryboardBeatOutput:
        if self.duration_seconds is None and self.duration_weight is None:
            raise ValueError("镜头必须提供 durationSeconds 或旧版 durationWeight")
        return self

    @property
    def action(self) -> str:
        """Legacy persistence name for the canonical direction text."""

        return self.direction


class StoryboardPlanOutput(BaseModel):
    """Tolerant LLM storyboard envelope with one legacy normalization boundary."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    shots: list[StoryboardBeatOutput] = Field(min_length=1, max_length=75)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_beats(cls, value: object) -> object:
        if not isinstance(value, dict) or "shots" in value:
            return value
        beats = value.get("beats")
        if isinstance(beats, list):
            normalized: list[object] = []
            for index, item in enumerate(beats, 1):
                if not isinstance(item, dict):
                    normalized.append(item)
                    continue
                direction = item.get("direction")
                if not isinstance(direction, str) or not direction.strip():
                    direction = item.get("visualDescription") or item.get("action")
                normalized.append(
                    {
                        **item,
                        "order": item.get("order", index),
                        "direction": direction,
                    }
                )
            return {**value, "shots": normalized}

        scenes = value.get("scenes")
        if not isinstance(scenes, list):
            return value
        flattened: list[object] = []
        for scene_index, scene in enumerate(scenes, 1):
            if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
                continue
            scene_order = scene.get("sceneOrder", scene_index)
            scene_label = scene.get("sceneLabel")
            for shot in scene["shots"]:
                if not isinstance(shot, dict):
                    flattened.append(shot)
                    continue
                direction = shot.get("direction")
                if not isinstance(direction, str) or not direction.strip():
                    direction = shot.get("visualDescription") or shot.get("action")
                flattened.append(
                    {
                        **shot,
                        "order": len(flattened) + 1,
                        "sceneOrder": shot.get("sceneOrder", scene_order),
                        "sceneLabel": shot.get("sceneLabel", scene_label),
                        "direction": direction,
                        "soundEffect": shot.get("soundEffect", shot.get("soundCue", "")),
                    }
                )
        return {**value, "shots": flattened}

    @model_validator(mode="after")
    def validate_execution_shape(self) -> StoryboardPlanOutput:
        orders = [shot.order for shot in self.shots]
        if orders != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头顺序必须从1开始且连续")
        scene_orders = sorted({shot.scene_order for shot in self.shots})
        if scene_orders != list(range(1, max(scene_orders) + 1)):
            raise ValueError("分镜场景必须从1开始且连续")
        if not (
            all(shot.duration_seconds is not None for shot in self.shots)
            or all(shot.duration_weight is not None for shot in self.shots)
        ):
            raise ValueError("同一分镜必须统一使用 durationSeconds 或旧版 durationWeight")
        return self

    @property
    def beats(self) -> list[StoryboardBeatOutput]:
        """Compatibility view for persistence code during the schema transition."""

        return self.shots


class StoryboardPlanParseResult(StrictModel):
    status: Literal["ready", "needs_structuring"]
    plan: StoryboardPlanOutput | None = None
    raw_text: str | None = Field(alias="rawText", default=None, max_length=200_000)
    diagnostics: list[CanvasDiagnostic] = Field(default_factory=list)


def parse_llm_storyboard_output(output: object) -> StoryboardPlanParseResult:
    """Preserve creative output while admitting only executable shot structure."""

    if isinstance(output, str):
        raw_text = output.strip()
        if not raw_text:
            raise ValueError("LLM 分镜输出不能为空")
        return _storyboard_needs_structuring(raw_text)
    if not isinstance(output, dict):
        raise ValueError("LLM 分镜输出必须是对象或非空文本")
    try:
        plan = StoryboardPlanOutput.model_validate(output)
    except ValidationError:
        return _storyboard_needs_structuring(
            json.dumps(output, ensure_ascii=False, sort_keys=True)
        )
    return StoryboardPlanParseResult(status="ready", plan=plan)


def _storyboard_needs_structuring(raw_text: str) -> StoryboardPlanParseResult:
    return StoryboardPlanParseResult(
        status="needs_structuring",
        rawText=raw_text,
        diagnostics=[
            CanvasDiagnostic(
                code="storyboard_needs_structuring",
                severity="blocker",
                message="分镜原文需要整理为至少一个包含标题、镜头描述和有效时长的镜头",
            )
        ],
    )


def storyboard_quality_diagnostics(shots: Sequence[object]) -> list[CanvasDiagnostic]:
    """Return non-blocking creative observations for editable storyboard content."""

    if any(str(getattr(shot, "dialogue", "")).strip() for shot in shots):
        return [
            CanvasDiagnostic(
                code="storyboard_dialogue_present",
                severity="warning",
                message="分镜包含对白；请确认口型、声音和镜头时长是否适合当前成片。",
            )
        ]
    return []


class CanvasConnection(StrictModel):
    source_node_id: uuid.UUID = Field(alias="sourceNodeId")
    source_node_type: CanvasNodeType = Field(alias="sourceNodeType")
    source_port: CanvasPortType = Field(alias="sourcePort")
    target_node_id: uuid.UUID = Field(alias="targetNodeId")
    target_node_type: CanvasNodeType = Field(alias="targetNodeType")
    target_port: CanvasPortType = Field(alias="targetPort")

    @model_validator(mode="after")
    def validate_ports(self) -> CanvasConnection:
        output_ports = _NODE_OUTPUT_PORTS[self.source_node_type]
        if self.source_port not in output_ports:
            raise ValueError(
                f"{self.source_node_type.value} 不提供 {self.source_port.value} 输出"
            )
        input_ports = _NODE_INPUT_PORTS[self.target_node_type]
        if self.target_port not in input_ports:
            raise ValueError(
                f"{self.target_node_type.value} 不接受 {self.target_port.value} 输入"
            )
        compatible_targets = _PORT_COMPATIBILITY[self.source_port]
        if self.target_port not in compatible_targets:
            raise ValueError(
                f"{self.source_port.value} 不接受连接到 {self.target_port.value}"
            )
        if self.source_node_id == self.target_node_id:
            raise ValueError("画布节点不能连接到自身")
        return self


class PromptRunDraft(StrictModel):
    purpose: str = Field(min_length=1, max_length=120)
    node_id: uuid.UUID | None = Field(alias="nodeId", default=None)
    business_object_type: str = Field(alias="businessObjectType", min_length=1, max_length=80)
    business_object_id: uuid.UUID = Field(alias="businessObjectId")
    parent_run_id: uuid.UUID | None = Field(alias="parentRunId", default=None)
    template_name: str = Field(alias="templateName", min_length=1, max_length=160)
    template_version: str = Field(alias="templateVersion", min_length=1, max_length=80)
    system_prompt: str = Field(alias="systemPrompt", default="", max_length=100_000)
    user_prompt: str = Field(alias="userPrompt", min_length=1, max_length=100_000)
    final_prompt: str = Field(alias="finalPrompt", min_length=1, max_length=200_000)
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    provider_request_snapshot: dict[str, Any] = Field(alias="providerRequestSnapshot")
    input_snapshot: dict[str, Any] = Field(alias="inputSnapshot")
    parameters: dict[str, Any] = Field(default_factory=dict)
    provider_internal_transform: Literal["not_observable"] = Field(
        alias="providerInternalTransform",
        default="not_observable",
    )


_NARRATIVE_ROLES = {
    SubjectRole.PROTAGONIST,
    SubjectRole.CO_PROTAGONIST,
    SubjectRole.SUPPORT,
}


def validate_story_inputs(
    brief: StoryBrief,
    subjects: tuple[SubjectDraft, ...],
) -> None:
    del brief
    narrative_subjects = [item for item in subjects if item.role in _NARRATIVE_ROLES]
    if len(narrative_subjects) < 2:
        raise ValueError("故事策略生成至少需要至少两个叙事主体")
    normalized_names = [item.name.casefold() for item in subjects]
    if len(normalized_names) != len(set(normalized_names)):
        raise ValueError("同一项目内主体名称不能重复")


def allocate_durations(
    total_seconds: int,
    weights: tuple[int, ...],
    *,
    minimum_seconds: int = 1,
) -> tuple[int, ...]:
    """Allocate an exact integer duration using stable largest remainders."""

    if not weights or any(weight <= 0 for weight in weights):
        raise ValueError("时长权重必须为正整数")
    if minimum_seconds < 1:
        raise ValueError("最小时长必须为正整数")
    if total_seconds < len(weights) * minimum_seconds:
        raise ValueError("总时长不足以满足每段最小时长")

    allocations = [0] * len(weights)
    active = set(range(len(weights)))
    remaining = total_seconds
    while active:
        active_weight = sum(weights[index] for index in active)
        under_minimum = [
            index
            for index in active
            if remaining * weights[index] / active_weight < minimum_seconds
        ]
        if not under_minimum:
            raw = {
                index: remaining * weights[index] / active_weight for index in active
            }
            for index, value in raw.items():
                allocations[index] = math.floor(value)
            remainder = remaining - sum(allocations[index] for index in active)
            order = sorted(
                active,
                key=lambda index: (-(raw[index] - math.floor(raw[index])), index),
            )
            for index in order[:remainder]:
                allocations[index] += 1
            break
        for index in under_minimum:
            allocations[index] = minimum_seconds
            remaining -= minimum_seconds
            active.remove(index)

    return tuple(allocations)


def allocate_bounded_durations(
    total_seconds: int,
    weights: tuple[int, ...],
    *,
    minimum_seconds: int,
    maximum_seconds: int,
) -> tuple[int, ...]:
    """Allocate an exact total while respecting provider clip duration bounds."""

    if not weights or any(weight <= 0 for weight in weights):
        raise ValueError("时长权重必须为正整数")
    if minimum_seconds < 1 or maximum_seconds < minimum_seconds:
        raise ValueError("供应商时长边界无效")
    if not minimum_seconds * len(weights) <= total_seconds <= maximum_seconds * len(weights):
        raise ValueError("当前 Beat 数量无法适配供应商时长范围")

    allocations = [minimum_seconds] * len(weights)
    remaining = total_seconds - sum(allocations)
    while remaining:
        active = [index for index, value in enumerate(allocations) if value < maximum_seconds]
        active_weight = sum(weights[index] for index in active)
        increments = [0] * len(weights)
        for index in active:
            share = remaining * weights[index] // active_weight
            increments[index] = min(maximum_seconds - allocations[index], share)
        distributed = sum(increments)
        if distributed == 0:
            index = max(
                active,
                key=lambda item: (
                    remaining * weights[item] % active_weight,
                    weights[item],
                    -item,
                ),
            )
            increments[index] = 1
            distributed = 1
        for index, increment in enumerate(increments):
            allocations[index] += increment
        remaining -= distributed
    return tuple(allocations)


def approve_story_revision(
    current_status: StoryRevisionStatus,
    *,
    scorecard: StoryScorecard | None,
    requires_scorecard: bool,
    revision_subject_ids: tuple[uuid.UUID, ...],
    required_subject_ids: tuple[uuid.UUID, ...],
) -> StoryRevisionStatus:
    if current_status is not StoryRevisionStatus.CANDIDATE:
        raise ValueError("只有候选故事版本可以批准")
    if requires_scorecard and scorecard is None:
        raise ValueError("Legacy 结构化故事批准前必须完成评审评分")
    missing = set(required_subject_ids) - set(revision_subject_ids)
    if missing:
        raise ValueError(f"故事版本缺少主体：{len(missing)} 个")
    return StoryRevisionStatus.APPROVED


_NODE_INPUT_PORTS: dict[CanvasNodeType, frozenset[CanvasPortType]] = {
    CanvasNodeType.RECIPE_GROUP: frozenset(),
    CanvasNodeType.BRIEF: frozenset(),
    CanvasNodeType.SUBJECT: frozenset(),
    CanvasNodeType.STYLE_PRESET: frozenset(),
    CanvasNodeType.CHARACTER_DESIGN: frozenset(
        {
            CanvasPortType.SUBJECTS,
            CanvasPortType.STORY_REVISION,
            CanvasPortType.IMAGE_REFERENCES,
        }
    ),
    CanvasNodeType.STORY_PLANNER: frozenset(
        {CanvasPortType.BRIEF, CanvasPortType.SUBJECTS}
    ),
    CanvasNodeType.STORY_EVENT: frozenset({CanvasPortType.STORY_EVENT}),
    CanvasNodeType.STORY_SCRIPT: frozenset({CanvasPortType.STORY_EVENT}),
    CanvasNodeType.STORY_CANDIDATE: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.STORY_CRITIC: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.APPROVAL_GATE: frozenset(
        {
            CanvasPortType.BRIEF,
            CanvasPortType.STORY_EVENT,
            CanvasPortType.STORY_REVISION,
            CanvasPortType.CHARACTER_DESIGN,
            CanvasPortType.SHOT_SEQUENCE,
            CanvasPortType.GENERATION_PLAN,
            CanvasPortType.COMPILED_PROMPT,
        }
    ),
    CanvasNodeType.STORYBOARD_DIRECTOR: frozenset(
        {
            CanvasPortType.STORY_REVISION,
            CanvasPortType.SUBJECTS,
            CanvasPortType.CHARACTER_DESIGN,
            CanvasPortType.IMAGE_REFERENCES,
        }
    ),
    CanvasNodeType.SCENE: frozenset({CanvasPortType.SCENE_PLAN}),
    CanvasNodeType.SHOT_BEAT: frozenset(
        {
            CanvasPortType.SHOT_BEATS,
            CanvasPortType.STORYBOARD_SHOTS,
            CanvasPortType.SUBJECTS,
        }
    ),
    CanvasNodeType.GENERATION_PLAN: frozenset({CanvasPortType.SHOT_SEQUENCE}),
    CanvasNodeType.IMAGE_GENERATION: frozenset(
        {
            CanvasPortType.SHOT_BEATS,
            CanvasPortType.SUBJECTS,
            CanvasPortType.IMAGE_REFERENCES,
            CanvasPortType.MEDIA_REFERENCES,
            CanvasPortType.PROMPT,
            CanvasPortType.VIDEO_SEGMENTS,
            CanvasPortType.COMPILED_PROMPT,
        }
    ),
    CanvasNodeType.VIDEO_GENERATION: frozenset(
        {
            CanvasPortType.SHOT_BEATS,
            CanvasPortType.SUBJECTS,
            CanvasPortType.IMAGE_REFERENCES,
            CanvasPortType.MEDIA_REFERENCES,
            CanvasPortType.IMAGE_ASSET,
            CanvasPortType.PROMPT,
            CanvasPortType.VIDEO_SEGMENTS,
            CanvasPortType.APPROVED_ANCHOR,
        }
    ),
    CanvasNodeType.REVIEW: frozenset(
        {
            CanvasPortType.IMAGE_ASSET,
            CanvasPortType.VIDEO_ASSET,
            CanvasPortType.APPROVED_ANCHOR,
        }
    ),
    CanvasNodeType.TIMELINE: frozenset({CanvasPortType.APPROVED_ASSET}),
    CanvasNodeType.REFERENCE_ASSET: frozenset(),
    CanvasNodeType.GENERATION_BATCH: frozenset(
        {CanvasPortType.PRODUCT_SUBJECT, CanvasPortType.MEDIA_REFERENCES}
    ),
    CanvasNodeType.IMAGE_ASSET: frozenset({CanvasPortType.IMAGE_ASSETS}),
    CanvasNodeType.VIDEO_ASSET: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasNodeType.VIDEO_EDIT: frozenset(
        {CanvasPortType.VIDEO_ASSET, CanvasPortType.MEDIA_REFERENCES}
    ),
    CanvasNodeType.VIDEO_SEGMENT: frozenset(
        {CanvasPortType.EDIT_RECIPE, CanvasPortType.GENERATION_PLAN}
    ),
    CanvasNodeType.PROMPT_ARTIFACT: frozenset({CanvasPortType.VIDEO_SEGMENTS}),
    CanvasNodeType.AUDIO_GENERATION: frozenset({CanvasPortType.PROMPT}),
}

_NODE_OUTPUT_PORTS: dict[CanvasNodeType, frozenset[CanvasPortType]] = {
    CanvasNodeType.RECIPE_GROUP: frozenset(),
    CanvasNodeType.BRIEF: frozenset({CanvasPortType.BRIEF}),
    CanvasNodeType.SUBJECT: frozenset(
        {CanvasPortType.SUBJECTS, CanvasPortType.PRODUCT_SUBJECT}
    ),
    CanvasNodeType.STYLE_PRESET: frozenset({CanvasPortType.IMAGE_REFERENCES}),
    CanvasNodeType.CHARACTER_DESIGN: frozenset(
        {CanvasPortType.CHARACTER_DESIGN, CanvasPortType.IMAGE_ASSET}
    ),
    CanvasNodeType.STORY_PLANNER: frozenset(
        {CanvasPortType.STORY_EVENT, CanvasPortType.STORY_REVISION}
    ),
    CanvasNodeType.STORY_EVENT: frozenset({CanvasPortType.STORY_EVENT}),
    CanvasNodeType.STORY_SCRIPT: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.STORY_CANDIDATE: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.STORY_CRITIC: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasNodeType.APPROVAL_GATE: frozenset(
        {
            CanvasPortType.BRIEF,
            CanvasPortType.STORY_EVENT,
            CanvasPortType.STORY_REVISION,
            CanvasPortType.CHARACTER_DESIGN,
            CanvasPortType.SHOT_SEQUENCE,
            CanvasPortType.GENERATION_PLAN,
            CanvasPortType.COMPILED_PROMPT,
        }
    ),
    CanvasNodeType.STORYBOARD_DIRECTOR: frozenset({CanvasPortType.SCENE_PLAN}),
    CanvasNodeType.SCENE: frozenset(
        {CanvasPortType.SHOT_BEATS, CanvasPortType.STORYBOARD_SHOTS}
    ),
    CanvasNodeType.SHOT_BEAT: frozenset(
        {CanvasPortType.SHOT_BEATS, CanvasPortType.STORYBOARD_SHOTS}
    ),
    CanvasNodeType.GENERATION_PLAN: frozenset({CanvasPortType.GENERATION_PLAN}),
    CanvasNodeType.IMAGE_GENERATION: frozenset(
        {CanvasPortType.IMAGE_ASSET, CanvasPortType.APPROVED_ANCHOR}
    ),
    CanvasNodeType.VIDEO_GENERATION: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasNodeType.REVIEW: frozenset(
        {CanvasPortType.APPROVED_ASSET, CanvasPortType.APPROVED_ANCHOR}
    ),
    CanvasNodeType.TIMELINE: frozenset(),
    CanvasNodeType.REFERENCE_ASSET: frozenset({CanvasPortType.MEDIA_REFERENCES}),
    CanvasNodeType.GENERATION_BATCH: frozenset({CanvasPortType.IMAGE_ASSETS}),
    CanvasNodeType.IMAGE_ASSET: frozenset(
        {CanvasPortType.IMAGE_ASSET, CanvasPortType.MEDIA_REFERENCES}
    ),
    CanvasNodeType.VIDEO_ASSET: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasNodeType.VIDEO_EDIT: frozenset({CanvasPortType.EDIT_RECIPE}),
    CanvasNodeType.VIDEO_SEGMENT: frozenset(
        {CanvasPortType.VIDEO_ASSET, CanvasPortType.VIDEO_SEGMENTS}
    ),
    CanvasNodeType.PROMPT_ARTIFACT: frozenset(
        {CanvasPortType.PROMPT, CanvasPortType.COMPILED_PROMPT}
    ),
    CanvasNodeType.AUDIO_GENERATION: frozenset({CanvasPortType.AUDIO_ASSET}),
}

_PORT_COMPATIBILITY: dict[CanvasPortType, frozenset[CanvasPortType]] = {
    CanvasPortType.BRIEF: frozenset({CanvasPortType.BRIEF}),
    CanvasPortType.SUBJECTS: frozenset({CanvasPortType.SUBJECTS}),
    CanvasPortType.CHARACTER_DESIGN: frozenset({CanvasPortType.CHARACTER_DESIGN}),
    CanvasPortType.STORY_EVENT: frozenset({CanvasPortType.STORY_EVENT}),
    CanvasPortType.STORY_REVISION: frozenset({CanvasPortType.STORY_REVISION}),
    CanvasPortType.SCENE_PLAN: frozenset({CanvasPortType.SCENE_PLAN}),
    CanvasPortType.SHOT_BEATS: frozenset({CanvasPortType.SHOT_BEATS}),
    CanvasPortType.STORYBOARD_SHOTS: frozenset({CanvasPortType.STORYBOARD_SHOTS}),
    CanvasPortType.SHOT_SEQUENCE: frozenset({CanvasPortType.SHOT_SEQUENCE}),
    CanvasPortType.GENERATION_PLAN: frozenset({CanvasPortType.GENERATION_PLAN}),
    CanvasPortType.VIDEO_SEGMENTS: frozenset({CanvasPortType.VIDEO_SEGMENTS}),
    CanvasPortType.COMPILED_PROMPT: frozenset({CanvasPortType.COMPILED_PROMPT}),
    CanvasPortType.APPROVED_ANCHOR: frozenset({CanvasPortType.APPROVED_ANCHOR}),
    CanvasPortType.IMAGE_REFERENCES: frozenset({CanvasPortType.IMAGE_REFERENCES}),
    CanvasPortType.IMAGE_ASSET: frozenset(
        {CanvasPortType.IMAGE_ASSET, CanvasPortType.IMAGE_REFERENCES}
    ),
    CanvasPortType.VIDEO_ASSET: frozenset({CanvasPortType.VIDEO_ASSET}),
    CanvasPortType.APPROVED_ASSET: frozenset({CanvasPortType.APPROVED_ASSET}),
    CanvasPortType.MEDIA_REFERENCES: frozenset({CanvasPortType.MEDIA_REFERENCES}),
    CanvasPortType.PRODUCT_SUBJECT: frozenset({CanvasPortType.PRODUCT_SUBJECT}),
    CanvasPortType.IMAGE_ASSETS: frozenset({CanvasPortType.IMAGE_ASSETS}),
    CanvasPortType.EDIT_RECIPE: frozenset({CanvasPortType.EDIT_RECIPE}),
    CanvasPortType.PROMPT: frozenset({CanvasPortType.PROMPT}),
    CanvasPortType.AUDIO_ASSET: frozenset({CanvasPortType.AUDIO_ASSET}),
}
