"""Approved-story scene lifecycle and legacy scene-plan normalization."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...domain.workflow import SceneStatus
from .models import Scene, StoryRevisionRecord

logger = logging.getLogger(__name__)


class SceneLookRevisionIntegrityError(RuntimeError):
    """An existing scene violates the persisted Scene Look revision invariant."""


def normalized_story_scenes(row: StoryRevisionRecord) -> list[dict[str, Any]]:
    """Read both legacy and continuity-aware story scene documents."""

    episode_rules = dict(row.episode_rules_json or {})
    default_location = str(episode_rules.get("mainScene") or "沿用原故事场景")
    default_environment = str(episode_rules.get("environment") or "indoor")
    if default_environment not in {"indoor", "outdoor"}:
        default_environment = "indoor"
    default_weather = str(episode_rules.get("timeWeather") or "沿用原故事时间与天气")
    default_props = [str(item) for item in episode_rules.get("coreProps") or []]
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(row.scene_plan_json, 1):
        outline = dict(source)
        continuity = dict(outline.get("continuity") or {})
        scene_key = str(outline.get("sceneKey") or f"scene-{index:02d}")
        normalized.append(
            {
                **outline,
                "sceneKey": scene_key,
                "purpose": str(outline.get("purpose") or outline.get("title") or "推进剧情"),
                "continuity": {
                    **continuity,
                    "location": str(
                        continuity.get("location")
                        or (
                            outline.get("title")
                            if len(row.scene_plan_json) > 1
                            else default_location
                        )
                    ),
                    "environment": (
                        continuity.get("environment")
                        if continuity.get("environment") in {"indoor", "outdoor"}
                        else default_environment
                    ),
                    "timeWeather": str(continuity.get("timeWeather") or default_weather),
                    "decorations": [str(item) for item in continuity.get("decorations") or []],
                    "props": [str(item) for item in continuity.get("props") or default_props],
                    "transitionReason": str(
                        continuity.get("transitionReason")
                        or (
                            ""
                            if index == 1
                            else f"旧项目兼容：剧情推进至{outline.get('title') or scene_key}"
                        )
                    ),
                },
            }
        )
    return normalized


def scene_look_plan_from_outline(outline: dict[str, Any]) -> dict[str, Any]:
    continuity = dict(outline.get("continuity") or {})
    decorations = [str(item) for item in continuity.get("decorations") or []]
    props = [str(item) for item in continuity.get("props") or []]
    location = str(continuity.get("location") or outline.get("title") or "待确认场景")
    time_weather = str(continuity.get("timeWeather") or "时间天气待确认")
    environment = continuity.get("environment")
    return {
        "personWardrobe": "",
        "personAccessories": "",
        "catAppearance": "",
        "keyProps": "、".join([*decorations, *props]),
        "environmentStyle": environment if environment in {"indoor", "outdoor"} else "indoor",
        "personPose": "",
        "catPose": "",
        "composition": "",
        "additionalInstructions": f"地点：{location}；时间天气：{time_weather}",
        "imageRecommended": True,
        "recommendationReason": "当前故事场景需要独立环境、装饰与道具视觉基准",
    }


def materialize_approved_story_scenes(
    session: Session,
    story: StoryRevisionRecord,
) -> tuple[Scene, ...]:
    """Make the approved story's semantic scenes current while preserving old rows."""

    outlines = normalized_story_scenes(story)
    if not outlines:
        raise ValueError("批准故事必须包含至少一个场景大纲")
    desired_keys = {str(outline["sceneKey"]) for outline in outlines}

    active_rows = list(
        session.scalars(
            select(Scene)
            .where(
                Scene.production_run_id == story.production_run_id,
                Scene.active.is_(True),
            )
            .order_by(Scene.sort_order)
            .with_for_update()
        )
    )
    current_by_key = {
        row.scene_key: row
        for row in active_rows
        if (
            row.story_revision_id == story.id
            and row.scene_key
            and row.scene_key in desired_keys
        )
    }
    for row in active_rows:
        row.active = False
        row.stale_reason = f"场景规划已切换到剧情 revision {story.revision}"
    session.flush()

    materialized: list[Scene] = []
    for index, outline in enumerate(outlines, 1):
        scene_key = str(outline["sceneKey"])
        next_look_plan = scene_look_plan_from_outline(outline)
        row = current_by_key.get(scene_key)
        if row is None:
            row = Scene(
                id=uuid.uuid4(),
                production_run_id=story.production_run_id,
                story_revision_id=story.id,
                scene_key=scene_key,
                active=True,
                sort_order=index,
                title=str(outline.get("title") or f"场景 {index}"),
                source_text=str(outline.get("synopsis") or outline.get("purpose") or "剧情场景"),
                story_mode="single",
                target_shot_count=1,
                look_plan_json=next_look_plan,
                look_draft_json={},
                look_draft_revision=1,
                selected_look_asset_id=None,
                status=SceneStatus.READY.value,
            )
            session.add(row)
            logger.info(
                "materialized new approved-story scene",
                extra={
                    "production_run_id": str(story.production_run_id),
                    "story_revision_id": str(story.id),
                    "story_revision": story.revision,
                    "scene_key": scene_key,
                    "scene_id": str(row.id),
                    "scene_reused": False,
                    "look_draft_revision": 1,
                },
            )
        else:
            revision = row.look_draft_revision
            if type(revision) is not int or revision < 0:
                raise SceneLookRevisionIntegrityError(
                    "已持久化场景的 Scene Look 修订号无效："
                    f"sceneId={row.id}, storyRevisionId={story.id}, "
                    f"storyRevision={story.revision}, sceneKey={scene_key}, "
                    f"lookDraftRevision={revision!r}"
                )
            if row.look_plan_json != next_look_plan:
                previous_plan = row.look_plan_json
                row.look_plan_json = next_look_plan
                row.look_draft_json = {}
                row.look_draft_revision = revision + 1
                row.selected_look_asset_id = None
                logger.info(
                    "updated approved-story scene look plan",
                    extra={
                        "production_run_id": str(story.production_run_id),
                        "story_revision_id": str(story.id),
                        "story_revision": story.revision,
                        "scene_key": scene_key,
                        "scene_id": str(row.id),
                        "scene_reused": True,
                        "previous_look_plan": previous_plan,
                        "next_look_plan": next_look_plan,
                        "previous_look_draft_revision": revision,
                        "look_draft_revision": revision + 1,
                    },
                )
        row.story_revision_id = story.id
        row.scene_key = scene_key
        row.active = True
        row.stale_reason = None
        row.sort_order = index
        row.title = str(outline.get("title") or f"场景 {index}")
        row.source_text = str(outline.get("synopsis") or outline.get("purpose") or "剧情场景")
        row.context_note = json.dumps(
            {
                "sceneKey": scene_key,
                "purpose": outline.get("purpose") or "推进剧情",
                "continuity": outline.get("continuity") or {},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        row.story_mode = "single"
        row.status = SceneStatus.READY.value
        materialized.append(row)
    session.flush()
    return tuple(materialized)
