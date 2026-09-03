from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session, sessionmaker

from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord, JobRecord

from .ark_results import ArkResultLandingService
from .media_jobs import LocalMediaJobExecutor


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

    def prepare_segment_media(
        self,
        job_id: uuid.UUID,
        base_asset_id: uuid.UUID,
        generation_start_frame: int,
        generation_end_frame: int,
        issue_start_frame: int,
        issue_end_frame: int,
        provider_duration_seconds: int,
    ) -> tuple[Path, Path, Path]:
        if not (
            0 <= generation_start_frame < generation_end_frame
            and generation_start_frame <= issue_start_frame < issue_end_frame
            and issue_end_frame <= generation_end_frame
            and 4 <= provider_duration_seconds <= 15
        ):
            raise ValueError("invalid frozen segment media ranges")
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            asset = session.get(AssetRecord, base_asset_id)
            if (
                job is None
                or asset is None
                or asset.project_id != job.project_id
                or asset.media_type != "video"
                or job.video_repair_id is None
            ):
                raise ValueError("segment media source does not match the repair job")
            source = self._media_store.resolve(asset.storage_key)
            project_id = job.project_id
        if not source.is_file():
            raise ValueError("segment media source file is missing")

        prefix = f"generated/{project_id}/video-repairs/{job_id}"
        context_key = f"{prefix}/context.mp4"
        anchor_in_key = f"{prefix}/anchor-in.png"
        anchor_out_key = f"{prefix}/anchor-out.png"
        context = self._media_store.resolve(context_key)
        anchor_in = self._media_store.resolve(anchor_in_key)
        anchor_out = self._media_store.resolve(anchor_out_key)
        context.parent.mkdir(parents=True, exist_ok=True)

        context_frames = generation_end_frame - generation_start_frame
        pad_seconds = max(0.0, provider_duration_seconds - context_frames / 24)
        context_filter = (
            f"fps=24,trim=start_frame={generation_start_frame}:"
            f"end_frame={generation_end_frame},setpts=PTS-STARTPTS"
        )
        if pad_seconds:
            context_filter += f",tpad=stop_mode=clone:stop_duration={pad_seconds:.6f}"
        context_filter += (
            f",trim=duration={provider_duration_seconds},"
            "scale=480:854:force_original_aspect_ratio=decrease,"
            "pad=480:854:(ow-iw)/2:(oh-ih)/2:color=0x1F1C1A,setsar=1,format=yuv420p"
        )
        self._render_atomic(
            context,
            [
                str(self._ffmpeg_path),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                context_filter,
                "-an",
                "-r",
                "24",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-movflags",
                "+faststart",
            ],
        )
        self._extract_exact_frame(source, issue_start_frame, anchor_in)
        self._extract_exact_frame(source, issue_end_frame - 1, anchor_out)

        self._persist_prepared_asset(
            job_id,
            project_id,
            role="repair_context",
            storage_key=context_key,
            path=context,
            metadata={
                "frameRateNumerator": 24,
                "frameRateDenominator": 1,
                "sourceStartFrame": generation_start_frame,
                "sourceEndFrame": generation_end_frame,
                "durationFrames": provider_duration_seconds * 24,
                "paddedTailFrames": provider_duration_seconds * 24 - context_frames,
            },
        )
        self._persist_prepared_asset(
            job_id,
            project_id,
            role="repair_anchor_in",
            storage_key=anchor_in_key,
            path=anchor_in,
            metadata={"sourceFrame": issue_start_frame},
        )
        self._persist_prepared_asset(
            job_id,
            project_id,
            role="repair_anchor_out",
            storage_key=anchor_out_key,
            path=anchor_out,
            metadata={"sourceFrame": issue_end_frame - 1},
        )
        return context, anchor_in, anchor_out

    def _extract_exact_frame(self, source: Path, frame_number: int, destination: Path) -> None:
        self._render_atomic(
            destination,
            [
                str(self._ffmpeg_path),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                f"fps=24,select=eq(n\\,{frame_number})",
                "-frames:v",
                "1",
            ],
        )

    @staticmethod
    def _render_atomic(destination: Path, command: list[str]) -> None:
        temporary = destination.with_name(f"{destination.stem}.partial{destination.suffix}")
        completed = subprocess.run(
            [*command, str(temporary)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0 or not temporary.is_file():
            temporary.unlink(missing_ok=True)
            raise ValueError(
                f"ffmpeg segment media preparation failed: {completed.stderr.strip()}"
            )
        temporary.replace(destination)

    def _persist_prepared_asset(
        self,
        job_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        role: str,
        storage_key: str,
        path: Path,
        metadata: dict[str, object],
    ) -> None:
        sha256 = _sha256(path)
        width: int | None = None
        height: int | None = None
        media_type = "video" if path.suffix.lower() == ".mp4" else "image"
        if media_type == "image":
            with Image.open(path) as image:
                image.verify()
                width, height = image.size
        with self._sessions.begin() as session:
            existing = session.query(AssetRecord).filter_by(
                project_id=project_id, role=role, sha256=sha256
            ).one_or_none()
            if existing is not None:
                return
            session.add(
                AssetRecord(
                    project_id=project_id,
                    producing_job_id=job_id,
                    role=role,
                    media_type=media_type,
                    storage_key=storage_key,
                    sha256=sha256,
                    byte_size=path.stat().st_size,
                    width=width,
                    height=height,
                    duration_ms=(
                        int(metadata["durationFrames"]) * 1000 // 24
                        if media_type == "video"
                        else None
                    ),
                    metadata_json=metadata,
                )
            )


class JobResultDispatcher:
    """Route durable result storage by the job's explicit provider/lifecycle owner."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        local: LocalMediaJobExecutor,
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
        if kind == "render_export" or provider == "local_ffmpeg":
            self._local.store_result(job_id)
            return
        if provider == "ark" and self._ark is not None:
            self._ark.store_result(job_id)
            return
        raise ValueError(f"no result owner for provider={provider!r}, kind={kind!r}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
