from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class StructuredProviderResult:
    payload: dict[str, object]
    response_id: str
    model: str
    usage: dict[str, int]
    request_hash: str


@dataclass(frozen=True, slots=True)
class ImageProviderResult:
    url: str
    response_id: str | None
    model: str


@dataclass(frozen=True, slots=True)
class VideoSubmissionResult:
    task_id: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class VideoPollResult:
    status: Literal["running", "succeeded", "failed"]
    video_url: str | None = None
    last_frame_url: str | None = None
    error: dict[str, object] | None = None
    model: str | None = None
    duration_seconds: int | None = None
    ratio: str | None = None
    resolution: str | None = None


class PlanningGateway(Protocol):
    def plan_story(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult: ...


class ImageGenerationGateway(Protocol):
    def generate_image(
        self, *, prompt: str, reference_paths: tuple[Path, ...]
    ) -> ImageProviderResult: ...


class DiagnosticGateway(Protocol):
    def diagnose(
        self,
        *,
        prompt: str,
        image_paths: tuple[Path, ...],
        output_schema: dict[str, object],
    ) -> StructuredProviderResult: ...


class VideoGenerationGateway(Protocol):
    def submit_video(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
        reference_roles: tuple[str, ...],
        duration_seconds: int,
        resolution: str,
    ) -> VideoSubmissionResult: ...

    def poll_video(self, task_id: str) -> VideoPollResult: ...

    def cancel_video(self, task_id: str) -> bool: ...


class ProviderGatewayError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        submission_unknown: bool,
        request_id: str | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.submission_unknown = submission_unknown
        self.request_id = request_id
        self.timed_out = timed_out

    def as_error_document(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "submissionUnknown": self.submission_unknown,
            "requestId": self.request_id,
            "timedOut": self.timed_out,
        }
