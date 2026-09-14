from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from array import array
from pathlib import Path

import pytest
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
from catflow_worker.media_probe import inspect_video
from catflow_worker.runtime_support import AssetMediaResolver


def test_real_trial_slip_comparison_and_second_edit_preserve_audio(tmp_path, request):
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    repo = PostgresStudioRepository(sessions)
    service = dto.StudioService(
        repo,
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="mock",
            image_model="mock",
            video_model="mock",
            diagnostic_model="mock",
            capability_revision="mock",
            paid_calls_enabled=True,
            maximum_video_references=5,
            segment_reference_publishing_ready=True,
        ),
    )
    project = service.create_project(
        dto.ProjectCreate(title="等长音画试装测试", theme="合成帧与脉冲", targetDurationSeconds=12)
    )

    def cleanup():
        with sessions.begin() as session:
            session.execute(
                update(JobRecord)
                .where(JobRecord.project_id == project.id)
                .values(status="cancelled")
            )
        engine.dispose()

    request.addfinalizer(cleanup)
    ffmpeg, ffprobe = Path(os.environ["FFMPEG_PATH"]), Path(os.environ["FFPROBE_PATH"])
    store = LocalMediaStore(tmp_path / "media")
    executor = LocalMediaJobExecutor(sessions, store, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe)

    def run(*args):
        return subprocess.run(
            [str(ffmpeg), "-v", "error", *map(str, args)],
            capture_output=True,
            check=True,
            timeout=120,
        ).stdout

    videos = []
    for name, frames, color, sound, rate in (
        ("root", 289, "red", "0", 44100),
        ("candidate", 120, "blue", "if(between(t,1.01,1.035),0.8*sin(2*PI*1000*t),0)", 32000),
    ):
        key = f"fixture/{name}.mp4"
        path = store.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        font = "C\\:/Windows/Fonts/consola.ttf"
        run(
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color={color}:s=480x854:r=24",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc='{sound}':s={rate}",
            "-vf",
            f"drawtext=fontfile='{font}':text='%{{n}}':x=20:y=20:fontsize=32:fontcolor=white",
            "-t",
            f"{frames / 24:.9f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            path,
        )
        facts = inspect_video(path, ffprobe, ffmpeg_path=ffmpeg)
        assert facts["durationFrames"] == frames
        videos.append(
            service.register_asset(
                project.id,
                role="video" if name == "root" else "repair_candidate",
                media_type="video",
                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                storage_key=key,
                byte_size=path.stat().st_size,
                metadata=facts,
            )
        )
    root, candidate = videos
    assert root.metadata["audioState"] == "silent"
    assert (
        candidate.metadata["audioState"] == "present" and candidate.metadata["audioChannels"] == 1
    )
    draft = service.create_video_edit_draft(
        project.id,
        dto.VideoEditDraftCreateCommand(
            sourceVideoAssetId=root.id, idempotencyKey=f"draft:{project.id}"
        ),
    )
    base = repo.get_edit(draft.head_edit_version_id)

    def mock_candidate(parent, issue):
        inputs = dto.SegmentRepairPreviewCommand(
            editContractVersion=2,
            baseVideoAssetId=root.id,
            baseEditVersionId=parent.id,
            editDraftId=draft.id,
            issueRange=issue,
            instruction="合成画面测试",
            generationMode="from_frame",
            anchorStartFrame=0,
            audioMode="generate_candidate",
        )
        preparation = service.prepare_segment_references(
            project.id, dto.SegmentReferencePreparationCommand(**inputs.model_dump())
        )
        AssetMediaResolver(sessions, store, ffmpeg_path=ffmpeg, ffprobe_path=ffprobe,
                           timeline_renderer=executor).store_reference_preparation(preparation.id)
        with sessions.begin() as session:
            session.get(JobRecord, preparation.id).status = "storing"
            session.get(JobRecord, preparation.id).status = "succeeded"
        inputs = inputs.model_copy(update={"reference_preparation_job_id": preparation.id})
        prepared = service.preview_video_repair(project.id, inputs)
        job = service.create_video_repair_job(
            project.id,
            dto.SegmentRepairCreateCommand(
                **inputs.model_dump(by_alias=True),
                expectedInputHash=prepared.input_hash,
                idempotencyKey=f"mock:{uuid.uuid4()}",
            ),
        )
        repo.set_video_repair_status(
            job.video_repair_id, status="candidate_ready", candidate_asset_id=candidate.id
        )
        with sessions.begin() as session:
            session.get(JobRecord, job.id).status = "storing"
            session.get(JobRecord, job.id).status = "succeeded"
        return job

    def materialize(parent, paid, start, end, audio):
        local = service.create_draft_preview(
            project.id,
            draft.id,
            dto.VideoDraftPreviewCommand(
                expectedEditVersionId=parent.id,
                expectedTimelineHash=parent.timeline_hash,
                repairId=paid.video_repair_id,
                placement={
                    "candidateSourceRange": {"startFrame": start, "endFrame": end},
                    "audioPolicy": audio,
                },
                idempotencyKey=f"local:{uuid.uuid4()}",
            ),
        )
        executor.store_result(local.id)
        with sessions.begin() as session:
            results = list(
                session.scalars(select(AssetRecord).where(AssetRecord.producing_job_id == local.id))
            )
            record = session.get(JobRecord, local.id)
            record.status = "storing"
            record.status = "succeeded"
        return local, results

    paid = mock_candidate(base, {"startFrame": 48, "endFrame": 96})
    local, results = materialize(base, paid, 24, 72, "use_candidate")
    preview = next(asset for asset in results if asset.role == "edit_preview")
    with sessions() as session:
        comparison = session.get(JobRecord, local.id).provider_result_json["comparison"]
    assert comparison["mode"] == "dual_player"
    before = next(asset for asset in results if str(asset.id) == comparison["beforeAssetId"])
    after = next(asset for asset in results if str(asset.id) == comparison["afterAssetId"])
    assert before.role == "edit_trial_base"
    assert after.id == preview.id
    assert preview.metadata_json["durationFrames"] == 289
    assert preview.metadata_json["audioChannels"] == 1

    def audio_samples(asset):
        return array(
            "f",
            run(
                "-i",
                store.resolve(asset.storage_key),
                "-map",
                "0:a:0",
                "-ar",
                "48000",
                "-f",
                "f32le",
                "-",
            ),
        )

    waveform = audio_samples(preview)
    # Candidate's native pulse at 1.01 s moves with source frame 24 to timeline frame 48.
    pulse = [index for index, sample in enumerate(waveform) if abs(sample) > 0.1]
    assert 2.0 <= pulse[0] / 48000 < 2.025
    assert pulse[-1] / 48000 < 2.06
    assert max(map(abs, audio_samples(before))) == 0
    assert audio_samples(after) == waveform

    def picture_hash(asset):
        return run(
            "-i",
            store.resolve(asset.storage_key),
            "-map",
            "0:v:0",
            "-c:v",
            "copy",
            "-f",
            "hash",
            "-",
        )

    assert picture_hash(before) != picture_hash(after)
    raw_pixels = run(
        "-i",
        store.resolve(after.storage_key),
        "-vf",
        "scale=1:1",
        "-pix_fmt",
        "rgb24",
        "-f",
        "rawvideo",
        "-",
    )
    pixels = list(zip(raw_pixels[::3], raw_pixels[1::3], raw_pixels[2::3], strict=True))
    assert len(pixels) == 289
    assert (
        pixels[47][0] > 200 and pixels[48][2] > 200 and pixels[95][2] > 200 and pixels[96][0] > 200
    )
    saved = service.save_video_draft(
        project.id,
        draft.id,
        dto.VideoDraftSaveCommand(
            expectedEditVersionId=base.id,
            expectedTimelineHash=base.timeline_hash,
            repairId=paid.video_repair_id,
            previewJobId=local.id,
            idempotencyKey=f"apply:{project.id}",
        ),
    )
    assert saved.edl.model_dump(mode="json", by_alias=True) == local.frozen_input["edl"]
    second = mock_candidate(saved, {"startFrame": 288, "endFrame": 289})
    again, second_results = materialize(saved, second, 0, 1, "preserve_current")
    second_preview = next(asset for asset in second_results if asset.role == "edit_preview")
    assert again.frozen_input["edl"]["audio"] == local.frozen_input["edl"]["audio"]
    assert audio_samples(second_preview) == waveform
    assert second_preview.metadata_json["durationFrames"] == 289
    assert "video" not in repo.current_selections(project.id)


@pytest.mark.parametrize("audio", [False, True])
def test_media_facts_distinguish_absent_and_silent_audio(tmp_path, audio):
    ffmpeg, ffprobe = Path(os.environ["FFMPEG_PATH"]), Path(os.environ["FFPROBE_PATH"])
    path = tmp_path / "one-frame.mp4"
    command = [str(ffmpeg), "-v", "error", "-f", "lavfi", "-i", "color=red:s=32x32:r=24"]
    if audio:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-c:a", "aac"]
    command += ["-t", "0.041666667", "-c:v", "libx264", str(path)]
    subprocess.run(command, capture_output=True, check=True)
    facts = inspect_video(path, ffprobe, ffmpeg_path=ffmpeg)
    assert facts["durationFrames"] == 1
    assert facts["audioState"] == ("silent" if audio else "absent")
