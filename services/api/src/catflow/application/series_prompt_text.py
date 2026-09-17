"""系列规划 prompt 的中文分项渲染 —— 替代 prompt 中的原始 JSON 内嵌。

三个 compile 函数(整季方案 / 续段方案 / 单集故事)过去把整季设定(SeriesBibleDraft)、
用户必须保留要求与缩编来源处理以 model_dump_json / json.dumps 直接拼进发给模型的
prompt;界面又会原样展示这份 prompt,创作者因此看到大量 camelCase 变量名。
本模块把同样的结构化内容渲染成中文分项文本:

- 信息零丢失:JSON 中每个字段都有对应的中文行;
- 不出现 camelCase 字段名、引号或花括号;
- 空值统一兜底"无";
- 常驻场景 / 道具的 key 是模型输出 outline 时 recurringLocationKeys /
  recurringPropKeys 必须引用的标识符,以"(标识 xxx)"形式保留。

类型只做标注用(TYPE_CHECKING 导入),运行时仅访问属性,避免与 series.py 循环导入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .series import SeriesBibleDraft, SourceTreatmentDraft

# 叙事模式枚举 → 创作者语言(与前端 SeriesWorkspaceView 的映射保持一致)
_NARRATIVE_MODE_LABELS = {
    "continuous": "连续剧情",
    "lightly_serialized": "轻连续",
    "anthology": "单元故事",
}
# 系列长度模式枚举 → 创作者语言
_LENGTH_MODE_LABELS = {"fixed": "固定集数", "ongoing": "持续连载"}
# 来源处理决定枚举 → 创作者语言
_TREATMENT_LABELS = {
    "retained": "保留",
    "merged": "合并",
    "simplified": "简化",
    "omitted": "省略",
}


def render_narrative_mode(value: str | None) -> str:
    """叙事模式枚举转中文;未知值原样返回,保证不吞数据。"""
    if value is None:
        return "未指定"
    return _NARRATIVE_MODE_LABELS.get(value, value)


def render_length_mode(value: str | None) -> str:
    """系列长度模式枚举转中文;未知值原样返回。"""
    if value is None:
        return "未指定"
    return _LENGTH_MODE_LABELS.get(value, value)


def render_must_keep(items: list[str] | None) -> str:
    """用户必须保留要求列表转顿号串;空列表兜底"无"。"""
    kept = [item.strip() for item in (items or []) if item.strip()]
    return "、".join(kept) or "无"


def render_source_treatments(treatments: list[SourceTreatmentDraft] | None) -> str:
    """缩编路线的来源处理决定转中文分项;空列表兜底"无"。"""
    if not treatments:
        return "无"
    lines = []
    for item in treatments:
        episodes = "、".join(f"第 {order} 集" for order in item.episode_orders) or "未指定集数"
        treatment = _TREATMENT_LABELS.get(item.treatment, item.treatment)
        lines.append(
            f"- 来源 {item.source_unit_ordinal}：{treatment}，覆盖{episodes}；"
            f"理由：{item.reason}"
        )
    return "\n".join(lines)


def _numbered_section(title: str, items: list[str]) -> str:
    """编号小节;空列表渲染为"标题：无"。"""
    if not items:
        return f"{title}：无"
    body = "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))
    return f"{title}：\n{body}"


def render_series_bible(bible: SeriesBibleDraft) -> str:
    """整季设定(SeriesBibleDraft)转中文分项文本,供 prompt 内嵌与界面展示。"""
    arc = bible.emotional_arc
    locations = []
    for location in bible.recurring_locations:
        name = location.name or location.key or "未命名场景"
        marker = f"(标识 {location.key})" if location.key else ""
        locations.append(f"{name}{marker}：{location.description or '无'}")
    props = []
    for prop in bible.recurring_props:
        name = prop.name or prop.key or "未命名道具"
        marker = f"(标识 {prop.key})" if prop.key else ""
        props.append(f"{name}{marker}：{prop.continuity_rule or '无'}")
    return "\n".join(
        [
            f"叙事模式：{render_narrative_mode(bible.narrative_mode)}",
            f"整季核心：{bible.logline or '无'}",
            f"整季主题：{bible.central_theme or '无'}",
            _numbered_section("世界规则", bible.world_rules),
            "情绪弧线：",
            f"- 开场：{arc.opening or '无'}",
            f"- 发展：{arc.development or '无'}",
            f"- 高潮：{arc.climax or '无'}",
            f"- 收束：{arc.resolution or '无'}",
            _numbered_section("常驻场景", locations) if locations else "常驻场景：无",
            _numbered_section("常驻道具", props) if props else "常驻道具：无",
            _numbered_section("服装规则", bible.wardrobe_rules),
            _numbered_section("整季连续性规则", bible.continuity_rules),
            f"视觉母题：{'、'.join(bible.visual_motifs) or '无'}",
            f"声音母题：{'、'.join(bible.sound_motifs) or '无'}",
            _numbered_section("禁止改动", bible.forbidden_changes),
        ]
    )
