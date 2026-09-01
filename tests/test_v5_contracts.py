from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain import contracts
from cat_video_generator.domain.prompts import (
    PromptCompilationError,
    compile_shot_video_prompt_parts,
)
from cat_video_generator.domain.rendering import (
    MediaSource,
    ProjectSequencePlan,
    ProviderMediaRole,
    SequenceClip,
    SequenceTransition,
    build_shot_input_plan,
)
from cat_video_generator.interfaces.api_schemas import (
    AcceptSuggestionsRequest,
    BuildSequenceRequest,
)


def test_v5_contract_defaults_are_backward_compatible() -> None:
    scene = contracts.SceneDraft(title="出门前", sourceText="人物和猫咪准备出门。")
    shot = contracts.ShotCardDraft(title="猫咪等候", direction="1. 中景，猫咪在门边等候。")

    assert contracts.CURRENT_CONTRACT_VERSION == 5
    assert scene.story_mode is contracts.StoryMode.SINGLE
    assert scene.target_shot_count == 1
    assert scene.look_plan is None
    assert shot.inherit_project_references is True
    assert shot.scene_look_usage is contracts.SceneLookUsage.OFF
    assert shot.use_scene_look is False


def test_accept_suggestions_contract_defaults_and_parses_shot_revisions() -> None:
    shot_id = uuid.uuid4()
    request = AcceptSuggestionsRequest.model_validate(
        {
            "lookPlan": None,
            "shots": [
                {
                    "title": "片段",
                    "direction": "1. 中景建立。\n2. 稳定收尾。",
                    "suggestedDurationSeconds": 10,
                }
            ],
            "applyMode": "update_existing",
            "sourceShotRevisions": {str(shot_id): 3},
        }
    )

    assert request.source_shot_revisions == {shot_id: 3}
    assert request.shots[0].anchor_mode is contracts.AnchorMode.TEXT_ONLY
    assert (
        request.shots[0].scene_look_usage
        is contracts.SceneLookUsage.OFF
    )


def test_shot_suggestion_accepts_visual_strategy_and_validates_derive_anchor() -> None:
    suggestion = contracts.ShotSuggestion(
        title="准备开柜",
        direction="1. 中景，小孩准备打开柜门。",
        suggestedDurationSeconds=10,
        anchorMode="generate",
        sceneLookUsage="derive_anchor",
    )

    assert suggestion.anchor_mode is contracts.AnchorMode.GENERATE
    assert suggestion.scene_look_usage is contracts.SceneLookUsage.DERIVE_ANCHOR

    with pytest.raises(ValidationError, match="derive_anchor"):
        contracts.ShotSuggestion(
            title="准备开柜",
            direction="1. 中景，小孩准备打开柜门。",
            anchorMode="text_only",
            sceneLookUsage="derive_anchor",
        )


def test_legacy_scene_look_boolean_maps_to_authoritative_usage() -> None:
    disabled = contracts.ShotCardDraft(
        title="猫咪观察",
        direction="1. 中景，猫咪观察门边。",
        useSceneLook=False,
    )
    explicit = contracts.ShotCardDraft(
        title="猫咪观察",
        direction="1. 中景，猫咪观察门边。",
        sceneLookUsage="full_reference",
        useSceneLook=False,
    )

    assert disabled.scene_look_usage is contracts.SceneLookUsage.OFF
    assert disabled.use_scene_look is False
    assert explicit.scene_look_usage is contracts.SceneLookUsage.FULL_REFERENCE
    assert explicit.use_scene_look is True


def test_derive_anchor_requires_generate_anchor_mode() -> None:
    with pytest.raises(ValidationError, match="derive_anchor"):
        contracts.ShotCardDraft(
            title="猫咪观察",
            direction="1. 中景，猫咪观察门边。",
            sceneLookUsage="derive_anchor",
            anchorMode="text_only",
        )

    draft = contracts.ShotCardDraft(
        title="猫咪观察",
        direction="1. 中景，猫咪观察门边。",
        sceneLookUsage="derive_anchor",
        anchorMode="generate",
    )
    assert draft.scene_look_usage is contracts.SceneLookUsage.DERIVE_ANCHOR


@pytest.mark.parametrize(
    ("story_mode", "target_shot_count"),
    [
        ("single", 2),
        ("multi", 1),
        ("multi", 7),
    ],
)
def test_scene_mode_rejects_incompatible_shot_count(
    story_mode: str, target_shot_count: int
) -> None:
    with pytest.raises(ValidationError):
        contracts.SceneDraft(
            title="出门前",
            sourceText="人物和猫咪准备出门。",
            storyMode=story_mode,
            targetShotCount=target_shot_count,
        )


def test_multi_scene_accepts_user_selected_shot_count() -> None:
    scene = contracts.SceneDraft(
        title="一天的准备",
        sourceText="人物和猫咪依次收拾装备。",
        storyMode="multi",
        targetShotCount=6,
    )

    assert scene.story_mode is contracts.StoryMode.MULTI
    assert scene.target_shot_count == 6


def test_scene_look_plan_is_strict_and_serializes_with_public_aliases() -> None:
    plan = contracts.SceneLookPlan(
        personWardrobe="浅色外套",
        personAccessories="帆布包",
        catAppearance="保持 Canon 外观",
        keyProps="钓鱼竿、小水桶",
        imageRecommended=True,
        recommendationReason="服装和关键道具贯穿整个场景",
    )

    assert plan.model_dump(mode="json", by_alias=True) == {
        "personWardrobe": "浅色外套",
        "personAccessories": "帆布包",
        "catAppearance": "保持 Canon 外观",
        "keyProps": "钓鱼竿、小水桶",
        "environmentStyle": "outdoor",
        "personPose": "",
        "catPose": "",
        "composition": "",
        "additionalInstructions": "",
        "imageRecommended": True,
        "recommendationReason": "服装和关键道具贯穿整个场景",
    }
    with pytest.raises(ValidationError):
        contracts.SceneLookPlan(
            personWardrobe="",
            personAccessories="",
            catAppearance="",
            keyProps="",
            unexpected="not allowed",
        )


def test_visual_profile_defaults_are_editable_and_reference_purposes_are_strict() -> None:
    profile = contracts.VisualProfileDraft(
        personBody="保持五至七岁儿童体型",
        referenceBindings=[
            {
                "assetId": str(uuid.uuid4()),
                "purpose": "person_identity",
                "instruction": "锁定人物脸型",
            }
        ],
    )

    assert "五至七岁" in profile.person_body
    assert profile.reference_bindings[0].purpose is contracts.LookReferencePurpose.PERSON_IDENTITY
    with pytest.raises(ValidationError, match="只允许人物、猫咪和画风"):
        contracts.VisualProfileDraft(
            referenceBindings=[
                {"assetId": str(uuid.uuid4()), "purpose": "wardrobe"}
            ]
        )


def _image_source(index: int) -> MediaSource:
    return MediaSource(
        asset_id=uuid.uuid4(),
        semantic_key=f"reference:{index}",
        media_type="image",
        sha256=f"{index:064x}",
        metadata={},
    )


@pytest.mark.parametrize("resolution", ["480p", "720p"])
def test_v5_video_input_contract_allows_anchor_as_only_media(resolution: str) -> None:
    anchor = _image_source(1)

    plan = build_shot_input_plan(
        resolution=resolution,
        duration_seconds=10,
        anchor=anchor,
    )

    assert len(plan.bindings) == 1
    assert plan.bindings[0].provider_role is ProviderMediaRole.FIRST_FRAME


def test_v5_first_frame_video_prompt_does_not_rewrite_identity_or_style() -> None:
    plan = build_shot_input_plan(
        resolution="720p",
        duration_seconds=10,
        anchor=_image_source(1),
    )
    context = contracts.ShotPromptContext(
        project_title="雨后亮叶",
        scene_title="雨后小院",
        scene_text="孩子和猫咪观察叶片上的雨珠。",
        shot_title="靠近亮叶",
        direction="1. 孩子缓慢蹲下，猫咪自然四足靠近。2. 镜头轻微推进，雨珠滚动。",
        duration_seconds=10,
    )
    profile = contracts.VisualProfileDraft(
        personIdentity="IDENTITY_SENTINEL_PERSON",
        personHair="HAIR_SENTINEL",
        personBody="BODY_SENTINEL",
        catIdentity="IDENTITY_SENTINEL_CAT",
        stylePositive=("STYLE_SENTINEL_A", "STYLE_SENTINEL_B", "STYLE_SENTINEL_C"),
        styleNegative=("NEGATIVE_SENTINEL_A", "NEGATIVE_SENTINEL_B"),
    )

    prompt = compile_shot_video_prompt_parts(
        context,
        plan,
        binding_descriptions=("@图片1=已批准开场锚点",),
        visual_profile=profile,
    ).final.text

    assert "唯一的人物身份、猫咪身份、外观、比例、构图和画风来源" in prompt
    assert "不得重写、重构或补充人物、猫咪与画风特征" in prompt
    assert "孩子缓慢蹲下" in prompt
    assert "IDENTITY_SENTINEL" not in prompt
    assert "STYLE_SENTINEL" not in prompt
    assert "NEGATIVE_SENTINEL" not in prompt


def test_legacy_web_sequence_request_accepts_intro_and_outro_fades() -> None:
    request = BuildSequenceRequest.model_validate(
        {
            "transitions": [],
            "introTransition": {"type": "fade_black", "durationMs": 400},
            "outroTransition": {"type": "fade_black", "durationMs": 400},
        }
    )

    assert request.intro_transition == SequenceTransition(
        type="fade_black",
        durationMs=400,
    )
    assert request.outro_transition == SequenceTransition(
        type="fade_black",
        durationMs=400,
    )


def test_v5_video_input_contract_rejects_reference_media_with_first_frame() -> None:
    with pytest.raises(ValueError, match="首帧模式不能同时提交普通参考图片"):
        build_shot_input_plan(
            resolution="480p",
            duration_seconds=10,
            anchor=_image_source(1),
            references=(_image_source(2),),
        )


def test_v5_video_input_contract_preserves_explicit_first_and_last_frames() -> None:
    plan = build_shot_input_plan(
        resolution="720p",
        duration_seconds=10,
        anchor=_image_source(1),
        last_frame=_image_source(2),
    )

    assert [item.provider_role for item in plan.bindings] == [
        ProviderMediaRole.FIRST_FRAME,
        ProviderMediaRole.LAST_FRAME,
    ]


def test_v5_video_reference_mode_rejects_tenth_image() -> None:
    with pytest.raises(ValueError, match="最多允许9项参考素材"):
        build_shot_input_plan(
            resolution="480p",
            duration_seconds=10,
            anchor=None,
            references=tuple(_image_source(index) for index in range(1, 11)),
        )


def test_semantic_material_links_follow_the_actual_image_order() -> None:
    plan = build_shot_input_plan(
        resolution="480p",
        duration_seconds=10,
        anchor=None,
        references=(_image_source(1), _image_source(2)),
    )
    context = contracts.ShotPromptContext(
        project_title="湖泊钓鱼",
        scene_title="出发准备",
        scene_text="小孩和猫咪准备钓具。",
        shot_title="取出装备",
        direction="1. {{人物}}拿起{{道具:伸缩鱼竿}}，{{猫咪}}在脚边观察。",
        duration_seconds=10,
    )
    aliases = {
        "人物": "人物“小孩”@图片1",
        "道具:伸缩鱼竿": "道具“伸缩鱼竿”@图片2",
    }

    preview = compile_shot_video_prompt_parts(
        context,
        plan,
        binding_descriptions=("@图片1=人物身份", "@图片2=伸缩鱼竿"),
        semantic_aliases=aliases,
        strict_semantic_links=False,
    )

    assert "人物“小孩”@图片1" in preview.creative_body
    assert "道具“伸缩鱼竿”@图片2" in preview.creative_body
    assert "猫咪（未绑定图片）" in preview.creative_body
    assert preview.link_warnings == ("语义素材“猫咪”尚未绑定可用图片",)
    with pytest.raises(PromptCompilationError, match="猫咪"):
        compile_shot_video_prompt_parts(
            context,
            plan,
            binding_descriptions=("@图片1=人物身份", "@图片2=伸缩鱼竿"),
            semantic_aliases=aliases,
        )


def test_prompt_compiler_rejects_a_ghost_provider_alias() -> None:
    plan = build_shot_input_plan(
        resolution="480p",
        duration_seconds=10,
        anchor=None,
        references=(_image_source(1),),
    )
    context = contracts.ShotPromptContext(
        project_title="项目",
        scene_title="场景",
        scene_text="准备出发。",
        shot_title="片段",
        direction="1. 人物参考@图片2拿起装备。",
        duration_seconds=10,
    )

    with pytest.raises(PromptCompilationError, match="@图片2"):
        compile_shot_video_prompt_parts(
            context,
            plan,
            binding_descriptions=("@图片1=人物身份",),
        )


def test_sequence_timeline_supports_cut_fade_and_cross_dissolve() -> None:
    shot_ids = [uuid.uuid4() for _ in range(3)]
    asset_ids = [uuid.uuid4() for _ in range(3)]
    clips = [
        SequenceClip(
            order=1,
            shot_card_id=shot_ids[0],
            source_asset_id=asset_ids[0],
            source_start_ms=0,
            source_end_ms=10_000,
            timeline_start_ms=0,
            timeline_end_ms=10_000,
        ),
        SequenceClip(
            order=2,
            shot_card_id=shot_ids[1],
            source_asset_id=asset_ids[1],
            source_start_ms=0,
            source_end_ms=10_000,
            timeline_start_ms=10_000,
            timeline_end_ms=20_000,
            transitionFromPrevious={"type": "fade_black", "durationMs": 300},
        ),
        SequenceClip(
            order=3,
            shot_card_id=shot_ids[2],
            source_asset_id=asset_ids[2],
            source_start_ms=0,
            source_end_ms=10_000,
            timeline_start_ms=19_700,
            timeline_end_ms=29_700,
            transitionFromPrevious={"type": "cross_dissolve", "durationMs": 300},
        ),
    ]

    plan = ProjectSequencePlan(
        duration_ms=29_700,
        clips=clips,
        introTransition={"type": "fade_black", "durationMs": 500},
        outroTransition={"type": "fade_black", "durationMs": 500},
    )

    assert plan.clips[0].transition_from_previous is None
    assert plan.intro_transition == SequenceTransition(
        type="fade_black",
        durationMs=500,
    )
    assert plan.outro_transition == SequenceTransition(
        type="fade_black",
        durationMs=500,
    )
    assert plan.clips[1].transition_from_previous == SequenceTransition(
        type="fade_black",
        durationMs=300,
    )
    assert plan.clips[2].timeline_start_ms == 19_700


def test_sequence_boundary_transitions_reject_cross_dissolve() -> None:
    clip = SequenceClip(
        order=1,
        shot_card_id=uuid.uuid4(),
        source_asset_id=uuid.uuid4(),
        source_start_ms=0,
        source_end_ms=8_000,
        timeline_start_ms=0,
        timeline_end_ms=8_000,
    )

    with pytest.raises(ValidationError):
        ProjectSequencePlan(
            duration_ms=8_000,
            clips=[clip],
            introTransition={"type": "cross_dissolve", "durationMs": 500},
        )


def test_sequence_rejects_timeline_that_ignores_dissolve_overlap() -> None:
    with pytest.raises(ValidationError, match="时间轴起点"):
        ProjectSequencePlan(
            duration_ms=20_000,
            clips=[
                SequenceClip(
                    order=1,
                    shot_card_id=uuid.uuid4(),
                    source_asset_id=uuid.uuid4(),
                    source_start_ms=0,
                    source_end_ms=10_000,
                    timeline_start_ms=0,
                    timeline_end_ms=10_000,
                ),
                SequenceClip(
                    order=2,
                    shot_card_id=uuid.uuid4(),
                    source_asset_id=uuid.uuid4(),
                    source_start_ms=0,
                    source_end_ms=10_000,
                    timeline_start_ms=10_000,
                    timeline_end_ms=20_000,
                    transitionFromPrevious={"type": "cross_dissolve", "durationMs": 300},
                ),
            ],
        )
