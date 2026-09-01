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


class ShotSpec(ContractModel):
    id: str = Field(min_length=1, max_length=80)
    order: int = Field(ge=1, le=4)
    duration_seconds: int = Field(alias="durationSeconds", ge=2, le=15)
    framing: str = Field(min_length=1, max_length=200)
    camera_movement: str = Field(alias="cameraMovement", min_length=1, max_length=200)
    child_action: str = Field(alias="childAction", min_length=1, max_length=500)
    cat_action: str = Field(alias="catAction", min_length=1, max_length=500)
    environment_change: str = Field(alias="environmentChange", min_length=1, max_length=500)
    transition: Literal["continuous", "soft_cut", "hard_cut"]


class ShotPlanDraft(ContractModel):
    source_story_version_id: uuid.UUID = Field(alias="sourceStoryVersionId")
    source_selection_hash: str = Field(alias="sourceSelectionHash", pattern=r"^[a-f0-9]{64}$")
    clip: LifeClipSpec
    shots: list[ShotSpec] = Field(min_length=1, max_length=4)

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
