from __future__ import annotations

import uuid

import pytest

from cat_video_generator.infrastructure.db.models import Scene, StoryRevisionRecord
from cat_video_generator.infrastructure.db.story_scenes import (
    SceneLookRevisionIntegrityError,
    materialize_approved_story_scenes,
    scene_look_plan_from_outline,
)


class _SceneSession:
    def __init__(self, rows: list[Scene] | None = None) -> None:
        self.rows = list(rows or [])
        self.added: list[Scene] = []
        self.flush_count = 0

    def scalars(self, _statement: object) -> list[Scene]:
        return list(self.rows)

    def add(self, row: Scene) -> None:
        self.added.append(row)

    def flush(self) -> None:
        self.flush_count += 1


def _outline(
    *,
    scene_key: str = "courtyard",
    title: str = "雨后小院",
    props: list[str] | None = None,
) -> dict[str, object]:
    return {
        "sceneKey": scene_key,
        "title": title,
        "purpose": "孩子和猫咪发现一片发亮的叶子",
        "synopsis": "雨后的光落在叶片水珠上。",
        "continuity": {
            "location": title,
            "environment": "outdoor",
            "timeWeather": "雨后初晴",
            "decorations": ["矮墙", "湿润植物"],
            "props": props or ["带雨珠的叶子"],
            "transitionReason": "",
        },
    }


def _story(*outlines: dict[str, object]) -> StoryRevisionRecord:
    return StoryRevisionRecord(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        revision=3,
        strategy="event_script",
        status="approved",
        title="亮叶子",
        logline="孩子和猫咪守着一片雨后亮叶。",
        synopsis="雨后，小院里出现一片被雨珠照亮的叶子。",
        subject_ids_json=[],
        scene_plan_json=list(outlines),
        episode_rules_json={
            "mainScene": "雨后小院",
            "environment": "outdoor",
            "timeWeather": "雨后初晴",
            "coreProps": ["带雨珠的叶子"],
        },
    )


def _persisted_scene(
    story: StoryRevisionRecord,
    *,
    outline: dict[str, object],
    revision: int | None = 7,
) -> Scene:
    return Scene(
        id=uuid.uuid4(),
        production_run_id=story.production_run_id,
        story_revision_id=story.id,
        scene_key=str(outline["sceneKey"]),
        active=True,
        sort_order=1,
        title=str(outline["title"]),
        source_text="原场景",
        story_mode="single",
        target_shot_count=1,
        look_plan_json=scene_look_plan_from_outline(outline),
        look_draft_json={"prompt": "已审核的场景 Look 草稿"},
        look_draft_revision=revision,  # type: ignore[arg-type]
        selected_look_asset_id=uuid.uuid4(),
        status="ready",
    )


def test_new_scene_initializes_first_look_revision_before_flush() -> None:
    outline = _outline()
    story = _story(outline)
    session = _SceneSession()

    scenes = materialize_approved_story_scenes(session, story)  # type: ignore[arg-type]

    assert len(scenes) == 1
    scene = scenes[0]
    assert scene in session.added
    assert scene.look_draft_revision == 1
    assert scene.look_draft_json == {}
    assert scene.look_plan_json == scene_look_plan_from_outline(outline)
    assert scene.selected_look_asset_id is None
    assert session.flush_count == 2


def test_same_scene_plan_is_idempotent_and_preserves_valid_look_state() -> None:
    outline = _outline()
    story = _story(outline)
    scene = _persisted_scene(story, outline=outline)
    original_draft = dict(scene.look_draft_json)
    original_asset_id = scene.selected_look_asset_id
    session = _SceneSession([scene])

    materialized = materialize_approved_story_scenes(session, story)  # type: ignore[arg-type]

    assert materialized == (scene,)
    assert scene.look_draft_revision == 7
    assert scene.look_draft_json == original_draft
    assert scene.selected_look_asset_id == original_asset_id
    assert scene.active is True
    assert scene.stale_reason is None
    assert session.added == []


def test_changed_scene_plan_invalidates_look_once() -> None:
    original = _outline()
    changed = _outline(props=["带雨珠的叶子", "浅色小陶碗"])
    story = _story(changed)
    scene = _persisted_scene(story, outline=original)
    session = _SceneSession([scene])

    materialize_approved_story_scenes(session, story)  # type: ignore[arg-type]

    assert scene.look_plan_json == scene_look_plan_from_outline(changed)
    assert scene.look_draft_revision == 8
    assert scene.look_draft_json == {}
    assert scene.selected_look_asset_id is None


@pytest.mark.parametrize("invalid_revision", [None, -1, True])
def test_existing_scene_with_invalid_revision_fails_explicitly(
    invalid_revision: int | None,
) -> None:
    outline = _outline()
    story = _story(outline)
    scene = _persisted_scene(story, outline=outline, revision=invalid_revision)
    session = _SceneSession([scene])

    with pytest.raises(SceneLookRevisionIntegrityError) as exc_info:
        materialize_approved_story_scenes(session, story)  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert str(scene.id) in message
    assert str(story.id) in message
    assert "sceneKey=courtyard" in message


def test_multiple_scene_keys_reuse_rows_in_story_order() -> None:
    first = _outline(scene_key="courtyard", title="雨后小院")
    second = _outline(scene_key="porch", title="屋檐下")
    story = _story(first, second)
    courtyard = _persisted_scene(story, outline=first)
    porch = _persisted_scene(story, outline=second)
    courtyard.sort_order = 2
    porch.sort_order = 1
    session = _SceneSession([porch, courtyard])

    scenes = materialize_approved_story_scenes(session, story)  # type: ignore[arg-type]

    assert scenes == (courtyard, porch)
    assert [scene.sort_order for scene in scenes] == [1, 2]
    assert [scene.scene_key for scene in scenes] == ["courtyard", "porch"]
    assert session.added == []
