from __future__ import annotations

import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete, select

from catflow.application.service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    EditCreateCommand,
    ExportCommand,
    ImageDiagnosisCommand,
    ProjectCreate,
    SegmentRepairApproveCommand,
    SegmentRepairCreateCommand,
    SegmentRepairPreviewCommand,
    StudioService,
)
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import (
    AssetRecord,
    EnvironmentPresetRecord,
    JobRecord,
    ProjectRecord,
)
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.media_jobs import MediaJobExecutor
from catflow_worker.runtime_support import AssetMediaResolver


def test_fake_video_result_is_a_valid_immutable_vertical_mp4(tmp_path: Path) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="Fake MP4 测试", theme="窗边纸星星", targetDurationSeconds=8)
    )
    job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="storing",
                    input_hash="d" * 64,
                    idempotency_key=f"fake-media-{job_id}",
                    provider="fake",
                    model="fake-video",
                    provider_task_id=f"fake-{job_id}",
                    expected_cost_micros=0,
                    frozen_input_json={"prompt": "窗边纸星星"},
                )
            )
        executor = MediaJobExecutor(
            sessions,
            LocalMediaStore(tmp_path / "media"),
            ffmpeg_path=Path(os.environ["FFMPEG_PATH"]),
            ffprobe_path=Path(os.environ["FFPROBE_PATH"]),
        )

        executor.store_result(job_id)
        executor.store_result(job_id)

        with sessions() as session:
            assets = list(
                session.scalars(
                    select(AssetRecord).where(AssetRecord.producing_job_id == job_id)
                ).all()
            )
            assert len(assets) == 1
            assert assets[0].role == "video"
            assert assets[0].width == 480
            assert assets[0].height == 854
            assert assets[0].metadata_json["resolution"] == "480p"
            assert assets[0].metadata_json["ratio"] == "9:16"
            assert 7_900 <= (assets[0].duration_ms or 0) <= 8_100
            assert (tmp_path / "media" / assets[0].storage_key).is_file()
    finally:
        with sessions.begin() as session:
            session.execute(
                delete(EnvironmentPresetRecord).where(
                    EnvironmentPresetRecord.source_project_id == project.id
                )
            )
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_fake_image_result_is_a_decodable_candidate_for_the_requested_slot(
    tmp_path: Path,
) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="Fake 图片测试", theme="晾晒手帕", targetDurationSeconds=8)
    )
    try:
        preview = service.preview_asset_generation(
            project.id, AssetGenerationPreviewCommand(kind="episode_cat")
        )
        job = service.create_asset_generation_job(
            project.id,
            AssetGenerationCommand(
                kind="episode_cat",
                expectedInputHash=preview.input_hash,
                idempotencyKey=f"fake-image-{project.id}",
            ),
        )
        executor = MediaJobExecutor(
            sessions,
            LocalMediaStore(tmp_path / "media"),
            ffmpeg_path=Path(os.environ["FFMPEG_PATH"]),
            ffprobe_path=Path(os.environ["FFPROBE_PATH"]),
        )

        executor.store_result(job.id)

        asset = service.list_assets(project.id)[0]
        diagnosis = service.create_image_diagnosis_job(
            project.id,
            ImageDiagnosisCommand(
                assetId=asset.id,
                idempotencyKey=f"fake-diagnosis-{project.id}",
            ),
        )
        executor.store_result(diagnosis.id)
        asset = service.get_asset(asset.id)
        assert asset.role == "episode_cat"
        assert asset.media_type == "image"
        assert asset.metadata["width"] == 720
        assert asset.metadata["height"] == 1280
        assert asset.metadata["qualityReport"]["identity"]["catMatch"] == "pass"
        assert (tmp_path / "media" / asset.storage_key).is_file()
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_ffmpeg_export_validates_edl_source_and_records_rendered_asset(tmp_path: Path) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="FFmpeg EDL 测试", theme="折叠毛巾", targetDurationSeconds=8)
    )
    source_job_id = uuid.uuid4()
    executor = MediaJobExecutor(
        sessions,
        LocalMediaStore(tmp_path / "media"),
        ffmpeg_path=Path(os.environ["FFMPEG_PATH"]),
        ffprobe_path=Path(os.environ["FFPROBE_PATH"]),
    )
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=source_job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="storing",
                    input_hash="e" * 64,
                    idempotency_key=f"edl-source-{source_job_id}",
                    provider="fake",
                    model="fake-video",
                    provider_task_id=f"fake-{source_job_id}",
                    expected_cost_micros=0,
                    frozen_input_json={"prompt": "折叠毛巾"},
                )
            )
        executor.store_result(source_job_id)
        source = service.list_assets(project.id)[0]
        service.select_asset(project.id, slot="video", asset_id=source.id)
        edit = service.create_edit(
            project.id,
            EditCreateCommand(
                edl={
                    "sourceVideoSelections": [
                        {
                            "assetId": str(source.id),
                            "sha256": source.sha256,
                            "startMs": 0,
                            "endMs": 8000,
                        }
                    ],
                    "transitions": [{"afterClipIndex": 0, "type": "fade", "durationMs": 250}],
                    "audioPolicy": "mute",
                    "output": {
                        "aspectRatio": "9:16",
                        "width": 720,
                        "height": 1280,
                        "format": "mp4",
                    },
                }
            ),
        )
        export = service.create_export_job(
            project.id,
            ExportCommand(
                editVersionId=edit.id,
                idempotencyKey=f"edl-export-{project.id}",
            ),
        )

        executor.store_result(export.id)

        final = next(asset for asset in service.list_assets(project.id) if asset.role == "final")
        rendered_edit = service.list_edits(project.id)[0]
        assert final.producing_job_id == export.id
        assert final.metadata["width"] == 720
        assert final.metadata["height"] == 1280
        assert rendered_edit.status == "rendered"
        assert rendered_edit.rendered_asset_id == final.id
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_fake_segment_job_creates_one_candidate_without_changing_the_active_edit(
    tmp_path: Path,
) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="Fake 片段候选", theme="雨天擦爪", targetDurationSeconds=12)
    )
    source_job_id = uuid.uuid4()
    executor = MediaJobExecutor(
        sessions,
        LocalMediaStore(tmp_path / "media"),
        ffmpeg_path=Path(os.environ["FFMPEG_PATH"]),
        ffprobe_path=Path(os.environ["FFPROBE_PATH"]),
    )
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=source_job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="storing",
                    input_hash="1" * 64,
                    idempotency_key=f"fake-repair-source-{source_job_id}",
                    provider="fake",
                    model="fake-video",
                    provider_task_id=f"fake-{source_job_id}",
                    expected_cost_micros=0,
                    frozen_input_json={
                        "prompt": "雨天擦爪",
                        "durationSeconds": 12,
                        "includeFakeAudio": True,
                    },
                )
            )
        executor.store_result(source_job_id)
        base = next(asset for asset in service.list_assets(project.id) if asset.role == "video")
        environment = service.register_asset(
            project.id, role="environment", media_type="image", sha256="2" * 64
        )
        service.select_asset(project.id, slot="video", asset_id=base.id)
        service.select_asset(project.id, slot="environment", asset_id=environment.id)
        preview = service.preview_video_repair(
            project.id,
            SegmentRepairPreviewCommand(
                baseVideoAssetId=base.id,
                issueRange={"startFrame": 96, "endFrame": 192},
                instruction="只重拍擦爪动作。",
            ),
        )
        repair_job = service.create_video_repair_job(
            project.id,
            SegmentRepairCreateCommand(
                baseVideoAssetId=base.id,
                issueRange={"startFrame": 96, "endFrame": 192},
                instruction="只重拍擦爪动作。",
                expectedInputHash=preview.input_hash,
                idempotencyKey=f"fake-repair-candidate-{project.id}",
            ),
        )

        resolver = AssetMediaResolver(
            sessions,
            LocalMediaStore(tmp_path / "media"),
            ffmpeg_path=Path(os.environ["FFMPEG_PATH"]),
        )
        context, anchor_in, anchor_out = resolver.prepare_segment_media(
            repair_job.id,
            base.id,
            72,
            216,
            96,
            192,
            6,
        )

        assert context.is_file()
        assert anchor_in.is_file()
        assert anchor_out.is_file()
        prepared_roles = {
            asset.role
            for asset in service.list_assets(project.id)
            if asset.producing_job_id == repair_job.id
        }
        assert prepared_roles == {
            "repair_context",
            "repair_anchor_in",
            "repair_anchor_out",
        }

        executor.store_result(repair_job.id)
        executor.store_result(repair_job.id)

        candidates = [
            asset for asset in service.list_assets(project.id) if asset.role == "repair_candidate"
        ]
        assert len(candidates) == 1
        first_candidate = candidates[0]
        assert first_candidate.producing_job_id == repair_job.id
        assert first_candidate.metadata["durationFrames"] == 144
        assert repair_job.video_repair_id is not None
        repair_id = repair_job.video_repair_id
        assert service.get_video_repair(repair_id).status == "candidate_ready"
        assert service.list_edits(project.id) == []

        repeated_preview = service.preview_video_repair(
            project.id,
            SegmentRepairPreviewCommand(
                baseVideoAssetId=base.id,
                issueRange={"startFrame": 96, "endFrame": 192},
                instruction="只重拍擦爪动作。",
            ),
        )
        repeated_job = service.create_video_repair_job(
            project.id,
            SegmentRepairCreateCommand(
                baseVideoAssetId=base.id,
                issueRange={"startFrame": 96, "endFrame": 192},
                instruction="只重拍擦爪动作。",
                expectedInputHash=repeated_preview.input_hash,
                idempotencyKey=f"fake-repair-repeated-content-{project.id}",
            ),
        )

        executor.store_result(repeated_job.id)

        candidates_by_job = {
            asset.producing_job_id: asset
            for asset in service.list_assets(project.id)
            if asset.role == "repair_candidate"
        }
        assert set(candidates_by_job) == {repair_job.id, repeated_job.id}
        assert candidates_by_job[repair_job.id].sha256 == candidates_by_job[repeated_job.id].sha256
        assert repeated_job.video_repair_id is not None
        assert (
            service.get_video_repair(repeated_job.video_repair_id).status == "candidate_ready"
        )

        edit = service.approve_video_repair(
            project.id,
            repair_id,
            SegmentRepairApproveCommand(
                candidateAssetId=first_candidate.id,
                candidateSourceRange={"startFrame": 24, "endFrame": 120},
                transition={"type": "dissolve", "durationFrames": 4},
                expectedBaseTimelineHash=preview.base_timeline_hash,
                idempotencyKey=f"fake-repair-approval-{project.id}",
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
        export = service.create_export_job(
            project.id,
            ExportCommand(
                editVersionId=edit.id,
                idempotencyKey=f"fake-repair-export-{project.id}",
            ),
        )

        executor.store_result(export.id)

        final = next(asset for asset in service.list_assets(project.id) if asset.role == "final")
        assert final.metadata["frameCount"] == 288
        assert final.metadata["durationFrames"] == 288
        assert final.metadata["audioPolicy"] == "preserve_original"
        assert final.metadata["candidateAudioUsed"] is False
        assert final.metadata["audioCodec"] == "aac"
        assert final.metadata["audioTranscoded"] is False
        assert service.list_edits(project.id)[0].rendered_asset_id == final.id
    finally:
        with sessions.begin() as session:
            session.execute(
                delete(EnvironmentPresetRecord).where(
                    EnvironmentPresetRecord.source_project_id == project.id
                )
            )
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
