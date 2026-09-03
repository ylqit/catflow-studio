from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import (
    AssetRecord,
    EditVersionRecord,
    JobRecord,
    VideoRepairRecord,
)

from .project_posters import ProjectPosterGenerator


class LocalMediaJobExecutor:
    """Render immutable EDL exports and their local project posters."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        media_store: LocalMediaStore,
        *,
        ffmpeg_path: Path,
        ffprobe_path: Path,
        poster_generator: ProjectPosterGenerator | None = None,
    ) -> None:
        self._sessions = sessions
        self._media_store = media_store
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path
        self._poster_generator = poster_generator or ProjectPosterGenerator(
            sessions,
            media_store,
            ffmpeg_path=ffmpeg_path,
        )

    def store_result(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            if job.kind != "render_export":
                raise ValueError(f"job kind is not a local media job: {job.kind}")
            expected_role = "final"
            existing = (
                session.scalar(
                    select(AssetRecord).where(
                        AssetRecord.producing_job_id == job_id,
                        AssetRecord.role == expected_role,
                    )
                )
                if expected_role
                else None
            )
            if existing is not None:
                if existing.role in {"video", "final"}:
                    self._poster_generator.ensure_for_asset(existing.id)
                return
        self._render_edit(job_id)
        with self._sessions() as session:
            primary = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == job_id,
                    AssetRecord.role == "final",
                )
            )
            if primary is not None:
                self._poster_generator.ensure_for_asset(primary.id)

    def _render_edit(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            edit_id = uuid.UUID(str(job.frozen_input_json["editVersionId"]))
            edit = session.get(EditVersionRecord, edit_id)
            if edit is None or edit.project_id != job.project_id:
                raise ValueError("edit version not found")
            format_version = edit.format_version

        if format_version == 2:
            self._render_edit_v2(job_id, edit_id)
        else:
            self._render_edit_v1(job_id, edit_id)

    def _render_edit_v1(self, job_id: uuid.UUID, edit_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            edit = session.get(EditVersionRecord, edit_id)
            if job is None or edit is None or edit.project_id != job.project_id:
                raise ValueError("edit version not found")
            sources = list(edit.edl_json["sourceVideoSelections"])
            if len(sources) != 1:
                raise ValueError("the first CatFlow renderer accepts one selected video")
            source = sources[0]
            source_asset = session.get(AssetRecord, uuid.UUID(str(source["assetId"])))
            if source_asset is None or source_asset.project_id != job.project_id:
                raise ValueError("edit source asset not found")
            if source_asset.sha256 != source["sha256"]:
                raise ValueError("edit source hash changed")
            source_path = self._media_store.resolve(source_asset.storage_key)
            if not source_path.is_file():
                raise ValueError("edit source content not found")
            project_id = job.project_id
            edl = edit.edl_json

        start_seconds = int(source["startMs"]) / 1000
        duration_seconds = (int(source["endMs"]) - int(source["startMs"])) / 1000
        transition = next(iter(edl.get("transitions", [])), None)
        fade_seconds = (
            min(float(transition.get("durationMs", 0)) / 1000, duration_seconds / 2)
            if transition and transition.get("type") != "none"
            else 0
        )
        filters = [
            "scale=720:1280:force_original_aspect_ratio=decrease",
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=0x1F1C1A",
            "setsar=1",
            "format=yuv420p",
        ]
        if fade_seconds > 0:
            fade_out_start = max(0, duration_seconds - fade_seconds)
            filters.extend(
                [
                    f"fade=t=in:st=0:d={fade_seconds:.3f}",
                    f"fade=t=out:st={fade_out_start:.3f}:d={fade_seconds:.3f}",
                ]
            )
        storage_key = f"generated/{project_id}/final/{job_id}.mp4"
        destination = self._media_store.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.stem}.partial.mp4")
        command = [
            str(self._ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_seconds:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-i",
            str(source_path),
            "-map",
            "0:v:0",
            "-vf",
            ",".join(filters),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-movflags",
            "+faststart",
        ]
        if edl["audioPolicy"] == "mute":
            command.append("-an")
        else:
            command.extend(["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"])
        command.append(str(temporary))
        self._run(command)
        temporary.replace(destination)
        metadata = self._probe(destination)
        asset_id = self._persist_asset(
            job_id,
            role="final",
            storage_key=storage_key,
            path=destination,
            media_type="video",
            metadata=metadata,
        )
        with self._sessions.begin() as session:
            edit = session.get(EditVersionRecord, edit_id)
            if edit is not None:
                edit.rendered_asset_id = asset_id
                edit.status = "rendered"

    def _render_edit_v2(self, job_id: uuid.UUID, edit_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            edit = session.get(EditVersionRecord, edit_id)
            if job is None or edit is None or edit.project_id != job.project_id:
                raise ValueError("edit version not found")
            edl = edit.edl_json
            if edl.get("format") != "catflow-edl-v2":
                raise ValueError("edit format version does not match its EDL")
            frame_rate = edl["frameRate"]
            if frame_rate != {"numerator": 24, "denominator": 1}:
                raise ValueError("CatFlow EDL v2 requires the 24 fps edit time base")
            segments = list(edl["videoSegments"])
            if not segments:
                raise ValueError("EDL v2 must contain at least one video segment")
            transitions_by_boundary = {
                int(item["afterSegmentIndex"]): item for item in edl.get("transitions", [])
            }
            if len(transitions_by_boundary) != len(edl.get("transitions", [])):
                raise ValueError("a segment boundary can have only one transition")

            assets: list[tuple[AssetRecord, Path]] = []
            for segment in segments:
                asset = session.get(AssetRecord, uuid.UUID(str(segment["assetId"])))
                if asset is None or asset.project_id != job.project_id:
                    raise ValueError("EDL v2 segment asset not found")
                if asset.media_type != "video" or asset.sha256 != segment["sha256"]:
                    raise ValueError("EDL v2 segment asset changed")
                if segment["origin"] == "repair_candidate":
                    repair_id = segment.get("repairId")
                    repair = (
                        session.get(VideoRepairRecord, uuid.UUID(str(repair_id)))
                        if repair_id
                        else None
                    )
                    if (
                        repair is None
                        or repair.project_id != job.project_id
                        or repair.status != "approved"
                        or repair.approved_candidate_asset_id != asset.id
                    ):
                        raise ValueError("repair candidate is not approved for this timeline")
                elif segment["origin"] != "base_video":
                    raise ValueError("unknown EDL v2 segment origin")
                path = self._media_store.resolve(asset.storage_key)
                if not path.is_file():
                    raise ValueError("EDL v2 segment content not found")
                assets.append((asset, path))

            root_id = uuid.UUID(str(edl["rootVideoAssetId"]))
            audio = edl["audio"]
            if audio.get("policy") != "preserve_original":
                raise ValueError("EDL v2 must preserve the original audio")
            if uuid.UUID(str(audio["assetId"])) != root_id:
                raise ValueError("EDL v2 audio must reference the root video")
            root = session.get(AssetRecord, root_id)
            if (
                root is None
                or root.project_id != job.project_id
                or root.sha256 != edl["rootVideoSha256"]
                or root.sha256 != audio["sha256"]
            ):
                raise ValueError("EDL v2 root audio asset changed")
            root_path = self._media_store.resolve(root.storage_key)
            if not root_path.is_file():
                raise ValueError("EDL v2 root audio content not found")
            project_id = job.project_id

        boundary_frames: list[int] = []
        for index in range(len(segments) - 1):
            transition = transitions_by_boundary.get(index)
            if transition is None or transition["type"] == "cut":
                frames = 0
            elif transition["type"] == "dissolve":
                frames = int(transition["durationFrames"])
                if frames not in {2, 4, 6}:
                    raise ValueError("dissolve duration must be 2, 4, or 6 frames")
            else:
                raise ValueError("unknown EDL v2 transition")
            boundary_frames.append(frames)

        left_handles = [0, *(frames // 2 for frames in boundary_frames)]
        right_handles = [*(frames // 2 for frames in boundary_frames), 0]
        adjusted_ranges: list[tuple[int, int]] = []
        for index, (segment, (asset, path)) in enumerate(zip(segments, assets, strict=True)):
            source_in = int(segment["sourceInFrame"])
            duration = int(segment["durationFrames"])
            if source_in < 0 or duration <= 0:
                raise ValueError("EDL v2 segment range is invalid")
            adjusted_start = source_in - left_handles[index]
            adjusted_end = source_in + duration + right_handles[index]
            available_frames = self._asset_frame_count(asset, path)
            if adjusted_start < 0 or adjusted_end > available_frames:
                raise ValueError("EDL v2 transition exceeds available source handles")
            adjusted_ranges.append((adjusted_start, adjusted_end))

        total_frames = sum(int(segment["durationFrames"]) for segment in segments)
        storage_key = f"generated/{project_id}/final/{job_id}.mp4"
        destination = self._media_store.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.stem}.partial.mp4")

        command = [str(self._ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y"]
        for _, path in assets:
            command.extend(["-i", str(path)])
        audio_input_index = len(assets)
        command.extend(["-i", str(root_path)])

        filters: list[str] = []
        adjusted_durations: list[int] = []
        for index, (adjusted_start, adjusted_end) in enumerate(adjusted_ranges):
            adjusted_duration = adjusted_end - adjusted_start
            adjusted_durations.append(adjusted_duration)
            filters.append(
                f"[{index}:v:0]trim=start_frame={adjusted_start}:end_frame={adjusted_end},"
                "setpts=PTS-STARTPTS,fps=24,"
                "scale=720:1280:force_original_aspect_ratio=decrease,"
                "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=0x1F1C1A,"
                f"setsar=1,format=yuv420p[s{index}]"
            )

        current_label = "s0"
        current_frames = adjusted_durations[0]
        for index, overlap_frames in enumerate(boundary_frames):
            next_label = f"s{index + 1}"
            output_label = f"joined{index}"
            if overlap_frames:
                offset_seconds = (current_frames - overlap_frames) / 24
                duration_seconds = overlap_frames / 24
                filters.append(
                    f"[{current_label}][{next_label}]xfade=transition=fade:"
                    f"duration={duration_seconds:.9f}:offset={offset_seconds:.9f}"
                    f"[{output_label}]"
                )
                current_frames += adjusted_durations[index + 1] - overlap_frames
            else:
                filters.append(f"[{current_label}][{next_label}]concat=n=2:v=1:a=0[{output_label}]")
                current_frames += adjusted_durations[index + 1]
            current_label = output_label
        if current_frames != total_frames:
            raise ValueError("EDL v2 transition math changed the total frame count")
        filters.append(f"[{current_label}]trim=end_frame={total_frames},setpts=PTS-STARTPTS[vout]")

        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[vout]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-r",
                "24",
                "-frames:v",
                str(total_frames),
                "-movflags",
                "+faststart",
            ]
        )
        audio_codec = self._audio_codec(root_path)
        if audio_codec is None:
            command.append("-an")
            audio_transcoded = False
        else:
            command.extend(["-map", f"{audio_input_index}:a:0"])
            if audio_codec == "aac":
                command.extend(["-c:a", "copy"])
                audio_transcoded = False
            else:
                command.extend(["-c:a", "aac", "-b:a", "128k"])
                audio_transcoded = True
        command.extend(["-t", f"{total_frames / 24:.9f}", str(temporary)])
        self._run(command)
        temporary.replace(destination)
        metadata = self._probe(
            destination,
            expected_duration_seconds=round(total_frames / 24),
            expected_frame_count=total_frames,
        )
        metadata.update(
            {
                "frameCount": total_frames,
                "durationFrames": total_frames,
                "frameRateNumerator": 24,
                "frameRateDenominator": 1,
                "audioPolicy": "preserve_original",
                "audioSourceAssetId": str(root_id),
                "candidateAudioUsed": False,
                "audioCodec": audio_codec,
                "audioTranscoded": audio_transcoded,
            }
        )
        asset_id = self._persist_asset(
            job_id,
            role="final",
            storage_key=storage_key,
            path=destination,
            media_type="video",
            metadata=metadata,
        )
        with self._sessions.begin() as session:
            edit = session.get(EditVersionRecord, edit_id)
            if edit is not None:
                edit.rendered_asset_id = asset_id
                edit.status = "rendered"

    def _persist_asset(
        self,
        job_id: uuid.UUID,
        *,
        role: str,
        storage_key: str,
        path: Path,
        media_type: str,
        metadata: dict[str, object],
    ) -> uuid.UUID:
        digest = _sha256(path)
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
            record = AssetRecord(
                project_id=job.project_id,
                producing_job_id=job.id,
                candidate_index=0,
                role=role,
                media_type=media_type,
                storage_key=storage_key,
                sha256=digest,
                byte_size=path.stat().st_size,
                width=int(metadata["width"]),
                height=int(metadata["height"]),
                duration_ms=(int(metadata["durationMs"]) if "durationMs" in metadata else None),
                metadata_json=metadata,
            )
            session.add(record)
            session.flush()
            return record.id

    def _probe(
        self,
        path: Path,
        *,
        expected_size: tuple[int, int] = (720, 1280),
        expected_duration_seconds: int | None = None,
        expected_frame_count: int | None = None,
    ) -> dict[str, object]:
        completed = self._run(
            [
                str(self._ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=width,height,codec_name,nb_read_frames:format=duration",
                "-of",
                "json",
                str(path),
            ]
        )
        document = json.loads(completed.stdout)
        stream = document["streams"][0]
        duration_ms = round(float(document["format"]["duration"]) * 1000)
        width = int(stream["width"])
        height = int(stream["height"])
        if (width, height) != expected_size:
            raise ValueError(f"unexpected output size: {width}x{height}")
        if expected_duration_seconds is not None and not (
            expected_duration_seconds * 1000 - 100
            <= duration_ms
            <= expected_duration_seconds * 1000 + 100
        ):
            raise ValueError(f"unexpected output duration: {duration_ms} ms")
        if expected_duration_seconds is None and not 7_900 <= duration_ms <= 15_100:
            raise ValueError(f"unexpected output duration: {duration_ms} ms")
        frame_count = int(stream.get("nb_read_frames") or 0)
        if expected_frame_count is not None and frame_count != expected_frame_count:
            raise ValueError(f"unexpected output frame count: {frame_count}")
        return {
            "width": width,
            "height": height,
            "durationMs": duration_ms,
            "codec": stream.get("codec_name"),
            "frameCount": frame_count,
        }

    def _asset_frame_count(self, asset: AssetRecord, path: Path) -> int:
        metadata_frames = asset.metadata_json.get("durationFrames")
        if metadata_frames is not None:
            return int(metadata_frames)
        completed = self._run(
            [
                str(self._ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(path),
            ]
        )
        return int(completed.stdout.strip())

    def _audio_codec(self, path: Path) -> str | None:
        completed = self._run(
            [
                str(self._ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(path),
            ]
        )
        return completed.stdout.strip() or None

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip()[-2_000:] or "media command failed")
        return completed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
