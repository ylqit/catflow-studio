from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from catflow.domain.contract import ContractModel
from catflow.domain.models import BlockingDesign, DirectorStoryTreatment, ShotSpec

VIDEO_PROMPT_COMPILER_REVISION = "seedance-professional-v4"

_BASE_VIDEO_EXCLUSIONS = (
    "真实摄影",
    "3D塑料质感",
    "叶片微距摄影污染",
    "儿童年龄、发型、脸型漂移",
    "猫咪毛色和虎斑分区漂移",
    "额外肢体",
    "融脸",
    "断尾",
    "错误四足",
    "文字",
    "Logo",
    "水印",
    "背景严重跳变",
    "原地互看",
    "静止停帧",
    "循环动作填充时长",
    "禁止8岁以上的修长儿童比例",
    "禁止青少年或成人脸型",
    "禁止过长四肢",
    "禁止身体比例超过约5头身",
    "禁止儿童身高与猫咪比例失真",
)
PromptSectionKey = Literal[
    "identity_style",
    "creative_treatment",
    "shot_execution",
    "ending_constraints",
]


class GenerationPromptSectionDto(ContractModel):
    key: PromptSectionKey
    title: str
    content: str


@dataclass(frozen=True)
class CompiledVideoGenerationPrompt:
    prompt: str
    negative_prompt: str
    prompt_summary: str
    prompt_sections: tuple[GenerationPromptSectionDto, ...]


_BOUNDARY_PUNCTUATION = " \t\r\n，、；。,.!?！？：:"


def _clean_fragment(value: str) -> str:
    text = value.strip()
    if text.endswith(("……", "...")):
        return text
    return text.rstrip(_BOUNDARY_PUNCTUATION).strip()


def _sentence(*clauses: str) -> str:
    content = "；".join(
        normalized for clause in clauses if (normalized := _clean_fragment(clause))
    )
    if not content or content.endswith(("……", "...")):
        return content
    return f"{content}。"


def _items(values: list[str]) -> str:
    normalized = [_clean_fragment(value) for value in values]
    return "、".join(value for value in normalized if value) or "无"


def _blocking_summary(blocking: BlockingDesign) -> str:
    return " → ".join(
        _clean_fragment(value)
        for value in (blocking.initial_state, blocking.movement_path, blocking.end_state)
    )


def synchronize_professional_shot_summaries(shot: ShotSpec) -> ShotSpec:
    """Make compatibility summaries reflect the authoritative structured shot fields."""

    updates: dict[str, str] = {}
    if shot.child_blocking is not None:
        updates["child_action"] = _blocking_summary(shot.child_blocking)
    if shot.cat_blocking is not None:
        updates["cat_action"] = _blocking_summary(shot.cat_blocking)
    if shot.physical_change is not None:
        updates["environment_change"] = (
            f"{_clean_fragment(shot.physical_change.subject)} · "
            f"{_clean_fragment(shot.physical_change.before)} → "
            f"{_clean_fragment(shot.physical_change.after)}"
        )
    return shot.model_copy(update=updates) if updates else shot


def _identity_style_section(project_title: str, target_duration_seconds: int) -> str:
    return _sentence(
        f"原创一人一猫生活短片《{_clean_fragment(project_title)}》，9:16，"
        f"{target_duration_seconds}秒",
        "固定同一位6至7岁儿童，身高约1.2米，齐下颌短发，保持圆润儿童脸型和"
        "约4.5至5头身的低龄儿童比例",
        "固定同一只灰白虎斑猫，保持毛色分区、眼睛、鼻口、环纹尾巴和正常四足结构",
        "二维柔和数字插画，暖灰细轮廓线，哑光材质，轻微纸感颗粒，柔和漫射暖光",
    )


def _creative_treatment_section(treatment: DirectorStoryTreatment | None) -> str:
    clauses: list[str] = []
    if treatment is not None:
        clauses.extend(
            (
                f"主题：{treatment.theme}",
                f"情绪气质：{_items(treatment.emotional_tone)}",
                f"视觉母题：{treatment.visual_motif}",
                f"空间：{treatment.spatial_setting}",
                f"声音方向：{treatment.sound_intent}",
            )
        )
    clauses.extend(
        (
            "整体基调不定义动作顺序",
            "逐镜执行是动作、节拍和最终状态的唯一权威",
        )
    )
    return _sentence(*clauses)


def _shot_execution(shot: ShotSpec) -> str:
    paragraphs = [
        _sentence(
            f"镜头设置：{shot.duration_seconds}秒，{shot.framing}",
            f"运镜：{shot.camera_movement}",
            f"转场：{shot.transition}",
        )
    ]
    if shot.lens is not None:
        paragraphs.append(
            _sentence(
                f"焦距与机位：{shot.lens.focal_length_equivalent}",
                f"机位高度：{shot.lens.camera_height}",
                f"角度：{shot.lens.camera_angle}",
                f"透视意图：{shot.lens.perspective_intent}",
            )
        )
    if shot.composition is not None:
        paragraphs.append(
            _sentence(
                f"构图主体：{shot.composition.subject_placement}",
                f"前景：{shot.composition.foreground}",
                f"中景：{shot.composition.middle_ground}",
                f"背景：{shot.composition.background}",
                f"运动方向：{shot.composition.screen_direction}",
                f"视线：{shot.composition.eye_line}",
            )
        )
    if shot.child_blocking is not None:
        paragraphs.append(
            _sentence(
                f"人物走位：{_blocking_summary(shot.child_blocking)}",
                f"人物微动作：{_items(shot.child_blocking.micro_motions)}",
            )
        )
    else:
        paragraphs.append(_sentence(f"人物动作：{shot.child_action}"))
    if shot.cat_blocking is not None:
        paragraphs.append(
            _sentence(
                f"猫咪走位：{_blocking_summary(shot.cat_blocking)}",
                f"猫咪微动作：{_items(shot.cat_blocking.micro_motions)}",
            )
        )
    else:
        paragraphs.append(_sentence(f"猫咪动作：{shot.cat_action}"))
    if shot.physical_change is not None:
        paragraphs.append(
            _sentence(
                f"物理变化：{shot.physical_change.subject}从"
                f"{_clean_fragment(shot.physical_change.before)} → "
                f"{_clean_fragment(shot.physical_change.after)}"
            )
        )
    else:
        paragraphs.append(_sentence(f"画面变化：{shot.environment_change}"))
    if shot.continuity is not None:
        paragraphs.append(
            _sentence(
                f"镜头承接：{shot.continuity.incoming}",
                f"离开状态：{shot.continuity.outgoing}",
                f"共享视觉元素：{shot.continuity.shared_visual_element}",
            )
        )
    if shot.lighting is not None:
        paragraphs.append(
            _sentence(
                f"光线方向：{shot.lighting.direction}",
                f"柔和度：{shot.lighting.softness}",
                f"色彩意图：{shot.lighting.color_intent}",
            )
        )
    if shot.sound is not None:
        sound_clauses = [
            f"环境声：{_items(shot.sound.ambience)}",
            f"物件声：{_items(shot.sound.object_effects)}",
            f"动作声：{_items(shot.sound.movement_effects)}",
            f"音乐：{shot.sound.music_intent}",
        ]
        if shot.sound.dialogue:
            sound_clauses.append(f"对白：{shot.sound.dialogue}")
        paragraphs.append(_sentence(*sound_clauses))
    if shot.director_intent:
        paragraphs.append(_sentence(f"导演意图：{shot.director_intent}"))
    return f"镜头 {shot.order}\n" + "\n".join(paragraph for paragraph in paragraphs if paragraph)


def _active_ending(shots: list[ShotSpec]) -> str:
    final_shot = shots[-1]
    if final_shot.continuity is not None and final_shot.continuity.final_frame.strip():
        return _clean_fragment(final_shot.continuity.final_frame)
    return _sentence(
        final_shot.child_action,
        final_shot.cat_action,
        final_shot.environment_change,
    ).removesuffix("。")


def _prompt_summary(project_title: str, shots: list[ShotSpec], active_ending: str) -> str:
    actions: list[str] = []
    changes: list[str] = []
    for shot in shots:
        child = (
            _clean_fragment(shot.child_blocking.movement_path)
            if shot.child_blocking is not None
            else _clean_fragment(shot.child_action)
        )
        cat = (
            _clean_fragment(shot.cat_blocking.movement_path)
            if shot.cat_blocking is not None
            else _clean_fragment(shot.cat_action)
        )
        actions.append(f"镜头{shot.order}：{child}，{cat}")
        if shot.physical_change is not None:
            changes.append(
                f"{_clean_fragment(shot.physical_change.subject)}从"
                f"{_clean_fragment(shot.physical_change.before)}变为"
                f"{_clean_fragment(shot.physical_change.after)}"
            )
        else:
            changes.append(_clean_fragment(shot.environment_change))
    return _sentence(
        f"《{project_title}》共{len(shots)}个镜头",
        f"主要动作：{'；'.join(actions)}",
        f"可见变化：{'；'.join(changes)}",
        f"最终画面：{active_ending}",
    )


def _negative_prompt(shots: list[ShotSpec]) -> str:
    items = list(_BASE_VIDEO_EXCLUSIONS)
    seen_meanings = {_clean_fragment(item).casefold() for item in items}
    for shot in shots:
        for risk in shot.generation_risks:
            message = _clean_fragment(risk.message)
            if not message or message.casefold() in seen_meanings:
                continue
            seen_meanings.add(message.casefold())
            items.append(f"镜头{shot.order}：{_clean_fragment(risk.code)}：{message}")
    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = _clean_fragment(item)
        if not normalized or normalized.casefold() in seen:
            continue
        seen.add(normalized.casefold())
        unique.append(normalized)
    return "，".join(unique)


def compile_video_generation_prompt(
    *,
    project_title: str,
    target_duration_seconds: int,
    shots: list[ShotSpec],
    director_treatment: DirectorStoryTreatment | None,
    continuity_constraints: tuple[str, ...] = (),
) -> CompiledVideoGenerationPrompt:
    if not shots:
        raise ValueError("video generation requires at least one shot")
    active_ending = _active_ending(shots)
    sections = (
        GenerationPromptSectionDto(
            key="identity_style",
            title="角色与画风",
            content=_identity_style_section(project_title, target_duration_seconds),
        ),
        GenerationPromptSectionDto(
            key="creative_treatment",
            title="整体基调",
            content=_creative_treatment_section(director_treatment),
        ),
        GenerationPromptSectionDto(
            key="shot_execution",
            title="逐镜执行",
            content="\n\n".join(_shot_execution(shot) for shot in shots),
        ),
        GenerationPromptSectionDto(
            key="ending_constraints",
            title="结尾与生成限制",
            content="\n".join(
                [
                    _sentence(f"主动结尾：{active_ending}"),
                    _sentence(
                        "结尾必须完成逐镜指定的最后动作并清楚呈现最终状态",
                        "不得擅自追加下一项任务",
                        "不得让儿童和猫咪原地互看",
                        "不得使用完全静止、重复呼吸、无意义慢镜头或停帧填充剩余时长",
                    ),
                    _sentence(
                        "无文字、无Logo、无水印",
                        "不复制任何画风来源中的叶片、露珠或摄影构图",
                    ),
                    *(_sentence(constraint) for constraint in continuity_constraints),
                ]
            ),
        ),
    )
    prompt = "\n\n".join(
        f"【{section.title}】\n{section.content}" for section in sections
    )
    return CompiledVideoGenerationPrompt(
        prompt=prompt,
        negative_prompt=_negative_prompt(shots),
        prompt_summary=_prompt_summary(project_title, shots, active_ending),
        prompt_sections=sections,
    )


def compile_provider_video_prompt(*, prompt: str, negative_prompt: str) -> str:
    """Build the single text instruction accepted by the current Ark video SDK."""

    target = prompt.strip()
    exclusions = negative_prompt.strip()
    if not target:
        raise ValueError("video generation prompt is required")
    if not exclusions:
        raise ValueError("video generation negative prompt is required")
    return f"【生成目标】\n{target}\n\n【必须避免】\n{exclusions}"
