from __future__ import annotations

import uuid

import pytest

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import (
    ProjectCreate,
    SegmentRepairApproveCommand,
    SegmentRepairCreateCommand,
    SegmentRepairPreviewCommand,
    StudioConflictError,
    StudioService,
)
from catflow.domain.video_repairs import FrameRange
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def _prepared_project(
    *, provider_runtime: ProviderRuntime | None = None
) -> tuple[StudioService, uuid.UUID, uuid.UUID]:
    service = StudioService(MemoryStudioRepository(), provider_runtime=provider_runtime)
    project = service.create_project(
        ProjectCreate(title="片段修复", theme="雨天擦爪", targetDurationSeconds=12)
    )
    video = service.register_asset(
        project.id,
        role="video",
        media_type="video",
        sha256="a" * 64,
        storage_key="generated/project/video/source.mp4",
        byte_size=1024,
        metadata={
            "durationFrames": 288,
            "frameRateNumerator": 24,
            "frameRateDenominator": 1,
            "durationMs": 12000,
        },
    )
    environment = service.register_asset(
        project.id,
        role="environment",
        media_type="image",
        sha256="e" * 64,
        storage_key="generated/project/image/environment.png",
        byte_size=512,
    )
    service.select_asset(project.id, slot="video", asset_id=video.id)
    service.select_asset(project.id, slot="environment", asset_id=environment.id)
    return service, project.id, video.id


def test_ark_repair_preview_stops_before_paid_work_without_https_video_publication() -> None:
    runtime = ProviderRuntime(
        provider="ark",
        planning_model="planning",
        image_model="image",
        video_model="video",
        diagnostic_model="diagnostic",
        capability_revision="ark-seedance-2.0-v1",
        paid_calls_enabled=True,
        maximum_video_references=5,
        segment_reference_publishing_ready=False,
    )
    service, project_id, video_id = _prepared_project(provider_runtime=runtime)

    with pytest.raises(StudioConflictError, match="HTTPS URL"):
        service.preview_video_repair(
            project_id,
            SegmentRepairPreviewCommand(
                baseVideoAssetId=video_id,
                issueRange={"startFrame": 96, "endFrame": 192},
                instruction="修正擦爪动作。",
            ),
        )

    assert service.list_video_repairs(project_id) == []


def test_repair_preview_and_paid_job_freeze_frame_ranges_and_reference_roles() -> None:
    service, project_id, video_id = _prepared_project()
    preview = service.preview_video_repair(
        project_id,
        SegmentRepairPreviewCommand(
            baseVideoAssetId=video_id,
            issueRange={"startFrame": 96, "endFrame": 192},
            instruction="让孩子用毛巾逐只擦干猫爪。",
        ),
    )

    assert preview.issue_range.start_frame == 96
    assert preview.issue_range.end_frame == 192
    assert preview.generation_range.start_frame == 72
    assert preview.generation_range.end_frame == 216
    assert preview.provider_duration_seconds == 6
    assert [item.role for item in preview.image_references] == [
        "anchor_in",
        "anchor_out",
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
        "style_board",
    ]
    assert preview.video_reference.role == "reference_video"
    assert preview.video_reference.asset_id == video_id
    assert preview.instruction == "让孩子用毛巾逐只擦干猫爪。"
    assert "第96帧（4.000秒）至第192帧（8.000秒）" in preview.prompt
    assert "初始状态—运动路径—结束状态" in preview.prompt
    assert all(item.role != "style_source" for item in preview.image_references)

    command = SegmentRepairCreateCommand(
        baseVideoAssetId=video_id,
        issueRange={"startFrame": 96, "endFrame": 192},
        instruction="让孩子用毛巾逐只擦干猫爪。",
        expectedInputHash=preview.input_hash,
        idempotencyKey="repair-job-one",
    )
    first = service.create_video_repair_job(project_id, command)
    repeated = service.create_video_repair_job(project_id, command)

    assert first.id == repeated.id
    assert first.kind == "regenerate_video_segment"
    assert first.video_repair_id is not None
    assert service.workspace(project_id)["latestRepairJob"]["id"] == str(first.id)
    assert first.frozen_input["issueRange"] == {"startFrame": 96, "endFrame": 192}
    assert first.frozen_input["generationRange"] == {"startFrame": 72, "endFrame": 216}
    assert first.frozen_input["providerDurationSeconds"] == 6
    assert "editIntent" not in first.frozen_input
    assert first.frozen_input["instruction"] == "让孩子用毛巾逐只擦干猫爪。"
    assert first.frozen_input["referenceRoles"] == [
        "anchor_in",
        "anchor_out",
        "episode_child",
        "episode_cat",
        "pair_scale",
        "environment",
        "style_board",
    ]
    assert service.list_edits(project_id) == []


def test_candidate_requires_all_quality_checks_before_creating_active_edit_v2() -> None:
    service, project_id, video_id = _prepared_project()
    preview = service.preview_video_repair(
        project_id,
        SegmentRepairPreviewCommand(
            baseVideoAssetId=video_id,
            issueRange={"startFrame": 96, "endFrame": 192},
            instruction="修正擦爪动作。",
        ),
    )
    job = service.create_video_repair_job(
        project_id,
        SegmentRepairCreateCommand(
            baseVideoAssetId=video_id,
            issueRange={"startFrame": 96, "endFrame": 192},
            instruction="修正擦爪动作。",
            expectedInputHash=preview.input_hash,
            idempotencyKey="repair-job-approval",
        ),
    )
    candidate = service.register_asset(
        project_id,
        role="repair_candidate",
        media_type="video",
        sha256="b" * 64,
        storage_key="generated/project/repair/candidate.mp4",
        byte_size=2048,
        producing_job_id=job.id,
        metadata={"durationFrames": 144, "frameRateNumerator": 24, "frameRateDenominator": 1},
    )
    assert job.video_repair_id is not None
    repair_id = job.video_repair_id
    service.mark_video_repair_candidate_ready(repair_id, candidate.id)

    incomplete = {
        "child_identity": "pass",
        "cat_identity": "pass",
        "pair_scale": "pass",
        "style": "pass",
        "structure": "pass",
        "motion_continuity": "pass",
        "causal_chain": "warning",
    }
    with pytest.raises(StudioConflictError, match="quality checks"):
        service.approve_video_repair(
            project_id,
            repair_id,
            SegmentRepairApproveCommand(
                candidateAssetId=candidate.id,
                candidateSourceRange={"startFrame": 24, "endFrame": 120},
                transition={"type": "cut", "durationFrames": 0},
                expectedBaseTimelineHash=preview.base_timeline_hash,
                idempotencyKey="repair-approval-one",
                qualityChecks=incomplete,
                seamChecks={"in": "pass", "out": "pass"},
            ),
        )

    all_pass = dict.fromkeys(incomplete, "pass")
    edit = service.approve_video_repair(
        project_id,
        repair_id,
        SegmentRepairApproveCommand(
            candidateAssetId=candidate.id,
            candidateSourceRange={"startFrame": 25, "endFrame": 121},
            transition={"type": "cut", "durationFrames": 0},
            expectedBaseTimelineHash=preview.base_timeline_hash,
            idempotencyKey="repair-approval-one",
            qualityChecks=all_pass,
            seamChecks={"in": "pass", "out": "pass"},
        ),
    )

    assert edit.active is True
    assert edit.format_version == 2
    assert edit.parent_edit_version_id is None
    assert edit.edl.format == "catflow-edl-v2"
    assert [item.duration_frames for item in edit.edl.video_segments] == [96, 96, 96]
    assert service.get_video_repair(repair_id).status == "approved"
    assert service.get_video_repair(repair_id).approved_edit_version_id == edit.id
    assert service.get_video_repair(repair_id).candidate_core_range == FrameRange(
        startFrame=25, endFrame=121
    )


def test_repair_approval_rejects_a_changed_base_video_selection() -> None:
    service, project_id, video_id = _prepared_project()
    preview = service.preview_video_repair(
        project_id,
        SegmentRepairPreviewCommand(
            baseVideoAssetId=video_id,
            issueRange={"startFrame": 96, "endFrame": 192},
            instruction="修正擦爪动作。",
        ),
    )
    job = service.create_video_repair_job(
        project_id,
        SegmentRepairCreateCommand(
            baseVideoAssetId=video_id,
            issueRange={"startFrame": 96, "endFrame": 192},
            instruction="修正擦爪动作。",
            expectedInputHash=preview.input_hash,
            idempotencyKey="repair-outdated-job",
        ),
    )
    assert job.video_repair_id is not None
    replacement = service.register_asset(
        project_id,
        role="video",
        media_type="video",
        sha256="c" * 64,
        storage_key="generated/project/video/replacement.mp4",
        byte_size=1024,
        metadata={"durationFrames": 288, "frameRateNumerator": 24, "frameRateDenominator": 1},
    )
    service.select_asset(project_id, slot="video", asset_id=replacement.id)

    with pytest.raises(StudioConflictError, match="base timeline changed"):
        service.approve_video_repair(
            project_id,
            job.video_repair_id,
            SegmentRepairApproveCommand(
                candidateAssetId=uuid.uuid4(),
                candidateSourceRange={"startFrame": 24, "endFrame": 120},
                transition={"type": "cut", "durationFrames": 0},
                expectedBaseTimelineHash=preview.base_timeline_hash,
                idempotencyKey="repair-outdated",
                qualityChecks={
                    "child_identity": "pass",
                    "cat_identity": "pass",
                    "pair_scale": "pass",
                    "style": "pass",
                    "structure": "pass",
                    "motion_continuity": "pass",
                    "causal_chain": "pass",
                },
                seamChecks={"in": "pass", "out": "pass"},
            ),
        )
