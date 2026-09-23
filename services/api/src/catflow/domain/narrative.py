"""Narrative intent and observable evidence shared by planning and production."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_serializer, model_validator

from .contract import ContractModel


class NarrativeDesign(ContractModel):
    character_goal: str = Field(alias="characterGoal", min_length=1, max_length=500)
    audience_expectation: str = Field(alias="audienceExpectation", min_length=1, max_length=500)
    small_disruption: str = Field(alias="smallDisruption", default="", max_length=500)
    reveal: str = Field(default="", max_length=500)
    response: str = Field(min_length=1, max_length=500)
    payoff: str = Field(min_length=1, max_length=500)
    adaptation_notes: str = Field(alias="adaptationNotes", default="", max_length=1000)


class NarrativeDocument(ContractModel):
    """Optional new narrative metadata must not alter historical snapshots."""
    narrative_design: NarrativeDesign | None = Field(alias="narrativeDesign", default=None)

    @model_serializer(mode="wrap")
    def historical_narrative(self, handler):
        result = handler(self)
        if self.narrative_design is None:
            result.pop("narrativeDesign", None)
            result.pop("narrative_design", None)
        return result


class KeyEvent(ContractModel):
    id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=400)
    start_frame: int = Field(alias="startFrame", ge=0)
    end_frame: int = Field(alias="endFrame", gt=0)
    required: bool = True

    @model_validator(mode="after")
    def ordered(self):
        if self.end_frame <= self.start_frame:
            raise ValueError("关键事件结束帧必须晚于开始帧")
        return self


class ShotInformation(ContractModel):
    role: Literal["establish", "action", "reaction", "reveal", "payoff", "transition"]
    new_information: str = Field(alias="newInformation", min_length=1, max_length=500)
    visible_subjects: list[Literal["child", "cat", "prop", "environment"]] = Field(
        alias="visibleSubjects", min_length=1, max_length=4
    )
    narrative_link: str = Field(alias="narrativeLink", default="", max_length=300)
    key_events: list[KeyEvent] = Field(alias="keyEvents", default_factory=list, max_length=12)
    scene_key: str = Field(alias="sceneKey", default="main", min_length=1, max_length=80)
    prop_keys: list[str] = Field(alias="propKeys", default_factory=list, max_length=12)
    isolate_generation: bool = Field(alias="isolateGeneration", default=False)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.visible_subjects)) != len(self.visible_subjects):
            raise ValueError("可见主体不可重复")
        if len({e.id for e in self.key_events}) != len(self.key_events):
            raise ValueError("关键事件标识不可重复")
        if len(set(self.prop_keys)) != len(self.prop_keys):
            raise ValueError("道具标识不可重复")
        return self


NARRATIVE_PLANNING = (
    "用narrativeDesign明确角色目标、观众期待、小意外、可见揭示、角色回应和结尾回报。"
    "小意外和反转可为空，不为填字段强加冲突。新增互动记录在adaptationNotes，保留来源事实。"
    "温暖来自角色理解并回应对方的选择，不用重复凝视、眨眼或摆尾代替故事推进。"
    "较长作品仍只有一条完整故事线，不在每15秒重新介绍场景或收尾。"
)

DIRECTOR_NARRATIVE = (
    "使用formatVersion=2；每镜durationFrames是唯一时长依据（24fps，24至360帧），"
    "durationSeconds填写durationFrames/24。最多24镜，总帧数精确等于作品时长乘24。"
    "information保存镜头职责role、新信息newInformation、visibleSubjects、叙事对应narrativeLink、"
    "关键事件keyEvents（id/description/startFrame/endFrame/required）、sceneKey、propKeys、"
    "isolateGeneration。所有事件区间是本镜局部帧。允许单角色、纯道具或环境揭示镜头。"
    "未入画角色的动作可为空、blocking可为null，不虚构画外动作。"
    "纯道具／环境节拍在environmentAction描述可见变化。"
    "机位、支撑、同一道具实例和遮挡仍需明确；从期待到揭示再到回应安排信息。"
    "独立镜头只用于确实需要分开生成的交互，不机械拆碎流畅表演。"
)
