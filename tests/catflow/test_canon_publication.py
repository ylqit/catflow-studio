from __future__ import annotations

import uuid

import pytest

from catflow.application.service import (
    CanonRevisionCreateCommand,
    ProjectCreate,
    StudioConflictError,
    StudioService,
)
from catflow.infrastructure.memory_repository import MemoryStudioRepository

FIXED_ROLES = ("episode_child", "episode_cat", "pair_scale", "style_board")


def test_series_can_pin_white_cat_without_changing_default_canon() -> None:
    from catflow.application.series import SeriesCreateCommand
    from catflow.application.video_generation import compile_video_generation_prompt
    from catflow.domain.models import ShotSpec

    repository = MemoryStudioRepository()
    service = StudioService(repository)
    original = service.current_canon()
    assets = {role: asset.id for role, asset in original.fixed_assets.items()}
    for index, role in enumerate(("episode_cat", "pair_scale"), start=10):
        assets[role] = service.register_canon_asset(
            role=role, sha256=f"{index:x}" * 64,
            storage_key=f"white/{role}.png", byte_size=100,
        ).id
    command = CanonRevisionCreateCommand(
        baseProfileId=original.id, activate=False, fixedAssets=assets,
        cat={"identity": "固定同一只白猫", "lockedTraits": ["白底浅灰虎斑", "深色大眼", "粉鼻"]},
    )
    white = service.publish_canon_revision(command)
    assert not white.active
    assert service.publish_canon_revision(command).id == white.id
    assert service.current_canon().id == original.id
    series = service.create_story_series(SeriesCreateCommand(
        title="白猫野餐", premise="准备、野餐、回家", narrativeMode="continuous",
        plannedEpisodeCount=3, defaultEpisodeDurationSeconds=15,
        worldSetting="森林", emotionalDirection="温馨", canonProfileId=white.id,
        additionalNotes="开场用品已装好，白猫只需放球入包",
    ))
    assert series.canon_profile_id == white.id
    assert "开场用品已装好" in service.preview_series_plan(series.id).prompt
    project = repository.create_project(
        ProjectCreate(title="白猫首集", theme="野餐", targetDurationSeconds=15),
        canon_profile_id=series.canon_profile_id,
    )
    selected = service.current_selections(project.id)
    assert selected["episode_cat"].id == assets["episode_cat"]
    assert selected["pair_scale"].id == assets["pair_scale"]
    assert selected["episode_child"].id == original.fixed_assets["episode_child"].id
    prompt = compile_video_generation_prompt(
        project_title=project.title, target_duration_seconds=15, director_treatment=None,
        cat_identity=white.cat_identity_prompt,
        shots=[ShotSpec(id="s1", order=1, durationSeconds=15, framing="中景",
                        cameraMovement="固定", childAction="摸猫", catAction="抬头",
                        environmentChange="球落入包内", transition="continuous")],
    ).prompt
    assert "固定同一只白猫" in prompt and "深色大眼" in prompt
    assert "固定同一只灰白虎斑猫" not in prompt


def test_published_canon_assets_are_inherited_and_cannot_be_overridden() -> None:
    service = StudioService(MemoryStudioRepository())
    uploaded = {
        role: service.register_canon_asset(
            role=role,
            sha256=f"{index:x}" * 64,
            storage_key=f"canon/{role}.png",
            byte_size=100,
        )
        for index, role in enumerate(FIXED_ROLES, start=1)
    }
    published = service.publish_canon_revision(
        CanonRevisionCreateCommand(
            fixedAssets={role: asset.id for role, asset in uploaded.items()}
        )
    )
    project = service.create_project(
        ProjectCreate(title="雨天擦爪", theme="雨天擦爪", targetDurationSeconds=12)
    )

    assert published.active is True
    assert published.spec_version == 4
    assert project.canon_profile_id == published.id
    assert service.current_selections(project.id) == uploaded

    candidate = service.register_asset(project.id, role="episode_child", sha256="f" * 64)
    with pytest.raises(StudioConflictError, match="global Canon"):
        service.select_asset(project.id, slot="episode_child", asset_id=candidate.id)


def test_canon_publication_requires_exact_fixed_roles_and_global_assets() -> None:
    service = StudioService(MemoryStudioRepository())
    project = service.create_project(
        ProjectCreate(title="浇花", theme="浇花", targetDurationSeconds=12)
    )
    project_asset = service.register_asset(project.id, role="episode_child", sha256="a" * 64)

    with pytest.raises(StudioConflictError, match="global Canon candidate"):
        service.publish_canon_revision(
            CanonRevisionCreateCommand(
                fixedAssets=dict.fromkeys(FIXED_ROLES, project_asset.id)
            )
        )

    with pytest.raises(ValueError, match="fixed Canon roles"):
        CanonRevisionCreateCommand(
            fixedAssets={"episode_child": uuid.uuid4()}
        )
