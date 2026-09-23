"""Compile only a unit's observable actions and applicable visual references."""
from __future__ import annotations

from .creative_direction import CAT_PERFORMANCE_DIRECTION
from .media_prompt import compile_provider_media_prompt
from .production_plan import ProductionUnit, provider_duration, shot_frames, unit_shots


def unit_references(document: dict, unit: ProductionUnit) -> list[dict]:
    shots = unit_shots(document, unit)
    scene_key = shots[0].information.scene_key if shots[0].information else "main"
    scene = next(item for item in document["scenes"] if item["key"] == scene_key)
    prop_keys = {key for shot in shots if shot.information for key in shot.information.prop_keys}
    sources = list(document["canon"]["references"])
    sources.insert(3, {"assetId": scene["assetId"], "sha256": scene["sha256"], "role": "environment"})
    sources += [{"assetId": prop["assetId"], "sha256": prop["sha256"], "role": f"prop:{prop['key']}"}
                for prop in document["props"] if prop["key"] in prop_keys]
    grouped: dict[str, dict] = {}
    for reference in sources:
        if reference["sha256"] in grouped:
            grouped[reference["sha256"]]["duties"].append(reference["role"])
        else:
            grouped[reference["sha256"]] = {**reference, "duties": [reference["role"]]}
    return list(grouped.values())


def compile_unit(document: dict, unit: ProductionUnit, *, references: list[dict],
                 incoming: dict | None) -> dict:
    shots = unit_shots(document, unit)
    scene_key = shots[0].information.scene_key if shots[0].information else "main"
    scene = next(item for item in document["scenes"] if item["key"] == scene_key)
    used_props = {key for s in shots if s.information for key in s.information.prop_keys}
    sections = []
    sections.append({"title": "本单元目标", "content": "；".join(
        s.information.new_information if s.information else s.director_intent or s.environment_change
        for s in shots
    )})
    initial = [f"场景：{scene['name']}。轴线：{scene.get('axis', '')}。"]
    for point in scene.get("layout", []):
        initial.append(f"{point['label']}：平面位置({point['x']},{point['y']})，{point['direction']}。")
    if incoming:
        initial.append("上一个已采用片段的实际结束状态：" + "；".join(
            fact["key"] + "：" + fact["value"] for fact in incoming["facts"]
            if fact["certainty"] == "observed"))
        initial.append("尚未完成：" + incoming.get("unfinishedActions", "无"))
    else:
        initial.append("本单元独立建立起点：" + unit.reason)
    sections.append({"title": "起点与空间", "content": "\n".join(initial)})
    offset, events, lines = 0, [], []
    for shot in shots:
        end = offset + shot_frames(shot)
        subjects = "、".join(shot.information.visible_subjects) if shot.information else "按原镜头"
        lines.append(f"{offset / 24:g}–{end / 24:g}秒：{shot.framing}，{shot.camera_movement}；仅拍{subjects}。")
        if shot.camera_spatial_relation:
            lines.append("空间：" + shot.camera_spatial_relation)
        for actor, blocking in (("儿童", shot.child_blocking), ("猫咪", shot.cat_blocking)):
            if blocking:
                lines.append(f"{actor}起始：{blocking.initial_state}；结束：{blocking.end_state}。")
        for beat in shot.action_beats or []:
            lines.append(f"{(offset + beat.start_frame) / 24:g}–{(offset + beat.end_frame) / 24:g}秒："
                         + "；".join(text for text in (beat.child_action, beat.cat_action,
                                                      beat.environment_action, beat.visible_change) if text))
            if beat.cat_performance and beat.cat_performance.visibility != "hidden":
                performance = beat.cat_performance
                lines.append(f"注意目标从{performance.gaze_from}转向{performance.gaze_to}；"
                             f"眼睑动作{performance.eyelid_action}，脸部须清楚可读。")
        if not shot.action_beats:
            lines.append("；".join([shot.child_action, shot.cat_action, shot.environment_change]))
        lines.extend(shot.interaction_constraints)
        if shot.sound:
            lines.append("声音：" + "；".join([*shot.sound.ambience, *shot.sound.object_effects,
                                             *shot.sound.movement_effects]))
        if shot.information:
            for event in shot.information.key_events:
                events.append({**event.model_dump(by_alias=True), "shotId": shot.id,
                               "unitStartFrame": offset + event.start_frame,
                               "unitEndFrame": offset + event.end_frame})
        offset = end
    sections.append({"title": "动作、反应与揭示", "content": "\n".join(lines)})
    fixed = [document["canon"]["catIdentity"], CAT_PERFORMANCE_DIRECTION]
    fixed += [f"道具{p['name']}：{p['identity']}；"
              + ("起点以已观察状态为准；" if incoming else f"初始{p['initialState']}；")
              +
              f"计划变化{p['plannedChange']}；归属{p['owner']}，位置{p['location']}。"
              for p in document["props"] if p["key"] in used_props]
    fixed.append("同一位儿童和同一只猫，无音乐无对白，仅自然环境与动作声。镜头未要求的主体不得入画。")
    sections.append({"title": "身份、道具与声音", "content": "\n".join(fixed)})
    duration = provider_duration(shots)
    lead = f"生成9:16、24fps视频；生成{duration}秒，目标取用{offset / 24:g}秒。"
    if unit.generation_mode == "from_frame":
        lead += "严格从首帧开始，之后按动作自然变化，不锁定表情。"
    prompt = lead + "\n" + "\n\n".join(f"【{s['title']}】\n{s['content']}" for s in sections)
    exclusions = "；".join(["多猫、额外肢体、道具增生、文字水印、循环动作填时长",
                           *(value for shot in shots for value in shot.visual_exclusions)])
    duties = "\n".join(f"图{i + 1}同时承担：{'、'.join(r['duties'])}。" for i, r in enumerate(references))
    compiled = compile_provider_media_prompt(
        prompt=prompt + "\n" + duties, negative_prompt=exclusions,
        reference_roles=tuple(r["role"] for r in references),
    )
    return {"prompt": prompt, "negativePrompt": exclusions, "compiledProviderPrompt": compiled,
            "promptSections": [{**s, "characters": len(s["content"])} for s in sections],
            "promptCharacters": len(prompt), "keyEvents": events,
            "durationSeconds": duration, "targetDurationFrames": offset,
            "warnings": ([{"code": "prompt_length", "message": "正文超过约2500字符，请检查重复信息；未自动截断。"}]
                         if len(prompt) > 2500 else [])}
