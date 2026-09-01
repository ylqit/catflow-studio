from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.production_recipes import (
    HEALING_CHILD_CAT_RECIPE,
    CatBehaviorMode,
    EpisodeRules,
    HumanReviewDecision,
    HumanReviewDraft,
    QualityTier,
    RecipeSequenceRunRequest,
    SoundPlan,
    build_temporal_beats,
    canon_reference_keys,
    canon_v2_reference_keys,
    recipe_task_source_hash,
    split_shot_durations,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_recipe_task_source_hash_fixes_payload_version_and_phase() -> None:
    instance_id = uuid.uuid4()
    first = recipe_task_source_hash(
        payload={"theme": "雨后亮叶", "duration": 15},
        instance_id=instance_id,
        expected_revision=3,
        phase="story",
    )
    reordered = recipe_task_source_hash(
        payload={"duration": 15, "theme": "雨后亮叶"},
        instance_id=instance_id,
        expected_revision=3,
        phase="story",
    )
    changed = recipe_task_source_hash(
        payload={"duration": 16, "theme": "雨后亮叶"},
        instance_id=instance_id,
        expected_revision=3,
        phase="story",
    )

    assert first == reordered
    assert first != changed


@pytest.mark.parametrize(
    ("total", "expected"),
    (
        (8, (8,)),
        (15, (15,)),
        (16, (8, 8)),
        (30, (15, 15)),
        (31, (11, 10, 10)),
        (45, (15, 15, 15)),
        (60, (15, 15, 15, 15)),
    ),
)
def test_healing_recipe_balances_duration_into_provider_sized_shots(
    total: int,
    expected: tuple[int, ...],
) -> None:
    durations = split_shot_durations(total)

    assert durations == expected
    assert sum(durations) == total
    assert all(8 <= item <= 15 for item in durations)


@pytest.mark.parametrize("total", (7, 61))
def test_healing_recipe_rejects_unsupported_total_duration(total: int) -> None:
    with pytest.raises(ValueError, match="8至60秒"):
        split_shot_durations(total)


def test_healing_recipe_has_fixed_defaults_and_quality_candidate_counts() -> None:
    recipe = HEALING_CHILD_CAT_RECIPE

    assert recipe.key.value == "healing_child_cat_v1"
    assert recipe.default_duration_seconds == 15
    assert recipe.aspect_ratio == "9:16"
    assert recipe.resolution == "720p"
    assert recipe.story_candidate_count == 3
    assert recipe.quality_tiers[QualityTier.QUICK].anchor_candidate_count == 1
    assert recipe.quality_tiers[QualityTier.QUICK].character_design_candidate_count == 1
    assert recipe.quality_tiers[QualityTier.BALANCED].anchor_candidate_count == 2
    assert recipe.quality_tiers[QualityTier.BALANCED].character_design_candidate_count == 2
    assert recipe.quality_tiers[QualityTier.PREMIUM].anchor_candidate_count == 4
    assert recipe.quality_tiers[QualityTier.PREMIUM].character_design_candidate_count == 4
    assert recipe.quality_tiers[QualityTier.PREMIUM].video_candidate_count == 2


def test_temporal_beats_are_three_ordered_actions_covering_the_whole_shot() -> None:
    beats = build_temporal_beats(
        15,
        actions=(
            ("孩子摆好小碗", "猫咪在一旁观察", "固定中景"),
            ("孩子发现一片落叶", "猫咪轻碰落叶", "缓慢推近"),
            ("孩子把落叶放进书中", "猫咪安静趴下", "停在温暖近景"),
        ),
    )

    assert [item.phase.value for item in beats] == ["beginning", "change", "warm_ending"]
    assert beats[0].start_second == 0
    assert beats[-1].end_second == 15
    assert all(
        left.end_second == right.start_second for left, right in zip(beats, beats[1:], strict=False)
    )
    assert all(item.child_action and item.cat_action and item.camera for item in beats)


def test_temporal_beats_require_exactly_three_explicit_actions() -> None:
    with pytest.raises(ValueError, match="三个动作节拍"):
        build_temporal_beats(
            12,
            actions=(("开始", "观察", "固定"), ("结束", "趴下", "近景")),
        )


def test_episode_rules_lock_cat_mode_and_disallow_dialogue() -> None:
    rules = EpisodeRules(
        personWardrobe="米白短袖与深蓝短裤",
        timeWeather="初夏雨后清晨",
        mainScene="木屋窗边",
        environment="indoor",
        coreProps=["小碗", "落叶"],
        catBehaviorMode=CatBehaviorMode.NATURAL,
        soundPlan=SoundPlan(
            ambient=["细雨", "窗外树叶声"],
            foley=["瓷碗轻响", "猫爪触碰纸面"],
            musicMood="轻柔木吉他",
            dialoguePolicy="none",
        ),
        stylePositive=["日系二维治愈插画", "柔和哑光水彩", "温和自然光"],
        styleExcluded=["真人写实摄影", "CG或PBR三维材质"],
        canonProfileId="canon-v2-healing-child-cat",
    )

    assert rules.cat_behavior_mode is CatBehaviorMode.NATURAL
    assert rules.environment == "indoor"
    assert rules.sound_plan.dialogue_policy == "none"

    with pytest.raises(ValidationError):
        SoundPlan(
            ambient=["雨声"],
            foley=["脚步"],
            musicMood="轻柔",
            dialoguePolicy="allowed",  # type: ignore[arg-type]
        )


def test_human_review_requires_explicit_resolution_for_blocking_diagnostics() -> None:
    target_id = uuid.uuid4()
    target_hash = "a" * 64

    with pytest.raises(ValidationError, match="普通批准"):
        HumanReviewDraft(
            targetType="video_asset",
            targetId=target_id,
            targetHash=target_hash,
            decision=HumanReviewDecision.APPROVE,
            blockingDiagnosticPresent=True,
        )

    with pytest.raises(ValidationError, match="覆盖理由"):
        HumanReviewDraft(
            targetType="video_asset",
            targetId=target_id,
            targetHash=target_hash,
            decision=HumanReviewDecision.OVERRIDE,
            blockingDiagnosticPresent=True,
            reason=" ",
        )

    with pytest.raises(ValidationError, match="修改原因"):
        HumanReviewDraft(
            targetType="video_asset",
            targetId=target_id,
            targetHash=target_hash,
            decision=HumanReviewDecision.REQUEST_CHANGES,
        )

    accepted = HumanReviewDraft(
        targetType="video_asset",
        targetId=target_id,
        targetHash=target_hash,
        decision=HumanReviewDecision.OVERRIDE,
        blockingDiagnosticPresent=True,
        reason="抽帧误把窗帘阴影识别成人物轮廓变化",
    )
    assert accepted.target_hash == target_hash


def test_recipe_sequence_transitions_are_optional_cuts_or_300_to_1000ms_fades() -> None:
    shot_id = uuid.uuid4()
    request = RecipeSequenceRunRequest(
        idempotencyKey="sequence-run-0001",
        acceptEstimatedCostMicros=0,
        transitions=[
            {
                "afterShotId": shot_id,
                "transition": {"type": "cross_dissolve", "durationMs": 500},
            }
        ],
    )

    assert request.transitions[0].after_shot_id == shot_id
    assert request.transitions[0].transition.duration_ms == 500

    with pytest.raises(ValidationError, match="300至1000毫秒"):
        RecipeSequenceRunRequest(
            idempotencyKey="sequence-run-0002",
            acceptEstimatedCostMicros=0,
            transitions=[
                {
                    "afterShotId": shot_id,
                    "transition": {"type": "fade_black", "durationMs": 200},
                }
            ],
        )


@pytest.mark.parametrize(
    ("environment", "style_key"),
    (("indoor", "style:indoor"), ("outdoor", "style:outdoor")),
)
def test_canon_v2_uses_one_watercolor_environment_style(
    environment: str,
    style_key: str,
) -> None:
    keys = canon_v2_reference_keys(environment)

    assert keys == (
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        style_key,
    )
    assert "style:line_texture" not in keys


@pytest.mark.parametrize("environment", ("indoor", "outdoor"))
def test_canon_v3_uses_fixed_identity_and_one_line_texture_style(environment: str) -> None:
    keys = canon_reference_keys("canon-v3-healing-child-cat-line-texture", environment)

    assert keys == (
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        "style:line_texture",
    )
    assert "style:indoor" not in keys
    assert "style:outdoor" not in keys


def test_canon_v3_manifest_keeps_style_reference_responsibility_narrow() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "风格定稿" / "Canon-v3" / "manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["profileId"] == "canon-v3-healing-child-cat-line-texture"
    assert manifest["requiredKeys"] == [
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        "style:line_texture",
    ]
    assert "style:indoor" not in manifest["requiredKeys"]
    assert "style:outdoor" not in manifest["requiredKeys"]
    assert "叶片" in manifest["styleReferenceInstruction"]
    assert "绿色" in manifest["styleReferenceInstruction"]


def test_golden_sample_manifest_covers_ten_original_indoor_outdoor_modes() -> None:
    cases = json.loads(
        (PROJECT_ROOT / "tests" / "golden_healing_child_cat_cases.json").read_text(encoding="utf-8")
    )

    assert len(cases) == 10
    assert {item["environment"] for item in cases} == {"indoor", "outdoor"}
    assert {item["catBehaviorMode"] for item in cases} == {
        "natural",
        "light_anthropomorphic",
    }
    assert all(item["maxPaidVideoAttemptsPerShot"] == 2 for item in cases)
    assert all(
        sum(split_shot_durations(item["durationSeconds"])) == item["durationSeconds"]
        for item in cases
    )
