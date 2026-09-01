from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from cat_video_generator.domain.aigc_canvas import (
    CanvasConnection,
    CanvasNodeType,
    CanvasPortType,
    NodeGenerationConfigDraft,
    SubjectDraft,
)
from cat_video_generator.domain.universal_canvas import (
    AnnotationTool,
    CanvasTemplateKey,
    ProviderEditCapability,
    VideoEditAnnotation,
    VideoEditRecipeDraft,
    compile_video_edit_plan,
    template_spec,
)


def test_product_subject_and_reference_semantics_are_first_class() -> None:
    subject = SubjectDraft(
        name="蓝色汽水罐",
        kind="product",
        role="hero_product",
        identityAnchors=["蓝色罐身、红白圆形标志、330ml"],
        immutableTraits=["标签文字和罐体比例不能改变"],
        references=[
            {
                "assetId": str(uuid.uuid4()),
                "semanticRole": "packshot_front",
                "instruction": "保持正面包装结构",
            }
        ],
    )

    assert subject.kind.value == "product"
    assert subject.role.value == "hero_product"
    assert subject.references[0].semantic_role == "packshot_front"


def test_product_template_defaults_to_four_candidates_without_story_requirements() -> None:
    spec = template_spec(CanvasTemplateKey.PRODUCT_AD)

    assert spec.default_candidate_count == 4
    assert 1 <= spec.default_candidate_count <= 8
    assert "GenerationBatchNode" in spec.node_types
    assert "StoryPlannerNode" not in spec.node_types


def test_universal_canvas_accepts_reference_to_batch_and_video_to_edit_edges() -> None:
    reference_edge = CanvasConnection(
        sourceNodeId=uuid.uuid4(),
        sourceNodeType=CanvasNodeType.REFERENCE_ASSET,
        sourcePort=CanvasPortType.MEDIA_REFERENCES,
        targetNodeId=uuid.uuid4(),
        targetNodeType=CanvasNodeType.GENERATION_BATCH,
        targetPort=CanvasPortType.MEDIA_REFERENCES,
    )
    edit_edge = CanvasConnection(
        sourceNodeId=uuid.uuid4(),
        sourceNodeType=CanvasNodeType.VIDEO_ASSET,
        sourcePort=CanvasPortType.VIDEO_ASSET,
        targetNodeId=uuid.uuid4(),
        targetNodeType=CanvasNodeType.VIDEO_EDIT,
        targetPort=CanvasPortType.VIDEO_ASSET,
    )

    assert reference_edge.target_node_type is CanvasNodeType.GENERATION_BATCH
    assert edit_edge.target_node_type is CanvasNodeType.VIDEO_EDIT


def test_generation_nodes_accept_subjects_and_general_media_references() -> None:
    subject_edge = CanvasConnection(
        sourceNodeId=uuid.uuid4(),
        sourceNodeType=CanvasNodeType.SUBJECT,
        sourcePort=CanvasPortType.SUBJECTS,
        targetNodeId=uuid.uuid4(),
        targetNodeType=CanvasNodeType.IMAGE_GENERATION,
        targetPort=CanvasPortType.SUBJECTS,
    )
    reference_edge = CanvasConnection(
        sourceNodeId=uuid.uuid4(),
        sourceNodeType=CanvasNodeType.REFERENCE_ASSET,
        sourcePort=CanvasPortType.MEDIA_REFERENCES,
        targetNodeId=uuid.uuid4(),
        targetNodeType=CanvasNodeType.VIDEO_GENERATION,
        targetPort=CanvasPortType.MEDIA_REFERENCES,
    )

    assert subject_edge.target_port is CanvasPortType.SUBJECTS
    assert reference_edge.target_port is CanvasPortType.MEDIA_REFERENCES


def test_node_generation_config_preserves_camera_motion_annotations_and_provider_slot() -> None:
    config = NodeGenerationConfigDraft(
        provider="ark",
        model="seedance-2",
        mode="image_to_video",
        aspectRatio="9:16",
        resolution="480p",
        durationSeconds=8,
        candidateCount=1,
        draftPrompt="保持包装文字并缓慢推近",
        cameraMotion="push_in",
        referenceAnnotations=[{
            "assetId": str(uuid.uuid4()),
            "tool": "rectangle",
            "points": [{"x": 0.1, "y": 0.2}, {"x": 0.5, "y": 0.7}],
            "label": "保持标签文字",
        }],
        actualReferences=[{
            "assetId": str(uuid.uuid4()),
            "semanticRole": "packshot_front",
            "providerIncluded": True,
            "providerSlot": "reference_image_1",
        }],
    )

    document = config.model_dump(by_alias=True, mode="json")
    assert document["draftPrompt"] == "保持包装文字并缓慢推近"
    assert document["cameraMotion"] == "push_in"
    assert document["referenceAnnotations"][0]["tool"] == "rectangle"
    assert document["actualReferences"][0]["providerSlot"] == "reference_image_1"


def test_video_edit_recipe_enforces_one_provider_sized_interval_and_normalized_marks() -> None:
    recipe = VideoEditRecipeDraft(
        projectId=uuid.uuid4(),
        sourceAssetId=uuid.uuid4(),
        startMs=4_000,
        endMs=10_000,
        instruction="保持产品标签不变，让人物手指离开标志区域",
        referenceAssetIds=[uuid.uuid4()],
        annotations=[
            VideoEditAnnotation(
                frameTimestampMs=5_000,
                tool=AnnotationTool.RECTANGLE,
                points=[{"x": 0.25, "y": 0.2}, {"x": 0.65, "y": 0.72}],
                label="需要修复的手部区域",
            )
        ],
    )

    assert recipe.duration_ms == 6_000
    assert recipe.annotations[0].coordinate_space == "source_normalized"
    assert recipe.model_dump(mode="json", by_alias=True)["annotations"][0][
        "frameTimestampMs"
    ] == 5_000

    with pytest.raises(ValidationError, match="0.5 至 13 秒"):
        VideoEditRecipeDraft(
            projectId=uuid.uuid4(),
            sourceAssetId=uuid.uuid4(),
            startMs=0,
            endMs=14_000,
            instruction="修改整个视频",
        )

    with pytest.raises(ValidationError):
        VideoEditAnnotation(
            frameTimestampMs=1_000,
            tool="rectangle",
            points=[{"x": 1.2, "y": 0.2}, {"x": 0.5, "y": 0.5}],
        )


def test_new_video_edit_recipe_never_hides_two_image_calls_behind_compilation() -> None:
    recipe = VideoEditRecipeDraft(
        projectId=uuid.uuid4(),
        sourceAssetId=uuid.uuid4(),
        startMs=4_000,
        endMs=10_000,
        instruction="保持产品标签并修复手部",
        referenceAssetIds=[uuid.uuid4()],
        annotations=[
            {
                "frameTimestampMs": 5_000,
                "tool": "rectangle",
                "points": [{"x": 0.2, "y": 0.2}, {"x": 0.6, "y": 0.7}],
            }
        ],
    )
    capability = ProviderEditCapability(
        provider="ark",
        model="seedance-test",
        supportsDirectAnnotations=False,
        maxDirectReferenceImages=0,
        supportsControlAnchors=True,
        imageCallCostMicros=1_500,
        videoCallCostMicros=8_000,
    )

    with pytest.raises(
        ValueError,
        match="无法直接接收当前标注和参考素材",
    ):
        compile_video_edit_plan(recipe, capability)


def test_direct_video_edit_compiles_to_zero_image_calls_and_one_video_call() -> None:
    recipe = VideoEditRecipeDraft(
        projectId=uuid.uuid4(),
        sourceAssetId=uuid.uuid4(),
        startMs=4_000,
        endMs=10_000,
        instruction="保持身份，只调整选中区间内的动作",
        referenceAssetIds=[uuid.uuid4()],
    )
    capability = ProviderEditCapability(
        provider="ark",
        model="seedance-test",
        supportsDirectAnnotations=False,
        maxDirectReferenceImages=1,
        supportsControlAnchors=True,
        imageCallCostMicros=1_500,
        videoCallCostMicros=8_000,
    )

    plan = compile_video_edit_plan(recipe, capability)

    assert plan.mode == "direct"
    assert plan.image_call_count == 0
    assert plan.video_call_count == 1
    assert plan.estimated_cost_micros == 8_000
    assert [stage.kind for stage in plan.stages] == ["video_edit"]
