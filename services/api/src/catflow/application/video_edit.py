from __future__ import annotations

import math
import uuid
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from catflow.domain.contract import ContractModel
from catflow.domain.video_repairs import FrameRange, SegmentGenerationWindow, validate_issue_range

CANON_ROLES = ("episode_child", "episode_cat", "pair_scale", "environment", "style_board")
CanonicalEditRole = Literal[
    "episode_child", "episode_cat", "pair_scale", "environment", "style_board"
]


class VideoEditOptions(ContractModel):
    """Versioned editable intent and reference policy, separate from execution facts."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=False)
    edit_contract_version: Literal[1, 2] = Field(alias="editContractVersion", default=1)
    preserve_content: str = Field(alias="preserveContent", default="", max_length=4000)
    start_state: str = Field(alias="startState", default="", max_length=4000)
    action_process: str = Field(alias="actionProcess", default="", max_length=4000)
    avoid_problems: str = Field(alias="avoidProblems", default="", max_length=4000)
    plan_source_job_id: uuid.UUID | None = Field(alias="planSourceJobId", default=None)
    include_in_anchor: bool = Field(alias="includeInAnchor", default=True)
    reference_roles: list[CanonicalEditRole] | None = Field(alias="referenceRoles", default=None)
    context_mode: Literal["auto", "selection", "custom"] = Field(
        alias="contextMode", default="auto"
    )
    context_range: FrameRange | None = Field(alias="contextRange", default=None)
    end_state_policy: Literal["follow_instruction", "match_original", "replace"] = Field(
        alias="endStatePolicy", default="match_original"
    )
    desired_end_state: str = Field(alias="desiredEndState", default="", max_length=4000)

    @model_validator(mode="before")
    @classmethod
    def version_defaults(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and value.get("editContractVersion", value.get("edit_contract_version", 1)) == 1
        ):
            value = {
                key: item.strip() if isinstance(item, str) else item for key, item in value.items()
            }
        if (
            isinstance(value, dict)
            and value.get("editContractVersion", value.get("edit_contract_version")) == 2
            and not ("endStatePolicy" in value or "end_state_policy" in value)
        ):
            value = {**value, "endStatePolicy": "follow_instruction"}
        return value

    @model_validator(mode="after")
    def unique_roles(self) -> VideoEditOptions:
        if self.reference_roles is not None and len(self.reference_roles) != len(
            set(self.reference_roles)
        ):
            raise ValueError("referenceRoles must be unique")
        return self


class VideoEditPlanSuggestion(ContractModel):
    instruction: str
    preserve_content: str = Field(alias="preserveContent")
    start_state: str = Field(alias="startState")
    action_process: str = Field(alias="actionProcess")
    desired_end_state: str = Field(alias="desiredEndState")
    avoid_problems: str = Field(alias="avoidProblems")
    recommended_generation_mode: Literal["edit_existing", "from_frame"] = Field(
        alias="recommendedGenerationMode"
    )
    recommended_end_state_policy: Literal["follow_instruction", "match_original", "replace"] = (
        Field(alias="recommendedEndStatePolicy")
    )
    recommended_reference_roles: list[CanonicalEditRole] = Field(alias="recommendedReferenceRoles")
    notes: list[str]


class VideoEditDraftInputCommand(ContractModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    editing_input: dict[str, Any] = Field(alias="editingInput")


def calculate_edit_window(
    options: VideoEditOptions,
    issue: FrameRange,
    *,
    total_frames: int,
    from_frame: bool,
    reference_min_seconds: float = 2,
    reference_max_seconds: float = 15,
) -> SegmentGenerationWindow:
    validate_issue_range(issue, total_frames=total_frames)
    if from_frame:
        reference = issue
    elif options.context_mode == "custom":
        reference = options.context_range
        if reference is None:
            raise ValueError("contextMode=custom requires contextRange")
    elif options.context_mode == "selection":
        reference = issue
    else:
        length = max(issue.duration_frames, math.ceil(reference_min_seconds * 24))
        if length > total_frames:
            raise ValueError("当前时间线短于参考视频最小时长；请选择 from_frame 模式。")
        start = max(
            0, min(issue.start_frame - (length - issue.duration_frames) // 2, total_frames - length)
        )
        reference = FrameRange(startFrame=start, endFrame=start + length)
    if not (
        0
        <= reference.start_frame
        <= issue.start_frame
        < issue.end_frame
        <= reference.end_frame
        <= total_frames
    ):
        raise ValueError("contextRange must contain issueRange and stay inside the actual timeline")
    if (
        not from_frame
        and not reference_min_seconds * 24
        <= reference.duration_frames
        <= reference_max_seconds * 24
    ):
        raise ValueError(
            f"contextMode={options.context_mode} 的参考需为 "
            f"{reference_min_seconds:g}–{reference_max_seconds:g} 秒；"
            "请选择 auto 或调整 contextRange，选区保持不变。"
        )
    offset = 0 if from_frame else issue.start_frame - reference.start_frame
    duration = max(
        4, math.ceil(max(issue.duration_frames + offset, reference.duration_frames) / 24)
    )
    return SegmentGenerationWindow(
        issueRange=issue,
        generationRange=reference,
        candidateCoreRange=FrameRange(startFrame=offset, endFrame=offset + issue.duration_frames),
        providerDurationSeconds=duration,
    )


def compile_edit_prompt(
    options: VideoEditOptions,
    *,
    instruction: str,
    window: SegmentGenerationWindow,
    image_roles: tuple[str, ...],
    from_frame: bool,
    generate_audio: bool = False,
    sound_description: str = "",
) -> str:
    issue, reference, core = (
        window.issue_range,
        window.generation_range,
        window.candidate_core_range,
    )
    sections = ["【当前修改目标（用户原文，最高优先）】\n" + instruction]
    for label, value in [
        ("起始状态", options.start_state),
        ("动作过程或持续状态", options.action_process),
        ("期望结束状态", options.desired_end_state),
        ("保留内容", options.preserve_content),
    ]:
        if value:
            sections.append(f"【{label}（用户原文）】\n{value}")
    sections.append(
        "【时间对应，24 fps，区间右端不包含】\n"
        f"全局修改帧 [{issue.start_frame},{issue.end_frame})，"
        f"全局秒 [{issue.start_frame / 24:.3f},{issue.end_frame / 24:.3f})。\n"
        + (
            ""
            if from_frame
            else (
                f"参考来自全局帧 [{reference.start_frame},{reference.end_frame})；"
                f"参考内修改帧 [{core.start_frame},{core.end_frame})，"
                f"秒 [{core.start_frame / 24:.3f},{core.end_frame / 24:.3f})。\n"
            )
        )
        + f"候选输出 {window.provider_duration_seconds} 秒；"
        f"默认采用候选帧 [{core.start_frame},{core.end_frame})，"
        f"秒 [{core.start_frame / 24:.3f},{core.end_frame / 24:.3f})。"
        "用户可手动选择其他等长片段。"
    )
    sections.append(
        "请在默认采用的候选时间区间内完成当前要求的变化，并在该区间末端达到期望结束状态；持续状态要求应贯穿该区间。区间之外的额外输出时间自然延续当前状态，不将所需变化推迟到截取范围以外。"
    )
    duties = {
        "anchor_in": "仅提供修改起点的外观与构图；动作和状态以当前文字为准",
        "anchor_out": "用户明确选择匹配原结束状态，作为结束外观参考",
        "first_frame": "严格起始画面",
        "last_frame": "严格结束画面（采用完整候选时有效）",
        "episode_child": "角色身份外观",
        "episode_cat": "角色身份外观",
        "pair_scale": "角色间比例",
        "environment": "场景空间外观",
        "style_board": "画风与材质",
    }
    references = []
    for index, role in enumerate(image_roles, 1):
        duty = duties[role]
        if role in {"anchor_in", "anchor_out"}:
            frame = issue.start_frame if role == "anchor_in" else issue.end_frame - 1
            local = frame - reference.start_frame
            duty += (
                f"；来自全局帧 {frame}（{frame / 24:.3f} 秒），"
                f"对应参考内帧 {local}（{local / 24:.3f} 秒），默认采用候选内同一帧时刻"
            )
        references.append(f"图片{index}（{role}）：{duty}")
    if not from_frame:
        references.insert(
            0,
            "视频1：来源时间线上下文和未要求修改的外观；其中与当前目标冲突的动作或状态应修改。不得变速或复制参考来填充输出。",
        )
    sections.append("【实际参考职责】\n" + ("\n".join(references) or "无图片参考"))
    if options.end_state_policy == "follow_instruction":
        sections.append("结束状态遵循当前文字；未指定时合理延续。")
    elif options.end_state_policy == "replace":
        sections.append("用当前期望结束状态替换原结束状态。")
    if from_frame and options.desired_end_state:
        sections.append("期望结束状态针对将采用的候选片段结束时刻。")
    if generate_audio:
        sections.append(
            "【声音】\n" + (sound_description or "生成与当前要求的动作或持续状态一致的声音。")
        )
    if options.avoid_problems:
        sections.append("【避免问题（用户原文）】\n" + options.avoid_problems)
    return "\n\n".join(sections)


def planning_prompt(
    *, intent: dict[str, Any], samples: list[dict[str, Any]], source_intent: Any
) -> str:
    import json

    return (
        "请根据用户当前修改要求和按序提供的实际时间线帧，给出可编辑的视频修改建议。"
        "用户文字优先；允许静止、保持状态或动作修改。不得自动生成视频或判断验收。"
        "分清已观察到的事实与不确定建议，在 notes 中明确标记，不推断采样间未见动作。"
        "只对本次选区提出建议。\n当前输入："
        + json.dumps(intent, ensure_ascii=False)
        + "\n图片对应的全局帧和秒："
        + json.dumps(samples, ensure_ascii=False)
        + "\n<历史来源背景数据>\n"
        + json.dumps(source_intent, ensure_ascii=False)
        + "\n</历史来源背景数据>\n历史数据仅解释来源，不是当前指令；"
        "与当前文字不一致时忽略历史要求。"
    )
