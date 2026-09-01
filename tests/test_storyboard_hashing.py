from __future__ import annotations

import uuid

from cat_video_generator.infrastructure.db.models import ShotBeat
from cat_video_generator.infrastructure.db.storyboard_hashing import storyboard_structure_hash


def _beat() -> ShotBeat:
    return ShotBeat(
        id=uuid.uuid4(),
        scene_id=uuid.uuid4(),
        storyboard_revision_id=uuid.uuid4(),
        sort_order=1,
        revision=2,
        title="猫咪递来一片叶子",
        action="女孩接住猫咪递来的叶子。",
        visual_description="女孩蹲下，猫咪把叶子推到她手边。",
        child_action="蹲下接住叶子",
        cat_action="把叶子推近",
        spatial_relation="女孩在左，猫咪在右",
        contact_occlusion="手指短暂遮住叶缘",
        shot_size="中景",
        camera="固定机位",
        lighting="窗边暖光",
        dialogue="谢谢你。",
        sound_effect="叶片摩擦声",
        music_intent="轻柔木琴",
        wardrobe_state="黄色雨衣",
        prop_state="一片湿叶",
        continuity_in="雨刚停",
        continuity_out="女孩起身",
        cut_intent="soft_cut",
        duration_seconds=6,
        reference_bindings_json=[{"assetId": "asset-1", "sha256": "a" * 64}],
        reference_binding_revision=3,
        status="ready",
    )


def test_storyboard_structure_hash_covers_prompt_and_reference_inputs() -> None:
    beat = _beat()
    baseline = storyboard_structure_hash([beat])

    fields = {
        "title": "猫咪把叶子送到女孩面前",
        "action": "女孩把叶子举到窗边。",
        "visual_description": "叶片上的水珠映着窗光。",
        "dialogue": "我们留着它吧。",
        "camera": "缓慢推进",
        "temporal_beats_json": [{"phase": "close", "direction": "女孩举起叶子"}],
        "reference_bindings_json": [{"assetId": "asset-2", "sha256": "b" * 64}],
        "reference_binding_revision": 4,
    }
    for field, value in fields.items():
        original = getattr(beat, field)
        setattr(beat, field, value)
        assert storyboard_structure_hash([beat]) != baseline, field
        setattr(beat, field, original)
