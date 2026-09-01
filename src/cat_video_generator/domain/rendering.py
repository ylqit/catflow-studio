"""单镜头视频输入与项目级非破坏性时间轴。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contract_base import StrictModel


class RenderOperation(StrEnum):
    SHOT = "shot"
    EDIT = "edit"


class AudioPolicy(StrEnum):
    NATIVE_REQUIRED = "native_required"
    NONE = "none"


class MediaModality(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class ProviderMediaRole(StrEnum):
    FIRST_FRAME = "first_frame"
    LAST_FRAME = "last_frame"
    REFERENCE_VIDEO = "reference_video"
    REFERENCE_IMAGE = "reference_image"


class SequenceStatus(StrEnum):
    CONTENT_REVIEW = "content_review"
    APPROVED = "approved"
    REJECTED = "rejected"


class SequenceTransitionType(StrEnum):
    CUT = "cut"
    FADE_BLACK = "fade_black"
    CROSS_DISSOLVE = "cross_dissolve"


class SequenceTransition(StrictModel):
    type: SequenceTransitionType = SequenceTransitionType.CUT
    duration_ms: Annotated[int, Field(default=0, alias="durationMs", ge=0, le=1_000)]

    @model_validator(mode="after")
    def validate_duration(self) -> SequenceTransition:
        if self.type is SequenceTransitionType.CUT and self.duration_ms != 0:
            raise ValueError("cut transition duration must be zero")
        if self.type is not SequenceTransitionType.CUT and self.duration_ms < 150:
            raise ValueError("fade transitions must be between 150 and 1000 milliseconds")
        return self


class MediaBinding(StrictModel):
    asset_id: UUID
    semantic_key: Annotated[str, Field(min_length=3, max_length=160)]
    modality: MediaModality
    provider_role: ProviderMediaRole
    ordinal: Annotated[int, Field(ge=1, le=9)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @property
    def prompt_alias(self) -> str:
        prefix = "@图片" if self.modality is MediaModality.IMAGE else "@视频"
        return f"{prefix}{self.ordinal}"


class VideoInputPlan(StrictModel):
    operation: RenderOperation
    resolution: Literal["480p", "720p"]
    duration_seconds: Annotated[int, Field(ge=4, le=15)]
    audio_policy: AudioPolicy = Field(
        alias="audioPolicy",
        default=AudioPolicy.NATIVE_REQUIRED,
    )
    bindings: list[MediaBinding] = Field(default_factory=list, max_length=9)

    @model_validator(mode="after")
    def validate_bindings(self) -> VideoInputPlan:
        for modality in MediaModality:
            ordinals = [item.ordinal for item in self.bindings if item.modality is modality]
            if ordinals and ordinals != list(range(1, len(ordinals) + 1)):
                raise ValueError(f"{modality.value}素材序号必须从1连续递增")
        if len({item.asset_id for item in self.bindings}) != len(self.bindings):
            raise ValueError("同一资产不能重复进入一个视频任务")
        if self.operation is RenderOperation.SHOT:
            first_frames = [
                item
                for item in self.bindings
                if item.provider_role is ProviderMediaRole.FIRST_FRAME
            ]
            last_frames = [
                item
                for item in self.bindings
                if item.provider_role is ProviderMediaRole.LAST_FRAME
            ]
            if len(first_frames) > 1 or len(last_frames) > 1:
                raise ValueError("一次镜头生成最多使用一张first_frame")
            if first_frames and self.bindings[0] is not first_frames[0]:
                raise ValueError("first_frame必须是第一项素材")
            if last_frames and not first_frames:
                raise ValueError("last_frame必须与first_frame一起使用")
            controlled_roles = [
                item.provider_role
                for item in self.bindings
                if item.provider_role in {
                    ProviderMediaRole.FIRST_FRAME,
                    ProviderMediaRole.LAST_FRAME,
                }
            ]
            if controlled_roles not in (
                [],
                [ProviderMediaRole.FIRST_FRAME],
                [ProviderMediaRole.FIRST_FRAME, ProviderMediaRole.LAST_FRAME],
            ):
                raise ValueError("首尾帧必须按first_frame、last_frame顺序提交")
            if first_frames and len(self.bindings) != len(controlled_roles):
                raise ValueError(
                    "Seedance首帧或首尾帧模式不能同时提交普通参考图片或参考视频"
                )
            if any(item.modality is MediaModality.VIDEO for item in self.bindings):
                raise ValueError("初始镜头生成不接收前序完整视频")
        else:
            roles = [item.provider_role for item in self.bindings]
            if (
                len(roles) < 3
                or roles[0] is not ProviderMediaRole.REFERENCE_VIDEO
                or any(role is not ProviderMediaRole.REFERENCE_IMAGE for role in roles[1:])
            ):
                raise ValueError(
                    "区间编辑必须先绑定@视频1和两张真实边界帧，随后才能附加参考图"
                )
        return self


class SequenceClip(StrictModel):
    order: Annotated[int, Field(ge=1)]
    shot_card_id: UUID
    source_asset_id: UUID
    source_start_ms: Annotated[int, Field(ge=0)]
    source_end_ms: Annotated[int, Field(gt=0)]
    timeline_start_ms: Annotated[int, Field(ge=0)]
    timeline_end_ms: Annotated[int, Field(gt=0)]
    transition_from_previous: SequenceTransition | None = Field(
        default=None,
        alias="transitionFromPrevious",
    )

    @model_validator(mode="after")
    def validate_interval(self) -> SequenceClip:
        if self.source_end_ms <= self.source_start_ms:
            raise ValueError("来源区间必须为正时长")
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("时间轴区间必须为正时长")
        if (self.source_end_ms - self.source_start_ms) != (
            self.timeline_end_ms - self.timeline_start_ms
        ):
            raise ValueError("来源区间与时间轴区间时长必须一致")
        return self


class ProjectSequencePlan(StrictModel):
    duration_ms: Annotated[int, Field(gt=0)]
    clips: list[SequenceClip] = Field(min_length=1)
    intro_transition: SequenceTransition | None = Field(default=None, alias="introTransition")
    outro_transition: SequenceTransition | None = Field(default=None, alias="outroTransition")

    @model_validator(mode="after")
    def validate_timeline(self) -> ProjectSequencePlan:
        for label, transition in (
            ("开场", self.intro_transition),
            ("结尾", self.outro_transition),
        ):
            if transition is not None and transition.type is SequenceTransitionType.CROSS_DISSOLVE:
                raise ValueError(f"{label}边界不支持叠化，只能使用淡黑或直接切换")
        if [clip.order for clip in self.clips] != list(range(1, len(self.clips) + 1)):
            raise ValueError("时间轴片段order必须连续")
        previous: SequenceClip | None = None
        for clip in self.clips:
            if previous is None:
                if clip.timeline_start_ms != 0 or clip.transition_from_previous is not None:
                    raise ValueError("首个片段必须从零开始且不能声明前置转场")
                previous = clip
                continue
            transition = clip.transition_from_previous or SequenceTransition()
            overlap = (
                transition.duration_ms
                if transition.type is SequenceTransitionType.CROSS_DISSOLVE
                else 0
            )
            if overlap >= min(
                previous.timeline_end_ms - previous.timeline_start_ms,
                clip.timeline_end_ms - clip.timeline_start_ms,
            ):
                raise ValueError("叠化时长必须小于相邻两个片段的时长")
            if clip.timeline_start_ms != previous.timeline_end_ms - overlap:
                raise ValueError("时间轴起点与所选转场不一致")
            previous = clip
        if self.clips[-1].timeline_end_ms != self.duration_ms:
            raise ValueError("时间轴末尾必须等于总时长")
        return self


@dataclass(frozen=True, slots=True)
class MediaSource:
    asset_id: UUID
    semantic_key: str
    media_type: str
    sha256: str
    metadata: dict[str, Any]


def build_shot_input_plan(
    *,
    resolution: str,
    duration_seconds: int,
    anchor: MediaSource | None,
    last_frame: MediaSource | None = None,
    references: tuple[MediaSource, ...] = (),
) -> VideoInputPlan:
    if resolution not in {"480p", "720p"}:
        raise ValueError(f"不支持的视频分辨率{resolution}")
    # Seedance exposes first-frame generation and reference-media generation as
    # mutually exclusive request modes.  Identity, wardrobe, environment and
    # style references must be resolved while producing the approved anchor;
    # once it is used as FIRST_FRAME, it is the only provider media input.
    if last_frame is not None and anchor is None:
        raise ValueError("尾帧控制必须同时提供首帧")
    if anchor is not None and references:
        raise ValueError("Seedance首帧模式不能同时提交普通参考图片")
    if len(references) > 9:
        raise ValueError("当前模型输入档案最多允许9项参考素材")
    sources = (
        (() if anchor is None else (anchor,))
        + (() if last_frame is None else (last_frame,))
        + references
    )
    if any(source.media_type != "image" for source in sources):
        raise ValueError("镜头生成的锚点与参考素材必须是图片")
    bindings: list[MediaBinding] = []
    for index, source in enumerate(sources, 1):
        bindings.append(
            MediaBinding(
                asset_id=source.asset_id,
                semantic_key=source.semantic_key,
                modality=MediaModality.IMAGE,
                provider_role=(
                    ProviderMediaRole.FIRST_FRAME
                    if anchor is not None and index == 1
                    else ProviderMediaRole.LAST_FRAME
                    if last_frame is not None and index == 2
                    else ProviderMediaRole.REFERENCE_IMAGE
                ),
                ordinal=index,
                sha256=source.sha256,
            )
        )
    return VideoInputPlan(
        operation=RenderOperation.SHOT,
        resolution=resolution,
        duration_seconds=duration_seconds,
        bindings=bindings,
    )


def build_edit_input_plan(
    *,
    resolution: str,
    duration_seconds: int,
    source_video: MediaSource,
    before_frame: MediaSource,
    after_frame: MediaSource,
    references: tuple[MediaSource, ...] = (),
) -> VideoInputPlan:
    sources = (source_video, before_frame, after_frame, *references)
    if [item.media_type for item in sources[:3]] != ["video", "image", "image"]:
        raise ValueError("区间编辑需要一个视频和两张边界图")
    if any(item.media_type != "image" for item in references):
        raise ValueError("视频局部编辑的额外参考素材必须是图片")
    if len(sources) > 9:
        raise ValueError("视频局部编辑最多允许六张额外参考图")
    roles = (ProviderMediaRole.REFERENCE_VIDEO,) + (
        (ProviderMediaRole.REFERENCE_IMAGE,) * (len(sources) - 1)
    )
    modalities = (MediaModality.VIDEO,) + ((MediaModality.IMAGE,) * (len(sources) - 1))
    ordinals = (1, *range(1, len(sources)))
    return VideoInputPlan(
        operation=RenderOperation.EDIT,
        resolution=resolution,
        duration_seconds=duration_seconds,
        bindings=[
            MediaBinding(
                asset_id=source.asset_id,
                semantic_key=source.semantic_key,
                modality=modality,
                provider_role=role,
                ordinal=ordinal,
                sha256=source.sha256,
            )
            for source, modality, role, ordinal in zip(
                sources,
                modalities,
                roles,
                ordinals,
                strict=True,
            )
        ],
    )
