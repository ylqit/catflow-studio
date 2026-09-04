from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from catflow.application.continuity import (
    EpisodeContinuityConfirmCommand,
    EpisodeContinuityKeyframesCommand,
    SeriesAssetBindingCommand,
    SeriesAssetBindingsPatchCommand,
)
from catflow.application.project_library import ProjectLibraryQuery
from catflow.application.provider_config import ProviderRuntime
from catflow.application.series import (
    SeriesCreateCommand,
    SeriesEpisodeMaterializeCommand,
    SeriesEpisodeStoryGenerationCommand,
    SeriesPlanActivationCommand,
    SeriesPlanDraft,
    SeriesPlanGenerationCommand,
    SeriesPlanMaterializeCommand,
    normalize_series_plan_result,
)
from catflow.application.service import (
    FinalSelectionCommand,
    GenerationCommand,
    StoryCreateCommand,
    StudioService,
)
from catflow.domain.models import (
    LifeClipSpec,
    LifeStoryProposalDraft,
    MicroEvent,
    ShotPlanDraft,
    ShotSpec,
)
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def _service() -> StudioService:
    return StudioService(
        MemoryStudioRepository(),
        provider_runtime=replace(
            ProviderRuntime.from_env(segment_reference_publishing_ready=False),
            paid_calls_enabled=True,
        ),
    )


def _series_command(*, episode_count: int = 3) -> SeriesCreateCommand:
    return SeriesCreateCommand(
        title="森林野餐",
        premise="孩子和猫咪从准备野餐到返程的连续一天",
        narrativeMode="continuous",
        plannedEpisodeCount=episode_count,
        defaultEpisodeDurationSeconds=12,
        worldSetting="家与附近森林，季节为初夏",
        emotionalDirection="从期待到满足，再温暖返程",
        endingGoal="一起沿森林小路回家",
        recurringElements=["野餐篮", "毛线球"],
        mustKeep=["同一位孩子", "同一只猫咪"],
        mustAvoid=["危险动作"],
    )


def _plan() -> SeriesPlanDraft:
    return SeriesPlanDraft.model_validate(
        {
            "seriesBible": {
                "logline": "孩子和猫咪共同完成一次森林野餐。",
                "centralTheme": "陪伴与协作",
                "narrativeMode": "continuous",
                "worldRules": ["同一天内发生", "道具状态连续"],
                "emotionalArc": {
                    "opening": "期待出发",
                    "development": "共同玩耍",
                    "climax": "发现毛线球",
                    "resolution": "收拾返程",
                },
                "recurringLocations": [
                    {"key": "home", "name": "家中厨房", "description": "清晨暖光"},
                    {"key": "forest", "name": "森林草地", "description": "树荫与斑驳阳光"},
                ],
                "recurringProps": [
                    {"key": "basket", "name": "野餐篮", "continuityRule": "内容物随剧情变化"},
                    {"key": "yarn", "name": "毛线球", "continuityRule": "由猫咪带入野餐篮"},
                ],
                "wardrobeRules": ["三集保持同一套夏季服装"],
                "continuityRules": ["下一集开场承接上一集结尾"],
                "visualMotifs": ["篮子与毛线球"],
                "soundMotifs": ["轻风与鸟鸣"],
                "forbiddenChanges": ["角色身份变化"],
            },
            "episodes": [
                {
                    "order": 1,
                    "title": "准备野餐",
                    "targetDurationSeconds": 12,
                    "premise": "在家装好野餐篮",
                    "openingState": "空篮子放在桌上",
                    "trigger": "孩子开始装食物",
                    "childIntent": "准备出发",
                    "childAction": "依次放入食物并背起背包",
                    "catResponse": "把毛线球放入篮子",
                    "visibleChange": "空篮子变满",
                    "endingState": "一人一猫走出家门",
                    "continuityCarryover": ["篮中已有毛线球"],
                    "recurringLocationKeys": ["home"],
                    "recurringPropKeys": ["basket", "yarn"],
                    "productionWarnings": [],
                },
                {
                    "order": 2,
                    "title": "快乐野餐",
                    "targetDurationSeconds": 12,
                    "premise": "在森林草地铺开野餐垫",
                    "openingState": "一人一猫到达森林",
                    "trigger": "孩子打开野餐篮",
                    "childIntent": "开始野餐",
                    "childAction": "铺开垫子并取出食物",
                    "catResponse": "抱住毛线球滚动",
                    "visibleChange": "篮中物品铺到垫上",
                    "endingState": "两人在树荫下休息",
                    "continuityCarryover": ["野餐物品已经取出"],
                    "recurringLocationKeys": ["forest"],
                    "recurringPropKeys": ["basket", "yarn"],
                    "productionWarnings": [],
                },
                {
                    "order": 3,
                    "title": "快乐返程",
                    "targetDurationSeconds": 12,
                    "premise": "收拾草地并回家",
                    "openingState": "夕阳下野餐结束",
                    "trigger": "孩子开始收拾",
                    "childIntent": "不留下垃圾",
                    "childAction": "把物品和垃圾分别装好",
                    "catResponse": "叼来一个空瓶子",
                    "visibleChange": "草地恢复整洁",
                    "endingState": "一人一猫沿小路回家",
                    "continuityCarryover": [],
                    "recurringLocationKeys": ["forest"],
                    "recurringPropKeys": ["basket"],
                    "productionWarnings": [],
                },
            ],
        }
    )


@pytest.mark.parametrize("episode_count", [1, 31])
def test_series_creation_rejects_episode_counts_outside_two_to_thirty(
    episode_count: int,
) -> None:
    with pytest.raises(ValidationError):
        _series_command(episode_count=episode_count)


def test_series_plan_is_one_scoped_job_and_does_not_create_projects() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)

    command = SeriesPlanGenerationCommand(
        expectedInputHash=preview.input_hash,
        idempotencyKey="series-plan-forest-picnic",
    )
    first = service.create_series_plan_job(series.id, command)
    second = service.create_series_plan_job(series.id, command)

    assert first.id == second.id
    assert first.project_id is None
    assert first.series_id == series.id
    assert first.kind == "plan_series"
    assert service.list_projects() == []


def test_series_plan_requires_adoption_before_episodes_exist() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="series-plan-candidate",
        ),
    )

    candidate = service.complete_series_plan_job(job.id, _plan())

    assert candidate.status == "candidate"
    assert candidate.active is False
    assert service.list_series_episodes(series.id) == []
    assert service.list_projects() == []

    accepted = service.activate_series_plan(
        series.id,
        candidate.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="accept-series-plan",
        ),
    )
    episodes = service.list_series_episodes(series.id)

    assert accepted.status == "accepted"
    assert accepted.active is True
    assert [episode.order for episode in episodes] == [1, 2, 3]
    assert all(episode.project_id is None for episode in episodes)
    assert service.list_projects() == []


def test_episode_project_is_created_lazily_and_idempotently() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="series-plan-materialize",
        ),
    )
    candidate = service.complete_series_plan_job(job.id, _plan())
    service.activate_series_plan(
        series.id,
        candidate.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="accept-materialize-plan",
        ),
    )
    episode = service.list_series_episodes(series.id)[0]

    command = SeriesEpisodeMaterializeCommand(idempotencyKey="materialize-episode-one")
    first = service.materialize_series_episode(series.id, episode.id, command)
    second = service.materialize_series_episode(series.id, episode.id, command)

    assert first.id == second.id
    assert first.title == "第1集 · 准备野餐"
    assert len(service.list_projects()) == 1
    refreshed = service.list_series_episodes(series.id)[0]
    assert refreshed.project_id == first.id

    library_item = service.project_library(ProjectLibraryQuery(limit=12)).items[0]
    assert library_item.series is not None
    assert library_item.series.series_id == series.id
    assert library_item.series.series_title == "森林野餐"
    assert library_item.series.episode_id == episode.id
    assert library_item.series.episode_order == 1


def test_series_plan_with_missing_episode_is_saved_for_manual_completion() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="series-plan-needs-input",
        ),
    )
    incomplete = _plan().model_copy(update={"episodes": _plan().episodes[:2]})

    candidate = service.complete_series_plan_job(job.id, incomplete)

    assert candidate.disposition == "needs_input"
    assert candidate.status == "candidate"
    assert candidate.active is False
    assert any(issue.code == "episode_count_mismatch" for issue in candidate.issues)


def test_series_provider_result_preserves_missing_text_and_extra_notes_for_review() -> None:
    payload = _plan().model_dump(mode="json", by_alias=True)
    del payload["seriesBible"]["emotionalArc"]["resolution"]
    payload["plannerNote"] = "建议最后一集保持夕阳方向一致"

    normalized = normalize_series_plan_result(
        payload,
        expected_episode_count=3,
        narrative_mode="continuous",
    )

    assert normalized.plan is not None
    assert normalized.disposition == "needs_input"
    assert normalized.plan.series_bible.emotional_arc.resolution == ""
    assert {issue.code for issue in normalized.issues} == {
        "provider_extra_field",
        "required_content_missing",
    }


def test_incomplete_series_plan_can_be_completed_without_another_provider_job() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="series-plan-to-complete",
        ),
    )
    incomplete = _plan().model_copy(update={"episodes": _plan().episodes[:2]})
    generated = service.complete_series_plan_job(job.id, incomplete)

    completed = service.materialize_series_plan(
        series.id,
        generated.id,
        SeriesPlanMaterializeCommand(
            basePlanVersionId=generated.id,
            plan=_plan(),
            idempotencyKey="complete-series-plan-locally",
        ),
    )
    repeated = service.materialize_series_plan(
        series.id,
        generated.id,
        SeriesPlanMaterializeCommand(
            basePlanVersionId=generated.id,
            plan=_plan(),
            idempotencyKey="complete-series-plan-locally",
        ),
    )

    assert completed.id == repeated.id
    assert completed.id != generated.id
    assert completed.producing_job_id is None
    assert completed.base_plan_version_id == generated.id
    assert completed.disposition == "candidate_ready"
    assert generated.status == "superseded"
    assert len(service.list_series_jobs(series.id)) == 1


def test_replanning_preserves_stable_episode_ids_and_materialized_project() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    first_job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="initial-series-plan",
        ),
    )
    first_plan = service.complete_series_plan_job(first_job.id, _plan())
    service.activate_series_plan(
        series.id,
        first_plan.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="accept-initial-series-plan",
        ),
    )
    before = service.list_series_episodes(series.id)
    project = service.materialize_series_episode(
        series.id,
        before[0].id,
        SeriesEpisodeMaterializeCommand(idempotencyKey="materialize-before-replan"),
    )

    second_preview = service.preview_series_plan(series.id)
    second_job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=second_preview.input_hash,
            idempotencyKey="replanned-series-plan",
        ),
    )
    changed_plan = _plan().model_copy(
        update={
            "episodes": [
                _plan().episodes[0].model_copy(update={"title": "重新整理野餐篮"}),
                *_plan().episodes[1:],
            ]
        }
    )
    candidate = service.complete_series_plan_job(second_job.id, changed_plan)
    service.activate_series_plan(
        series.id,
        candidate.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=first_plan.id,
            idempotencyKey="accept-replanned-series-plan",
        ),
    )

    after = service.list_series_episodes(series.id)
    assert [item.id for item in after] == [item.id for item in before]
    assert after[0].project_id == project.id
    assert after[0].outline.title == "重新整理野餐篮"
    assert after[0].active_outline_version_id != before[0].active_outline_version_id


def test_episode_story_generation_is_previewed_and_landed_as_candidate() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    plan_job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="episode-story-series-plan",
        ),
    )
    candidate = service.complete_series_plan_job(plan_job.id, _plan())
    service.activate_series_plan(
        series.id,
        candidate.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="episode-story-accept-plan",
        ),
    )
    episode = service.list_series_episodes(series.id)[0]
    project = service.materialize_series_episode(
        series.id,
        episode.id,
        SeriesEpisodeMaterializeCommand(idempotencyKey="episode-story-project"),
    )

    notes = "突出猫咪把毛线球放进篮子的动作"
    story_preview = service.preview_series_episode_story(
        series.id, episode.id, additional_notes=notes
    )
    command = SeriesEpisodeStoryGenerationCommand(
        expectedInputHash=story_preview.input_hash,
        additionalNotes=notes,
        idempotencyKey="episode-story-generation",
    )
    first = service.create_series_episode_story_job(series.id, episode.id, command)
    second = service.create_series_episode_story_job(series.id, episode.id, command)

    assert first.id == second.id
    assert first.kind == "plan_series_episode"
    assert first.project_id == project.id
    assert first.series_id is None
    assert service.list_series_jobs(series.id)[0].id == first.id
    assert service.get_planner(project.id).latest_job is not None
    assert service.get_planner(project.id).latest_job.id == first.id
    proposal = service.complete_series_episode_story_job(
        first.id,
        LifeStoryProposalDraft(
            title="准备野餐",
            summary="猫咪把毛线球也放进野餐篮。",
            body="孩子装好食物，猫咪叼来毛线球放进篮子，两人一起出门。",
            trigger="孩子打开空野餐篮",
            childAction="孩子依次装好食物并蹲下摸摸猫咪",
            catResponse="猫咪叼来毛线球并放进篮子",
            visibleChange="空篮子装满食物并多出毛线球",
            warmEnding="孩子背起背包，猫咪跟着走出家门",
            targetDurationSeconds=12,
            dialoguePolicy="none",
            environmentIntent="清晨家中餐桌旁的空场景，野餐篮在桌上",
            propIntent="野餐篮、食物、毛线球和背包",
        ),
    )

    assert proposal.project_id == project.id
    assert proposal.status == "draft"
    assert service.list_stories(project.id) == []


def test_series_plan_creates_planned_continuity_and_confirmation_is_versioned() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="continuity-series-plan",
        ),
    )
    candidate = service.complete_series_plan_job(job.id, _plan())
    service.activate_series_plan(
        series.id,
        candidate.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="continuity-accept-plan",
        ),
    )
    episodes = service.list_series_episodes(series.id)
    continuity = service.get_series_episode_continuity(series.id, episodes[1].id)

    assert continuity.previous_episode_id == episodes[0].id
    assert continuity.incoming is not None
    assert continuity.incoming.source == "planned"
    assert continuity.incoming.confirmed is False
    assert "一人一猫走出家门" in continuity.incoming.state.child_state

    confirmed = service.confirm_series_episode_continuity(
        series.id,
        episodes[1].id,
        EpisodeContinuityConfirmCommand(
            direction="incoming",
            state=continuity.incoming.state,
            decisions={"wardrobe": "inherit", "location": "adjust"},
            expectedSnapshotId=continuity.incoming.id,
            idempotencyKey="confirm-episode-two-continuity",
        ),
    )
    repeated = service.confirm_series_episode_continuity(
        series.id,
        episodes[1].id,
        EpisodeContinuityConfirmCommand(
            direction="incoming",
            state=continuity.incoming.state,
            decisions={"wardrobe": "inherit", "location": "adjust"},
            expectedSnapshotId=continuity.incoming.id,
            idempotencyKey="confirm-episode-two-continuity",
        ),
    )

    assert confirmed.id == repeated.id
    assert confirmed.source == "confirmed"
    assert confirmed.confirmed is True
    assert confirmed.id != continuity.incoming.id


def test_series_shared_assets_are_bound_without_copying_media() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    canon = service.current_canon()
    style = canon.fixed_assets["style_board"]

    bindings = service.update_series_assets(
        series.id,
        SeriesAssetBindingsPatchCommand(
            bindings=[
                SeriesAssetBindingCommand(
                    bindingKey="series-style",
                    role="style_board",
                    assetId=style.id,
                )
            ]
        ),
    )
    repeated = service.update_series_assets(
        series.id,
        SeriesAssetBindingsPatchCommand(
            bindings=[
                SeriesAssetBindingCommand(
                    bindingKey="series-style",
                    role="style_board",
                    assetId=style.id,
                )
            ]
        ),
    )

    assert len(bindings) == 1
    assert repeated[0].id == bindings[0].id
    assert bindings[0].asset_id == style.id
    assert bindings[0].asset_sha256 == style.sha256
    assert len(service.list_series_assets(series.id)) == 1


def test_approving_series_episode_final_enqueues_one_local_continuity_frame_job() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    plan_job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="continuity-frame-series-plan",
        ),
    )
    candidate = service.complete_series_plan_job(plan_job.id, _plan())
    service.activate_series_plan(
        series.id,
        candidate.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="continuity-frame-accept-plan",
        ),
    )
    episode = service.list_series_episodes(series.id)[0]
    project = service.materialize_series_episode(
        series.id,
        episode.id,
        SeriesEpisodeMaterializeCommand(idempotencyKey="continuity-frame-project"),
    )
    final = service.register_asset(
        project.id,
        role="final",
        media_type="video",
        sha256="9" * 64,
        metadata={"durationFrames": 288},
    )

    first = service.approve_final(project.id, FinalSelectionCommand(assetId=final.id))
    second = service.approve_final(project.id, FinalSelectionCommand(assetId=final.id))
    jobs = [
        job
        for job in service.list_project_jobs(project.id)
        if job.kind == "extract_continuity_frames"
    ]

    assert first.asset_id == second.asset_id == final.id
    assert len(jobs) == 1
    assert jobs[0].provider == "local_ffmpeg"
    assert jobs[0].expected_cost_micros == 0
    assert jobs[0].frozen_input["seriesEpisodeId"] == str(episode.id)
    assert jobs[0].frozen_input["sourceVideoAssetId"] == str(final.id)
    assert jobs[0].frozen_input["keyframeSeconds"] == [3.0, 9.0]


def test_episode_keyframes_are_limited_to_two_project_images() -> None:
    with pytest.raises(ValidationError):
        EpisodeContinuityKeyframesCommand(
            assetIds=[
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
                "00000000-0000-0000-0000-000000000003",
            ]
        )


def test_episode_continuity_frames_keep_tail_and_selected_keyframes_separate() -> None:
    service = _service()
    series = service.create_story_series(_series_command())
    preview = service.preview_series_plan(series.id)
    job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash,
            idempotencyKey="continuity-images-series-plan",
        ),
    )
    candidate = service.complete_series_plan_job(job.id, _plan())
    service.activate_series_plan(
        series.id,
        candidate.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="continuity-images-accept-plan",
        ),
    )
    episode = service.list_series_episodes(series.id)[0]
    project = service.materialize_series_episode(
        series.id,
        episode.id,
        SeriesEpisodeMaterializeCommand(idempotencyKey="continuity-images-project"),
    )
    final = service.register_asset(
        project.id,
        role="final",
        media_type="video",
        sha256="8" * 64,
        metadata={"durationFrames": 288},
    )
    service.approve_final(project.id, FinalSelectionCommand(assetId=final.id))
    common = {
        "seriesEpisodeId": str(episode.id),
        "sourceVideoAssetId": str(final.id),
    }
    tail = service.register_asset(
        project.id,
        role="episode_last_frame",
        sha256="7" * 64,
        metadata=common,
    )
    keyframe_one = service.register_asset(
        project.id,
        role="episode_keyframe",
        sha256="6" * 64,
        metadata={**common, "timestampSeconds": 3.0},
    )
    keyframe_two = service.register_asset(
        project.id,
        role="episode_keyframe",
        sha256="5" * 64,
        metadata={**common, "timestampSeconds": 9.0},
    )

    selected = service.select_episode_continuity_keyframes(
        series.id,
        episode.id,
        EpisodeContinuityKeyframesCommand(assetIds=[keyframe_two.id, keyframe_one.id]),
    )
    frames = service.get_episode_continuity_frames(series.id, episode.id)

    assert [item.id for item in selected] == [keyframe_two.id, keyframe_one.id]
    assert frames.source_video_asset_id == final.id
    assert frames.last_frame is not None and frames.last_frame.id == tail.id
    assert {item.id for item in frames.candidates} == {keyframe_one.id, keyframe_two.id}
    assert [item.id for item in frames.selected_keyframes] == [
        keyframe_two.id,
        keyframe_one.id,
    ]


def test_previous_episode_video_is_previewed_off_and_frozen_only_after_opt_in() -> None:
    service = StudioService(
        MemoryStudioRepository(),
        provider_runtime=replace(
            ProviderRuntime.from_env(segment_reference_publishing_ready=True),
            paid_calls_enabled=True,
            maximum_video_input_references=3,
        ),
    )
    series = service.create_story_series(_series_command())
    plan_preview = service.preview_series_plan(series.id)
    plan_job = service.create_series_plan_job(
        series.id,
        SeriesPlanGenerationCommand(
            expectedInputHash=plan_preview.input_hash,
            idempotencyKey="video-reference-series-plan",
        ),
    )
    plan = service.complete_series_plan_job(plan_job.id, _plan())
    service.activate_series_plan(
        series.id,
        plan.id,
        SeriesPlanActivationCommand(
            expectedActivePlanVersionId=None,
            idempotencyKey="video-reference-accept-plan",
        ),
    )
    episodes = service.list_series_episodes(series.id)
    previous_project = service.materialize_series_episode(
        series.id,
        episodes[0].id,
        SeriesEpisodeMaterializeCommand(idempotencyKey="video-reference-episode-one"),
    )
    current_project = service.materialize_series_episode(
        series.id,
        episodes[1].id,
        SeriesEpisodeMaterializeCommand(idempotencyKey="video-reference-episode-two"),
    )
    previous_final = service.register_asset(
        previous_project.id,
        role="final",
        media_type="video",
        sha256="9" * 64,
        metadata={"durationSeconds": 12},
    )
    service.approve_final(
        previous_project.id, FinalSelectionCommand(assetId=previous_final.id)
    )
    continuity = service.get_series_episode_continuity(series.id, episodes[1].id)
    assert continuity.incoming is not None
    service.confirm_series_episode_continuity(
        series.id,
        episodes[1].id,
        EpisodeContinuityConfirmCommand(
            direction="incoming",
            state=continuity.incoming.state,
            decisions={"location": "inherit"},
            expectedSnapshotId=continuity.incoming.id,
            idempotencyKey="video-reference-confirm-continuity",
        ),
    )
    story = service.create_story(
        current_project.id,
        StoryCreateCommand(
            title="快乐野餐",
            body="孩子和猫咪到达森林后铺开野餐垫。",
            microEvent=MicroEvent(
                trigger="孩子打开野餐篮",
                childAction="孩子铺开垫子并取出食物",
                catResponse="猫咪抱住毛线球滚动",
                visibleChange="篮中物品铺到垫上",
                warmEnding="一人一猫在树荫下休息",
            ),
            targetDurationSeconds=12,
            dialoguePolicy="none",
            environmentIntent="森林草地与树荫暖光",
        ),
    )
    environment = service.register_asset(
        current_project.id, role="environment", sha256="8" * 64
    )
    service.select_asset(
        current_project.id, slot="environment", asset_id=environment.id
    )
    shot_plan = service.create_shot_plan(
        current_project.id,
        ShotPlanDraft(
            sourceStoryVersionId=story.id,
            sourceSelectionHash=service.current_selection_hash(current_project.id),
            clip=LifeClipSpec(
                durationSeconds=12,
                aspectRatio="9:16",
                microEvent="打开野餐篮",
                childAction="铺开垫子并取出食物",
                catActionOrObservation="抱住毛线球滚动",
                visibleCauseAndEffect="篮中物品铺到垫上",
                warmEnding="一人一猫在树荫下休息",
                dialoguePolicy="none",
                environmentIntent="森林草地与树荫暖光",
            ),
            shots=[
                ShotSpec(
                    id="shot-1",
                    order=1,
                    durationSeconds=12,
                    framing="中景",
                    cameraMovement="缓慢跟随",
                    childAction="铺开垫子并取出食物",
                    catAction="抱住毛线球滚动",
                    environmentChange="篮中物品铺到垫上",
                    transition="continuous",
                )
            ],
        ),
    )

    default_preview = service.preview_video_generation(current_project.id)
    opted_in_preview = service.preview_video_generation(
        current_project.id, include_previous_episode_video=True
    )

    assert default_preview.video_references[0].asset_id == previous_final.id
    assert default_preview.video_references[0].included is False
    assert opted_in_preview.video_references[0].included is True
    assert default_preview.input_hash != opted_in_preview.input_hash
    assert "用户已明确启用上一集完整成片" in opted_in_preview.prompt
    assert "用户已明确启用上一集完整成片" in next(
        section.content
        for section in opted_in_preview.prompt_sections
        if section.key == "ending_constraints"
    )
    assert opted_in_preview.prompt == "\n\n".join(
        f"【{section.title}】\n{section.content}"
        for section in opted_in_preview.prompt_sections
    )
    assert shot_plan.id == opted_in_preview.shot_plan_version_id

    job = service.create_video_job(
        current_project.id,
        GenerationCommand(
            expectedInputHash=opted_in_preview.input_hash,
            idempotencyKey="video-reference-generate-episode-two",
            includePreviousEpisodeVideo=True,
        ),
    )

    assert job.frozen_input["previousEpisodeVideoAssetId"] == str(previous_final.id)
    assert job.input_snapshot is not None
    assert [item.asset_id for item in job.input_snapshot.video_references] == [
        previous_final.id
    ]
