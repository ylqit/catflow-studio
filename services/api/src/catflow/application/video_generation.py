"""视频生成的 Prompt 编译 —— 把"导演 + 整片 + 逐镜"折叠成 Seedance 视频 prompt。

文件职责:
1. 定义视频 Prompt 模板常量(VIDEO_PROMPT_COMPILER_REVISION / _BASE_VIDEO_EXCLUSIONS)
2. 提供 generate_director_treatment_prompt / compile_whole_video_prompt 等函数
3. 与 creative_direction / media_prompt 协作,产出最终 Ark SDK frozen 文本

调用方:
- application/service.py::preview_video_generation —— 整片 / 逐镜 preview
- application/service.py::create_video_job —— 入队视频任务
- application/service.py::create_shot_plan_generation_job —— 分镜生成
- worker/ark_gateway.py —— 消费 frozen_input_json,提交 Ark
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from catflow.domain.contract import ContractModel
from catflow.domain.models import BlockingDesign, DirectorStoryTreatment, ShotSpec

from .creative_direction import CAT_PERFORMANCE_DIRECTION, ENDING_DIRECTION
from .media_prompt import validate_system_prompt

VIDEO_PROMPT_COMPILER_REVISION = "seedance-professional-v9-performance"

# 视频生成通用禁用规则(任何视频都禁止,不分集数)
# 与分镜级别的镜 1 / 镜 2 禁用叠加
_BASE_VIDEO_EXCLUSIONS = (
    "真实摄影",
    "3D塑料质感",
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
    "长时间无回应地对视填充时长",
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


def compile_prompt_sentence(*clauses: str) -> str:
    """Serialize field boundaries once, shared by whole-video and segment-edit compilers."""
    content = "；".join(normalized for clause in clauses if (normalized := _clean_fragment(clause)))
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

    updates: dict[str, object] = {}
    if shot.action_beats:
        for actor in ("child", "cat"):
            actions = (
                "；".join(
                    action
                    for beat in shot.action_beats
                    if (action := getattr(beat, f"{actor}_action"))
                )
                or "本镜头无可见动作"
            )
            summary = actions if len(actions) <= 500 else actions[:499] + "…"
            updates[f"{actor}_action"] = summary
            blocking = getattr(shot, f"{actor}_blocking")
            if blocking is not None:
                updates[f"{actor}_blocking"] = blocking.model_copy(
                    update={"movement_path": summary, "micro_motions": []}
                )
        changes = "；".join(beat.visible_change for beat in shot.action_beats)
        updates["environment_change"] = changes if len(changes) <= 500 else changes[:499] + "…"
        return shot.model_copy(update=updates)
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


def _identity_style_section(
    project_title: str, target_duration_seconds: int, cat_identity: str
) -> str:
    return compile_prompt_sentence(
        f"原创一人一猫生活短片《{_clean_fragment(project_title)}》，9:16，"
        f"{target_duration_seconds}秒",
        "固定同一位6至7岁儿童，身高约1.2米，齐下颌短发，保持圆润儿童脸型和"
        "约4.5至5头身的低龄儿童比例",
        cat_identity,
        CAT_PERFORMANCE_DIRECTION,
        "二维柔和数字插画，暖灰细轮廓线，哑光材质，轻微纸感颗粒，柔和漫射暖光",
    )


def _creative_treatment_section(treatment: DirectorStoryTreatment | None) -> str:
    clauses: list[str] = []
    if treatment is not None:
        validate_system_prompt(
            treatment.model_dump(
                by_alias=True,
                include={
                    "theme",
                    "emotional_tone",
                    "visual_motif",
                    "spatial_setting",
                },
            ),
            source="directorTreatment",
        )
        clauses.extend(
            (
                f"主题：{treatment.theme}",
                f"情绪气质：{_items(treatment.emotional_tone)}",
                f"视觉母题：{treatment.visual_motif}",
                f"空间：{treatment.spatial_setting}",
            )
        )
    clauses.extend(
        (
            "整体基调不定义动作顺序",
            "逐镜执行是动作、节拍和最终状态的唯一权威",
        )
    )
    return compile_prompt_sentence(*clauses)


def _shot_execution(
    shot: ShotSpec,
    *,
    start_seconds: int = 0,
    initial_frame: bool = False,
    previous_shot: ShotSpec | None = None,
) -> str:
    transition = {"continuous": "连续衔接", "soft_cut": "柔和切换", "hard_cut": "直接切换"}
    paragraphs = [
        compile_prompt_sentence(
            f"景别：{shot.framing}",
            "" if initial_frame else f"运镜：{shot.camera_movement}",
            "" if initial_frame else f"转场：{transition[shot.transition]}",
        )
    ]
    if shot.action_beats and not initial_frame:
        purposes = {"trigger": "触发", "action": "行动", "reaction": "反应", "payoff": "回报"}
        eyelids = {
            "none": "",
            "blink": "自然闭合眼睑后重新睁开，过程清楚可见",
            "slow_blink": "缓慢闭合眼睑，短暂停留，再自然睁开",
            "squint_release": "因当前刺激短暂半眯眼，随后恢复正常睁眼",
        }
        visibility = {
            "visible": "脸部清楚可读",
            "partial": "脸部部分可见",
            "hidden": "脸部不可见，不要求可见眼部表演",
        }
        for beat in shot.action_beats:
            start = f"{start_seconds + beat.start_frame / 24:.3f}".rstrip("0").rstrip(".")
            end = f"{start_seconds + beat.end_frame / 24:.3f}".rstrip("0").rstrip(".")
            paragraphs.append(
                compile_prompt_sentence(
                    f"节拍 {start}–{end}秒 · {purposes[beat.purpose]}",
                    f"儿童：{beat.child_action}" if beat.child_action else "",
                    f"猫咪：{beat.cat_action}" if beat.cat_action else "",
                    f"可见变化：{beat.visible_change}",
                )
            )
            performance = beat.cat_performance
            if performance is not None:
                paragraphs.append(
                    compile_prompt_sentence(
                        f"本节拍猫咪表演：{visibility[performance.visibility]}",
                        f"目光从{performance.gaze_from}转向{performance.gaze_to}"
                        if performance.visibility != "hidden"
                        and performance.gaze_from
                        and performance.gaze_to
                        else "",
                        eyelids[performance.eyelid_action]
                        if performance.visibility != "hidden"
                        else "",
                    )
                )
    if shot.lens is not None:
        paragraphs.append(
            compile_prompt_sentence(
                f"焦距与机位：{shot.lens.focal_length_equivalent}",
                f"机位高度：{shot.lens.camera_height}",
                f"角度：{shot.lens.camera_angle}",
                f"透视意图：{shot.lens.perspective_intent}",
            )
        )
    paragraphs.append(
        compile_prompt_sentence(
            "环境使用："
            + (
                "沿用参考布局"
                if shot.environment_use == "preserve_layout"
                else "保持场景外观与空间关系，允许镜头重新构图"
            )
        )
    )
    if shot.camera_spatial_relation:
        paragraphs.append(
            compile_prompt_sentence(f"机位与空间关系：{shot.camera_spatial_relation}")
        )
    if shot.interaction_constraints and not initial_frame:
        paragraphs.append(
            compile_prompt_sentence("交互约束：" + _items(shot.interaction_constraints))
        )
    if shot.composition is not None:
        paragraphs.append(
            compile_prompt_sentence(
                f"构图主体：{shot.composition.subject_placement}",
                f"前景：{shot.composition.foreground}",
                f"中景：{shot.composition.middle_ground}",
                f"背景：{shot.composition.background}",
                "" if initial_frame else f"画面运动方向：{shot.composition.screen_direction}",
                f"视线：{shot.composition.eye_line}" if not shot.action_beats else "",
            )
        )
    if shot.child_blocking is not None:
        paragraphs.append(
            compile_prompt_sentence(
                f"人物起始状态：{shot.child_blocking.initial_state}"
                if initial_frame
                else (
                    f"人物空间起止：{shot.child_blocking.initial_state}"
                    f" → {shot.child_blocking.end_state}"
                    if shot.action_beats
                    else f"人物走位：{_blocking_summary(shot.child_blocking)}"
                ),
                ""
                if initial_frame or shot.action_beats or not shot.child_blocking.micro_motions
                else f"人物微动作：{_items(shot.child_blocking.micro_motions)}",
            )
        )
    elif not initial_frame and not shot.action_beats:
        paragraphs.append(compile_prompt_sentence(f"人物动作：{shot.child_action}"))
    if shot.cat_blocking is not None:
        paragraphs.append(
            compile_prompt_sentence(
                f"猫咪起始状态：{shot.cat_blocking.initial_state}"
                if initial_frame
                else (
                    f"猫咪空间起止：{shot.cat_blocking.initial_state}"
                    f" → {shot.cat_blocking.end_state}"
                    if shot.action_beats
                    else f"猫咪走位：{_blocking_summary(shot.cat_blocking)}"
                ),
                ""
                if initial_frame or shot.action_beats or not shot.cat_blocking.micro_motions
                else f"猫咪微动作：{_items(shot.cat_blocking.micro_motions)}",
            )
        )
    elif not initial_frame and not shot.action_beats:
        paragraphs.append(compile_prompt_sentence(f"猫咪动作：{shot.cat_action}"))
    if shot.physical_change is not None and (initial_frame or not shot.action_beats):
        paragraphs.append(
            compile_prompt_sentence(
                f"物体起始状态：{shot.physical_change.subject}，{shot.physical_change.before}"
                if initial_frame
                else f"物理变化：{shot.physical_change.subject}从"
                f"{_clean_fragment(shot.physical_change.before)} → "
                f"{_clean_fragment(shot.physical_change.after)}"
            )
        )
    elif not initial_frame and not shot.action_beats:
        paragraphs.append(compile_prompt_sentence(f"画面变化：{shot.environment_change}"))
    if shot.continuity is not None:
        paragraphs.append(
            compile_prompt_sentence(
                f"镜头承接：{shot.continuity.incoming}",
                "" if initial_frame else f"离开状态：{shot.continuity.outgoing}",
                f"共享视觉元素：{shot.continuity.shared_visual_element}",
            )
        )
    if (
        previous_shot is not None
        and shot.lighting is not None
        and shot.lighting == previous_shot.lighting
    ):
        paragraphs.append(f"光线：沿用镜头{previous_shot.order}的方向、柔和度与色彩。")
    elif shot.lighting is not None:
        paragraphs.append(
            compile_prompt_sentence(
                f"光线方向：{shot.lighting.direction}",
                f"柔和度：{shot.lighting.softness}",
                f"色彩意图：{shot.lighting.color_intent}",
            )
        )
    if shot.sound is not None and not initial_frame:
        sound_clauses = [
            f"环境声：{_items(shot.sound.ambience)}" if shot.sound.ambience else "",
            f"物件声：{_items(shot.sound.object_effects)}" if shot.sound.object_effects else "",
            f"动作声：{_items(shot.sound.movement_effects)}" if shot.sound.movement_effects else "",
            f"音乐：{shot.sound.music_intent}",
        ]
        if shot.sound.dialogue:
            sound_clauses.append(f"对白：{shot.sound.dialogue}")
        paragraphs.append(compile_prompt_sentence(*sound_clauses))
    elif not initial_frame:
        paragraphs.append("声音：生成与可见动作同步的自然环境声、物件声和动作声。")
    title = (
        f"镜头 {shot.order} 起始画面"
        if initial_frame
        else (f"镜头 {shot.order}（{start_seconds}–{start_seconds + shot.duration_seconds}秒）")
    )
    return title + "\n" + "\n".join(paragraph for paragraph in paragraphs if paragraph)


def _active_ending(shots: list[ShotSpec]) -> str:
    final_shot = shots[-1]
    if final_shot.continuity is not None and final_shot.continuity.final_frame.strip():
        return _clean_fragment(final_shot.continuity.final_frame)
    return compile_prompt_sentence(
        final_shot.child_action,
        final_shot.cat_action,
        final_shot.environment_change,
    ).removesuffix("。")


def _prompt_summary(project_title: str, shots: list[ShotSpec], active_ending: str) -> str:
    actions: list[str] = []
    changes: list[str] = []
    for shot in shots:
        if shot.action_beats:
            actions.append(
                f"镜头{shot.order}："
                + "；".join(
                    "，".join(action for action in (beat.child_action, beat.cat_action) if action)
                    for beat in shot.action_beats
                )
            )
            changes.extend(beat.visible_change for beat in shot.action_beats)
            continue
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
    return compile_prompt_sentence(
        f"《{project_title}》共{len(shots)}个镜头",
        f"主要动作：{'；'.join(actions)}",
        f"可见变化：{'；'.join(changes)}",
        f"最终画面：{active_ending}",
    )


def _negative_prompt(shots: list[ShotSpec], *, initial_frame: bool = False) -> str:
    items = [
        item
        for item in _BASE_VIDEO_EXCLUSIONS
        if not initial_frame
        or item
        not in {
            "背景严重跳变",
            "长时间无回应地对视填充时长",
            "静止停帧",
            "循环动作填充时长",
        }
    ]
    seen_meanings = {_clean_fragment(item).casefold() for item in items}
    for shot in shots:
        for exclusion in shot.visual_exclusions:
            message = _clean_fragment(exclusion)
            if not message or message.casefold() in seen_meanings:
                continue
            seen_meanings.add(message.casefold())
            items.append(f"镜头{shot.order}：{message}")
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
    cat_identity: str = "固定同一只灰白虎斑猫，保持毛色分区、眼睛、鼻口、环纹尾巴和正常四足结构",
) -> CompiledVideoGenerationPrompt:
    if not shots:
        raise ValueError("video generation requires at least one shot")
    for shot in shots:
        validate_system_prompt(
            shot.model_dump(
                by_alias=True,
                exclude={
                    "generation_risks",
                    "director_intent",
                    "confirmed_frame",
                },
            ),
            source=f"shots[{shot.order - 1}]",
        )
    validate_system_prompt(continuity_constraints, source="continuity")
    validate_system_prompt(cat_identity, source="catIdentity")
    executions = []
    if continuity_constraints:
        executions.append(
            "本集进入状态：\n"
            + "\n".join(
                compile_prompt_sentence(constraint) for constraint in continuity_constraints
            )
        )
    elapsed = 0
    previous_shot = None
    for shot in shots:
        executions.append(_shot_execution(shot, start_seconds=elapsed, previous_shot=previous_shot))
        elapsed += shot.duration_seconds
        previous_shot = shot
    active_ending = _active_ending(shots)
    sections = (
        GenerationPromptSectionDto(
            key="identity_style",
            title="角色与画风",
            content=_identity_style_section(project_title, target_duration_seconds, cat_identity),
        ),
        GenerationPromptSectionDto(
            key="creative_treatment",
            title="整体基调",
            content=_creative_treatment_section(director_treatment),
        ),
        GenerationPromptSectionDto(
            key="shot_execution",
            title="逐镜执行",
            content="\n\n".join(executions),
        ),
        GenerationPromptSectionDto(
            key="ending_constraints",
            title="结尾与生成限制",
            content="\n".join(
                [
                    compile_prompt_sentence(f"主动结尾：{active_ending}"),
                    compile_prompt_sentence(
                        "结尾必须完成逐镜指定的最后动作并清楚呈现最终状态",
                        "不得擅自追加下一项任务",
                        ENDING_DIRECTION,
                    ),
                    compile_prompt_sentence(
                        "无文字、无Logo、无水印",
                        "参考图只用于已指定职责，不从中添加未设计的角色或道具",
                    ),
                ]
            ),
        ),
    )
    prompt = "\n\n".join(f"【{section.title}】\n{section.content}" for section in sections)
    return CompiledVideoGenerationPrompt(
        prompt=prompt,
        negative_prompt=_negative_prompt(shots),
        prompt_summary=_prompt_summary(project_title, shots, active_ending),
        prompt_sections=sections,
    )


def compile_shot_media_prompt(
    *, shot: ShotSpec, initial_frame: bool
) -> CompiledVideoGenerationPrompt:
    """Select only this shot, and only pre-action state for an image."""
    if initial_frame and (shot.child_blocking is None or shot.cat_blocking is None):
        raise ValueError(f"镜头{shot.order}缺少明确的人物或猫咪起始状态，请补充分镜后生成起始图")
    validate_system_prompt(
        shot.model_dump(
            by_alias=True,
            exclude={
                "generation_risks",
                "director_intent",
                "confirmed_frame",
            },
        ),
        source=f"shots[{shot.order - 1}]",
    )
    content = _shot_execution(shot, initial_frame=initial_frame)
    if initial_frame:
        content = "生成动作尚未开始的一张画面，保持起始状态，不提前表现动作结果。\n" + content
    else:
        content = (
            CAT_PERFORMANCE_DIRECTION + "严格首帧仅固定开始状态，之后按节拍自然表演。\n" + content
        )
    return CompiledVideoGenerationPrompt(
        prompt=content,
        negative_prompt=_negative_prompt([shot], initial_frame=initial_frame),
        prompt_summary=content,
        prompt_sections=(),
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
