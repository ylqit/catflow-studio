from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from catflow.domain.contract import ContractModel

from .series import SeriesBibleDraft, SeriesEpisodeOutlineDraft

ContinuityDirection = Literal["incoming", "outgoing"]
ContinuitySource = Literal["planned", "confirmed", "final_video"]
ContinuityDecision = Literal["inherit", "adjust", "reset"]
CONTINUITY_DECISION_FIELDS = {
    "wardrobe",
    "location",
    "weather",
    "timeOfDay",
    "lighting",
    "childState",
    "catState",
    "spatialPositions",
    "props",
    "unfinishedActions",
    "endingImage",
}


class ContinuityPropState(ContractModel):
    key: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    state: str = Field(min_length=1, max_length=500)
    location: str | None = Field(default=None, max_length=300)
    owner: Literal["child", "cat", "environment"] | None = None


class EpisodeContinuityState(ContractModel):
    wardrobe: str = Field(min_length=1, max_length=1_000)
    location: str = Field(min_length=1, max_length=1_000)
    weather: str = Field(min_length=1, max_length=500)
    time_of_day: str = Field(alias="timeOfDay", min_length=1, max_length=500)
    lighting: str = Field(min_length=1, max_length=500)
    child_state: str = Field(alias="childState", min_length=1, max_length=1_000)
    cat_state: str = Field(alias="catState", min_length=1, max_length=1_000)
    spatial_positions: str = Field(alias="spatialPositions", min_length=1, max_length=1_000)
    props: list[ContinuityPropState] = Field(default_factory=list)
    unfinished_actions: list[str] = Field(alias="unfinishedActions", default_factory=list)
    ending_image: str = Field(alias="endingImage", min_length=1, max_length=1_000)


class EpisodeContinuitySnapshotDto(ContractModel):
    id: uuid.UUID
    episode_id: uuid.UUID = Field(alias="episodeId")
    direction: ContinuityDirection
    source: ContinuitySource
    state: EpisodeContinuityState
    decisions: dict[str, ContinuityDecision] = Field(default_factory=dict)
    confirmed: bool
    active: bool
    created_at: datetime = Field(alias="createdAt")


class EpisodeContinuityDto(ContractModel):
    episode_id: uuid.UUID = Field(alias="episodeId")
    previous_episode_id: uuid.UUID | None = Field(alias="previousEpisodeId", default=None)
    incoming: EpisodeContinuitySnapshotDto | None = None
    outgoing: EpisodeContinuitySnapshotDto | None = None


class EpisodeContinuityConfirmCommand(ContractModel):
    direction: ContinuityDirection
    state: EpisodeContinuityState
    decisions: dict[str, ContinuityDecision] = Field(default_factory=dict)
    expected_snapshot_id: uuid.UUID | None = Field(alias="expectedSnapshotId", default=None)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)

    @model_validator(mode="after")
    def require_decisions(self) -> EpisodeContinuityConfirmCommand:
        if self.direction == "incoming" and not self.decisions:
            raise ValueError("incoming continuity confirmation requires decisions")
        unknown = set(self.decisions) - CONTINUITY_DECISION_FIELDS
        if unknown:
            raise ValueError(f"unknown continuity decision fields: {', '.join(sorted(unknown))}")
        return self


class EpisodeContinuityResetCommand(ContractModel):
    direction: ContinuityDirection
    expected_snapshot_id: uuid.UUID = Field(alias="expectedSnapshotId")


class EpisodeContinuityKeyframesCommand(ContractModel):
    asset_ids: list[uuid.UUID] = Field(alias="assetIds", max_length=2)

    @model_validator(mode="after")
    def require_unique_assets(self) -> EpisodeContinuityKeyframesCommand:
        if len(self.asset_ids) != len(set(self.asset_ids)):
            raise ValueError("continuity keyframes must be unique")
        return self


class SeriesAssetBindingCommand(ContractModel):
    binding_key: str = Field(alias="bindingKey", min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=64)
    asset_id: uuid.UUID = Field(alias="assetId")


class SeriesAssetBindingsPatchCommand(ContractModel):
    bindings: list[SeriesAssetBindingCommand] = Field(max_length=100)

    @model_validator(mode="after")
    def require_unique_binding_keys(self) -> SeriesAssetBindingsPatchCommand:
        keys = [item.binding_key.strip().casefold() for item in self.bindings]
        if len(keys) != len(set(keys)):
            raise ValueError("series asset binding keys must be unique")
        return self


class SeriesAssetBindingDto(ContractModel):
    id: uuid.UUID
    series_id: uuid.UUID = Field(alias="seriesId")
    binding_key: str = Field(alias="bindingKey")
    role: str
    asset_id: uuid.UUID = Field(alias="assetId")
    asset_sha256: str = Field(alias="assetSha256", pattern=r"^[a-f0-9]{64}$")
    active: bool
    created_at: datetime = Field(alias="createdAt")


def planned_continuity_state(
    *,
    bible: SeriesBibleDraft,
    episode: SeriesEpisodeOutlineDraft,
    direction: ContinuityDirection,
    previous_episode: SeriesEpisodeOutlineDraft | None = None,
) -> EpisodeContinuityState:
    locations = {item.key: item.name for item in bible.recurring_locations}
    props = {item.key: item for item in bible.recurring_props}
    location = "、".join(
        locations.get(key, key) for key in episode.recurring_location_keys
    ) or "未在当前简纲中指定"
    wardrobe = "；".join(bible.wardrobe_rules) or "未在系列方案中指定"
    selected_props = [
        ContinuityPropState(
            key=key,
            name=props[key].name if key in props else key,
            state=(
                props[key].continuity_rule
                if key in props
                else "状态需在制作本集时确认"
            ),
        )
        for key in episode.recurring_prop_keys
    ]
    if direction == "incoming":
        inherited = previous_episode.ending_state if previous_episode is not None else None
        state = inherited or episode.opening_state
        unfinished = (
            list(previous_episode.continuity_carryover)
            if previous_episode is not None
            else []
        )
        ending_image = episode.opening_state
    else:
        state = episode.ending_state
        unfinished = list(episode.continuity_carryover)
        ending_image = episode.ending_state
    return EpisodeContinuityState(
        wardrobe=wardrobe,
        location=location,
        weather="未在当前简纲中指定",
        timeOfDay="未在当前简纲中指定",
        lighting="沿用系列画风与本集环境设定",
        childState=state,
        catState=state,
        spatialPositions=(
            episode.opening_state if direction == "incoming" else episode.ending_state
        ),
        props=selected_props,
        unfinishedActions=unfinished,
        endingImage=ending_image,
    )
