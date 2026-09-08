from __future__ import annotations

import uuid

import pytest

from catflow.application import service as contracts
from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import SegmentRepairPreviewCommand
from catflow.domain.video_repairs import (
    CandidatePlacement,
    FrameRange,
    build_base_timeline,
    build_candidate_trial,
    validate_issue_range,
)
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def test_one_frame_issue_is_valid_independently_of_provider_minimum_duration() -> None:
    issue = FrameRange(startFrame=180, endFrame=181)
    command = SegmentRepairPreviewCommand(
        baseVideoAssetId=uuid.uuid4(), issueRange=issue, instruction="饼干留在篮内。"
    )
    validate_issue_range(command.issue_range, total_frames=289)
    assert command.issue_range.duration_frames == 1


def prepared_video():
    repo = MemoryStudioRepository()
    service = contracts.StudioService(
        repo,
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="test",
            image_model="test",
            video_model="test",
            diagnostic_model="test",
            capability_revision="test-v1",
            paid_calls_enabled=True,
            maximum_video_references=5,
            segment_reference_publishing_ready=True,
        ),
    )
    project = service.create_project(
        contracts.ProjectCreate(
            title="编辑不等于采用",
            theme="野餐",
            targetDurationSeconds=12,
        )
    )
    video = service.register_asset(
        project.id,
        role="video",
        media_type="video",
        sha256="a" * 64,
        storage_key="test/video.mp4",
        byte_size=100,
        metadata={"durationFrames": 289, "frameRateNumerator": 24, "frameRateDenominator": 1},
    )
    return service, repo, project, video


def repair_draft():
    service, repo, project, video = prepared_video()
    environment = service.register_asset(
        project.id,
        role="environment",
        media_type="image",
        sha256="e" * 64,
        storage_key="test/environment.png",
        byte_size=100,
    )
    service.select_asset(project.id, slot="environment", asset_id=environment.id)
    draft = service.create_video_edit_draft(
        project.id,
        contracts.VideoEditDraftCreateCommand(
            sourceVideoAssetId=video.id,
            confirmCurrentReferences=True,
            idempotencyKey="repair-draft",
        ),
    )
    return service, repo, project, video, draft


@pytest.mark.parametrize("start,end", [(0, 1), (288, 289), (144, 289)])
def test_slipped_trial_preserves_length_and_audio_source_offsets(start, end):
    root = build_base_timeline(asset_id=uuid.uuid4(), sha256="a" * 64, total_frames=289)
    original = root.model_dump(mode="json", by_alias=True)
    trial = build_candidate_trial(
        root,
        issue_range=FrameRange(startFrame=start, endFrame=end),
        candidate_asset_id=uuid.uuid4(),
        candidate_sha256="b" * 64,
        repair_id=uuid.uuid4(),
        placement=CandidatePlacement(
            candidateSourceRange={"startFrame": 30, "endFrame": 30 + end - start},
            audioPolicy="use_candidate",
        ),
    )
    video = next(
        segment for segment in trial.video_segments if segment.origin == "repair_candidate"
    )
    audio = next(segment for segment in trial.audio.segments if segment.repair_id)
    assert (video.source_in_frame, audio.source_in_frame) == (30, 30)
    assert (
        trial.total_frames
        == sum(segment.duration_frames for segment in trial.audio.segments)
        == 289
    )
    assert root.model_dump(mode="json", by_alias=True) == original


def test_second_audio_edit_splits_existing_envelope_without_restarting_fade():
    root = build_base_timeline(asset_id=uuid.uuid4(), sha256="a" * 64, total_frames=289)
    first = build_candidate_trial(
        root,
        issue_range=FrameRange(startFrame=48, endFrame=144),
        candidate_asset_id=uuid.uuid4(),
        candidate_sha256="b" * 64,
        repair_id=uuid.uuid4(),
        placement=CandidatePlacement(
            candidateSourceRange={"startFrame": 24, "endFrame": 120},
            audioPolicy="use_candidate",
            fadeInMs=500,
            fadeOutMs=750,
        ),
    )
    second = build_candidate_trial(
        first,
        issue_range=FrameRange(startFrame=72, endFrame=96),
        candidate_asset_id=uuid.uuid4(),
        candidate_sha256="c" * 64,
        repair_id=uuid.uuid4(),
        placement=CandidatePlacement(
            candidateSourceRange={"startFrame": 0, "endFrame": 24}, audioPolicy="use_candidate"
        ),
    )
    retained = [segment for segment in second.audio.segments if segment.sha256 == "b" * 64]
    assert [(segment.source_in_frame, segment.duration_frames) for segment in retained] == [
        (24, 24),
        (72, 48),
    ]
    assert all(
        (segment.envelope_start_frame, segment.envelope_duration_frames, segment.fade_out_ms)
        == (24, 96, 750)
        for segment in retained
    )
    preserved = build_candidate_trial(
        first,
        issue_range=FrameRange(startFrame=288, endFrame=289),
        candidate_asset_id=uuid.uuid4(),
        candidate_sha256="c" * 64,
        repair_id=uuid.uuid4(),
        placement=CandidatePlacement(
            candidateSourceRange={"startFrame": 0, "endFrame": 1}, audioPolicy="preserve_current"
        ),
    )
    assert preserved.audio == first.audio


def test_free_trials_are_immutable_and_application_is_bound_to_completed_preview():
    service, repo, project, video, draft = repair_draft()
    base = repo.get_edit(draft.head_edit_version_id)
    inputs = contracts.SegmentRepairPreviewCommand(
        baseVideoAssetId=video.id,
        baseEditVersionId=base.id,
        editDraftId=draft.id,
        issueRange={"startFrame": 24, "endFrame": 72},
        instruction="抬起手再放下",
        audioMode="generate_candidate",
    )
    preview = service.preview_video_repair(project.id, inputs)
    paid = service.create_video_repair_job(
        project.id,
        contracts.SegmentRepairCreateCommand(
            **inputs.model_dump(by_alias=True),
            expectedInputHash=preview.input_hash,
            idempotencyKey="paid-mock-only",
        ),
    )
    candidate = service.register_asset(
        project.id,
        role="repair_candidate",
        media_type="video",
        sha256="b" * 64,
        metadata={"durationFrames": 120, "hasAudio": True},
    )
    repo.set_video_repair_status(
        paid.video_repair_id, status="candidate_ready", candidate_asset_id=candidate.id
    )
    repo._jobs[paid.id] = paid.model_copy(update={"status": "succeeded"})

    def trial(start, key):
        return service.create_draft_preview(
            project.id,
            draft.id,
            contracts.VideoDraftPreviewCommand(
                expectedEditVersionId=base.id,
                expectedTimelineHash=base.timeline_hash,
                repairId=paid.video_repair_id,
                placement={
                    "candidateSourceRange": {"startFrame": start, "endFrame": start + 48},
                    "audioPolicy": "use_candidate",
                },
                idempotencyKey=key,
            ),
        )

    first = trial(0, "trial-first")
    second = trial(12, "trial-second")
    assert first.frozen_input["edl"] != second.frozen_input["edl"]
    assert (
        service.get_video_repair(paid.video_repair_id).candidate_core_range
        == preview.candidate_core_range
    )
    assert repo.get_video_edit_draft(draft.id).head_edit_version_id == base.id
    apply = contracts.VideoDraftSaveCommand(
        expectedEditVersionId=base.id,
        expectedTimelineHash=base.timeline_hash,
        repairId=paid.video_repair_id,
        previewJobId=second.id,
        idempotencyKey="apply-completed-only",
    )
    with pytest.raises(contracts.StudioConflictError, match="已完成"):
        service.save_video_draft(project.id, draft.id, apply)
    materialized = service.register_asset(
        project.id,
        role="edit_preview",
        media_type="video",
        sha256="c" * 64,
        producing_job_id=second.id,
        metadata={"timelineHash": second.frozen_input["timelineHash"]},
    )
    repo._jobs[second.id] = second.model_copy(
        update={"status": "succeeded", "result_asset_ids": [materialized.id]}
    )
    saved = service.save_video_draft(project.id, draft.id, apply)
    assert (
        saved.format_version == 3
        and saved.edl.model_dump(mode="json", by_alias=True) == second.frozen_input["edl"]
    )
    assert service.save_video_draft(project.id, draft.id, apply).id == saved.id
    assert trial(1, "old-parent-still-viewable").provider == "local_ffmpeg"
    with pytest.raises(contracts.StudioConflictError, match="草稿"):
        service.save_video_draft(
            project.id, draft.id, apply.model_copy(update={"idempotency_key": "conflicting-apply"})
        )
    assert len([job for job in repo._jobs.values() if job.provider == "ark"]) == 1

    complete = service.register_asset(
        project.id,
        role="edit_preview",
        media_type="video",
        sha256="d" * 64,
        metadata={
            "editDraftId": str(draft.id),
            "editVersionId": str(saved.id),
            "timelineHash": saved.timeline_hash,
            "hasAudio": True,
        },
    )
    review_input = contracts.VideoReviewCreateCommand(
        assetId=complete.id,
        editVersionId=saved.id,
        timelineHash=saved.timeline_hash,
        checks=dict.fromkeys(contracts.VIDEO_REVIEW_KEYS, "pass"),
        idempotencyKey="review-exact-audio-version",
    )
    visual_only = service.create_video_review(project.id, review_input)
    with pytest.raises(contracts.StudioConflictError, match="声音意图"):
        service.require_video_review(project.id, complete.id, visual_only.id)
    with pytest.raises(contracts.StudioConflictError, match="rendered edit"):
        service.create_video_review(
            project.id, review_input.model_copy(update={"timeline_hash": base.timeline_hash})
        )
    sound_review = service.create_video_review(
        project.id,
        review_input.model_copy(
            update={
                "audio_checks": {"soundIntent": "pass", "sync": "pass", "continuity": "pass"},
                "idempotency_key": "review-exact-audio-approved",
            }
        ),
    )
    service.require_video_review(project.id, complete.id, sound_review.id)
    assert "video" not in repo.current_selections(project.id)


def test_strict_frame_preview_and_create_freeze_only_the_actual_references():
    service, repo, project, video, draft = repair_draft()
    inputs = contracts.SegmentRepairPreviewCommand(
        baseVideoAssetId=video.id,
        baseEditVersionId=draft.head_edit_version_id,
        editDraftId=draft.id,
        issueRange={"startFrame": 144, "endFrame": 289},
        instruction="保持手中物件",
        generationMode="from_frame",
        anchorStartFrame=130,
        audioMode="generate_candidate",
        soundDescription="衣物摩擦声",
        endStatePolicy="replace",
        desiredEndState="物件留在手中",
    )
    preview = service.preview_video_repair(project.id, inputs)
    assert preview.video_reference is None
    assert [reference.role for reference in preview.image_references] == ["first_frame"]
    assert preview.image_references[0].frame_number == 130
    assert preview.candidate_core_range.start_frame == 0
    assert preview.input_snapshot.video.generate_audio is True
    paid = service.create_video_repair_job(
        project.id,
        contracts.SegmentRepairCreateCommand(
            **inputs.model_dump(by_alias=True),
            expectedInputHash=preview.input_hash,
            idempotencyKey="strict-mode-mock-only",
        ),
    )
    assert paid.frozen_input["referenceAssetIds"] == []
    assert paid.frozen_input["generateAudio"] is True
    assert paid.frozen_input["videoReference"] is None
    assert paid.frozen_input["promptCompilerRevision"] == "segment-edit-v4"


def test_unselected_video_creates_inactive_draft_and_replays_same_request():
    service, repo, project, video = prepared_video()
    command = contracts.VideoEditDraftCreateCommand(
        sourceVideoAssetId=video.id,
        idempotencyKey="draft-once",
    )
    draft = service.create_video_edit_draft(project.id, command)
    repeated = service.create_video_edit_draft(project.id, command)
    assert repeated.id == draft.id
    edit = repo.get_edit(draft.head_edit_version_id)
    assert edit.edl.total_frames == 289
    assert edit.active is False
    assert "video" not in repo.current_selections(project.id)
    assert len(service.list_edits(project.id)) == 1


def test_draft_creation_does_not_accept_a_video_from_another_project():
    service, _, project, video = prepared_video()
    other = service.create_project(
        contracts.ProjectCreate(
            title="另一项目",
            theme="雨天",
            targetDurationSeconds=12,
        )
    )
    with pytest.raises(contracts.StudioConflictError):
        service.create_video_edit_draft(
            other.id,
            contracts.VideoEditDraftCreateCommand(
                sourceVideoAssetId=video.id,
                idempotencyKey="cross-project-draft",
            ),
        )


def test_review_failure_is_persisted_without_accepting_video():
    service, repo, project, video = prepared_video()
    review = service.create_video_review(
        project.id,
        contracts.VideoReviewCreateCommand(
            assetId=video.id,
            checks={
                "childIdentity": "pass",
                "catIdentity": "pass",
                "pairScale": "pass",
                "styleConsistency": "pass",
                "anatomy": "pass",
                "technical": "pass",
                "causalChainAndActiveEnding": "fail",
            },
            notes="饼干被拿出篮子",
            issues=[{"range": {"startFrame": 180, "endFrame": 289}, "note": "留在篮内"}],
            idempotencyKey="review-failure",
        ),
    )
    assert service.list_video_reviews(project.id, video.id)[0].id == review.id
    with pytest.raises(contracts.StudioConflictError):
        service.require_video_review(project.id, video.id, review.id)
    assert "video" not in repo.current_selections(project.id)


def test_draft_repair_uses_local_time_and_excludes_wrong_out_anchor():
    service, repo, project, video, draft = repair_draft()
    preview = service.preview_video_repair(
        project.id,
        contracts.SegmentRepairPreviewCommand(
            baseVideoAssetId=video.id,
            baseEditVersionId=draft.head_edit_version_id,
            editDraftId=draft.id,
            issueRange={"startFrame": 144, "endFrame": 289},
            instruction="饼干留在篮内",
            endStatePolicy="replace",
            desiredEndState="桌面无饼干。",
        ),
    )
    assert preview.generation_range == FrameRange(startFrame=120, endFrame=289)
    assert preview.candidate_core_range == FrameRange(startFrame=24, endFrame=169)
    assert preview.provider_duration_seconds == 8
    assert "1.000" in preview.prompt and "7.042" in preview.prompt
    assert "桌面无饼干。不继承" in preview.prompt
    assert "。。" not in preview.prompt
    assert preview.desired_end_state == "桌面无饼干。"
    assert [item.role for item in preview.image_references] == [
        "anchor_in",
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
        "style_board",
    ]
    assert preview.base_edl.root_video_asset_id == video.id
    assert "video" not in repo.current_selections(project.id)


def test_repair_submission_freezes_draft_edl_and_replays_without_new_job():
    service, repo, project, video, draft = repair_draft()
    inputs = {
        "baseVideoAssetId": video.id,
        "baseEditVersionId": draft.head_edit_version_id,
        "editDraftId": draft.id,
        "issueRange": {"startFrame": 144, "endFrame": 289},
        "instruction": "饼干留在篮内",
        "endStatePolicy": "replace",
        "desiredEndState": "桌面无饼干",
    }
    preview = service.preview_video_repair(
        project.id, contracts.SegmentRepairPreviewCommand(**inputs)
    )
    command = contracts.SegmentRepairCreateCommand(
        **inputs, expectedInputHash=preview.input_hash, idempotencyKey="one-possible-paid-call"
    )
    job = service.create_video_repair_job(project.id, command)
    assert service.create_video_repair_job(project.id, command).id == job.id
    assert job.frozen_input["baseEdl"]["rootVideoAssetId"] == str(video.id)
    assert job.frozen_input["endStatePolicy"] == "replace"
    assert service.get_video_repair(job.video_repair_id).selection_policy_version == 3
    assert "video" not in repo.current_selections(project.id)


def test_draft_preview_is_local_frozen_and_does_not_accept_or_export():
    service, repo, project, video, draft = repair_draft()
    edit = repo.get_edit(draft.head_edit_version_id)
    command = contracts.VideoDraftPreviewCommand(
        expectedEditVersionId=edit.id,
        expectedTimelineHash=edit.timeline_hash,
        idempotencyKey="free-composite-preview",
    )
    job = service.create_draft_preview(project.id, draft.id, command)
    assert job.kind == "render_edit_preview" and job.provider == "local_ffmpeg"
    assert job.expected_cost_micros == 0
    assert job.frozen_input["edl"] == edit.edl.model_dump(mode="json", by_alias=True)
    assert service.create_draft_preview(project.id, draft.id, command).id == job.id
    assert repo.get_edit(edit.id).active is False
    assert "video" not in repo.current_selections(project.id)


def test_draft_save_is_immutable_compare_and_swap_and_next_repair_uses_new_edl():
    service, repo, project, video, draft = repair_draft()
    old = repo.get_edit(draft.head_edit_version_id)
    command = contracts.VideoDraftSaveCommand(
        expectedEditVersionId=old.id,
        expectedTimelineHash=old.timeline_hash,
        edl=old.edl,
        idempotencyKey="save-draft-once",
    )
    saved = service.save_video_draft(project.id, draft.id, command)
    assert saved.id != old.id and saved.parent_edit_version_id == old.id
    assert service.save_video_draft(project.id, draft.id, command).id == saved.id
    with pytest.raises(contracts.StudioConflictError):
        service.save_video_draft(
            project.id, draft.id, command.model_copy(update={"idempotency_key": "stale-save-key"})
        )
    preview = service.preview_video_repair(
        project.id,
        contracts.SegmentRepairPreviewCommand(
            baseVideoAssetId=video.id,
            baseEditVersionId=saved.id,
            editDraftId=draft.id,
            issueRange={"startFrame": 144, "endFrame": 289},
            instruction="饼干留在篮内",
        ),
    )
    assert preview.base_edl == saved.edl
    assert service.get_video_edit_draft(project.id, draft.id).head_edit_version_id == saved.id
    assert not saved.active and not repo.get_edit(old.id).active
    assert "video" not in repo.current_selections(project.id)


def test_comparison_is_deterministic_and_apply_preserves_candidate_for_next_repair():
    service, repo, project, video, draft = repair_draft()
    base = repo.get_edit(draft.head_edit_version_id)
    command = contracts.SegmentRepairPreviewCommand(
        baseVideoAssetId=video.id,
        baseEditVersionId=base.id,
        editDraftId=draft.id,
        issueRange={"startFrame": 144, "endFrame": 289},
        instruction="饼干留在篮内",
        endStatePolicy="replace",
        desiredEndState="饼干仍在篮内",
    )
    preview = service.preview_video_repair(project.id, command)
    job = service.create_video_repair_job(
        project.id,
        contracts.SegmentRepairCreateCommand(
            **command.model_dump(by_alias=True),
            expectedInputHash=preview.input_hash,
            idempotencyKey="chain-first-request",
        ),
    )
    with pytest.raises(contracts.StudioConflictError, match="正在处理"):
        service.create_video_repair_job(
            project.id,
            contracts.SegmentRepairCreateCommand(
                **command.model_dump(by_alias=True),
                expectedInputHash=preview.input_hash,
                idempotencyKey="chain-forbidden-concurrent",
            ),
        )
    candidate = service.register_asset(
        project.id,
        role="repair_candidate",
        media_type="video",
        sha256="b" * 64,
        metadata={"durationFrames": 192},
    )
    repo.set_video_repair_status(
        job.video_repair_id, status="candidate_ready", candidate_asset_id=candidate.id
    )
    render = contracts.VideoDraftPreviewCommand(
        expectedEditVersionId=base.id,
        expectedTimelineHash=base.timeline_hash,
        repairId=job.video_repair_id,
        idempotencyKey="chain-render-preview",
    )
    first = service.create_draft_preview(project.id, draft.id, render)
    assert service.create_draft_preview(project.id, draft.id, render).id == first.id
    _, edl = service.draft_preview_timeline(project.id, draft.id, render)
    assert edl.total_frames == 289
    assert [s.duration_frames for s in edl.video_segments] == [144, 145]
    assert edl.video_segments[1].source_in_frame == 24
    save = contracts.VideoDraftSaveCommand(
        **render.model_dump(by_alias=True, exclude={"idempotency_key"}),
        edl=edl,
        idempotencyKey="chain-apply-once",
    )
    saved = service.save_video_draft(project.id, draft.id, save)
    assert service.save_video_draft(project.id, draft.id, save).id == saved.id
    assert service.get_video_repair(job.video_repair_id).status == "applied_to_draft"
    next_preview = service.preview_video_repair(
        project.id, command.model_copy(update={"base_edit_version_id": saved.id})
    )
    assert next_preview.base_edl.video_segments[1].asset_id == candidate.id
    assert not saved.active and "video" not in repo.current_selections(project.id)
    with pytest.raises(contracts.StudioConflictError, match="草稿"):
        service.create_export_job(
            project.id,
            contracts.ExportCommand(editVersionId=saved.id, idempotencyKey="cannot-export-draft"),
        )
