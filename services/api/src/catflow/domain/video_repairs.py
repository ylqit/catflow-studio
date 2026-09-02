from __future__ import annotations

import math
import uuid
from typing import Literal

from pydantic import Field, model_validator

from .contract import ContractModel

EDIT_FRAME_RATE = 24
MIN_ISSUE_FRAMES = 4 * EDIT_FRAME_RATE
MAX_ISSUE_FRAMES = 15 * EDIT_FRAME_RATE


class RationalFrameRate(ContractModel):
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)

    @property
    def frames_per_second(self) -> float:
        return self.numerator / self.denominator


class FrameRange(ContractModel):
    start_frame: int = Field(alias="startFrame", ge=0)
    end_frame: int = Field(alias="endFrame", gt=0)

    @model_validator(mode="after")
    def require_non_empty_range(self) -> FrameRange:
        if self.end_frame <= self.start_frame:
            raise ValueError("endFrame must be greater than startFrame")
        return self

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame


class SegmentGenerationWindow(ContractModel):
    issue_range: FrameRange = Field(alias="issueRange")
    generation_range: FrameRange = Field(alias="generationRange")
    candidate_core_range: FrameRange = Field(alias="candidateCoreRange")
    provider_duration_seconds: int = Field(alias="providerDurationSeconds", ge=4, le=15)


def validate_issue_range(issue_range: FrameRange, *, total_frames: int) -> None:
    if total_frames <= 0 or issue_range.end_frame > total_frames:
        raise ValueError("issue range must be inside the video")
    if issue_range.duration_frames < MIN_ISSUE_FRAMES:
        raise ValueError("issue range must be at least 4 seconds (96 frames)")
    if issue_range.duration_frames > min(total_frames, MAX_ISSUE_FRAMES):
        raise ValueError("issue range must not exceed 15 seconds (360 frames)")


class EditVideoSegment(ContractModel):
    id: uuid.UUID
    asset_id: uuid.UUID = Field(alias="assetId")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_in_frame: int = Field(alias="sourceInFrame", ge=0)
    duration_frames: int = Field(alias="durationFrames", gt=0)
    origin: Literal["base_video", "repair_candidate"]
    repair_id: uuid.UUID | None = Field(alias="repairId", default=None)


class EditTransitionV2(ContractModel):
    after_segment_index: int = Field(alias="afterSegmentIndex", ge=0)
    type: Literal["cut", "dissolve"]
    duration_frames: Literal[0, 2, 4, 6] = Field(alias="durationFrames")

    @model_validator(mode="after")
    def require_matching_duration(self) -> EditTransitionV2:
        if self.type == "cut" and self.duration_frames != 0:
            raise ValueError("cut transitions must have zero duration")
        if self.type == "dissolve" and self.duration_frames == 0:
            raise ValueError("dissolve transitions require frame handles")
        return self


class EditAudioV2(ContractModel):
    policy: Literal["preserve_original"]
    asset_id: uuid.UUID = Field(alias="assetId")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class EditOutputV2(ContractModel):
    aspect_ratio: Literal["9:16"] = Field(alias="aspectRatio")
    width: Literal[720]
    height: Literal[1280]
    format: Literal["mp4"]


class EditDecisionListV2(ContractModel):
    format: Literal["catflow-edl-v2"]
    frame_rate: RationalFrameRate = Field(alias="frameRate")
    root_video_asset_id: uuid.UUID = Field(alias="rootVideoAssetId")
    root_video_sha256: str = Field(alias="rootVideoSha256", pattern=r"^[a-f0-9]{64}$")
    video_segments: list[EditVideoSegment] = Field(alias="videoSegments", min_length=1)
    transitions: list[EditTransitionV2] = Field(default_factory=list)
    audio: EditAudioV2
    output: EditOutputV2

    @model_validator(mode="after")
    def validate_timeline(self) -> EditDecisionListV2:
        maximum_index = len(self.video_segments) - 2
        if any(item.after_segment_index > maximum_index for item in self.transitions):
            raise ValueError("transition refers to a missing segment boundary")
        if self.audio.asset_id != self.root_video_asset_id:
            raise ValueError("the original audio must come from the root video")
        if self.audio.sha256 != self.root_video_sha256:
            raise ValueError("the original audio hash must match the root video")
        return self

    @property
    def total_frames(self) -> int:
        return sum(item.duration_frames for item in self.video_segments)


def expand_generation_window(
    issue_range: FrameRange,
    *,
    total_frames: int,
    frame_rate: RationalFrameRate,
) -> SegmentGenerationWindow:
    if total_frames <= 0 or issue_range.end_frame > total_frames:
        raise ValueError("issue range must be inside the video")

    one_second = math.ceil(frame_rate.frames_per_second)
    minimum_frames = math.ceil(4 * frame_rate.frames_per_second)
    start = max(0, issue_range.start_frame - one_second)
    end = min(total_frames, issue_range.end_frame + one_second)

    missing = max(0, minimum_frames - (end - start))
    add_left = min(start, missing // 2)
    start -= add_left
    missing -= add_left
    add_right = min(total_frames - end, missing)
    end += add_right
    missing -= add_right
    if missing:
        start -= min(start, missing)

    duration_frames = end - start
    provider_duration = math.ceil(duration_frames / frame_rate.frames_per_second)
    if provider_duration > 15:
        raise ValueError("expanded repair context exceeds the provider maximum")
    provider_duration = max(4, provider_duration)
    core_start = issue_range.start_frame - start
    return SegmentGenerationWindow(
        issueRange=issue_range,
        generationRange=FrameRange(startFrame=start, endFrame=end),
        candidateCoreRange=FrameRange(
            startFrame=core_start,
            endFrame=core_start + issue_range.duration_frames,
        ),
        providerDurationSeconds=provider_duration,
    )


def build_base_timeline(
    *, asset_id: uuid.UUID, sha256: str, total_frames: int
) -> EditDecisionListV2:
    if total_frames <= 0:
        raise ValueError("base video must have at least one frame")
    return EditDecisionListV2(
        format="catflow-edl-v2",
        frameRate={"numerator": 24, "denominator": 1},
        rootVideoAssetId=asset_id,
        rootVideoSha256=sha256,
        videoSegments=[
            EditVideoSegment(
                id=uuid.uuid5(uuid.NAMESPACE_URL, f"catflow-base-video:{asset_id}:{sha256}"),
                assetId=asset_id,
                sha256=sha256,
                sourceInFrame=0,
                durationFrames=total_frames,
                origin="base_video",
            )
        ],
        transitions=[],
        audio={"policy": "preserve_original", "assetId": asset_id, "sha256": sha256},
        output={"aspectRatio": "9:16", "width": 720, "height": 1280, "format": "mp4"},
    )


def splice_repair_candidate(
    timeline: EditDecisionListV2,
    *,
    issue_range: FrameRange,
    candidate_asset_id: uuid.UUID,
    candidate_sha256: str,
    candidate_source_range: FrameRange,
    repair_id: uuid.UUID,
    transition: EditTransitionV2,
) -> EditDecisionListV2:
    if issue_range.end_frame > timeline.total_frames:
        raise ValueError("repair range must be inside the timeline")
    if candidate_source_range.duration_frames != issue_range.duration_frames:
        raise ValueError("candidate source range must preserve the repair duration")

    previous_boundary_frames: dict[int, EditTransitionV2] = {}
    previous_cursor = 0
    previous_transitions = {item.after_segment_index: item for item in timeline.transitions}
    for index, segment in enumerate(timeline.video_segments[:-1]):
        previous_cursor += segment.duration_frames
        if index in previous_transitions:
            previous_boundary_frames[previous_cursor] = previous_transitions[index]

    segments: list[EditVideoSegment] = []
    cursor = 0
    inserted = False
    for segment in timeline.video_segments:
        segment_start = cursor
        segment_end = cursor + segment.duration_frames
        cursor = segment_end
        if segment_end <= issue_range.start_frame or segment_start >= issue_range.end_frame:
            segments.append(segment)
            continue
        if segment_start < issue_range.start_frame:
            segments.append(
                segment.model_copy(
                    update={
                        "id": uuid.uuid4(),
                        "duration_frames": issue_range.start_frame - segment_start,
                    }
                )
            )
        if not inserted:
            segments.append(
                EditVideoSegment(
                    id=uuid.uuid4(),
                    assetId=candidate_asset_id,
                    sha256=candidate_sha256,
                    sourceInFrame=candidate_source_range.start_frame,
                    durationFrames=candidate_source_range.duration_frames,
                    origin="repair_candidate",
                    repairId=repair_id,
                )
            )
            inserted = True
        if segment_end > issue_range.end_frame:
            consumed = issue_range.end_frame - segment_start
            segments.append(
                segment.model_copy(
                    update={
                        "id": uuid.uuid4(),
                        "source_in_frame": segment.source_in_frame + consumed,
                        "duration_frames": segment_end - issue_range.end_frame,
                    }
                )
            )

    if not inserted:
        raise ValueError("repair range did not intersect the timeline")
    candidate_index = next(
        index for index, item in enumerate(segments) if item.repair_id == repair_id
    )
    new_boundary_indexes: dict[int, int] = {}
    new_cursor = 0
    for index, segment in enumerate(segments[:-1]):
        new_cursor += segment.duration_frames
        new_boundary_indexes[new_cursor] = index

    transitions_by_index: dict[int, EditTransitionV2] = {}
    for boundary_frame, previous_transition in previous_boundary_frames.items():
        boundary_index = new_boundary_indexes.get(boundary_frame)
        if boundary_index is not None and boundary_frame not in {
            issue_range.start_frame,
            issue_range.end_frame,
        }:
            transitions_by_index[boundary_index] = previous_transition.model_copy(
                update={"after_segment_index": boundary_index}
            )
    if candidate_index > 0:
        transitions_by_index[candidate_index - 1] = transition.model_copy(
            update={"after_segment_index": candidate_index - 1}
        )
    if candidate_index < len(segments) - 1:
        transitions_by_index[candidate_index] = transition.model_copy(
            update={"after_segment_index": candidate_index}
        )

    repaired = timeline.model_copy(
        update={
            "video_segments": segments,
            "transitions": [transitions_by_index[index] for index in sorted(transitions_by_index)],
        }
    )
    if repaired.total_frames != timeline.total_frames:
        raise ValueError("repair changed the timeline duration")
    return repaired
