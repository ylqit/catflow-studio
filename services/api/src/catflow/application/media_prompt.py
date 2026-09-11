from __future__ import annotations

import re
from collections.abc import Mapping


class MediaPromptError(ValueError):
    """An actionable generation-input error, surfaced before any paid submission."""


def validate_system_prompt(value: object, *, source: str) -> None:
    """Reject unresolved templates at their source; never silently rewrite user prose."""
    if isinstance(value, str):
        match = re.search(r"\$\{[^{}]+\}|\{\{[^{}]+\}\}|<%[^%]+%>", value)
        if match:
            raise MediaPromptError(
                f"{source}: 未解析的系统占位符 {match.group(0)}，请修正此项后重新预览"
            )
    elif isinstance(value, Mapping):
        for key, item in value.items():
            validate_system_prompt(item, source=f"{source}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_system_prompt(item, source=f"{source}[{index}]")


_REFERENCE_DUTIES = {
    "episode_child": "提供儿童身份与服装，仅使用本片同一位儿童",
    "episode_cat": "提供猫咪身份、毛色与体型，仅使用本片同一只猫咪",
    "pair_scale": "提供上述同一位儿童与同一只猫咪的同框比例，不增加人物或动物",
    "environment": "提供环境外观与固定空间；可移动道具的数量、归属和起始状态以镜头设计为准",
    "style_board": "仅提供线条、色彩、材质与光感，不复制其中的物体或构图",
    "first_frame": "提供本次视频的严格起始画面，从这个状态开始执行动作",
    "last_frame": "提供已确认的目标结束画面，动作自然到达这个状态",
    "anchor_in": "提供修改片段的入点衔接状态",
    "anchor_out": "提供修改片段的出点衔接状态",
    "previous_episode_frame": "提供上一集视觉连续性线索，仅继承本集已确认保留的状态",
}


def compile_provider_media_prompt(
    *,
    prompt: str,
    negative_prompt: str,
    reference_roles: tuple[str, ...],
    video_reference: str | None = None,
) -> str:
    """Compile the complete frozen SDK text using the actual ordered submission list."""
    if not prompt.strip():
        raise MediaPromptError("media generation prompt is required")
    references = []
    for index, role in enumerate(reference_roles, 1):
        duty = _REFERENCE_DUTIES.get(role)
        if duty is None:
            match = re.fullmatch(r"shot_(scene|frame)_(\d+)", role)
            if match:
                duty = (
                    f"提供镜头{match[2]}的场景外观与空间"
                    if match[1] == "scene"
                    else f"提供镜头{match[2]}的构图与动作起点普通参考，不是额外的严格首帧"
                )
            elif re.fullmatch(r"previous_episode_(?:last_frame|keyframe_\d+|frame_\d+)", role):
                duty = _REFERENCE_DUTIES["previous_episode_frame"]
            else:
                raise MediaPromptError(f"referenceRoles[{index - 1}]: 无法映射参考职责 {role!r}")
        references.append(f"图{index}：{duty}。")
    if video_reference == "previous_episode":
        references.append(
            "视频1：提供上一集连续性线索，仅继承本集明确保留的状态；调整或重置项服从本集设计。"
        )
    elif video_reference == "segment":
        references.append(
            "视频1：提供机位、构图、光线和未指定修改的内容；不照搬需要修正的动作和道具状态。"
        )
    elif video_reference is not None:
        raise MediaPromptError(f"videoReference: 无法映射参考职责 {video_reference!r}")
    parts = [f"【生成目标】\n{prompt.strip()}"]
    if references:
        parts.append("【参考职责】\n" + "\n".join(references))
    if negative_prompt.strip():
        parts.append(f"【必须避免】\n{negative_prompt.strip()}")
    return "\n\n".join(parts)
