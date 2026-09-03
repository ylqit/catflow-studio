from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import (
    ProjectCreate,
    SegmentRepairCreateCommand,
    SegmentRepairPreviewCommand,
    StudioConflictError,
    StudioService,
)
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def _prepared_project() -> tuple[StudioService, uuid.UUID, uuid.UUID]:
    service = StudioService(
        MemoryStudioRepository(),
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="planning",
            image_model="image",
            video_model="video",
            diagnostic_model="diagnostic",
            capability_revision="ark-seedance-2.0-v1",
            paid_calls_enabled=True,
            maximum_video_references=5,
            segment_reference_publishing_ready=True,
        ),
    )
    project = service.create_project(
        ProjectCreate(title="无类型局部编辑", theme="雨天擦爪", targetDurationSeconds=12)
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


def test_preview_is_untyped_and_does_not_persist_a_repair() -> None:
    service, project_id, video_id = _prepared_project()

    preview = service.preview_video_repair(
        project_id,
        SegmentRepairPreviewCommand(
            baseVideoAssetId=video_id,
            issueRange={"startFrame": 96, "endFrame": 192},
            instruction="先让猫咪抬爪，再让孩子擦干湿爪，同时让地面水印减少。",
        ),
    )

    assert service.list_video_repairs(project_id) == []
    assert "editIntent" not in preview.model_dump(mode="json", by_alias=True)
    assert "本区间修改目标" in preview.prompt
    assert preview.input_snapshot is not None
    assert preview.input_snapshot.state == "preview"
    assert preview.input_snapshot.segment_edit is not None
    assert preview.input_snapshot.segment_edit.instruction == preview.instruction
    public_snapshot = json.dumps(
        preview.input_snapshot.model_dump(mode="json", by_alias=True)
    ).lower()
    assert "storage_key" not in public_snapshot
    assert "storagekey" not in public_snapshot
    assert "x-amz-signature" not in public_snapshot
    assert "accesskey" not in public_snapshot


def test_preview_rejects_a_problem_range_shorter_than_four_seconds() -> None:
    service, project_id, video_id = _prepared_project()

    with pytest.raises(ValidationError, match="at least 4 seconds"):
        service.preview_video_repair(
            project_id,
            SegmentRepairPreviewCommand(
                baseVideoAssetId=video_id,
                issueRange={"startFrame": 0, "endFrame": 95},
                instruction="修正动作。",
            ),
        )

    assert service.list_video_repairs(project_id) == []


def test_create_recompiles_the_preview_and_freezes_one_untyped_job() -> None:
    service, project_id, video_id = _prepared_project()
    request = {
        "baseVideoAssetId": video_id,
        "issueRange": {"startFrame": 96, "endFrame": 192},
        "instruction": "同时修正抬爪、毛巾受力和地面水印变化。",
    }
    preview = service.preview_video_repair(project_id, SegmentRepairPreviewCommand(**request))
    command = SegmentRepairCreateCommand(
        **request,
        expectedInputHash=preview.input_hash,
        idempotencyKey="untyped-edit-job",
    )

    first = service.create_video_repair_job(project_id, command)
    repeated = service.create_video_repair_job(project_id, command)

    assert first.id == repeated.id
    assert first.video_repair_id is not None
    assert "editIntent" not in first.frozen_input
    assert first.frozen_input["instruction"] == request["instruction"]
    assert first.input_snapshot is not None
    assert first.input_snapshot.state == "submitted"
    assert first.input_snapshot.prompt == preview.prompt
    assert first.input_snapshot.segment_edit is not None
    assert first.input_snapshot.segment_edit.issue_range.duration_frames == 96
    assert len(service.list_video_repairs(project_id)) == 1


def test_create_rejects_a_stale_preview_hash_without_persisting() -> None:
    service, project_id, video_id = _prepared_project()

    with pytest.raises(StudioConflictError, match="input hash changed"):
        service.create_video_repair_job(
            project_id,
            SegmentRepairCreateCommand(
                baseVideoAssetId=video_id,
                issueRange={"startFrame": 96, "endFrame": 192},
                instruction="修正动作。",
                expectedInputHash="0" * 64,
                idempotencyKey="stale-edit-preview",
            ),
        )

    assert service.list_video_repairs(project_id) == []
