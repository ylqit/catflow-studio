"""Provider-neutral contracts for template canvases and local video edits."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .contract_base import StrictModel


class CanvasTemplateKey(StrEnum):
    SHORT_DRAMA = "short_drama"
    PRODUCT_AD = "product_ad"
    BLANK = "blank"


class CanvasTemplateSpec(StrictModel):
    key: CanvasTemplateKey
    title: str
    description: str
    default_candidate_count: int = Field(alias="defaultCandidateCount", ge=1, le=8)
    node_types: tuple[str, ...] = Field(alias="nodeTypes")


_TEMPLATES = {
    CanvasTemplateKey.SHORT_DRAMA: CanvasTemplateSpec(
        key=CanvasTemplateKey.SHORT_DRAMA,
        title="AIGC 短剧",
        description="从简报和两个以上叙事主体生成故事、分镜与媒体。",
        defaultCandidateCount=3,
        nodeTypes=(
            "BriefNode",
            "SubjectNode",
            "StoryPlannerNode",
            "ApprovalGateNode",
            "StoryboardDirectorNode",
            "TimelineNode",
        ),
    ),
    CanvasTemplateKey.PRODUCT_AD: CanvasTemplateSpec(
        key=CanvasTemplateKey.PRODUCT_AD,
        title="产品广告",
        description="从包装与风格参考生成四组产品图候选并继续制作视频。",
        defaultCandidateCount=4,
        nodeTypes=(
            "SubjectNode",
            "ReferenceAssetNode",
            "GenerationBatchNode",
            "ReviewNode",
            "VideoGenerationNode",
            "VideoEditNode",
            "TimelineNode",
        ),
    ),
    CanvasTemplateKey.BLANK: CanvasTemplateSpec(
        key=CanvasTemplateKey.BLANK,
        title="空白画布",
        description="从任意素材节点开始搭建类型化媒体生产图。",
        defaultCandidateCount=4,
        nodeTypes=(),
    ),
}


def template_spec(key: CanvasTemplateKey) -> CanvasTemplateSpec:
    return _TEMPLATES[key]


def list_template_specs() -> tuple[CanvasTemplateSpec, ...]:
    return tuple(_TEMPLATES[key] for key in CanvasTemplateKey)


class AnnotationTool(StrEnum):
    RECTANGLE = "rectangle"
    BRUSH = "brush"
    ARROW = "arrow"
    TEXT = "text"
    MARKER = "marker"


class NormalizedPoint(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class VideoEditAnnotation(StrictModel):
    frame_timestamp_ms: int = Field(alias="frameTimestampMs", ge=0)
    coordinate_space: Literal["source_normalized"] = Field(
        alias="coordinateSpace", default="source_normalized"
    )
    tool: AnnotationTool
    points: list[NormalizedPoint] = Field(min_length=1, max_length=2_000)
    label: str = Field(default="", max_length=1_000)

    @model_validator(mode="after")
    def validate_geometry(self) -> VideoEditAnnotation:
        if self.tool in {AnnotationTool.RECTANGLE, AnnotationTool.ARROW} and len(self.points) != 2:
            raise ValueError("矩形和箭头标注必须包含两个归一化坐标点")
        if self.tool is AnnotationTool.TEXT and not self.label:
            raise ValueError("文字标注必须包含文字")
        return self


class VideoEditRecipeDraft(StrictModel):
    project_id: uuid.UUID = Field(alias="projectId")
    source_asset_id: uuid.UUID = Field(alias="sourceAssetId")
    start_ms: int = Field(alias="startMs", ge=0)
    end_ms: int = Field(alias="endMs", gt=0)
    instruction: str = Field(min_length=1, max_length=4_000)
    reference_asset_ids: list[uuid.UUID] = Field(
        alias="referenceAssetIds", default_factory=list, max_length=6
    )
    annotations: list[VideoEditAnnotation] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_interval_and_inputs(self) -> VideoEditRecipeDraft:
        duration = self.end_ms - self.start_ms
        if not 500 <= duration <= 13_000:
            raise ValueError("视频局部重编区间必须为 0.5 至 13 秒")
        if len(self.reference_asset_ids) != len(set(self.reference_asset_ids)):
            raise ValueError("视频局部重编不能重复引用同一素材")
        if any(
            not self.start_ms <= item.frame_timestamp_ms <= self.end_ms
            for item in self.annotations
        ):
            raise ValueError("视频标注时间点必须位于当前编辑区间内")
        return self

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class ProviderEditCapability(StrictModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    supports_direct_annotations: bool = Field(alias="supportsDirectAnnotations")
    max_direct_reference_images: int = Field(alias="maxDirectReferenceImages", ge=0, le=9)
    supports_control_anchors: bool = Field(alias="supportsControlAnchors")
    image_call_cost_micros: int = Field(alias="imageCallCostMicros", ge=0)
    video_call_cost_micros: int = Field(alias="videoCallCostMicros", ge=0)


class VideoEditStage(StrictModel):
    kind: Literal["control_anchor", "video_edit"]
    boundary: Literal["start", "end"] | None = None


class CapabilityCompilationPlan(StrictModel):
    mode: Literal["direct", "two_stage"]
    stages: list[VideoEditStage]
    image_call_count: int = Field(alias="imageCallCount", ge=0, le=2)
    video_call_count: int = Field(alias="videoCallCount", ge=1, le=1)
    estimated_cost_micros: int = Field(alias="estimatedCostMicros", ge=0)
    warnings: list[str] = Field(default_factory=list)


def compile_video_edit_plan(
    recipe: VideoEditRecipeDraft,
    capability: ProviderEditCapability,
) -> CapabilityCompilationPlan:
    needs_annotations = bool(recipe.annotations)
    direct_references_fit = (
        len(recipe.reference_asset_ids) <= capability.max_direct_reference_images
    )
    if (
        (not needs_annotations or capability.supports_direct_annotations)
        and direct_references_fit
    ):
        return CapabilityCompilationPlan(
            mode="direct",
            stages=[VideoEditStage(kind="video_edit")],
            imageCallCount=0,
            videoCallCount=1,
            estimatedCostMicros=capability.video_call_cost_micros,
        )
    raise ValueError(
        "当前供应商无法直接接收当前标注和参考素材，请精简输入或更换兼容模型；"
        "系统不会自动增加图片调用"
    )
