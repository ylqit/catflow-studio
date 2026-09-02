from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field, model_validator

from .contract import ContractModel


class MicroEvent(ContractModel):
    trigger: str = Field(min_length=1, max_length=500)
    child_action: str = Field(alias="childAction", min_length=1, max_length=500)
    cat_response: str = Field(alias="catResponse", min_length=1, max_length=500)
    visible_change: str = Field(alias="visibleChange", min_length=1, max_length=500)
    warm_ending: str = Field(alias="warmEnding", min_length=1, max_length=500)


class LifeStoryProposalDraft(ContractModel):
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=4_000)
    trigger: str = Field(min_length=1, max_length=500)
    child_action: str = Field(alias="childAction", min_length=1, max_length=500)
    cat_response: str = Field(alias="catResponse", min_length=1, max_length=500)
    visible_change: str = Field(alias="visibleChange", min_length=1, max_length=500)
    warm_ending: str = Field(alias="warmEnding", min_length=1, max_length=500)
    target_duration_seconds: int = Field(alias="targetDurationSeconds", ge=8, le=15)
    dialogue_policy: Literal["none", "minimal"] = Field(alias="dialoguePolicy")
    environment_intent: str = Field(alias="environmentIntent", min_length=1, max_length=500)
    prop_intent: str | None = Field(alias="propIntent", default=None, max_length=300)

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
    duration_seconds: int = Field(alias="durationSeconds", ge=8, le=15)
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
    micro_motions: list[str] = Field(alias="microMotions", default_factory=list, max_length=3)


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
    ambience: list[str] = Field(default_factory=list, max_length=3)
    object_effects: list[str] = Field(alias="objectEffects", default_factory=list, max_length=3)
    movement_effects: list[str] = Field(alias="movementEffects", default_factory=list, max_length=3)
    music_intent: str = Field(alias="musicIntent", min_length=1, max_length=240)
    dialogue: str | None = Field(default=None, max_length=240)


class GenerationRisk(ContractModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=400)


class ShotSpec(ContractModel):
    id: str = Field(min_length=1, max_length=80)
    order: int = Field(ge=1, le=4)
    duration_seconds: int = Field(alias="durationSeconds", ge=2, le=15)
    duration_frames: int | None = Field(alias="durationFrames", default=None, ge=48, le=360)
    framing: str = Field(min_length=1, max_length=200)
    camera_movement: str = Field(alias="cameraMovement", min_length=1, max_length=200)
    child_action: str = Field(alias="childAction", min_length=1, max_length=500)
    cat_action: str = Field(alias="catAction", min_length=1, max_length=500)
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
    generation_risks: list[GenerationRisk] = Field(
        alias="generationRisks", default_factory=list, max_length=8
    )

    @model_validator(mode="after")
    def validate_frame_duration(self) -> ShotSpec:
        if self.duration_frames is not None and self.duration_frames != self.duration_seconds * 24:
            raise ValueError("durationFrames must equal durationSeconds at the 24 fps edit rate")
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
        if shot.physical_change.before.strip() == shot.physical_change.after.strip():
            raise ValueError(
                f"professional shot {shot.order} requires a visible physical state change"
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
                f"screen direction conflict between professional shots "
                f"{previous.order} and {current.order}"
            )

    assert shots[-1].continuity is not None
    final_frame = shots[-1].continuity.final_frame
    static_markers = ("原地互看", "画面静止", "完全静止", "停帧", "循环动作")
    active_markers = (
        "迈",
        "走",
        "折",
        "放",
        "收",
        "摆",
        "落",
        "抬",
        "推",
        "盖",
        "转",
        "移",
        "擦",
        "浇",
        "滚",
        "提",
        "拿",
        "起身",
    )
    if any(marker in final_frame for marker in static_markers) or not any(
        marker in final_frame for marker in active_markers
    ):
        raise ValueError("professional shot plan requires an observable active ending")


class ShotPlanDraft(ContractModel):
    source_story_version_id: uuid.UUID = Field(alias="sourceStoryVersionId")
    source_selection_hash: str = Field(alias="sourceSelectionHash", pattern=r"^[a-f0-9]{64}$")
    clip: LifeClipSpec
    shots: list[ShotSpec] = Field(min_length=1, max_length=4)
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
        expected_orders = list(range(1, len(self.shots) + 1))
        if [shot.order for shot in self.shots] != expected_orders:
            raise ValueError("镜头顺序必须从 1 开始且连续")
        if self.total_duration_seconds != self.clip.duration_seconds:
            raise ValueError("镜头总时长必须与短片目标总时长一致")
        return self

    @property
    def total_duration_seconds(self) -> int:
        return sum(shot.duration_seconds for shot in self.shots)


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
        alias="feasibilityWarnings", default_factory=list, max_length=8
    )


class DirectorPlanPayload(ContractModel):
    target_duration_seconds: int = Field(alias="targetDurationSeconds", ge=8, le=15)
    director_treatment: DirectorStoryTreatment = Field(alias="directorTreatment")
    shots: list[ShotSpec] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def validate_professional_timeline(self) -> DirectorPlanPayload:
        if [shot.order for shot in self.shots] != list(range(1, len(self.shots) + 1)):
            raise ValueError("professional shot order must start at one and remain continuous")
        if sum(shot.duration_seconds for shot in self.shots) != self.target_duration_seconds:
            raise ValueError("professional shot durations must equal targetDurationSeconds")
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
            missing = [name for name in required if getattr(shot, name) is None]
            if shot.duration_frames is None:
                missing.append("duration_frames")
            if missing:
                raise ValueError(
                    f"professional shot {shot.order} is missing: {', '.join(missing)}"
                )
        _validate_professional_semantics(self.shots)
        return self


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
            missing = [name for name in professional_fields if getattr(shot, name) is None]
            if shot.duration_frames is None:
                missing.append("duration_frames")
            if missing:
                raise ValueError(
                    f"professional shot {shot.order} is missing: {', '.join(missing)}"
                )
        if sum(shot.duration_frames or 0 for shot in self.shots) != self.clip.duration_seconds * 24:
            raise ValueError("professional shot frames must equal the target duration at 24 fps")
        _validate_professional_semantics(self.shots)
        serialized = self.model_dump_json(by_alias=True, ensure_ascii=False)
        prohibited = ("8–9岁", "8-9岁", "青少年脸型", "成人化身体", "成人化表情")
        if any(term in serialized for term in prohibited):
            raise ValueError("professional shot plan contains an adult or older-child description")
        return self
