from __future__ import annotations

import hashlib
import logging
import subprocess
import uuid
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord

LOGGER = logging.getLogger(__name__)
POSTER_ROLES = {"video", "final"}


class ProjectPosterGenerator:
    """Own idempotent, non-blocking poster extraction for locally landed videos."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        media_store: LocalMediaStore,
        *,
        ffmpeg_path: Path,
    ) -> None:
        self._sessions = sessions
        self._media_store = media_store
        self._ffmpeg_path = ffmpeg_path

    def ensure_for_asset(self, asset_id: uuid.UUID) -> uuid.UUID | None:
        try:
            return self._ensure_for_asset(asset_id)
        except Exception as exc:  # poster failure must not turn a completed video into a failure
            LOGGER.warning("project poster generation failed for asset %s: %s", asset_id, exc)
            return None

    def backfill_missing(self, *, limit: int = 200) -> tuple[int, int]:
        with self._sessions() as session:
            source_ids = list(
                session.scalars(
                    select(AssetRecord.id)
                    .where(
                        AssetRecord.role.in_(POSTER_ROLES),
                        AssetRecord.media_type == "video",
                    )
                    .order_by(AssetRecord.created_at.desc(), AssetRecord.id.desc())
                    .limit(limit)
                ).all()
            )
        processed = 0
        failed = 0
        for asset_id in source_ids:
            if self.ensure_for_asset(asset_id) is None:
                failed += 1
            else:
                processed += 1
        return processed, failed

    def _ensure_for_asset(self, asset_id: uuid.UUID) -> uuid.UUID:
        with self._sessions() as session:
            source = session.get(AssetRecord, asset_id)
            if source is None:
                raise ValueError("poster source asset not found")
            if source.role not in POSTER_ROLES or source.media_type != "video":
                raise ValueError("poster source must be a generated video or final asset")
            existing = self._existing_poster(session, source)
            if existing is not None:
                return existing.id
            if source.project_id is None:
                raise ValueError("poster source must belong to a project")
            source_path = self._media_store.resolve(source.storage_key)
            project_id = source.project_id
            producing_job_id = source.producing_job_id
            duration_ms = source.duration_ms or 0

        if not source_path.is_file():
            raise ValueError("poster source content not found")
        storage_key = f"generated/{project_id}/posters/{asset_id}.jpg"
        destination = self._media_store.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file():
            temporary = destination.with_name(f"{destination.stem}.partial.jpg")
            seek_seconds = max(0.0, duration_ms / 2000)
            command = [
                str(self._ffmpeg_path),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{seek_seconds:.3f}",
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-vf",
                (
                    "scale=360:640:force_original_aspect_ratio=decrease,"
                    "pad=360:640:(ow-iw)/2:(oh-ih)/2:color=0x1F1C1A,setsar=1"
                ),
                "-q:v",
                "3",
                str(temporary),
            ]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if completed.returncode != 0:
                temporary.unlink(missing_ok=True)
                raise RuntimeError(completed.stderr.strip()[-2_000:] or "poster extraction failed")
            temporary.replace(destination)

        with Image.open(destination) as image:
            image.verify()
        with Image.open(destination) as image:
            width, height = image.size
        if (width, height) != (360, 640):
            raise ValueError(f"unexpected poster size: {width}x{height}")

        record = AssetRecord(
            project_id=project_id,
            producing_job_id=producing_job_id,
            candidate_index=0,
            role="project_poster",
            media_type="image",
            storage_key=storage_key,
            sha256=_sha256(destination),
            byte_size=destination.stat().st_size,
            width=width,
            height=height,
            metadata_json={
                "sourceAssetId": str(asset_id),
                "generatedLocally": True,
            },
        )
        try:
            with self._sessions.begin() as session:
                source = session.get(AssetRecord, asset_id)
                if source is None:
                    raise ValueError("poster source asset disappeared")
                existing = self._existing_poster(session, source)
                if existing is not None:
                    return existing.id
                session.add(record)
                session.flush()
                return record.id
        except IntegrityError:
            with self._sessions() as session:
                source = session.get(AssetRecord, asset_id)
                if source is None:
                    raise ValueError("poster source asset disappeared") from None
                existing = self._existing_poster(session, source)
                if existing is None:
                    raise
                return existing.id

    @staticmethod
    def _existing_poster(session: Session, source: AssetRecord) -> AssetRecord | None:
        if source.producing_job_id is not None:
            by_job = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == source.producing_job_id,
                    AssetRecord.role == "project_poster",
                )
            )
            if by_job is not None:
                return by_job
        if source.project_id is None:
            return None
        return next(
            (
                poster
                for poster in session.scalars(
                    select(AssetRecord).where(
                        AssetRecord.project_id == source.project_id,
                        AssetRecord.role == "project_poster",
                    )
                ).all()
                if poster.metadata_json.get("sourceAssetId") == str(source.id)
            ),
            None,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
