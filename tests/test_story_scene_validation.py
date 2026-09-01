from __future__ import annotations

import pytest

from cat_video_generator.domain.aigc_canvas import (
    StoryCandidateOutput,
    validate_story_scene_plan,
)


def _candidate(
    scene_count: int,
    *,
    transition_reason: str = "推进下一段行动",
) -> StoryCandidateOutput:
    return StoryCandidateOutput.model_validate(
        {
            "title": "雨后亮叶",
            "logline": "孩子和猫发现一片被雨珠点亮的叶子。",
            "synopsis": "两者在安静的日常中完成一次温暖发现。",
            "scenes": [
                {
                    "sceneKey": f"scene-{index:02d}",
                    "title": f"场景 {index}",
                    "purpose": "建立发现" if index == 1 else "延续行动",
                    "synopsis": "孩子与猫共同观察环境中的小变化。",
                    "durationWeight": 1,
                    "continuity": {
                        "location": "雨后小院" if index == 1 else "屋檐下",
                        "environment": "outdoor",
                        "timeWeather": "雨后午后",
                        "decorations": ["湿润绿植"],
                        "props": ["发亮叶片"],
                        "transitionReason": "" if index == 1 else transition_reason,
                    },
                }
                for index in range(1, scene_count + 1)
            ],
        }
    )


def test_short_story_rejects_more_than_one_scene() -> None:
    with pytest.raises(ValueError, match="最多允许1个场景"):
        validate_story_scene_plan(_candidate(2), target_duration_seconds=15)


def test_longer_story_allows_story_driven_scene_count_within_shot_limit() -> None:
    validate_story_scene_plan(_candidate(2), target_duration_seconds=30)


def test_scene_change_requires_narrative_reason() -> None:
    with pytest.raises(ValueError, match="换场必须填写叙事目的"):
        validate_story_scene_plan(
            _candidate(2, transition_reason=""),
            target_duration_seconds=30,
        )
