from __future__ import annotations

import uuid

import pytest

from catflow.application.service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    GenerationCommand,
    ImageDiagnosisCommand,
    PlannerMessageCommand,
    ProjectCreate,
    ProjectPatch,
    StudioConflictError,
    StudioService,
)
from catflow.domain.models import LifeClipSpec, LifeStoryProposalDraft, ShotPlanDraft, ShotSpec
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def _proposal() -> LifeStoryProposalDraft:
    return LifeStoryProposalDraft(
        title="雨天擦爪",
        summary="孩子替猫咪擦干回家后的湿爪子。",
        body="雨声轻轻落在窗外，孩子在玄关铺开一块小毛巾。",
        trigger="猫咪带着湿脚印进门",
        childAction="孩子蹲下并铺开毛巾",
        catResponse="猫咪依次把前爪放到毛巾上",
        visibleChange="湿脚印停止延伸，猫爪变干",
        warmEnding="猫咪靠在孩子膝边打呼噜",
        targetDurationSeconds=12,
        dialoguePolicy="none",
        environmentIntent="雨天玄关，柔和暖光",
    )


def _service() -> StudioService:
    return StudioService(MemoryStudioRepository())


def _project(service: StudioService):  # type: ignore[no-untyped-def]
    return service.create_project(
        ProjectCreate(
            title="雨天擦爪",
            theme="孩子替回家的猫咪擦爪",
            targetDurationSeconds=12,
        )
    )


def test_planner_message_is_idempotent_and_adoption_activates_one_story() -> None:
    service = _service()
    project = _project(service)
    command = PlannerMessageCommand(
        text="做一个雨天回家后擦爪的安静小故事",
        expectedContextRevision=1,
        idempotencyKey="planner-rainy-paws",
    )

    first = service.enqueue_planner_message(project.id, command)
    second = service.enqueue_planner_message(project.id, command)

    assert first.id == second.id
    assert first.kind == "plan_story"
    assert first.frozen_input["targetDurationSeconds"] == project.target_duration_seconds
    assert len(service.get_planner(project.id).messages) == 1

    proposal = service.complete_planner_job(first.id, _proposal())
    story = service.adopt_proposal(project.id, proposal.id)
    planner = service.get_planner(project.id)

    assert story.active is True
    assert story.revision == 1
    assert planner.proposals[0].status == "adopted"


def test_planner_rejects_changed_context_instead_of_silently_using_it() -> None:
    service = _service()
    project = _project(service)

    with pytest.raises(StudioConflictError, match="context revision"):
        service.enqueue_planner_message(
            project.id,
            PlannerMessageCommand(
                text="继续",
                expectedContextRevision=2,
                idempotencyKey="wrong-revision",
            ),
        )


def test_project_brief_change_advances_context_and_marks_draft_proposals_outdated() -> None:
    service = _service()
    project = _project(service)
    job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="planner-before-project-patch",
        ),
    )
    service.complete_planner_job(job.id, _proposal())

    service.update_project(project.id, ProjectPatch(theme="雨停后的玄关擦爪"))

    snapshot = service.get_planner(project.id)
    assert snapshot.context_revision == 2
    assert snapshot.proposals[0].status == "outdated"


def test_story_shot_plan_assets_and_generation_form_one_direct_chain() -> None:
    service = _service()
    project = _project(service)
    job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="planner-chain",
        ),
    )
    proposal = service.complete_planner_job(job.id, _proposal())
    story = service.adopt_proposal(project.id, proposal.id)

    selected_assets: dict[str, uuid.UUID] = {}
    for index, slot in enumerate(
        ("episode_child", "episode_cat", "pair_scale", "environment", "style_board"), start=1
    ):
        asset = service.register_asset(
            project.id,
            role=slot,
            sha256=f"{index:x}" * 64,
        )
        service.select_asset(project.id, slot=slot, asset_id=asset.id)
        selected_assets[slot] = asset.id

    shot_plan = service.create_shot_plan(
        project.id,
        ShotPlanDraft(
            sourceStoryVersionId=story.id,
            sourceSelectionHash=service.current_selection_hash(project.id),
            clip=LifeClipSpec(
                durationSeconds=12,
                aspectRatio="9:16",
                microEvent="雨天擦爪",
                childAction="孩子替猫咪擦爪",
                catActionOrObservation="猫咪安静配合",
                visibleCauseAndEffect="湿脚印停止延伸",
                warmEnding="猫咪靠着孩子打呼噜",
                dialoguePolicy="none",
                environmentIntent="雨天玄关",
            ),
            shots=[
                ShotSpec(
                    id="shot-1",
                    order=1,
                    durationSeconds=4,
                    framing="中景",
                    cameraMovement="固定",
                    childAction="注意到湿脚印",
                    catAction="走进玄关",
                    environmentChange="地面出现湿脚印",
                    transition="continuous",
                ),
                ShotSpec(
                    id="shot-2",
                    order=2,
                    durationSeconds=4,
                    framing="近景",
                    cameraMovement="轻微下移",
                    childAction="擦干猫爪",
                    catAction="把爪子放上毛巾",
                    environmentChange="脚印停止延伸",
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
        ),
    )
    preview = service.preview_video_generation(project.id, maximum_references=4)

    assert shot_plan.revision == 1
    assert [reference.role for reference in preview.references if reference.included] == [
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
    ]
    assert next(
        item for item in preview.references if item.role == "style_board"
    ).omitted_reason == ("provider_reference_limit")
    assert preview.story_version_id == story.id
    assert preview.shot_plan_version_id == shot_plan.id

    command = GenerationCommand(
        expectedInputHash=preview.input_hash,
        expectedCostMicros=preview.expected_cost_micros,
        idempotencyKey="video-rainy-paws-1",
    )
    first_video_job = service.create_video_job(project.id, command)
    same_video_job = service.create_video_job(project.id, command)

    assert first_video_job.id == same_video_job.id
    assert first_video_job.frozen_input["referenceAssetIds"] == [
        str(selected_assets[slot])
        for slot in ("episode_child", "episode_cat", "pair_scale", "environment")
    ]

    selected_video = service.register_asset(
        project.id,
        role="video",
        media_type="video",
        sha256="e" * 64,
        storage_key="video/chosen.mp4",
        byte_size=10,
    )
    service.select_asset(project.id, slot="video", asset_id=selected_video.id)
    assert service.preview_video_generation(project.id).input_hash == preview.input_hash

    with pytest.raises(StudioConflictError, match="input hash"):
        service.create_video_job(
            project.id,
            GenerationCommand(
                expectedInputHash="f" * 64,
                expectedCostMicros=preview.expected_cost_micros,
                idempotencyKey="video-stale",
            ),
        )


def test_asset_generation_preview_and_job_freeze_role_without_style_source() -> None:
    service = _service()
    project = _project(service)
    preview = service.preview_asset_generation(
        project.id, AssetGenerationPreviewCommand(kind="episode_cat")
    )
    job = service.create_asset_generation_job(
        project.id,
        AssetGenerationCommand(
            kind="episode_cat",
            expectedInputHash=preview.input_hash,
            expectedCostMicros=preview.expected_cost_micros,
            idempotencyKey="asset-cat-fake-1",
        ),
    )

    assert preview.kind == "episode_cat"
    assert job.kind == "generate_image"
    assert job.frozen_input["role"] == "episode_cat"
    assert all(reference["role"] != "style_source" for reference in job.frozen_input["references"])


def test_image_diagnosis_freezes_candidate_and_labeled_identity_style_references() -> None:
    service = _service()
    project = _project(service)
    assets = {}
    for index, role in enumerate(
        ("episode_child", "episode_cat", "pair_scale", "style_board"), start=1
    ):
        asset = service.register_asset(project.id, role=role, sha256=f"{index}" * 64)
        service.select_asset(project.id, slot=role, asset_id=asset.id)
        assets[role] = asset

    job = service.create_image_diagnosis_job(
        project.id,
        ImageDiagnosisCommand(
            assetId=assets["pair_scale"].id,
            idempotencyKey="diagnose-pair-scale-1",
        ),
    )

    assert job.kind == "diagnose_image"
    assert job.frozen_input["candidateAssetId"] == str(assets["pair_scale"].id)
    assert [reference["label"] for reference in job.frozen_input["references"]] == [
        "本集儿童设计",
        "本集猫咪设计",
        "人猫同框比例",
        "Canon v4 净化画风板",
    ]
    assert all(reference["role"] != "style_source" for reference in job.frozen_input["references"])
