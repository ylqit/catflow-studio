from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from PIL import Image
from volcenginesdkarkruntime._response import to_raw_response_wrapper

from catflow.application.gateways import (
    ImageProviderResult,
    ProviderGatewayError,
    SegmentVideoGenerationRequest,
    StructuredProviderResult,
    VideoPollResult,
    VideoSubmissionResult,
)
from catflow.application.image_generation import compile_provider_image_prompt

from .ark_responses import parse_response, receive_response_stream, response_usage, save_response
from .provider_diagnostics import redact_transport_error, request_size_evidence, timeout_evidence
from .provider_receipts import provider_call, read_http_receipt, receipt_document, receive_receipt


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
            planning_model=os.environ.get("ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"),
            image_model=os.environ.get("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"),
            video_model=os.environ.get("ARK_VIDEO_MODEL", "doubao-seedance-2-0-260128"),
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

            # A paid submission with an unknown outcome must remain a single
            # attempt; only CatFlow can decide whether another job is allowed.
            client = Ark(api_key=settings.api_key, base_url=settings.base_url, max_retries=0)
        self._client = client

    def validate_execution_contract(
        self, contract: dict[str, Any], kind: str | None = None
    ) -> None:
        """Do not send a frozen request or task ID to a different runtime environment."""
        endpoint = contract.get("apiBaseUrl")
        expected_model = contract.get("model")
        model = (
            self._settings.image_model
            if kind == "generate_image"
            else self._settings.video_model
            if kind in {"generate_video", "regenerate_video_segment"}
            else self._settings.diagnostic_model
            if kind in {"diagnose_image", "diagnose_video"}
            else self._settings.planning_model
        )
        if (endpoint and endpoint != self._settings.base_url.rstrip("/")) or (
            kind and expected_model and expected_model != model
        ):
            raise ProviderGatewayError(
                code="execution_environment_changed",
                message="当前外部接口或模型与冻结配置不符，请恢复原执行配置后继续。",
                retryable=False,
                submission_unknown=False,
            )
        if kind:
            # Check both submission and recovery before recording a paid attempt.
            paths = (
                ("images.generate",)
                if kind == "generate_image"
                else ("content_generation.tasks.create", "content_generation.tasks.get")
                if kind in {"generate_video", "regenerate_video_segment"}
                else ("responses.create", "responses.retrieve")
            )
            for path in paths:
                operation = self._client
                for part in path.split("."):
                    operation = getattr(operation, part, None)
                if not callable(operation):
                    raise ProviderGatewayError(
                        code="local_adapter_error",
                        message=f"本地 Ark SDK 缺少可调用接口 {path}；尚未提交模型请求。",
                        retryable=False,
                        submission_unknown=False,
                    )

    def plan_story(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return self._structured_response(
            model=self._settings.planning_model,
            prompt=prompt,
            image_paths=(),
            output_schema=output_schema,
            max_output_tokens=4000,
        )

    def plan_shots(
        self,
        *,
        prompt: str,
        output_schema: dict[str, object],
        image_paths: tuple[Path, ...] = (),
        input_instruction: str = "结合这些参考规划分镜，遵守指令中各图片的职责与顺序。",
    ) -> StructuredProviderResult:
        return self._structured_response(
            model=self._settings.planning_model,
            prompt=prompt,
            image_paths=image_paths,
            output_schema=output_schema,
            max_output_tokens=8000,
            input_instruction=input_instruction,
        )

    def plan_series(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return self._structured_response(
            model=self._settings.planning_model,
            prompt=prompt,
            image_paths=(),
            output_schema=output_schema,
            max_output_tokens=16000,
        )

    def plan_series_episode(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return self._structured_response(
            model=self._settings.planning_model,
            prompt=prompt,
            image_paths=(),
            output_schema=output_schema,
            max_output_tokens=4000,
        )

    def analyze_story_source(
        self, *, prompt: str, output_schema: dict[str, object]
    ) -> StructuredProviderResult:
        return self._structured_response(
            model=self._settings.planning_model,
            prompt=prompt,
            image_paths=(),
            output_schema=output_schema,
            max_output_tokens=12000,
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
            max_output_tokens=4000,
            input_instruction="按顺序比较所有图片并返回诊断。",
        )

    def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        reference_paths: tuple[Path, ...],
        reference_roles: tuple[str, ...],
        compiled_provider_prompt: str | None = None,
    ) -> ImageProviderResult:
        if len(reference_paths) != len(reference_roles):
            raise ValueError("image reference paths and roles must have the same length")
        request: dict[str, object] = {
            "model": self._settings.image_model,
            "prompt": (
                compiled_provider_prompt
                if compiled_provider_prompt is not None
                else compile_provider_image_prompt(prompt=prompt, negative_prompt=negative_prompt)
            ),
            "response_format": "url",
            "size": "2K",
            "watermark": False,
            "output_format": "png",
            "optimize_prompt": False,
            "timeout": self._settings.request_timeout_seconds,
        }
        if reference_paths:
            request["image"] = [_image_data_url(path) for path in reference_paths]
        try:
            call = provider_call.get()
            if call is not None and call.contract.get("version", 1) >= 2:
                request["timeout"] = httpx.Timeout(
                    call.contract.get("imageTimeoutSeconds", 600), connect=10
                )
                raw = to_raw_response_wrapper(self._client.images.generate)(**request)
                document = read_http_receipt(raw)
                image_error = document.get("error") or next(
                    (item.get("error") for item in document.get("data", []) if item.get("error")),
                    None,
                )
                receive_receipt(
                    {
                        "serverRequestId": raw.headers.get("x-request-id"),
                        "complete": True,
                        "providerStatus": "failed" if image_error else "completed",
                        "providerError": image_error,
                        "result": {"rawResponse": document},
                        "usage": _usage_document(document.get("usage")),
                    }
                )
                if image_error:
                    raise ProviderGatewayError(
                        code="image_generation_failed",
                        message=str(image_error),
                        retryable=False,
                        submission_unknown=False,
                    )
                response = raw.parse()
            else:
                response = self._client.images.generate(**request)
        except Exception as exc:
            raise _provider_error(exc, submission=True) from exc
        receive_receipt(
            {
                "complete": True,
                "providerStatus": "completed",
                "result": {"rawResponse": receipt_document(response)},
                "usage": _usage_document(getattr(response, "usage", None)),
            }
        )
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
        reference_video_url: str | None = None,
        duration_seconds: int,
        resolution: str,
        generate_audio: bool = False,
        generation_mode: str = "references",
        provider_prompt_version: int = 0,
    ) -> VideoSubmissionResult:
        if generation_mode == "from_frame" and (
            len(reference_paths) != 1
            or reference_roles != ("first_frame",)
            or reference_video_url is not None
        ):
            raise ValueError(
                "strict first-frame mode accepts exactly one first frame and no other references"
            )
        if generation_mode not in {"from_frame", "references"}:
            raise ValueError("unsupported video generation mode")
        if len(reference_paths) != len(reference_roles):
            raise ValueError("video reference paths and roles must have the same length")
        if len(reference_paths) > 9:
            raise ValueError("CatFlow Seedance capability accepts at most nine image references")
        if reference_video_url is not None:
            parsed = urlsplit(reference_video_url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("video reference must use an HTTPS URL without credentials")
        role_sequence = " → ".join(reference_roles)
        video_guidance = (
            "\n上一集成片只负责服装、道具、空间位置、时间、光线与直接接镜状态；"
            "不得取代前五张图片对儿童、猫咪、比例、当前环境和画风的约束。"
            if reference_video_url is not None
            else ""
        )
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    prompt
                    if provider_prompt_version == 1
                    else f"{prompt}\n参考图片按顺序承担以下职责：{role_sequence}。{video_guidance}"
                ),
            }
        ]
        for path in reference_paths:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(path)},
                    "role": "first_frame" if generation_mode == "from_frame" else "reference_image",
                }
            )
        if reference_video_url is not None:
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": reference_video_url},
                    "role": "reference_video",
                }
            )
        return self._submit_video_task(
            content=content,
            generate_audio=generate_audio,
            resolution=resolution,
            ratio="9:16",
            duration=duration_seconds,
        )

    def poll_video(self, task_id: str) -> VideoPollResult:
        if call := provider_call.get():
            self.validate_execution_contract(call.contract)
        try:
            call = provider_call.get()
            if call is not None and call.contract.get("version", 1) >= 2:
                raw = to_raw_response_wrapper(self._client.content_generation.tasks.get)(
                    task_id=task_id,
                    timeout=httpx.Timeout(30, connect=10),
                )
                document = read_http_receipt(raw)
                receive_receipt(
                    {
                        "taskId": document.get("id"),
                        "providerStatus": document.get("status"),
                        "complete": document.get("status") == "succeeded",
                        "providerError": document.get("error"),
                        "result": {"rawResponse": document},
                        "usage": _usage_document(document.get("usage")),
                    }
                )
                task = raw.parse()
            else:
                task = self._client.content_generation.tasks.get(
                    task_id=task_id,
                    timeout=httpx.Timeout(30, connect=10),
                )
        except Exception as exc:
            raise _provider_error(exc, submission=False) from exc
        status = str(getattr(task, "status", "unknown"))
        receive_receipt(
            {
                "providerStatus": status,
                "complete": status == "succeeded",
                "providerError": receipt_document(getattr(task, "error", None)),
                "result": {"rawResponse": receipt_document(task)},
                "usage": _usage_document(getattr(task, "usage", None)),
            }
        )
        if status in {"queued", "running"}:
            return VideoPollResult(status="running", provider_status=status)
        if status == "succeeded":
            content = getattr(task, "content", None)
            video_url = None if content is None else getattr(content, "video_url", None)
            if not video_url:
                return VideoPollResult(
                    status="succeeded",
                    provider_status=status,
                    error={
                        "code": "missing_video_url",
                        "message": "Seedance succeeded without a video URL",
                        "retryable": False,
                    },
                )
            return VideoPollResult(
                status="succeeded",
                provider_status=status,
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
        if status not in {"failed", "cancelled", "expired"}:
            return VideoPollResult(
                status="unknown",
                provider_status=status,
                error={
                    "code": "provider_state_unrecognized",
                    "message": f"无法识别外部状态：{status}",
                },
            )
        error = getattr(task, "error", None)
        return VideoPollResult(
            status="failed",
            provider_status=status,
            error={
                "code": str(getattr(error, "code", "provider_failed")),
                "message": str(getattr(error, "message", "Seedance task failed")),
                "retryable": False,
            },
        )

    def submit_segment_video(self, request: SegmentVideoGenerationRequest) -> VideoSubmissionResult:
        image_paths = (
            *((request.anchor_in_path,) if request.anchor_in_path is not None else ()),
            *((request.anchor_out_path,) if request.anchor_out_path is not None else ()),
            *request.canon_reference_paths,
        )
        image_roles = (
            *(("anchor_in",) if request.anchor_in_path is not None else ()),
            *(("anchor_out",) if request.anchor_out_path is not None else ()),
            *request.canon_reference_roles,
        )
        role_sequence = " → ".join(image_roles)
        content: list[dict[str, object]] = [
            {
                "type": "text",
                "text": (
                    request.compiled_provider_prompt
                    if request.compiled_provider_prompt is not None
                    else f"{request.prompt}\n需要避免的问题：{request.negative_prompt}"
                    if request.prompt_compiler_revision
                    in {"segment-edit-v3", "segment-edit-v4", "segment-edit-v5"}
                    else (
                        f"本区间修改目标：{request.instruction}\n"
                        f"精确问题时间：{request.issue_start_seconds:.3f}–"
                        f"{request.issue_end_seconds:.3f}秒。\n{request.prompt}\n"
                        f"负面约束：{request.negative_prompt}\n"
                        "视频1只负责原动作、机位、节奏和前后连续性；"
                        f"图片职责按顺序为：{role_sequence}。"
                    )
                ),
            },
        ]
        if request.generation_mode == "edit_existing":
            content.append(
                {
                    "type": "video_url",
                    "video_url": {"url": request.context_video_url},
                    "role": "reference_video",
                }
            )
        content.extend(
            {
                "type": "image_url",
                "image_url": {"url": _image_data_url(path)},
                "role": ("first_frame" if index == 0 else "last_frame")
                if request.generation_mode == "from_frame"
                else "reference_image",
            }
            for index, path in enumerate(image_paths)
        )
        return self._submit_video_task(
            content=content,
            generate_audio=request.generate_audio,
            resolution=request.resolution,
            ratio=request.ratio,
            duration=request.duration_seconds,
        )

    def _submit_video_task(self, **parameters) -> VideoSubmissionResult:
        """One creation boundary for full, shot and repair videos, with durable receipts."""
        call = provider_call.get()
        request = {
            "model": self._settings.video_model,
            "return_last_frame": True,
            "watermark": False,
            "timeout": self._settings.request_timeout_seconds,
            **parameters,
        }
        encoding_started = time.monotonic()
        content = parameters.get("content", [])
        diagnostics = {
            **(call.diagnostics if call is not None else {}),
            "stage": "video_submit",
            "timeoutSeconds": self._settings.request_timeout_seconds,
            "imageReferenceCount": sum(item.get("type") == "image_url" for item in content),
            "videoReferenceCount": sum(item.get("type") == "video_url" for item in content),
            "requestBytes": len(
                json.dumps(
                    {key: value for key, value in request.items() if key != "timeout"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            ),
            "requestBytesSource": "estimated_json",
        }
        diagnostics["requestMeasurementMs"] = round((time.monotonic() - encoding_started) * 1000, 3)
        client_id = None
        started = time.monotonic()
        try:
            if call is not None and call.contract.get("version", 1) >= 2:
                client_id = str(uuid.uuid4())
                call.receive(
                    {"clientRequestId": client_id, "result": {"submissionDiagnostics": diagnostics}}
                )
                started = time.monotonic()
                raw = self._client.content_generation.tasks.with_raw_response.create(
                    **request, extra_headers={"X-Client-Request-Id": client_id}
                )
                request_id = raw.headers.get("x-request-id")
                diagnostics.update(
                    request_size_evidence(
                        getattr(getattr(raw, "http_response", None), "request", None)
                    )
                )
                document = read_http_receipt(raw)
            else:
                response = self._client.content_generation.tasks.create(**request)
                document = receipt_document(response)
                request_id = _optional_string(getattr(response, "request_id", None))
        except Exception as exc:
            diagnostics["submitElapsedMs"] = round((time.monotonic() - started) * 1000, 3)
            diagnostics["clientRequestId"] = client_id
            # The structured evidence retains exception classes and phase. Avoid
            # logging the SDK cause, which may echo signed URLs or request data.
            raise _provider_error(exc, submission=True, diagnostics=diagnostics) from None
        diagnostics.update(
            submitElapsedMs=round((time.monotonic() - started) * 1000, 3),
            clientRequestId=client_id,
            serverRequestId=request_id,
        )
        task_id = _optional_string(document.get("id"))
        receive_receipt(
            {
                "taskId": task_id,
                "serverRequestId": request_id,
                "providerStatus": "submitted",
                "result": {"createReceipt": document, "submissionDiagnostics": diagnostics},
            }
        )
        if not task_id:
            raise ProviderGatewayError(
                code="empty_task_id",
                message="Seedance did not return a task ID",
                retryable=False,
                submission_unknown=True,
                request_id=request_id,
                client_request_id=client_id,
                diagnostics=diagnostics,
            )
        return VideoSubmissionResult(
            task_id=task_id,
            request_id=request_id,
        )

    def _structured_response(
        self,
        *,
        model: str,
        prompt: str,
        image_paths: tuple[Path, ...],
        output_schema: dict[str, object],
        max_output_tokens: int,
        input_instruction: str = "只返回符合 Schema 的 JSON 对象。",
    ) -> StructuredProviderResult:
        input_content: object = input_instruction
        if image_paths:
            content: list[dict[str, str]] = [{"type": "input_text", "text": input_instruction}]
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
            "inputInstruction": input_instruction,
        }
        request = {
            "model": model,
            "instructions": schema_instruction,
            "input": input_content,
            "text": {"format": text_format},
            "temperature": 0.2,
            "max_output_tokens": max_output_tokens,
            "thinking": {"type": "disabled"},
        }
        call = provider_call.get()
        try:
            if call is not None and call.contract.get("version", 1) >= 2:
                document = receive_response_stream(self._client, request)
            else:
                response = self._client.responses.create(
                    **request, store=False, timeout=self._settings.request_timeout_seconds
                )
                document = save_response(response)
        except ProviderGatewayError:
            raise
        except Exception as exc:
            raise _provider_error(exc, submission=True) from exc
        try:
            parsed = parse_response(document)
        except ProviderGatewayError as exc:
            exc.max_output_tokens = max_output_tokens
            raise
        return StructuredProviderResult(
            payload=parsed["payload"],
            response_id=parsed["responseId"],
            model=parsed["model"],
            usage=response_usage(document),
            request_hash=hashlib.sha256(
                json.dumps(
                    request_document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        )

    def retrieve_response(self, response_id: str) -> dict[str, Any]:
        if call := provider_call.get():
            self.validate_execution_contract(call.contract)
        try:
            raw = to_raw_response_wrapper(self._client.responses.retrieve)(
                response_id, timeout=httpx.Timeout(30, connect=10)
            )
            document = read_http_receipt(raw)
        except Exception as exc:
            raise _provider_error(exc, submission=False) from exc
        return save_response(document)


def _provider_error(
    exc: Exception, *, submission: bool, diagnostics: dict[str, Any] | None = None
) -> ProviderGatewayError:
    if isinstance(exc, ProviderGatewayError):
        return exc
    evidence = {**(diagnostics or {}), **timeout_evidence(exc)}
    timed_out = evidence["timeoutCategory"] is not None
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    request = getattr(exc, "request", None)
    evidence.update(request_size_evidence(request))
    client_id = getattr(request, "headers", {}).get("x-client-request-id")
    if client_id is None and type(exc).__module__.startswith("volcenginesdkarkruntime"):
        client_id = _optional_string(getattr(exc, "request_id", None))
    server_id = headers.get("x-request-id")
    client_id = client_id or evidence.get("clientRequestId")
    evidence.update(clientRequestId=client_id, serverRequestId=server_id)
    known_rejection = status_code in {400, 401, 403, 404, 413, 415, 422, 429}
    submission_unknown = submission and not known_rejection
    receive_receipt(
        {
            "serverRequestId": server_id,
            "clientRequestId": client_id,
            "result": {
                "requestError": {
                    "message": redact_transport_error(str(exc)),
                    "httpStatus": status_code,
                    "body": redact_transport_error(receipt_document(getattr(exc, "body", None))),
                    "diagnostics": evidence,
                }
            },
        }
    )
    retry_after = headers.get("retry-after")
    try:
        retry_after = float(retry_after) if retry_after else None
    except (TypeError, ValueError):
        from datetime import UTC, datetime
        from email.utils import parsedate_to_datetime

        try:
            retry_after = max(
                0, (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
            )
        except (ValueError, TypeError):
            retry_after = None
    code = (
        "local_adapter_error"
        if isinstance(exc, (AttributeError, TypeError)) and status_code is None
        else _optional_string(getattr(exc, "code", None))
        or ("provider_timeout" if timed_out else "provider_error")
    )
    return ProviderGatewayError(
        code=code,
        message=redact_transport_error(str(exc)) or "Ark request failed",
        retryable=bool(
            not submission_unknown
            and isinstance(status_code, int)
            and (status_code == 429 or status_code >= 500)
        ),
        submission_unknown=submission_unknown,
        request_id=server_id,
        client_request_id=client_id,
        http_status=status_code,
        retry_after_seconds=retry_after,
        timed_out=timed_out,
        diagnostics=evidence,
    )


def _image_data_url(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"reference image not found: {path}")
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("reference image exceeds 20 MiB")
    with Image.open(path) as image:
        image.verify()
        image_format = str(image.format or "").upper()
    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image_format)
    if mime is None:
        raise ValueError(f"unsupported reference image format: {image_format}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


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
        value = (
            usage.get(provider_name)
            if isinstance(usage, dict)
            else getattr(usage, provider_name, None)
        )
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
