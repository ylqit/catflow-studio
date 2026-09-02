from __future__ import annotations

import uuid

from catflow.domain.video_repairs import (
    EditDecisionListV2,
    EditTransitionV2,
    EditVideoSegment,
    FrameRange,
    RationalFrameRate,
    build_base_timeline,
    expand_generation_window,
    splice_repair_candidate,
)


def test_one_second_issue_expands_evenly_to_seedance_four_second_minimum() -> None:
    window = expand_generation_window(
        FrameRange(startFrame=120, endFrame=144),
        total_frames=288,
        frame_rate=RationalFrameRate(numerator=24, denominator=1),
    )

    assert window.issue_range == FrameRange(startFrame=120, endFrame=144)
    assert window.generation_range == FrameRange(startFrame=84, endFrame=180)
    assert window.provider_duration_seconds == 4
    assert window.candidate_core_range == FrameRange(startFrame=36, endFrame=60)


def test_issue_at_video_start_moves_missing_left_context_to_the_right() -> None:
    window = expand_generation_window(
        FrameRange(startFrame=0, endFrame=24),
        total_frames=288,
        frame_rate=RationalFrameRate(numerator=24, denominator=1),
    )

    assert window.generation_range == FrameRange(startFrame=0, endFrame=96)
    assert window.candidate_core_range == FrameRange(startFrame=0, endFrame=24)
    assert window.provider_duration_seconds == 4


def test_repair_splice_preserves_total_frames_and_root_audio() -> None:
    base_asset_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    repair_asset_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    repair_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    timeline = build_base_timeline(
        asset_id=base_asset_id,
        sha256="a" * 64,
        total_frames=288,
    )

    repaired = splice_repair_candidate(
        timeline,
        issue_range=FrameRange(startFrame=96, endFrame=192),
        candidate_asset_id=repair_asset_id,
        candidate_sha256="b" * 64,
        candidate_source_range=FrameRange(startFrame=24, endFrame=120),
        repair_id=repair_id,
        transition=EditTransitionV2(afterSegmentIndex=0, type="cut", durationFrames=0),
    )

    assert [segment.duration_frames for segment in repaired.video_segments] == [96, 96, 96]
    assert [segment.source_in_frame for segment in repaired.video_segments] == [0, 24, 192]
    assert [segment.origin for segment in repaired.video_segments] == [
        "base_video",
        "repair_candidate",
        "base_video",
    ]
    assert sum(segment.duration_frames for segment in repaired.video_segments) == 288
    assert repaired.audio.asset_id == base_asset_id
    assert repaired.audio.sha256 == "a" * 64
    assert [
        (item.after_segment_index, item.type, item.duration_frames) for item in repaired.transitions
    ] == [
        (0, "cut", 0),
        (1, "cut", 0),
    ]


def test_second_repair_can_replace_frames_across_an_existing_repair() -> None:
    root_id = uuid.UUID("00000000-0000-0000-0000-000000000011")
    first_id = uuid.UUID("00000000-0000-0000-0000-000000000012")
    second_id = uuid.UUID("00000000-0000-0000-0000-000000000013")
    timeline = EditDecisionListV2(
        format="catflow-edl-v2",
        frameRate={"numerator": 24, "denominator": 1},
        rootVideoAssetId=root_id,
        rootVideoSha256="1" * 64,
        videoSegments=[
            EditVideoSegment(
                id=uuid.uuid4(),
                assetId=root_id,
                sha256="1" * 64,
                sourceInFrame=0,
                durationFrames=96,
                origin="base_video",
            ),
            EditVideoSegment(
                id=uuid.uuid4(),
                assetId=first_id,
                sha256="2" * 64,
                sourceInFrame=24,
                durationFrames=96,
                origin="repair_candidate",
                repairId=uuid.uuid4(),
            ),
            EditVideoSegment(
                id=uuid.uuid4(),
                assetId=root_id,
                sha256="1" * 64,
                sourceInFrame=192,
                durationFrames=96,
                origin="base_video",
            ),
        ],
        transitions=[],
        audio={"policy": "preserve_original", "assetId": root_id, "sha256": "1" * 64},
        output={"aspectRatio": "9:16", "width": 720, "height": 1280, "format": "mp4"},
    )

    repaired = splice_repair_candidate(
        timeline,
        issue_range=FrameRange(startFrame=72, endFrame=216),
        candidate_asset_id=second_id,
        candidate_sha256="3" * 64,
        candidate_source_range=FrameRange(startFrame=12, endFrame=156),
        repair_id=uuid.uuid4(),
        transition=EditTransitionV2(afterSegmentIndex=0, type="dissolve", durationFrames=4),
    )

    assert [segment.duration_frames for segment in repaired.video_segments] == [72, 144, 72]
    assert [segment.source_in_frame for segment in repaired.video_segments] == [0, 12, 216]
    assert repaired.video_segments[1].asset_id == second_id
    assert sum(segment.duration_frames for segment in repaired.video_segments) == 288
    assert [item.duration_frames for item in repaired.transitions] == [4, 4]


def test_disjoint_repair_preserves_existing_explicit_transitions() -> None:
    root_asset_id = uuid.uuid4()
    first = splice_repair_candidate(
        build_base_timeline(asset_id=root_asset_id, sha256="1" * 64, total_frames=288),
        issue_range=FrameRange(startFrame=96, endFrame=144),
        candidate_asset_id=uuid.uuid4(),
        candidate_sha256="2" * 64,
        candidate_source_range=FrameRange(startFrame=24, endFrame=72),
        repair_id=uuid.uuid4(),
        transition=EditTransitionV2(afterSegmentIndex=0, type="dissolve", durationFrames=4),
    )

    second = splice_repair_candidate(
        first,
        issue_range=FrameRange(startFrame=216, endFrame=240),
        candidate_asset_id=uuid.uuid4(),
        candidate_sha256="3" * 64,
        candidate_source_range=FrameRange(startFrame=24, endFrame=48),
        repair_id=uuid.uuid4(),
        transition=EditTransitionV2(afterSegmentIndex=0, type="cut", durationFrames=0),
    )

    boundaries = {
        item.after_segment_index: (item.type, item.duration_frames) for item in second.transitions
    }
    assert boundaries == {
        0: ("dissolve", 4),
        1: ("dissolve", 4),
        2: ("cut", 0),
        3: ("cut", 0),
    }
    assert second.total_frames == 288
