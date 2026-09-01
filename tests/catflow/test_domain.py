from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from catflow.domain.jobs import JobStatus, transition_job
from catflow.domain.models import LifeClipSpec, LifeStoryProposalDraft, ShotPlanDraft, ShotSpec
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
