"""火山方舟 Ark Provider 配置 —— 唯一外部模型服务。

Ark 模型家族(2026-09 当前):
- planning_model   : doubao-seed-2-1-pro-260628 —— JSON-Schema 规划(故事 / 分镜 / 系列 / 诊断)
- image_model      : doubao-seedream-5-0-260128 —— 图片生成(参考图 / 环境图 / 关键帧)
- video_model      : doubao-seedance-2-0-260128 —— 视频生成(整片 / 逐镜 / 局部编辑)
- diagnostic_model : 同 planning_model —— 失败诊断(视频诊断 / 镜头诊断)

Cap 限制:
- 整片 / 逐镜 最多 9 张图片参考 + 0 视频参考(ARK_MAX_VIDEO_IMAGE_REFERENCES=9)
- 局部编辑 最多 9 张图片 + 1 个视频上下文(ARK_MAX_SEGMENT_VIDEO_REFERENCES=1)
- 视频上下文时长 2~15 秒

付费开关:
- CATFLOW_PAID_CALLS_ENABLED=false (默认)时所有付费提交被拒,但历史任务可继续轮询
- 必须显式 .env=true 才允许新提交

Response 保留:
- ARK_RESPONSE_RETENTION_SECONDS 默认 259200 (3 天),即 Responses API 仅 3 天内可重读
- 本机 frozen_input 永久保留,所以即使 Ark 过期也能用 Receipt 重放
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from catflow.infrastructure.object_storage import ObjectStorageSettings


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    """火山方舟 Ark 的运行时配置(冻结)。"""

    # 4 个 Ark 模型 ID + 能力版本(frozen_input 校验用)
    provider: Literal["ark"]
    planning_model: str
    image_model: str
    video_model: str
    diagnostic_model: str
    capability_revision: str
    # 付费开关(默认 False;需显式 .env=true 才允许新提交)
    paid_calls_enabled: bool
    # 整片 / 逐镜 / 局部编辑的参考图与视频上限(对应 Ark 实际 cap)
    maximum_video_references: int
    maximum_video_input_references: int = 3
    maximum_segment_image_references: int = 9
    maximum_segment_video_references: int = 1
    # 视频修复入点视频时长范围 (0, 15] 秒
    minimum_segment_reference_seconds: float = 2
    maximum_segment_reference_seconds: float = 15
    # 受管对象存储(TOS / MinIO)是否 ready(供 video_repair 使用)
    segment_reference_publishing_ready: bool = False
    # API 网关与响应保留
    api_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    response_retention_seconds: int = 259200

    def __post_init__(self) -> None:
        """配置合法性校验:启动时崩,避免运行时隐藏错。"""
        from urllib.parse import urlsplit

        # 1. endpoint 必须 HTTPS,且无 user:password / query / fragment
        endpoint = urlsplit(self.api_base_url)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError(
                "Ark endpoint must be an HTTPS URL without credentials or query parameters"
            )
        # 2. 视频修复上下文时长范围 (0, 15] 秒,下限严格大于 0
        if (
            not 0
            < self.minimum_segment_reference_seconds
            <= self.maximum_segment_reference_seconds
            <= 15
        ):
            raise ValueError("segment reference duration capability must be within 0–15 seconds")
        # 3. Response 保留范围 1 秒 ~ 3 天(259200 秒 = 72h)
        if not 1 <= self.response_retention_seconds <= 259200:
            raise ValueError("Response retention must be between 1 and 259200 seconds")

    @property
    def segment_repair_block_reason(self) -> str | None:
        """视频局部编辑前置条件检查 —— 返回 None 表示可用,否则返回中文原因。

        两个阻塞条件:
        1. 上下文视频未发布到受管 HTTPS(没配 TOS / MinIO)
        2. 配置允许的视频参考数 < 1(本次提交要传上下文视频)
        """
        if not self.segment_reference_publishing_ready:
            return (
                "Ark 片段修复需要先把本地上下文视频安全发布为 Provider 可读取的 HTTPS URL；"
                "当前未配置受管发布器。"
            )
        if self.maximum_segment_video_references < 1:
            return "configured Provider capability cannot accept the repair context video"
        return None

    @property
    def segment_repair_supported(self) -> bool:
        """是否支持视频局部编辑(segment_repair_block_reason 为 None)。"""
        return self.segment_repair_block_reason is None

    @classmethod
    def from_env(cls, *, segment_reference_publishing_ready: bool | None = None) -> ProviderRuntime:
        """从 .env 加载,优先级:命令行入参 > ObjectStorageSettings.configured > False。

        segment_reference_publishing_ready 含义:受管对象存储(TOS / MinIO)已 ready,
        可以把本机 MP4 发布成 Ark 可访问的 HTTPS URL。

        若 segment_reference_publishing_ready 未显式传入,从 ObjectStorageSettings 配置推断:
        配置了 TOS 凭据 + bucket 即视为 true
        """
        if segment_reference_publishing_ready is None:
            # 用 ObjectStorageSettings.configured 作为近似信号
            # (更精确应 .head_bucket() 但启动时不能阻塞)
            segment_reference_publishing_ready = ObjectStorageSettings.from_env().configured
        return cls(
            provider="ark",
            api_base_url=os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            response_retention_seconds=int(
                os.environ.get("ARK_RESPONSE_RETENTION_SECONDS", "259200")
            ),
            planning_model=os.environ.get("ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"),
            image_model=os.environ.get("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"),
            video_model=os.environ.get("ARK_VIDEO_MODEL", "doubao-seedance-2-0-260128"),
            diagnostic_model=os.environ.get(
                "ARK_DIAGNOSTIC_MODEL",
                # 默认诊断模型 = 规划模型(诊断与规划同能力)
                os.environ.get("ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"),
            ),
            capability_revision="ark-seedance-2.0-v1",
            # 付费开关:严格解析 "true",其他任何值都视作关闭
            paid_calls_enabled=os.environ.get("CATFLOW_PAID_CALLS_ENABLED", "false").lower()
            == "true",
            maximum_video_references=int(os.environ.get("ARK_MAX_VIDEO_IMAGE_REFERENCES", "9")),
            maximum_video_input_references=int(os.environ.get("ARK_MAX_VIDEO_REFERENCES", "3")),
            minimum_segment_reference_seconds=float(
                os.environ.get("ARK_MIN_SEGMENT_REFERENCE_SECONDS", "2")
            ),
            maximum_segment_reference_seconds=float(
                os.environ.get("ARK_MAX_SEGMENT_REFERENCE_SECONDS", "15")
            ),
            maximum_segment_image_references=int(
                os.environ.get("ARK_MAX_SEGMENT_IMAGE_REFERENCES", "9")
            ),
            maximum_segment_video_references=int(
                os.environ.get("ARK_MAX_SEGMENT_VIDEO_REFERENCES", "1")
            ),
            segment_reference_publishing_ready=segment_reference_publishing_ready,
        )