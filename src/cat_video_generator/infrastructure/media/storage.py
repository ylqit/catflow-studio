"""Immutable local media storage and non-destructive FFmpeg operations."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
from PIL import Image

from ...application.ports import LandedAsset
from ...domain.rendering import (
    ProjectSequencePlan,
    SequenceTransitionType,
)


class AssetStorageError(RuntimeError):
    pass


class LocalAssetStore:
    def __init__(
        self,
        *,
        work_root: Path,
        asset_root: Path,
        ffmpeg_path: Path | None = None,
        max_bytes: int = 2_000_000_000,
    ) -> None:
        self._work_root = work_root.expanduser().resolve()
        self._asset_root = asset_root.expanduser().resolve()
        self._ffmpeg_path = None if ffmpeg_path is None else ffmpeg_path.expanduser().resolve()
        self._max_bytes = max_bytes
        if (
            self._work_root.drive
            and self._asset_root.drive
            and self._work_root.drive.lower() != self._asset_root.drive.lower()
        ):
            raise AssetStorageError("work and asset roots must be on the same volume")

    def download(self, url: str, *, suffix: str) -> LandedAsset:
        self._work_root.mkdir(parents=True, exist_ok=True)
        temporary = self._work_root / f".download-{uuid.uuid4().hex}.part"
        digest = hashlib.sha256()
        byte_size = 0
        try:
            with (
                httpx.Client(
                    follow_redirects=True, timeout=httpx.Timeout(120, connect=15)
                ) as client,
                client.stream("GET", url) as response,
            ):
                response.raise_for_status()
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        output.write(chunk)
                        digest.update(chunk)
                        byte_size += len(chunk)
                        if byte_size > self._max_bytes:
                            raise AssetStorageError("provider media exceeds configured size limit")
                    output.flush()
                    os.fsync(output.fileno())
            if byte_size == 0:
                raise AssetStorageError("provider returned an empty media file")
            extension = suffix if suffix.startswith(".") else f".{suffix}"
            return self._land_temp(temporary, digest.hexdigest(), byte_size, extension)
        except (OSError, httpx.HTTPError) as exc:
            raise AssetStorageError(f"media download failed: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def import_local(self, path: Path) -> LandedAsset:
        source = path.expanduser().resolve()
        if not source.is_file():
            raise AssetStorageError(f"local media does not exist: {source}")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = (
            self._asset_root
            / "imported"
            / "sha256"
            / digest[:2]
            / f"{digest}{source.suffix.lower()}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            temporary = destination.with_suffix(destination.suffix + ".part")
            shutil.copy2(source, temporary)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
                temporary.unlink(missing_ok=True)
                raise AssetStorageError("copied media hash does not match source")
            os.replace(temporary, destination)
        return LandedAsset(destination, digest, destination.stat().st_size)

    def crop_local(self, path: Path, *, box: tuple[int, int, int, int]) -> LandedAsset:
        source = path.expanduser().resolve()
        self._work_root.mkdir(parents=True, exist_ok=True)
        temporary = self._work_root / f".crop-{uuid.uuid4().hex}.png"
        try:
            with Image.open(source) as image:
                left, top, right, bottom = box
                if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                    raise AssetStorageError("crop box is outside the source image")
                image.crop(box).save(temporary, format="PNG")
            return self.import_local(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def compose_sequence(
        self,
        paths: tuple[Path, ...],
        plan: ProjectSequencePlan,
    ) -> LandedAsset:
        """Render the validated sequence with cuts, black fades and dissolves."""

        ffmpeg = self._require_ffmpeg()
        if not paths:
            raise AssetStorageError("a project sequence needs at least one clip")
        if len(paths) != len(plan.clips):
            raise AssetStorageError("sequence source count does not match the render plan")
        resolved = tuple(path.expanduser().resolve() for path in paths)
        if any(not path.is_file() for path in resolved):
            raise AssetStorageError("a project sequence source clip is missing")
        self._work_root.mkdir(parents=True, exist_ok=True)
        output = self._work_root / f".sequence-{uuid.uuid4().hex}.mp4"
        command = [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y"]
        for path in resolved:
            command.extend(("-i", str(path)))
        filters: list[str] = []
        durations = [
            (clip.source_end_ms - clip.source_start_ms) / 1000
            for clip in plan.clips
        ]
        for index, duration in enumerate(durations):
            # `concat` emits AVTB (1/1_000_000). Keep every source on that
            # time base after frame-rate normalization so a later `xfade`
            # can consume either a raw clip or a preceding concat result.
            video_filters = "setpts=PTS-STARTPTS,fps=30,settb=AVTB,setsar=1,format=yuv420p"
            audio_filters = "asetpts=PTS-STARTPTS,aformat=sample_rates=48000:channel_layouts=stereo"
            incoming = plan.clips[index].transition_from_previous
            outgoing = (
                None
                if index + 1 == len(plan.clips)
                else plan.clips[index + 1].transition_from_previous
            )
            if incoming is not None and incoming.type is SequenceTransitionType.FADE_BLACK:
                seconds = incoming.duration_ms / 1000
                video_filters += f",fade=t=in:st=0:d={seconds:.3f}"
                audio_filters += f",afade=t=in:st=0:d={seconds:.3f}"
            if (
                index == 0
                and plan.intro_transition is not None
                and plan.intro_transition.type is SequenceTransitionType.FADE_BLACK
            ):
                seconds = plan.intro_transition.duration_ms / 1000
                video_filters += f",fade=t=in:st=0:d={seconds:.3f}"
                audio_filters += f",afade=t=in:st=0:d={seconds:.3f}"
            if outgoing is not None and outgoing.type is SequenceTransitionType.FADE_BLACK:
                seconds = outgoing.duration_ms / 1000
                start = duration - seconds
                video_filters += f",fade=t=out:st={start:.3f}:d={seconds:.3f}"
                audio_filters += f",afade=t=out:st={start:.3f}:d={seconds:.3f}"
            if (
                index + 1 == len(plan.clips)
                and plan.outro_transition is not None
                and plan.outro_transition.type is SequenceTransitionType.FADE_BLACK
            ):
                seconds = plan.outro_transition.duration_ms / 1000
                start = duration - seconds
                video_filters += f",fade=t=out:st={start:.3f}:d={seconds:.3f}"
                audio_filters += f",afade=t=out:st={start:.3f}:d={seconds:.3f}"
            filters.append(f"[{index}:v]{video_filters}[v{index}]")
            filters.append(
                f"[{index}:a]{audio_filters}[a{index}]"
            )
        current_video = "v0"
        current_audio = "a0"
        current_duration = durations[0]
        for index in range(1, len(resolved)):
            transition = plan.clips[index].transition_from_previous
            transition_type = (
                SequenceTransitionType.CUT if transition is None else transition.type
            )
            video_out = "vout" if index + 1 == len(resolved) else f"vm{index}"
            audio_out = "aout" if index + 1 == len(resolved) else f"am{index}"
            if transition_type is SequenceTransitionType.CROSS_DISSOLVE:
                if transition is None:
                    raise AssetStorageError("cross dissolve is missing its transition settings")
                seconds = transition.duration_ms / 1000
                offset = current_duration - seconds
                filters.append(
                    f"[{current_video}][v{index}]xfade=transition=fade:"
                    f"duration={seconds:.3f}:offset={offset:.3f}[{video_out}]"
                )
                filters.append(
                    f"[{current_audio}][a{index}]acrossfade=d={seconds:.3f}:"
                    f"c1=tri:c2=tri[{audio_out}]"
                )
                current_duration += durations[index] - seconds
            else:
                filters.append(
                    f"[{current_video}][{current_audio}][v{index}][a{index}]"
                    f"concat=n=2:v=1:a=1[{video_out}][{audio_out}]"
                )
                current_duration += durations[index]
            current_video = video_out
            current_audio = audio_out
        if len(resolved) == 1:
            filters.append("[v0]null[vout]")
            filters.append("[a0]anull[aout]")
        command.extend(
            (
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output),
            )
        )
        try:
            _run(command, timeout=1800, label="project sequence")
            return self.import_local(output)
        finally:
            output.unlink(missing_ok=True)

    def render_range_replacement(
        self,
        *,
        base_path: Path,
        replacement_path: Path,
        replacement_duration_ms: int,
        start_ms: int,
        end_ms: int,
    ) -> LandedAsset:
        """Replace image content for one interval and preserve the base audio track."""

        ffmpeg = self._require_ffmpeg()
        base = base_path.expanduser().resolve()
        replacement = replacement_path.expanduser().resolve()
        if not base.is_file() or not replacement.is_file():
            raise AssetStorageError("range edit source or replacement is missing")
        if not 0 <= start_ms < end_ms or replacement_duration_ms <= 0:
            raise AssetStorageError("range edit timing is invalid")
        self._work_root.mkdir(parents=True, exist_ok=True)
        output = self._work_root / f".range-edit-{uuid.uuid4().hex}.mp4"
        target_seconds = (end_ms - start_ms) / 1000
        replacement_seconds = replacement_duration_ms / 1000
        filters: list[str] = []
        inputs: list[str] = []
        if start_ms > 0:
            filters.append(
                f"[0:v]trim=0:{start_ms / 1000:.3f},setpts=PTS-STARTPTS,"
                "settb=AVTB,fps=30,setsar=1,format=yuv420p[vpre]"
            )
            inputs.append("[vpre]")
        ratio = target_seconds / replacement_seconds
        filters.append(
            f"[1:v]trim=0:{replacement_seconds:.3f},setpts={ratio:.9f}*(PTS-STARTPTS),"
            "settb=AVTB,fps=30,setsar=1,format=yuv420p[vreplace]"
        )
        inputs.append("[vreplace]")
        filters.append(
            f"[0:v]trim=start={end_ms / 1000:.3f},setpts=PTS-STARTPTS,"
            "settb=AVTB,fps=30,setsar=1,format=yuv420p[vpost]"
        )
        inputs.append("[vpost]")
        filters.append(f"{''.join(inputs)}concat=n={len(inputs)}:v=1:a=0[vout]")
        command = [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(base),
            "-i",
            str(replacement),
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
        try:
            _run(command, timeout=900, label="range edit")
            return self.import_local(output)
        finally:
            output.unlink(missing_ok=True)

    def _land_temp(
        self, temporary: Path, sha256: str, byte_size: int, extension: str
    ) -> LandedAsset:
        destination = (
            self._asset_root / "generated" / "sha256" / sha256[:2] / f"{sha256}{extension.lower()}"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, destination)
        return LandedAsset(destination, sha256, byte_size)

    def _require_ffmpeg(self) -> Path:
        if self._ffmpeg_path is None or not self._ffmpeg_path.is_file():
            raise AssetStorageError("FFmpeg is required for local video editing")
        return self._ffmpeg_path


def _run(command: list[str], *, timeout: int, label: str) -> None:
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = (
            exc.stderr.strip()[-1200:]
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        raise AssetStorageError(f"{label} failed: {detail}") from exc
