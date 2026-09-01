"""Fixed-IP production recipes and their human-review contracts."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Sequence

from pydantic import ConfigDict, Field, model_validator

from .contract_base import StrictModel
from .rendering import SequenceTransition, SequenceTransitionType


class ProductionRecipeKey(StrEnum):
    HEALING_CHILD_CAT_V1 = "healing_child_cat_v1"


class VisualPresetKey(StrEnum):
    HEALING_CHILD_CAT_LINE_TEXTURE = "healing_child_cat_line_texture_v3"
    HEALING_CHILD_CAT_STYLE_BOARD_V4 = "healing_child_cat_style_board_v4"


CANON_V2_PROFILE_ID = "canon-v2-healing-child-cat"
CANON_V2_STYLE_POSITIVE = (
    "原创柔和水彩画风",
    "柔和纸张纹理",
    "低对比自然光",
)
CANON_V2_STYLE_NEGATIVE = (
    "准写实",
    "3D塑料感",
)
CANON_V3_PROFILE_ID = "canon-v3-healing-child-cat-line-texture"
CANON_V3_STYLE_POSITIVE = (
    "细腻柔和的数字插画材质",
    "克制轮廓线",
    "湿润半透明高光",
    "柔和漫射光",
    "自然层次",
)
CANON_V3_STYLE_NEGATIVE = (
    "摄影写实",
    "复制参考物体或构图",
    "复制参考图中的叶片、露珠或微距构图",
    "绿色污染",
    "改变儿童或猫咪身份",
    "同时混入旧室内或户外水彩参考",
)

CANON_V4_PROFILE_ID = "canon-v4-healing-child-cat-style-board"
CANON_V4_STYLE_SOURCE_KEY = "style_source:leaf_material_v1"
CANON_V4_STYLE_BOARD_KEY = "style:healing_line_texture_v4"
CANON_V4_STYLE_POSITIVE = (
    "原创二维柔和数字插画",
    "细而克制的暖灰色轮廓线",
    "细腻哑光肤色、灰白毛发与布料材质",
    "轻微纸感颗粒",
    "柔和漫射光",
    "低到中等对比度",
    "自然但不过度鲜艳的色彩",
)
CANON_V4_STYLE_NEGATIVE = (
    "摄影写实",
    "3D塑料玩具质感",
    "角色身份、年龄、发型、毛色或身体结构漂移",
    "无剧情依据地复制画风来源中的物体、颜色或构图",
    "文字、Logo、水印或供应商界面",
)
CANON_V4_STYLE_SOURCE_EXCLUSIONS = (
    "叶片、枝条、花朵与草地",
    "露珠、水滴与微距构图",
    "绿色主导配色",
    "摄影属性",
)


class ReferenceAuthorityRole(StrEnum):
    IDENTITY = "identity"
    EPISODE_APPEARANCE = "episode_appearance"
    PAIR_SCALE = "pair_scale"
    ENVIRONMENT = "environment"
    STYLE_SOURCE = "style_source"
    STYLE_BOARD = "style_board"


class ReferenceAuthorityDto(StrictModel):
    """A reference's single visual responsibility and provider eligibility."""

    role: ReferenceAuthorityRole
    provider_eligible: bool = Field(alias="providerEligible")
    priority: int = Field(ge=0, le=100)
    locked_traits: tuple[str, ...] = Field(alias="lockedTraits", default=())
    mutable_traits: tuple[str, ...] = Field(alias="mutableTraits", default=())
    forbidden_transfer: tuple[str, ...] = Field(alias="forbiddenTransfer", default=())


class QualityTier(StrEnum):
    QUICK = "quick"
    BALANCED = "balanced"
    PREMIUM = "premium"


class CatBehaviorMode(StrEnum):
    NATURAL = "natural"
    LIGHT_ANTHROPOMORPHIC = "light_anthropomorphic"


def recipe_task_source_hash(
    *,
    payload: dict[str, Any],
    instance_id: uuid.UUID,
    expected_revision: int,
    phase: str,
) -> str:
    """Fingerprint the immutable recipe inputs captured at enqueue time."""
    document = {
        "payload": payload,
        "recipeInstanceId": str(instance_id),
        "expectedInstanceRevision": expected_revision,
        "phase": phase,
    }
    return hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


class RecipeStage(StrEnum):
    CONCEPT = "concept"
    STORYBOARD = "storyboard"
    ANCHORS = "anchors"
    VIDEO = "video"
    SEQUENCE = "sequence"
    COMPLETE = "complete"


class RecipePhaseKey(StrEnum):
    """User-facing six-stage recipe phase derived from approved domain state."""

    CREATIVE = "creative"
    STORY = "story"
    CHARACTER_DESIGN = "character_design"
    STORYBOARD = "storyboard"
    RENDER = "render"
    EXPORT = "export"
    COMPLETE = "complete"


class CharacterDesignSlot(StrEnum):
    CHILD = "child"
    CAT = "cat"
    PAIR_SCALE = "pair_scale"


CHARACTER_DESIGN_SEMANTIC_ROLE_BY_SLOT: dict[CharacterDesignSlot, str] = {
    CharacterDesignSlot.CHILD: "appearance",
    CharacterDesignSlot.CAT: "pose",
    CharacterDesignSlot.PAIR_SCALE: "scale",
}


class CharacterDesignRunStage(StrEnum):
    """The paid character-design boundary selected by the creator.

    Canon v4 deliberately generates the two identity-bearing episode designs before
    the pair-scale board.  The legacy ``all`` value remains readable for v3 tasks and
    persisted recovery snapshots that atomically scheduled all three slots.
    """

    ALL = "all"
    IDENTITY = "identity"
    PAIR_SCALE = "pair_scale"


class VisualPresetSlotDto(StrictModel):
    semantic_key: str = Field(alias="semanticKey", min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=120)
    role: Literal["person", "cat", "style"]
    purpose: Literal["identity", "style"]
    required: bool = True
    asset_id: uuid.UUID | None = Field(alias="assetId", default=None)
    content_url: str | None = Field(alias="contentUrl", default=None)
    thumbnail_url: str | None = Field(alias="thumbnailUrl", default=None)
    approval_status: str = Field(alias="approvalStatus", default="missing")
    sha256: str | None = None
    instruction: str = Field(min_length=1, max_length=1_000)
    authority: ReferenceAuthorityDto | None = None


class VisualPresetProfileDto(StrictModel):
    key: VisualPresetKey
    canon_profile_id: str = Field(alias="canonProfileId")
    title: str
    description: str
    version: int = Field(ge=1)
    ready: bool
    slots: list[VisualPresetSlotDto]


class HumanReviewDecision(StrEnum):
    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    OVERRIDE = "override"


class StoryboardRevisionStatus(StrEnum):
    DRAFT = "draft"
    STRUCTURE_APPROVED = "structure_approved"
    PRODUCTION_APPROVED = "production_approved"
    CHANGES_REQUESTED = "changes_requested"
    SUPERSEDED = "superseded"


class GenerationPlanStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    STALE = "stale"


class EditorialCutIntent(StrEnum):
    CONTINUOUS = "continuous"
    SOFT_CUT = "soft_cut"
    HARD_CUT = "hard_cut"


class StoryboardReviewTarget(StrEnum):
    STRUCTURE = "storyboard_structure"
    GENERATION_PLAN = "generation_plan"
    PRODUCTION_PACKAGE = "storyboard_package"


class ImageModelCapability(StrictModel):
    """Versioned image limits and tariff inputs used before a paid request."""

    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    maximum_image_references: int = Field(alias="maximumImageReferences", ge=0, le=20)
    supported_modes: tuple[str, ...] = Field(alias="supportedModes")
    supported_resolutions: tuple[str, ...] = Field(alias="supportedResolutions")
    supported_aspect_ratios: tuple[str, ...] = Field(alias="supportedAspectRatios")
    supported_candidate_counts: tuple[int, ...] = Field(alias="supportedCandidateCounts")
    input_image_cost_micros: int = Field(alias="inputImageCostMicros", ge=0)
    output_image_cost_micros: int = Field(alias="outputImageCostMicros", ge=0)

    def estimate_cost_micros(
        self,
        *,
        reference_count: int,
        candidate_count: int,
    ) -> int:
        if not 0 <= reference_count <= self.maximum_image_references:
            raise ValueError("图片参考数量超出当前模型能力")
        if candidate_count not in self.supported_candidate_counts:
            raise ValueError("图片候选数量不在当前模型能力范围内")
        return candidate_count * (
            self.output_image_cost_micros
            + reference_count * self.input_image_cost_micros
        )

    def provider_capability_document(self) -> dict[str, object]:
        return {
            "capabilityRevision": self.capability_revision,
            "maxReferenceImages": self.maximum_image_references,
            "modes": list(self.supported_modes),
            "aspectRatios": list(self.supported_aspect_ratios),
            "resolutions": list(self.supported_resolutions),
            "durations": [],
            "candidateCounts": list(self.supported_candidate_counts),
            "audio": False,
            "inputImageCostMicros": self.input_image_cost_micros,
            "outputImageCostMicros": self.output_image_cost_micros,
        }


SEEDREAM_5_0_CAPABILITY = ImageModelCapability(
    provider="volcengine-ark-standard",
    model="doubao-seedream-5-0-260128",
    capabilityRevision="ark-seedream-5.0-260128-tariff-2026-08",
    maximumImageReferences=14,
    supportedModes=("text_to_image", "all_reference"),
    supportedResolutions=("2K",),
    supportedAspectRatios=("9:16", "16:9", "1:1"),
    supportedCandidateCounts=(1, 2, 3, 4),
    inputImageCostMicros=20_000,
    outputImageCostMicros=300_000,
)


class VideoModelCapability(StrictModel):
    """Versioned limits used to validate generation plans before a provider call."""

    provider: str
    model: str
    capability_revision: str = Field(alias="capabilityRevision")
    minimum_duration_seconds: int = Field(alias="minimumDurationSeconds", ge=1, le=60)
    maximum_duration_seconds: int = Field(alias="maximumDurationSeconds", ge=1, le=60)
    supported_durations: tuple[int, ...] = Field(alias="supportedDurations")
    supports_multi_shot: bool = Field(alias="supportsMultiShot")
    maximum_editorial_shots: int = Field(alias="maximumEditorialShots", ge=1, le=20)
    maximum_image_references: int = Field(alias="maximumImageReferences", ge=0, le=20)
    maximum_video_references: int = Field(alias="maximumVideoReferences", ge=0, le=20)
    maximum_audio_references: int = Field(alias="maximumAudioReferences", ge=0, le=20)
    supports_first_frame: bool = Field(alias="supportsFirstFrame")
    first_frame_excludes_references: bool = Field(alias="firstFrameExcludesReferences")
    supports_native_audio: bool = Field(alias="supportsNativeAudio")
    supported_resolutions: tuple[str, ...] = Field(alias="supportedResolutions")
    supported_aspect_ratios: tuple[str, ...] = Field(alias="supportedAspectRatios")
    image_call_cost_micros: int | None = Field(alias="imageCallCostMicros", default=None)
    video_call_cost_micros: int | None = Field(alias="videoCallCostMicros", default=None)

    @model_validator(mode="after")
    def validate_duration_profile(self) -> VideoModelCapability:
        if self.maximum_duration_seconds < self.minimum_duration_seconds:
            raise ValueError("视频模型最大时长不能小于最小时长")
        expected = tuple(range(self.minimum_duration_seconds, self.maximum_duration_seconds + 1))
        if self.supported_durations != expected:
            raise ValueError("当前能力档案必须显式列出连续可用时长")
        if not self.supports_multi_shot and self.maximum_editorial_shots != 1:
            raise ValueError("不支持多镜头的模型每个片段只能包含一个导演分镜")
        return self


SEEDANCE_2_0_CAPABILITY = VideoModelCapability(
    provider="ark",
    model="doubao-seedance-2-0-mini-260615",
    capabilityRevision="ark-seedance-2.0-2026-06",
    minimumDurationSeconds=4,
    maximumDurationSeconds=15,
    supportedDurations=tuple(range(4, 16)),
    supportsMultiShot=True,
    maximumEditorialShots=4,
    maximumImageReferences=9,
    maximumVideoReferences=9,
    maximumAudioReferences=1,
    supportsFirstFrame=True,
    firstFrameExcludesReferences=True,
    supportsNativeAudio=True,
    supportedResolutions=("480p", "720p"),
    supportedAspectRatios=("9:16", "16:9", "1:1"),
)


@dataclass(frozen=True, slots=True)
class EditorialShotDescriptor:
    shot_beat_id: uuid.UUID
    scene_id: uuid.UUID
    duration_seconds: int
    cut_intent: EditorialCutIntent = EditorialCutIntent.CONTINUOUS
    wardrobe_state: str = ""
    prop_state: str = ""


@dataclass(frozen=True, slots=True)
class GenerationClipProposal:
    editorial_shots: tuple[EditorialShotDescriptor, ...]
    duration_seconds: int

    @property
    def shot_beat_ids(self) -> tuple[uuid.UUID, ...]:
        return tuple(item.shot_beat_id for item in self.editorial_shots)


class StoryboardCreationMode(StrEnum):
    FROM_STORY = "from_story"
    FROM_CHARACTERS = "from_characters"
    MANUAL = "manual"


class TemporalBeatPhase(StrEnum):
    BEGINNING = "beginning"
    CHANGE = "change"
    WARM_ENDING = "warm_ending"


class SoundPlan(StrictModel):
    ambient: list[str] = Field(min_length=1, max_length=8)
    foley: list[str] = Field(min_length=1, max_length=8)
    music_mood: str = Field(alias="musicMood", min_length=1, max_length=200)
    dialogue_policy: Literal["none"] = Field(alias="dialoguePolicy", default="none")


class EpisodeRules(StrictModel):
    """Per-episode choices that become immutable after story approval."""

    person_wardrobe: str = Field(alias="personWardrobe", min_length=1, max_length=500)
    time_weather: str = Field(alias="timeWeather", min_length=1, max_length=300)
    main_scene: str = Field(alias="mainScene", min_length=1, max_length=500)
    environment: Literal["indoor", "outdoor"]
    core_props: list[str] = Field(alias="coreProps", default_factory=list, max_length=12)
    cat_behavior_mode: CatBehaviorMode = Field(alias="catBehaviorMode")
    sound_plan: SoundPlan = Field(alias="soundPlan")
    style_positive: list[str] = Field(alias="stylePositive", min_length=3, max_length=10)
    style_excluded: list[str] = Field(alias="styleExcluded", min_length=2, max_length=10)
    canon_profile_id: str = Field(
        alias="canonProfileId",
        pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$",
    )


class TemporalBeat(StrictModel):
    phase: TemporalBeatPhase
    start_second: int = Field(alias="startSecond", ge=0, le=15)
    end_second: int = Field(alias="endSecond", gt=0, le=15)
    child_action: str = Field(alias="childAction", min_length=1, max_length=1_000)
    cat_action: str = Field(alias="catAction", min_length=1, max_length=1_000)
    camera: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_interval(self) -> TemporalBeat:
        if self.end_second <= self.start_second:
            raise ValueError("动作节拍结束时间必须晚于开始时间")
        return self


class QualityTierPolicy(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
        frozen=True,
    )

    anchor_candidate_count: int = Field(alias="anchorCandidateCount", ge=1, le=8)
    video_candidate_count: int = Field(alias="videoCandidateCount", ge=1, le=8)
    character_design_candidate_count: int = Field(
        alias="characterDesignCandidateCount", ge=1, le=8
    )


class ProductionRecipeDefinition(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
        frozen=True,
    )

    key: ProductionRecipeKey
    title: str
    description: str
    default_duration_seconds: int = Field(alias="defaultDurationSeconds", ge=8, le=60)
    minimum_duration_seconds: int = Field(alias="minimumDurationSeconds", ge=8, le=60)
    maximum_duration_seconds: int = Field(alias="maximumDurationSeconds", ge=8, le=60)
    minimum_shot_seconds: int = Field(alias="minimumShotSeconds", ge=8, le=15)
    maximum_shot_seconds: int = Field(alias="maximumShotSeconds", ge=8, le=15)
    minimum_editorial_shot_seconds: int = Field(
        alias="minimumEditorialShotSeconds", ge=2, le=15
    )
    default_editorial_shot_count: int = Field(
        alias="defaultEditorialShotCount", ge=2, le=4
    )
    maximum_editorial_shots_per_clip: int = Field(
        alias="maximumEditorialShotsPerClip", ge=1, le=20
    )
    aspect_ratio: Literal["9:16"] = Field(alias="aspectRatio")
    resolution: Literal["720p"]
    story_candidate_count: int = Field(alias="storyCandidateCount", ge=1, le=8)
    quality_tiers: dict[QualityTier, QualityTierPolicy] = Field(alias="qualityTiers")


class ProductionRecipeInstanceDraft(StrictModel):
    recipe_key: ProductionRecipeKey = Field(
        alias="recipeKey",
        default=ProductionRecipeKey.HEALING_CHILD_CAT_V1,
    )
    theme: str = Field(min_length=1, max_length=2_000)
    inspiration_key: str | None = Field(
        alias="inspirationKey",
        default=None,
        max_length=80,
    )
    target_duration_seconds: int = Field(
        alias="targetDurationSeconds",
        default=15,
        ge=8,
        le=60,
    )
    quality_tier: QualityTier = Field(
        alias="qualityTier",
        default=QualityTier.BALANCED,
    )


class ProductionRecipeInstancePatch(StrictModel):
    theme: str | None = Field(default=None, min_length=1, max_length=2_000)
    inspiration_key: str | None = Field(
        alias="inspirationKey",
        default=None,
        max_length=80,
    )
    target_duration_seconds: int | None = Field(
        alias="targetDurationSeconds",
        default=None,
        ge=8,
        le=60,
    )
    quality_tier: QualityTier | None = Field(alias="qualityTier", default=None)

    @model_validator(mode="after")
    def require_change(self) -> ProductionRecipeInstancePatch:
        if not self.model_fields_set:
            raise ValueError("配方实例修改至少需要一个字段")
        return self


class DirectorWorkflowAdoptionRequest(StrictModel):
    """Explicit, zero-Provider migration of a legacy project into the director workflow."""

    expected_source_hash: str = Field(
        alias="expectedSourceHash",
        pattern=r"^[0-9a-f]{64}$",
    )
    recipe_key: ProductionRecipeKey = Field(
        alias="recipeKey",
        default=ProductionRecipeKey.HEALING_CHILD_CAT_V1,
    )
    target_duration_seconds: int = Field(
        alias="targetDurationSeconds",
        ge=8,
        le=60,
    )
    quality_tier: QualityTier = Field(
        alias="qualityTier",
        default=QualityTier.QUICK,
    )


class PaidRecipeRunRequest(StrictModel):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    accept_estimated_cost_micros: int = Field(
        alias="acceptEstimatedCostMicros",
        ge=0,
    )
    reason: str | None = Field(default=None, max_length=2_000)
    expected_input_hash: str | None = Field(
        alias="expectedInputHash",
        default=None,
        min_length=64,
        max_length=64,
    )


class CharacterDesignRecipeRunRequest(PaidRecipeRunRequest):
    character_design_stage: CharacterDesignRunStage = Field(
        alias="characterDesignStage",
        default=CharacterDesignRunStage.ALL,
    )


class CharacterDesignBatchDraft(StrictModel):
    """A character-design image batch submitted through the universal media queue."""

    project_id: uuid.UUID = Field(alias="projectId")
    canvas_node_id: uuid.UUID = Field(alias="canvasNodeId")
    media_kind: Literal["image"] = Field(alias="mediaKind", default="image")
    candidate_count: int = Field(alias="candidateCount", ge=1, le=8)
    provider: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)
    expected_input_hash: str | None = Field(
        alias="expectedInputHash",
        default=None,
        min_length=64,
        max_length=64,
    )
    input: dict[str, Any]


class RecipeDispatchError(RuntimeError):
    """A recoverable recipe scheduling failure before any provider submission.

    This exception is intentionally limited to persistence/dispatch boundaries where
    the enclosing transaction has rolled back and the caller can prove that no
    provider request was made.  Provider and transport failures must use their own
    error types so they can never be mistaken for a safe retry.
    """

    code = "recipe_dispatch_failed"
    failed_step = "create_generation_batches"
    recoverable = True
    provider_submitted = False

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context = dict(context or {})

    def to_error_document(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "failedStep": self.failed_step,
            "recoverable": self.recoverable,
            "providerSubmitted": self.provider_submitted,
            "message": str(self),
            "context": self.context,
        }


class CanvasGroupRunRequest(PaidRecipeRunRequest):
    """Execute exactly one derived recipe phase and stop at its review gate."""


class StoryboardRecipeRunRequest(PaidRecipeRunRequest):
    creation_mode: StoryboardCreationMode = Field(
        alias="creationMode",
        default=StoryboardCreationMode.FROM_STORY,
    )
    reference_asset_ids: list[uuid.UUID] = Field(
        alias="referenceAssetIds",
        default_factory=list,
        max_length=6,
    )
    source_story_revision_id: uuid.UUID | None = Field(
        alias="sourceStoryRevisionId",
        default=None,
    )
    instruction: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_creation_source(self) -> StoryboardRecipeRunRequest:
        if self.creation_mode is StoryboardCreationMode.MANUAL:
            raise ValueError("手工分镜不会调用付费生成接口，请使用人工分镜草稿接口")
        if self.source_story_revision_id is None:
            raise ValueError("生成分镜必须明确选择一个已批准剧情脚本")
        if (
            self.creation_mode is StoryboardCreationMode.FROM_CHARACTERS
            and not self.reference_asset_ids
        ):
            raise ValueError("角色生成分镜至少需要一个角色素材")
        return self


class GenerationPlanClipDraft(StrictModel):
    shot_beat_ids: list[uuid.UUID] = Field(
        alias="shotBeatIds", min_length=1, max_length=20
    )

    @model_validator(mode="after")
    def require_unique_shots(self) -> GenerationPlanClipDraft:
        if len(self.shot_beat_ids) != len(set(self.shot_beat_ids)):
            raise ValueError("同一真实生成片段不能重复包含导演分镜")
        return self


class GenerationPlanRevisionDraft(StrictModel):
    """A human-edited grouping proposal; saving it never submits provider work."""

    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    capability_revision: str = Field(alias="capabilityRevision", min_length=1, max_length=80)
    clips: list[GenerationPlanClipDraft] = Field(min_length=1, max_length=200)
    reason: str = Field(default="人工调整相邻导演分镜分组", max_length=2_000)

    @model_validator(mode="after")
    def require_unique_complete_draft(self) -> GenerationPlanRevisionDraft:
        shot_ids = [shot_id for clip in self.clips for shot_id in clip.shot_beat_ids]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("生成编排中的导演分镜不能跨片段重复")
        return self


class StoryboardProductionPlanConfirmation(StrictModel):
    """One human decision pinned to both executable storyboard snapshots."""

    idempotency_key: str = Field(
        alias="idempotencyKey",
        min_length=8,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    storyboard_revision_id: uuid.UUID = Field(alias="storyboardRevisionId")
    storyboard_revision: int = Field(alias="storyboardRevision", ge=1)
    structure_hash: str = Field(alias="structureHash", pattern=r"^[0-9a-f]{64}$")
    generation_plan_id: uuid.UUID = Field(alias="generationPlanId")
    generation_plan_revision: int = Field(alias="generationPlanRevision", ge=1)
    generation_plan_hash: str = Field(
        alias="generationPlanHash",
        pattern=r"^[0-9a-f]{64}$",
    )
    reason: str | None = Field(default=None, max_length=2_000)


class RecipeSequenceTransition(StrictModel):
    after_shot_id: uuid.UUID = Field(alias="afterShotId")
    transition: SequenceTransition

    @model_validator(mode="after")
    def validate_recipe_fade_duration(self) -> RecipeSequenceTransition:
        if (
            self.transition.type is not SequenceTransitionType.CUT
            and self.transition.duration_ms < 300
        ):
            raise ValueError("组合包淡化或叠化转场必须为300至1000毫秒")
        return self


class RecipeSequenceRunRequest(PaidRecipeRunRequest):
    transitions: list[RecipeSequenceTransition] = Field(default_factory=list, max_length=3)
    intro_transition: SequenceTransition | None = Field(default=None, alias="introTransition")
    outro_transition: SequenceTransition | None = Field(default=None, alias="outroTransition")

    @model_validator(mode="after")
    def require_unique_following_shots(self) -> RecipeSequenceRunRequest:
        shot_ids = [item.after_shot_id for item in self.transitions]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("同一镜头只能配置一个转场")
        for label, transition in (
            ("开场", self.intro_transition),
            ("结尾", self.outro_transition),
        ):
            if transition is None:
                continue
            if transition.type is SequenceTransitionType.CROSS_DISSOLVE:
                raise ValueError(f"{label}边界不支持叠化")
            if transition.type is not SequenceTransitionType.CUT and transition.duration_ms < 300:
                raise ValueError(f"组合包{label}淡黑必须为300至1000毫秒")
        return self


class HumanReviewDraft(StrictModel):
    """A human decision pinned to an immutable target snapshot."""

    target_type: str = Field(alias="targetType", min_length=1, max_length=80)
    target_id: uuid.UUID = Field(alias="targetId")
    target_revision: int | None = Field(alias="targetRevision", default=None, ge=1)
    target_hash: str | None = Field(
        alias="targetHash",
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    decision: HumanReviewDecision
    blocking_diagnostic_present: bool = Field(
        alias="blockingDiagnosticPresent",
        default=False,
    )
    issues: list[str] = Field(default_factory=list, max_length=30)
    reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_resolution(self) -> HumanReviewDraft:
        reason = (self.reason or "").strip()
        if self.target_revision is None and self.target_hash is None:
            raise ValueError("人工审核必须固定目标版本或内容哈希")
        if self.decision is HumanReviewDecision.APPROVE and self.blocking_diagnostic_present:
            raise ValueError("存在阻断诊断时不能普通批准，请修改或人工覆盖")
        if self.decision is HumanReviewDecision.OVERRIDE and not reason:
            raise ValueError("人工覆盖必须填写覆盖理由")
        if (
            self.decision is HumanReviewDecision.REQUEST_CHANGES
            and not reason
            and not self.issues
        ):
            raise ValueError("请求修改必须填写修改原因或问题清单")
        return self


HEALING_CHILD_CAT_RECIPE = ProductionRecipeDefinition(
    key=ProductionRecipeKey.HEALING_CHILD_CAT_V1,
    title="一人一猫治愈短片",
    description="固定儿童、固定猫咪与统一锁定画风的日常治愈短片配方。",
    defaultDurationSeconds=15,
    minimumDurationSeconds=8,
    maximumDurationSeconds=60,
    minimumShotSeconds=8,
    maximumShotSeconds=15,
    minimumEditorialShotSeconds=2,
    defaultEditorialShotCount=3,
    maximumEditorialShotsPerClip=4,
    aspectRatio="9:16",
    resolution="720p",
    storyCandidateCount=3,
    qualityTiers={
        QualityTier.QUICK: QualityTierPolicy(
            anchorCandidateCount=1,
            videoCandidateCount=1,
            characterDesignCandidateCount=1,
        ),
        QualityTier.BALANCED: QualityTierPolicy(
            anchorCandidateCount=2,
            videoCandidateCount=1,
            characterDesignCandidateCount=2,
        ),
        QualityTier.PREMIUM: QualityTierPolicy(
            anchorCandidateCount=4,
            videoCandidateCount=2,
            characterDesignCandidateCount=4,
        ),
    },
)


def split_shot_durations(total_seconds: int) -> tuple[int, ...]:
    """Balance an episode into the healing recipe's preferred 8-15 second windows."""

    if not 8 <= total_seconds <= 60:
        raise ValueError("治愈短片总时长必须为8至60秒")
    shot_count = math.ceil(total_seconds / 15)
    base, remainder = divmod(total_seconds, shot_count)
    durations = tuple(base + (1 if index < remainder else 0) for index in range(shot_count))
    if any(not 8 <= item <= 15 for item in durations):
        raise ValueError("无法把总时长拆成配方要求的8至15秒连续时间窗")
    return durations


def split_editorial_shot_durations(total_seconds: int) -> tuple[int, ...]:
    """Create default director beats independently of provider call boundaries."""

    result: list[int] = []
    for clip_duration in split_shot_durations(total_seconds):
        beat_count = HEALING_CHILD_CAT_RECIPE.default_editorial_shot_count
        base, remainder = divmod(clip_duration, beat_count)
        durations = tuple(
            base + (1 if index < remainder else 0) for index in range(beat_count)
        )
        if any(
            item < HEALING_CHILD_CAT_RECIPE.minimum_editorial_shot_seconds
            for item in durations
        ):
            raise ValueError("无法在不少于2秒的约束下拆分导演分镜")
        result.extend(durations)
    return tuple(result)


def plan_generation_clips(
    shots: Sequence[EditorialShotDescriptor],
    *,
    capability: VideoModelCapability = SEEDANCE_2_0_CAPABILITY,
) -> tuple[GenerationClipProposal, ...]:
    """Find the fewest legal clips that completely cover ordered director beats."""

    ordered = tuple(shots)
    if not ordered:
        raise ValueError("生成编排至少需要一个导演分镜")
    if any(item.duration_seconds < 1 for item in ordered):
        raise ValueError("导演分镜时长必须大于0秒")

    best: list[tuple[GenerationClipProposal, ...] | None] = [None] * (len(ordered) + 1)
    best[len(ordered)] = ()
    for start in range(len(ordered) - 1, -1, -1):
        duration = 0
        for end in range(start, min(len(ordered), start + capability.maximum_editorial_shots)):
            current = ordered[end]
            if end > start:
                previous = ordered[end - 1]
                if current.cut_intent is EditorialCutIntent.HARD_CUT:
                    break
                if current.scene_id != previous.scene_id:
                    break
                if current.wardrobe_state != previous.wardrobe_state:
                    break
                if current.prop_state != previous.prop_state:
                    break
            duration += current.duration_seconds
            if duration > capability.maximum_duration_seconds:
                break
            if duration < capability.minimum_duration_seconds:
                continue
            if not capability.supports_multi_shot and end != start:
                break
            suffix = best[end + 1]
            if suffix is None:
                continue
            candidate = (
                GenerationClipProposal(
                    editorial_shots=ordered[start : end + 1],
                    duration_seconds=duration,
                ),
                *suffix,
            )
            if best[start] is None or len(candidate) < len(best[start] or ()):
                best[start] = candidate
    if best[0] is None:
        raise ValueError(
            "当前分镜无法按模型能力完整打包；请调整时长、切镜或切换兼容模型"
        )
    return best[0] or ()


def build_temporal_beats(
    shot_duration_seconds: int,
    *,
    actions: tuple[tuple[str, str, str], ...],
) -> tuple[TemporalBeat, ...]:
    """Build the three complete action windows used by a continuous shot."""

    if not (
        SEEDANCE_2_0_CAPABILITY.minimum_duration_seconds
        <= shot_duration_seconds
        <= SEEDANCE_2_0_CAPABILITY.maximum_duration_seconds
    ):
        raise ValueError("真实生成片段时长必须符合当前视频模型的4至15秒能力")
    if len(actions) != 3:
        raise ValueError("连续镜头必须包含三个动作节拍")
    base, remainder = divmod(shot_duration_seconds, 3)
    durations = tuple(base + (1 if index < remainder else 0) for index in range(3))
    phases = (
        TemporalBeatPhase.BEGINNING,
        TemporalBeatPhase.CHANGE,
        TemporalBeatPhase.WARM_ENDING,
    )
    cursor = 0
    beats: list[TemporalBeat] = []
    for phase, duration, action in zip(phases, durations, actions, strict=True):
        child_action, cat_action, camera = action
        beats.append(
            TemporalBeat(
                phase=phase,
                startSecond=cursor,
                endSecond=cursor + duration,
                childAction=child_action,
                catAction=cat_action,
                camera=camera,
            )
        )
        cursor += duration
    return tuple(beats)


def canon_v2_reference_keys(
    environment: Literal["indoor", "outdoor"] | str,
) -> tuple[str, ...]:
    """Return the fixed identity order and exactly one watercolor style reference."""

    if environment not in {"indoor", "outdoor"}:
        raise ValueError("Canon-v2环境必须为indoor或outdoor")
    return (
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        f"style:{environment}",
    )


def canon_reference_keys(
    canon_profile_id: str,
    environment: Literal["indoor", "outdoor"] | str,
) -> tuple[str, ...]:
    """Resolve the immutable reference set for old and new recipe instances."""

    if canon_profile_id == CANON_V4_PROFILE_ID:
        return (
            "person:headshot",
            "person:fullbody",
            "cat:front",
            "cat:side",
            CANON_V4_STYLE_BOARD_KEY,
        )
    if canon_profile_id == CANON_V3_PROFILE_ID:
        return (
            "person:headshot",
            "person:fullbody",
            "cat:front",
            "cat:side",
            "style:line_texture",
        )
    if canon_profile_id == CANON_V2_PROFILE_ID:
        return canon_v2_reference_keys(environment)
    raise ValueError(f"不支持的 Canon 配置：{canon_profile_id}")
