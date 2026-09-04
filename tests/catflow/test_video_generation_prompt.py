from __future__ import annotations

from datetime import UTC, datetime

from catflow.application.service import GenerationInputSnapshotDto
from catflow.application.video_generation import compile_video_generation_prompt
from catflow.domain.models import ShotSpec


def _legacy_shot(**updates: object) -> ShotSpec:
    values: dict[str, object] = {
        "id": "shot-1",
        "order": 1,
        "durationSeconds": 12,
        "durationFrames": 288,
        "framing": "中景。",
        "cameraMovement": "固定观察；",
        "childAction": "孩子整理版本 v4.0 的野餐清单。",
        "catAction": "猫咪看向写着“出发！”的卡片……",
        "environmentChange": "计时从 0.5 秒推进到 12.0 秒，参考 https://example.com/a?b=1。",
        "transition": "continuous",
        "generationRisks": [
            {"code": "limb", "message": "额外肢体。"},
            {"code": "limb_repeat", "message": "额外肢体；"},
        ],
    }
    values.update(updates)
    return ShotSpec.model_validate(values)


def test_prompt_boundary_normalization_preserves_internal_punctuation_and_is_stable() -> None:
    shot = _legacy_shot()

    first = compile_video_generation_prompt(
        project_title="森林野餐",
        target_duration_seconds=12,
        shots=[shot],
        director_treatment=None,
    )
    second = compile_video_generation_prompt(
        project_title="森林野餐",
        target_duration_seconds=12,
        shots=[shot],
        director_treatment=None,
    )

    assert first == second
    for invalid_boundary in ("。；", "。，", "。。", "；。"):
        assert invalid_boundary not in first.prompt
    assert "v4.0" in first.prompt
    assert "0.5" in first.prompt
    assert "12.0" in first.prompt
    assert "https://example.com/a?b=1" in first.prompt
    assert "“出发！”" in first.prompt
    assert "……" in first.prompt
    assert "……。" not in first.prompt


def test_legacy_shot_summaries_remain_the_prompt_fallback() -> None:
    shot = _legacy_shot(
        childAction="孩子把三明治放进篮子。",
        catAction="猫咪安静观察。",
        environmentChange="空篮子逐渐装满。",
        generationRisks=[],
    )

    compiled = compile_video_generation_prompt(
        project_title="准备野餐",
        target_duration_seconds=12,
        shots=[shot],
        director_treatment=None,
    )

    assert "人物动作：孩子把三明治放进篮子。" in compiled.prompt
    assert "猫咪动作：猫咪安静观察。" in compiled.prompt
    assert "画面变化：空篮子逐渐装满。" in compiled.prompt


def test_fixed_and_shot_specific_negative_risks_are_deduplicated_by_meaning() -> None:
    compiled = compile_video_generation_prompt(
        project_title="准备野餐",
        target_duration_seconds=12,
        shots=[_legacy_shot()],
        director_treatment=None,
    )

    assert compiled.negative_prompt.count("额外肢体") == 1
    assert "limb" not in compiled.prompt


def test_legacy_generation_snapshot_remains_readable_without_display_sections() -> None:
    snapshot = GenerationInputSnapshotDto.model_validate(
        {
            "schemaVersion": 1,
            "kind": "whole_video",
            "state": "submitted",
            "provider": "ark",
            "model": "historical-video-model",
            "capabilityRevision": "historical-capability",
            "inputHash": "a" * 64,
            "prompt": "当时实际提交的完整生成指令",
            "negativePrompt": "当时实际提交的需要避免的问题",
            "references": [],
            "video": {
                "durationSeconds": 12,
                "resolution": "480p",
                "aspectRatio": "9:16",
                "frameRate": 24,
            },
            "source": {},
            "promptCompilerRevision": "seedance-professional-v3",
            "createdAt": datetime.now(UTC),
        }
    )

    assert snapshot.schema_version == 1
    assert snapshot.prompt_summary is None
    assert snapshot.prompt_sections == []
    assert snapshot.prompt == "当时实际提交的完整生成指令"
