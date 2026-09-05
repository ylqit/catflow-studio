from __future__ import annotations

import uuid

import pytest

from catflow.application import service as contracts
from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import SegmentRepairPreviewCommand
from catflow.domain.video_repairs import FrameRange, validate_issue_range
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
