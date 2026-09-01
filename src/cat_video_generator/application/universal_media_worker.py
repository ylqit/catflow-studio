"""Durable worker dispatch for universal media canvas jobs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Any, Protocol

from pydantic import ValidationError

from ..domain.aigc_canvas import SubjectCompletionProposal
from ..domain.production_recipes import RecipeDispatchError
from ..domain.rendering import VideoInputPlan
from ..domain.workflow import StepStatus
from .ports import (
    DirectorResult,
    GatewayError,
    ImageResult,
    LandedAsset,
    StoredAsset,
    VideoTaskResult,
)


class MediaCanvasQueue(Protocol):
    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        operation_prefixes: tuple[str, ...] = (),
    ) -> Any | None: ...

    def heartbeat(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> Any | None: ...

    def update_progress(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        current_step: int,
        total_steps: int,
        percent: int,
        message: str,
    ) -> Any: ...

    def finish(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        status: StepStatus,
        error: dict[str, object] | None = None,
        next_retry_at: datetime | None = None,
        result_summary: dict[str, object] | None = None,
        progress_update: dict[str, object] | None = None,
    ) -> None: ...

    def assert_provider_submission_allowed(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
    ) -> None: ...


class MediaCanvasWorkRepository(Protocol):
    def subject_completion_work(self, step_id: uuid.UUID) -> dict[str, object]: ...

    def complete_subject_completion(
        self,
        step_id: uuid.UUID,
        *,
        proposal: dict[str, object],
        raw_response: dict[str, object],
    ) -> str: ...

    def image_candidate_work(self, step_id: uuid.UUID) -> dict[str, object]: ...

    def complete_image_candidate(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
    ) -> str: ...

    def video_candidate_work(self, step_id: uuid.UUID) -> dict[str, object]: ...

    def record_video_candidate_submission(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None: ...

    def complete_video_candidate(
        self,
        step_id: uuid.UUID,
        *,
        landed: LandedAsset,
        provider_url: str,
        provider_model: str,
        last_frame_landed: LandedAsset | None = None,
        last_frame_provider_url: str | None = None,
    ) -> str: ...


class ImageGateway(Protocol):
    def generate_image(self, *, prompt: str, reference_paths: tuple[Path, ...]) -> ImageResult: ...

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
    ) -> DirectorResult: ...

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
    ) -> VideoTaskResult: ...

    def get_video_task(self, task_id: str) -> VideoTaskResult: ...


class DownloadStore(Protocol):
    def download(self, url: str, *, suffix: str) -> LandedAsset: ...


class VideoEditExecutor(Protocol):
    def execute(
        self,
        step_id: uuid.UUID,
        *,
        operation_key: str,
    ) -> MediaExecutionResult: ...


class ShotVideoExecutor(Protocol):
    def resume_step(self, step_id: uuid.UUID, *, wait: bool = False) -> dict[str, Any]: ...


class FilmstripWorkRepository(Protocol):
    def filmstrip_work(self, step_id: uuid.UUID) -> dict[str, object]: ...

    def complete_filmstrip(
        self,
        step_id: uuid.UUID,
        *,
        frames: tuple[LandedAsset, ...],
        timestamps_ms: tuple[int, ...],
    ) -> tuple[str, ...]: ...


class FilmstripFrameExtractor(Protocol):
    def extract_frames_at(
        self, source: StoredAsset, *, timestamps_ms: tuple[int, ...]
    ) -> tuple[Path, ...]: ...


class LocalAssetImporter(Protocol):
    def import_local(self, path: Path) -> LandedAsset: ...


@dataclass(frozen=True, slots=True)
class MediaExecutionResult:
    payload: dict[str, object]
    status: StepStatus
    next_retry_at: datetime | None = None


class RecipeTaskExecutor(Protocol):
    def execute_queued_task(
        self,
        step_id: uuid.UUID,
        *,
        operation_key: str,
        input_snapshot: dict[str, object],
    ) -> MediaExecutionResult: ...


class _LeaseHeartbeat:
    """Renews a claimed task while a blocking provider/application call runs."""

    def __init__(
        self,
        *,
        queue: MediaCanvasQueue,
        step_id: uuid.UUID,
        worker_id: str,
        interval_seconds: float = 20,
        lease_seconds: int = 60,
    ) -> None:
        self._queue = queue
        self._step_id = step_id
        self._worker_id = worker_id
        self._interval_seconds = interval_seconds
        self._lease_seconds = lease_seconds
        self._stop = Event()
        self._error: BaseException | None = None
        self._thread = Thread(target=self._run, daemon=True, name=f"lease-{step_id}")

    def __enter__(self) -> _LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, exception_type: object, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval_seconds + 1)
        if exception_type is None and self._error is not None:
            raise RuntimeError("workflow lease heartbeat failed") from self._error

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                renewed = self._queue.heartbeat(
                    self._step_id,
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                )
                if renewed is None:
                    self._stop.set()
                    return
            except BaseException as exc:
                self._error = exc
                self._stop.set()
                return


class VideoFilmstripExecutor:
    """Extracts cached timeline thumbnails through the local FFmpeg boundary."""

    def __init__(
        self,
        *,
        repository: FilmstripWorkRepository,
        frame_extractor: FilmstripFrameExtractor | None,
        asset_store: LocalAssetImporter,
    ) -> None:
        self._repository = repository
        self._frame_extractor = frame_extractor
        self._asset_store = asset_store

    def execute(self, step_id: uuid.UUID) -> MediaExecutionResult:
        if self._frame_extractor is None:
            raise RuntimeError("FFmpeg 未配置，无法生成真实视频帧带")
        work = self._repository.filmstrip_work(step_id)
        source = work["source"]
        timestamps_ms = tuple(int(value) for value in work["timestampsMs"])  # type: ignore[arg-type]
        paths = self._frame_extractor.extract_frames_at(
            source,  # type: ignore[arg-type]
            timestamps_ms=timestamps_ms,
        )
        try:
            frames = tuple(self._asset_store.import_local(path) for path in paths)
            self._repository.complete_filmstrip(
                step_id,
                frames=frames,
                timestamps_ms=timestamps_ms,
            )
        finally:
            for path in paths:
                path.unlink(missing_ok=True)
        return MediaExecutionResult(
            payload={"frameCount": str(len(timestamps_ms))},
            status=StepStatus.SUCCEEDED,
        )


class UniversalMediaWorker:
    """Executes only media-canvas operation prefixes from the shared durable queue."""

    def __init__(
        self,
        *,
        queue: MediaCanvasQueue,
        repository: MediaCanvasWorkRepository,
        gateway: ImageGateway,
        asset_store: DownloadStore,
        worker_id: str,
        video_edit_executor: VideoEditExecutor | None = None,
        shot_video_executor: ShotVideoExecutor | None = None,
        filmstrip_executor: VideoFilmstripExecutor | None = None,
        recipe_task_executor: RecipeTaskExecutor | None = None,
        provider_poll_interval_seconds: float = 10,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        self._queue = queue
        self._repository = repository
        self._gateway = gateway
        self._asset_store = asset_store
        self._worker_id = worker_id.strip()
        self._video_edit_executor = video_edit_executor
        self._shot_video_executor = shot_video_executor
        self._filmstrip_executor = filmstrip_executor
        self._recipe_task_executor = recipe_task_executor
        self._provider_poll_interval_seconds = provider_poll_interval_seconds

    def run_once(self) -> dict[str, str] | None:
        lease = self._queue.claim_next(
            worker_id=self._worker_id,
            operation_prefixes=(
                "subject:complete:",
                "media:image:batch:",
                "media:video:batch:",
                "media:filmstrip:",
                "video:shot",
                "video:edit-anchor:",
                "video:edit-recipe:",
                "recipe:",
                "canvas-group:",
            ),
        )
        if lease is None:
            return None
        try:
            self._queue.update_progress(
                lease.step_id,
                worker_id=self._worker_id,
                current_step=1,
                total_steps=3,
                percent=10,
                message="Worker 已领取任务，正在验证固定输入",
            )
            with _LeaseHeartbeat(
                queue=self._queue,
                step_id=lease.step_id,
                worker_id=self._worker_id,
            ):
                self._queue.update_progress(
                    lease.step_id,
                    worker_id=self._worker_id,
                    current_step=2,
                    total_steps=3,
                    percent=35,
                    message="输入验证完成，正在执行生成步骤",
                )
                self._queue.assert_provider_submission_allowed(
                    lease.step_id,
                    worker_id=self._worker_id,
                )
                execution = self._execute(lease)
        except ValidationError as exc:
            if lease.operation_key != "recipe:character_design":
                self._queue.finish(
                    lease.step_id,
                    worker_id=self._worker_id,
                    status=StepStatus.FAILED,
                    error={"code": "media_worker_failed", "message": str(exc)},
                    progress_update={"message": "Worker 输入验证失败"},
                )
                raise
            self._queue.finish(
                lease.step_id,
                worker_id=self._worker_id,
                status=StepStatus.FAILED,
                error={
                    "code": "recipe_input_validation_failed",
                    "failedStep": "validate_recipe_input",
                    "recoverable": True,
                    "providerSubmitted": False,
                    "message": str(exc),
                },
                progress_update={
                    "currentStep": 2,
                    "totalSteps": 3,
                    "percent": 35,
                    "message": "角色设计输入解析失败；供应商尚未提交，可从失败步骤继续",
                    "providerStatus": "not_submitted",
                },
            )
            raise
        except RecipeDispatchError as exc:
            validation_dispatch = (
                lease.operation_key == "recipe:character_design_validation"
            )
            error_document = exc.to_error_document()
            if validation_dispatch:
                error_document.update(
                    {
                        "recoverable": False,
                        "message": "引用验证调度失败；未提交 Provider，且本轮禁止自动恢复或重试",
                    }
                )
            self._queue.finish(
                lease.step_id,
                worker_id=self._worker_id,
                status=StepStatus.FAILED,
                error=error_document,
                progress_update={
                    "currentStep": 2,
                    "totalSteps": 3,
                    "percent": 35,
                    "message": (
                        "引用验证调度失败；供应商尚未提交，本轮已停止且不自动重试"
                        if validation_dispatch
                        else "角色设计调度失败；供应商尚未提交，可从失败步骤继续"
                    ),
                    "providerStatus": "not_submitted",
                },
            )
            raise
        except GatewayError as exc:
            retry_count = int(lease.progress.get("networkRetryCount", 0) or 0)
            task_input = lease.input_snapshot.get("input")
            character_design = (
                task_input.get("characterDesign")
                if isinstance(task_input, dict)
                else None
            )
            validation_only = bool(
                lease.operation_key.startswith("media:image:batch:")
                and isinstance(character_design, dict)
                and character_design.get("validationOnly") is True
            )
            should_retry = (
                not validation_only
                and exc.retryable
                and not exc.submission_unknown
                and retry_count < 3
            )
            status = (
                StepStatus.SUBMISSION_UNKNOWN
                if exc.submission_unknown
                else StepStatus.PENDING if should_retry else StepStatus.FAILED
            )
            self._queue.finish(
                lease.step_id,
                worker_id=self._worker_id,
                status=status,
                error={"code": exc.code, "message": str(exc)},
                next_retry_at=(
                    datetime.now(UTC) + timedelta(seconds=2 ** retry_count)
                    if should_retry
                    else None
                ),
                progress_update=(
                    {
                        "networkRetryCount": retry_count + 1,
                        "message": f"网络异常，已安排第 {retry_count + 1}/3 次自动重试",
                    }
                    if should_retry
                    else {"message": "Provider 提交状态未知，等待人工对账恢复"}
                    if exc.submission_unknown
                    else {
                        "message": (
                            "引用验证首次 Provider 调用失败；已按费用边界停止，不自动重试"
                            if validation_only
                            else "任务执行失败"
                        )
                    }
                ),
            )
            raise
        except Exception as exc:
            self._queue.finish(
                lease.step_id,
                worker_id=self._worker_id,
                status=StepStatus.FAILED,
                error={"code": "media_worker_failed", "message": str(exc)},
                progress_update={"message": "Worker 执行失败"},
            )
            raise
        self._queue.finish(
            lease.step_id,
            worker_id=self._worker_id,
            status=execution.status,
            next_retry_at=execution.next_retry_at,
            result_summary=execution.payload,
            progress_update=(
                {
                    "message": "Provider 正在处理，已安排下一次状态查询",
                    "percent": 60,
                    **(
                        {"providerStatus": provider_status}
                        if (
                            provider_status := str(
                                execution.payload.get("providerStatus")
                                or execution.payload.get("status")
                                or ""
                            ).strip().lower()
                        ) in {"pending", "queued", "running"}
                        else {}
                    ),
                }
                if execution.status is StepStatus.QUEUED
                else {
                    "currentStep": 3,
                    "totalSteps": 3,
                    "percent": 100,
                    "message": (
                        "生成完成，等待人工审核"
                        if execution.status is StepStatus.AWAITING_REVIEW
                        else "任务已完成"
                    ),
                }
            ),
        )
        return {
            "stepId": str(lease.step_id),
            **{key: str(value) for key, value in execution.payload.items()},
        }

    def _execute(self, lease: Any) -> MediaExecutionResult:
        if lease.operation_key.startswith(("recipe:", "canvas-group:")):
            if self._recipe_task_executor is None:
                raise RuntimeError("recipe worker executor is not configured")
            return self._recipe_task_executor.execute_queued_task(
                lease.step_id,
                operation_key=lease.operation_key,
                input_snapshot=lease.input_snapshot,
            )
        if lease.operation_key.startswith("subject:complete:"):
            return MediaExecutionResult(
                payload=self._complete_subject(lease.step_id),
                status=StepStatus.AWAITING_REVIEW,
            )
        if lease.operation_key.startswith("media:image:batch:"):
            return MediaExecutionResult(
                payload=self._generate_image_candidate(lease.step_id),
                status=StepStatus.AWAITING_REVIEW,
            )
        if lease.operation_key.startswith("media:video:batch:"):
            return self._generate_video_candidate(lease.step_id)
        if lease.operation_key.startswith("media:filmstrip:"):
            if self._filmstrip_executor is None:
                raise RuntimeError("filmstrip worker executor is not configured")
            return self._filmstrip_executor.execute(lease.step_id)
        if lease.operation_key.startswith("video:shot"):
            if self._shot_video_executor is None:
                raise RuntimeError("shot video worker executor is not configured")
            result = self._shot_video_executor.resume_step(lease.step_id, wait=False)
            status = str(result.get("status") or "")
            if status in {StepStatus.QUEUED.value, StepStatus.RUNNING.value}:
                return MediaExecutionResult(
                    payload=dict(result),
                    status=StepStatus.QUEUED,
                    next_retry_at=datetime.now(UTC)
                    + timedelta(seconds=self._provider_poll_interval_seconds),
                )
            if status in {StepStatus.AWAITING_REVIEW.value, StepStatus.SUCCEEDED.value}:
                return MediaExecutionResult(
                    payload=dict(result),
                    status=StepStatus.AWAITING_REVIEW,
                )
            if status == StepStatus.SUBMISSION_UNKNOWN.value:
                return MediaExecutionResult(
                    payload=dict(result),
                    status=StepStatus.SUBMISSION_UNKNOWN,
                )
            raise RuntimeError(f"逐镜视频子任务执行失败：{result}")
        if self._video_edit_executor is None:
            raise RuntimeError("VIDEO_EDIT_V2 worker executor is not configured")
        return self._video_edit_executor.execute(
            lease.step_id,
            operation_key=lease.operation_key,
        )

    def _generate_image_candidate(self, step_id: uuid.UUID) -> dict[str, str]:
        work = self._repository.image_candidate_work(step_id)
        result = self._gateway.generate_image(
            prompt=str(work["prompt"]),
            reference_paths=tuple(work["referencePaths"]),  # type: ignore[arg-type]
        )
        landed = self._asset_store.download(result.url, suffix=".png")
        asset_id = self._repository.complete_image_candidate(
            step_id,
            landed=landed,
            provider_url=result.url,
            provider_model=result.model,
        )
        return {"assetId": asset_id}

    def _generate_video_candidate(self, step_id: uuid.UUID) -> MediaExecutionResult:
        work = self._repository.video_candidate_work(step_id)
        task_id = work.get("providerTaskId")
        if task_id:
            result = self._gateway.get_video_task(str(task_id))
        else:
            result = self._gateway.submit_video(
                prompt=str(work["prompt"]),
                input_plan=work["inputPlan"],  # type: ignore[arg-type]
                input_sources=tuple(work["inputSources"]),  # type: ignore[arg-type]
            )
            self._repository.record_video_candidate_submission(
                step_id,
                provider_task_id=result.task_id,
                provider_status=result.status,
            )
        if result.status in {"pending", "queued", "running"}:
            return MediaExecutionResult(
                payload={
                    "providerTaskId": result.task_id,
                    "providerStatus": result.status,
                },
                status=StepStatus.QUEUED,
                next_retry_at=datetime.now(UTC)
                + timedelta(seconds=self._provider_poll_interval_seconds),
            )
        if result.status != "succeeded" or not result.video_url:
            raise GatewayError(
                result.error_message or "video generation failed",
                code=result.error_code or "video_generation_failed",
                retryable=False,
            )
        landed = self._asset_store.download(result.video_url, suffix=".mp4")
        last_frame_landed = (
            self._asset_store.download(result.last_frame_url, suffix=".png")
            if result.last_frame_url
            else None
        )
        asset_id = self._repository.complete_video_candidate(
            step_id,
            landed=landed,
            provider_url=result.video_url,
            provider_model=result.model or "unknown",
            last_frame_landed=last_frame_landed,
            last_frame_provider_url=result.last_frame_url,
        )
        return MediaExecutionResult(
            payload={"assetId": asset_id},
            status=StepStatus.AWAITING_REVIEW,
        )

    def _complete_subject(self, step_id: uuid.UUID) -> dict[str, str]:
        work = self._repository.subject_completion_work(step_id)
        result = self._gateway.generate_structured(
            prompt=str(work["prompt"]),
            schema=SubjectCompletionProposal.model_json_schema(),
            output_name="SubjectCompletionProposal",
        )
        proposal = SubjectCompletionProposal.model_validate(result.payload)
        run_id = self._repository.complete_subject_completion(
            step_id,
            proposal=proposal.model_dump(mode="json", by_alias=True),
            raw_response={
                "responseId": result.response_id,
                "model": result.model,
                "requestHash": result.request_hash,
                "payload": result.payload,
            },
        )
        return {"subjectCompletionRunId": run_id}
