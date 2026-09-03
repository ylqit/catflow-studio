from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from catflow.infrastructure.object_storage import ObjectStorageSettings


@dataclass(frozen=True, slots=True)
class ProviderRuntime:
    provider: Literal["ark"]
    planning_model: str
    image_model: str
    video_model: str
    diagnostic_model: str
    capability_revision: str
    paid_calls_enabled: bool
    maximum_video_references: int
    maximum_segment_image_references: int = 9
    maximum_segment_video_references: int = 1
    segment_reference_publishing_ready: bool = False

    @property
    def segment_repair_block_reason(self) -> str | None:
        if not self.segment_reference_publishing_ready:
            return (
                "Ark 片段修复需要先把本地上下文视频安全发布为 Provider 可读取的 HTTPS URL；"
                "当前未配置受管发布器。"
            )
        if self.maximum_segment_video_references < 1:
            return "configured Provider capability cannot accept the repair context video"
        if self.maximum_segment_image_references < 7:
            return "configured Provider capability cannot accept seven ordered repair images"
        return None

    @property
    def segment_repair_supported(self) -> bool:
        return self.segment_repair_block_reason is None

    @classmethod
    def from_env(
        cls, *, segment_reference_publishing_ready: bool | None = None
    ) -> ProviderRuntime:
        if segment_reference_publishing_ready is None:
            segment_reference_publishing_ready = ObjectStorageSettings.from_env().configured
        return cls(
            provider="ark",
            planning_model=os.environ.get("ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"),
            image_model=os.environ.get("ARK_IMAGE_MODEL", "doubao-seedream-5-0-260128"),
            video_model=os.environ.get("ARK_VIDEO_MODEL", "doubao-seedance-2-0-260128"),
            diagnostic_model=os.environ.get(
                "ARK_DIAGNOSTIC_MODEL",
                os.environ.get("ARK_PLANNING_MODEL", "doubao-seed-2-1-pro-260628"),
            ),
            capability_revision="ark-seedance-2.0-v1",
            paid_calls_enabled=os.environ.get("CATFLOW_PAID_CALLS_ENABLED", "false").lower()
            == "true",
            maximum_video_references=5,
            maximum_segment_image_references=int(
                os.environ.get("ARK_MAX_SEGMENT_IMAGE_REFERENCES", "9")
            ),
            maximum_segment_video_references=int(
                os.environ.get("ARK_MAX_SEGMENT_VIDEO_REFERENCES", "1")
            ),
            segment_reference_publishing_ready=segment_reference_publishing_ready,
        )
