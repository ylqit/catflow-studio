from __future__ import annotations

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.aigc_canvas import parse_llm_storyboard_output
from cat_video_generator.interfaces.api_v2 import (
    ManualStoryboardDraftRequest,
    StoryboardPromptCompilationRequest,
)


def test_minimal_storyboard_output_ignores_extra_model_fields() -> None:
    parsed = parse_llm_storyboard_output(
        {
            "sourceStoryRevisionId": "00000000-0000-0000-0000-000000000001",
            "shots": [
                {
                    "order": 1,
                    "title": "看见雨云",
                    "direction": "孩子抬头看见雨云，猫咪停在画纸旁。",
                    "durationSeconds": 8,
                    "sceneLabel": "阳台",
                    "providerComment": "这个额外字段不影响可用正文",
                }
            ],
            "modelComment": "extra",
        }
    )

    assert parsed.status == "ready"
    assert parsed.plan is not None
    assert parsed.plan.shots[0].direction == "孩子抬头看见雨云，猫咪停在画纸旁。"
    assert parsed.plan.shots[0].camera == ""


def test_legacy_beats_are_normalized_at_the_llm_boundary() -> None:
    parsed = parse_llm_storyboard_output(
        {
            "beats": [
                {
                    "sceneOrder": 1,
                    "title": "收画",
                    "action": "孩子收起画纸，猫咪自然跟随。",
                    "durationWeight": 2,
                }
            ]
        }
    )

    assert parsed.status == "ready"
    assert parsed.plan is not None
    shot = parsed.plan.shots[0]
    assert shot.order == 1
    assert shot.direction == "孩子收起画纸，猫咪自然跟随。"
    assert shot.duration_seconds is None
    assert shot.duration_weight == 2


def test_nested_scene_storyboard_envelope_is_flattened_at_the_llm_boundary() -> None:
    parsed = parse_llm_storyboard_output(
        {
            "aspectRatio": "9:16",
            "globalNotes": ["无对白，单场景连续推进"],
            "scenes": [
                {
                    "sceneLabel": "清晨木窗边",
                    "sceneOrder": 1,
                    "shots": [
                        {
                            "order": 1,
                            "title": "纸星星落下",
                            "direction": "纸星星被风吹落到窗台。",
                            "durationSeconds": 2,
                            "soundCue": "轻微风声",
                        },
                        {
                            "order": 2,
                            "title": "猫咪推回星星",
                            "direction": "猫咪保持四足姿态，用鼻尖推回纸星星。",
                            "durationSeconds": 3,
                        },
                    ],
                },
                {
                    "sceneLabel": "同一窗边的收尾构图",
                    "sceneOrder": 2,
                    "shots": [
                        {
                            "order": 1,
                            "title": "并肩迎光",
                            "direction": "孩子贴稳星星，与猫咪并肩迎接晨光。",
                            "durationSeconds": 3,
                        }
                    ],
                },
            ],
        }
    )

    assert parsed.status == "ready"
    assert parsed.plan is not None
    assert [shot.order for shot in parsed.plan.shots] == [1, 2, 3]
    assert [shot.scene_order for shot in parsed.plan.shots] == [1, 1, 2]
    assert [shot.scene_label for shot in parsed.plan.shots] == [
        "清晨木窗边",
        "清晨木窗边",
        "同一窗边的收尾构图",
    ]
    assert parsed.plan.shots[0].sound_effect == "轻微风声"


def test_non_json_storyboard_text_is_preserved_for_manual_structuring() -> None:
    raw = "镜头一：孩子发现雨云。\n镜头二：孩子和猫一起把画纸收好。"

    parsed = parse_llm_storyboard_output(raw)

    assert parsed.status == "needs_structuring"
    assert parsed.plan is None
    assert parsed.raw_text == raw
    assert [item.model_dump(by_alias=True) for item in parsed.diagnostics] == [
        {
            "code": "storyboard_needs_structuring",
            "severity": "blocker",
            "message": "分镜原文需要整理为至少一个包含标题、镜头描述和有效时长的镜头",
            "targetId": None,
        }
    ]


@pytest.mark.parametrize(
    "shots",
    [
        [
            {
                "order": 2,
                "title": "错误顺序",
                "direction": "存在描述",
                "durationSeconds": 8,
            }
        ],
        [
            {
                "order": 1,
                "title": "空描述",
                "direction": "   ",
                "durationSeconds": 8,
            }
        ],
        [
            {
                "order": 1,
                "title": "无效时长",
                "direction": "存在描述",
                "durationSeconds": 0,
            }
        ],
    ],
)
def test_invalid_executable_storyboard_becomes_needs_structuring(
    shots: list[dict[str, object]],
) -> None:
    parsed = parse_llm_storyboard_output({"shots": shots})

    assert parsed.status == "needs_structuring"
    assert parsed.plan is None
    assert parsed.raw_text
    assert parsed.diagnostics[0].code == "storyboard_needs_structuring"


def test_manual_storyboard_accepts_direction_only_and_dialogue_as_warning() -> None:
    payload = ManualStoryboardDraftRequest.model_validate(
        {
            "healingRecipe": True,
            "shots": [
                {
                    "order": 1,
                    "title": "收画",
                    "direction": "孩子将画纸收进文件夹。",
                    "durationSeconds": 10,
                    "dialogue": "要下雨了。",
                }
            ],
        }
    )

    assert payload.shots[0].direction == "孩子将画纸收进文件夹。"
    assert payload.shots[0].action == "孩子将画纸收进文件夹。"
    assert payload.diagnostics[0].code == "storyboard_dialogue_present"
    assert payload.diagnostics[0].severity == "warning"


def test_manual_storyboard_direction_wins_when_legacy_action_is_also_present() -> None:
    payload = ManualStoryboardDraftRequest.model_validate(
        {
            "shots": [
                {
                    "order": 1,
                    "title": "收画",
                    "direction": "新的完整导演描述。",
                    "action": "旧动作字段。",
                    "durationSeconds": 10,
                }
            ]
        }
    )

    assert payload.shots[0].direction == "新的完整导演描述。"
    assert payload.shots[0].action == "新的完整导演描述。"


def test_prompt_compilation_accepts_direction_without_advanced_fields_and_warns_dialogue() -> None:
    payload = StoryboardPromptCompilationRequest.model_validate(
        {
            "storyRevisionId": "00000000-0000-0000-0000-000000000001",
            "visualProfileRevisionId": "00000000-0000-0000-0000-000000000002",
            "healingRecipe": False,
            "shots": [
                {
                    "order": 1,
                    "sceneId": "00000000-0000-0000-0000-000000000003",
                    "title": "收画",
                    "direction": "孩子收起画纸。",
                    "durationSeconds": 10,
                    "dialogue": "收好了。",
                }
            ],
        }
    )

    assert payload.shots[0].action == "孩子收起画纸。"
    assert payload.diagnostics[0].code == "storyboard_dialogue_present"


def test_prompt_compilation_still_rejects_non_contiguous_orders() -> None:
    with pytest.raises(ValidationError, match="镜头顺序必须从1开始且连续"):
        StoryboardPromptCompilationRequest.model_validate(
            {
                "storyRevisionId": "00000000-0000-0000-0000-000000000001",
                "visualProfileRevisionId": "00000000-0000-0000-0000-000000000002",
                "shots": [
                    {
                        "order": 2,
                        "sceneId": "00000000-0000-0000-0000-000000000003",
                        "title": "错误顺序",
                        "direction": "存在描述。",
                        "durationSeconds": 10,
                    }
                ],
            }
        )
