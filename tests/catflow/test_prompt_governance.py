from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from catflow.application.continuity import (
    EpisodeContinuitySnapshotDto,
    compile_continuity_constraints,
)
from catflow.application.gateways import SegmentVideoGenerationRequest
from catflow.application.media_prompt import compile_provider_media_prompt
from catflow.application.shot_production import shot_design_hash
from catflow.application.video_generation import (
    compile_shot_media_prompt,
    compile_video_generation_prompt,
)
from catflow.domain.director_results import normalize_director_result
from catflow.domain.models import DirectorStoryTreatment, ShotSpec
from catflow_worker.ark_gateway import ArkTypedGateway
from test_ark_gateway import Recorder, _image, _settings
from test_director_result_normalization import _director_payload


def test_new_contract_requires_spatial_fields_and_old_contract_remains_readable() -> None:
    payload = _director_payload()
    for field in ("cameraSpatialRelation", "interactionConstraints", "visualExclusions"):
        payload["shots"][0].pop(field)
    assert normalize_director_result(
        payload, output_contract_revision="professional-director-v2"
    ).plan
    result = normalize_director_result(payload)
    assert result.disposition == "needs_input"
    assert {issue.path for issue in result.issues if issue.severity == "blocking"} >= {
        "shots.0.cameraSpatialRelation",
        "shots.0.interactionConstraints",
        "shots.0.visualExclusions",
    }


def test_six_recorded_inputs_compile_without_internal_planning_documents() -> None:
    fixtures = json.loads(
        (Path(__file__).parent / "fixtures/prompt-governance-six-episodes.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(fixtures) == 6
    for fixture in fixtures:
        continuity = ()
        if fixture["continuityState"]:
            snapshot = EpisodeContinuitySnapshotDto(
                id=uuid.uuid4(),
                episodeId=uuid.uuid4(),
                direction="incoming",
                source="confirmed",
                state=fixture["continuityState"],
                confirmed=True,
                active=True,
                createdAt=datetime.now(UTC),
            )
            continuity = compile_continuity_constraints(snapshot)
        shots = [ShotSpec.model_validate(shot) for shot in fixture["shots"]]
        compiled = compile_video_generation_prompt(
            project_title=fixture["projectTitle"],
            target_duration_seconds=fixture["targetDurationSeconds"],
            shots=shots,
            director_treatment=DirectorStoryTreatment.model_validate(fixture["directorTreatment"]),
            continuity_constraints=continuity,
        )
        final = compile_provider_media_prompt(
            prompt=compiled.prompt,
            negative_prompt=compiled.negative_prompt,
            reference_roles=tuple(fixture["referenceRoles"]),
        )
        for forbidden in (
            '"wardrobe"',
            '"owner"',
            '"location"',
            "null",
            "soft_cut",
            "continuous",
            "episode_child",
            "pair_scale",
            "需退回",
        ):
            assert forbidden not in final, (fixture["projectTitle"], forbidden)
        assert "图1：提供儿童身份" in final
        assert "图3：提供上述同一位儿童" in final
        for shot in shots:
            assert shot.child_blocking.movement_path.rstrip("。；，") in final
            for risk in shot.generation_risks:
                assert risk.code not in compiled.negative_prompt
        # Regression compilation exposes historical design without inventing missing relations.
        assert all(shot.camera_spatial_relation is None for shot in shots)


@pytest.mark.parametrize(
    ("relation", "constraint", "exclusion"),
    [
        ("摄影机和孩子在帐篷外，猫身体在入口外侧", "猫四足落在帐篷外草地", "猫身体穿过帐篷布"),
        (
            "摄影机侧拍孩子与机器人之间",
            "孩子将唯一积木放入机器人夹爪后松手",
            "递接时出现第二块积木",
        ),
        (
            "摄影机在矮墙侧面，孩子肩部和伸过墙顶的手臂同时可见",
            "右手从孩子右肩经墙顶伸向球",
            "墙另一侧凭空出现手臂",
        ),
        (
            "摄影机斜对镜面，孩子与同一孩子的镜像分处镜面两侧",
            "镜像与孩子同步抬起同一只手",
            "镜中多出第二个孩子",
        ),
        ("椅背遮住猫躯干一部分，前爪和落脚地面可见", "同一只猫从椅背左缘走出", "遮挡后增加一只猫"),
        (
            "俯侧机位看见孩子双手和床罩边缘",
            "双手拉动同一床罩，布料覆盖盒子后盒子留在原处",
            "覆盖物下出现额外盒子",
        ),
        ("摄影机固定看桌面和地面篮筐", "唯一木球由孩子手中放到同一篮筐", "桌面残留第二颗木球"),
    ],
)
def test_generic_relations_are_compiled_from_design_without_prop_keywords(
    relation, constraint, exclusion
) -> None:
    payload = _director_payload()["shots"][0]
    payload.pop("blocking_note")
    payload.update(
        cameraSpatialRelation=relation,
        interactionConstraints=[constraint],
        visualExclusions=[exclusion],
    )
    shot = ShotSpec.model_validate(payload)
    compiled = compile_shot_media_prompt(shot=shot, initial_frame=False)
    assert relation in compiled.prompt
    assert constraint in compiled.prompt
    assert exclusion in compiled.negative_prompt
    assert exclusion not in compiled.prompt


def test_start_image_omits_actions_and_design_changes_invalidate_confirmation() -> None:
    raw = _director_payload()["shots"][0]
    raw.pop("blocking_note")
    raw["childBlocking"]["movementPath"] = "专属动作过程"
    raw["childBlocking"]["endState"] = "专属结束状态"
    raw["physicalChange"]["after"] = "专属变化结果"
    raw["interactionConstraints"] = ["孩子放下唯一积木后松手，夹爪合上并持住积木"]
    shot = ShotSpec.model_validate(raw)
    image = compile_shot_media_prompt(shot=shot, initial_frame=True)
    assert shot.child_blocking.initial_state in image.prompt
    assert shot.physical_change.before in image.prompt
    for value in (
        "专属动作过程",
        "专属结束状态",
        "专属变化结果",
        "环境声：",
        "物件声：",
        "夹爪合上",
    ):
        assert value not in image.prompt
    assert "静止停帧" not in image.negative_prompt
    previous = shot_design_hash(shot, [], "草地")
    updated = shot.model_copy(update={"camera_spatial_relation": "摄影机换到同一角色背面"})
    assert shot_design_hash(updated, [], "草地") != previous
    legacy = shot.model_copy(
        update={
            "camera_spatial_relation": None,
            "interaction_constraints": [],
            "visual_exclusions": [],
        }
    )
    old = legacy.model_dump(
        mode="json",
        by_alias=True,
        exclude={
            "confirmed_frame",
            "camera_spatial_relation",
            "interaction_constraints",
            "visual_exclusions",
        },
    )
    document = {"shot": old, "references": [], "environmentIntent": "草地"}
    expected = hashlib.sha256(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert shot_design_hash(legacy, [], "草地") == expected


def test_unresolved_system_placeholder_reports_source_but_user_prose_is_preserved() -> None:
    raw = _director_payload()["shots"][0]
    raw.pop("blocking_note")
    raw["childBlocking"]["movementPath"] = "${actor}拿起道具"
    with pytest.raises(ValueError, match=r"shots\[0\].childBlocking.movementPath.*\$\{actor\}"):
        compile_shot_media_prompt(shot=ShotSpec.model_validate(raw), initial_frame=False)
    with pytest.raises(ValueError, match=r"referenceRoles\[1\].*unknown_role"):
        compile_provider_media_prompt(
            prompt="normal", negative_prompt="", reference_roles=("style_board", "unknown_role")
        )
    user_text = '拍摄写着 "Hello v4.0, 35mm, ${price}" 的说明卡'
    final = compile_provider_media_prompt(prompt=user_text, negative_prompt="", reference_roles=())
    assert user_text in final

    treatment = DirectorStoryTreatment.model_validate(_director_payload()["directorTreatment"])
    treatment = treatment.model_copy(update={"spatial_setting": "${scene_location}"})
    with pytest.raises(ValueError, match=r"directorTreatment.spatialSetting.*scene_location"):
        compile_video_generation_prompt(
            project_title="整理玩具",
            target_duration_seconds=12,
            shots=[
                ShotSpec.model_validate(
                    {
                        key: value
                        for key, value in _director_payload()["shots"][0].items()
                        if key != "blocking_note"
                    }
                )
            ],
            director_treatment=treatment,
        )


def test_continuity_decisions_describe_effective_incoming_state_and_omit_empty_values():
    raw = {
        "wardrobe": "蓝衬衣",
        "location": "新场景",
        "weather": "晴",
        "timeOfDay": "下午",
        "lighting": "柔光",
        "childState": "站在桌旁",
        "catState": "四足落地",
        "spatialPositions": "孩子左猫右",
        "props": [
            {"key": "prop_a", "name": "小木勺", "state": "一把", "owner": "child", "location": None}
        ],
        "unfinishedActions": [],
        "endingImage": "本集开始的桌边画面",
    }
    snapshot = EpisodeContinuitySnapshotDto(
        id=uuid.uuid4(),
        episodeId=uuid.uuid4(),
        direction="incoming",
        source="confirmed",
        state=raw,
        decisions={"wardrobe": "inherit", "location": "reset", "props": "adjust"},
        confirmed=True,
        active=True,
        createdAt=datetime.now(UTC),
    )
    text = "\n".join(compile_continuity_constraints(snapshot))
    assert "服装（沿用已确认状态）：蓝衬衣" in text
    assert "场景（在本集重新设置）：新场景" in text
    assert "道具小木勺（按本集确认调整）：一把；归属：儿童" in text
    assert "本集开场画面" in text
    for missing in ("prop_a", "待续动作", "null", "None", "owner", "[]", "上一集结束状态"):
        assert missing not in text


@pytest.mark.parametrize(
    "mode", ["references", "from_frame", "image", "segment", "segment_from_frame"]
)
def test_sdk_receives_exact_frozen_text_in_all_media_modes(tmp_path, mode) -> None:
    roles = ("episode_child", "episode_cat", "pair_scale", "environment", "style_board")
    image_path = _image(tmp_path / "ref.png", "blue")
    paths = (image_path,) * 5
    if mode in {"from_frame", "segment_from_frame"}:
        roles = ("first_frame",) if mode == "from_frame" else ("first_frame", "last_frame")
        paths = (image_path,) * len(roles)
    elif mode == "segment":
        roles = ("anchor_in", "anchor_out", *roles)
    final = compile_provider_media_prompt(
        prompt="孩子放下唯一木球。\n保留35mm机位。",
        negative_prompt="物体复制",
        reference_roles=roles,
        video_reference="segment" if mode == "segment" else None,
    )
    recorder = Recorder(
        SimpleNamespace(
            id="task-test", data=[SimpleNamespace(url="https://example.com/generated.png")]
        )
    )
    client = SimpleNamespace(images=recorder, content_generation=SimpleNamespace(tasks=recorder))
    gateway = ArkTypedGateway(_settings(), client=client)
    if mode == "image":
        gateway.generate_image(
            prompt="ignored source",
            negative_prompt="ignored negative",
            reference_paths=paths,
            reference_roles=roles,
            compiled_provider_prompt=final,
        )
        assert recorder.calls[0]["prompt"] == final
    elif mode.startswith("segment"):
        from_frame = mode == "segment_from_frame"
        gateway.submit_segment_video(
            SegmentVideoGenerationRequest(
                instruction="用户修复要求",
                prompt="source",
                negative_prompt="negative",
                compiled_provider_prompt=final,
                context_video_url=None if from_frame else "https://example.com/context.mp4",
                issue_start_seconds=0,
                issue_end_seconds=4,
                anchor_in_path=image_path,
                anchor_out_path=image_path,
                canon_reference_paths=() if from_frame else paths,
                canon_reference_roles=() if from_frame else roles[-5:],
                duration_seconds=4,
                resolution="480p",
                ratio="9:16",
                generation_mode="from_frame" if from_frame else "edit_existing",
                prompt_compiler_revision="segment-edit-v6",
            )
        )
        assert recorder.calls[0]["content"][0]["text"] == final
    else:
        gateway.submit_video(
            prompt=final,
            reference_paths=paths,
            reference_roles=roles,
            duration_seconds=4,
            resolution="480p",
            generation_mode=mode,
            provider_prompt_version=1,
        )
        assert recorder.calls[0]["content"][0]["text"] == final
