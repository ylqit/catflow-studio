from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.service import StudioService
from catflow.domain.models import DirectorPlanPayload, LifeStoryProposalDraft
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord, JobRecord

from .project_posters import ProjectPosterGenerator
from .provider_media import LandedProviderMedia, ProviderMediaDownloader


class ArkResultLandingService:
    """Land durable Ark results into immutable local assets or structured project facts."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        media_store: LocalMediaStore,
        *,
        studio_service: StudioService,
        downloader: ProviderMediaDownloader,
        ffprobe_path: Path,
        poster_generator: ProjectPosterGenerator | None = None,
    ) -> None:
        self._sessions = sessions
        self._media_store = media_store
        self._studio_service = studio_service
        self._downloader = downloader
        self._ffprobe_path = ffprobe_path
        self._poster_generator = poster_generator

    def store_result(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            kind = job.kind
        if kind == "plan_story":
            self._store_planner(job_id)
        elif kind == "plan_shots":
            self._store_shot_plan(job_id)
        elif kind == "generate_image":
            self._store_image(job_id)
        elif kind == "diagnose_image":
            self._store_diagnosis(job_id, metadata_key="qualityReport")
        elif kind == "generate_video":
            self._store_video(job_id)
        elif kind == "regenerate_video_segment":
            self._store_video(job_id, repair_candidate=True)
        elif kind == "diagnose_video":
            self._store_diagnosis(job_id, metadata_key="videoDiagnosis")
        else:
            raise ValueError(f"Ark result landing does not own job kind: {kind}")

    def _store_planner(self, job_id: uuid.UUID) -> None:
        result = self._provider_result(job_id)
        proposal = result.get("payload")
        if not isinstance(proposal, dict):
            raise ValueError("planner result payload is missing")
        self._studio_service.complete_planner_job(
            job_id,
            LifeStoryProposalDraft.model_validate(proposal),
        )

    def _store_shot_plan(self, job_id: uuid.UUID) -> None:
        result = self._provider_result(job_id)
        payload = result.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("director result payload is missing")
        self._studio_service.complete_shot_plan_job(
            job_id,
            DirectorPlanPayload.model_validate(payload),
        )

    def _store_image(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            existing = session.scalar(
                select(AssetRecord).where(AssetRecord.producing_job_id == job_id)
            )
            if existing is not None:
                return
            project_id = job.project_id
            role = str(job.frozen_input_json["role"])
            result = dict(job.provider_result_json or {})
        url = str(result.get("url", ""))
        if not url:
            raise ValueError("image provider result URL is missing")
        storage_key = f"generated/{project_id}/image/{job_id}.png"
        landed = self._downloader.download_image(url, self._media_store.resolve(storage_key))
        asset_id = self._persist_asset(
            job_id,
            role=role,
            storage_key=storage_key,
            media_type="image",
            landed=landed,
            metadata={
                "provider": "ark",
                "responseId": result.get("responseId"),
                "model": result.get("model"),
                "width": landed.width,
                "height": landed.height,
            },
        )
        self._sanitize_result(job_id, asset_id, result)

    def _store_video(self, job_id: uuid.UUID, *, repair_candidate: bool = False) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            existing = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == job_id,
                    AssetRecord.role == ("repair_candidate" if repair_candidate else "video"),
                )
            )
            if existing is not None:
                if repair_candidate and job.video_repair_id is not None:
                    self._studio_service.mark_video_repair_candidate_ready(
                        job.video_repair_id, existing.id
                    )
                elif self._poster_generator is not None:
                    self._poster_generator.ensure_for_asset(existing.id)
                return
            project_id = job.project_id
            provider_task_id = job.provider_task_id
            video_repair_id = job.video_repair_id
            expected_duration = int(job.frozen_input_json.get("durationSeconds", 12))
            result = dict(job.provider_result_json or {})
        url = str(result.get("videoUrl", ""))
        if not url:
            raise ValueError("video provider result URL is missing")
        storage_key = (
            f"generated/{project_id}/video-repairs/{job_id}/candidate.mp4"
            if repair_candidate
            else f"generated/{project_id}/video/{job_id}.mp4"
        )
        landed = self._downloader.download_video(
            url,
            self._media_store.resolve(storage_key),
            ffprobe_path=self._ffprobe_path,
            expected_duration_seconds=expected_duration,
        )
        asset_id = self._persist_asset(
            job_id,
            role="repair_candidate" if repair_candidate else "video",
            storage_key=storage_key,
            media_type="video",
            landed=landed,
            metadata={
                "provider": "ark",
                "providerTaskId": provider_task_id,
                "providerRequestId": result.get("requestId"),
                "model": result.get("model"),
                "width": landed.width,
                "height": landed.height,
                "durationMs": landed.duration_ms,
                "codec": landed.codec,
                "ratio": result.get("ratio"),
                "resolution": result.get("resolution"),
                "durationFrames": round((landed.duration_ms or 0) * 24 / 1000),
                "frameRateNumerator": 24,
                "frameRateDenominator": 1,
            },
        )
        self._sanitize_result(job_id, asset_id, result)
        if repair_candidate:
            if video_repair_id is None:
                raise ValueError("repair candidate job has no video repair")
            self._studio_service.mark_video_repair_candidate_ready(video_repair_id, asset_id)
        elif self._poster_generator is not None:
            self._poster_generator.ensure_for_asset(asset_id)

    def _store_diagnosis(self, job_id: uuid.UUID, *, metadata_key: str) -> None:
        with self._sessions.begin() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            key = "candidateAssetId" if job.kind == "diagnose_image" else "videoAssetId"
            asset_id = uuid.UUID(str(job.frozen_input_json[key]))
            asset = session.get(AssetRecord, asset_id)
            if asset is None or asset.project_id != job.project_id:
                raise ValueError("diagnosis target asset not found")
            result = dict(job.provider_result_json or {})
            payload = result.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("diagnosis result payload is missing")
            metadata = dict(asset.metadata_json)
            metadata[metadata_key] = payload
            metadata[f"{metadata_key}JobId"] = str(job.id)
            if job.provider_task_id:
                metadata[f"{metadata_key}ProviderTaskId"] = job.provider_task_id
            request_id = result.get("requestId")
            if request_id:
                metadata[f"{metadata_key}ProviderRequestId"] = request_id
            asset.metadata_json = metadata

    def _provider_result(self, job_id: uuid.UUID) -> dict[str, object]:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None or not isinstance(job.provider_result_json, dict):
                raise ValueError("persisted provider result is missing")
            return dict(job.provider_result_json)

    def _persist_asset(
        self,
        job_id: uuid.UUID,
        *,
        role: str,
        storage_key: str,
        media_type: str,
        landed: LandedProviderMedia,
        metadata: dict[str, object],
    ) -> uuid.UUID:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == job_id,
                    AssetRecord.role == role,
                )
            )
            if existing is not None:
                return existing.id
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            asset = AssetRecord(
                project_id=job.project_id,
                producing_job_id=job.id,
                candidate_index=0,
                role=role,
                media_type=media_type,
                storage_key=storage_key,
                sha256=landed.sha256,
                byte_size=landed.byte_size,
                width=landed.width,
                height=landed.height,
                duration_ms=landed.duration_ms,
                metadata_json=metadata,
            )
            session.add(asset)
            session.flush()
            return asset.id

    def _sanitize_result(
        self, job_id: uuid.UUID, asset_id: uuid.UUID, result: dict[str, object]
    ) -> None:
        with self._sessions.begin() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            job.provider_result_json = {
                "landedAssetId": str(asset_id),
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"url", "videoUrl", "lastFrameUrl"}
                },
            }
