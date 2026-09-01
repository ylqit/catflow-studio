from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from cat_video_generator.application.ports import (
    StoredAsset,
    StoredProject,
    StoredScene,
    StoredVisualProfileRevision,
)
from cat_video_generator.application.shot_queue import (
    RevisionConflictError,
    ShotProductionService,
)
from cat_video_generator.domain.contracts import (
    LookReferenceBinding,
    SceneDraft,
    SceneLookDraft,
    SceneLookPlan,
    VisualProfileDraft,
)
from cat_video_generator.domain.workflow import RunStatus, SceneStatus


def _asset(
    tmp_path: Path,
    semantic_key: str,
    *,
    digest: str,
    ready: bool = True,
) -> StoredAsset:
    path = tmp_path / f"{semantic_key.replace(':', '-')}.png"
    if ready:
        path.write_bytes(semantic_key.encode("utf-8"))
    return StoredAsset(
        id=uuid.uuid4(),
        project_id=None,
        scene_id=None,
        shot_card_id=None,
        step_id=None,
        role="canon_reference",
        media_type="image",
        scope="canon",
        status="approved",
        path=path if ready else None,
        sha256=digest,
        metadata={},
        semantic_key=semantic_key,
    )


def _service(repository: object) -> ShotProductionService:
    return ShotProductionService(
        repository=repository,  # type: ignore[arg-type]
        gateway=None,
        asset_store=object(),  # type: ignore[arg-type]
        media_probe=object(),  # type: ignore[arg-type]
        frame_extractor=None,
        provider_name="fake",
        resolution="720p",
    )


def _scene(project_id: uuid.UUID, *, draft: SceneLookDraft | None = None) -> StoredScene:
    return StoredScene(
        id=uuid.uuid4(),
        project_id=project_id,
        order=1,
        draft=SceneDraft(title="采茶", sourceText="孩子和猫咪在茶园准备采茶。"),
        status=SceneStatus.DRAFT,
        look_draft=draft,
        look_draft_revision=0 if draft is None else 1,
    )


def test_default_look_draft_uses_identity_and_environment_style_in_fixed_order(
    tmp_path: Path,
) -> None:
    project_id = uuid.uuid4()
    assets = (
        _asset(tmp_path, "person:headshot", digest="1" * 64),
        _asset(tmp_path, "person:fullbody", digest="2" * 64),
        _asset(tmp_path, "person:front", digest="2" * 64),
        _asset(tmp_path, "cat:front", digest="3" * 64),
        _asset(tmp_path, "cat:side", digest="4" * 64),
        _asset(tmp_path, "style:line_texture", digest="5" * 64),
        _asset(tmp_path, "style:outdoor", digest="6" * 64),
        _asset(tmp_path, "style:indoor", digest="7" * 64),
    )
    purposes = (
        "person_identity",
        "person_body",
        "person_body",
        "cat_identity",
        "cat_identity",
        "style",
        "style",
        "style",
    )
    profile = StoredVisualProfileRevision(
        id=uuid.uuid4(),
        project_id=project_id,
        revision=3,
        profile_hash="8" * 64,
        source_profile_id="canon-v1",
        draft=VisualProfileDraft(
            referenceBindings=[
                LookReferenceBinding(assetId=assets[index].id, purpose=purposes[index])
                for index in (5, 6, 7, 3, 4, 0, 1, 2)
            ]
        ),
    )
    scene = _scene(project_id)

    class Repository:
        def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
            assert scene_id == scene.id
            return scene

        def get_scene_look_draft(self, scene_id: uuid.UUID) -> StoredScene:
            return self.get_scene(scene_id)

        def get_visual_profile(self, requested_project_id: uuid.UUID):
            assert requested_project_id == project_id
            return profile

        def get_visual_profile_revision(self, revision_id: uuid.UUID):
            assert revision_id == profile.id
            return profile

        def get_project(self, requested_project_id: uuid.UUID):
            return StoredProject(
                id=requested_project_id,
                title="茶园日常",
                content_date=date(2026, 8, 13),
                status=RunStatus.ACTIVE,
            )

        def get_asset(self, asset_id: uuid.UUID) -> StoredAsset:
            return next(item for item in assets if item.id == asset_id)

    service = _service(Repository())
    draft = service.get_scene_look_draft(scene.id)["draft"]
    assert [item["assetId"] for item in draft["referenceBindings"]] == [
        str(item.id) for item in assets if item.semantic_key != "style:indoor"
    ]

    preview = service.preview_scene_look_prompt(scene.id)
    assert preview["warnings"] == []
    assert preview["referenceCount"] == 6
    assert [item["semanticKey"] for item in preview["references"]] == [
        "person:headshot",
        "person:fullbody",
        "cat:front",
        "cat:side",
        "style:line_texture",
        "style:outdoor",
    ]
    assert "忽略旧服装与背景" in preview["prompt"]


def test_scene_look_generation_preflight_rejects_missing_category_before_gateway(
    tmp_path: Path,
) -> None:
    project_id = uuid.uuid4()
    person = _asset(tmp_path, "person:headshot", digest="1" * 64)
    style = _asset(tmp_path, "style:outdoor", digest="2" * 64)
    profile = StoredVisualProfileRevision(
        id=uuid.uuid4(),
        project_id=project_id,
        revision=1,
        profile_hash="3" * 64,
        source_profile_id="canon-v1",
        draft=VisualProfileDraft(
            referenceBindings=[
                LookReferenceBinding(assetId=person.id, purpose="person_identity"),
                LookReferenceBinding(assetId=style.id, purpose="style"),
            ]
        ),
    )
    draft = SceneLookDraft(
        visualProfileRevisionId=profile.id,
        lookPlan=SceneLookPlan(),
        referenceBindings=profile.draft.reference_bindings,
    )
    scene = _scene(project_id, draft=draft)

    class Repository:
        def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
            assert scene_id == scene.id
            return scene

        def get_visual_profile_revision(self, revision_id: uuid.UUID):
            assert revision_id == profile.id
            return profile

        def get_asset(self, asset_id: uuid.UUID) -> StoredAsset:
            return person if asset_id == person.id else style

    service = _service(Repository())
    with pytest.raises(ValueError, match="猫咪身份"):
        service.validate_scene_look_request(scene.id, 1)


def test_scene_look_generation_rejects_stale_draft_revision() -> None:
    project_id = uuid.uuid4()
    scene = _scene(
        project_id,
        draft=SceneLookDraft(
            visualProfileRevisionId=uuid.uuid4(),
            lookPlan=SceneLookPlan(),
        ),
    )

    class Repository:
        def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
            assert scene_id == scene.id
            return scene

    with pytest.raises(RevisionConflictError, match="已更新"):
        _service(Repository()).validate_scene_look_request(scene.id, 2)
