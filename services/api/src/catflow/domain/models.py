from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from .contract import ContractModel
from .narrative import NarrativeDesign, ShotInformation


class MicroEvent(ContractModel):
    trigger: str = Field(min_length=1, max_length=500)
    child_action: str = Field(alias="childAction", min_length=1, max_length=500)
    cat_response: str = Field(alias="catResponse", min_length=1, max_length=500)
    visible_change: str = Field(alias="visibleChange", min_length=1, max_length=500)
    warm_ending: str = Field(alias="warmEnding", min_length=1, max_length=500)


class LifeStoryProposalDraft(ContractModel):
    narrative_design: NarrativeDesign | None = Field(alias="narrativeDesign", default=None)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=4_000)
    trigger: str = Field(min_length=1, max_length=500)
    child_action: str = Field(alias="childAction", min_length=1, max_length=500)
    cat_response: str = Field(alias="catResponse", min_length=1, max_length=500)
    visible_change: str = Field(alias="visibleChange", min_length=1, max_length=500)
    warm_ending: str = Field(alias="warmEnding", min_length=1, max_length=500)
    target_duration_seconds: int = Field(alias="targetDurationSeconds", ge=8, le=60)
    dialogue_policy: Literal["none", "minimal"] = Field(alias="dialoguePolicy")
    environment_intent: str = Field(alias="environmentIntent", min_length=1, max_length=500)
    prop_intent: str | None = Field(alias="propIntent", default=None, max_length=300)

    @model_serializer(mode="wrap")
    def historical_serialization(self, handler):
        result = handler(self)
        if self.narrative_design is None:
            result.pop("narrativeDesign", None)
            result.pop("narrative_design", None)
        return result

    @property
    def micro_event(self) -> MicroEvent:
        return MicroEvent(
            trigger=self.trigger,
            childAction=self.child_action,
            catResponse=self.cat_response,
            visibleChange=self.visible_change,
            warmEnding=self.warm_ending,
        )


class LifeClipSpec(ContractModel):
    format_version: Literal[1, 2] = Field(alias="formatVersion", default=1)
    duration_seconds: int = Field(alias="durationSeconds", ge=8, le=60)
    aspect_ratio: Literal["9:16"] = Field(alias="aspectRatio")
    micro_event: str = Field(alias="microEvent", min_length=1, max_length=500)
    child_action: str = Field(alias="childAction", min_length=1, max_length=500)
    cat_action_or_observation: str = Field(
        alias="catActionOrObservation", min_length=1, max_length=500
    )
    visible_cause_and_effect: str = Field(
        alias="visibleCauseAndEffect", min_length=1, max_length=500
    )
    warm_ending: str = Field(alias="warmEnding", min_length=1, max_length=500)
    dialogue_policy: Literal["none", "minimal"] = Field(alias="dialoguePolicy")
    environment_intent: str = Field(alias="environmentIntent", min_length=1, max_length=500)
    prop_intent: str | None = Field(alias="propIntent", default=None, max_length=300)


    @model_validator(mode="after")
    def legacy_duration(self):
        if self.format_version == 1 and self.duration_seconds > 15:
            raise ValueError("历史短片格式最多15秒，请创建新版分镜")
        return self

    @model_serializer(mode="wrap")
    def historical_serialization(self, handler):
        result = handler(self)
        if self.format_version == 1:
            result.pop("formatVersion", None)
            result.pop("format_version", None)
        return result


class LensDesign(ContractModel):
    focal_length_equivalent: str = Field(alias="focalLengthEquivalent", min_length=1, max_length=80)
    camera_height: str = Field(alias="cameraHeight", min_length=1, max_length=120)
    camera_angle: str = Field(alias="cameraAngle", min_length=1, max_length=120)
    perspective_intent: str = Field(alias="perspectiveIntent", min_length=1, max_length=300)


class CompositionDesign(ContractModel):
    subject_placement: str = Field(alias="subjectPlacement", min_length=1, max_length=300)
    foreground: str = Field(min_length=1, max_length=200)
    middle_ground: str = Field(alias="middleGround", min_length=1, max_length=200)
    background: str = Field(min_length=1, max_length=200)
    screen_direction: str = Field(alias="screenDirection", min_length=1, max_length=160)
    eye_line: str = Field(alias="eyeLine", min_length=1, max_length=160)


class BlockingDesign(ContractModel):
    initial_state: str = Field(alias="initialState", min_length=1, max_length=300)
    movement_path: str = Field(alias="movementPath", min_length=1, max_length=500)
    end_state: str = Field(alias="endState", min_length=1, max_length=300)
    # Three micro-motions are the recommended execution density, not a data boundary.
    # Provider results keep every returned item and surface dense lists as production advice.
    micro_motions: list[str] = Field(alias="microMotions", default_factory=list)


class PhysicalChangeDesign(ContractModel):
    subject: str = Field(min_length=1, max_length=160)
    before: str = Field(min_length=1, max_length=300)
    after: str = Field(min_length=1, max_length=300)


class ContinuityDesign(ContractModel):
    incoming: str = Field(min_length=1, max_length=300)
    outgoing: str = Field(min_length=1, max_length=300)
    shared_visual_element: str = Field(alias="sharedVisualElement", min_length=1, max_length=300)
    final_frame: str = Field(alias="finalFrame", min_length=1, max_length=400)


class LightingDesign(ContractModel):
    direction: str = Field(min_length=1, max_length=160)
    softness: str = Field(min_length=1, max_length=120)
    color_intent: str = Field(alias="colorIntent", min_length=1, max_length=200)


class ShotSoundDesign(ContractModel):
    ambience: list[str] = Field(default_factory=list)
    object_effects: list[str] = Field(alias="objectEffects", default_factory=list)
    movement_effects: list[str] = Field(alias="movementEffects", default_factory=list)
    music_intent: str = Field(alias="musicIntent", min_length=1, max_length=240)
    dialogue: str | None = Field(default=None, max_length=240)


class GenerationRisk(ContractModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=400)


class ConfirmedShotFrame(ContractModel):
    asset_id: uuid.UUID = Field(alias="assetId")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    design_hash: str = Field(alias="designHash", pattern=r"^[a-f0-9]{64}$")
    checks: list[
        Literal["identity_scale", "placement_state", "movement_space", "action_start", "continuity"]
    ] = Field(min_length=5, max_length=5)


class CatPerformance(ContractModel):
    visibility: Literal["visible", "partial", "hidden"]
    gaze_from: str = Field(alias="gazeFrom", max_length=160)
    gaze_to: str = Field(alias="gazeTo", max_length=160)
    eyelid_action: Literal["none", "blink", "slow_blink", "squint_release"] = Field(
        alias="eyelidAction"
    )


class ActionBeat(ContractModel):
    start_frame: int = Field(alias="startFrame", ge=0, lt=360)
    end_frame: int = Field(alias="endFrame", gt=0, le=360)
    purpose: Literal["trigger", "action", "reaction", "payoff"]
    child_action: str = Field(alias="childAction", max_length=300)
    cat_action: str = Field(alias="catAction", max_length=300)
    visible_change: str = Field(alias="visibleChange", min_length=1, max_length=300)
    environment_action: str = Field(alias="environmentAction", default="", max_length=300)
    cat_performance: CatPerformance | None = Field(alias="catPerformance", default=None)

    @model_validator(mode="after")
    def validate_beat(self) -> ActionBeat:
        if self.end_frame <= self.start_frame:
            raise ValueError("节拍结束帧必须晚于开始帧")
        if not any((self.child_action.strip(), self.cat_action.strip(), self.environment_action.strip())):
            raise ValueError("节拍至少需要一个角色的可见动作或反应")
        if not self.visible_change.strip():
            raise ValueError("节拍需要说明可见的新变化")
        return self


    @model_serializer(mode="wrap")
    def historical_serialization(self, handler):
        result = handler(self)
        if not self.environment_action:
            result.pop("environmentAction", None)
            result.pop("environment_action", None)
        return result


class ShotSpec(ContractModel):
    format_version: Literal[1, 2] = Field(alias="formatVersion", default=1)
    information: ShotInformation | None = None
    id: str = Field(min_length=1, max_length=80)
    order: int = Field(ge=1, le=24)
    duration_seconds: float = Field(alias="durationSeconds", ge=1, le=15)
    duration_frames: int | None = Field(alias="durationFrames", default=None, ge=24, le=360)

    @model_validator(mode="before")
    @classmethod
    def frame_authority(cls, value):
        if isinstance(value, dict) and value.get("formatVersion", value.get("format_version", cls.model_fields["format_version"].default)) == 2:
            frames = value.get("durationFrames", value.get("duration_frames"))
            if isinstance(frames, int) and not isinstance(frames, bool):
                value = {**value, "durationSeconds": frames / 24}
                value.pop("duration_seconds", None)
        return value
    framing: str = Field(min_length=1, max_length=200)
    camera_movement: str = Field(alias="cameraMovement", min_length=1, max_length=200)
    child_action: str = Field(alias="childAction", max_length=500)
    cat_action: str = Field(alias="catAction", max_length=500)
    environment_change: str = Field(alias="environmentChange", min_length=1, max_length=500)
    transition: Literal["continuous", "soft_cut", "hard_cut"]
    lens: LensDesign | None = None
    composition: CompositionDesign | None = None
    child_blocking: BlockingDesign | None = Field(alias="childBlocking", default=None)
    cat_blocking: BlockingDesign | None = Field(alias="catBlocking", default=None)
    physical_change: PhysicalChangeDesign | None = Field(alias="physicalChange", default=None)
    continuity: ContinuityDesign | None = None
    lighting: LightingDesign | None = None
    sound: ShotSoundDesign | None = None
    director_intent: str | None = Field(alias="directorIntent", default=None, max_length=500)
    generation_risks: list[GenerationRisk] = Field(alias="generationRisks", default_factory=list)
    camera_spatial_relation: str | None = Field(
        alias="cameraSpatialRelation", default=None, min_length=1, max_length=1000
    )
    interaction_constraints: list[str] = Field(alias="interactionConstraints", default_factory=list)
    visual_exclusions: list[str] = Field(alias="visualExclusions", default_factory=list)
    scene_asset_id: uuid.UUID | None = Field(alias="sceneAssetId", default=None)
    environment_use: Literal["recompose", "preserve_layout"] = Field(
        alias="environmentUse", default="recompose"
    )
    confirmed_frame: ConfirmedShotFrame | None = Field(alias="confirmedFrame", default=None)
    action_beats: list[ActionBeat] | None = Field(alias="actionBeats", default=None, min_length=1)

    @model_serializer(mode="wrap")
    def preserve_historical_document(self, handler):
        document = handler(self)
        if self.format_version == 1:
            document.pop("formatVersion", None)
            document.pop("format_version", None)
            for key in ("durationSeconds", "duration_seconds"):
                if key in document:
                    document[key] = int(self.duration_seconds)
        if self.information is None:
            document.pop("information", None)
        # Absent performance design must not change old snapshots or design hashes.
        if self.action_beats is None:
            document.pop("actionBeats", None)
            document.pop("action_beats", None)
        return document

    @model_validator(mode="after")
    def validate_frame_duration(self) -> ShotSpec:
        if self.format_version == 1:
            if self.order > 4 or self.duration_seconds < 2 or not float(self.duration_seconds).is_integer():
                raise ValueError("历史分镜最多4镜且每镜为2至15整数秒")
            if not self.child_action or not self.cat_action:
                raise ValueError("历史分镜需要儿童和猫咪动作")
        else:
            if self.duration_frames is None or self.information is None:
                raise ValueError("新版分镜需要durationFrames与information")
            if any(e.end_frame > self.duration_frames for e in self.information.key_events):
                raise ValueError("关键事件超出镜头")
        if self.duration_frames is not None and abs(self.duration_frames - self.duration_seconds * 24) > 0.00001:
            raise ValueError("镜头帧数必须等于秒数乘24（24fps剪辑帧率）。")
        previous_end = 0
        for beat in self.action_beats or []:
            if beat.start_frame < previous_end or beat.end_frame > self.duration_seconds * 24:
                raise ValueError("节拍须按时间排列、不可重叠或超出镜头时长")
            previous_end = beat.end_frame
        return self


def _screen_direction(value: str) -> Literal["left_to_right", "right_to_left"] | None:
    compact = value.replace(" ", "")
    if any(marker in compact for marker in ("从左向右", "由左向右", "左→右", "左到右")):
        return "left_to_right"
    if any(marker in compact for marker in ("从右向左", "由右向左", "右→左", "右到左")):
        return "right_to_left"
    return None


def _validate_professional_semantics(shots: list[ShotSpec]) -> None:
    for shot in shots:
        assert shot.physical_change is not None
        if (
            not shot.action_beats
            and shot.physical_change.before.strip() == shot.physical_change.after.strip()
        ):
            raise ValueError(
                f"第{shot.order}镜缺少可见的物理状态变化，需明确前后差异或补充动作节拍。"
            )

    for previous, current in zip(shots, shots[1:], strict=False):
        assert previous.composition is not None
        assert current.composition is not None
        outgoing_direction = _screen_direction(previous.composition.screen_direction)
        incoming_direction = _screen_direction(current.composition.screen_direction)
        if (
            outgoing_direction is not None
            and incoming_direction is not None
            and outgoing_direction != incoming_direction
        ):
            raise ValueError(
                f"第{previous.order}镜与第{current.order}镜的画面水平运动方向相反"
                "（180度轴线冲突），请统一方向或在连续性说明中给出合理反转。"
            )

    # Ending quality needs the full shot design and visual review; substrings in a
    # final-frame description cannot establish motion or understand its negation.
    # Keep that uncertainty in director-result advice, not the acceptance boundary.


class ShotPlanDraft(ContractModel):
    source_story_version_id: uuid.UUID = Field(alias="sourceStoryVersionId")
    source_selection_hash: str = Field(alias="sourceSelectionHash", pattern=r"^[a-f0-9]{64}$")
    base_shot_plan_version_id: uuid.UUID | None = Field(alias="baseShotPlanVersionId", default=None)
    expected_active_shot_plan_version_id: uuid.UUID | None = Field(
        alias="expectedActiveShotPlanVersionId", default=None
    )
    clip: LifeClipSpec
    shots: list[ShotSpec] = Field(min_length=1, max_length=24)
    director_treatment: DirectorStoryTreatment | None = Field(
        alias="directorTreatment", default=None
    )
    director_prompt_revision: str | None = Field(
        alias="directorPromptRevision", default=None, max_length=80
    )
    director_model: str | None = Field(alias="directorModel", default=None, max_length=120)
    director_input_hash: str | None = Field(
        alias="directorInputHash", default=None, pattern=r"^[a-f0-9]{64}$"
    )

    @model_validator(mode="after")
    def validate_timeline(self) -> ShotPlanDraft:
        if self.clip.format_version == 1 and len(self.shots) > 4:
            raise ValueError("历史分镜最多4镜")
        if any(s.format_version != self.clip.format_version for s in self.shots):
            raise ValueError("镜头与计划格式版本不一致")
        expected_orders = list(range(1, len(self.shots) + 1))
        if [shot.order for shot in self.shots] != expected_orders:
            raise ValueError("镜头顺序必须从 1 开始且连续")
        if sum(round(s.duration_seconds * 24) for s in self.shots) != self.clip.duration_seconds * 24:
            raise ValueError("镜头总时长必须与短片目标总时长一致")
        return self

    @property
    def total_duration_seconds(self) -> int:
        return round(sum(shot.duration_seconds for shot in self.shots))


class EmotionalArc(ContractModel):
    opening: str = Field(min_length=1, max_length=300)
    development: str = Field(min_length=1, max_length=300)
    resolution: str = Field(min_length=1, max_length=300)


class DirectorMicroEvent(ContractModel):
    trigger: str = Field(min_length=1, max_length=300)
    child_intent: str = Field(alias="childIntent", min_length=1, max_length=300)
    child_action: str = Field(alias="childAction", min_length=1, max_length=400)
    cat_response: str = Field(alias="catResponse", min_length=1, max_length=400)
    visible_cause_and_effect: str = Field(
        alias="visibleCauseAndEffect", min_length=1, max_length=400
    )
    warm_ending: str = Field(alias="warmEnding", min_length=1, max_length=400)


class PropStateChange(ContractModel):
    initial_state: str = Field(alias="initialState", min_length=1, max_length=300)
    changed_state: str = Field(alias="changedState", min_length=1, max_length=300)


class DirectorStoryTreatment(ContractModel):
    logline: str = Field(min_length=1, max_length=400)
    theme: str = Field(min_length=1, max_length=200)
    emotional_tone: list[str] = Field(alias="emotionalTone", min_length=1, max_length=4)
    visual_motif: str = Field(alias="visualMotif", min_length=1, max_length=300)
    spatial_setting: str = Field(alias="spatialSetting", min_length=1, max_length=300)
    emotional_arc: EmotionalArc = Field(alias="emotionalArc")
    micro_event: DirectorMicroEvent = Field(alias="microEvent")
    prop_state_change: PropStateChange | None = Field(alias="propStateChange", default=None)
    sound_intent: str = Field(alias="soundIntent", min_length=1, max_length=300)
    ending_image: str = Field(alias="endingImage", min_length=1, max_length=400)
    feasibility_warnings: list[GenerationRisk] = Field(
        alias="feasibilityWarnings", default_factory=list
    )


class ProfessionalShotOutput(ShotSpec):
    """Generation boundary: required fields are visible in the provider's schema.

    ShotSpec remains nullable for historical, hand-authored timelines.
    """

    duration_frames: int = Field(alias="durationFrames", ge=48, le=360)
    lens: LensDesign
    composition: CompositionDesign
    child_blocking: BlockingDesign = Field(alias="childBlocking")
    cat_blocking: BlockingDesign = Field(alias="catBlocking")
    physical_change: PhysicalChangeDesign = Field(alias="physicalChange")
    continuity: ContinuityDesign
    lighting: LightingDesign
    sound: ShotSoundDesign
    director_intent: str = Field(alias="directorIntent", min_length=1, max_length=500)
    camera_spatial_relation: str = Field(
        alias="cameraSpatialRelation", min_length=1, max_length=1000
    )
    interaction_constraints: list[str] = Field(alias="interactionConstraints")
    visual_exclusions: list[str] = Field(alias="visualExclusions")


class DirectorPlanPayload(ContractModel):
    format_version: Literal[1, 2] = Field(alias="formatVersion", default=1)
    target_duration_seconds: int = Field(alias="targetDurationSeconds", ge=8, le=60)
    director_treatment: DirectorStoryTreatment = Field(alias="directorTreatment")
    shots: list[ShotSpec] = Field(min_length=1, max_length=24)

    @model_serializer(mode="wrap")
    def historical_serialization(self, handler):
        result = handler(self)
        if self.format_version == 1:
            result.pop("formatVersion", None)
            result.pop("format_version", None)
        return result

    @model_validator(mode="after")
    def validate_professional_timeline(self) -> DirectorPlanPayload:
        if self.format_version == 1 and (self.target_duration_seconds > 15 or len(self.shots) > 4):
            raise ValueError("历史导演格式最多15秒4镜")
        if any(s.format_version != self.format_version for s in self.shots):
            raise ValueError("导演与镜头格式不一致")
        if [shot.order for shot in self.shots] != list(range(1, len(self.shots) + 1)):
            raise ValueError("镜头序号必须从1开始连续编号。")
        if sum(round(shot.duration_seconds * 24) for shot in self.shots) != self.target_duration_seconds * 24:
            raise ValueError(
                f"各镜头秒数之和必须精确等于目标时长{self.target_duration_seconds}秒。"
            )
        required = (
            "lens",
            "composition",
            "child_blocking",
            "cat_blocking",
            "physical_change",
            "continuity",
            "lighting",
            "sound",
            "director_intent",
        )
        for shot in self.shots:
            missing = [name for name in required if getattr(shot, name) is None
                       and not (self.format_version == 2 and name in {"child_blocking", "cat_blocking"}
                                and name.split("_")[0] not in shot.information.visible_subjects)]
            if shot.duration_frames is None:
                missing.append("duration_frames")
            if missing:
                raise ValueError(f"第{shot.order}镜缺少必填内容：{', '.join(missing)}")
        _validate_professional_semantics(self.shots)
        return self


class ProfessionalDirectorOutput(DirectorPlanPayload):
    shots: list[ProfessionalShotOutput] = Field(min_length=1, max_length=4)


class PerformanceShotOutput(ProfessionalShotOutput):
    action_beats: list[ActionBeat] = Field(alias="actionBeats", min_length=1)


class PerformanceDirectorOutput(DirectorPlanPayload):
    shots: list[PerformanceShotOutput] = Field(min_length=1, max_length=4)


class ProfessionalShotPlanDraft(ShotPlanDraft):
    director_treatment: DirectorStoryTreatment = Field(alias="directorTreatment")
    director_prompt_revision: str = Field(
        alias="directorPromptRevision", min_length=1, max_length=80
    )
    director_model: str = Field(alias="directorModel", min_length=1, max_length=120)
    director_input_hash: str = Field(alias="directorInputHash", pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_professional_execution_fields(self) -> ProfessionalShotPlanDraft:
        professional_fields = (
            "lens",
            "composition",
            "child_blocking",
            "cat_blocking",
            "physical_change",
            "continuity",
            "lighting",
            "sound",
            "director_intent",
        )
        for shot in self.shots:
            missing = [name for name in professional_fields if getattr(shot, name) is None
                       and not (shot.format_version == 2 and name in {"child_blocking", "cat_blocking"}
                                and name.split("_")[0] not in shot.information.visible_subjects)]
            if shot.duration_frames is None:
                missing.append("duration_frames")
            if missing:
                raise ValueError(f"第{shot.order}镜缺少必填内容：{', '.join(missing)}")
        if sum(shot.duration_frames or 0 for shot in self.shots) != self.clip.duration_seconds * 24:
            raise ValueError("各镜头帧数之和必须等于目标时长乘24的总帧数。")
        _validate_professional_semantics(self.shots)
        safety_content = self.model_dump(mode="json", by_alias=True)
        treatment = safety_content.get("directorTreatment")
        if isinstance(treatment, dict):
            treatment.pop("feasibilityWarnings", None)
        for shot in safety_content.get("shots", []):
            if isinstance(shot, dict):
                shot.pop("generationRisks", None)
                shot.pop("visualExclusions", None)
        serialized = str(safety_content)
        prohibited = ("8–9岁", "8-9岁", "青少年脸型", "成人化身体", "成人化表情")
        if any(term in serialized for term in prohibited):
            raise ValueError("分镜包含成人化或超龄描述，请按角色设定修正。")
        return self


class NarrativeShotOutput(ShotSpec):
    format_version: Literal[2] = Field(alias="formatVersion", default=2)
    information: ShotInformation
    duration_frames: int = Field(alias="durationFrames", ge=24, le=360)
    lens: LensDesign
    composition: CompositionDesign
    physical_change: PhysicalChangeDesign = Field(alias="physicalChange")
    continuity: ContinuityDesign
    lighting: LightingDesign
    sound: ShotSoundDesign
    director_intent: str = Field(alias="directorIntent", min_length=1, max_length=500)
    action_beats: list[ActionBeat] = Field(alias="actionBeats", min_length=1)


class NarrativeDirectorOutput(DirectorPlanPayload):
    format_version: Literal[2] = Field(alias="formatVersion", default=2)
    shots: list[NarrativeShotOutput] = Field(min_length=1, max_length=24)
