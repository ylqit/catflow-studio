"""V5任意场景、视频片段和人工参考素材契约。

创作事实只保存项目标题、场景原文和完整镜头描述。供应商输入、数据库状态和
审核结果属于各自边界，不重复塞进剧情JSON。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel
from .production_recipes import ReferenceAuthorityDto
from .visual_profiles import DEFAULT_SERIES_VISUAL_PROFILE, DEFAULT_STYLE_PROFILE

CURRENT_CONTRACT_VERSION = 5


class StoryMode(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


class AnchorMode(StrEnum):
    TEXT_ONLY = "text_only"
    EXISTING = "existing"
    GENERATE = "generate"


class SceneLookUsage(StrEnum):
    OFF = "off"
    APPEARANCE_ONLY = "appearance_only"
    FULL_REFERENCE = "full_reference"
    DERIVE_ANCHOR = "derive_anchor"


class ReferenceUsage(StrEnum):
    APPROVED_ANCHOR = "approved_anchor"
    GENERATION_REFERENCE = "generation_reference"


class ReferenceRole(StrEnum):
    IDENTITY = "identity"
    STYLE = "style"
    SCENE = "scene"
    PROP = "prop"
    COMPOSITION = "composition"


class ReferenceTarget(StrEnum):
    ANCHOR = "anchor"
    VIDEO = "video"
    BOTH = "both"


class EnvironmentStyle(StrEnum):
    OUTDOOR = "outdoor"
    INDOOR = "indoor"


class StoryIssueCategory(StrEnum):
    CONTINUITY = "continuity"
    CANON_CONFLICT = "canon_conflict"
    PHYSICAL_FEASIBILITY = "physical_feasibility"
    ACTION_DENSITY = "action_density"
    CAUSALITY = "causality"
    HUMAN_CAT_INTERACTION = "human_cat_interaction"
    GENERATION_CLARITY = "generation_clarity"
    OTHER = "other"


class StoryRewriteStrategy(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    CREATIVE = "creative"


class LookReferencePurpose(StrEnum):
    PERSON_IDENTITY = "person_identity"
    PERSON_BODY = "person_body"
    CAT_IDENTITY = "cat_identity"
    STYLE = "style"
    WARDROBE = "wardrobe"
    ENVIRONMENT = "environment"
    PROP = "prop"
    COMPOSITION = "composition"


class VisualAssetPurpose(StrEnum):
    WARDROBE = "wardrobe"
    ENVIRONMENT = "environment"
    PROP = "prop"
    COMPOSITION = "composition"


class VisualAssetScope(StrEnum):
    PROJECT = "project"
    SCENE = "scene"


class VisualAssetAction(StrEnum):
    GENERATE = "generate"
    UPLOAD = "upload"
    EXISTING = "existing"
    SKIP = "skip"


class ReferenceBinding(StrictModel):
    asset_id: Annotated[UUID, Field(alias="assetId")]
    usage: ReferenceUsage
    role: ReferenceRole
    apply_to: Annotated[ReferenceTarget, Field(alias="applyTo")]
    authority: ReferenceAuthorityDto | None = None


class LookReferenceBinding(StrictModel):
    asset_id: Annotated[UUID, Field(alias="assetId")]
    purpose: LookReferencePurpose
    instruction: Annotated[str, Field(max_length=1_000)] = ""
    authority: ReferenceAuthorityDto | None = None


class VisualProfileDraft(StrictModel):
    person_identity: Annotated[
        str,
        Field(alias="personIdentity", min_length=8, max_length=600),
    ] = DEFAULT_SERIES_VISUAL_PROFILE.person_identity
    person_hair: Annotated[
        str,
        Field(alias="personHair", min_length=4, max_length=300),
    ] = DEFAULT_SERIES_VISUAL_PROFILE.person_hair
    person_body: Annotated[
        str,
        Field(alias="personBody", min_length=4, max_length=300),
    ] = DEFAULT_SERIES_VISUAL_PROFILE.person_body
    cat_identity: Annotated[
        str,
        Field(alias="catIdentity", min_length=8, max_length=600),
    ] = DEFAULT_SERIES_VISUAL_PROFILE.cat_identity
    style_positive: Annotated[
        tuple[str, ...],
        Field(alias="stylePositive", min_length=3, max_length=10),
    ] = DEFAULT_STYLE_PROFILE.positive_features
    style_negative: Annotated[
        tuple[str, ...],
        Field(alias="styleNegative", min_length=2, max_length=10),
    ] = DEFAULT_STYLE_PROFILE.excluded_features
    reference_bindings: list[LookReferenceBinding] = Field(
        default_factory=list,
        alias="referenceBindings",
        max_length=14,
    )

    @model_validator(mode="after")
    def validate_profile_references(self) -> VisualProfileDraft:
        allowed = {
            LookReferencePurpose.PERSON_IDENTITY,
            LookReferencePurpose.PERSON_BODY,
            LookReferencePurpose.CAT_IDENTITY,
            LookReferencePurpose.STYLE,
        }
        if any(item.purpose not in allowed for item in self.reference_bindings):
            raise ValueError("项目视觉档案只允许人物、猫咪和画风参考")
        _validate_unique_look_assets(self.reference_bindings)
        return self


class StoryProjectInput(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=160)]
    first_scene_title: Annotated[
        str,
        Field(alias="firstSceneTitle", min_length=1, max_length=120),
    ] = "场景1"
    first_scene_text: Annotated[
        str,
        Field(alias="firstSceneText", min_length=1, max_length=12_000),
    ]


class SceneLookPlan(StrictModel):
    person_wardrobe: Annotated[
        str,
        Field(alias="personWardrobe", max_length=1_000),
    ] = ""
    person_accessories: Annotated[
        str,
        Field(alias="personAccessories", max_length=1_000),
    ] = ""
    cat_appearance: Annotated[
        str,
        Field(alias="catAppearance", max_length=1_000),
    ] = ""
    key_props: Annotated[
        str,
        Field(alias="keyProps", max_length=1_000),
    ] = ""
    environment_style: EnvironmentStyle = Field(
        default=EnvironmentStyle.OUTDOOR,
        alias="environmentStyle",
    )
    person_pose: Annotated[str, Field(alias="personPose", max_length=1_000)] = ""
    cat_pose: Annotated[str, Field(alias="catPose", max_length=1_000)] = ""
    composition: Annotated[str, Field(max_length=1_500)] = ""
    additional_instructions: Annotated[
        str,
        Field(alias="additionalInstructions", max_length=2_000),
    ] = ""
    image_recommended: bool = Field(default=False, alias="imageRecommended")
    recommendation_reason: Annotated[
        str | None,
        Field(alias="recommendationReason", max_length=2_000),
    ] = None


class SceneLookDraft(StrictModel):
    visual_profile_revision_id: Annotated[UUID, Field(alias="visualProfileRevisionId")]
    look_plan: SceneLookPlan = Field(alias="lookPlan")
    reference_bindings: list[LookReferenceBinding] = Field(
        default_factory=list,
        alias="referenceBindings",
        max_length=14,
    )

    @model_validator(mode="after")
    def validate_reference_assets(self) -> SceneLookDraft:
        _validate_unique_look_assets(self.reference_bindings)
        return self


class SceneDraft(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=120)]
    source_text: Annotated[str, Field(alias="sourceText", min_length=1, max_length=12_000)]
    chapter_label: Annotated[
        str | None,
        Field(alias="chapterLabel", max_length=80),
    ] = None
    context_note: Annotated[
        str | None,
        Field(alias="contextNote", max_length=2_000),
    ] = None
    story_mode: StoryMode = Field(default=StoryMode.SINGLE, alias="storyMode")
    target_shot_count: Annotated[
        int,
        Field(default=1, alias="targetShotCount", ge=1, le=6),
    ]
    look_plan: SceneLookPlan | None = Field(default=None, alias="lookPlan")

    @model_validator(mode="after")
    def validate_story_mode(self) -> SceneDraft:
        if self.story_mode is StoryMode.SINGLE and self.target_shot_count != 1:
            raise ValueError("single模式必须只生成一个视频片段")
        if self.story_mode is StoryMode.MULTI and self.target_shot_count < 2:
            raise ValueError("multi模式必须生成2到6个视频片段")
        return self


class StoryIssue(StrictModel):
    category: StoryIssueCategory
    evidence: Annotated[str, Field(min_length=1, max_length=2_000)]
    impact: Annotated[str, Field(min_length=1, max_length=2_000)]
    suggestion: Annotated[str, Field(min_length=1, max_length=2_000)]


class StoryRewriteOption(StrictModel):
    strategy: StoryRewriteStrategy
    title: Annotated[str, Field(min_length=1, max_length=120)]
    summary: Annotated[str, Field(min_length=1, max_length=3_000)]
    tradeoffs: Annotated[str, Field(min_length=1, max_length=2_000)]


class StoryDiagnosisOutput(StrictModel):
    overall_assessment: Annotated[
        str,
        Field(alias="overallAssessment", min_length=1, max_length=4_000),
    ]
    issues: list[StoryIssue] = Field(default_factory=list, max_length=20)
    rewrite_options: list[StoryRewriteOption] = Field(
        alias="rewriteOptions",
        min_length=3,
        max_length=3,
    )

    @model_validator(mode="after")
    def require_three_rewrite_strategies(self) -> StoryDiagnosisOutput:
        strategies = [item.strategy for item in self.rewrite_options]
        expected = {
            StoryRewriteStrategy.CONSERVATIVE,
            StoryRewriteStrategy.BALANCED,
            StoryRewriteStrategy.CREATIVE,
        }
        if len(set(strategies)) != 3 or set(strategies) != expected:
            raise ValueError("story diagnosis must contain three rewrite strategies")
        return self


class StoryRewriteOutput(StrictModel):
    rewritten_story: Annotated[
        str,
        Field(alias="rewrittenStory", min_length=1, max_length=12_000),
    ]
    change_summary: list[Annotated[str, Field(min_length=1, max_length=1_000)]] = Field(
        default_factory=list,
        alias="changeSummary",
        max_length=20,
    )
    unresolved_questions: list[
        Annotated[str, Field(min_length=1, max_length=1_000)]
    ] = Field(
        default_factory=list,
        alias="unresolvedQuestions",
        max_length=20,
    )


class StoryExpansionOutput(StrictModel):
    expanded_story: Annotated[
        str,
        Field(alias="expandedStory", min_length=1, max_length=12_000),
    ]
    creative_summary: Annotated[
        str,
        Field(alias="creativeSummary", min_length=1, max_length=2_000),
    ]
    unresolved_questions: list[
        Annotated[str, Field(min_length=1, max_length=1_000)]
    ] = Field(
        default_factory=list,
        alias="unresolvedQuestions",
        max_length=20,
    )


class VisualAssetSuggestion(StrictModel):
    suggestion_key: Annotated[
        str,
        Field(alias="suggestionKey", min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$"),
    ]
    display_name: Annotated[str, Field(alias="displayName", min_length=1, max_length=120)]
    purpose: VisualAssetPurpose
    target_scope: VisualAssetScope = Field(alias="targetScope")
    rationale: Annotated[str, Field(min_length=1, max_length=2_000)]
    prompt: Annotated[str, Field(min_length=1, max_length=6_000)]
    reference_asset_ids: list[UUID] = Field(
        default_factory=list,
        alias="referenceAssetIds",
        max_length=14,
    )


class VisualAssetPlanOutput(StrictModel):
    overall_assessment: Annotated[
        str,
        Field(alias="overallAssessment", min_length=1, max_length=4_000),
    ]
    suggestions: list[VisualAssetSuggestion] = Field(default_factory=list, max_length=12)
    text_only_items: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list,
        alias="textOnlyItems",
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_suggestion_keys(self) -> VisualAssetPlanOutput:
        keys = [item.suggestion_key for item in self.suggestions]
        if len(keys) != len(set(keys)):
            raise ValueError("visual asset suggestion keys must be unique")
        return self


class VisualAssetPlanSelection(StrictModel):
    suggestion_key: Annotated[
        str,
        Field(alias="suggestionKey", min_length=1, max_length=100),
    ]
    action: VisualAssetAction
    display_name: Annotated[str, Field(alias="displayName", min_length=1, max_length=120)]
    purpose: VisualAssetPurpose
    target_scope: VisualAssetScope = Field(alias="targetScope")
    prompt: Annotated[str, Field(min_length=1, max_length=6_000)]
    reference_asset_ids: list[UUID] = Field(
        default_factory=list,
        alias="referenceAssetIds",
        max_length=14,
    )
    existing_asset_id: UUID | None = Field(default=None, alias="existingAssetId")

    @model_validator(mode="after")
    def validate_action_source(self) -> VisualAssetPlanSelection:
        if self.action is VisualAssetAction.EXISTING and self.existing_asset_id is None:
            raise ValueError("existing action requires existingAssetId")
        if self.action is not VisualAssetAction.EXISTING and self.existing_asset_id is not None:
            raise ValueError("existingAssetId is only valid for existing action")
        return self


class AcceptedVisualAssetPlan(StrictModel):
    selections: list[VisualAssetPlanSelection] = Field(max_length=12)

    @model_validator(mode="after")
    def validate_selection_keys(self) -> AcceptedVisualAssetPlan:
        keys = [item.suggestion_key for item in self.selections]
        if len(keys) != len(set(keys)):
            raise ValueError("accepted visual asset selection keys must be unique")
        return self


class SceneAssetSlotReadiness(StrictModel):
    key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(alias="displayName", min_length=1, max_length=160)
    purpose: VisualAssetPurpose
    required: bool = True
    asset_ids: list[UUID] = Field(alias="assetIds", default_factory=list, max_length=30)
    status: Literal["ready", "missing", "stale"]


class SceneAssetReadiness(StrictModel):
    required_slots: list[SceneAssetSlotReadiness] = Field(
        alias="requiredSlots",
        default_factory=list,
        max_length=30,
    )
    bound_asset_ids: list[UUID] = Field(
        alias="boundAssetIds",
        default_factory=list,
        max_length=100,
    )
    missing_asset_keys: list[str] = Field(
        alias="missingAssetKeys",
        default_factory=list,
        max_length=30,
    )
    stale_asset_keys: list[str] = Field(
        alias="staleAssetKeys",
        default_factory=list,
        max_length=30,
    )
    scene_look_status: Literal["approved", "missing", "stale", "off"] = Field(
        alias="sceneLookStatus"
    )
    visual_asset_plan_current: bool = Field(alias="visualAssetPlanCurrent")
    can_generate_scene_look: bool = Field(alias="canGenerateSceneLook")
    can_compile_shot_prompt: bool = Field(alias="canCompileShotPrompt")
    blockers: list[str] = Field(default_factory=list, max_length=30)


class ReferenceImageDraft(StrictModel):
    display_name: Annotated[str, Field(alias="displayName", min_length=1, max_length=120)]
    purpose: VisualAssetPurpose
    prompt: Annotated[str, Field(min_length=1, max_length=6_000)]
    reference_asset_ids: list[UUID] = Field(
        default_factory=list,
        alias="referenceAssetIds",
        max_length=14,
    )
    source_revision: Annotated[str, Field(alias="sourceRevision", min_length=1, max_length=160)]


class ShotSuggestion(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=100)]
    direction: Annotated[str, Field(min_length=1, max_length=6_000)]
    suggested_duration_seconds: Annotated[
        int,
        Field(alias="suggestedDurationSeconds", ge=4, le=15),
    ] = 4
    anchor_mode: AnchorMode = Field(default=AnchorMode.TEXT_ONLY, alias="anchorMode")
    scene_look_usage: SceneLookUsage = Field(
        default=SceneLookUsage.OFF,
        alias="sceneLookUsage",
    )

    @model_validator(mode="after")
    def validate_visual_strategy(self) -> ShotSuggestion:
        if (
            self.scene_look_usage is SceneLookUsage.DERIVE_ANCHOR
            and self.anchor_mode is not AnchorMode.GENERATE
        ):
            raise ValueError("derive_anchor requires anchorMode=generate")
        return self


class ShotSuggestionOutput(StrictModel):
    scene_title: Annotated[
        str,
        Field(alias="sceneTitle", min_length=1, max_length=120),
    ]
    look_plan: SceneLookPlan = Field(default_factory=SceneLookPlan, alias="lookPlan")
    shots: list[ShotSuggestion] = Field(min_length=1, max_length=6)


class ShotCardDraft(StrictModel):
    title: Annotated[str, Field(min_length=1, max_length=100)]
    direction: Annotated[str, Field(min_length=1, max_length=6_000)]
    duration_seconds: Annotated[int, Field(alias="durationSeconds", ge=4, le=15)] = 4
    anchor_mode: AnchorMode = Field(default=AnchorMode.TEXT_ONLY, alias="anchorMode")
    reference_bindings: list[ReferenceBinding] = Field(
        default_factory=list,
        alias="referenceBindings",
    )
    inherit_project_references: bool = Field(
        default=True,
        alias="inheritProjectReferences",
    )
    scene_look_usage: SceneLookUsage = Field(
        default=SceneLookUsage.OFF,
        alias="sceneLookUsage",
    )

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_scene_look_flag(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        legacy = data.pop("useSceneLook", data.pop("use_scene_look", None))
        if "sceneLookUsage" not in data and "scene_look_usage" not in data and legacy is not None:
            data["sceneLookUsage"] = (
                SceneLookUsage.APPEARANCE_ONLY if legacy else SceneLookUsage.OFF
            )
        return data

    @property
    def use_scene_look(self) -> bool:
        """Compatibility view; scene_look_usage is the authoritative value."""

        return self.scene_look_usage is not SceneLookUsage.OFF

    @model_validator(mode="after")
    def validate_references(self) -> ShotCardDraft:
        asset_ids = [item.asset_id for item in self.reference_bindings]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("同一素材不能在一张镜头卡中重复绑定")
        approved_anchors = [
            item for item in self.reference_bindings if item.usage is ReferenceUsage.APPROVED_ANCHOR
        ]
        if self.anchor_mode is AnchorMode.EXISTING and len(approved_anchors) != 1:
            raise ValueError("existing模式必须且只能选择一张approved_anchor")
        if self.anchor_mode is not AnchorMode.EXISTING and approved_anchors:
            raise ValueError("只有existing模式允许绑定approved_anchor")
        if (
            self.scene_look_usage is SceneLookUsage.DERIVE_ANCHOR
            and self.anchor_mode is not AnchorMode.GENERATE
        ):
            raise ValueError("derive_anchor 定妆策略必须使用 generate 锚点模式")
        return self


class ShotPacingBeat(StrictModel):
    ordinal: Annotated[int, Field(ge=1, le=4)]
    description: Annotated[str, Field(min_length=1, max_length=1_000)]
    rhythm: Literal["brief", "standard", "expanded"]


class ShotPacingPlan(StrictModel):
    recommended_duration_seconds: Annotated[
        int,
        Field(alias="recommendedDurationSeconds", ge=4, le=15),
    ]
    rationale: Annotated[str, Field(min_length=1, max_length=2_000)]
    beats: list[ShotPacingBeat] = Field(min_length=2, max_length=4)


class ShotReferenceDecision(StrictModel):
    asset_id: Annotated[UUID, Field(alias="assetId")]
    decision: Literal["keep", "remove", "change_role"]
    recommended_role: ReferenceRole | None = Field(
        default=None,
        alias="recommendedRole",
    )
    reason: Annotated[str, Field(min_length=1, max_length=1_000)]


class ShotContinuityAdvice(StrictModel):
    previous_issues: list[Annotated[str, Field(max_length=1_000)]] = Field(
        default_factory=list,
        alias="previousIssues",
        max_length=12,
    )
    next_issues: list[Annotated[str, Field(max_length=1_000)]] = Field(
        default_factory=list,
        alias="nextIssues",
        max_length=12,
    )
    recommendation: Annotated[str, Field(min_length=1, max_length=2_000)]


class ShotAssistPatch(StrictModel):
    title: Annotated[str | None, Field(min_length=1, max_length=100)] = None
    direction: Annotated[str | None, Field(min_length=1, max_length=6_000)] = None
    duration_seconds: Annotated[
        int | None,
        Field(default=None, alias="durationSeconds", ge=4, le=15),
    ]
    scene_look_usage: SceneLookUsage | None = Field(
        default=None,
        alias="sceneLookUsage",
    )
    anchor_mode: AnchorMode | None = Field(default=None, alias="anchorMode")
    reference_bindings: list[ReferenceBinding] | None = Field(
        default=None,
        alias="referenceBindings",
    )

    @model_validator(mode="after")
    def require_selected_field(self) -> ShotAssistPatch:
        if all(
            value is None
            for value in (
                self.title,
                self.direction,
                self.duration_seconds,
                self.scene_look_usage,
                self.anchor_mode,
                self.reference_bindings,
            )
        ):
            raise ValueError("at least one shot-assistance field must be selected")
        return self


class ShotCreativeAlternative(StrictModel):
    label: Literal["conservative", "stable"]
    body: Annotated[str, Field(min_length=1, max_length=6_000)]
    rationale: Annotated[str, Field(min_length=1, max_length=2_000)]


class ShotAssistAnalysis(StrictModel):
    action_density_assessment: Annotated[
        str,
        Field(alias="actionDensityAssessment", min_length=1, max_length=2_000),
    ]
    asset_compatibility_assessment: Annotated[
        str,
        Field(alias="assetCompatibilityAssessment", max_length=3_000),
    ] = ""
    pacing_plan: ShotPacingPlan = Field(alias="pacingPlan")
    recommended_scene_look_usage: SceneLookUsage = Field(
        alias="recommendedSceneLookUsage"
    )
    recommended_anchor_mode: AnchorMode = Field(alias="recommendedAnchorMode")
    reference_decisions: list[ShotReferenceDecision] = Field(
        default_factory=list,
        alias="referenceDecisions",
        max_length=9,
    )
    continuity: ShotContinuityAdvice
    prompt_risks: list[Annotated[str, Field(max_length=1_000)]] = Field(
        default_factory=list,
        alias="promptRisks",
        max_length=12,
    )
    creative_body: Annotated[
        str | None,
        Field(default=None, alias="creativeBody", min_length=1, max_length=6_000),
    ]
    creative_alternatives: list[ShotCreativeAlternative] = Field(
        default_factory=list,
        alias="creativeAlternatives",
        max_length=2,
    )
    anchor_brief: Annotated[
        str | None,
        Field(default=None, alias="anchorBrief", min_length=1, max_length=4_000),
    ]
    patch: ShotAssistPatch | None = None


class ShotPromptContext(StrictModel):
    """编译Prompt所需的最小只读投影。"""

    project_title: str
    scene_title: str
    scene_text: str
    context_note: str | None = None
    shot_title: str
    direction: str
    duration_seconds: int


def _validate_unique_look_assets(bindings: list[LookReferenceBinding]) -> None:
    asset_ids = [item.asset_id for item in bindings]
    if len(asset_ids) != len(set(asset_ids)):
        raise ValueError("同一素材不能在定妆参考中重复绑定")
