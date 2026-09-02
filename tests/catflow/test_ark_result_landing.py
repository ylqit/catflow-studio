from __future__ import annotations

import uuid
from io import BytesIO
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PIL import Image
from sqlalchemy import delete, select

from catflow.application.service import (
    PlannerMessageCommand,
    ProjectCreate,
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
from catflow_worker.ark_results import ArkResultLandingService
from catflow_worker.provider_media import LandedProviderMedia, ProviderMediaDownloader


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


def test_ark_segment_result_lands_as_candidate_and_never_changes_the_edit(
    tmp_path: Path,
) -> None:
    class SegmentDownloader:
        def download_video(
            self,
            _url: str,
            destination: Path,
            *,
            ffprobe_path: Path,
            expected_duration_seconds: int,
        ) -> LandedProviderMedia:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"candidate")
            return LandedProviderMedia(
                sha256="9" * 64,
                byte_size=9,
                width=480,
                height=854,
                duration_ms=expected_duration_seconds * 1000,
                codec="h264",
            )

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="Ark 片段落地", theme="雨天擦爪", targetDurationSeconds=12)
    )
    try:
        base = service.register_asset(
            project.id,
            role="video",
            media_type="video",
            sha256="7" * 64,
            metadata={"durationFrames": 288, "frameRateNumerator": 24, "frameRateDenominator": 1},
        )
        environment = service.register_asset(
            project.id, role="environment", media_type="image", sha256="8" * 64
        )
        service.select_asset(project.id, slot="video", asset_id=base.id)
        service.select_asset(project.id, slot="environment", asset_id=environment.id)
        preview = service.preview_video_repair(
            project.id,
            SegmentRepairPreviewCommand(
                baseVideoAssetId=base.id,
                issueRange={"startFrame": 96, "endFrame": 192},
                prompt="只重拍擦爪动作。",
            ),
        )
        job = service.create_video_repair_job(
            project.id,
            SegmentRepairCreateCommand(
                repairId=preview.repair_id,
                expectedInputHash=preview.input_hash,
                expectedCostMicros=0,
                idempotencyKey=f"ark-segment-landing-{project.id}",
                paidConfirmation=True,
            ),
        )
        with sessions.begin() as session:
            record = session.get(JobRecord, job.id)
            assert record is not None
            record.provider = "ark"
            record.provider_task_id = "segment-task-1"
            record.status = "storing"
            record.provider_result_json = {
                "videoUrl": "https://ark.cn-beijing.volces.com/segment.mp4",
                "requestId": "segment-request-1",
                "model": "doubao-seedance-2-0-260128",
                "resolution": "480p",
                "ratio": "9:16",
            }
        landing = ArkResultLandingService(
            sessions,
            LocalMediaStore(tmp_path),
            studio_service=service,
            downloader=SegmentDownloader(),  # type: ignore[arg-type]
            ffprobe_path=Path("ffprobe"),
        )

        landing.store_result(job.id)

        repair = service.get_video_repair(preview.repair_id)
        candidate = service.get_asset(repair.candidate_asset_id)  # type: ignore[arg-type]
        assert repair.status == "candidate_ready"
        assert candidate.role == "repair_candidate"
        assert candidate.metadata["providerTaskId"] == "segment-task-1"
        assert candidate.metadata["durationFrames"] == 144
        assert service.list_edits(project.id) == []
        assert "videoUrl" not in service.get_job(job.id).provider_result
    finally:
        with sessions.begin() as session:
            session.execute(
                delete(EnvironmentPresetRecord).where(
                    EnvironmentPresetRecord.source_project_id == project.id
                )
            )
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
