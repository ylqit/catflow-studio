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
    StudioService,
)
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord, JobRecord, ProjectRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.media_jobs import MediaJobExecutor


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
                expectedCostMicros=0,
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
