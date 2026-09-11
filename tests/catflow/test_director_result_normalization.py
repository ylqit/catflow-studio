from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

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
                "cameraSpatialRelation": "摄影机在室内，孩子和猫位于窗台内侧，双手与肩部连接可见",
                "interactionConstraints": ["孩子双手握住同一水壶，水从壶嘴落到盆土接触处"],
                "visualExclusions": ["独立于孩子身体的手臂"],
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
        "ending_review",
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


def test_professional_output_schema_requires_the_same_fields_as_validation() -> None:
    from catflow.domain.director_results import director_provider_output_schema
    from catflow.domain.models import ProfessionalDirectorOutput, ShotSpec

    schema = director_provider_output_schema()["$defs"]["ProfessionalShotOutput"]
    for key in ("childBlocking", "catBlocking", "durationFrames", "lens", "composition",
                "physicalChange", "continuity", "lighting", "sound", "directorIntent",
                "cameraSpatialRelation", "interactionConstraints", "visualExclusions"):
        assert key in schema["required"]
        assert "default" not in schema["properties"][key]
        assert "anyOf" not in schema["properties"][key]
        payload = _director_payload()
        payload["shots"][0].pop("blocking_note")
        payload["shots"][0][key] = None
        with pytest.raises(ValueError):
            ProfessionalDirectorOutput.model_validate(payload)
    assert ShotSpec.model_fields["cat_blocking"].default is None


@pytest.mark.parametrize("canonical", ["missing", "null", "equal", "different", "wrong_type"])
def test_only_unambiguous_blocking_alias_is_recovered(canonical: str) -> None:
    from catflow.domain.director_results import normalize_director_result

    payload = _director_payload()
    shot = payload["shots"][0]
    original = deepcopy(shot["catBlocking"])
    shot["blocking"] = {"catBlocking": deepcopy(original)}
    if canonical == "missing":
        del shot["catBlocking"]
    elif canonical == "null":
        shot["catBlocking"] = None
    elif canonical == "different":
        shot["catBlocking"]["endState"] = "不同的结束状态"
    elif canonical == "wrong_type":
        shot["catBlocking"] = "不是对象"
    before = deepcopy(payload)
    result = normalize_director_result(payload)
    assert payload == before == result.raw_payload
    if canonical in {"different", "wrong_type"}:
        assert result.disposition == "needs_input"
        assert result.plan is None
        conflict = next(issue for issue in result.issues if issue.code == "blocking_path_conflict")
        assert conflict.provider_value["nested"] == original
    else:
        assert result.disposition == "candidate_ready"
        assert result.normalized_payload["shots"][0]["catBlocking"] == original
        assert result.validation_document()["adjustments"]
        second = normalize_director_result(result.normalized_payload)
        assert second.normalized_payload == result.normalized_payload


def test_real_episode_two_receipt_recovers_without_changing_any_action() -> None:
    from catflow.domain.director_results import normalize_director_result

    fixture = Path(__file__).parent / "fixtures" / "director-episode-two.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    before = deepcopy(payload)
    legacy = normalize_director_result(payload, legacy=True)
    assert legacy.disposition == "needs_input"
    result = normalize_director_result(payload, output_contract_revision="professional-director-v2")
    assert payload == before
    assert result.disposition == "candidate_ready"
    assert [shot.duration_seconds for shot in result.plan.shots] == [3, 4, 5, 3]
    for source, target in zip(payload["shots"], result.normalized_payload["shots"], strict=True):
        expected = deepcopy(source)
        expected.update(expected.pop("blocking"))
        assert target == expected
    assert len(result.validation_document()["adjustments"]) == 8
    assert all(issue.severity == "warning" for issue in result.issues)


def test_missing_nested_detail_and_wrong_duration_remain_blocking() -> None:
    from catflow.domain.director_results import normalize_director_result

    payload = _director_payload()
    del payload["shots"][0]["catBlocking"]["endState"]
    result = normalize_director_result(payload)
    assert result.disposition == "needs_input"
    assert any(issue.path == "shots.0.catBlocking.endState" for issue in result.issues)
    payload = _director_payload()
    payload["targetDurationSeconds"] = 15
    assert normalize_director_result(payload).disposition == "needs_input"


@pytest.mark.parametrize("final_frame", [
    "猫耳轻颤，胡须舒展，水面泛起涟漪",
    "不做停帧，不让角色原地互看，猫耳轻颤",
    "孩子和猫咪原地互看，画面静止",
    "孩子推正托盘，猫咪继续迈步",
])
def test_ending_uncertainty_is_advice_and_preserves_provider_text(final_frame: str) -> None:
    from catflow.domain.director_results import normalize_director_result

    payload = _director_payload()
    payload["shots"][-1]["continuity"]["finalFrame"] = final_frame
    before = deepcopy(payload)
    result = normalize_director_result(payload)

    assert result.disposition == "candidate_ready"
    assert result.plan.shots[-1].continuity.final_frame == final_frame
    assert payload == before == result.raw_payload
    assert result.normalized_payload["shots"][-1]["continuity"]["finalFrame"] == final_frame
    advice = next(issue for issue in result.issues if issue.code == "ending_review")
    assert advice.severity == "warning"
    assert advice.path == f"shots.{len(payload['shots']) - 1}.continuity.finalFrame"
    assert advice.provider_value == final_frame


@pytest.mark.parametrize("invalid_frame", [None, "", 123])
def test_ending_advice_does_not_relax_required_final_frame(invalid_frame: object) -> None:
    from catflow.domain.director_results import normalize_director_result

    payload = _director_payload()
    payload["shots"][-1]["continuity"]["finalFrame"] = invalid_frame
    result = normalize_director_result(payload)
    assert result.disposition == "needs_input"
    assert result.plan is None
    assert any(issue.severity == "blocking" and issue.path.endswith("finalFrame")
               for issue in result.issues)
