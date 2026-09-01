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
