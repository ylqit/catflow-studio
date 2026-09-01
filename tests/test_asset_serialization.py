from __future__ import annotations

import uuid
from pathlib import Path

from cat_video_generator.application.ports import StoredAsset
from cat_video_generator.infrastructure.db.repositories import _json_asset
from cat_video_generator.interfaces.api import _asset_json


def test_asset_dtos_expose_stored_shot_card_id_as_shot_id() -> None:
    shot_card_id = uuid.uuid4()
    asset = StoredAsset(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        scene_id=uuid.uuid4(),
        shot_card_id=shot_card_id,
        step_id=None,
        role="shot_anchor",
        media_type="image",
        scope="shot",
        status="approved",
        path=Path("missing.png"),
        sha256="0" * 64,
        metadata={},
    )

    assert _json_asset(asset)["shotId"] == str(shot_card_id)
    assert _asset_json(asset)["shotId"] == str(shot_card_id)


def test_asset_dtos_report_missing_content_without_dereferencing_a_path() -> None:
    asset = StoredAsset(
        id=uuid.uuid4(),
        project_id=None,
        scene_id=None,
        shot_card_id=None,
        step_id=None,
        role="identity",
        media_type="image",
        scope="canon",
        status="approved",
        path=None,
        sha256="0" * 64,
        metadata={},
        semantic_key="person:headshot",
    )

    assert _json_asset(asset)["contentReady"] is False
    assert _asset_json(asset)["contentReady"] is False


def test_character_design_display_name_hides_internal_semantic_key() -> None:
    revision_id = uuid.uuid4()
    asset = StoredAsset(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        scene_id=None,
        shot_card_id=None,
        step_id=None,
        role="character_design_child",
        media_type="image",
        scope="project",
        status="approved",
        path=Path("missing.png"),
        sha256="0" * 64,
        metadata={"displayName": f"character-design:{revision_id}:child:candidate:1"},
        semantic_key=f"character-design:{revision_id}:child:candidate:1",
    )

    assert asset.display_name == "本集儿童设计"


def test_video_version_display_name_hides_shot_storage_key() -> None:
    shot_id = uuid.uuid4()
    asset = StoredAsset(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        scene_id=uuid.uuid4(),
        shot_card_id=shot_id,
        step_id=None,
        role="shot_video",
        media_type="video",
        scope="shot",
        status="approved",
        path=Path("missing.mp4"),
        sha256="0" * 64,
        metadata={},
        semantic_key=f"shot:{shot_id}:video:2",
    )

    assert asset.display_name == "视频版本 V2"
