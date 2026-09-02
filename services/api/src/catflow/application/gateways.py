from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit


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


@dataclass(frozen=True, slots=True)
class SegmentVideoGenerationRequest:
    prompt: str
    negative_prompt: str
    context_video_url: str
    anchor_in_path: Path
    anchor_out_path: Path
    canon_reference_paths: tuple[Path, ...]
    canon_reference_roles: tuple[str, ...]
    duration_seconds: int
    resolution: Literal["480p"]
    ratio: Literal["9:16"]

    def __post_init__(self) -> None:
        if not 4 <= self.duration_seconds <= 15:
            raise ValueError("segment generation duration must be between 4 and 15 seconds")
        if len(self.canon_reference_paths) != 5:
            raise ValueError("segment generation requires all five Canon references")
        if self.canon_reference_roles != (
            "episode_child",
            "episode_cat",
            "pair_scale",
            "environment",
            "style_board",
        ):
            raise ValueError("segment generation Canon roles are incomplete or out of order")
        parsed = urlsplit(self.context_video_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("segment generation context video must use an HTTPS URL")


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

    def submit_segment_video(
        self, request: SegmentVideoGenerationRequest
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
