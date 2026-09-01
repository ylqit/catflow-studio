from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord, JobRecord

from .ark_results import ArkResultLandingService
from .media_jobs import MediaJobExecutor


class AssetMediaResolver:
    """Resolve immutable Asset IDs and extract diagnostic frames inside managed storage."""

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

    def resolve_paths(self, asset_ids: tuple[uuid.UUID, ...]) -> tuple[Path, ...]:
        paths: list[Path] = []
        with self._sessions() as session:
            for asset_id in asset_ids:
                asset = session.get(AssetRecord, asset_id)
                if asset is None:
                    raise ValueError(f"frozen asset is missing: {asset_id}")
                path = self._media_store.resolve(asset.storage_key)
                if not path.is_file():
                    raise ValueError(f"frozen asset content is missing: {asset_id}")
                paths.append(path)
        return tuple(paths)

    def extract_video_frames(
        self, asset_id: uuid.UUID, timestamps: tuple[float, ...]
    ) -> tuple[Path, ...]:
        if not timestamps:
            raise ValueError("video diagnosis requires timestamps")
        (source,) = self.resolve_paths((asset_id,))
        output_directory = self._media_store.resolve(f"work/video-diagnosis/{asset_id}")
        output_directory.mkdir(parents=True, exist_ok=True)
        frames: list[Path] = []
        for index, timestamp in enumerate(timestamps):
            if timestamp < 0:
                raise ValueError("video diagnosis timestamp cannot be negative")
            destination = output_directory / f"{index:02d}-{timestamp:.1f}.png"
            completed = subprocess.run(
                [
                    str(self._ffmpeg_path),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    str(destination),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0 or not destination.is_file():
                raise ValueError(f"ffmpeg frame extraction failed: {completed.stderr.strip()}")
            frames.append(destination)
        return tuple(frames)


class JobResultDispatcher:
    """Route durable result storage by the job's explicit provider/lifecycle owner."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        local: MediaJobExecutor,
        ark: ArkResultLandingService | None,
    ) -> None:
        self._sessions = sessions
        self._local = local
        self._ark = ark

    def store_result(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            provider = job.provider
            kind = job.kind
        if kind == "render_export" or provider in {"fake", "local_ffmpeg"}:
            self._local.store_result(job_id)
            return
        if provider == "ark" and self._ark is not None:
            self._ark.store_result(job_id)
            return
        raise ValueError(f"no result owner for provider={provider!r}, kind={kind!r}")
