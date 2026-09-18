"""Worker 侧 Ark SDK 网关 —— 实现 api 层 gateways.py 定义的 Provider Protocol。

职责:
- 把冻结输入(frozen_input_json / compiled provider prompt)提交给 Ark 的
  规划(结构化 JSON)、图片、视频、片段修复与诊断接口;
- 模型与密钥配置来自 ArkGatewaySettings(环境变量);
- 带 providerPromptVersion 新标记的任务直接发送冻结文本,不再追加隐藏说明。

失败语义:所有传输/协议异常统一折叠为 ProviderGatewayError(见 _provider_error);
付费提交结果未知时以 submission_unknown=True 标记,是否重试由 CatFlow 上层决定,
网关自身永不重试提交(SDK max_retries=0),也永不改写 CatFlow 业务状态。
"""

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
    """Ark 网关配置(冻结数据类):密钥、Base URL、四类模型与请求超时。

    构造时校验:api_key 去空白后非空,base_url 必须使用 HTTPS。
    """

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
        """从环境变量读取配置;未设置时用默认端点与模型,诊断模型默认回落到规划模型。"""
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
        """规划故事:纯文本冻结 prompt + JSON Schema,走结构化 JSON 通道(正文上限 4000 token)。"""
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
        # 多模态输入的首条文字指令默认值:告知模型参考图按顺序承担各自职责,规划分镜时须遵守
        input_instruction: str = "结合这些参考规划分镜，遵守指令中各图片的职责与顺序。",
    ) -> StructuredProviderResult:
        """规划分镜:冻结 prompt + 可选参考图,结构化 JSON 输出(正文上限 8000 token)。

        图片按传入顺序编号发送(见 _structured_response);
        传输/解析失败抛 ProviderGatewayError,不返回半成品结果。
        """
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
        """规划系列(多集大纲):结构化 JSON 输出;系列篇幅最长,正文上限 16000 token。"""
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
        """规划单集剧情:结构化 JSON 输出(正文上限 4000 token)。"""
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
        """解析原作素材:结构化 JSON 输出(正文上限 12000 token)。"""
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
        """图片诊断:候选图与参考图按传入顺序一并发给诊断模型,结构化 JSON 输出(上限 4000 token)。

        无图直接抛 ValueError(本地输入问题,不是 Provider 故障);
        传输/解析失败抛 ProviderGatewayError。
        """
        if not image_paths:
            raise ValueError("diagnosis requires at least one image")
        return self._structured_response(
            model=self._settings.diagnostic_model,
            prompt=prompt,
            image_paths=image_paths,
            output_schema=output_schema,
            max_output_tokens=4000,
            # 诊断输入指令:要求模型按发送顺序逐张比较全部图片后,再给出诊断结论
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
        """图片生成(Seedream images.generate),返回单张可下载图片的 URL 与用量。

        正文优先使用 compiled_provider_prompt(新版冻结全文,原样发送),否则由
        prompt + negative_prompt 现场编译;参考图转 base64 data URL 附带。
        契约 v2 时经 raw HTTP 响应先落持久回执再解析结果。
        失败语义:本地校验抛 ValueError;传输异常折叠为 ProviderGatewayError
        (submission=True);响应含 error→image_generation_failed;
        非恰好一张带 URL 的图→invalid_image_result(均不可重试)。
        """
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
        """整片视频生成提交(Seedance content_generation.tasks.create),返回任务 ID。

        本地校验:from_frame 严格模式只收一张首帧、不带其他参考与参考视频;
        参考图 ≤9 且路径/角色数量一致;参考视频必须是无凭证的 HTTPS URL。
        provider_prompt_version==1 时 prompt 已是冻结全文,原样发送;
        旧版本才在 prompt 后追加参考图职责顺序与成片连续性说明。
        失败语义:校验失败抛 ValueError;提交异常折叠为 ProviderGatewayError,
        结果未知时 submission_unknown=True(是否重试由上层决定,网关不重试)。
        """
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
        # 上一集成片参考的职责边界:只提供服装/道具/空间位置/时间/光线与直接接镜状态
        # 等连续性线索,不取代前五张固定参考图(儿童、猫咪、比例、当前环境、画风)的约束职责
        video_guidance = (
            "\n上一集成片只负责服装、道具、空间位置、时间、光线与直接接镜状态；"
            "不得取代前五张图片对儿童、猫咪、比例、当前环境和画风的约束。"
            if reference_video_url is not None
            else ""
        )
        content: list[dict[str, object]] = [
            {
                "type": "text",
                # provider_prompt_version==1:prompt 即编译好的冻结全文,不再追加任何隐藏说明;
                # 旧版本:在冻结 prompt 后追加参考图职责顺序(存在成片参考时再追加 video_guidance)
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
        """按任务 ID 查询视频生成状态,归一化为 VideoPollResult。

        状态映射:queued/running→running;succeeded→提取 video_url 与 last_frame_url
        (缺 video_url 时以 missing_video_url 错误返回);failed/cancelled/expired→failed
        (附 Provider 错误码);其余→unknown(provider_state_unrecognized)。
        契约 v2 时先落 HTTP 回执再解析;查询异常折叠为
        ProviderGatewayError(submission=False,轮询不产生新的付费提交)。
        """
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
        """片段修复提交:重生成问题区间对应的短视频,返回任务 ID。

        图片顺序 = 入锚帧(可选)→出锚帧(可选)→Canon 参考图;edit_existing 模式
        在图片前附带上下文视频(HTTPS URL),from_frame 模式只发首/末帧、绝不发视频。
        请求合法性(时长、角色顺序、URL 形态等)由 SegmentVideoGenerationRequest
        的 __post_init__ 保证;提交失败语义同 submit_video。
        """
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
                # 修复正文三级回退:compiled_provider_prompt(新版冻结全文,原样发送)
                # → segment-edit-v3/v4/v5 的"prompt + 需要避免的问题"简化拼接
                # → 最早版本的完整拼接(修改目标/精确问题时间/负面约束/媒体职责)
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
                        # 媒体职责切分:视频1(上下文视频)只负责原动作、机位、节奏与前后连续性,
                        # 各图片按 role_sequence 顺序承担锚帧与 Canon 参考的约束职责
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
        # 用户输入首条文字的默认值:纯文本调用时它就是全部用户输入,有图片时排在图片之前
        input_instruction: str = "只返回符合 Schema 的 JSON 对象。",
    ) -> StructuredProviderResult:
        """结构化 JSON 调用通道:所有规划/诊断方法共用的 Responses 提交与解析。

        有图片时组装多模态 input:指令在前,每张图前插入"有序图片N"标签保证顺序语义;
        request_hash 是冻结请求文档(模型/prompt/Schema/图片 SHA256/指令)的
        SHA256,供 Receipt 对账;解析经 parse_response 完成(含确定性 JSON 修复,
        修复计数以 text_repairs 透传)。契约 v2 走流式接收(receive_response_stream),
        否则同步 create 后 save_response。
        失败语义:传输/流式异常折叠为 ProviderGatewayError(submission=True);
        解析失败抛 ProviderGatewayError 并附 max_output_tokens 供上层判断是否截断。
        """
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
        # Schema 指令 = 冻结 prompt + 输出契约:只返回一个 JSON 对象、不要 Markdown,
        # 且必须严格符合随附的 JSON Schema(sort_keys 序列化,保证同输入得到同指令文本)
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
            text_repairs=tuple(parsed.get("textRepairs") or ()),
        )

    def retrieve_response(self, response_id: str) -> dict[str, Any]:
        """按 Response ID 重读已保存的 Ark 响应(恢复/核实路径),并落持久回执。

        先校验执行契约(不得跨环境重读冻结任务);查询异常折叠为
        ProviderGatewayError(submission=False,重读不产生新的付费提交)。
        """
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
    """把任意 SDK/传输异常折叠为统一的 ProviderGatewayError,并落脱敏后的错误回执。

    证据提取:超时分类、请求体大小、客户端/服务端请求 ID、retry-after
    (兼容秒数与 HTTP 日期两种格式);错误文本经 redact_transport_error 脱敏,
    避免签名 URL 等敏感内容落库。
    失败语义:submission=True 且非明确 4xx 拒绝时 submission_unknown=True
    (付费提交结果未知,是否重试由 CatFlow 决定);仅结果已知且 429/5xx 才标
    retryable;AttributeError/TypeError 且无 HTTP 状态视为 local_adapter_error
    (本地 SDK 适配问题,未触达 Provider)。
    """
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
    """把参考图编码为 base64 data URL,发送前完成本地校验。

    要求:文件存在、≤20MiB、可被 PIL 解码且格式为 PNG/JPEG/WEBP;
    不满足抛 ValueError —— 属本地输入问题,不是 Provider 故障。
    """
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
    """把 Provider usage(dict 或 SDK 对象)归一化为公开字段名的整型字典;缺失字段跳过。"""
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
