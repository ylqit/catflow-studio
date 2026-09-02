from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from catflow.application.gateways import (
    ImageProviderResult,
    ProviderGatewayError,
    SegmentVideoGenerationRequest,
    StructuredProviderResult,
    VideoPollResult,
    VideoSubmissionResult,
)


@dataclass(frozen=True, slots=True)
class ArkGatewaySettings:
    api_key: str
    base_url: str
    planning_model: str
    image_model: str
    video_model: str
    diagnostic_model: str
    request_timeout_seconds: float

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("ARK_API_KEY is required")
        if not self.base_url.startswith("https://"):
            raise ValueError("ARK_BASE_URL must use HTTPS")

    @classmethod
    def from_env(cls) -> ArkGatewaySettings:
        import os

        return cls(
            api_key=os.environ.get("ARK_API_KEY", ""),
            base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            planning_model=os.environ.get(
                "ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"
            ),
            image_model=os.environ.get(
                "ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"
            ),
            video_model=os.environ.get(
                "ARK_VIDEO_MODEL", "doubao-seedance-2-0-260128"
            ),
            diagnostic_model=os.environ.get(
                "ARK_DIAGNOSTIC_MODEL",
                os.environ.get("ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"),
            ),
            request_timeout_seconds=float(os.environ.get("ARK_REQUEST_TIMEOUT_SECONDS", "120")),
        )


class ArkTypedGateway:
    """Typed Ark transport boundary; it never mutates CatFlow business state."""

    def __init__(
        self,
        settings: ArkGatewaySettings,
        *,
        client: Any | None = None,
    ) -> None:
        self._settings = settings
        if client is None:
            from volcenginesdkarkruntime import Ark

            client = Ark(api_key=settings.api_key, base_url=settings.base_url)
        self._client = client

    def plan_story(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return self._structured_response(
            model=self._settings.planning_model,
            prompt=prompt,
            image_paths=(),
            output_schema=output_schema,
        )

    def plan_shots(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return self._structured_response(
            model=self._settings.planning_model,
            prompt=prompt,
            image_paths=(),
            output_schema=output_schema,
        )

    def diagnose(
        self,
        *,
        prompt: str,
        image_paths: tuple[Path, ...],
        output_schema: dict[str, object],
    ) -> StructuredProviderResult:
        if not image_paths:
            raise ValueError("diagnosis requires at least one image")
        return self._structured_response(
            model=self._settings.diagnostic_model,
            prompt=prompt,
            image_paths=image_paths,
            output_schema=output_schema,
        )

    def generate_image(
        self, *, prompt: str, reference_paths: tuple[Path, ...]
    ) -> ImageProviderResult:
        request: dict[str, object] = {
            "model": self._settings.image_model,
            "prompt": prompt,
            "response_format": "url",
            "size": "2K",
            "watermark": False,
            "output_format": "png",
            "timeout": self._settings.request_timeout_seconds,
        }
        if reference_paths:
            request["image"] = [_image_data_url(path) for path in reference_paths]
        try:
            response = self._client.images.generate(**request)
        except Exception as exc:
            raise _provider_error(exc, submission=True) from exc
        data = list(getattr(response, "data", ()) or ())
        if len(data) != 1 or not getattr(data[0], "url", None):
            raise ProviderGatewayError(
                code="invalid_image_result",
                message="Seedream did not return one downloadable image",
                retryable=False,
                submission_unknown=False,
            )
        return ImageProviderResult(
            url=str(data[0].url),
            response_id=_optional_string(getattr(response, "id", None)),
            model=str(getattr(response, "model", self._settings.image_model)),
            usage=_usage_document(getattr(response, "usage", None)),
        )

    def submit_video(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
        reference_roles: tuple[str, ...],
        duration_seconds: int,
        resolution: str,
    ) -> VideoSubmissionResult:
        if len(reference_paths) != len(reference_roles):
            raise ValueError("video reference paths and roles must have the same length")
        if len(reference_paths) > 5:
            raise ValueError("CatFlow Seedance capability accepts at most five references")
        role_sequence = " → ".join(reference_roles)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": f"{prompt}\n参考图片按顺序承担以下职责：{role_sequence}。",
            }
        ]
        for path in reference_paths:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(path)},
                    "role": "reference_image",
                }
            )
        try:
            response = self._client.content_generation.tasks.create(
                model=self._settings.video_model,
                content=content,
                return_last_frame=True,
                generate_audio=False,
                watermark=False,
                resolution=resolution,
                ratio="9:16",
                duration=duration_seconds,
                timeout=self._settings.request_timeout_seconds,
            )
        except Exception as exc:
            raise _provider_error(exc, submission=True) from exc
        task_id = str(getattr(response, "id", "")).strip()
        if not task_id:
            raise ProviderGatewayError(
                code="empty_task_id",
                message="Seedance did not return a task ID",
                retryable=False,
                submission_unknown=True,
            )
        return VideoSubmissionResult(
            task_id=task_id,
            request_id=_optional_string(
                getattr(response, "request_id", None)
                or getattr(response, "_request_id", None)
            ),
        )

    def poll_video(self, task_id: str) -> VideoPollResult:
        try:
            task = self._client.content_generation.tasks.get(
                task_id=task_id,
                timeout=self._settings.request_timeout_seconds,
            )
        except Exception as exc:
            raise _provider_error(exc, submission=False) from exc
        status = str(getattr(task, "status", "failed"))
        if status in {"queued", "running"}:
            return VideoPollResult(status="running")
        if status == "succeeded":
            content = getattr(task, "content", None)
            video_url = None if content is None else getattr(content, "video_url", None)
            if not video_url:
                return VideoPollResult(
                    status="failed",
                    error={
                        "code": "missing_video_url",
                        "message": "Seedance succeeded without a video URL",
                        "retryable": False,
                    },
                )
            return VideoPollResult(
                status="succeeded",
                video_url=str(video_url),
                last_frame_url=(
                    None
                    if content is None or not getattr(content, "last_frame_url", None)
                    else str(content.last_frame_url)
                ),
                model=_optional_string(getattr(task, "model", None)),
                duration_seconds=_optional_int(getattr(task, "duration", None)),
                ratio=_optional_string(getattr(task, "ratio", None)),
                resolution=_optional_string(getattr(task, "resolution", None)),
                usage=_usage_document(getattr(task, "usage", None)),
            )
        error = getattr(task, "error", None)
        return VideoPollResult(
            status="failed",
            error={
                "code": str(getattr(error, "code", "provider_failed")),
                "message": str(getattr(error, "message", "Seedance task failed")),
                "retryable": False,
            },
        )

    def submit_segment_video(
        self, request: SegmentVideoGenerationRequest
    ) -> VideoSubmissionResult:
        image_paths = (
            request.anchor_in_path,
            request.anchor_out_path,
            *request.canon_reference_paths,
        )
        image_roles = (
            "anchor_in",
            "anchor_out",
            *request.canon_reference_roles,
        )
        role_sequence = " → ".join(image_roles)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    f"本区间修改目标：{request.instruction}\n"
                    f"精确问题时间：{request.issue_start_seconds:.3f}–"
                    f"{request.issue_end_seconds:.3f}秒。\n{request.prompt}\n"
                    f"负面约束：{request.negative_prompt}\n"
                    "视频1只负责原动作、机位、节奏和前后连续性；"
                    f"图片职责按顺序为：{role_sequence}。"
                ),
            },
            {
                "type": "video_url",
                "video_url": {"url": request.context_video_url},
                "role": "reference_video",
            },
        ]
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": _image_data_url(path)},
                "role": "reference_image",
            }
            for path in image_paths
        )
        try:
            response = self._client.content_generation.tasks.create(
                model=self._settings.video_model,
                content=content,
                return_last_frame=True,
                generate_audio=False,
                watermark=False,
                resolution=request.resolution,
                ratio=request.ratio,
                duration=request.duration_seconds,
                timeout=self._settings.request_timeout_seconds,
            )
        except Exception as exc:
            raise _provider_error(exc, submission=True) from exc
        task_id = str(getattr(response, "id", "")).strip()
        if not task_id:
            raise ProviderGatewayError(
                code="empty_task_id",
                message="Seedance did not return a segment repair task ID",
                retryable=False,
                submission_unknown=True,
            )
        return VideoSubmissionResult(
            task_id=task_id,
            request_id=_optional_string(
                getattr(response, "request_id", None)
                or getattr(response, "_request_id", None)
            ),
        )

    def cancel_video(self, task_id: str) -> bool:
        try:
            self._client.content_generation.tasks.delete(
                task_id=task_id,
                timeout=self._settings.request_timeout_seconds,
            )
        except Exception as exc:
            raise _provider_error(exc, submission=False) from exc
        return True

    def _structured_response(
        self,
        *,
        model: str,
        prompt: str,
        image_paths: tuple[Path, ...],
        output_schema: dict[str, object],
    ) -> StructuredProviderResult:
        input_content: object = "只返回符合 Schema 的 JSON 对象。"
        if image_paths:
            content: list[dict[str, str]] = [
                {"type": "input_text", "text": "按顺序比较所有图片并返回诊断。"}
            ]
            for index, path in enumerate(image_paths, 1):
                content.append({"type": "input_text", "text": f"有序图片{index}"})
                content.append({"type": "input_image", "image_url": _image_data_url(path)})
            input_content = [{"role": "user", "content": content}]
        # Ark Responses currently rejects the SDK's json_schema wire shape for the
        # configured Seed 2.1 endpoint. JSON object mode is supported; the exact
        # schema remains explicit in the instruction and is validated again at
        # the typed result boundary before business state is changed.
        text_format = {"type": "json_object"}
        schema_instruction = (
            f"{prompt}\n\n只返回一个 JSON 对象，不要 Markdown。必须严格符合以下 JSON Schema：\n"
            + json.dumps(output_schema, ensure_ascii=False, sort_keys=True)
        )
        request_document = {
            "model": model,
            "prompt": prompt,
            "schema": output_schema,
            "imageSha256": [_sha256(path) for path in image_paths],
        }
        try:
            response = self._client.responses.create(
                model=model,
                instructions=schema_instruction,
                input=input_content,
                text={"format": text_format},
                temperature=0.2,
                max_output_tokens=4000,
                thinking={"type": "disabled"},
                store=False,
                timeout=self._settings.request_timeout_seconds,
            )
        except Exception as exc:
            raise _provider_error(exc, submission=True) from exc
        if getattr(response, "status", None) != "completed":
            raise ProviderGatewayError(
                code="response_not_completed",
                message=f"Ark response status is {getattr(response, 'status', None)!r}",
                retryable=False,
                submission_unknown=False,
                request_id=_optional_string(getattr(response, "id", None)),
            )
        try:
            payload = json.loads(_response_text(response))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProviderGatewayError(
                code="invalid_structured_output",
                message="Ark did not return a valid JSON object",
                retryable=False,
                submission_unknown=False,
                request_id=_optional_string(getattr(response, "id", None)),
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderGatewayError(
                code="invalid_structured_output",
                message="Ark structured output must be a JSON object",
                retryable=False,
                submission_unknown=False,
                request_id=_optional_string(getattr(response, "id", None)),
            )
        return StructuredProviderResult(
            payload=payload,
            response_id=str(response.id),
            model=str(response.model),
            usage=_usage_document(getattr(response, "usage", None)),
            request_hash=hashlib.sha256(
                json.dumps(
                    request_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        )


def _provider_error(exc: Exception, *, submission: bool) -> ProviderGatewayError:
    name = type(exc).__name__.lower()
    timed_out = isinstance(exc, TimeoutError) or "timeout" in name
    connection_error = "connection" in name or "connect" in name
    status_code = getattr(exc, "status_code", None)
    request_id = _optional_string(getattr(exc, "request_id", None))
    known_rejection = isinstance(status_code, int)
    submission_unknown = submission and not known_rejection and (timed_out or connection_error)
    code = str(getattr(exc, "code", "provider_timeout" if timed_out else "provider_error"))
    return ProviderGatewayError(
        code=code,
        message=str(exc) or "Ark request failed",
        retryable=bool(
            not submission_unknown
            and isinstance(status_code, int)
            and (status_code == 429 or status_code >= 500)
        ),
        submission_unknown=submission_unknown,
        request_id=request_id,
        timed_out=timed_out,
    )


def _image_data_url(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"reference image not found: {path}")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("reference image exceeds 20 MiB")
    with Image.open(path) as image:
        image.verify()
        image_format = str(image.format or "").upper()
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(
        image_format
    )
    if mime is None:
        raise ValueError(f"unsupported reference image format: {image_format}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _response_text(response: object) -> str:
    direct = getattr(response, "output_text", None)
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    for output in getattr(response, "output", ()):
        if getattr(output, "type", None) != "message":
            continue
        for content in getattr(output, "content", ()):
            if getattr(content, "type", None) == "output_text":
                parts.append(str(content.text))
    if not parts:
        raise TypeError("Ark response contains no output text")
    return "".join(parts)


def _usage_document(usage: object | None) -> dict[str, int]:
    if usage is None:
        return {}
    fields = (
        ("inputTokens", "input_tokens"),
        ("outputTokens", "output_tokens"),
        ("completionTokens", "completion_tokens"),
        ("totalTokens", "total_tokens"),
        ("generatedImages", "generated_images"),
        ("generatedVideoSeconds", "generated_video_seconds"),
    )
    document: dict[str, int] = {}
    for public_name, provider_name in fields:
        value = getattr(usage, provider_name, None)
        if value is not None:
            document[public_name] = int(value)
    return document


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_int(value: object) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
