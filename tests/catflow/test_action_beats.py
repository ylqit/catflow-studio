from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from catflow.application.service import (
    PlannerMessageCommand,
    ProjectCreate,
    ShotPlanGenerationCommand,
)
from catflow.application.shot_production import shot_design_hash
from catflow.application.video_generation import (
    compile_shot_media_prompt,
    compile_video_generation_prompt,
    synchronize_professional_shot_summaries,
)
from catflow.domain.director_results import DIRECTOR_OUTPUT_CONTRACT, normalize_director_result
from catflow.domain.models import ActionBeat, DirectorPlanPayload, ShotSpec
from test_provider_prompt_boundary import _ready_storyboard
from test_workflow import _director_payload, _service


def _shot(seconds=4, order=1):
    values = _director_payload().shots[0].model_dump(by_alias=True)
    values.update(
        id=f"shot-{order}", order=order, durationSeconds=seconds, durationFrames=seconds * 24
    )
    values["actionBeats"] = [
        {
            "startFrame": 0,
            "endFrame": seconds * 24,
            "purpose": "reaction",
            "childAction": "孩子把风车移回风中",
            "catAction": "猫抬头关注风车并回应孩子",
            "visibleChange": "风车重新转动，猫完成闭眼再睁眼",
            "catPerformance": {
                "visibility": "visible",
                "gazeFrom": "风车",
                "gazeTo": "孩子的脸",
                "eyelidAction": "slow_blink",
            },
        }
    ]
    return ShotSpec.model_validate(values)


@pytest.mark.parametrize(
    "updates",
    [
        {"startFrame": -1},
        {"startFrame": 20, "endFrame": 20},
        {"startFrame": 25, "endFrame": 20},
        {"endFrame": 361},
        {"startFrame": 0.5},
        {"childAction": " ", "catAction": " "},
        {"visibleChange": " "},
    ],
)
def test_invalid_beats_are_rejected_without_silently_retiming(updates):
    beat = _shot().action_beats[0].model_dump(by_alias=True)
    with pytest.raises(ValidationError):
        ActionBeat.model_validate({**beat, **updates})


@pytest.mark.parametrize("ranges", [[(0, 60), (48, 96)], [(48, 96), (0, 48)], [(0, 97)]])
def test_shot_rejects_overlaps_reverse_order_and_outside_frames(ranges):
    values = _shot().model_dump(by_alias=True)
    template = values["actionBeats"][0]
    values["actionBeats"] = [
        {**template, "startFrame": start, "endFrame": end} for start, end in ranges
    ]
    with pytest.raises(ValidationError):
        ShotSpec.model_validate(values)


def test_legacy_absence_preserves_nested_json_and_original_design_hash():
    values = _director_payload().model_dump(mode="json", by_alias=True)
    values["shots"][0].pop("actionBeats")
    legacy = DirectorPlanPayload.model_validate(values)
    assert legacy.model_dump(mode="json", by_alias=True) == values
    assert json.loads(legacy.model_dump_json(by_alias=True)) == values
    shot = legacy.shots[0]
    assert "action_beats" not in shot.model_dump()
    assert shot.model_dump(include={"id", "action_beats"}, by_alias=True) == {"id": shot.id}
    old_shot = deepcopy(values["shots"][0])
    old_shot.pop("confirmedFrame")
    for field in ("cameraSpatialRelation", "interactionConstraints", "visualExclusions"):
        if not old_shot[field]:
            del old_shot[field]
    old_hash = hashlib.sha256(
        json.dumps(
            {"shot": old_shot, "references": [], "environmentIntent": "房间"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert shot_design_hash(shot, [], "房间") == old_hash
    assert shot_design_hash(_director_payload().shots[0], [], "房间") != old_hash


def test_new_director_requires_beats_but_v3_recovers_original_result():
    values = _director_payload().model_dump(mode="json", by_alias=True)
    values["shots"][0].pop("actionBeats")
    before = deepcopy(values)
    current = normalize_director_result(values, output_contract_revision=DIRECTOR_OUTPUT_CONTRACT)
    assert current.disposition == "needs_input"
    assert current.raw_payload == before
    assert any(issue.path == "shots.0.actionBeats" for issue in current.issues)
    historical = normalize_director_result(
        values, output_contract_revision="professional-director-v3"
    )
    assert historical.disposition == "candidate_ready"
    assert historical.plan.shots[0].action_beats is None
    assert historical.normalized_payload == before


def test_new_reaction_can_return_to_same_physical_state_without_inventing_a_prop_change():
    values = _director_payload().model_dump(mode="json", by_alias=True)
    values["shots"][0]["physicalChange"].update(before="风车转动", after="风车转动")
    assert normalize_director_result(values).disposition == "candidate_ready"
    values["shots"][0].pop("actionBeats")
    assert (
        normalize_director_result(
            values, output_contract_revision="professional-director-v3"
        ).disposition
        == "needs_input"
    )


@pytest.mark.parametrize("cat_identity", ["原版灰白虎斑猫", "V4白底浅灰虎斑白猫"])
def test_full_clip_uses_global_time_and_shot_uses_local_time_with_same_performance(cat_identity):
    first, second = _shot(3), _shot(5, 2)
    whole = compile_video_generation_prompt(
        project_title="风车",
        target_duration_seconds=8,
        shots=[first, second],
        director_treatment=None,
        cat_identity=cat_identity,
    )
    single = compile_shot_media_prompt(shot=second, initial_frame=False)
    assert "节拍 0–3秒" in whole.prompt and "节拍 3–8秒" in whole.prompt
    assert "节拍 0–5秒" in single.prompt and "3–8秒" not in single.prompt
    for prompt in (whole.prompt, single.prompt):
        assert "不固定眼睑开合或目光方向" in prompt
        assert "目光从风车转向孩子的脸" in prompt
        assert "缓慢闭合眼睑，短暂停留，再自然睁开" in prompt
        assert "双手沿猫爪方向移动" not in prompt
        assert "尾巴轻摆" not in prompt
    assert cat_identity in whole.prompt
    assert "孩子把风车移回风中" in whole.prompt_summary
    assert "双手沿猫爪方向移动" not in whole.prompt_summary
    assert "沿用镜头1的方向" in whole.prompt
    assert "严格首帧仅固定开始状态" in single.prompt


def test_first_frame_contains_only_initial_state_and_hidden_face_has_no_eye_instruction():
    shot = _shot()
    still = compile_shot_media_prompt(shot=shot, initial_frame=True)
    assert "猫咪四足站稳" in still.prompt
    for ending_action in ("节拍", "缓慢闭合", "移回风中", "儿童开始折好毛巾", "猫咪向右迈步"):
        assert ending_action not in still.prompt
    values = shot.model_dump(by_alias=True)
    values["actionBeats"][0]["catPerformance"]["visibility"] = "hidden"
    hidden = compile_shot_media_prompt(shot=ShotSpec.model_validate(values), initial_frame=False)
    assert "不要求可见眼部表演" in hidden.prompt
    assert "目光从风车" not in hidden.prompt
    assert "缓慢闭合眼睑" not in hidden.prompt


def test_beats_derive_compatibility_summaries_and_eye_edits_change_design():
    shot = _shot()
    original = shot.model_dump(by_alias=True)
    synced = synchronize_professional_shot_summaries(shot)
    assert synced.child_action == shot.action_beats[0].child_action
    assert synced.cat_blocking.movement_path == shot.action_beats[0].cat_action
    assert synced.cat_blocking.micro_motions == []
    assert synced.cat_blocking.initial_state == shot.cat_blocking.initial_state
    assert shot.model_dump(by_alias=True) == original
    changed = deepcopy(original)
    changed["actionBeats"][0]["catPerformance"]["eyelidAction"] = "blink"
    assert shot_design_hash(ShotSpec.model_validate(changed), [], "风区") != shot_design_hash(
        shot, [], "风区"
    )


@pytest.mark.parametrize("seconds", [8, 12, 15])
def test_story_schema_and_frozen_prompt_agree_with_project_duration(seconds):
    service = _service()
    project = service.create_project(
        ProjectCreate(title="风车", theme="孩子和猫试风车", targetDurationSeconds=seconds)
    )
    job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="允许小幅增写人猫互动，保留原始事实",
            expectedContextRevision=1,
            idempotencyKey=f"duration-{seconds}",
        ),
    )
    schema = job.frozen_input["outputSchema"]
    assert schema["properties"]["targetDurationSeconds"]["const"] == seconds
    assert f"目标严格为{seconds}秒" in job.frozen_input["prompt"]
    assert "猫咪至少一次能影响孩子下一步行为的反应" in job.frozen_input["prompt"]
    assert "不得让儿童和猫咪原地互看" not in job.frozen_input["prompt"]
    assert project.theme in job.frozen_input["prompt"]


def test_director_freezes_only_user_notes_from_the_adopted_story_job():
    service, project_id, _ = _ready_storyboard()
    first_job = next(
        job for job in service._repository.list_project_jobs(project_id) if job.kind == "plan_shots"
    )
    assert first_job.frozen_input["storyUserDirections"] == "整理物品"
    session = service.get_planner(project_id)
    service.enqueue_planner_message(
        project_id,
        PlannerMessageCommand(
            text="尚未采用的新要求不应修改旧故事",
            expectedContextRevision=session.context_revision,
            idempotencyKey="unadopted-directions",
        ),
    )
    director = service.create_shot_plan_generation_job(
        project_id, ShotPlanGenerationCommand(idempotencyKey="adopted-notes-only")
    )
    assert director.frozen_input["storyUserDirections"] == "整理物品"
    assert "尚未采用的新要求" not in director.frozen_input["prompt"]
