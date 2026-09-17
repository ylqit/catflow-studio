from __future__ import annotations

from catflow.application.series import SeriesBibleDraft, SourceTreatmentDraft
from catflow.application.series_prompt_text import (
    render_length_mode,
    render_must_keep,
    render_narrative_mode,
    render_series_bible,
    render_source_treatments,
)

# 与创作者真实整季设定同构的样例:覆盖 bible 的全部 12 个字段
_RAIN_BIBLE = {
    "logline": "一个持续下雨的日子里，小孩和一只白猫先在水雾玻璃上画画互动，"
    "再到门口放纸船看它漂远，最后在暖夜灯下依偎听雨声。",
    "centralTheme": "雨天里，小孩与白猫在窗边、门口和夜晚地毯上共享安静陪伴。",
    "narrativeMode": "lightly_serialized",
    "worldRules": ["时间从白天经傍晚过渡到夜色渐深，雨一直未停。", "室内陈设保持普通居家感。"],
    "emotionalArc": {
        "opening": "白天雨声中，小孩和白猫一起趴在窗边。",
        "development": "小孩在玻璃上画猫，白猫按出爪印引发笑声。",
        "climax": "纸船顺着雨水缓缓漂远，小孩和白猫同步转头目送。",
        "resolution": "夜色加深，暖黄小夜灯下依偎听雨。",
    },
    "recurringLocations": [
        {"key": "window_nook_day", "name": "白天窗边", "description": "室内窗边，窗外是湿院子。"},
        {"key": "home_entry_eaves", "name": "家门口屋檐下", "description": "屋檐外有浅浅水流。"},
    ],
    "recurringProps": [
        {
            "key": "colored_paper_boat", "name": "彩色纸与纸船",
            "continuityRule": "第二集折成同一艘小纸船。",
        },
        {"key": "soft_blanket", "name": "柔软毯子", "continuityRule": "第三集盖住小孩与白猫。"},
    ],
    "wardrobeRules": ["小孩穿着舒适便服，颜色柔和。", "白猫外观固定为V4参考，不穿衣服。"],
    "continuityRules": ["三集按时间顺序从白天窗边到门口放纸船，再到雨夜室内收尾。"],
    "visualMotifs": ["玻璃上滑落的雨滴", "暖黄色小夜灯与柔软阴影"],
    "soundMotifs": ["持续淅沥雨声", "雨滴敲打玻璃声"],
    "forbiddenChanges": ["不得把白猫整体染成奶油黄。", "不得增加新角色或新主线事件。"],
}


def test_render_series_bible_keeps_every_field_in_chinese_sections():
    rendered = render_series_bible(SeriesBibleDraft.model_validate(_RAIN_BIBLE))
    for expected in (
        "叙事模式：轻连续",
        "整季核心：一个持续下雨的日子里",
        "整季主题：雨天里，小孩与白猫",
        "世界规则：",
        "1. 时间从白天经傍晚过渡到夜色渐深，雨一直未停。",
        "情绪弧线：",
        "- 开场：白天雨声中",
        "- 发展：小孩在玻璃上画猫",
        "- 高潮：纸船顺着雨水缓缓漂远",
        "- 收束：夜色加深",
        "常驻场景：",
        "1. 白天窗边(标识 window_nook_day)：室内窗边，窗外是湿院子。",
        "2. 家门口屋檐下(标识 home_entry_eaves)：屋檐外有浅浅水流。",
        "常驻道具：",
        "1. 彩色纸与纸船(标识 colored_paper_boat)：第二集折成同一艘小纸船。",
        "服装规则：",
        "整季连续性规则：",
        "视觉母题：玻璃上滑落的雨滴、暖黄色小夜灯与柔软阴影",
        "声音母题：持续淅沥雨声、雨滴敲打玻璃声",
        "禁止改动：",
        "1. 不得把白猫整体染成奶油黄。",
        "2. 不得增加新角色或新主线事件。",
    ):
        assert expected in rendered


def test_render_series_bible_leaves_no_json_traces():
    rendered = render_series_bible(SeriesBibleDraft.model_validate(_RAIN_BIBLE))
    for forbidden in ('"', "{", "}", "worldRules", "narrativeMode", "emotionalArc",
                      "recurringLocations", "continuityRule", "forbiddenChanges"):
        assert forbidden not in rendered


def test_render_series_bible_falls_back_to_none_for_empty_fields():
    rendered = render_series_bible(SeriesBibleDraft())
    assert "叙事模式：未指定" in rendered
    assert "整季核心：无" in rendered
    assert "整季主题：无" in rendered
    assert "世界规则：无" in rendered
    assert "- 开场：无" in rendered
    assert "常驻场景：无" in rendered
    assert "常驻道具：无" in rendered
    assert "视觉母题：无" in rendered
    assert "禁止改动：无" in rendered


def test_enum_renderers_translate_known_values_and_pass_unknown_through():
    assert render_narrative_mode("continuous") == "连续剧情"
    assert render_narrative_mode("lightly_serialized") == "轻连续"
    assert render_narrative_mode("anthology") == "单元故事"
    assert render_narrative_mode(None) == "未指定"
    assert render_narrative_mode("surprise_mode") == "surprise_mode"
    assert render_length_mode("fixed") == "固定集数"
    assert render_length_mode("ongoing") == "持续连载"
    assert render_length_mode(None) == "未指定"


def test_render_must_keep_joins_and_falls_back():
    assert render_must_keep(["玻璃画猫", "爪印"]) == "玻璃画猫、爪印"
    assert render_must_keep([]) == "无"
    assert render_must_keep(None) == "无"
    assert render_must_keep(["  ", "热饮"]) == "热饮"


def test_render_source_treatments_lists_every_decision_in_chinese():
    treatments = [
        SourceTreatmentDraft.model_validate({
            "sourceUnitOrdinal": 1, "treatment": "merged",
            "episodeOrders": [1, 2], "reason": "两段同一场景，合并为一集。",
        }),
        SourceTreatmentDraft.model_validate({
            "sourceUnitOrdinal": 3, "treatment": "omitted",
            "episodeOrders": [], "reason": "与主线无关的旁支。",
        }),
    ]
    rendered = render_source_treatments(treatments)
    assert "- 来源 1：合并，覆盖第 1 集、第 2 集；理由：两段同一场景，合并为一集。" in rendered
    assert "- 来源 3：省略，覆盖未指定集数；理由：与主线无关的旁支。" in rendered
    assert '"' not in rendered
    assert render_source_treatments([]) == "无"
    assert render_source_treatments(None) == "无"
