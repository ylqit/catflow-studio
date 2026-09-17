"""Provider 抽象 —— CatFlow 应用层只依赖接口,不依赖具体 Ark 实现。

设计原则:
- 接口与实现分离:应用层 (service.py) 只调 provider_gateway.plan_story(...) 等抽象方法
- Worker 层 (worker/ark_gateway.py) 提供 ArkTypedGateway 实现
- 通过 catflow.infrastructure.postgres_repository 注入

即使只有 Ark 一个实现,也保留 Protocol 接口 —— 便于将来扩展其他 Provider
(如 OpenAI / 自研模型),且便于测试时用 MockProviderGateway。

调用方:
- application/service.py —— 调用 self._gateway.plan_story / submit_video / poll_video
- worker/ark_gateway.py —— 实现 ArkTypedGateway
- tests/catflow/ —— MockProviderGateway 测试实现
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class StructuredProviderResult:
    """规划类调用结果(plan_story / plan_shots / analyze_source 等 JSON-Schema 调用)。

    Attributes:
        payload: 已解析的 JSON 字典(对应 Ark 返回的 JSON-Schema 响应)
        response_id: Ark Response ID(可重读用)
        model: 实际调用的模型 ID(可能与请求不同,如 fallback)
        usage: token 消耗统计
        request_hash: SHA256(冻结输入哈希,用于 Receipt 对账)
        text_repairs: 解析层确定性文本修复审计(如 escaped_unstructured_quotes:N);
            空表示正文严格解析通过,未做任何修复
    """
    payload: dict[str, object]
    response_id: str
    model: str
    usage: dict[str, int]
    request_hash: str
    text_repairs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ImageProviderResult:
    url: str
    response_id: str | None
    model: str
    usage: dict[str, int]


@dataclass(frozen=True, slots=True)
class VideoSubmissionResult:
    task_id: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class VideoPollResult:
    status: Literal["running", "succeeded", "failed", "unknown"]
    provider_status: str | None = None
    video_url: str | None = None
    last_frame_url: str | None = None
    error: dict[str, object] | None = None
    model: str | None = None
    duration_seconds: int | None = None
    ratio: str | None = None
    resolution: str | None = None
    usage: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class SegmentVideoGenerationRequest:
    instruction: str
    prompt: str
    negative_prompt: str
    context_video_url: str | None
    issue_start_seconds: float
    issue_end_seconds: float
    anchor_in_path: Path | None
    anchor_out_path: Path | None
    canon_reference_paths: tuple[Path, ...]
    canon_reference_roles: tuple[str, ...]
    duration_seconds: int
    resolution: Literal["480p"]
    ratio: Literal["9:16"]
    prompt_compiler_revision: str = "segment-edit-v2"
    generation_mode: Literal["edit_existing", "from_frame"] = "edit_existing"
    generate_audio: bool = False
    compiled_provider_prompt: str | None = None

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("segment generation instruction is required")
        if self.issue_start_seconds < 0 or self.issue_end_seconds <= self.issue_start_seconds:
            raise ValueError("segment generation issue time range is invalid")
        if not 4 <= self.duration_seconds <= 15:
            raise ValueError("segment generation duration must be between 4 and 15 seconds")
        if self.generation_mode == "from_frame":
            if (
                self.anchor_in_path is None
                or self.context_video_url is not None
                or self.canon_reference_paths
                or self.canon_reference_roles
            ):
                raise ValueError("strict frame mode cannot include video or omni references")
            return
        if self.generation_mode != "edit_existing":
            raise ValueError("unknown segment generation mode")
        canonical = ("episode_child", "episode_cat", "pair_scale", "environment", "style_board")
        expected = tuple(role for role in canonical if role in self.canon_reference_roles)
        if self.canon_reference_roles != expected or len(self.canon_reference_paths) != len(
            expected
        ):
            raise ValueError("segment generation Canon roles are incomplete or out of order")
        if self.prompt_compiler_revision not in {
            "segment-edit-v7", "segment-edit-v8-performance"
        } and expected != canonical:
            raise ValueError("legacy segment generation requires all five Canon references")
        parsed = urlsplit(self.context_video_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("segment generation context video must use an HTTPS URL")


class PlanningGateway(Protocol):
    def plan_story(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult: ...

    def plan_shots(
        self,
        *,
        prompt: str,
        output_schema: dict[str, object],
        image_paths: tuple[Path, ...] = (),
        input_instruction: str = "结合这些参考规划分镜，遵守指令中各图片的职责与顺序。",
    ) -> StructuredProviderResult: ...

    def plan_series(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult: ...

    def plan_series_episode(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult: ...

    def analyze_story_source(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult: ...


class ImageGenerationGateway(Protocol):
    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        reference_paths: tuple[Path, ...],
        reference_roles: tuple[str, ...],
        compiled_provider_prompt: str | None = None,
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
        reference_video_url: str | None = None,
        duration_seconds: int,
        resolution: str,
        generate_audio: bool = False,
        generation_mode: str = "references",
        provider_prompt_version: int = 0,
    ) -> VideoSubmissionResult: ...

    def submit_segment_video(
        self, request: SegmentVideoGenerationRequest
    ) -> VideoSubmissionResult: ...

    def poll_video(self, task_id: str) -> VideoPollResult: ...


class ProviderGatewayError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        submission_unknown: bool = False,
        request_id: str | None = None,
        timed_out: bool = False,
        provider_status: str | None = None,
        incomplete_reason: str | None = None,
        max_output_tokens: int | None = None,
        usage: dict[str, int] | None = None,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
        response_id: str | None = None,
        client_request_id: str | None = None,
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.submission_unknown = submission_unknown
        self.request_id = request_id
        self.timed_out = timed_out
        self.provider_status = provider_status
        self.incomplete_reason = incomplete_reason
        self.max_output_tokens = max_output_tokens
        self.usage = usage
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.response_id = response_id
        self.client_request_id = client_request_id
        self.diagnostics = diagnostics

    def as_error_document(self) -> dict[str, object]:
        document: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "submissionUnknown": self.submission_unknown,
            "requestId": self.request_id,
            "timedOut": self.timed_out,
        }
        for key, value in (
            ("httpStatus", self.http_status),
            ("retryAfterSeconds", self.retry_after_seconds),
            ("responseId", self.response_id),
            ("clientRequestId", self.client_request_id),
            ("diagnostics", self.diagnostics),
        ):
            if value is not None:
                document[key] = value
        if self.provider_status is not None:
            document["providerStatus"] = self.provider_status
        if self.incomplete_reason is not None:
            document["incompleteReason"] = self.incomplete_reason
        if self.max_output_tokens is not None:
            document["maxOutputTokens"] = self.max_output_tokens
        if self.usage is not None:
            document["providerUsage"] = self.usage
        return document
