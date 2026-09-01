"""火山Ark导演、Seedream和Seedance网关。

本模块只负责协议映射、错误分类和供应商返回值，不修改项目、场景或镜头状态。
调用意图、幂等和恢复由Application Service与Repository共同负责。
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from volcenginesdkarkruntime import Ark
from volcenginesdkarkruntime._exceptions import (
    ArkAPIConnectionError,
    ArkAPIError,
    ArkAPITimeoutError,
)

from ...application.ports import (
    CreativeDirectorResult,
    DirectorResult,
    GatewayError,
    ImageDiagnosticResult,
    ImageResult,
    VideoDiagnosticResult,
    VideoTaskResult,
)
from ...config import RuntimeSettings
from ...domain.rendering import AudioPolicy, VideoInputPlan
from .review_schemas import IMAGE_DIAGNOSTIC_SCHEMA, VIDEO_DIAGNOSTIC_SCHEMA


class ArkGatewayError(GatewayError):
    """已脱敏且可用于恢复决策的Ark错误。"""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retryable: bool,
        submission_unknown: bool = False,
        request_id: str | None = None,
        timed_out: bool = False,
    ) -> None:
        super().__init__(
            message,
            code=code,
            retryable=retryable,
            submission_unknown=submission_unknown,
            request_id=request_id,
            timed_out=timed_out,
        )


class ArkGateway:
    """当前系统唯一的Ark运行时边界。"""

    def __init__(
        self,
        settings: RuntimeSettings,
        *,
        client: Any | None = None,
    ) -> None:
        settings.validate_for_ark_access()
        self._settings = settings
        self._client = client or Ark(
            api_key=settings.ark_api_key,
            base_url=settings.ark_base_url,
        )

    @property
    def model(self) -> str:
        return self._settings.ark_planning_model

    @property
    def analysis_model(self) -> str:
        # The four creative roles intentionally share the planning model.
        # review_model remains reserved for post-generation video diagnostics.
        return self.model

    @property
    def image_model(self) -> str:
        return self._settings.ark_image_model

    @property
    def video_model(self) -> str:
        return self._settings.ark_video_model

    @property
    def review_model(self) -> str:
        return self._settings.ark_review_model

    def generate_creative_text(
        self,
        *,
        prompt: str,
        output_name: str,
    ) -> CreativeDirectorResult:
        return self._generate_flexible_text(
            prompt=prompt,
            output_name=output_name,
            mode="creative_text",
        )

    def generate_storyboard_text(
        self,
        *,
        prompt: str,
        output_name: str,
        image_paths: tuple[Path, ...] = (),
    ) -> CreativeDirectorResult:
        return self._generate_flexible_text(
            prompt=prompt,
            output_name=output_name,
            mode="storyboard_text",
            image_paths=image_paths,
        )

    def _generate_flexible_text(
        self,
        *,
        prompt: str,
        output_name: str,
        mode: str,
        image_paths: tuple[Path, ...] = (),
    ) -> CreativeDirectorResult:
        """Own tolerant creative output and ordered multimodal reference transport."""

        input_text = f"生成一个{output_name}。"
        ordered_image_hashes: list[str] = []
        input_content: Any = input_text
        if image_paths:
            content: list[dict[str, str]] = [{"type": "input_text", "text": input_text}]
            for index, path in enumerate(image_paths, 1):
                _validate_reference_file(path)
                ordered_image_hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
                content.append({"type": "input_text", "text": f"按顺序查看@图片{index}"})
                content.append(
                    {
                        "type": "input_image",
                        "image_url": _analysis_preview_data_url(path),
                    }
                )
            input_content = [{"role": "user", "content": content}]
        request_document: dict[str, Any] = {
            "input": input_text,
            "instructions": prompt,
            "mode": mode,
            "model": self.model,
            "outputName": output_name,
        }
        if ordered_image_hashes:
            request_document["orderedImageSha256"] = ordered_image_hashes
        request_hash = _json_hash(request_document)
        response = self._run_planning_response(
            instructions=prompt,
            input_text=input_content,
            text_format={"type": "text"},
        )
        response_text = _response_text(response, reject_blank=True)
        try:
            decoded = json.loads(response_text)
        except json.JSONDecodeError:
            payload: dict[str, Any] | str = response_text
        else:
            payload = decoded if isinstance(decoded, dict) else response_text
        return CreativeDirectorResult(
            payload=payload,
            response_id=response.id,
            model=response.model,
            request_hash=request_hash,
        )

    def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
        image_paths: tuple[Path, ...] = (),
    ) -> DirectorResult:
        if image_paths:
            return self.analyze_structured(
                prompt=prompt,
                schema=schema,
                output_name=output_name,
                image_paths=image_paths,
            )
        instructions, text_format = self._structured_output(
            prompt,
            schema,
            output_name,
        )
        request_hash = _json_hash(
            {
                "model": self.model,
                "instructions": instructions,
                "schema": schema,
                "outputName": output_name,
            }
        )
        response = self._run_planning_response(
            instructions=instructions,
            input_text=f"生成一个{output_name}对象。",
            text_format=text_format,
        )
        try:
            payload = json.loads(_response_text(response))
        except json.JSONDecodeError as exc:
            raise ArkGatewayError(
                "Ark导演没有返回合法JSON对象。",
                code="invalid_director_output",
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise ArkGatewayError(
                "Ark导演返回的JSON顶层必须是对象。",
                code="invalid_director_output",
                retryable=False,
            )
        return DirectorResult(
            payload=payload,
            response_id=response.id,
            model=response.model,
            request_hash=request_hash,
        )

    def _run_planning_response(
        self,
        *,
        instructions: str,
        input_text: Any,
        text_format: dict[str, Any],
    ) -> Any:
        """Own the planning request lifecycle and its recoverable error policy."""

        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=instructions,
                input=input_text,
                text={"format": text_format},
                temperature=0.35,
                max_output_tokens=8000,
                thinking={"type": "disabled"},
                store=False,
                timeout=self._settings.ark_director_request_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if response.status != "completed":
            incomplete_reason = getattr(
                getattr(response, "incomplete_details", None),
                "reason",
                "",
            )
            suffix = f"，原因={incomplete_reason}" if incomplete_reason else ""
            raise ArkGatewayError(
                f"Ark导演任务状态为{response.status!r}{suffix}",
                code=(
                    f"director_incomplete_{incomplete_reason}"
                    if response.status == "incomplete" and incomplete_reason
                    else "director_not_completed"
                ),
                retryable=incomplete_reason == "max_output_tokens",
            )
        return response

    def analyze_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
        image_paths: tuple[Path, ...],
    ) -> DirectorResult:
        """Run paid multimodal analysis using compressed, non-persistent previews."""

        if len(image_paths) > 9:
            raise ArkGatewayError(
                "片段创作分析最多允许9张图片",
                code="invalid_shot_assistance_image_count",
                retryable=False,
            )
        instructions, text_format = self._structured_output(prompt, schema, output_name)
        image_hashes: list[str] = []
        content: list[dict[str, str]] = [
            {"type": "input_text", "text": f"生成一个{output_name}对象。"}
        ]
        for index, path in enumerate(image_paths, 1):
            _validate_reference_file(path)
            image_hashes.append(hashlib.sha256(path.read_bytes()).hexdigest())
            content.append({"type": "input_text", "text": f"按顺序查看@图片{index}"})
            content.append(
                {
                    "type": "input_image",
                    "image_url": _analysis_preview_data_url(path),
                }
            )
        request_hash = _json_hash(
            {
                "model": self.analysis_model,
                "instructions": instructions,
                "schema": schema,
                "outputName": output_name,
                "orderedImageSha256": image_hashes,
            }
        )
        try:
            response = self._client.responses.create(
                model=self.analysis_model,
                instructions=instructions,
                input=[{"role": "user", "content": content}],
                text={"format": text_format},
                temperature=0.2,
                max_output_tokens=6000,
                thinking={"type": "disabled"},
                store=False,
                timeout=self._settings.ark_review_request_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if response.status != "completed":
            raise ArkGatewayError(
                f"Ark片段创作分析状态为{response.status!r}",
                code="shot_assistance_not_completed",
                retryable=False,
            )
        try:
            payload = json.loads(_response_text(response))
        except json.JSONDecodeError as exc:
            raise ArkGatewayError(
                "Ark片段创作分析没有返回合法JSON对象。",
                code="invalid_shot_assistance_output",
                retryable=False,
            ) from exc
        if not isinstance(payload, dict):
            raise ArkGatewayError(
                "Ark片段创作分析的JSON顶层必须是对象。",
                code="invalid_shot_assistance_output",
                retryable=False,
            )
        return DirectorResult(
            payload=payload,
            response_id=response.id,
            model=response.model,
            request_hash=request_hash,
        )

    def generate_image(
        self,
        *,
        prompt: str,
        reference_paths: tuple[Path, ...],
    ) -> ImageResult:
        """生成一张场景视觉基准图或开场视觉锚点。"""

        try:
            request: dict[str, Any] = {
                "model": self.image_model,
                "prompt": prompt,
                "response_format": "url",
                "size": "2K",
                "watermark": False,
                "output_format": "png",
                "timeout": self._settings.ark_image_request_timeout_seconds,
            }
            if reference_paths:
                request["image"] = [_asset_data_url(path) for path in reference_paths]
            response = self._client.images.generate(
                **request,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        except (AttributeError, TypeError) as exc:
            raise ArkGatewayError(
                "Seedream单图请求参数无法由Ark SDK序列化。",
                code="provider_request_serialization_failed",
                retryable=False,
            ) from exc
        if len(response.data or ()) != 1 or not getattr(response.data[0], "url", None):
            raise ArkGatewayError(
                "Seedream单图请求没有返回唯一可下载图片。",
                code="invalid_image_result",
                retryable=False,
            )
        return ImageResult(
            url=response.data[0].url,
            model=getattr(response, "model", self.image_model),
        )

    def diagnose_image(
        self,
        *,
        prompt: str,
        image_path: Path,
    ) -> ImageDiagnosticResult:
        result = self.analyze_structured(
            prompt=prompt,
            schema=IMAGE_DIAGNOSTIC_SCHEMA,
            output_name="ImageSemanticDiagnostic",
            image_paths=(image_path,),
        )
        try:
            payload = result.payload
            return ImageDiagnosticResult(
                identity_ok=bool(payload["identityOk"]),
                style_ok=bool(payload["styleOk"]),
                constraints_ok=bool(payload["constraintsOk"]),
                confidence=float(payload["confidence"]),
                violations=tuple(str(item) for item in payload["violations"]),
                evidence=tuple(
                    {
                        "object": str(item["object"]),
                        "observation": str(item["observation"]),
                        "relationError": (
                            None if item["relationError"] is None else str(item["relationError"])
                        ),
                    }
                    for item in payload["evidence"]
                ),
                response_id=result.response_id,
                model=result.model,
                request_hash=result.request_hash,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ArkGatewayError(
                "Ark图片语义诊断没有返回合法结构。",
                code="invalid_image_diagnostic_output",
                retryable=False,
            ) from exc

    def diagnose_video_frames(
        self,
        *,
        prompt: str,
        frame_paths: tuple[Path, ...],
        reference_paths: tuple[Path, ...] = (),
        reference_labels: tuple[str, ...] = (),
    ) -> VideoDiagnosticResult:
        """按时间顺序审核抽帧序列；诊断结果不直接批准最终视频。"""

        if not 4 <= len(frame_paths) <= 12:
            raise ArkGatewayError(
                "视频语义诊断需要4至12张有序抽帧",
                code="invalid_video_review_frame_count",
                retryable=False,
            )
        if len(reference_paths) != len(reference_labels):
            raise ArkGatewayError(
                "视频身份诊断的参考图与职责标签数量不一致",
                code="invalid_video_review_reference_manifest",
                retryable=False,
            )
        schema = VIDEO_DIAGNOSTIC_SCHEMA
        instructions, text_format = self._structured_output(
            prompt,
            schema,
            "VideoSemanticDiagnostic",
        )
        frame_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in frame_paths]
        reference_hashes = [
            hashlib.sha256(path.read_bytes()).hexdigest() for path in reference_paths
        ]
        request_hash = _json_hash(
            {
                "model": self.review_model,
                "instructions": instructions,
                "schema": schema,
                "orderedFrameSha256": frame_hashes,
                "referenceSha256": reference_hashes,
                "referenceLabels": list(reference_labels),
            }
        )
        content: list[dict[str, str]] = [
            {
                "type": "input_text",
                "text": (
                    "先给出身份 Canon、已批准本集设计与画风参考，再给出按视频时间排序的抽帧。"
                    "必须逐项比较固定特征；不能只根据文字猜测身份。请只返回结构化诊断。"
                ),
            }
        ]
        for label, path in zip(reference_labels, reference_paths, strict=True):
            content.append({"type": "input_text", "text": f"参考职责：{label}"})
            content.append(
                {
                    "type": "input_image",
                    "image_url": _asset_data_url(path),
                }
            )
        for index, path in enumerate(frame_paths, 1):
            content.append({"type": "input_text", "text": f"有序抽帧{index}"})
            content.append(
                {
                    "type": "input_image",
                    "image_url": _asset_data_url(path),
                }
            )
        try:
            response = self._client.responses.create(
                model=self.review_model,
                instructions=instructions,
                input=[{"role": "user", "content": content}],
                text={"format": text_format},
                temperature=0,
                max_output_tokens=2400,
                thinking={"type": "disabled"},
                store=False,
                timeout=self._settings.ark_review_request_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if response.status != "completed":
            raise ArkGatewayError(
                f"Ark视频语义诊断状态为{response.status!r}",
                code="video_diagnostic_not_completed",
                retryable=False,
            )
        try:
            payload = json.loads(_response_text(response))
            return VideoDiagnosticResult(
                identity_ok=bool(payload["identityOk"]),
                identity_assessment=str(payload["identityAssessment"]),
                style_ok=bool(payload["styleOk"]),
                constraints_ok=bool(payload["constraintsOk"]),
                narrative_order_ok=bool(payload["narrativeOrderOk"]),
                confidence=float(payload["confidence"]),
                violations=tuple(str(item) for item in payload["violations"]),
                evidence=tuple(
                    {
                        "timestamp": str(item["timestamp"]),
                        "object": str(item["object"]),
                        "observation": str(item["observation"]),
                        "relationError": (
                            None if item["relationError"] is None else str(item["relationError"])
                        ),
                    }
                    for item in payload["evidence"]
                ),
                shot_boundaries_seconds=tuple(
                    float(item) for item in payload["shotBoundariesSeconds"]
                ),
                response_id=response.id,
                model=response.model,
                request_hash=request_hash,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArkGatewayError(
                "Ark视频语义诊断没有返回合法结构。",
                code="invalid_video_diagnostic_output",
                retryable=False,
            ) from exc

    def submit_video(
        self,
        *,
        prompt: str,
        input_plan: VideoInputPlan,
        input_sources: tuple[Path | str, ...],
    ) -> VideoTaskResult:
        if len(input_plan.bindings) != len(input_sources):
            raise ArkGatewayError(
                "多模态输入计划与实际素材数量不一致",
                code="invalid_visual_input_count",
                retryable=False,
            )
        if (
            sum(
                source.stat().st_size
                for source in input_sources
                if isinstance(source, Path) and source.is_file()
            )
            > 64 * 1024 * 1024
        ):
            raise ArkGatewayError(
                "多模态请求素材总大小超过64MB",
                code="reference_payload_too_large",
                retryable=False,
            )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for source, binding in zip(
            input_sources,
            input_plan.bindings,
            strict=True,
        ):
            field_name = "image_url" if binding.modality.value == "image" else "video_url"
            if isinstance(source, Path):
                if binding.modality.value != "image":
                    raise ArkGatewayError(
                        "Seedance视频延展的reference_video必须使用供应商Web URL",
                        code="reference_video_url_required",
                        retryable=False,
                    )
                _validate_reference_file(source)
                url = _asset_data_url(source)
            else:
                if binding.modality.value != "video" or not source.startswith("https://"):
                    raise ArkGatewayError(
                        "供应商视频参考必须是HTTPS URL",
                        code="invalid_reference_video_url",
                        retryable=False,
                    )
                url = source
            content.append(
                {
                    "type": field_name,
                    field_name: {"url": url},
                    "role": binding.provider_role.value,
                }
            )
        try:
            response = self._client.content_generation.tasks.create(
                model=self.video_model,
                content=content,
                return_last_frame=True,
                generate_audio=input_plan.audio_policy is AudioPolicy.NATIVE_REQUIRED,
                watermark=False,
                resolution=input_plan.resolution,
                ratio="9:16",
                duration=input_plan.duration_seconds,
                timeout=self._settings.ark_video_api_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=True) from exc
        if not response.id:
            raise ArkGatewayError(
                "Seedance没有返回task ID。",
                code="empty_task_id",
                retryable=False,
            )
        return VideoTaskResult(task_id=response.id, status="queued")

    def get_video_task(self, task_id: str) -> VideoTaskResult:
        try:
            task = self._client.content_generation.tasks.get(
                task_id=task_id,
                timeout=self._settings.ark_video_api_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=False) from exc
        return _video_task_result(task)

    def cancel_video_task(self, task_id: str) -> VideoTaskResult:
        """Delete one Ark video task that the Provider still reports as queued."""

        normalized_task_id = task_id.strip()
        if not normalized_task_id:
            raise ValueError("Ark视频任务ID不能为空")
        try:
            self._client.content_generation.tasks.delete(
                task_id=normalized_task_id,
                timeout=self._settings.ark_video_api_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=False) from exc
        return VideoTaskResult(task_id=normalized_task_id, status="cancelled")

    def list_video_tasks(
        self,
        *,
        model: str,
        page_size: int = 100,
    ) -> tuple[VideoTaskResult, ...]:
        """列出可用于submission_unknown人工对账的近期视频任务。"""

        if not 1 <= page_size <= 100:
            raise ValueError("Ark视频任务列表page_size必须在1至100之间")
        try:
            response = self._client.content_generation.tasks.list(
                page_num=1,
                page_size=page_size,
                model=model,
                timeout=self._settings.ark_video_api_timeout_seconds,
            )
        except ArkAPIError as exc:
            raise _provider_error(exc, submission=False) from exc
        return tuple(_video_task_result(item) for item in response.items)

    def _structured_output(
        self,
        prompt: str,
        schema: dict[str, Any],
        output_name: str,
    ) -> tuple[str, dict[str, Any]]:
        if self._settings.ark_structured_output_mode == "json_schema":
            return prompt, {
                "type": "json_schema",
                "json_schema": {
                    "name": output_name,
                    "description": "镜头队列中的结构化镜头建议",
                    "schema": schema,
                    "strict": True,
                },
            }
        if self._settings.ark_structured_output_mode == "json_object_schema_prompt":
            return (
                "\n".join(
                    (
                        prompt,
                        "只允许输出下列JSON Schema定义的字段和类型：",
                        json.dumps(
                            schema,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                ),
                {"type": "json_object"},
            )
        raise ArkGatewayError(
            "不支持的Ark结构化输出模式。",
            code="invalid_structured_output_mode",
            retryable=False,
        )


def _response_text(response: Any, *, reject_blank: bool = False) -> str:
    parts: list[str] = []
    for output in response.output:
        if getattr(output, "type", None) != "message":
            continue
        for part in getattr(output, "content", ()):
            if getattr(part, "type", None) == "output_text":
                parts.append(part.text)
    text = _repair_utf8_mojibake("".join(parts))
    if not parts or (reject_blank and not text.strip()):
        raise ArkGatewayError(
            "Ark Responses没有返回文本。",
            code="empty_director_result",
            retryable=False,
        )
    return text


def _repair_utf8_mojibake(value: str) -> str:
    """修复供应商SDK把UTF-8响应按Latin-1展开后的可逆乱码。

    正常中文无法编码为Latin-1，会原样返回；只有整段文本能够无损还原，且原文
    命中常见UTF-8乱码标记时才替换，避免改写合法的拉丁文本或结构化字段名。
    """

    best = value
    best_cjk = _cjk_count(value)
    for encoding in ("latin-1", "cp1252"):
        try:
            candidate = value.encode(encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidate_cjk = _cjk_count(candidate)
        if candidate_cjk > best_cjk:
            best = candidate
            best_cjk = candidate_cjk
    return best


def _cjk_count(value: str) -> int:
    return sum(0x3400 <= ord(character) <= 0x9FFF for character in value)


def _analysis_preview_data_url(path: Path) -> str:
    """Encode a bounded JPEG preview without modifying or persisting the source image."""

    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            payload = BytesIO()
            image.save(payload, format="JPEG", quality=82, optimize=True)
    except (OSError, ValueError) as exc:
        raise ArkGatewayError(
            f"无法创建分析预览图: {path.name}",
            code="analysis_preview_failed",
            retryable=False,
        ) from exc
    return f"data:image/jpeg;base64,{base64.b64encode(payload.getvalue()).decode('ascii')}"


def _asset_data_url(path: Path) -> str:
    mime_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
    }.get(path.suffix.lower())
    if mime_type is None:
        raise ArkGatewayError(
            f"不支持的Ark参考素材类型: {path.suffix}",
            code="unsupported_input_asset",
            retryable=False,
        )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ArkGatewayError(
            f"无法读取Ark输入素材: {path}",
            code="input_asset_read_failed",
            retryable=False,
        ) from exc
    return f"data:{mime_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _validate_reference_file(path: Path) -> None:
    """在Base64编码前阻断缺失、空文件和超过官方单素材大小限制的输入。"""

    if not path.is_file():
        raise ArkGatewayError(
            f"参考素材不存在: {path.name}",
            code="missing_reference_file",
            retryable=False,
        )
    size = path.stat().st_size
    if size <= 0 or size > 30 * 1024 * 1024:
        raise ArkGatewayError(
            f"参考素材大小不合法: {path.name}",
            code="invalid_reference_file_size",
            retryable=False,
        )


def _provider_error(exc: ArkAPIError, *, submission: bool) -> ArkGatewayError:
    body = getattr(exc, "body", None)
    nested = (
        body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
    )
    code = getattr(exc, "code", None) or nested.get("code") or type(exc).__name__
    message = nested.get("message") or getattr(exc, "message", None) or "Ark请求失败"
    request_id = getattr(exc, "request_id", None)
    if request_id:
        # Request ID不是鉴权秘密，可用于Ark控制台和工单对账；签名URL、
        # API Key及请求正文仍不得进入错误记录。
        message = f"{message} (requestId={request_id})"
    if isinstance(exc, (ArkAPIConnectionError, ArkAPITimeoutError)):
        # 提交阶段断线时无法判断供应商是否已创建收费任务，必须冻结对账；
        # 查询阶段则可以安全重试同一个task ID。
        return ArkGatewayError(
            message,
            code=code,
            retryable=not submission,
            submission_unknown=submission,
            request_id=request_id,
            timed_out=isinstance(exc, ArkAPITimeoutError),
        )
    status_code = getattr(exc, "status_code", None)
    quota_error = any(
        marker in str(code).lower() for marker in ("quota", "balance", "insufficient", "account")
    )
    return ArkGatewayError(
        message,
        code=str(code),
        retryable=bool(
            status_code and (status_code >= 500 or status_code == 429) and not quota_error
        ),
        request_id=request_id,
    )


def _video_task_result(task: Any) -> VideoTaskResult:
    """把Get/List的SDK对象归一为同一严格结果，避免Application依赖SDK类型。"""

    content = getattr(task, "content", None)
    error = getattr(task, "error", None)
    created_at = getattr(task, "created_at", None)
    raw_duration = getattr(task, "duration", None)
    try:
        duration_seconds = None if raw_duration is None else int(raw_duration)
    except (TypeError, ValueError):
        duration_seconds = None
    return VideoTaskResult(
        task_id=str(task.id),
        status=str(task.status),
        video_url=(None if content is None else getattr(content, "video_url", None)),
        last_frame_url=(
            None if content is None else getattr(content, "last_frame_url", None)
        ),
        error_code=None if error is None else getattr(error, "code", None),
        error_message=None if error is None else getattr(error, "message", None),
        model=getattr(task, "model", None),
        created_at=(
            datetime.fromtimestamp(created_at, tz=timezone.utc)
            if isinstance(created_at, int | float)
            else None
        ),
        duration_seconds=duration_seconds,
        ratio=getattr(task, "ratio", None),
        resolution=getattr(task, "resolution", None),
        generate_audio=getattr(task, "generate_audio", None),
    )


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
