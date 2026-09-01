from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from cat_video_generator.application.ports import StoredScene, StoredStep
from cat_video_generator.application.shot_queue import ProjectEditingService
from cat_video_generator.domain.contracts import (
    SceneDraft,
    SceneLookPlan,
    ShotSuggestion,
    ShotSuggestionOutput,
)
from cat_video_generator.domain.creative_workflow import story_source_hash
from cat_video_generator.domain.prompts import (
    compile_scene_look_prompt,
    compile_shot_suggestion_prompt,
)
from cat_video_generator.domain.workflow import SceneStatus, StepKind, StepStatus


def test_director_prompt_defines_clip_count_subshots_and_cat_led_interaction() -> None:
    prompt = compile_shot_suggestion_prompt(
        project_title="湖边日常",
        scene_title="出门准备",
        source_text="人物收拾钓具，猫咪在旁边等候并跟着出门。",
        context_note=None,
        story_mode="multi",
        target_shot_count=4,
    )

    assert "严格输出4个视频片段" in prompt
    assert "2至4个编号子镜头" in prompt
    assert "猫咪是主要观察和行动对象" in prompt
    assert "人物负责需要手部或工具" in prompt
    assert "不得在direction中编造精确秒点" in prompt


def test_suggestion_output_contains_editable_look_plan_and_at_most_six_clips() -> None:
    output = ShotSuggestionOutput(
        sceneTitle="出门准备",
        lookPlan={
            "personWardrobe": "浅色外套",
            "personAccessories": "帆布包",
            "catAppearance": "不增加服饰",
            "keyProps": "钓具",
            "imageRecommended": True,
            "recommendationReason": "服装和道具跨片段复用",
        },
        shots=[
            {
                "title": "猫咪等候",
                "direction": "1. 中景，猫咪看向人物。\n2. 近景，人物拿起钓具。",
                "suggestedDurationSeconds": 10,
            }
        ],
    )

    assert output.look_plan.image_recommended is True
    with pytest.raises(ValueError):
        ShotSuggestionOutput(
            sceneTitle="过长",
            lookPlan={},
            shots=[
                {"title": str(index), "direction": "1. 猫咪行走。"}
                for index in range(7)
            ],
        )


def test_scene_look_prompt_uses_editable_plan_without_replacing_canon_identity() -> None:
    prompt = compile_scene_look_prompt(
        project_title="湖边日常",
        scene_title="出门准备",
        scene_text="人物和猫咪准备去钓鱼。",
        look_plan=SceneLookPlan(
            personWardrobe="浅色外套",
            personAccessories="帆布包",
            catAppearance="不增加服饰",
            keyProps="钓鱼竿、小水桶",
            imageRecommended=True,
            recommendationReason="跨片段统一",
        ),
        reference_descriptions=("@图片1只负责identity", "@图片2只负责style"),
    )

    assert "浅色外套" in prompt.text
    assert "猫咪不增加服饰" in prompt.text
    assert "不得改写Canon身份" in prompt.text
    assert "9:16" in prompt.text


def test_accept_suggestions_persists_edited_output_and_requires_target_count() -> None:
    project_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    step_id = uuid.uuid4()
    scene = StoredScene(
        id=scene_id,
        project_id=project_id,
        order=1,
        draft=SceneDraft(
            title="出门准备",
            sourceText="人物和猫咪准备出门。",
            storyMode="multi",
            targetShotCount=2,
        ),
        status=SceneStatus.DRAFT,
    )
    step = StoredStep(
        id=step_id,
        project_id=project_id,
        scene_id=scene_id,
        shot_card_id=None,
        kind=StepKind.DIRECTOR,
        status=StepStatus.SUCCEEDED,
        attempt=1,
        operation_key="director:shot-suggestions",
        input_snapshot={
            "sourceHash": story_source_hash(scene.draft),
            "providerOutput": {"sceneTitle": "原始", "lookPlan": {}, "shots": []},
        },
        created_at=datetime.now(UTC),
    )

    class Repository:
        accepted: dict[str, object] | None = None

        def get_step(self, received_step_id: uuid.UUID) -> StoredStep:
            assert received_step_id == step_id
            return step

        def get_scene(self, received_scene_id: uuid.UUID) -> StoredScene:
            assert received_scene_id == scene_id
            return scene

        def accept_scene_suggestions(self, **values: object) -> tuple[object, ...]:
            self.accepted = values
            return ()

    repository = Repository()
    service = ProjectEditingService(
        repository=repository,
        director=None,
        provider_name="test",
    )
    look_plan = SceneLookPlan(personWardrobe="浅色外套")
    shots = (
        ShotSuggestion(title="猫等待", direction="1. 猫咪等待。"),
        ShotSuggestion(title="一起出门", direction="1. 人物开门。\n2. 猫咪跟随。"),
    )

    source_revisions = {uuid.uuid4(): 3, uuid.uuid4(): 4}
    service.accept_suggestions(
        step_id,
        look_plan=look_plan,
        shots=shots,
        apply_mode="update_existing",
        source_shot_revisions=source_revisions,
    )

    assert repository.accepted is not None
    assert repository.accepted["step_id"] == step_id
    assert repository.accepted["apply_mode"] == "update_existing"
    assert repository.accepted["source_shot_revisions"] == source_revisions
    accepted_output = repository.accepted["accepted_output"]
    assert isinstance(accepted_output, dict)
    assert accepted_output["lookPlan"]["personWardrobe"] == "浅色外套"
    assert accepted_output["applyMode"] == "update_existing"

    with pytest.raises(ValueError, match="2"):
        service.accept_suggestions(step_id, look_plan=look_plan, shots=shots[:1])
