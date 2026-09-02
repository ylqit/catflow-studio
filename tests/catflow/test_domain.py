from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from catflow.domain.jobs import JobStatus, transition_job
from catflow.domain.models import (
    BlockingDesign,
    CompositionDesign,
    ContinuityDesign,
    DirectorPlanPayload,
    LensDesign,
    LifeClipSpec,
    LifeStoryProposalDraft,
    LightingDesign,
    PhysicalChangeDesign,
    ShotPlanDraft,
    ShotSoundDesign,
    ShotSpec,
)
from catflow.domain.references import ProviderReference, compile_references


def _proposal(**overrides: object) -> LifeStoryProposalDraft:
    payload: dict[str, object] = {
        "title": "雨天擦爪",
        "summary": "孩子替回家的猫咪擦干爪子。",
        "body": "雨声里，孩子把小毛巾铺在门边。",
        "trigger": "猫咪踩着湿脚印进门",
        "child_action": "孩子蹲下并展开毛巾",
        "cat_response": "猫咪依次把前爪放到毛巾上",
        "visible_change": "湿脚印停止延伸，爪子变干",
        "warm_ending": "猫咪靠在孩子膝边打呼噜",
        "target_duration_seconds": 12,
        "dialogue_policy": "none",
        "environment_intent": "雨天玄关，柔和室内暖光",
    }
    payload.update(overrides)
    return LifeStoryProposalDraft.model_validate(payload)


def test_life_story_proposal_enforces_single_short_micro_event() -> None:
    proposal = _proposal()

    assert proposal.target_duration_seconds == 12
    assert proposal.micro_event.warm_ending.endswith("打呼噜")

    with pytest.raises(ValidationError, match="8"):
        _proposal(target_duration_seconds=7)


def test_shot_plan_accepts_one_to_four_shots_totalling_clip_duration() -> None:
    clip = LifeClipSpec(
        durationSeconds=12,
        aspectRatio="9:16",
        microEvent="雨天擦爪",
        childAction="孩子铺开毛巾",
        catActionOrObservation="猫咪把爪子放上去",
        visibleCauseAndEffect="爪子擦干，脚印停止",
        warmEnding="猫咪靠着孩子打呼噜",
        dialoguePolicy="none",
        environmentIntent="雨天玄关",
    )
    plan = ShotPlanDraft(
        sourceStoryVersionId=uuid.uuid4(),
        sourceSelectionHash="a" * 64,
        clip=clip,
        shots=[
            ShotSpec(
                id="shot-1",
                order=1,
                durationSeconds=4,
                framing="中景",
                cameraMovement="固定",
                childAction="注意到湿脚印",
                catAction="走进玄关",
                environmentChange="地面出现脚印",
                transition="continuous",
            ),
            ShotSpec(
                id="shot-2",
                order=2,
                durationSeconds=4,
                framing="近景",
                cameraMovement="轻微下移",
                childAction="擦干猫爪",
                catAction="把前爪放上毛巾",
                environmentChange="湿脚印停止延伸",
                transition="soft_cut",
            ),
            ShotSpec(
                id="shot-3",
                order=3,
                durationSeconds=4,
                framing="中近景",
                cameraMovement="缓慢推进",
                childAction="收起毛巾",
                catAction="靠着孩子打呼噜",
                environmentChange="暖光落在人猫身上",
                transition="continuous",
            ),
        ],
    )

    assert plan.total_duration_seconds == 12


def test_shot_plan_rejects_short_shots_and_duration_drift() -> None:
    clip = LifeClipSpec(
        durationSeconds=8,
        aspectRatio="9:16",
        microEvent="窗边纸星星",
        childAction="折纸",
        catActionOrObservation="观察纸星星",
        visibleCauseAndEffect="星星被挂到窗边",
        warmEnding="猫咪影子和纸星星一起摇动",
        dialoguePolicy="none",
        environmentIntent="午后窗边",
    )
    with pytest.raises(ValidationError, match="总时长"):
        ShotPlanDraft(
            sourceStoryVersionId=uuid.uuid4(),
            sourceSelectionHash="b" * 64,
            clip=clip,
            shots=[
                ShotSpec(
                    id="shot-1",
                    order=1,
                    durationSeconds=3,
                    framing="近景",
                    cameraMovement="固定",
                    childAction="折纸",
                    catAction="观察",
                    environmentChange="纸张变成星星",
                    transition="continuous",
                )
            ],
        )


def test_professional_shot_keeps_action_path_continuity_sound_and_generation_risks() -> None:
    shot = ShotSpec(
        id="shot-1",
        order=1,
        durationSeconds=4,
        durationFrames=96,
        framing="中景",
        cameraMovement="轻微下移",
        childAction="孩子蹲下擦猫爪",
        catAction="猫咪依次抬爪配合",
        environmentChange="湿爪印逐渐减少",
        transition="continuous",
        lens=LensDesign(
            focalLengthEquivalent="35mm",
            cameraHeight="儿童腰部高度",
            cameraAngle="轻微俯拍",
            perspectiveIntent="同时看清手、猫爪和湿脚印",
        ),
        composition=CompositionDesign(
            subjectPlacement="孩子在画面左侧，猫咪在右下侧",
            foreground="软毛巾",
            middleGround="孩子双手与猫爪",
            background="暖光玄关",
            screenDirection="由门口向室内",
            eyeLine="孩子视线落在猫爪",
        ),
        childBlocking=BlockingDesign(
            initialState="孩子站在脚垫旁",
            movementPath="屈膝蹲下并把毛巾包住前爪",
            endState="孩子握住已经折好的毛巾",
            microMotions=["视线跟随猫爪", "重新握紧毛巾"],
        ),
        catBlocking=BlockingDesign(
            initialState="猫咪四足站在湿脚垫边缘",
            movementPath="依次抬起前爪并把重心移向后腿",
            endState="猫咪四足落在干燥脚垫上",
            microMotions=["猫耳转向", "尾巴自然摆动"],
        ),
        physicalChange=PhysicalChangeDesign(
            subject="猫爪和地面水印",
            before="猫爪潮湿并留下连续水印",
            after="猫爪擦干且水印明显减少",
        ),
        continuity=ContinuityDesign(
            incoming="承接猫咪刚进门的向右运动",
            outgoing="猫咪继续向室内迈步",
            sharedVisualElement="同一块脚垫和毛巾",
            finalFrame="猫咪前爪落在干燥脚垫，孩子开始折毛巾",
        ),
        lighting=LightingDesign(
            direction="室内右上方",
            softness="柔和漫射",
            colorIntent="雨天冷窗光与室内暖光平衡",
        ),
        sound=ShotSoundDesign(
            ambience=["细雨声"],
            objectEffects=["毛巾摩擦声"],
            movementEffects=["猫爪轻落脚垫"],
            musicIntent="极轻木琴点音",
        ),
        directorIntent="用明确的前后物理变化讲清擦爪因果",
        generationRisks=[{"code": "paw_occlusion", "message": "手掌不可遮挡全部猫爪"}],
    )

    assert shot.child_blocking is not None
    assert shot.child_blocking.movement_path.startswith("屈膝")
    assert shot.continuity is not None
    assert "脚垫" in shot.continuity.final_frame
    assert shot.duration_frames == 96

    with pytest.raises(ValidationError, match="at most 3"):
        BlockingDesign(
            initialState="开始",
            movementPath="移动",
            endState="结束",
            microMotions=["一", "二", "三", "四"],
        )


def _professional_director_payload() -> dict[str, object]:
    def shot(order: int, direction: str, final_frame: str) -> dict[str, object]:
        return {
            "id": f"shot-{order}",
            "order": order,
            "durationSeconds": 6,
            "durationFrames": 144,
            "framing": "中景",
            "cameraMovement": "轻微跟随",
            "childAction": "孩子用毛巾擦干猫爪",
            "catAction": "猫咪抬爪后向室内迈步",
            "environmentChange": "湿爪印逐渐减少",
            "transition": "continuous",
            "lens": {
                "focalLengthEquivalent": "35mm",
                "cameraHeight": "儿童腰部",
                "cameraAngle": "轻微俯拍",
                "perspectiveIntent": "同时看清手、猫爪和脚垫",
            },
            "composition": {
                "subjectPlacement": "儿童左、猫咪右",
                "foreground": "毛巾",
                "middleGround": "儿童与猫咪",
                "background": "玄关",
                "screenDirection": direction,
                "eyeLine": "儿童看向猫爪",
            },
            "childBlocking": {
                "initialState": "儿童蹲在脚垫边",
                "movementPath": "双手沿猫爪方向移动",
                "endState": "儿童开始折好毛巾",
                "microMotions": ["重新握紧毛巾"],
            },
            "catBlocking": {
                "initialState": "猫咪四足站稳",
                "movementPath": "逐只抬爪并向右移重心",
                "endState": "猫咪向右迈步",
                "microMotions": ["尾巴轻摆"],
            },
            "physicalChange": {
                "subject": "猫爪和地面水印",
                "before": "猫爪潮湿且水印连续",
                "after": "猫爪擦干且水印减少",
            },
            "continuity": {
                "incoming": "承接向右运动",
                "outgoing": "保持向右运动",
                "sharedVisualElement": "同一毛巾、脚垫和轴线",
                "finalFrame": final_frame,
            },
            "lighting": {
                "direction": "室内右上方",
                "softness": "柔和漫射",
                "colorIntent": "冷暖平衡",
            },
            "sound": {
                "ambience": ["轻雨声"],
                "objectEffects": ["毛巾摩擦"],
                "movementEffects": ["猫爪轻落"],
                "musicIntent": "轻柔木琴",
            },
            "directorIntent": "通过动作闭合呈现照顾感",
            "generationRisks": [{"code": "paw_contact", "message": "避免手爪融合"}],
        }

    return {
        "targetDurationSeconds": 12,
        "directorTreatment": {
            "logline": "雨天回家后的一次温柔擦爪",
            "theme": "照顾",
            "emotionalTone": ["温暖", "克制"],
            "visualMotif": "湿脚印逐渐消失",
            "spatialSetting": "雨天玄关",
            "emotionalArc": {
                "opening": "发现湿爪",
                "development": "擦干",
                "resolution": "走进室内",
            },
            "microEvent": {
                "trigger": "猫咪留下湿爪印",
                "childIntent": "照顾刚回家的猫咪",
                "childAction": "擦干猫爪",
                "catResponse": "抬爪配合",
                "visibleCauseAndEffect": "猫爪变干且水印减少",
                "warmEnding": "孩子折毛巾，猫咪继续走进室内",
            },
            "propStateChange": {"initialState": "毛巾展开", "changedState": "毛巾折好"},
            "soundIntent": "轻雨、毛巾摩擦和猫爪落地",
            "endingImage": "孩子折好毛巾，猫咪向室内迈步",
            "feasibilityWarnings": [],
        },
        "shots": [
            shot(1, "从左向右", "猫咪抬爪后继续向右迈步"),
            shot(2, "从左向右", "孩子折好毛巾，猫咪继续向右迈步"),
        ],
    }


def test_director_plan_rejects_direction_conflicts_static_endings_and_no_state_change() -> None:
    valid = _professional_director_payload()
    DirectorPlanPayload.model_validate(valid)

    conflicting = _professional_director_payload()
    conflicting["shots"][1]["composition"]["screenDirection"] = "从右向左"  # type: ignore[index]
    with pytest.raises(ValidationError, match="screen direction conflict"):
        DirectorPlanPayload.model_validate(conflicting)

    static = _professional_director_payload()
    static["shots"][1]["continuity"]["finalFrame"] = "孩子和猫咪原地互看，画面静止"  # type: ignore[index]
    with pytest.raises(ValidationError, match="active ending"):
        DirectorPlanPayload.model_validate(static)

    unchanged = _professional_director_payload()
    before = unchanged["shots"][0]["physicalChange"]["before"]  # type: ignore[index]
    unchanged["shots"][0]["physicalChange"]["after"] = before  # type: ignore[index]
    with pytest.raises(ValidationError, match="visible physical state change"):
        DirectorPlanPayload.model_validate(unchanged)


def test_reference_compiler_excludes_style_source_and_preserves_priority() -> None:
    references = [
        ProviderReference(assetId=uuid.uuid4(), role="environment", sha256="d" * 64),
        ProviderReference(assetId=uuid.uuid4(), role="episode_cat", sha256="b" * 64),
        ProviderReference(assetId=uuid.uuid4(), role="style_source", sha256="f" * 64),
        ProviderReference(assetId=uuid.uuid4(), role="style_board", sha256="e" * 64),
        ProviderReference(assetId=uuid.uuid4(), role="pair_scale", sha256="c" * 64),
        ProviderReference(assetId=uuid.uuid4(), role="episode_child", sha256="a" * 64),
    ]

    compiled = compile_references(references, maximum_references=4)

    assert [item.role for item in compiled.references if item.included] == [
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
    ]
    assert next(
        item for item in compiled.references if item.role == "style_source"
    ).omitted_reason == ("style_source_not_provider_eligible")
    assert next(
        item for item in compiled.references if item.role == "style_board"
    ).omitted_reason == ("provider_reference_limit")
    assert len(compiled.input_hash) == 64


def test_job_state_machine_never_reenters_submission_after_provider_acceptance() -> None:
    assert transition_job(JobStatus.QUEUED, JobStatus.SUBMITTING) is JobStatus.SUBMITTING
    assert transition_job(JobStatus.SUBMITTING, JobStatus.SUBMITTED) is JobStatus.SUBMITTED
    assert transition_job(JobStatus.SUBMITTED, JobStatus.POLLING) is JobStatus.POLLING

    with pytest.raises(ValueError, match="submitted.*submitting"):
        transition_job(JobStatus.SUBMITTED, JobStatus.SUBMITTING)


def test_submission_unknown_is_terminal_and_cannot_be_resubmitted() -> None:
    assert (
        transition_job(JobStatus.SUBMITTING, JobStatus.SUBMISSION_UNKNOWN)
        is JobStatus.SUBMISSION_UNKNOWN
    )

    with pytest.raises(ValueError, match="submission_unknown.*submitting"):
        transition_job(JobStatus.SUBMISSION_UNKNOWN, JobStatus.SUBMITTING)
