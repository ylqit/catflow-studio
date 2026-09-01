from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import (
    AssetRecord,
    EditVersionRecord,
    JobRecord,
    ShotPlanVersionRecord,
)


class MediaJobExecutor:
    """Materialize fake video candidates and formal EDL exports as immutable MP4 assets."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        media_store: LocalMediaStore,
        *,
        ffmpeg_path: Path,
        ffprobe_path: Path,
    ) -> None:
        self._sessions = sessions
        self._media_store = media_store
        self._ffmpeg_path = ffmpeg_path
        self._ffprobe_path = ffprobe_path

    def store_result(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            existing = session.scalar(
                select(AssetRecord).where(AssetRecord.producing_job_id == job_id)
            )
            if existing is not None:
                return
            kind = job.kind
        if kind == "diagnose_image":
            self._store_fake_diagnosis(job_id)
        elif kind == "generate_image":
            self._create_fake_image(job_id)
        elif kind == "generate_video":
            self._create_fake_video(job_id)
        elif kind == "render_export":
            self._render_edit(job_id)
        else:
            raise ValueError(f"job kind does not produce media: {kind}")

    def _store_fake_diagnosis(self, job_id: uuid.UUID) -> None:
        with self._sessions.begin() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            candidate_id = uuid.UUID(str(job.frozen_input_json["candidateAssetId"]))
            candidate = session.get(AssetRecord, candidate_id)
            if candidate is None or candidate.project_id != job.project_id:
                raise ValueError("diagnosis candidate not found")
            identity: dict[str, str] = {}
            if candidate.role in {"episode_child", "pair_scale"}:
                identity["childMatch"] = "pass"
            if candidate.role in {"episode_cat", "pair_scale"}:
                identity["catMatch"] = "pass"
            if candidate.role == "pair_scale":
                identity["pairScale"] = "pass"
            metadata = dict(candidate.metadata_json)
            metadata["qualityReport"] = {
                "identity": identity,
                "style": "pass",
                "anatomy": "pass",
                "technical": "pass",
                "warnings": [],
            }
            metadata["diagnosedByJobId"] = str(job.id)
            candidate.metadata_json = metadata

    def _create_fake_image(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            role = str(job.frozen_input_json["role"])
            project_id = job.project_id
        storage_key = f"generated/{project_id}/image/{job_id}.png"
        destination = self._media_store.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        canvas = Image.new("RGB", (720, 1280), "#e7d8c7")
        draw = ImageDraw.Draw(canvas)
        draw.ellipse((90, 100, 630, 640), fill="#f1e5d4")
        if role in {"episode_child", "pair_scale"}:
            draw.ellipse((155, 330, 340, 515), fill="#d7a187", outline="#786c63", width=5)
            draw.rounded_rectangle(
                (175, 480, 325, 910), radius=55, fill="#bc7664", outline="#786c63", width=5
            )
        if role in {"episode_cat", "pair_scale"}:
            draw.ellipse((385, 590, 585, 805), fill="#d9d7d0", outline="#786c63", width=5)
            draw.polygon([(410, 625), (440, 540), (480, 630)], fill="#b9b6ae")
            draw.polygon([(500, 625), (545, 545), (565, 650)], fill="#b9b6ae")
            draw.arc((500, 710, 650, 950), 250, 90, fill="#786c63", width=18)
        if role == "environment":
            draw.rectangle((80, 210, 640, 1070), fill="#c9b89f", outline="#786c63", width=5)
            draw.rectangle((150, 300, 570, 730), fill="#e9d8b6", outline="#786c63", width=5)
            draw.ellipse((250, 850, 470, 1060), fill="#9aaa92")
        if role == "style_board":
            palette = ("#d78368", "#869c88", "#d8b893", "#80776e", "#eee3d2")
            for index, color in enumerate(palette):
                top = 210 + index * 160
                draw.rounded_rectangle((120, top, 600, top + 120), radius=35, fill=color)
        canvas.save(destination, format="PNG", optimize=True)
        self._persist_asset(
            job_id,
            role=role,
            storage_key=storage_key,
            path=destination,
            media_type="image",
            metadata={"width": 720, "height": 1280, "generator": "fake-image-v1"},
        )

    def _create_fake_video(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            shot_plan_id = job.frozen_input_json.get("shotPlanVersionId")
            shot_plan = (
                session.get(ShotPlanVersionRecord, uuid.UUID(str(shot_plan_id)))
                if shot_plan_id
                else None
            )
            duration_seconds = shot_plan.total_duration_seconds if shot_plan is not None else 8
            project_id = job.project_id
        storage_key = f"generated/{project_id}/video/{job_id}.mp4"
        destination = self._media_store.resolve(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.stem}.partial.mp4")
        command = [
            str(self._ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0xE4D2BC:s=720x1280:r=24:d={duration_seconds}",
            "-vf",
            "format=yuv420p",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        self._run(command)
        temporary.replace(destination)
        metadata = self._probe(destination)
        self._persist_asset(
            job_id,
            role="video",
            storage_key=storage_key,
            path=destination,
            media_type="video",
            metadata=metadata,
        )

    def _render_edit(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            edit_id = uuid.UUID(str(job.frozen_input_json["editVersionId"]))
            edit = session.get(EditVersionRecord, edit_id)
            if edit is None or edit.project_id != job.project_id:
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
                select(AssetRecord).where(AssetRecord.producing_job_id == job_id)
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

    def _probe(self, path: Path) -> dict[str, object]:
        completed = self._run(
            [
                str(self._ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name:format=duration",
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
        if (width, height) != (720, 1280):
            raise ValueError(f"unexpected output size: {width}x{height}")
        if not 7_900 <= duration_ms <= 15_100:
            raise ValueError(f"unexpected output duration: {duration_ms} ms")
        return {
            "width": width,
            "height": height,
            "durationMs": duration_ms,
            "codec": stream.get("codec_name"),
        }

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
