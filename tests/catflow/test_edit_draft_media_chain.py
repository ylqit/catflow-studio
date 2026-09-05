from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select, update

from catflow.application import service as dto
from catflow.application.provider_config import ProviderRuntime
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord, JobRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.media_jobs import LocalMediaJobExecutor
from catflow_worker.runtime_support import AssetMediaResolver


def test_postgres_second_repair_reads_first_composite_and_preserves_legacy_edit(
    tmp_path: Path, request: pytest.FixtureRequest
):
    # tests/conftest.py supplies a newly created, isolated database; never production.
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    repo = PostgresStudioRepository(sessions)
    service = dto.StudioService(
        repo,
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="test",
            image_model="test",
            video_model="test",
            diagnostic_model="test",
            capability_revision="test",
            paid_calls_enabled=True,
            maximum_video_references=5,
            segment_reference_publishing_ready=True,
        ),
    )
    project = service.create_project(
        dto.ProjectCreate(title="连续修复真实合成", theme="无付费测试", targetDurationSeconds=12)
    )

    def release_fixture_jobs():
        # Worker tests share this disposable DB; fixture-only jobs must not remain claimable.
        with sessions.begin() as session:
            session.execute(
                update(JobRecord)
                .where(
                    JobRecord.project_id == project.id,
                    JobRecord.status.not_in(("succeeded", "failed", "cancelled")),
                )
                .values(status="cancelled")
            )

    request.addfinalizer(release_fixture_jobs)
    store = LocalMediaStore(tmp_path / "media")
    ffmpeg = Path(os.environ["FFMPEG_PATH"])
    executor = LocalMediaJobExecutor(
        sessions, store, ffmpeg_path=ffmpeg, ffprobe_path=Path(os.environ["FFPROBE_PATH"])
    )
    videos = []
    for name, frames, color in (("root", 289, "red"), ("candidate", 192, "blue")):
        key = f"test/{name}.mp4"
        path = store.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                str(ffmpeg),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={color}:s=480x854:r=24",
                "-frames:v",
                str(frames),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ],
            check=True,
        )
        videos.append(
            service.register_asset(
                project.id,
                role="video" if name == "root" else "repair_candidate",
                media_type="video",
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                storage_key=key,
                byte_size=path.stat().st_size,
                metadata={
                    "durationFrames": frames,
                    "frameRateNumerator": 24,
                    "frameRateDenominator": 1,
                },
            )
        )
    root, candidate = videos
    for role in ("episode_child", "episode_cat", "pair_scale", "environment", "style_board"):
        asset = service.register_asset(
            project.id, role=role, sha256=hashlib.sha256(role.encode()).hexdigest()
        )
        service.select_asset(project.id, slot=role, asset_id=asset.id)
    draft_command = dto.VideoEditDraftCreateCommand(
        sourceVideoAssetId=root.id,
        confirmCurrentReferences=True,
        idempotencyKey=f"draft:{project.id}",
    )
    draft = service.create_video_edit_draft(project.id, draft_command)
    assert service.create_video_edit_draft(project.id, draft_command).id == draft.id
    base = repo.get_edit(draft.head_edit_version_id)
    inputs = dto.SegmentRepairPreviewCommand(
        baseVideoAssetId=root.id,
        baseEditVersionId=base.id,
        editDraftId=draft.id,
        issueRange={"startFrame": 144, "endFrame": 289},
        instruction="蓝色代表已修复的下半段",
        endStatePolicy="replace",
        desiredEndState="保持蓝色",
    )
    preview = service.preview_video_repair(project.id, inputs)
    command = dto.SegmentRepairCreateCommand(
        **inputs.model_dump(by_alias=True),
        expectedInputHash=preview.input_hash,
        idempotencyKey=f"repair:{project.id}",
    )
    job = service.create_video_repair_job(project.id, command)
    assert service.create_video_repair_job(project.id, command).id == job.id
    with pytest.raises(dto.StudioConflictError):
        service.create_video_repair_job(
            project.id, command.model_copy(update={"idempotency_key": f"concurrent:{project.id}"})
        )
    repo.set_video_repair_status(
        job.video_repair_id, status="candidate_ready", candidate_asset_id=candidate.id
    )
    with sessions.begin() as session:
        session.get(JobRecord, job.id).status = "succeeded"
    render = dto.VideoDraftPreviewCommand(
        expectedEditVersionId=base.id,
        expectedTimelineHash=base.timeline_hash,
        repairId=job.video_repair_id,
        idempotencyKey=f"compare:{project.id}",
    )
    local = service.create_draft_preview(project.id, draft.id, render)
    assert service.create_draft_preview(project.id, draft.id, render).id == local.id
    executor.store_result(local.id)
    executor.store_result(local.id)
    with sessions() as session:
        rendered = session.scalar(
            select(AssetRecord).where(
                AssetRecord.producing_job_id == local.id, AssetRecord.role == "edit_preview"
            )
        )
        assert rendered.metadata_json["durationFrames"] == 289
        assert rendered.metadata_json["candidateAudioUsed"] is False
        assert (
            len(
                list(
                    session.scalars(
                        select(AssetRecord.id).where(
                            AssetRecord.producing_job_id == local.id,
                            AssetRecord.role == "edit_thumbnail",
                        )
                    )
                )
            )
            == 12
        )
    frozen_edl = dto.EditDecisionListV2.model_validate(local.frozen_input["edl"])
    saved = service.save_video_draft(
        project.id,
        draft.id,
        dto.VideoDraftSaveCommand(
            **render.model_dump(by_alias=True, exclude={"idempotency_key"}),
            edl=frozen_edl,
            idempotencyKey=f"apply:{project.id}",
        ),
    )
    assert not saved.active and "video" not in repo.current_selections(project.id)
    second_input = inputs.model_copy(
        update={
            "base_edit_version_id": saved.id,
            "issue_range": dto.FrameRange(startFrame=180, endFrame=200),
        }
    )
    second_preview = service.preview_video_repair(project.id, second_input)
    second_job = service.create_video_repair_job(
        project.id,
        dto.SegmentRepairCreateCommand(
            **second_input.model_dump(by_alias=True),
            expectedInputHash=second_preview.input_hash,
            idempotencyKey=f"second:{project.id}",
        ),
    )
    resolver = AssetMediaResolver(sessions, store, ffmpeg_path=ffmpeg, timeline_renderer=executor)
    _, anchor, _ = resolver.prepare_segment_media(
        second_job.id,
        root.id,
        second_preview.generation_range.start_frame,
        second_preview.generation_range.end_frame,
        180,
        200,
        second_preview.provider_duration_seconds,
    )
    with Image.open(anchor) as image:
        red, _, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))
        assert blue > 200 and red < 25, "second repair must see the first blue repair, not red root"
    # A legacy trim + fade + mute must be rendered first, without changing the old edit.
    service.select_asset(project.id, slot="video", asset_id=root.id)
    old = service.create_edit(
        project.id,
        dto.EditCreateCommand(
            edl={
                "sourceVideoSelections": [
                    {
                        "assetId": str(root.id),
                        "sha256": root.sha256,
                        "startMs": 2000,
                        "endMs": 12000,
                    }
                ],
                "transitions": [{"afterClipIndex": 0, "type": "fade", "durationMs": 250}],
                "audioPolicy": "mute",
                "output": {"aspectRatio": "9:16", "width": 720, "height": 1280, "format": "mp4"},
            }
        ),
    )
    legacy = dto.VideoEditDraftCreateCommand(
        sourceVideoAssetId=root.id,
        sourceEditVersionId=old.id,
        confirmCurrentReferences=True,
        idempotencyKey=f"legacy:{project.id}",
    )
    with pytest.raises(dto.StudioConflictError, match="旧剪辑"):
        service.create_video_edit_draft(project.id, legacy)
    legacy_render = service.create_edit_preview(
        project.id,
        dto.ExportCommand(editVersionId=old.id, idempotencyKey=f"legacy-render:{project.id}"),
    )
    executor.store_result(legacy_render.id)
    legacy_draft = service.create_video_edit_draft(project.id, legacy)
    legacy_base = repo.get_edit(legacy_draft.head_edit_version_id)
    assert legacy_base.edl.total_frames == 240
    assert legacy_base.edl.root_video_asset_id != root.id
    assert repo.get_edit(old.id).edl == old.edl
    assert service.get_asset(legacy_base.edl.root_video_asset_id).metadata["audioPolicy"] == "mute"
    engine.dispose()
