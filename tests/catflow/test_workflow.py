from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    GenerationCommand,
    ImageDiagnosisCommand,
    JobDto,
    PlannerMessageCommand,
    ProjectCreate,
    ProjectPatch,
    ShotPlanActivationCommand,
    ShotPlanGenerationCommand,
    ShotPlanGenerationMaterializeCommand,
    ShotPlanGenerationRecoveryCommand,
    StoryCreateCommand,
    StudioConflictError,
    StudioService,
)
from catflow.domain.models import (
    BlockingDesign,
    CompositionDesign,
    ContinuityDesign,
    DirectorMicroEvent,
    DirectorPlanPayload,
    DirectorStoryTreatment,
    EmotionalArc,
    LensDesign,
    LifeClipSpec,
    LifeStoryProposalDraft,
    LightingDesign,
    PhysicalChangeDesign,
    PropStateChange,
    ShotPlanDraft,
    ShotSoundDesign,
    ShotSpec,
)
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


def _director_payload() -> DirectorPlanPayload:
    return DirectorPlanPayload.model_validate(
        {
            "targetDurationSeconds": 12,
            "directorTreatment": {
                "logline": "雨天门边的一次擦爪",
                "theme": "照顾",
                "emotionalTone": ["安静", "温暖"],
                "visualMotif": "湿脚印逐渐消失",
                "spatialSetting": "雨天玄关",
                "emotionalArc": {
                    "opening": "发现湿爪",
                    "development": "逐只擦干",
                    "resolution": "走进室内",
                },
                "microEvent": {
                    "trigger": "猫咪留下湿爪印",
                    "childIntent": "照顾刚回家的猫咪",
                    "childAction": "用毛巾逐只擦干猫爪",
                    "catResponse": "抬爪配合后向前迈步",
                    "visibleCauseAndEffect": "猫爪变干且水印减少",
                    "warmEnding": "孩子折好毛巾，猫咪继续走进室内",
                },
                "propStateChange": {
                    "initialState": "毛巾展开",
                    "changedState": "毛巾折好并带有湿痕",
                },
                "soundIntent": "轻雨声与毛巾摩擦声",
                "endingImage": "孩子折好毛巾，猫咪向室内迈步",
                "feasibilityWarnings": [],
            },
            "shots": [
                {
                    "id": "shot-1",
                    "order": 1,
                    "durationSeconds": 12,
                    "durationFrames": 288,
                    "framing": "中景",
                    "cameraMovement": "轻微跟随",
                    "childAction": "孩子用毛巾逐只擦干猫爪",
                    "catAction": "猫咪抬爪配合后向室内迈步",
                    "environmentChange": "湿爪印逐渐减少",
                    "transition": "continuous",
                    "lens": {
                        "focalLengthEquivalent": "35mm",
                        "cameraHeight": "儿童腰部",
                        "cameraAngle": "轻微俯拍",
                        "perspectiveIntent": "看清手、猫爪和脚垫",
                    },
                    "composition": {
                        "subjectPlacement": "儿童左、猫咪右",
                        "foreground": "毛巾",
                        "middleGround": "儿童与猫咪",
                        "background": "玄关",
                        "screenDirection": "从左向右",
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
                        "incoming": "承接进门动作",
                        "outgoing": "保持向右运动",
                        "sharedVisualElement": "同一毛巾和脚垫",
                        "finalFrame": "孩子折好毛巾，猫咪继续向右迈步",
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
                    "generationRisks": [
                        {"code": "paw_contact", "message": "避免手爪融合"}
                    ],
                }
            ],
        }
    )


def _service() -> StudioService:
    return StudioService(
        MemoryStudioRepository(),
        provider_runtime=replace(
            ProviderRuntime.from_env(segment_reference_publishing_ready=False),
            paid_calls_enabled=True,
        ),
    )


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
    prompt = str(first.frozen_input["prompt"])
    assert "标题使用4至12个汉字" in prompt
    assert "摘要不超过60个汉字" in prompt
    assert "不得复述用户原文" in prompt
    assert "围绕……展开" in prompt
    assert "environmentIntent只描述空间、天气、家具、道具、构图和光线" in prompt
    assert "不得包含儿童、猫咪或其他角色的动作" in prompt
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
    assert preview.input_snapshot is not None
    assert preview.input_snapshot.state == "preview"
    assert preview.input_snapshot.video.duration_seconds == 12

    command = GenerationCommand(
        expectedInputHash=preview.input_hash,
        idempotencyKey="video-rainy-paws-1",
    )
    first_video_job = service.create_video_job(project.id, command)
    same_video_job = service.create_video_job(project.id, command)

    assert first_video_job.id == same_video_job.id
    assert first_video_job.input_snapshot is not None
    assert first_video_job.input_snapshot.state == "submitted"
    assert first_video_job.input_snapshot.prompt == preview.prompt
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
                idempotencyKey="video-stale",
            ),
        )


def test_director_planner_job_freezes_story_canon_assets_and_professional_schema() -> None:
    service = _service()
    project = _project(service)
    planner_job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="director-source-story",
        ),
    )
    proposal = service.complete_planner_job(planner_job.id, _proposal())
    story = service.adopt_proposal(project.id, proposal.id)
    environment = service.register_asset(
        project.id,
        role="environment",
        sha256="7" * 64,
    )
    service.select_asset(project.id, slot="environment", asset_id=environment.id)

    job = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(idempotencyKey="professional-director-plan"),
    )

    assert job.kind == "plan_shots"
    assert service.workspace(project.id)["latestDirectorJob"]["id"] == str(job.id)
    assert job.frozen_input["storyVersionId"] == str(story.id)
    assert job.frozen_input["directorPromptRevision"] == "catflow-director-v3"
    assert "不得输出空占位镜头、备用镜头或修订镜头" in job.frozen_input["prompt"]
    assert job.frozen_input["referenceRoles"] == [
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
        "style_board",
    ]
    assert "初始状态—运动路径—结束状态" in str(job.frozen_input["prompt"])
    assert "不得复述故事原文" in str(job.frozen_input["prompt"])
    assert "围绕……展开" in str(job.frozen_input["prompt"])
    schema = job.frozen_input["outputSchema"]
    assert isinstance(schema, dict)
    assert "directorTreatment" in str(schema)
    assert "childBlocking" in str(schema)


def test_repository_idempotency_input_conflict_has_a_stable_error_code() -> None:
    repository = MemoryStudioRepository()
    now = datetime.now(UTC)
    first = JobDto(
        id=uuid.uuid4(),
        projectId=uuid.uuid4(),
        kind="plan_shots",
        status="failed",
        inputHash="a" * 64,
        idempotencyKey="stable-conflict-key",
        frozenInput={},
        resultAssetIds=[],
        createdAt=now,
        updatedAt=now,
    )
    repository.create_job(first)

    with pytest.raises(StudioConflictError) as caught:
        repository.create_job(
            first.model_copy(update={"id": uuid.uuid4(), "input_hash": "b" * 64})
        )

    assert getattr(caught.value, "code", None) == "idempotency_input_conflict"


def test_director_completion_creates_a_candidate_without_replacing_the_current_plan() -> None:
    service = _service()
    project = _project(service)
    planner_job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="candidate-source-story",
        ),
    )
    proposal = service.complete_planner_job(planner_job.id, _proposal())
    story = service.adopt_proposal(project.id, proposal.id)
    environment = service.register_asset(project.id, role="environment", sha256="8" * 64)
    service.select_asset(project.id, slot="environment", asset_id=environment.id)
    base = service.create_shot_plan(
        project.id,
        ShotPlanDraft(
            sourceStoryVersionId=story.id,
            sourceSelectionHash=service.current_selection_hash(project.id),
            clip=LifeClipSpec(
                durationSeconds=12,
                aspectRatio="9:16",
                microEvent="雨天擦爪",
                childAction="孩子擦猫爪",
                catActionOrObservation="猫咪抬爪配合",
                visibleCauseAndEffect="湿脚印减少",
                warmEnding="猫咪继续走进室内",
                dialoguePolicy="none",
                environmentIntent="雨天玄关",
            ),
            shots=[
                ShotSpec(
                    id="manual-shot-1",
                    order=1,
                    durationSeconds=12,
                    framing="中景",
                    cameraMovement="固定",
                    childAction="孩子擦猫爪",
                    catAction="猫咪抬爪配合",
                    environmentChange="湿脚印减少",
                    transition="continuous",
                )
            ],
        ),
    )
    job = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(idempotencyKey="candidate-director-plan"),
    )

    candidate = service.complete_shot_plan_job(job.id, _director_payload())

    active = next(plan for plan in service.list_shot_plans(project.id) if plan.active)
    assert active.id == base.id
    assert candidate.active is False
    assert candidate.review_status == "candidate"
    assert candidate.producing_job_id == job.id
    assert candidate.base_shot_plan_version_id == base.id
    assert candidate.revision == 2

    adopted = service.activate_shot_plan(
        project.id,
        candidate.id,
        ShotPlanActivationCommand(
            expectedActiveShotPlanVersionId=base.id,
            idempotencyKey="adopt-director-candidate",
        ),
    )
    assert adopted.active is True
    assert adopted.review_status == "accepted"
    historical = next(
        plan for plan in service.list_shot_plans(project.id) if plan.id == base.id
    )
    assert historical.active is False


def test_paid_director_result_can_be_recovered_without_another_provider_job() -> None:
    repository = MemoryStudioRepository()
    service = StudioService(
        repository,
        provider_runtime=replace(
            ProviderRuntime.from_env(segment_reference_publishing_ready=False),
            paid_calls_enabled=True,
        ),
    )
    project = _project(service)
    planner_job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="recover-source-story",
        ),
    )
    proposal = service.complete_planner_job(planner_job.id, _proposal())
    story = service.adopt_proposal(project.id, proposal.id)
    environment = service.register_asset(project.id, role="environment", sha256="1" * 64)
    service.select_asset(project.id, slot="environment", asset_id=environment.id)
    base = service.create_shot_plan(
        project.id,
        ShotPlanDraft(
            sourceStoryVersionId=story.id,
            sourceSelectionHash=service.current_selection_hash(project.id),
            clip=LifeClipSpec(
                durationSeconds=12,
                aspectRatio="9:16",
                microEvent="雨天擦爪",
                childAction="孩子擦猫爪",
                catActionOrObservation="猫咪抬爪配合",
                visibleCauseAndEffect="湿脚印减少",
                warmEnding="猫咪继续走进室内",
                dialoguePolicy="none",
                environmentIntent="雨天玄关",
            ),
            shots=[
                ShotSpec(
                    id="manual-shot-1",
                    order=1,
                    durationSeconds=12,
                    framing="中景",
                    cameraMovement="固定",
                    childAction="孩子擦猫爪",
                    catAction="猫咪抬爪配合",
                    environmentChange="湿脚印减少",
                    transition="continuous",
                )
            ],
        ),
    )
    job = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(idempotencyKey="recover-director-plan"),
    )
    payload = _director_payload().model_dump(mode="json", by_alias=True)
    payload["shots"][0]["sound"]["objectEffects"] = ["一", "二", "三", "四"]
    payload["shots"][0]["blocking_note"] = "内嵌角色调度符合要求"
    repository._jobs[job.id] = job.model_copy(  # noqa: SLF001 - fixture controls persistence.
        update={
            "status": "failed",
            "provider_result": {"payload": payload, "responseId": "response-paid-once"},
            "error": {
                "code": "director_output_validation_failed",
                "message": "legacy strict validation failure",
            },
        }
    )

    attempt = service.list_shot_plan_generation_attempts(project.id)[0]
    assert attempt.result is not None
    assert attempt.result.disposition == "candidate_ready"
    assert {issue.code for issue in attempt.result.issues} == {
        "sound_detail_dense",
        "unknown_provider_field",
    }

    command = ShotPlanGenerationRecoveryCommand(idempotencyKey="recover-existing-result")
    candidate = service.recover_shot_plan_generation_result(project.id, job.id, command)
    repeated = service.recover_shot_plan_generation_result(project.id, job.id, command)

    assert repeated.id == candidate.id
    assert candidate.review_status == "candidate"
    assert candidate.active is False
    assert candidate.producing_job_id == job.id
    assert candidate.shots[0].sound is not None
    assert candidate.shots[0].sound.object_effects == ["一", "二", "三", "四"]
    assert next(plan for plan in service.list_shot_plans(project.id) if plan.active).id == base.id
    assert len(repository.list_project_jobs(project.id)) == 2


def test_incomplete_director_draft_can_be_corrected_without_another_provider_job() -> None:
    repository = MemoryStudioRepository()
    service = StudioService(
        repository,
        provider_runtime=replace(
            ProviderRuntime.from_env(segment_reference_publishing_ready=False),
            paid_calls_enabled=True,
        ),
    )
    project = _project(service)
    planner_job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="materialize-source-story",
        ),
    )
    proposal = service.complete_planner_job(planner_job.id, _proposal())
    service.adopt_proposal(project.id, proposal.id)
    environment = service.register_asset(project.id, role="environment", sha256="2" * 64)
    service.select_asset(project.id, slot="environment", asset_id=environment.id)
    job = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(idempotencyKey="materialize-director-plan"),
    )
    incomplete_payload = _director_payload().model_dump(mode="json", by_alias=True)
    del incomplete_payload["shots"][0]["catBlocking"]["endState"]
    repository._jobs[job.id] = job.model_copy(  # noqa: SLF001 - fixture controls persistence.
        update={
            "status": "succeeded",
            "provider_result": {"payload": incomplete_payload, "responseId": "response-paid-once"},
        }
    )

    attempt = service.list_shot_plan_generation_attempts(project.id)[0]
    assert attempt.result is not None
    assert attempt.result.disposition == "needs_input"
    assert any(issue.severity == "blocking" for issue in attempt.result.issues)
    original_job_count = len(repository.list_project_jobs(project.id))

    candidate = service.materialize_shot_plan_generation_result(
        project.id,
        job.id,
        ShotPlanGenerationMaterializeCommand(
            idempotencyKey="materialize-existing-result",
            payload=_director_payload(),
        ),
    )

    assert candidate.review_status == "candidate"
    assert candidate.active is False
    assert candidate.producing_job_id == job.id
    assert len(repository.list_project_jobs(project.id)) == original_job_count


def test_running_director_job_blocks_a_second_paid_submission() -> None:
    service = _service()
    project = _project(service)
    planner_job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="running-source-story",
        ),
    )
    proposal = service.complete_planner_job(planner_job.id, _proposal())
    service.adopt_proposal(project.id, proposal.id)
    environment = service.register_asset(project.id, role="environment", sha256="9" * 64)
    service.select_asset(project.id, slot="environment", asset_id=environment.id)
    first = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(idempotencyKey="running-director-one"),
    )

    with pytest.raises(StudioConflictError, match="already running"):
        service.create_shot_plan_generation_job(
            project.id,
            ShotPlanGenerationCommand(idempotencyKey="running-director-two"),
        )

    repeated = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(idempotencyKey="running-director-one"),
    )
    assert repeated.id == first.id


def test_video_prompt_compiles_professional_director_fields_in_execution_order() -> None:
    service = _service()
    project = _project(service)
    planner_job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="professional-prompt-story",
        ),
    )
    proposal = service.complete_planner_job(planner_job.id, _proposal())
    service.adopt_proposal(project.id, proposal.id)
    environment = service.register_asset(project.id, role="environment", sha256="7" * 64)
    service.select_asset(project.id, slot="environment", asset_id=environment.id)
    job = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(idempotencyKey="professional-prompt-plan"),
    )
    shot = ShotSpec(
        id="shot-1",
        order=1,
        durationSeconds=12,
        durationFrames=288,
        framing="中景",
        cameraMovement="缓慢跟随",
        childAction="孩子逐只擦干猫爪",
        catAction="猫咪抬爪后走上脚垫",
        environmentChange="湿爪印明显减少",
        transition="continuous",
        lens=LensDesign(
            focalLengthEquivalent="35mm",
            cameraHeight="儿童腰部高度",
            cameraAngle="轻微俯拍",
            perspectiveIntent="同时看清手、猫爪和水印",
        ),
        composition=CompositionDesign(
            subjectPlacement="孩子左侧，猫咪右侧",
            foreground="软毛巾",
            middleGround="孩子双手与猫爪",
            background="暖光玄关",
            screenDirection="从门口向室内",
            eyeLine="孩子看向猫爪",
        ),
        childBlocking=BlockingDesign(
            initialState="孩子站在脚垫旁",
            movementPath="屈膝蹲下并逐只擦拭",
            endState="孩子起身折好毛巾",
            microMotions=["重新握紧毛巾"],
        ),
        catBlocking=BlockingDesign(
            initialState="猫咪四足站在湿脚垫边缘",
            movementPath="依次抬爪并转移重心",
            endState="猫咪向室内迈出两步",
            microMotions=["尾巴自然摆动"],
        ),
        physicalChange=PhysicalChangeDesign(
            subject="猫爪和地面水印",
            before="潮湿并留下连续水印",
            after="猫爪擦干且水印减少",
        ),
        continuity=ContinuityDesign(
            incoming="承接猫咪进门动作",
            outgoing="猫咪继续走向室内",
            sharedVisualElement="同一块毛巾和脚垫",
            finalFrame="孩子折好毛巾，猫咪仍在向前迈步",
        ),
        lighting=LightingDesign(
            direction="右上方室内暖光",
            softness="柔和漫射",
            colorIntent="雨天冷暖平衡",
        ),
        sound=ShotSoundDesign(
            ambience=["轻雨声"],
            objectEffects=["毛巾摩擦声"],
            movementEffects=["猫爪轻落脚垫"],
            musicIntent="极轻木琴点音",
        ),
        directorIntent="用动作和物理变化呈现照顾感",
        generationRisks=[{"code": "paw_occlusion", "message": "避免手与猫爪融合"}],
    )
    payload = DirectorPlanPayload(
        targetDurationSeconds=12,
        directorTreatment=DirectorStoryTreatment(
            logline="孩子在雨天门边为猫咪擦干爪子",
            theme="温柔照顾",
            emotionalTone=["安静", "温暖"],
            visualMotif="湿爪印逐渐消失",
            spatialSetting="雨天玄关",
            emotionalArc=EmotionalArc(
                opening="发现水印",
                development="耐心擦拭",
                resolution="一起走进室内",
            ),
            microEvent=DirectorMicroEvent(
                trigger="猫咪湿爪进门",
                childIntent="保持室内干净并照顾猫咪",
                childAction="逐只擦干猫爪",
                catResponse="抬爪配合并向前迈步",
                visibleCauseAndEffect="水印明显减少",
                warmEnding="孩子折毛巾，猫咪继续走向室内",
            ),
            propStateChange=PropStateChange(
                initialState="毛巾展开且干燥",
                changedState="毛巾折好并带有湿痕",
            ),
            soundIntent="用雨声和毛巾摩擦声表达安静日常",
            endingImage="孩子折好毛巾，猫咪仍在迈步",
        ),
        shots=[shot],
    )
    candidate = service.complete_shot_plan_job(job.id, payload)
    service.activate_shot_plan(
        project.id,
        candidate.id,
        ShotPlanActivationCommand(
            expectedActiveShotPlanVersionId=None,
            idempotencyKey="activate-professional-plan",
        ),
    )

    prompt = service.preview_video_generation(project.id).prompt

    for expected in (
        "35mm",
        "孩子站在脚垫旁—屈膝蹲下并逐只擦拭—孩子起身折好毛巾",
        "猫咪四足站在湿脚垫边缘—依次抬爪并转移重心—猫咪向室内迈出两步",
        "潮湿并留下连续水印→猫爪擦干且水印减少",
        "孩子折好毛巾，猫咪仍在向前迈步",
        "轻雨声",
        "paw_occlusion：避免手与猫爪融合",
    ):
        assert expected in prompt


def test_selected_environment_is_scoped_to_its_project() -> None:
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
    assert "environment" not in service.current_selections(second.id)


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
            idempotencyKey="asset-cat-sample-1",
        ),
    )

    assert preview.kind == "episode_cat"
    assert job.kind == "generate_image"
    assert job.frozen_input["role"] == "episode_cat"
    assert all(reference["role"] != "style_source" for reference in job.frozen_input["references"])


def test_environment_generation_uses_story_intent_and_only_the_three_rendering_references() -> None:
    service = _service()
    project = _project(service)
    story = service.create_story(
        project.id,
        StoryCreateCommand(
            title="雨天擦爪",
            body="猫咪从雨里回家，孩子在玄关替它擦爪。",
            microEvent=_proposal().micro_event,
            targetDurationSeconds=12,
            dialoguePolicy="none",
            environmentIntent="雨天玄关，柔和暖光。",
        ),
    )
    previous_environment = service.register_asset(
        project.id,
        role="environment",
        sha256="8" * 64,
    )
    service.select_asset(project.id, slot="environment", asset_id=previous_environment.id)

    preview = service.preview_asset_generation(
        project.id, AssetGenerationPreviewCommand(kind="environment")
    )
    roles = [reference.role for reference in preview.references if reference.included]

    assert preview.image_input_snapshot is not None
    assert preview.image_input_snapshot.source_story_version_id == story.id
    assert preview.image_input_snapshot.environment_intent == "雨天玄关，柔和暖光。"
    assert preview.image_input_snapshot.subject_policy == "empty_scene"
    assert roles == ["style_board", "episode_child", "episode_cat"]
    assert "空场景" in preview.prompt
    assert "雨天玄关，柔和暖光" in preview.prompt
    assert "柔和暖光。。" not in preview.prompt
    assert "儿童" in preview.negative_prompt
    assert "猫咪" in preview.negative_prompt
    assert all(reference.asset_id != previous_environment.id for reference in preview.references)

    job = service.create_asset_generation_job(
        project.id,
        AssetGenerationCommand(
            kind="environment",
            expectedInputHash=preview.input_hash,
            idempotencyKey="environment-generation-rainy-paws",
        ),
    )
    assert job.image_input_snapshot is not None
    assert job.image_input_snapshot.state == "submitted"
    assert job.frozen_input["referenceRoles"] == [
        "style_board",
        "episode_child",
        "episode_cat",
    ]
    assert job.frozen_input["compiledProviderPrompt"] == (
        f"【生成目标】\n{preview.prompt}\n\n【必须避免】\n{preview.negative_prompt}"
    )


def test_environment_preview_requires_an_active_story() -> None:
    service = _service()
    project = _project(service)

    with pytest.raises(ValueError, match="active story"):
        service.preview_asset_generation(
            project.id, AssetGenerationPreviewCommand(kind="environment")
        )


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


def test_environment_diagnosis_checks_an_empty_scene_against_story_and_rendering_language() -> None:
    service = _service()
    project = _project(service)
    story = service.create_story(
        project.id,
        StoryCreateCommand(
            title="雨天擦爪",
            body="猫咪从雨里回家，孩子在玄关替它擦爪。",
            microEvent=_proposal().micro_event,
            targetDurationSeconds=12,
            dialoguePolicy="none",
            environmentIntent="雨天玄关，柔和暖光和吸水脚垫",
        ),
    )
    candidate = service.register_asset(
        project.id,
        role="environment",
        sha256="7" * 64,
    )

    job = service.create_image_diagnosis_job(
        project.id,
        ImageDiagnosisCommand(
            assetId=candidate.id,
            idempotencyKey="diagnose-empty-environment-1",
        ),
    )

    assert job.frozen_input["diagnosticSchema"] == "environment-quality-report-v2"
    assert job.frozen_input["subjectPolicy"] == "empty_scene"
    assert job.frozen_input["sourceStoryVersionId"] == str(story.id)
    assert job.frozen_input["environmentIntent"] == "雨天玄关，柔和暖光和吸水脚垫"
    assert [reference["role"] for reference in job.frozen_input["references"]] == [
        "style_board",
        "episode_child",
        "episode_cat",
    ]
    assert set(job.frozen_input["outputSchema"]["properties"]) == {
        "intentMatch",
        "characterFree",
        "styleMatch",
        "stagingSpace",
        "technical",
        "warnings",
    }
    assert "不应出现" in job.frozen_input["prompt"]
