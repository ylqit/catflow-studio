from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image
from sqlalchemy import delete, select

from catflow.application.service import PlannerMessageCommand, ProjectCreate, StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord, JobRecord, ProjectRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.ark_results import ArkResultLandingService
from catflow_worker.provider_media import ProviderMediaDownloader


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (480, 854), "#D8C2A8").save(buffer, format="PNG")
    return buffer.getvalue()


def test_ark_result_landing_creates_planner_proposal_from_persisted_result(
    tmp_path: Path,
) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="规划落地", theme="雨天擦爪", targetDurationSeconds=12)
    )
    job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey=f"landing-plan-{project.id}",
        ),
    )
    try:
        with sessions.begin() as session:
            record = session.get(JobRecord, job.id)
            assert record is not None
            record.status = "storing"
            record.provider_result_json = {
                "payload": {
                    "title": "雨天擦爪",
                    "summary": "孩子擦干猫爪，猫咪主动走进室内。",
                    "body": "湿爪印触发一次温暖而清晰的擦爪事件。",
                    "trigger": "猫咪留下湿爪印",
                    "childAction": "孩子用毛巾逐只擦干猫爪",
                    "catResponse": "猫咪配合抬爪并迈到脚垫",
                    "visibleChange": "湿爪和水印明显减少",
                    "warmEnding": "孩子折好毛巾，猫咪向室内走两步",
                    "targetDurationSeconds": 12,
                    "dialoguePolicy": "none",
                    "environmentIntent": "雨天玄关暖光",
                },
                "responseId": "response-1",
                "model": "planning-model",
                "requestHash": "a" * 64,
            }

        landing = ArkResultLandingService(
            sessions,
            LocalMediaStore(tmp_path),
            studio_service=service,
            downloader=ProviderMediaDownloader(
                client=httpx.Client(transport=httpx.MockTransport(lambda _request: None)),
                resolve_host=lambda _host: ("8.8.8.8",),
            ),
            ffprobe_path=Path("ffprobe"),
        )
        landing.store_result(job.id)

        snapshot = service.get_planner(project.id)
        assert snapshot.proposals[0].title == "雨天擦爪"
        assert snapshot.proposals[0].micro_event.warm_ending.endswith("走两步")
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_ark_result_landing_downloads_image_and_removes_signed_url_from_job(
    tmp_path: Path,
) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="环境落地", theme="共享暖光室内", targetDurationSeconds=12)
    )
    job_id = uuid.uuid4()
    payload = _png_bytes()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=job_id,
                    project_id=project.id,
                    kind="generate_image",
                    status="storing",
                    input_hash="e" * 64,
                    idempotency_key=f"landing-image-{job_id}",
                    provider="ark",
                    model="image-model",
                    frozen_input_json={"role": "environment"},
                    provider_result_json={
                        "url": "https://ark.cn-beijing.volces.com/environment.png",
                        "responseId": "image-response",
                        "model": "image-model",
                    },
                )
            )
        downloader = ProviderMediaDownloader(
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, content=payload)
                )
            ),
            resolve_host=lambda _host: ("8.8.8.8",),
        )
        landing = ArkResultLandingService(
            sessions,
            LocalMediaStore(tmp_path),
            studio_service=service,
            downloader=downloader,
            ffprobe_path=Path("ffprobe"),
        )

        landing.store_result(job_id)

        with sessions() as session:
            asset = session.scalar(
                select(AssetRecord).where(AssetRecord.producing_job_id == job_id)
            )
            job = session.get(JobRecord, job_id)
            assert asset is not None
            assert asset.role == "environment"
            assert asset.width == 480
            assert asset.height == 854
            assert job is not None
            assert job.provider_result_json == {
                "landedAssetId": str(asset.id),
                "responseId": "image-response",
                "model": "image-model",
            }
            assert "url" not in asset.metadata_json
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()


def test_video_diagnosis_landing_keeps_its_provider_ids_separate_from_video_ids(
    tmp_path: Path,
) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="诊断证据", theme="雨天擦爪", targetDurationSeconds=12)
    )
    video_asset = service.register_asset(
        project.id,
        role="video",
        sha256="f" * 64,
        media_type="video",
        storage_key="generated/video.mp4",
        byte_size=1024,
    )
    diagnosis_job_id = uuid.uuid4()
    try:
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=diagnosis_job_id,
                    project_id=project.id,
                    kind="diagnose_video",
                    status="storing",
                    input_hash="d" * 64,
                    idempotency_key=f"diagnosis-evidence-{diagnosis_job_id}",
                    provider="ark",
                    model="diagnostic-model",
                    provider_task_id="diagnosis-task-1",
                    frozen_input_json={"videoAssetId": str(video_asset.id)},
                    provider_result_json={
                        "requestId": "diagnosis-request-1",
                        "payload": {
                            "childIdentity": "pass",
                            "catIdentity": "pass",
                            "pairScale": "pass",
                            "styleConsistency": "pass",
                            "anatomy": "pass",
                            "technical": "pass",
                            "causalChainAndActiveEnding": "pass",
                            "warnings": [],
                        },
                    },
                )
            )

        landing = ArkResultLandingService(
            sessions,
            LocalMediaStore(tmp_path),
            studio_service=service,
            downloader=ProviderMediaDownloader(
                client=httpx.Client(transport=httpx.MockTransport(lambda _request: None)),
                resolve_host=lambda _host: ("8.8.8.8",),
            ),
            ffprobe_path=Path("ffprobe"),
        )
        landing.store_result(diagnosis_job_id)

        with sessions() as session:
            persisted = session.get(AssetRecord, video_asset.id)
            assert persisted is not None
            assert persisted.metadata_json["videoDiagnosisJobId"] == str(diagnosis_job_id)
            assert (
                persisted.metadata_json["videoDiagnosisProviderTaskId"]
                == "diagnosis-task-1"
            )
            assert (
                persisted.metadata_json["videoDiagnosisProviderRequestId"]
                == "diagnosis-request-1"
            )
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
