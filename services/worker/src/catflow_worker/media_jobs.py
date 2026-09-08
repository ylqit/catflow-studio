from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.service import EditDecisionListDto
from catflow.domain.video_repairs import EditDecisionListV2, EditDecisionListV3
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import (
    AssetRecord,
    EditVersionRecord,
    JobRecord,
    VideoRepairRecord,
)

from .media_probe import inspect_video
from .project_posters import ProjectPosterGenerator


class LocalMediaJobExecutor:
    """Own deterministic FFmpeg jobs that create immutable local media assets."""

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
            if job.kind == "extract_continuity_frames":
                if job.frozen_input_json.get("purpose") == "shot_frame":
                    self._extract_shot_frame(job_id)
                else:
                    self._extract_continuity_frames(job_id)
                return
            if job.kind not in {"render_export", "render_edit_preview"}:
                raise ValueError(f"job kind is not a local media job: {job.kind}")
            expected_role = "edit_preview" if job.kind == "render_edit_preview" else "final"
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
            if existing is not None and expected_role != "edit_preview":
                if existing.role in {"video", "final"}:
                    self._poster_generator.ensure_for_asset(existing.id)
                return
        if expected_role == "edit_preview":
            self._render_draft_preview(job_id)
        else:
            self._render_edit(job_id)
        with self._sessions() as session:
            primary = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == job_id,
                    AssetRecord.role == expected_role,
                )
            )
            if primary is not None:
                self._poster_generator.ensure_for_asset(primary.id)

    def _extract_shot_frame(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("frame job not found")
            if (
                session.scalar(select(AssetRecord).where(AssetRecord.producing_job_id == job_id))
                is not None
            ):
                return
            frozen = dict(job.frozen_input_json)
            asset = session.get(AssetRecord, uuid.UUID(frozen["sourceVideoAssetId"]))
            if (
                asset is None
                or asset.project_id != job.project_id
                or asset.sha256 != frozen["sourceVideoSha256"]
            ):
                raise ValueError("frame source changed")
            source = self._media_store.resolve(asset.storage_key)
            frame = int(frozen["sourceFrame"])
            if not 0 <= frame < self._asset_frame_count(asset, source):
                raise ValueError("frame outside source")
            key = f"generated/{job.project_id}/shot-frames/{job_id}.png"
        path = self._media_store.resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                str(self._ffmpeg_path),
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                f"select=eq(n\\,{frame})",
                "-frames:v",
                "1",
                str(path),
            ]
        )
        self._persist_asset(
            job_id,
            role="shot_frame",
            storage_key=key,
            path=path,
            media_type="image",
            metadata={**self._probe_still(path), **frozen},
        )

    def _extract_continuity_frames(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None or job.kind != "extract_continuity_frames":
                raise ValueError("continuity frame job not found")
            frozen = dict(job.frozen_input_json)
            source_id = uuid.UUID(str(frozen["sourceVideoAssetId"]))
            source = session.get(AssetRecord, source_id)
            if (
                source is None
                or source.project_id != job.project_id
                or source.media_type != "video"
            ):
                raise ValueError("continuity source video not found")
            if source.sha256 != frozen.get("sourceVideoSha256"):
                raise ValueError("continuity source video hash changed")
            source_path = self._media_store.resolve(source.storage_key)
            if not source_path.is_file():
                raise ValueError("continuity source video content not found")
            episode_id = uuid.UUID(str(frozen["seriesEpisodeId"]))
            keyframe_seconds = [float(value) for value in frozen.get("keyframeSeconds", [])]
            if len(keyframe_seconds) > 2 or any(value < 0 for value in keyframe_seconds):
                raise ValueError("continuity keyframe timestamps are invalid")
            duration_seconds = (source.duration_ms or 0) / 1000
            if duration_seconds and any(value >= duration_seconds for value in keyframe_seconds):
                raise ValueError("continuity keyframe exceeds source duration")
            project_id = job.project_id

        destination_root = f"generated/{project_id}/continuity/{job_id}"
        frame_specs: list[tuple[str, int, float | None]] = []
        if bool(frozen.get("extractLastFrame", True)):
            frame_specs.append(("episode_last_frame", 0, None))
        frame_specs.extend(
            ("episode_keyframe", index, timestamp)
            for index, timestamp in enumerate(keyframe_seconds)
        )
        for role, candidate_index, timestamp in frame_specs:
            suffix = (
                "last-frame" if role == "episode_last_frame" else f"keyframe-{candidate_index + 1}"
            )
            storage_key = f"{destination_root}/{suffix}.png"
            destination = self._media_store.resolve(storage_key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if timestamp is None:
                command = [
                    str(self._ffmpeg_path),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-sseof",
                    "-0.05",
                    "-i",
                    str(source_path),
                ]
            else:
                command = [
                    str(self._ffmpeg_path),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{timestamp:.6f}",
                    "-i",
                    str(source_path),
                ]
            command.extend(["-frames:v", "1", "-update", "1", str(destination)])
            self._run(command)
            dimensions = self._probe_still(destination)
            self._persist_asset(
                job_id,
                role=role,
                candidate_index=candidate_index,
                storage_key=storage_key,
                path=destination,
                media_type="image",
                metadata={
                    **dimensions,
                    "seriesEpisodeId": str(episode_id),
                    "sourceVideoAssetId": str(source_id),
                    "sourceVideoSha256": source.sha256,
                    "timestampSeconds": timestamp if timestamp is not None else duration_seconds,
                },
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
            format_version = edit.format_version

        if format_version in {2, 3}:
            self._render_edit_v2(job_id, edit_id)
        else:
            self._render_edit_v1(job_id, edit_id)

    def _render_edit_v1(self, job_id: uuid.UUID, edit_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            edit = session.get(EditVersionRecord, edit_id)
            if job is None or edit is None or edit.project_id != job.project_id:
                raise ValueError("edit version not found")
            edl = job.frozen_input_json.get("edl", edit.edl_json)
            storage_key = f"generated/{job.project_id}/final/{job_id}.mp4"
        destination = self._media_store.resolve(storage_key)
        metadata = self.render_timeline(job_id, edl, destination)
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

    def _render_legacy_timeline(
        self, job_id: uuid.UUID, edl: dict[str, object], destination: Path
    ) -> dict[str, object]:
        edl = EditDecisionListDto.model_validate(edl).model_dump(mode="json", by_alias=True)
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("legacy timeline job not found")
            sources = list(edl["sourceVideoSelections"])
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

        start_seconds = int(source["startMs"]) / 1000
        duration_seconds = (int(source["endMs"]) - int(source["startMs"])) / 1000
        transition = next(iter(edl.get("transitions", [])), None)
        fade_seconds = (
            min(float(transition.get("durationMs", 0)) / 1000, duration_seconds / 2)
            if transition and transition.get("type") != "none"
            else 0
        )
        filters = [
            "fps=24",
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
            if (
                edl["audioPolicy"] == "native_fades"
                and fade_seconds > 0
                and self._audio_codec(source_path)
            ):
                command.extend(
                    [
                        "-af",
                        f"afade=t=in:st=0:d={fade_seconds:.3f},afade=t=out:st={fade_out_start:.3f}:d={fade_seconds:.3f}",
                    ]
                )
        command.append(str(temporary))
        self._run(command)
        temporary.replace(destination)
        metadata = self._probe(
            destination,
            expected_duration_seconds=duration_seconds,
            expected_frame_count=round(duration_seconds * 24),
        )
        metadata.update(
            {
                "durationFrames": metadata["frameCount"],
                "frameRateNumerator": 24,
                "frameRateDenominator": 1,
                "audioPolicy": edl["audioPolicy"],
            }
        )
        return metadata

    def _render_draft_preview(self, job_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("preview job not found")
            frozen = dict(job.frozen_input_json)
            project_id = job.project_id
            existing = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == job_id, AssetRecord.role == "edit_preview"
                )
            )
            if existing is not None:
                storage_key, asset_id, metadata = (
                    existing.storage_key,
                    existing.id,
                    dict(existing.metadata_json),
                )
        if existing is None:
            storage_key = f"generated/{project_id}/edit-previews/{job_id}.mp4"
            destination = self._media_store.resolve(storage_key)
            metadata = self.render_timeline(job_id, frozen["edl"], destination, allow_draft=True)
            metadata.update(
                {
                    "editDraftId": frozen.get("editDraftId"),
                    "editVersionId": frozen["editVersionId"],
                    "repairId": frozen.get("repairId"),
                    "timelineHash": frozen["timelineHash"],
                    "previewOnly": True,
                }
            )
            asset_id = self._persist_asset(
                job_id,
                role="edit_preview",
                storage_key=storage_key,
                path=destination,
                media_type="video",
                metadata=metadata,
            )
        destination = self._media_store.resolve(storage_key)
        if frozen.get("editDraftId") and not frozen.get("repairId"):
            with self._sessions.begin() as session:
                edit = session.get(EditVersionRecord, uuid.UUID(frozen["editVersionId"]))
                if edit is not None:
                    edit.rendered_asset_id = asset_id
        total = int(metadata["durationFrames"])
        self._prepare_preview_thumbnails(job_id, project_id, asset_id, destination, metadata)

        if frozen.get("placement"):
            self._prepare_trial_comparison(job_id, project_id, frozen, destination, total)

    def _prepare_preview_thumbnails(
        self,
        job_id: uuid.UUID,
        project_id: uuid.UUID,
        asset_id: uuid.UUID,
        destination: Path,
        metadata: dict,
        *,
        index_offset: int = 0,
    ) -> None:
        """Persist frame evidence belonging to the actual displayed file, with resumable slots."""
        total = int(metadata["durationFrames"])
        with self._sessions() as session:
            completed_thumbnails = set(
                session.scalars(
                    select(AssetRecord.candidate_index).where(
                        AssetRecord.producing_job_id == job_id, AssetRecord.role == "edit_thumbnail"
                    )
                )
            )
        for index, frame in enumerate(sorted({round(i * (total - 1) / 11) for i in range(12)})):
            if index + index_offset in completed_thumbnails:
                continue
            key = f"generated/{project_id}/edit-previews/{job_id}-{index_offset}-{frame}.jpg"
            path = self._media_store.resolve(key)
            self._run(
                [
                    str(self._ffmpeg_path),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(destination),
                    "-vf",
                    f"select=eq(n\\,{frame}),scale=160:-2",
                    "-frames:v",
                    "1",
                    str(path),
                ]
            )
            self._persist_asset(
                job_id,
                role="edit_thumbnail",
                candidate_index=index + index_offset,
                storage_key=key,
                path=path,
                media_type="image",
                metadata={
                    **self._probe_still(path),
                    "sourceFrame": frame,
                    "previewAssetId": str(asset_id),
                    **{
                        k: metadata[k]
                        for k in ("editDraftId", "editVersionId", "repairId", "timelineHash")
                    },
                },
            )

    def _prepare_trial_comparison(
        self,
        job_id: uuid.UUID,
        project_id: uuid.UUID,
        frozen: dict,
        trial_path: Path,
        total_frames: int,
    ) -> None:
        """Materialize independent, provenance-bound sources for the linked viewer."""
        with self._sessions() as session:
            existing = list(
                session.scalars(
                    select(AssetRecord).where(
                        AssetRecord.producing_job_id == job_id,
                        AssetRecord.role.in_(("edit_trial_base", "edit_comparison")),
                    )
                )
            )
        finished = {(asset.role, asset.candidate_index): asset for asset in existing}
        base_key = f"generated/{project_id}/edit-previews/{job_id}-base.mp4"
        base_path = self._media_store.resolve(base_key)
        shared = {
            "editDraftId": frozen["editDraftId"],
            "editVersionId": frozen["editVersionId"],
            "repairId": frozen["repairId"],
            "timelineHash": frozen["timelineHash"],
            "parentTimelineHash": frozen["parentTimelineHash"],
            "previewOnly": True,
            "trialJobId": str(job_id),
        }
        if ("edit_trial_base", 0) not in finished:
            facts = self.render_timeline(job_id, frozen["baseEdl"], base_path, allow_draft=True)
            self._persist_asset(
                job_id,
                role="edit_trial_base",
                storage_key=base_key,
                path=base_path,
                media_type="video",
                metadata={
                    **facts,
                    **shared,
                    "timelineHash": frozen["parentTimelineHash"],
                    "view": "before",
                },
            )
        with self._sessions() as session:
            base = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == job_id, AssetRecord.role == "edit_trial_base"
                )
            )
            trial = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.producing_job_id == job_id, AssetRecord.role == "edit_preview"
                )
            )
            repair = session.get(VideoRepairRecord, uuid.UUID(frozen["repairId"]))
            candidate = (
                session.get(AssetRecord, repair.candidate_asset_id) if repair is not None else None
            )
        if base is None or trial is None:
            raise ValueError("materialized trial sources are missing")
        self._prepare_preview_thumbnails(
            job_id, project_id, base.id, base_path, base.metadata_json, index_offset=100
        )
        if candidate is not None:
            self._prepare_preview_thumbnails(
                job_id,
                project_id,
                candidate.id,
                self._media_store.resolve(candidate.storage_key),
                {**candidate.metadata_json, **shared},
                index_offset=2000,
            )
        with self._sessions.begin() as session:
            job = session.get(JobRecord, job_id)
            job.provider_result_json = {
                **(job.provider_result_json or {}),
                "comparison": {
                    "beforeAssetId": str(base.id),
                    "afterAssetId": str(trial.id),
                    "parentEditVersionId": frozen["editVersionId"],
                    "parentTimelineHash": frozen["parentTimelineHash"],
                    "timelineHash": frozen["timelineHash"],
                    "mode": "dual_player",
                },
            }

    def _render_edit_v2(self, job_id: uuid.UUID, edit_id: uuid.UUID) -> None:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            edit = session.get(EditVersionRecord, edit_id)
            if job is None or edit is None or edit.project_id != job.project_id:
                raise ValueError("edit version not found")
            edl = job.frozen_input_json.get("edl", edit.edl_json)
            storage_key = f"generated/{job.project_id}/final/{job_id}.mp4"
        destination = self._media_store.resolve(storage_key)
        metadata = self.render_timeline(job_id, edl, destination)
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

    def render_timeline(
        self,
        job_id: uuid.UUID,
        edl: dict[str, object],
        destination: Path,
        *,
        allow_draft: bool = False,
    ) -> dict[str, object]:
        """Materialize one frozen EDL for export, draft preview, or repair input extraction."""
        if "sourceVideoSelections" in edl:
            return self._render_legacy_timeline(job_id, edl, destination)
        segmented_audio = edl.get("format") == "catflow-edl-v3"
        model = EditDecisionListV3 if segmented_audio else EditDecisionListV2
        edl = model.model_validate(edl).model_dump(mode="json", by_alias=True)
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("timeline render job not found")
            if edl.get("format") not in {"catflow-edl-v2", "catflow-edl-v3"}:
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
                        or not (
                            (allow_draft and repair.candidate_asset_id == asset.id)
                            or (
                                repair.status == "approved"
                                and repair.approved_candidate_asset_id == asset.id
                            )
                        )
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
            root = session.get(AssetRecord, root_id)
            if (
                root is None
                or root.project_id != job.project_id
                or root.sha256 != edl["rootVideoSha256"]
            ):
                raise ValueError("timeline root asset changed")
            root_path = self._media_store.resolve(root.storage_key)
            if not root_path.is_file():
                raise ValueError("timeline root content not found")
            audio_assets = []
            if segmented_audio:
                for interval in audio["segments"]:
                    asset = session.get(AssetRecord, uuid.UUID(interval["assetId"]))
                    if (
                        asset is None
                        or asset.project_id != job.project_id
                        or asset.sha256 != interval["sha256"]
                        or asset.media_type != "video"
                    ):
                        raise ValueError("audio source content changed")
                    if interval.get("repairId"):
                        repair = session.get(VideoRepairRecord, uuid.UUID(interval["repairId"]))
                        if (
                            repair is None
                            or repair.project_id != job.project_id
                            or repair.candidate_asset_id != asset.id
                        ):
                            raise ValueError("audio candidate provenance is invalid")
                    elif asset.id != root_id:
                        source_job = (
                            session.get(JobRecord, asset.producing_job_id)
                            if asset.producing_job_id
                            else None
                        )
                        if not (
                            asset.role == "shot_video"
                            and source_job is not None
                            and source_job.project_id == job.project_id
                            and source_job.frozen_input_json.get("purpose") == "shot_video"
                            and source_job.frozen_input_json.get("shotDesignHash")
                            == asset.metadata_json.get("shotDesignHash")
                        ):
                            raise ValueError("non-root audio requires candidate or shot provenance")
                    path = self._media_store.resolve(asset.storage_key)
                    if interval["sourceInFrame"] + interval[
                        "durationFrames"
                    ] > self._asset_frame_count(asset, path):
                        raise ValueError("audio interval exceeds its source video")
                    audio_assets.append(path)
            elif (
                audio.get("policy") != "preserve_original"
                or audio["assetId"] != str(root_id)
                or audio["sha256"] != root.sha256
            ):
                raise ValueError("EDL v2 must preserve root audio")
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
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f"{destination.stem}.partial.mp4")

        command = [str(self._ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y"]
        for _, path in assets:
            command.extend(["-i", str(path)])
        audio_input_index = len(assets)
        for path in audio_assets if segmented_audio else [root_path]:
            command.extend(["-i", str(path)])

        filters: list[str] = []
        adjusted_durations: list[int] = []
        for index, (adjusted_start, adjusted_end) in enumerate(adjusted_ranges):
            adjusted_duration = adjusted_end - adjusted_start
            adjusted_durations.append(adjusted_duration)
            filters.append(
                f"[{index}:v:0]fps=24,trim=start_frame={adjusted_start}:end_frame={adjusted_end},"
                "setpts=PTS-STARTPTS,"
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

        audio_present = False
        if segmented_audio:
            facts = [inspect_video(path, self._ffprobe_path) for path in audio_assets]
            # Keep a mono project mono; a prior stereo track retains its channels.
            layout = "stereo" if any(fact["audioChannels"] > 1 for fact in facts) else "mono"
            for index, (interval, fact) in enumerate(zip(audio["segments"], facts, strict=True)):
                start_sample = interval["sourceInFrame"] * 2000
                samples = interval["durationFrames"] * 2000
                if not fact["hasAudio"]:
                    if interval["requireAudio"]:
                        raise ValueError(
                            "candidate audio was requested for this trial but is absent"
                        )
                    filters.append(
                        f"anullsrc=r=48000:cl={layout},atrim=end_sample={samples}[a{index}]"
                    )
                    continue
                audio_present = True
                chain = (
                    f"[{audio_input_index + index}:a:0]aresample=48000,"
                    f"aformat=channel_layouts={layout},asetpts=PTS-STARTPTS"
                )
                if interval["fadeInMs"]:
                    chain += (
                        f",afade=t=in:st={interval['envelopeStartFrame'] / 24:.9f}"
                        f":d={interval['fadeInMs'] / 1000:.9f}"
                    )
                if interval["fadeOutMs"]:
                    end = (interval["envelopeStartFrame"] + interval["envelopeDurationFrames"]) / 24
                    chain += (
                        f",afade=t=out:st={end - interval['fadeOutMs'] / 1000:.9f}"
                        f":d={interval['fadeOutMs'] / 1000:.9f}"
                    )
                # Audio shares the exact source interval with video. Silence after a naturally
                # shorter audio stream fills its scheduled interval; video is never padded.
                chain += (
                    f",atrim=start_sample={start_sample}:end_sample={start_sample + samples},"
                    f"asetpts=PTS-STARTPTS,apad=whole_len={samples},"
                    f"atrim=end_sample={samples}[a{index}]"
                )
                filters.append(chain)
            labels = "".join(f"[a{index}]" for index in range(len(audio_assets)))
            filters.append(f"{labels}concat=n={len(audio_assets)}:v=0:a=1[aout]")
            if not audio_present:
                filters.append("[aout]anullsink")

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
        audio_codec = (
            self._audio_codec(root_path)
            if not segmented_audio
            else ("aac" if audio_present else None)
        )
        audio_transcoded = False
        if segmented_audio and audio_present:
            command.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "128k", "-ar", "48000"])
            audio_transcoded = True
        elif audio_codec is None:
            command.append("-an")
        else:
            command.extend(["-map", f"{audio_input_index}:a:0"])
            if audio_codec == "aac":
                command.extend(["-c:a", "copy"])
            else:
                command.extend(["-c:a", "aac", "-b:a", "128k"])
                audio_transcoded = True
        command.extend(["-t", f"{total_frames / 24:.9f}", str(temporary)])
        self._run(command)
        temporary.replace(destination)
        metadata = self._probe(
            destination,
            expected_duration_seconds=total_frames / 24,
            expected_frame_count=total_frames,
        )
        metadata.update(
            {
                "frameCount": total_frames,
                "durationFrames": total_frames,
                "frameRateNumerator": 24,
                "frameRateDenominator": 1,
                "audioPolicy": audio["policy"],
                "audioSourceAssetId": str(root_id) if not segmented_audio else None,
                "candidateAudioUsed": segmented_audio
                and any(item.get("repairId") for item in audio["segments"]),
                "audioCodec": audio_codec,
                "audioTranscoded": audio_transcoded,
            }
        )
        return metadata

    def _persist_asset(
        self,
        job_id: uuid.UUID,
        *,
        role: str,
        candidate_index: int = 0,
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
                    AssetRecord.candidate_index == candidate_index,
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
                candidate_index=candidate_index,
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

    def _probe_still(self, path: Path) -> dict[str, object]:
        completed = self._run(
            [
                str(self._ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name",
                "-of",
                "json",
                str(path),
            ]
        )
        document = json.loads(completed.stdout)
        stream = document["streams"][0]
        return {
            "width": int(stream["width"]),
            "height": int(stream["height"]),
            "codec": stream.get("codec_name"),
        }

    def _probe(
        self,
        path: Path,
        *,
        expected_size: tuple[int, int] = (720, 1280),
        expected_duration_seconds: float | None = None,
        expected_frame_count: int | None = None,
    ) -> dict[str, object]:
        facts = inspect_video(path, self._ffprobe_path, ffmpeg_path=self._ffmpeg_path)
        duration_ms = facts["durationMs"]
        width, height = facts["width"], facts["height"]
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
        frame_count = facts["frameCount"]
        if expected_frame_count is not None and frame_count != expected_frame_count:
            raise ValueError(f"unexpected output frame count: {frame_count}")
        return facts

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
