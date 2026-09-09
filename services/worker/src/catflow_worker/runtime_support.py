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
from .media_probe import inspect_video


class AssetMediaResolver:
    """Resolve immutable Asset IDs and extract diagnostic frames inside managed storage."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        media_store: LocalMediaStore,
        *,
        ffmpeg_path: Path,
        timeline_renderer: LocalMediaJobExecutor | None = None,
        ffprobe_path: Path | None = None,
    ) -> None:
        self._sessions = sessions
        self._media_store = media_store
        self._ffmpeg_path = ffmpeg_path
        self._timeline_renderer = timeline_renderer
        self._ffprobe_path = ffprobe_path

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
                if _sha256(path) != asset.sha256:
                    raise ValueError(f"frozen asset bytes changed: {asset_id}")
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

    def store_reference_preparation(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if (
                job is None
                or job.provider != "local_ffmpeg"
                or job.frozen_input_json.get("purpose") != "segment_reference"
            ):
                raise ValueError("invalid local reference preparation")
            frozen = dict(job.frozen_input_json)
        generation, issue = frozen["generationRange"], frozen["issueRange"]
        self.prepare_segment_media(
            job_id,
            uuid.UUID(frozen["baseVideoAssetId"]),
            generation["startFrame"],
            generation["endFrame"],
            issue["startFrame"],
            issue["endFrame"],
            frozen["providerDurationSeconds"],
        )

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
                or (
                    job.video_repair_id is None
                    and job.frozen_input_json.get("purpose") != "segment_reference"
                )
            ):
                raise ValueError("segment media source does not match the repair job")
            source = self._media_store.resolve(asset.storage_key)
            project_id = job.project_id
            exact_reference = job.frozen_input_json.get("referenceRevision", 0) >= 2
            frozen_timeline = job.frozen_input_json.get("inputEdl") or job.frozen_input_json.get(
                "baseEdl"
            )
            from_frame = job.frozen_input_json.get("generationMode") == "from_frame"
            anchor_start = (
                job.frozen_input_json.get("anchorStartFrame") if from_frame else issue_start_frame
            )
            anchor_end = (
                job.frozen_input_json.get("anchorEndFrame") if from_frame else issue_end_frame - 1
            )
            if exact_reference and job.frozen_input_json.get("endStatePolicy") == "replace":
                anchor_end = None
            if from_frame and not isinstance(anchor_start, int):
                raise ValueError("strict frame generation requires a selected start")
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
        if frozen_timeline is not None:
            if self._timeline_renderer is None:
                raise ValueError(
                    "frozen timeline renderer is unavailable; refusing root-video fallback"
                )
            source = context.parent / "base-timeline.mp4"
            self._timeline_renderer.render_timeline(
                job_id, frozen_timeline, source, allow_draft=True
            )

        if not from_frame:
            context_frames = generation_end_frame - generation_start_frame
            pad_seconds = (
                0 if exact_reference else max(0.0, provider_duration_seconds - context_frames / 24)
            )
            context_filter = (
                f"fps=24,trim=start_frame={generation_start_frame}:"
                f"end_frame={generation_end_frame},setpts=PTS-STARTPTS"
            )
            if pad_seconds:
                context_filter += f",tpad=stop_mode=clone:stop_duration={pad_seconds:.6f}"
            context_filter += (
                ("," if exact_reference else f",trim=duration={provider_duration_seconds},")
                + "scale=480:854:force_original_aspect_ratio=decrease,"
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
        self._extract_exact_frame(source, anchor_start, anchor_in)
        if anchor_end is not None:
            self._extract_exact_frame(source, anchor_end, anchor_out)

        if not from_frame:
            facts = {}
            if exact_reference:
                if self._ffprobe_path is None:
                    raise ValueError("actual reference inspection is unavailable")
                facts = inspect_video(context, self._ffprobe_path)
                if facts["durationFrames"] != context_frames or facts["hasAudio"]:
                    raise ValueError("reference frames or audio differ from the frozen range")
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
                    "durationFrames": context_frames
                    if exact_reference
                    else provider_duration_seconds * 24,
                    "paddedTailFrames": 0
                    if exact_reference
                    else provider_duration_seconds * 24 - context_frames,
                    "audioRemoved": True,
                    "outputDurationSeconds": provider_duration_seconds,
                    **facts,
                },
            )
        self._persist_prepared_asset(
            job_id,
            project_id,
            role="repair_anchor_in",
            storage_key=anchor_in_key,
            path=anchor_in,
            metadata={"sourceFrame": anchor_start},
        )
        if anchor_end is not None:
            self._persist_prepared_asset(
                job_id,
                project_id,
                role="repair_anchor_out",
                storage_key=anchor_out_key,
                path=anchor_out,
                metadata={"sourceFrame": anchor_end},
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
            raise ValueError(f"ffmpeg segment media preparation failed: {completed.stderr.strip()}")
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
        width: int | None = metadata.get("width")
        height: int | None = metadata.get("height")
        media_type = "video" if path.suffix.lower() == ".mp4" else "image"
        if media_type == "image":
            with Image.open(path) as image:
                image.verify()
                width, height = image.size
        with self._sessions.begin() as session:
            existing = (
                session.query(AssetRecord)
                .filter_by(producing_job_id=job_id, role=role)
                .one_or_none()
            )
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
        references: AssetMediaResolver | None = None,
    ) -> None:
        self._sessions = sessions
        self._local = local
        self._ark = ark
        self._references = references

    def store_result(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            provider = job.provider
            kind = job.kind
            reference_preparation = job.frozen_input_json.get("purpose") == "segment_reference"
        if provider == "local_ffmpeg" and reference_preparation:
            if self._references is None:
                raise ValueError("reference preparation owner is not configured")
            self._references.store_reference_preparation(job_id)
            return
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
