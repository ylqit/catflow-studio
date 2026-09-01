"""Provider execution for non-destructive universal-canvas video edits."""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any, Protocol

from ..domain.rendering import MediaSource, build_edit_input_plan
from ..domain.workflow import StepStatus
from .ports import (
    AssetStore,
    FrameExtractor,
    GatewayError,
    LandedAsset,
    MediaGateway,
    MediaProbe,
    StoredAsset,
    VideoTaskResult,
)
from .universal_media_worker import MediaExecutionResult


class UniversalVideoEditRepository(Protocol):
    def video_edit_anchor_work(self, step_id: uuid.UUID) -> dict[str, object]: ...

    def record_control_anchor_input(
        self,
        step_id: uuid.UUID,
        *,
        boundary_frame: LandedAsset,
    ) -> str: ...

    def complete_control_anchor(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
    ) -> str: ...

    def video_edit_video_work(self, step_id: uuid.UUID) -> dict[str, object]: ...

    def record_video_edit_submission(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None: ...

    def record_video_edit_inputs(
        self,
        step_id: uuid.UUID,
        *,
        input_plan: dict[str, Any],
        input_assets: tuple[StoredAsset, ...],
    ) -> None: ...

    def complete_video_edit(
        self,
        step_id: uuid.UUID,
        *,
        provider_segment: LandedAsset,
        full_video: LandedAsset,
        provider_url: str,
        provider_model: str,
        replacement_qc: dict[str, Any],
        full_qc: dict[str, Any],
    ) -> dict[str, str]: ...

    def record_failed_video_edit_candidate(
        self,
        step_id: uuid.UUID,
        *,
        provider_segment: LandedAsset,
        provider_url: str,
        provider_model: str,
        replacement_qc: dict[str, Any],
    ) -> str: ...


class UniversalVideoEditExecutor:
    """Runs one control-anchor or one provider video step from persisted intent."""

    def __init__(
        self,
        *,
        repository: UniversalVideoEditRepository,
        gateway: MediaGateway,
        asset_store: AssetStore,
        media_probe: MediaProbe,
        frame_extractor: FrameExtractor | None,
        resolution: str,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._asset_store = asset_store
        self._media_probe = media_probe
        self._frame_extractor = frame_extractor
        self._resolution = resolution

    def execute(
        self,
        step_id: uuid.UUID,
        *,
        operation_key: str,
    ) -> MediaExecutionResult:
        if operation_key.startswith("video:edit-anchor:"):
            return self._execute_control_anchor(step_id)
        if operation_key.startswith("video:edit-recipe:"):
            return self._execute_video_edit(step_id)
        raise ValueError(f"unsupported media operation {operation_key}")

    def _execute_control_anchor(self, step_id: uuid.UUID) -> MediaExecutionResult:
        work = self._repository.video_edit_anchor_work(step_id)
        extractor = self._require_frame_extractor()
        source = _stored_asset(work["source"])
        timestamp_ms = int(work["timestampMs"])
        extracted = extractor.extract_frames_at(
            source,
            timestamps_ms=(timestamp_ms,),
        )
        try:
            boundary = self._asset_store.import_local(extracted[0])
        finally:
            for path in extracted:
                path.unlink(missing_ok=True)
        self._repository.record_control_anchor_input(
            step_id,
            boundary_frame=boundary,
        )
        result = self._gateway.generate_image(
            prompt=str(work["prompt"]),
            reference_paths=(boundary.path, *tuple(work["referencePaths"])),  # type: ignore[arg-type]
        )
        landed = self._asset_store.download(result.url, suffix=".png")
        asset_id = self._repository.complete_control_anchor(
            step_id,
            landed=landed,
            provider_url=result.url,
            provider_model=result.model,
        )
        return MediaExecutionResult(
            payload={"assetId": asset_id},
            status=StepStatus.SUCCEEDED,
        )

    def _execute_video_edit(self, step_id: uuid.UUID) -> MediaExecutionResult:
        work = self._repository.video_edit_video_work(step_id)
        if not bool(work["ready"]):
            return MediaExecutionResult(
                payload={"status": "waiting_for_control_anchors"},
                status=StepStatus.PENDING,
            )
        source = _stored_asset(work["source"])
        start_ms = int(work["startMs"])
        end_ms = int(work["endMs"])
        provider_duration = min(13, max(4, math.ceil((end_ms - start_ms) / 1000)))
        anchors = tuple(_stored_asset(item) for item in work["anchors"])  # type: ignore[union-attr]
        compilation = work.get("compilation")
        direct_mode = isinstance(compilation, dict) and compilation.get("mode") == "direct"
        references = (
            tuple(_stored_asset(item) for item in work.get("references", ()))
            if direct_mode
            else ()
        )
        temporary_paths: tuple[Path, ...] = ()
        if anchors:
            if len(anchors) != 2:
                raise ValueError("video edit requires exactly two compiled control anchors")
            before_frame, after_frame = anchors
        else:
            extractor = self._require_frame_extractor()
            duration_ms = _duration_ms(source)
            temporary_paths = extractor.extract_frames_at(
                source,
                timestamps_ms=(
                    max(0, start_ms - 1),
                    min(duration_ms - 1, end_ms),
                ),
            )
            try:
                before_frame, after_frame = tuple(
                    _ephemeral_boundary(
                        step_id,
                        boundary,
                        self._asset_store.import_local(path),
                    )
                    for boundary, path in zip(
                        ("start", "end"),
                        temporary_paths,
                        strict=True,
                    )
                )
            finally:
                for path in temporary_paths:
                    path.unlink(missing_ok=True)

        input_plan = build_edit_input_plan(
            resolution=self._resolution,
            duration_seconds=provider_duration,
            source_video=_media_source(source),
            before_frame=_media_source(before_frame),
            after_frame=_media_source(after_frame),
            references=tuple(_media_source(item) for item in references),
        )
        self._repository.record_video_edit_inputs(
            step_id,
            input_plan=input_plan.model_dump(mode="json", by_alias=True),
            input_assets=(source, before_frame, after_frame, *references),
        )
        task_id = work.get("providerTaskId")
        if isinstance(task_id, str) and task_id:
            task = self._gateway.get_video_task(task_id)
        else:
            task = self._gateway.submit_video(
                prompt=str(work["prompt"]),
                input_plan=input_plan,
                input_sources=(
                    work["sourceInput"],  # type: ignore[arg-type]
                    before_frame.require_path(),
                    after_frame.require_path(),
                    *(item.require_path() for item in references),
                ),
            )
            self._repository.record_video_edit_submission(
                step_id,
                provider_task_id=task.task_id,
                provider_status=task.status,
            )
        return self._finish_or_continue_video(
            step_id,
            task=task,
            source=source,
            start_ms=start_ms,
            end_ms=end_ms,
            expected_provider_duration=provider_duration,
        )

    def _finish_or_continue_video(
        self,
        step_id: uuid.UUID,
        *,
        task: VideoTaskResult,
        source: StoredAsset,
        start_ms: int,
        end_ms: int,
        expected_provider_duration: int,
    ) -> MediaExecutionResult:
        status = task.status.lower()
        if status in {"queued", "pending", "running", "processing"}:
            return MediaExecutionResult(
                payload={"providerTaskId": task.task_id, "status": status},
                status=StepStatus.QUEUED,
            )
        if status not in {"succeeded", "completed"}:
            raise GatewayError(
                task.error_message or "video edit provider task failed",
                code=task.error_code or "video_edit_failed",
                retryable=False,
            )
        if not task.video_url:
            raise GatewayError(
                "video edit provider task completed without a video URL",
                code="missing_video_url",
                retryable=False,
            )
        segment = self._asset_store.download(task.video_url, suffix=".mp4")
        replacement_qc = self._media_probe.inspect_video(
            segment.path,
            expected_duration_seconds=expected_provider_duration,
            expected_resolution=self._resolution,
            minimum_duration_seconds=1,
            maximum_duration_seconds=15,
            duration_tolerance_ms=5_000,
            require_audio=False,
        )
        if not replacement_qc.get("passed"):
            candidate_id = self._repository.record_failed_video_edit_candidate(
                step_id,
                provider_segment=segment,
                provider_url=task.video_url,
                provider_model=task.model or self._gateway.video_model,
                replacement_qc=replacement_qc,
            )
            raise ValueError(
                "video edit replacement QC failed: "
                f"{replacement_qc.get('failures')} (candidate {candidate_id})"
            )
        replacement_duration_ms = int(replacement_qc.get("durationMs") or 0)
        if replacement_duration_ms <= 0:
            raise ValueError("video edit provider result has no measurable duration")
        full_video = self._asset_store.render_range_replacement(
            base_path=source.require_path(),
            replacement_path=segment.path,
            replacement_duration_ms=replacement_duration_ms,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        source_duration_ms = _duration_ms(source)
        full_qc = self._media_probe.inspect_video(
            full_video.path,
            expected_duration_seconds=max(1, round(source_duration_ms / 1000)),
            expected_resolution=self._resolution,
            minimum_duration_seconds=1,
            maximum_duration_seconds=max(15, math.ceil(source_duration_ms / 1000)),
            duration_tolerance_ms=1_500,
            require_audio=False,
        )
        if not full_qc.get("passed"):
            raise ValueError(f"composed video technical QC failed: {full_qc.get('failures')}")
        result = self._repository.complete_video_edit(
            step_id,
            provider_segment=segment,
            full_video=full_video,
            provider_url=task.video_url,
            provider_model=task.model or self._gateway.video_model,
            replacement_qc=replacement_qc,
            full_qc=full_qc,
        )
        return MediaExecutionResult(
            payload=result,
            status=StepStatus.AWAITING_REVIEW,
        )

    def _require_frame_extractor(self) -> FrameExtractor:
        if self._frame_extractor is None:
            raise RuntimeError("VIDEO_EDIT_V2 requires ffmpeg frame extraction")
        return self._frame_extractor


def _stored_asset(value: object) -> StoredAsset:
    if not isinstance(value, StoredAsset):
        raise TypeError("video edit repository returned an invalid StoredAsset")
    return value


def _duration_ms(asset: StoredAsset) -> int:
    qc = asset.metadata.get("qc")
    value = qc.get("durationMs") if isinstance(qc, dict) else asset.metadata.get("durationMs")
    if not isinstance(value, int) or value <= 0:
        raise ValueError("video edit source is missing durationMs")
    return value


def _media_source(asset: StoredAsset) -> MediaSource:
    return MediaSource(
        asset_id=asset.id,
        semantic_key=asset.semantic_key or f"asset:{asset.id}",
        media_type=asset.media_type,
        sha256=asset.sha256,
        metadata=asset.metadata,
    )


def _ephemeral_boundary(
    step_id: uuid.UUID,
    boundary: str,
    landed: LandedAsset,
) -> StoredAsset:
    return StoredAsset(
        id=uuid.uuid5(step_id, f"video-edit-boundary:{boundary}:{landed.sha256}"),
        project_id=None,
        scene_id=None,
        shot_card_id=None,
        step_id=step_id,
        role="video_edit_boundary",
        media_type="image",
        scope="canvas_node",
        status="ready",
        path=landed.path,
        sha256=landed.sha256,
        metadata={"boundary": boundary},
        semantic_key=f"video-edit:{step_id}:boundary:{boundary}",
    )
