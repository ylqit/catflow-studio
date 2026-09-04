from __future__ import annotations

from copy import deepcopy

from catflow.domain.models import BlockingDesign, ShotSoundDesign


def _director_payload() -> dict[str, object]:
    return {
        "targetDurationSeconds": 12,
        "directorTreatment": {
            "logline": "孩子给窗台花盆浇水，猫咪主动避开最后一滴水。",
            "theme": "照料",
            "emotionalTone": ["平静"],
            "visualMotif": "土壤颜色逐渐变深",
            "spatialSetting": "室内窗台",
            "emotionalArc": {
                "opening": "发现盆土干燥",
                "development": "平缓浇水",
                "resolution": "水壶和托盘归位",
            },
            "microEvent": {
                "trigger": "盆土干燥",
                "childIntent": "给花盆补水",
                "childAction": "孩子平缓浇水后放回水壶",
                "catResponse": "猫咪挪步避开水滴",
                "visibleCauseAndEffect": "土壤颜色变深",
                "warmEnding": "孩子推正托盘，猫咪绕花盆迈一步",
            },
            "propStateChange": {
                "initialState": "盆土干燥、托盘偏离",
                "changedState": "盆土湿润、托盘归位",
            },
            "soundIntent": "水流、托盘和猫爪的自然声音",
            "endingImage": "孩子推正托盘，猫咪继续迈步",
            "feasibilityWarnings": [],
        },
        "shots": [
            {
                "id": "shot-1",
                "order": 1,
                "durationSeconds": 12,
                "durationFrames": 288,
                "framing": "中景",
                "cameraMovement": "固定观察",
                "childAction": "孩子平缓浇水后放回水壶并推正托盘",
                "catAction": "猫咪挪步避开水滴后绕花盆迈一步",
                "environmentChange": "盆土颜色变深，托盘回到花盆正下方",
                "transition": "continuous",
                "lens": {
                    "focalLengthEquivalent": "35mm",
                    "cameraHeight": "窗台高度",
                    "cameraAngle": "轻微俯拍",
                    "perspectiveIntent": "同时看清盆土、孩子双手和猫咪",
                },
                "composition": {
                    "subjectPlacement": "花盆居中，孩子和猫咪分列两侧",
                    "foreground": "窗台边缘",
                    "middleGround": "花盆、孩子双手和猫咪",
                    "background": "柔和窗光",
                    "screenDirection": "从左向右",
                    "eyeLine": "孩子看向盆土，猫咪看向水滴",
                },
                "childBlocking": {
                    "initialState": "孩子站在窗台左侧并握住水壶",
                    "movementPath": "将水壶移到花盆上方，浇水后放回并推托盘",
                    "endState": "孩子收回手指",
                    "microMotions": ["握稳水壶", "调整壶嘴", "收回手指", "轻推托盘"],
                },
                "catBlocking": {
                    "initialState": "猫咪蹲在花盆右侧",
                    "movementPath": "向右挪步避开水滴，再沿花盆迈一步",
                    "endState": "猫咪保持向前迈步",
                    "microMotions": ["耳朵转向", "尾巴轻摆"],
                },
                "physicalChange": {
                    "subject": "盆土和托盘",
                    "before": "盆土干燥且托盘偏离",
                    "after": "盆土湿润且托盘归位",
                },
                "continuity": {
                    "incoming": "承接孩子靠近窗台",
                    "outgoing": "猫咪继续沿花盆迈步",
                    "sharedVisualElement": "同一花盆、水壶和托盘",
                    "finalFrame": "孩子推正托盘，猫咪抬爪继续迈步",
                },
                "lighting": {
                    "direction": "窗外斜向室内",
                    "softness": "柔和漫射",
                    "colorIntent": "自然暖灰色",
                },
                "sound": {
                    "ambience": ["安静室内环境声"],
                    "objectEffects": [
                        "平缓水流声",
                        "水壶轻碰台面声",
                        "水滴落入托盘声",
                        "指尖推动托盘的摩擦声",
                    ],
                    "movementEffects": ["猫爪轻落窗台声"],
                    "musicIntent": "无配乐",
                },
                "directorIntent": "在单镜头中完成浇水和归位的因果闭合",
                "generationRisks": [],
                "blocking_note": "内嵌角色调度符合要求",
            }
        ],
    }


def test_creative_detail_lists_are_not_hard_schema_limits() -> None:
    sound = ShotSoundDesign(
        ambience=["一", "二", "三", "四"],
        objectEffects=["一", "二", "三", "四"],
        movementEffects=["一", "二", "三", "四"],
        musicIntent="无配乐",
    )
    blocking = BlockingDesign(
        initialState="开始",
        movementPath="移动",
        endState="结束",
        microMotions=["一", "二", "三", "四"],
    )

    assert len(sound.object_effects) == 4
    assert len(blocking.micro_motions) == 4


def test_normalizer_keeps_four_sound_effects_and_ignores_unknown_provider_field() -> None:
    from catflow.domain.director_results import normalize_director_result

    result = normalize_director_result(_director_payload())

    assert result.disposition == "candidate_ready"
    assert result.plan is not None
    assert result.plan.shots[0].sound is not None
    assert result.plan.shots[0].sound.object_effects == [
        "平缓水流声",
        "水壶轻碰台面声",
        "水滴落入托盘声",
        "指尖推动托盘的摩擦声",
    ]
    assert "blocking_note" not in result.normalized_payload["shots"][0]
    assert {issue.code for issue in result.issues} == {
        "sound_detail_dense",
        "unknown_provider_field",
        "micro_motion_dense",
    }
    assert all(issue.severity == "warning" for issue in result.issues)
    extra_issue = next(
        issue for issue in result.issues if issue.code == "unknown_provider_field"
    )
    assert extra_issue.provider_value == "内嵌角色调度符合要求"


def test_normalizer_preserves_recoverable_payload_when_required_content_is_missing() -> None:
    from catflow.domain.director_results import normalize_director_result

    payload = _director_payload()
    del payload["shots"][0]["catBlocking"]  # type: ignore[index]

    result = normalize_director_result(payload)

    assert result.disposition == "needs_input"
    assert result.plan is None
    assert result.normalized_payload["shots"]
    assert any(
        issue.severity == "blocking" and issue.path == "shots.0.catBlocking"
        for issue in result.issues
    )


def test_normalizer_removes_only_empty_zero_duration_placeholders() -> None:
    from catflow.domain.director_results import normalize_director_result

    payload = _director_payload()
    payload["shots"].append(  # type: ignore[union-attr]
        {
            "id": "S2Fix",
            "order": 2,
            "durationSeconds": 0,
            "durationFrames": 0,
            "framing": "",
            "cameraMovement": "",
            "childAction": "",
            "catAction": "",
            "environmentChange": "",
            "transition": "continuous",
        }
    )

    result = normalize_director_result(payload)

    assert result.disposition == "candidate_ready"
    assert len(result.normalized_payload["shots"]) == 1
    assert any(issue.code == "empty_placeholder_ignored" for issue in result.issues)


def test_normalizer_does_not_discard_meaningful_fifth_shot() -> None:
    from catflow.domain.director_results import normalize_director_result

    payload = _director_payload()
    base_shot = payload["shots"][0]  # type: ignore[index]
    shots = []
    for order in range(1, 6):
        shot = deepcopy(base_shot)
        shot["id"] = f"shot-{order}"
        shot["order"] = order
        shot["durationSeconds"] = 2
        shot["durationFrames"] = 48
        shots.append(shot)
    payload["shots"] = shots

    result = normalize_director_result(payload)

    assert result.disposition == "needs_input"
    assert len(result.normalized_payload["shots"]) == 5
    assert any(issue.code == "too_many_meaningful_shots" for issue in result.issues)


def test_normalizer_rejects_payload_without_a_shot_array() -> None:
    from catflow.domain.director_results import normalize_director_result

    result = normalize_director_result({"targetDurationSeconds": 12})

    assert result.disposition == "invalid"
    assert result.plan is None
    assert any(issue.severity == "fatal" for issue in result.issues)
