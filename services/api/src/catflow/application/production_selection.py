"""Adopted footage, observed state and exact mappings into the edit timeline."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from catflow.domain.contract import ContractModel
from catflow.domain.video_repairs import EditDecisionListV3
from .production_plan import ProductionUnit, shot_frames, unit_shots

ObservationVerdict = Literal["pass", "warning", "fail", "unknown", "not_applicable"]


class ObservedFact(ContractModel):
    key: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=500)
    certainty: Literal["observed", "unobserved", "uncertain"]
    required: bool = True


class EndState(ContractModel):
    facts: list[ObservedFact] = Field(min_length=1, max_length=40)
    unfinished_actions: str = Field(alias="unfinishedActions", default="", max_length=500)
    evidence_frame: int = Field(alias="evidenceFrame", ge=0)
    evidence_asset_id: uuid.UUID = Field(alias="evidenceAssetId")
    confirmed: bool = False

    @model_validator(mode="after")
    def unique_keys(self):
        if len({fact.key for fact in self.facts}) != len(self.facts):
            raise ValueError("实际状态标识重复")
        return self


class UnitTake(ContractModel):
    shot_id: str = Field(alias="shotId", min_length=1)
    source_in_frame: int = Field(alias="sourceInFrame", ge=0)
    duration_frames: int = Field(alias="durationFrames", gt=0)


class EventObservation(ContractModel):
    shot_id: str = Field(alias="shotId")
    event_id: str = Field(alias="eventId")
    verdict: ObservationVerdict
    source_start_frame: int = Field(alias="sourceStartFrame", ge=0)
    source_end_frame: int = Field(alias="sourceEndFrame", gt=0)
    notes: str = Field(default="", max_length=1000)


class UnitSelectionCommand(ContractModel):
    plan_id: uuid.UUID = Field(alias="planId")
    expected_design_hash: str = Field(alias="expectedDesignHash", pattern=r"^[a-f0-9]{64}$")
    expected_selection_id: uuid.UUID | None = Field(alias="expectedSelectionId", default=None)
    asset_id: uuid.UUID = Field(alias="assetId")
    takes: list[UnitTake] = Field(min_length=1, max_length=4)
    events: list[EventObservation] = Field(default_factory=list, max_length=48)
    end_state: EndState = Field(alias="endState")
    disposition: Literal["accepted", "accepted_with_issues", "rejected"]
    notes: str = Field(default="", max_length=4000)
    audio_policy: Literal["native", "mute"] = Field(alias="audioPolicy", default="native")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class UnitEvidenceCommand(ContractModel):
    plan_id: uuid.UUID = Field(alias="planId")
    asset_id: uuid.UUID = Field(alias="assetId")
    source_frame: int = Field(alias="sourceFrame", ge=0)


class UnitSelectionDto(ContractModel):
    id: uuid.UUID
    project_id: uuid.UUID = Field(alias="projectId")
    unit_id: str = Field(alias="unitId")
    revision: int
    active: bool
    input_hash: str = Field(alias="inputHash")
    request_hash: str = Field(alias="requestHash")
    idempotency_key: str = Field(alias="idempotencyKey")
    document: dict
    created_at: datetime = Field(alias="createdAt")


class ProductionAssembly(ContractModel):
    expected_selection_hashes: dict[str, str] = Field(alias="expectedSelectionHashes")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


def validate_selection(plan: dict, unit: ProductionUnit, command: UnitSelectionCommand,
                       asset) -> list[dict]:
    shots = unit_shots(plan, unit)
    if [take.shot_id for take in command.takes] != unit.shot_ids:
        raise ValueError("取用必须按单元镜头顺序覆盖全部镜头")
    metadata = asset.metadata
    if (metadata.get("frameRateNumerator"), metadata.get("frameRateDenominator")) != (24, 1):
        raise ValueError("候选必须实际为24fps，不自动变速或补帧")
    last_end, timeline_frame = 0, 0
    intervals = {}
    for shot, take in zip(shots, command.takes, strict=True):
        end = take.source_in_frame + take.duration_frames
        if (take.duration_frames != shot_frames(shot) or take.source_in_frame < last_end
                or end > metadata.get("durationFrames", 0)):
            raise ValueError("取用帧数不匹配、重叠或超出素材")
        intervals[shot.id] = (take.source_in_frame, end, timeline_frame)
        timeline_frame += take.duration_frames
        last_end = end
    if command.end_state.evidence_frame != last_end - 1:
        raise ValueError("实际结束状态必须以最后取用帧为证据")
    required = {actor for shot in shots if shot.information
                for actor in shot.information.visible_subjects if actor in {"child", "cat"}}
    required.update(f"prop:{key}" for shot in shots if shot.information
                    for key in shot.information.prop_keys)
    supplied = {fact.key for fact in command.end_state.facts if fact.required}
    if required - supplied:
        raise ValueError("实际状态缺少关键主体：" + "、".join(sorted(required - supplied)))
    planned = {(s.id, e.id): e for s in shots if s.information for e in s.information.key_events}
    seen, mapped = set(), []
    for event in command.events:
        key = (event.shot_id, event.event_id)
        if key not in planned or key in seen:
            raise ValueError("检查事件不存在或重复")
        seen.add(key)
        start, end, offset = intervals[event.shot_id]
        if not start <= event.source_start_frame < event.source_end_frame <= end:
            raise ValueError("检查区间超出对应镜头取用范围")
        mapped.append({**event.model_dump(mode="json", by_alias=True),
                       "unitStartFrame": offset + event.source_start_frame - start,
                       "unitEndFrame": offset + event.source_end_frame - start})
    if command.disposition == "accepted":
        if any(e.required and key not in seen for key, e in planned.items()):
            raise ValueError("采用通过前必须检查所有关键事件")
        if any(e.verdict not in {"pass", "not_applicable"} for e in command.events):
            raise ValueError("存在未达到或无法判断的事件，请标记带问题采用")
        if any(e.verdict == "not_applicable" and planned[(e.shot_id, e.event_id)].required
               for e in command.events):
            raise ValueError("必需事件不能标记不适用")
    return mapped


def selection_can_continue(selection: UnitSelectionDto) -> bool:
    document = selection.document
    state = document["endState"]
    return (document["disposition"] != "rejected" and state["confirmed"]
            and all(f["certainty"] == "observed" for f in state["facts"] if f["required"]))


def assemble_timeline(selections: list[UnitSelectionDto], *, plan_id=None) -> EditDecisionListV3:
    segments, audio = [], []
    events, offset = [], 0
    for selection in selections:
        document = selection.document
        for take in document["takes"]:
            source = {"assetId": document["assetId"], "sha256": document["assetSha256"],
                      "sourceInFrame": take["sourceInFrame"], "durationFrames": take["durationFrames"]}
            segments.append({**source, "id": str(uuid.uuid4()), "origin": "base_video"})
            audio.append({**source, "requireAudio": False,
                          "muted": document.get("audioPolicy") == "mute"})
        events.extend({**event, "unitId": selection.unit_id, "assetId": document["assetId"],
                       "timelineStartFrame": offset + event["unitStartFrame"],
                       "timelineEndFrame": offset + event["unitEndFrame"]}
                      for event in document.get("events", []))
        offset += sum(take["durationFrames"] for take in document["takes"])
    first = selections[0].document
    return EditDecisionListV3.model_validate({
        "format": "catflow-edl-v3", "frameRate": {"numerator": 24, "denominator": 1},
        "rootVideoAssetId": first["assetId"], "rootVideoSha256": first["assetSha256"],
        "videoSegments": segments, "transitions": [], "audio": {"policy": "segmented", "segments": audio},
        "output": {"aspectRatio": "9:16", "width": 720, "height": 1280, "format": "mp4"},
        **({"productionEvidence": {"planId": str(plan_id),
                                   "selections": {s.unit_id: {"id": str(s.id), "hash": s.input_hash} for s in
                                                  selections},
                                   "events": events}} if plan_id else {}),
    })
