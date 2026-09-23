"""Immutable production plans, bounded grouping and source-sensitive design hashes."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from catflow.domain.contract import ContractModel
from catflow.domain.models import ShotSpec
from .job_execution import PaidJobCommand

PRODUCTION_REVISION = "catflow-production-v1"
PRODUCTION_CAPABILITIES = {
    "revision": PRODUCTION_REVISION, "minimumWorkSeconds": 8, "maximumWorkSeconds": 60,
    "frameRate": 24, "minimumShotFrames": 24, "maximumShotFrames": 360,
    "maximumShots": 24, "minimumGenerationSeconds": 4, "maximumGenerationSeconds": 15,
    "maximumUnitShots": 4,
}


def document_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


class LayoutPoint(ContractModel):
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["region", "furniture", "support", "child", "cat", "prop", "camera"]
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    direction: str = Field(default="", max_length=200)


class SceneBinding(ContractModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    asset_id: uuid.UUID = Field(alias="assetId")
    sha256: str | None = None
    layout: list[LayoutPoint] = Field(default_factory=list, max_length=40)
    axis: str = Field(default="", max_length=500)


class PropBinding(ContractModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    asset_id: uuid.UUID = Field(alias="assetId")
    sha256: str | None = None
    identity: str = Field(min_length=1, max_length=500)
    initial_state: str = Field(alias="initialState", min_length=1, max_length=500)
    planned_change: str = Field(alias="plannedChange", default="", max_length=500)
    location: str = Field(min_length=1, max_length=300)
    owner: Literal["child", "cat", "environment"] = "environment"


class ProductionUnit(ContractModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    shot_ids: list[str] = Field(alias="shotIds", min_length=1, max_length=4)
    continuity: Literal["inherit", "reset"] = "reset"
    reason: str = Field(default="首个单元建立起点", min_length=1, max_length=500)
    generation_mode: Literal["references", "from_frame"] = Field(
        alias="generationMode", default="references"
    )


class ProductionPlanDraft(ContractModel):
    shot_plan_version_id: uuid.UUID = Field(alias="shotPlanVersionId")
    scenes: list[SceneBinding] = Field(default_factory=list, max_length=24)
    props: list[PropBinding] = Field(default_factory=list, max_length=24)
    units: list[ProductionUnit] = Field(default_factory=list, max_length=24)
    expected_active_plan_id: uuid.UUID | None = Field(alias="expectedActivePlanId", default=None)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)

    @model_validator(mode="after")
    def unique_keys(self):
        for items in (self.scenes, self.props):
            if len({item.key for item in items}) != len(items):
                raise ValueError("场景或道具标识重复")
        if len({unit.id for unit in self.units}) != len(self.units):
            raise ValueError("生成单元标识重复")
        return self


class ProductionPlanDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    revision: int
    active: bool
    input_hash: str = Field(alias="inputHash")
    request_hash: str = Field(alias="requestHash")
    idempotency_key: str = Field(alias="idempotencyKey")
    document: dict
    created_at: datetime = Field(alias="createdAt")


class ProductionActivation(ContractModel):
    expected_active_plan_id: uuid.UUID | None = Field(alias="expectedActivePlanId")


class UnitTarget(ContractModel):
    plan_id: uuid.UUID = Field(alias="planId")


class UnitGeneration(UnitTarget, PaidJobCommand):
    expected_input_hash: str = Field(alias="expectedInputHash", pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


def shot_frames(shot: ShotSpec) -> int:
    return shot.duration_frames or round(shot.duration_seconds * 24)


def unit_shots(document: dict, unit: ProductionUnit) -> list[ShotSpec]:
    lookup = {shot["id"]: ShotSpec.model_validate(shot) for shot in document["shots"]}
    return [lookup[key] for key in unit.shot_ids]


def unit_design_hash(document: dict, unit: ProductionUnit, *, edit_independent: bool = False) -> str:
    """Strict design identity, or content equivalence for explicitly reconfirmed recuts."""
    shots = unit_shots(document, unit)
    scene_keys = {s.information.scene_key if s.information else "main" for s in shots}
    prop_keys = {key for s in shots if s.information for key in s.information.prop_keys}
    shot_documents = [s.model_dump(mode="json", by_alias=True, exclude={"confirmed_frame"}) for s in shots]
    if edit_independent:
        for shot in shot_documents:
            shot.pop("durationFrames", None)
            shot.pop("durationSeconds", None)
            for item in [*(shot.get("actionBeats") or []), *shot.get("information", {}).get("keyEvents", [])]:
                item.pop("startFrame", None)
                item.pop("endFrame", None)
    return document_hash({
        "revision": PRODUCTION_REVISION, "canon": document["canon"],
        "unit": unit.model_dump(mode="json", by_alias=True),
        "shots": shot_documents, "totalFrames": sum(shot_frames(s) for s in shots),
        "scenes": [s for s in document["scenes"] if s["key"] in scene_keys],
        "props": [p for p in document["props"] if p["key"] in prop_keys],
    })


def group_shots(shots: list[ShotSpec], scenes: list[SceneBinding], props: list[PropBinding],
                canon_references: list[dict], maximum_references: int) -> list[ProductionUnit]:
    """Deterministic greedy grouping; no paid call or reference omission."""
    scenes_by_key = {s.key: s for s in scenes}
    props_by_key = {p.key: p for p in props}
    groups: list[list[ShotSpec]] = []
    for shot in shots:
        key = shot.information.scene_key if shot.information else "main"
        if key not in scenes_by_key:
            raise ValueError(f"镜头{shot.id}缺少场景：{key}")
        prior = groups[-1] if groups else []
        candidate = [*prior, shot]
        candidate_keys = {s.information.scene_key if s.information else "main" for s in candidate}
        used_props = {p for s in candidate if s.information for p in s.information.prop_keys}
        if used_props - props_by_key.keys():
            raise ValueError("缺少关键道具图片绑定：" + "、".join(sorted(used_props - props_by_key.keys())))
        ids = {r["sha256"] for r in canon_references}
        ids.update(scenes_by_key[k].sha256 for k in candidate_keys)
        ids.update(props_by_key[k].sha256 for k in used_props)
        split = bool(prior) and (
                len(candidate_keys) > 1 or len(candidate) > 4
                or sum(shot_frames(s) for s in candidate) > 360
                or len(ids) + int(len(groups) > 1) > maximum_references
                or (shot.information and shot.information.isolate_generation)
                or (prior[-1].information and prior[-1].information.isolate_generation)
        )
        if not prior or split:
            groups.append([shot])
        else:
            prior.append(shot)
    return [ProductionUnit(
        id=f"unit-{index + 1}", shotIds=[s.id for s in group],
        continuity="reset" if index == 0 or (group[0].information and groups[index - 1][-1].information
                                             and group[0].information.scene_key != groups[index - 1][
                                                 -1].information.scene_key) else "inherit",
        reason="按场景建立起点" if index == 0 or (group[0].information and groups[index - 1][-1].information
                                                  and group[0].information.scene_key != groups[index - 1][
                                                      -1].information.scene_key) else "承接上一单元已确认的实际状态",
    ) for index, group in enumerate(groups)]


def validate_units(shots: list[ShotSpec], units: list[ProductionUnit]) -> None:
    if [key for u in units for key in u.shot_ids] != [s.id for s in shots]:
        raise ValueError("单元必须按顺序覆盖所有镜头一次，不可遗漏或重复")
    lookup = {s.id: s for s in shots}
    if len(lookup) != len(shots):
        raise ValueError("镜头ID重复")
    for index, unit in enumerate(units):
        members = [lookup[key] for key in unit.shot_ids]
        if sum(shot_frames(s) for s in members) > 360:
            raise ValueError(f"{unit.id}超过单次15秒能力")
        if len({s.information.scene_key if s.information else "main" for s in members}) > 1:
            raise ValueError(f"{unit.id}不可跨场景")
        if index == 0 and unit.continuity == "inherit":
            raise ValueError("首个单元没有可承接的上游")
        if unit.generation_mode == "from_frame" and len(members) != 1:
            raise ValueError("首版严格首帧模式只支持单镜单元")


def provider_duration(shots: list[ShotSpec]) -> int:
    frames = sum(shot_frames(s) for s in shots)
    if not 24 <= frames <= 360:
        raise ValueError("单元取用帧数超出模型能力")
    return max(4, math.ceil(frames / 24))
