from __future__ import annotations

import pytest

from catflow.domain.validation import (
    ValidationCallKind,
    ValidationLimitError,
    first_release_manifest,
    reserve_validation_call,
)


def test_first_release_manifest_freezes_three_topics_and_ten_paid_calls() -> None:
    manifest = first_release_manifest()

    assert manifest.topics == ("雨天擦爪", "浇花", "寻找滚落线团")
    assert manifest.duration_seconds == 12
    assert manifest.resolution == "480p"
    assert manifest.aspect_ratio == "9:16"
    assert manifest.target_budget_cny == 50
    assert manifest.call_limits == {
        ValidationCallKind.PLAN_STORY: 3,
        ValidationCallKind.GENERATE_IMAGE: 1,
        ValidationCallKind.DIAGNOSE_IMAGE: 1,
        ValidationCallKind.GENERATE_VIDEO: 3,
        ValidationCallKind.DIAGNOSE_VIDEO: 1,
        ValidationCallKind.REGENERATE_VIDEO_SEGMENT: 1,
    }
    assert manifest.total_call_limit == 10
    assert manifest.repair_topic == "雨天擦爪"
    assert (manifest.repair_start_frame, manifest.repair_end_frame) == (96, 192)
    assert "逐只擦干猫爪" in manifest.repair_prompt


def test_validation_call_reservation_refuses_the_eleventh_call_and_second_repair() -> None:
    manifest = first_release_manifest()
    usage = dict.fromkeys(manifest.call_limits, 0)

    for kind, limit in manifest.call_limits.items():
        for _ in range(limit):
            usage = reserve_validation_call(manifest, usage, kind)

    with pytest.raises(ValidationLimitError, match="total paid-call limit"):
        reserve_validation_call(manifest, usage, ValidationCallKind.PLAN_STORY)

    video_usage = dict.fromkeys(manifest.call_limits, 0)
    for _ in range(3):
        video_usage = reserve_validation_call(
            manifest, video_usage, ValidationCallKind.GENERATE_VIDEO
        )
    with pytest.raises(ValidationLimitError, match="generate_video limit"):
        reserve_validation_call(manifest, video_usage, ValidationCallKind.GENERATE_VIDEO)

    repair_usage = dict.fromkeys(manifest.call_limits, 0)
    repair_usage = reserve_validation_call(
        manifest, repair_usage, ValidationCallKind.REGENERATE_VIDEO_SEGMENT
    )
    with pytest.raises(ValidationLimitError, match="regenerate_video_segment limit"):
        reserve_validation_call(manifest, repair_usage, ValidationCallKind.REGENERATE_VIDEO_SEGMENT)
