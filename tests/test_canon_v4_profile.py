from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from cat_video_generator.domain.contracts import ReferenceBinding, VisualProfileDraft
from cat_video_generator.domain.production_recipes import (
    CANON_V4_PROFILE_ID,
    CANON_V4_STYLE_BOARD_KEY,
    CANON_V4_STYLE_NEGATIVE,
    CANON_V4_STYLE_SOURCE_EXCLUSIONS,
    CANON_V4_STYLE_SOURCE_KEY,
    canon_reference_keys,
)
from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    _compile_provider_reference_manifest,
    _compile_storyboard_prompt_text,
)
from cat_video_generator.infrastructure.db.models import (
    Scene,
    StoryRevisionRecord,
    VisualProfileRevision,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_canon_v4_manifest_files_are_immutable_and_exact() -> None:
    root = PROJECT_ROOT / "风格定稿" / "Canon-v4"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["profileId"] == CANON_V4_PROFILE_ID
    assert {item["semanticKey"] for item in manifest["assets"]} == {
        CANON_V4_STYLE_SOURCE_KEY,
        CANON_V4_STYLE_BOARD_KEY,
    }
    for item in manifest["assets"]:
        source = root / item["file"]
        assert source.is_file()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == item["sha256"]


def test_daily_provider_canon_keys_use_distilled_board_not_leaf_source() -> None:
    keys = canon_reference_keys(CANON_V4_PROFILE_ID, "indoor")

    assert CANON_V4_STYLE_BOARD_KEY in keys
    assert CANON_V4_STYLE_SOURCE_KEY not in keys
    assert "禁止绿色" not in "、".join(CANON_V4_STYLE_NEGATIVE)
    assert "叶片" in "、".join(CANON_V4_STYLE_SOURCE_EXCLUSIONS)


def test_style_source_is_omitted_without_consuming_provider_slot() -> None:
    source_id = str(uuid.uuid4())
    board_id = str(uuid.uuid4())
    compiled, blockers, warnings = _compile_provider_reference_manifest(
        [
            {
                "assetId": source_id,
                "sha256": "a" * 64,
                "title": "叶片材质来源",
                "source": "canon",
                "purpose": CANON_V4_STYLE_SOURCE_KEY,
                "role": "style",
                "authority": {
                    "role": "style_source",
                    "providerEligible": False,
                    "priority": 10,
                },
            },
            {
                "assetId": board_id,
                "sha256": "b" * 64,
                "title": "纯画风板",
                "source": "canon",
                "purpose": CANON_V4_STYLE_BOARD_KEY,
                "role": "style",
                "authority": {
                    "role": "style_board",
                    "providerEligible": True,
                    "priority": 50,
                },
            },
        ],
        maximum=1,
    )

    assert blockers == []
    assert warnings == []
    assert compiled[0]["providerIncluded"] is False
    assert "仅用于画风提炼" in compiled[0]["omissionReason"]
    assert compiled[1]["providerIncluded"] is True
    assert compiled[1]["providerSlot"] == "reference_image_1"


def test_visual_profile_round_trips_strict_reference_authority() -> None:
    asset_id = uuid.uuid4()
    profile = VisualProfileDraft.model_validate(
        {
            "personIdentity": "固定同一个 8–9 岁儿童的脸型与五官",
            "personHair": "固定齐下颌短发与发际线",
            "personBody": "固定儿童身体比例",
            "catIdentity": "固定灰白分区、虎斑、四足结构与尾巴环纹",
            "stylePositive": ["原创二维插画", "暖灰轮廓线", "柔和漫射光"],
            "styleNegative": ["摄影写实", "3D塑料感"],
            "referenceBindings": [
                {
                    "assetId": str(asset_id),
                    "purpose": "style",
                    "instruction": "只控制轮廓线、材质、色阶与光影",
                    "authority": {
                        "role": "style_board",
                        "providerEligible": True,
                        "priority": 50,
                        "lockedTraits": ["轮廓线", "材质"],
                        "mutableTraits": ["剧情场景颜色"],
                        "forbiddenTransfer": ["具体物体与构图"],
                    },
                }
            ],
        }
    )

    dumped = profile.model_dump(mode="json", by_alias=True)
    assert dumped["referenceBindings"][0]["authority"]["role"] == "style_board"
    assert dumped["referenceBindings"][0]["authority"]["providerEligible"] is True


def test_generation_reference_round_trips_strict_reference_authority() -> None:
    binding = ReferenceBinding.model_validate(
        {
            "assetId": str(uuid.uuid4()),
            "usage": "generation_reference",
            "role": "identity",
            "applyTo": "anchor",
            "authority": {
                "role": "identity",
                "providerEligible": True,
                "priority": 100,
                "lockedTraits": ["脸型", "五官", "齐下颌短发"],
                "mutableTraits": ["服装", "动作"],
                "forbiddenTransfer": ["背景", "旧服装"],
            },
        }
    )

    dumped = binding.model_dump(mode="json", by_alias=True)
    assert dumped["authority"]["role"] == "identity"
    assert dumped["authority"]["lockedTraits"] == ["脸型", "五官", "齐下颌短发"]


def test_video_prompt_is_natural_language_and_keeps_reference_responsibilities() -> None:
    profile = VisualProfileRevision(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        revision=4,
        profile_hash="c" * 64,
        source_profile_id=CANON_V4_PROFILE_ID,
        person_identity="固定柔和圆脸和五官",
        person_hair="固定深棕黑色齐下颌短发",
        person_body="固定 8–9 岁儿童身体比例",
        cat_identity="固定灰白分区、主要虎斑、四足结构和尾巴环纹",
        style_positive_json=["原创二维柔和数字插画", "暖灰轮廓线"],
        style_negative_json=list(CANON_V4_STYLE_NEGATIVE),
        reference_bindings_json=[],
        reference_snapshot_json=[],
    )
    story = StoryRevisionRecord(
        id=uuid.uuid4(),
        production_run_id=profile.production_run_id,
        revision=1,
        strategy="creative_text",
        status="approved",
        title="纸星星",
        logline="孩子与猫咪把纸星星贴到窗上。",
        synopsis="孩子发现纸星星，猫咪推回，两者一起贴到玻璃上。",
        subject_ids_json=[],
        scene_plan_json=[],
        episode_rules_json={
            "personWardrobe": "本集批准服装保持一致",
            "timeWeather": "清晨柔光",
            "mainScene": "窗边",
            "coreProps": ["纸星星"],
        },
    )
    scene = Scene(
        id=uuid.uuid4(),
        production_run_id=profile.production_run_id,
        sort_order=1,
        title="清晨窗边",
        source_text="孩子与猫咪在窗边迎接阳光。",
        status="ready",
        look_plan_json={},
        look_draft_json={},
        look_draft_revision=1,
        active=True,
    )
    prompt = _compile_storyboard_prompt_text(
        profile=profile,
        story=story,
        scene=scene,
        shot={
            "order": 1,
            "title": "贴上纸星星",
            "durationSeconds": 8,
            "direction": "孩子接过猫咪推回的纸星星，贴到玻璃上，阳光照亮一人一猫。",
            "directorShots": [
                {
                    "startSecond": 0,
                    "endSecond": 8,
                    "childAction": "贴上纸星星",
                    "catAction": "四足站立陪伴",
                }
            ],
        },
        reference_bindings=[
            {
                "title": "character-design:89a6a873-bf9d-4f7a-9823-a57b29f9c510:child:candidate:1",
                "purpose": "child",
                "role": "appearance",
                "providerIncluded": True,
                "authority": {"role": "episode_appearance"},
            },
            {
                "title": "纯画风板",
                "purpose": CANON_V4_STYLE_BOARD_KEY,
                "role": "style",
                "providerIncluded": True,
                "authority": {"role": "style_board"},
            },
        ],
        healing_recipe=True,
    )

    assert "@图片1" in prompt and "当前唯一身份与本集造型来源" in prompt
    assert "本集儿童设计" in prompt
    assert "89a6a873-bf9d-4f7a-9823-a57b29f9c510" not in prompt
    assert "@图片2" in prompt and "只锁定轮廓线、材质、色阶与渲染语言" in prompt
    assert "canonProfileId" not in prompt
    assert "episodeRules" not in prompt
    assert "禁止绿色" not in prompt
    assert "禁止叶片" not in prompt
    assert "Sowii" not in prompt and "蘑菇秃秃" not in prompt


def test_video_prompt_uses_current_director_window_once_instead_of_stale_clip_text() -> None:
    profile = VisualProfileRevision(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        revision=4,
        profile_hash="d" * 64,
        source_profile_id=CANON_V4_PROFILE_ID,
        person_identity="固定柔和圆脸和五官",
        person_hair="固定深棕黑色齐下颌短发",
        person_body="固定 8–9 岁儿童身体比例",
        cat_identity="固定灰白分区、主要虎斑、四足结构和尾巴环纹",
        style_positive_json=["原创二维柔和数字插画"],
        style_negative_json=list(CANON_V4_STYLE_NEGATIVE),
        reference_bindings_json=[],
        reference_snapshot_json=[],
    )
    story = StoryRevisionRecord(
        id=uuid.uuid4(),
        production_run_id=profile.production_run_id,
        revision=1,
        strategy="creative_text",
        status="approved",
        title="纸星星",
        logline="孩子与猫咪把纸星星贴到窗上。",
        synopsis="孩子发现纸星星，猫咪推回，两者一起贴到玻璃上。",
        subject_ids_json=[],
        scene_plan_json=[],
        episode_rules_json={"personWardrobe": "米色上衣与棕色背带裤"},
    )
    scene = Scene(
        id=uuid.uuid4(),
        production_run_id=profile.production_run_id,
        sort_order=1,
        title="清晨窗边",
        source_text="孩子与猫咪在窗边迎接阳光。",
        status="ready",
        look_plan_json={},
        look_draft_json={},
        look_draft_revision=1,
        active=True,
    )
    current_direction = "儿童保持齐下颌短发和批准服装，猫咪用鼻尖推回纸星星。"

    prompt = _compile_storyboard_prompt_text(
        profile=profile,
        story=story,
        scene=scene,
        shot={
            "order": 1,
            "title": "贴上纸星星",
            "durationSeconds": 8,
            "direction": "旧分镜要求儿童穿宽松棉麻睡衣。",
            "directorShots": [
                {
                    "startSecond": 0,
                    "endSecond": 8,
                    "title": "贴上纸星星",
                    "direction": current_direction,
                    "childAction": "该字段不应在完整 direction 后重复拼接",
                }
            ],
        },
        reference_bindings=[],
        healing_recipe=True,
    )

    assert "宽松棉麻睡衣" not in prompt
    assert prompt.count(current_direction) == 1
    assert "该字段不应在完整 direction 后重复拼接" not in prompt
