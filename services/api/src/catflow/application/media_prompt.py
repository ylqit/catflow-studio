"""视频 / 图片生成 Prompt 编译工具 —— 把"导演 + 整片 + 逐镜 + 节拍"折叠成 Ark SDK 文本。

本文件只负责字符串拼接与占位符校验,不涉及具体 Ark 接口。

核心结构(compile_provider_media_prompt 输出的格式):
    【生成目标】 + prompt
    【参考职责】 + 图1职责 + 图2职责 + ... (按 reference_roles 顺序)
    【必须避免】 + negative_prompt

5 张固定参考图职责(REFERENCE_DUTIES):
    - episode_child    → 提供儿童身份与服装
    - episode_cat      → 提供猫咪身份、毛色、体型(允许眼睑/目光表演)
    - pair_scale       → 提供同一位儿童 + 同一只猫的同框比例
    - environment      → 提供环境外观与固定空间
    - style_board      → 仅线条/色彩/材质/光感,不复制物体或构图
特殊参考:
    - first_frame / last_frame:分段视频的首/末帧控制
    - anchor_in / anchor_out  :视频修复的入/出点衔接
    - previous_episode_frame :系列上一集的视觉连续性线索
"""

from __future__ import annotations

import re
from collections.abc import Mapping


class MediaPromptError(ValueError):
    """可操作的生成输入错误,在付费提交前抛出(不是数据库错误)。

    触发场景:
    - prompt 含未解析的系统占位符(${}, {{}}, <% %>)
    - prompt 为空字符串
    - referenceRoles 含未知角色(无法映射职责)
    - videoReference 值非法
    """


def validate_system_prompt(value: object, *, source: str) -> None:
    """递归校验嵌套对象中是否还有未解析的系统占位符。

    CatFlow 的 prompt 文本由两路来源合成:
    1. 导演 / 整片 / 局部编辑 阶段固化的英文/中文模板(已展开)
    2. 用户补充的"用户文字"(允许留空)

    本函数拒绝的占位符类型:
    - ${...} / {{...}} —— 模板字符串未渲染
    - <% ... %>        —— ERB / JSP 类模板未渲染

    Args:
        value: 要校验的对象(str / Mapping / list / tuple)
        source: 错误消息用,标识占位符所在字段路径(如 "story.body[2].text")
    """
    if isinstance(value, str):
        # 单行正则匹配三种占位符;None 表示全部解析干净
        match = re.search(r"\$\{[^{}]+\}|\{\{[^{}]+\}\}|<%[^%]+%>", value)
        if match:
            raise MediaPromptError(
                # 中文错误提示,直接给用户看,不需要翻译
                f"{source}: 未解析的系统占位符 {match.group(0)}，请修正此项后重新预览"
            )
    elif isinstance(value, Mapping):
        # 递归到子键,路径追加 ".key"
        for key, item in value.items():
            validate_system_prompt(item, source=f"{source}.{key}")
    elif isinstance(value, (list, tuple)):
        # 递归到列表元素,路径追加 "[index]"
        for index, item in enumerate(value):
            validate_system_prompt(item, source=f"{source}[{index}]")


# 8 类常规参考图的职责描述 —— 编译 prompt 时,按 reference_roles 顺序生成"图N: 职责"列表
# 注:5 张基础参考图(episode_child / cat / pair_scale / environment / style_board)
# 是固定槽位,后端不允许调整顺序或来源(style_source 永不进入)
_REFERENCE_DUTIES = {
    "episode_child": "提供儿童身份与服装，仅使用本片同一位儿童",
    "episode_cat": "提供猫咪身份、毛色与体型，仅使用本片同一只猫咪；不固定眼睑开合、目光方向或表情",
    "pair_scale": "提供上述同一位儿童与同一只猫咪的同框比例，不增加人物或动物",
    "environment": "提供环境外观与固定空间；可移动道具的数量、归属和起始状态以镜头设计为准",
    "style_board": "仅提供线条、色彩、材质与光感，不复制其中的物体或构图",
    "first_frame": "提供本次视频的严格起始画面，从这个状态开始执行动作，不锁定后续眼睑、目光与姿态",
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
    """把 director / storyboard 编译成 Ark SDK 完整 frozen 文本。

    输出格式(用 \\n\\n 分段):
        【生成目标】<prompt>
        【参考职责】图1:<duty1>\n图2:<duty2>\n...
        【必须避免】<negative_prompt>

    Args:
        prompt: 必填,导演/整片/逐镜的完整正文(已通过 validate_system_prompt)
        negative_prompt: 可选,本片必须避免的视觉/或叙事规则
        reference_roles: 本次提交实际使用的参考图角色元组(顺序敏感)
            - 5 张固定槽位 (episode_child/cat/pair_scale/environment/style_board) 不可调换
            - 特殊槽位 first_frame/last_frame/anchor_in/anchor_out/previous_episode_frame 可选
            - shot_scene_N / shot_frame_N —— 镜头级额外参考
        video_reference: 视频参考类型,可取 None / "previous_episode" / "segment"
            - "previous_episode":系列上一集成片
            - "segment":视频修复片段的入/出点参考
            - 其他值视为非法

    Returns:
        完整的 Ark SDK frozen 文本(供 jobs.frozen_input_json 持久化)

    Raises:
        MediaPromptError: prompt 为空、reference_role 未知、video_reference 非法
    """
    # 1. 校验 prompt 必填(空字符串视作未填写)
    if not prompt.strip():
        raise MediaPromptError("media generation prompt is required")
    references: list[str] = []
    # 2. 按 reference_roles 顺序,把每个角色映射成"图N: 职责。"
    for index, role in enumerate(reference_roles, 1):
        duty = _REFERENCE_DUTIES.get(role)
        if duty is None:
            # 兜底:支持镜头级额外参考 shot_scene_N / shot_frame_N
            match = re.fullmatch(r"shot_(scene|frame)_(\d+)", role)
            if match:
                # shot_scene_N → "场景外观与空间";shot_frame_N → "构图与动作起点普通参考"
                duty = (
                    f"提供镜头{match[2]}的场景外观与空间"
                    if match[1] == "scene"
                    else f"提供镜头{match[2]}的构图与动作起点普通参考，不是额外的严格首帧"
                )
            # previous_episode 系列的子角色(末帧 / keyframe_N / frame_N)统一映射
            elif re.fullmatch(r"previous_episode_(?:last_frame|keyframe_\d+|frame_\d+)", role):
                duty = _REFERENCE_DUTIES["previous_episode_frame"]
            else:
                # 真正的未知角色 → 报错,防止静默吃掉
                raise MediaPromptError(f"referenceRoles[{index - 1}]: 无法映射参考职责 {role!r}")
        references.append(f"图{index}：{duty}。")
    # 3. 视频参考(整片/逐镜用 None,series 上一集用 previous_episode,视频修复用 segment)
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
    # 4. 拼接 3 段(生成目标 / 参考职责 / 必须避免)
    parts = [f"【生成目标】\n{prompt.strip()}"]
    if references:
        parts.append("【参考职责】\n" + "\n".join(references))
    if negative_prompt.strip():
        parts.append(f"【必须避免】\n{negative_prompt.strip()}")
    return "\n\n".join(parts)