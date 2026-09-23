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

# Prompt 编译器版本标记 —— 写入任务 frozen input(promptCompilerRevision)与输入快照,
# 用于追溯某段生成文本由哪个版本的编译器产出
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
# 整片 prompt 的 4 个固定小节 key(与 compile_video_generation_prompt 的 sections 一一对应)
PromptSectionKey = Literal[
    "identity_style",
    "creative_treatment",
    "shot_execution",
    "ending_constraints",
]


class GenerationPromptSectionDto(ContractModel):
    """prompt 分节 DTO —— key/title/content 三元组,供前端按小节展示与编辑预览。"""

    key: PromptSectionKey
    title: str
    content: str


@dataclass(frozen=True)
class CompiledVideoGenerationPrompt:
    """一次 prompt 编译的完整产物。

    - prompt:发给生成方的正文(【小节标题】+ 内容,空行分段)
    - negative_prompt:负面约束正文(【必须避免】小节的内容)
    - prompt_summary:面向用户的人类可读摘要(任务输入快照 / UI 展示用)
    - prompt_sections:分节结构;单镜头编译时无分节,为空元组
    """

    prompt: str
    negative_prompt: str
    prompt_summary: str
    prompt_sections: tuple[GenerationPromptSectionDto, ...]


# 边界标点集合 —— _clean_fragment 用它剥掉字段片段末尾的标点,
# 保证片段再经"；""、""。"拼接时接缝处不会残留双重标点
_BOUNDARY_PUNCTUATION = " \t\r\n，、；。,.!?！？：:"


def _clean_fragment(value: str) -> str:
    """清理单个字段片段:去掉首尾空白与末尾的边界标点。

    以省略号(…… / ...)结尾的片段原样保留 —— 省略号是刻意的"未完待续"表达
    (也是 500 字摘要截断的标记),不属于要剥离的边界标点。
    清理后的片段由 compile_prompt_sentence / _items 统一拼接并补标点,
    因此各结构化字段本身带不带句尾标点都不影响最终文本。
    """
    text = value.strip()
    if text.endswith(("……", "...")):
        return text
    return text.rstrip(_BOUNDARY_PUNCTUATION).strip()


def compile_prompt_sentence(*clauses: str) -> str:
    """把多个字段片段序列化成一句中文文本 —— 边界标点只在这一处统一添加。

    整片编译与片段编辑(service.py 的局部编辑拼装)共用本函数,保证两路的
    句内分隔一致:各 clause 经 _clean_fragment 清理后丢弃空片段,非空片段用
    "；"连接;结果为空或以省略号结尾时原样返回(省略号本身就是收尾),
    否则统一补句号"。"。
    """
    content = "；".join(normalized for clause in clauses if (normalized := _clean_fragment(clause)))
    if not content or content.endswith(("……", "...")):
        return content
    return f"{content}。"


def _items(values: list[str]) -> str:
    """把字符串列表序列化成顿号列举短语(逐项清理、丢弃空项)。

    列表为空或全部被清理为空时返回"无",显式声明该项没有内容,
    而不是让字段在句子里直接消失。
    """
    normalized = [_clean_fragment(value) for value in values]
    return "、".join(value for value in normalized if value) or "无"


def _blocking_summary(blocking: BlockingDesign) -> str:
    """把走位设计折叠成一句"初始状态 → 移动路径 → 结束状态"摘要。

    用于无动作节拍的镜头:兼容摘要(child_action/cat_action 回填)与
    "人物走位/猫咪走位"段落都用这一种表述;三个字段在 BlockingDesign
    中均为必填(min_length=1),不存在空段。
    """
    return " → ".join(
        _clean_fragment(value)
        for value in (blocking.initial_state, blocking.movement_path, blocking.end_state)
    )


def synchronize_professional_shot_summaries(shot: ShotSpec) -> ShotSpec:
    """让兼容摘要字段反映权威的结构化镜头字段,返回副本(不修改原对象)。

    兼容摘要指 child_action / cat_action / environment_change 这几个扁平文本字段,
    供只读取扁平字段的展示与编译路径使用;权威来源分两档:
    1. 有 action_beats 时:节拍是时间和动作顺序的唯一权威(见
       DIRECTOR_BEAT_DIRECTION)—— 每个角色的摘要由各节拍动作按"；"连接而成
       (全为空则写"本镜头无可见动作"),environment_change 由各节拍
       visibleChange 连接而成;有走位设计的角色,其 blocking.movement_path
       同步为同一摘要并清空 micro_motions,避免走位文本与节拍另排一套动作。
    2. 无节拍时:从结构化字段折叠 —— blocking 用 _blocking_summary 生成
       "初始 → 路径 → 结束"摘要,physical_change 生成"主体 · 前状态 → 后状态"。
    摘要超过 500 字截断为 499 字加省略号,防止扁平字段无限膨胀;
    无任何可更新项时原样返回传入的 shot。
    """

    updates: dict[str, object] = {}
    if shot.action_beats:
        # 有节拍:两个角色的扁平摘要与走位路径全部由节拍重算
        for actor in ("child", "cat"):
            actions = (
                "；".join(
                    action
                    for beat in shot.action_beats
                    if (action := getattr(beat, f"{actor}_action"))
                )
                or "本镜头无可见动作"
            )
            # 超 500 字截断为 499 字 + 省略号(_clean_fragment 会原样保留省略号结尾)
            summary = actions if len(actions) <= 500 else actions[:499] + "…"
            updates[f"{actor}_action"] = summary
            blocking = getattr(shot, f"{actor}_blocking")
            if blocking is not None:
                # movement_path 与节拍共用同一摘要、清空 micro_motions:
                # 节拍已定义动作顺序,走位不允许再单独排一套动作
                updates[f"{actor}_blocking"] = blocking.model_copy(
                    update={"movement_path": summary, "micro_motions": []}
                )
        changes = "；".join(beat.visible_change for beat in shot.action_beats)
        updates["environment_change"] = changes if len(changes) <= 500 else changes[:499] + "…"
        return shot.model_copy(update=updates)
    # 无节拍:退回结构化走位 / 物理变化字段折叠摘要
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
    """编译【角色与画风】小节:片名规格与两位角色的固定身份一次性声明。

    按顺序拼接:片名 / 9:16 / 目标时长 → 儿童固定身份(6-7 岁、约 1.2 米、
    齐下颌短发、圆润脸型、4.5-5 头身) → 猫咪身份(调用方传入 cat_identity,
    默认为灰白虎斑描述) → 猫咪表演方向(CAT_PERFORMANCE_DIRECTION,区分身份
    与表演) → 二维柔和数字插画画风。身份约束是每次生成都重复写入的固定文本,
    防止跨镜头出现角色漂移。
    """
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
    """编译【整体基调】小节:导演 treatment 只提供背景,不安排动作顺序。

    有 treatment 时先经 validate_system_prompt 校验 theme / emotional_tone /
    visual_motif / spatial_setting 四个字段没有未解析的系统占位符(其余字段
    不进入生成文本,无需校验),再写成"主题 / 情绪气质 / 视觉母题 / 空间"
    四个子句;无论有没有 treatment,都追加两条权威声明 —— "整体基调不定义
    动作顺序""逐镜执行是动作、节拍和最终状态的唯一权威",防止基调描述与
    镜头级节拍设计冲突时模型无从取舍。
    """
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
    """把单个镜头规格编译成一段"镜头执行"文本(标题行 + 每项设计一段一句话)。

    段落顺序:景别/运镜/转场 → 动作节拍(含猫咪表演) → 焦距与机位 → 环境使用
    → 机位与空间关系 → 交互约束 → 构图 → 人物走位/动作 → 猫咪走位/动作
    → 物理变化/画面变化 → 镜头承接 → 光线 → 声音。

    Args:
        shot: 镜头规格;结构化字段(blocking/beats/physical_change)优先,
            扁平摘要(child_action 等)只作无结构化字段时的回退。
        start_seconds: 本镜头在整片中的起始秒,用于把节拍的镜头内 24fps
            帧号换算成整片秒;单镜头编译时用默认 0。
        initial_frame: True 表示生成该镜头的"起始画面"静帧 —— 只输出动作
            开始前的静态状态:运镜、转场、节拍、交互约束、画面运动方向、
            离开状态、声音等时序内容全部省略,走位与物理变化只写起始状态。
        previous_shot: 前一镜头;两镜 lighting 完全相同时折叠成一句
            "沿用镜头N的光线",避免逐镜重复描述同一套灯光。
    """
    # 转场枚举 → 中文表述
    transition = {"continuous": "连续衔接", "soft_cut": "柔和切换", "hard_cut": "直接切换"}
    paragraphs = [
        # 静帧模式没有运镜与转场(静态画面不存在时序设计)
        compile_prompt_sentence(
            f"景别：{shot.framing}",
            "" if initial_frame else f"运镜：{shot.camera_movement}",
            "" if initial_frame else f"转场：{transition[shot.transition]}",
        )
    ]
    if shot.action_beats and not initial_frame:
        # 节拍枚举 → 可观察的中文描述;eyelid "none" 映射为空串即不输出该子句
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
            # 镜头内 24fps 帧号 → 整片秒;去掉尾随零让文本简洁(2.500→2.5、3.000→3)
            start = f"{start_seconds + beat.start_frame / 24:.3f}".rstrip("0").rstrip(".")
            end = f"{start_seconds + beat.end_frame / 24:.3f}".rstrip("0").rstrip(".")
            paragraphs.append(
                compile_prompt_sentence(
                    f"节拍 {start}–{end}秒 · {purposes[beat.purpose]}",
                    f"儿童：{beat.child_action}" if beat.child_action else "",
                    f"猫咪：{beat.cat_action}" if beat.cat_action else "",
                    f"道具与环境：{beat.environment_action}" if beat.environment_action else "",
                    f"可见变化：{beat.visible_change}",
                )
            )
            performance = beat.cat_performance
            if performance is not None:
                paragraphs.append(
                    compile_prompt_sentence(
                        f"本节拍猫咪表演：{visibility[performance.visibility]}",
                        # 目光转移需要起点和终点都填写;脸不可见(hidden)时
                        # 不要求目光与眼睑表演 —— 看不见脸就谈不上眼部可读性
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
            # preserve_layout → 严格沿用参考布局;其余模式允许重新构图,
            # 但场景外观与空间关系不变
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
    # 交互约束是时序规则(动作过程中的边界),静帧模式不输出
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
                # 有节拍时不输出构图"视线"子句 —— 目光转移由节拍的
                # catPerformance(gazeFrom/gazeTo)权威描述,避免两处打架
                f"视线：{shot.composition.eye_line}" if not shot.action_beats else "",
            )
        )
    # 人物段落三档形态:静帧 → 只写起始状态;有节拍 → 只写空间起止
    # (动作过程归节拍,微动作也一并让位);其余 → 完整走位摘要 + 微动作
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
        # 无结构化走位且无节拍时才回退到扁平动作摘要(静帧没有动作可言)
        paragraphs.append(compile_prompt_sentence(f"人物动作：{shot.child_action}"))
    # 猫咪段落与人物段落同一套三档形态
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
    # 物理变化只在静帧(写起始状态)或无节拍镜头(写完整 before → after)输出;
    # 有节拍时变化过程已由各节拍的 visibleChange 覆盖,不再重复
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
        # 无结构化物理变化时回退到扁平的画面变化摘要
        paragraphs.append(compile_prompt_sentence(f"画面变化：{shot.environment_change}"))
    if shot.continuity is not None:
        paragraphs.append(
            compile_prompt_sentence(
                f"镜头承接：{shot.continuity.incoming}",
                # 离开状态描述镜头结束时序,静帧(动作未开始)不输出
                "" if initial_frame else f"离开状态：{shot.continuity.outgoing}",
                f"共享视觉元素：{shot.continuity.shared_visual_element}",
            )
        )
    if (
        previous_shot is not None
        and shot.lighting is not None
        and shot.lighting == previous_shot.lighting
    ):
        # 与前一镜的灯光设计完全相同 → 一句话声明沿用,不重复展开三要素
        paragraphs.append(f"光线：沿用镜头{previous_shot.order}的方向、柔和度与色彩。")
    elif shot.lighting is not None:
        paragraphs.append(
            compile_prompt_sentence(
                f"光线方向：{shot.lighting.direction}",
                f"柔和度：{shot.lighting.softness}",
                f"色彩意图：{shot.lighting.color_intent}",
            )
        )
    # 静帧没有声音;有声音设计时分类列举(对白仅在有内容时追加),否则用通用兜底句
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
    # 标题:静帧标"起始画面";视频标注本镜在整片中的起止秒;拼接时过滤空段落
    title = (
        f"镜头 {shot.order} 起始画面"
        if initial_frame
        else (f"镜头 {shot.order}（{start_seconds:g}–{start_seconds + shot.duration_seconds:g}秒）")
    )
    return title + "\n" + "\n".join(paragraph for paragraph in paragraphs if paragraph)


def _active_ending(shots: list[ShotSpec]) -> str:
    """提取整片的"主动结尾"描述 —— 结尾小节要求画面最终到达的状态。

    优先取最后一镜 continuity.final_frame(分镜设计时确认的结尾画面文本);
    没有 continuity 或 final_frame 为空白时,退回最后一镜的人物动作、猫咪动作
    与环境变化三个兼容摘要折叠成一句,并去掉句尾句号 —— 调用方会把它再拼进
    "主动结尾：…"整句,句号由外层统一补。
    """
    final_shot = shots[-1]
    if final_shot.continuity is not None and final_shot.continuity.final_frame.strip():
        return _clean_fragment(final_shot.continuity.final_frame)
    return compile_prompt_sentence(
        final_shot.child_action,
        final_shot.cat_action,
        final_shot.environment_change,
    ).removesuffix("。")


def _prompt_summary(project_title: str, shots: list[ShotSpec], active_ending: str) -> str:
    """生成人类可读的整片摘要(prompt_summary),用于 UI 展示与任务输入快照。

    每镜收集两条信息:
    - 主要动作:有节拍时取各节拍的儿童/猫咪动作(节拍内"，"分隔、
      节拍间"；"分隔);无节拍时优先取 blocking 的 movement_path,
      没有 blocking 再退回扁平动作摘要。
    - 可见变化:有节拍时收集各节拍 visibleChange;无节拍时优先
      physical_change("主体从before变为after"),否则 environment_change。
    最后拼成一句:《片名》共 N 个镜头 + 主要动作 + 可见变化 + 最终画面。
    """
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
        # 无节拍:动作摘要优先走位路径(blocking 存在时它就是权威)
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
    """编译负面 prompt 正文,即提交文本中【必须避免】小节的内容。

    组成:_BASE_VIDEO_EXCLUSIONS 通用禁用项 + 各镜头 visual_exclusions
    (加"镜头N："前缀标明作用范围)。镜头级条目先与已有条目按语义去重
    (casefold 后比较),最终整体再清理、去重一次并用"，"连接。

    initial_frame=True(起始画面静帧)时剔除 4 条通用禁用项:起始帧是一张
    静态画面,"背景严重跳变 / 长时间无回应地对视填充时长 / 静止停帧 /
    循环动作填充时长"都是视频时序特有的问题,对单帧不存在,写进去反而
    向模型引入与静帧无关的概念。
    """
    items = [
        item
        for item in _BASE_VIDEO_EXCLUSIONS
        if not initial_frame
        or item
        not in {
            # 静帧模式剔除清单 —— 全部是"视频时序"类禁用项(见 docstring)
            "背景严重跳变",
            "长时间无回应地对视填充时长",
            "静止停帧",
            "循环动作填充时长",
        }
    ]
    # 已有条目的语义集合(casefold),镜头级禁用项与其重复时直接跳过
    seen_meanings = {_clean_fragment(item).casefold() for item in items}
    for shot in shots:
        for exclusion in shot.visual_exclusions:
            message = _clean_fragment(exclusion)
            if not message or message.casefold() in seen_meanings:
                continue
            seen_meanings.add(message.casefold())
            items.append(f"镜头{shot.order}：{message}")
    # 汇总后再统一清理一次边界标点并去重,保证每条禁用项以干净文本入列
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
    """整片视频 prompt 编译主入口:把"导演 treatment + 整片配置 + 逐镜设计"折叠成 4 小节文本。

    流程:
    1. 校验:shots 非空;逐镜校验字段没有未解析的系统占位符(排除
       generation_risks / director_intent / confirmed_frame —— 三者是内部
       注记与已确认帧引用,不进入生成文本);再校验连续性约束与猫咪身份。
    2. 有 continuity_constraints 时,在逐镜执行前加"本集进入状态"段落
       (系列片从上一集继承的已确认状态)。
    3. 逐镜调用 _shot_execution,按各镜 duration_seconds 累计整片秒数
       (24fps 时间轴),并传入前一镜头供灯光相同时折叠沿用。
    4. 组装 4 个固定小节:角色与画风 / 整体基调 / 逐镜执行 / 结尾与生成限制;
       结尾小节包含主动结尾、完成度要求(拼接 ENDING_DIRECTION)与
       "无文字/Logo/水印、参考图只用已指定职责"限制。

    Returns:
        CompiledVideoGenerationPrompt:prompt 为"【小节标题】\\n内容"按空行
        连接的完整正文;negative_prompt 来自 _negative_prompt;
        prompt_summary 与 prompt_sections 供 UI 展示与任务快照。

    Raises:
        ValueError: shots 为空。
        MediaPromptError: 字段中残留未解析的系统占位符(validate_system_prompt 抛出)。
    """
    if not shots:
        raise ValueError("video generation requires at least one shot")
    for shot in shots:
        validate_system_prompt(
            shot.model_dump(
                by_alias=True,
                exclude={
                    # 内部字段不进入 prompt:生成风险注记 / 导演意图 / 已确认帧引用
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
    # 系列连续性约束作为"本集进入状态"段落,排在所有镜头执行段之前
    if continuity_constraints:
        executions.append(
            "本集进入状态：\n"
            + "\n".join(
                compile_prompt_sentence(constraint) for constraint in continuity_constraints
            )
        )
    # 按各镜时长累计整片秒数;previous_shot 供灯光相同的相邻镜头折叠沿用
    elapsed = 0
    previous_shot = None
    for shot in shots:
        executions.append(_shot_execution(shot, start_seconds=elapsed, previous_shot=previous_shot))
        elapsed += shot.duration_seconds
        previous_shot = shot
    active_ending = _active_ending(shots)
    # 4 个小节顺序固定:角色与画风 → 整体基调 → 逐镜执行 → 结尾与生成限制
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
    # 每小节以"【标题】+ 换行 + 内容"呈现,小节之间空行分隔,构成完整生成正文
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
    """只选取当前这一个镜头编译生成文本;静帧模式只输出动作开始前的状态。

    initial_frame=True(生成"起始画面"静帧):要求镜头同时具有明确的
    child_blocking 与 cat_blocking —— 静帧必须固定两位角色的起始状态,
    缺失时抛 ValueError 提示先补分镜;正文前加"生成动作尚未开始的一张画面"
    声明,负面 prompt 同步剔除 4 条视频时序禁用项(见 _negative_prompt)。
    initial_frame=False(单镜头视频):正文前加猫咪表演方向与"严格首帧仅
    固定开始状态,之后按节拍自然表演"声明,避免首帧约束被误解为全程锁定。

    单镜头自身就是全部上下文,prompt_summary 直接复用正文;不做分节展示,
    prompt_sections 为空元组。
    """
    visible = set(shot.information.visible_subjects) if shot.information else {"child", "cat"}
    if initial_frame and (("child" in visible and shot.child_blocking is None)
                          or ("cat" in visible and shot.cat_blocking is None)):
        raise ValueError(f"镜头{shot.order}缺少明确的人物或猫咪起始状态，请补充分镜后生成起始图")
    validate_system_prompt(
        shot.model_dump(
            by_alias=True,
            exclude={
                # 与整片编译一致:内部注记字段不进入生成文本
                "generation_risks",
                "director_intent",
                "confirmed_frame",
            },
        ),
        source=f"shots[{shot.order - 1}]",
    )
    # 单镜头时间从 0 秒起算,不传整片偏移(start_seconds 用默认值)
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
    """把正文与负面约束折叠成当前 Ark 视频 SDK 接受的单条文本指令。

    视频 SDK 只接受一段 prompt 文本(参考图走独立的 content 数组,由
    worker 网关按 reference_roles 另行标注职责顺序),负面约束没有独立字段,
    必须并入正文:输出固定为"【生成目标】…\\n\\n【必须避免】…"两段。
    两个字段都必填 —— 任一为空即抛 ValueError 拒绝本次付费提交;
    负面约束缺失被视为输入错误,不允许静默降级。
    调用方:worker/ark_job_gateway.py,仅在 frozen input 没有预编译的
    compiledProviderPrompt 时兜底;整片/逐镜的完整编译(含【参考职责】段)
    走 media_prompt.compile_provider_media_prompt。
    """

    target = prompt.strip()
    exclusions = negative_prompt.strip()
    if not target:
        raise ValueError("video generation prompt is required")
    if not exclusions:
        raise ValueError("video generation negative prompt is required")
    return f"【生成目标】\n{target}\n\n【必须避免】\n{exclusions}"
