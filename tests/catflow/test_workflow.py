from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from catflow.application.service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    GenerationCommand,
    ImageDiagnosisCommand,
    JobDto,
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
    pending_planner = service.get_planner(project.id)
    assert len(pending_planner.messages) == 1
    assert pending_planner.latest_job is not None
    assert pending_planner.latest_job.id == first.id
    assert pending_planner.latest_job.status == "queued"

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

    selected_assets = {
        role: asset.id for role, asset in service.current_selections(project.id).items()
    }
    environment = service.register_asset(
        project.id,
        role="environment",
        sha256="5" * 64,
    )
    service.select_asset(project.id, slot="environment", asset_id=environment.id)
    selected_assets["environment"] = environment.id

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
                    childAction="孩子收起毛巾",
                    catAction="猫咪靠着孩子打呼噜",
                    environmentChange="暖光落在人猫身上",
                    transition="continuous",
                ),
            ],
        ),
    )
    preview = service.preview_video_generation(project.id)

    assert shot_plan.revision == 1
    assert [reference.role for reference in preview.references if reference.included] == [
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
        "style_board",
    ]
    assert all(reference.omitted_reason is None for reference in preview.references)
    assert "6至7岁" in preview.prompt
    assert "约1.2米" in preview.prompt
    assert "4.5至5头身" in preview.prompt
    assert "禁止8岁以上" in preview.negative_prompt
    assert "8至9岁" not in preview.prompt
    assert preview.story_version_id == story.id
    assert preview.shot_plan_version_id == shot_plan.id
    assert "孩子孩子" not in preview.prompt
    assert "猫咪猫咪" not in preview.prompt
    assert story.body not in preview.prompt
    assert story.micro_event.trigger in preview.prompt
    assert story.micro_event.visible_change in preview.prompt

    command = GenerationCommand(
        expectedInputHash=preview.input_hash,
        expectedCostMicros=preview.expected_cost_micros,
        idempotencyKey="video-rainy-paws-1",
    )
    first_video_job = service.create_video_job(project.id, command)
    same_video_job = service.create_video_job(project.id, command)

    assert first_video_job.id == same_video_job.id
    workspace = service.workspace(project.id)
    assert workspace["latestVideoJob"]["id"] == str(first_video_job.id)
    assert workspace["eventCursor"] >= 1
    assert all("storageKey" not in asset for asset in workspace["selections"].values())
    assert first_video_job.frozen_input["referenceAssetIds"] == [
        str(selected_assets[slot])
        for slot in (
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        )
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


def test_selected_environment_preset_is_shared_by_every_project() -> None:
    service = _service()
    first = _project(service)
    second = service.create_project(
        ProjectCreate(
            title="浇花",
            theme="孩子浇花，猫咪避开最后一滴水",
            targetDurationSeconds=12,
        )
    )
    environment = service.register_asset(
        first.id,
        role="environment",
        sha256="9" * 64,
    )

    service.select_asset(first.id, slot="environment", asset_id=environment.id)

    assert service.current_selections(first.id)["environment"].id == environment.id
    assert service.current_selections(second.id)["environment"].id == environment.id
    assert service.environment_presets()[0].asset.id == environment.id
    assert service.environment_presets()[0].active is True


def test_result_storage_failure_can_resume_without_creating_or_resubmitting_a_job() -> None:
    repository = MemoryStudioRepository()
    service = StudioService(repository)
    project = _project(service)
    now = datetime.now(UTC)
    job = repository.create_job(
        JobDto(
            id=uuid.uuid4(),
            projectId=project.id,
            kind="generate_video",
            status="failed",
            inputHash="a" * 64,
            idempotencyKey=f"video-storage-recovery-{project.id}",
            provider="ark",
            model="video-model",
            providerTaskId="provider-task-1",
            providerResult={
                "videoUrl": "https://ark.cn-beijing.volces.com/result.mp4",
                "ratio": "9:16",
                "resolution": "480p",
            },
            frozenInput={"durationSeconds": 12},
            error={
                "code": "result_storage_failed",
                "message": "native Ark size was rejected",
                "retryable": False,
            },
            createdAt=now,
            updatedAt=now,
        )
    )

    resumed = service.resume_job_storage(job.id)

    assert resumed.id == job.id
    assert resumed.status == "storing"
    assert resumed.provider_task_id == "provider-task-1"
    assert resumed.error is None
    assert service.workspace(project.id)["latestVideoJob"]["id"] == str(job.id)


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
    assets = service.current_selections(project.id)

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
