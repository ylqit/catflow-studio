from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from catflow.maintenance.cleanup import CleanupPolicy, validate_storage_key


def test_cleanup_policy_pins_the_reviewed_project_and_version_decisions() -> None:
    policy = CleanupPolicy.reviewed()

    assert policy.delete_project_ids == {
        uuid.UUID("4eaa29c6-e2ed-45bb-8776-40a6ac427f20"),
        uuid.UUID("20b65033-76f6-49e6-b3f5-3f3412f46077"),
        uuid.UUID("067067e7-a556-445d-8760-89e8ecb93250"),
        uuid.UUID("c2d7ed18-e424-4787-b6d9-1c6ac2dad1f3"),
        uuid.UUID("e1f7e598-1a61-4ec2-ba67-e9ae63e603cd"),
    }
    assert policy.mixed_project_id == uuid.UUID("cf284238-0984-49fe-b88d-342fb20b1df5")
    assert policy.restore_shot_plan_id == uuid.UUID(
        "01cde068-9fee-4513-a5fb-9ab1fa8f3d3d"
    )
    assert policy.restore_edit_version_id == uuid.UUID(
        "026f1134-027c-4afe-a3c6-e2d3c61bdfd9"
    )


@pytest.mark.parametrize("value", ["C:/outside.mp4", "../outside.mp4", "a/../../outside.mp4"])
def test_cleanup_rejects_storage_keys_outside_the_media_root(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError, match="managed media root"):
        validate_storage_key(tmp_path, value)


def test_cleanup_accepts_a_relative_storage_key_inside_the_media_root(tmp_path: Path) -> None:
    assert validate_storage_key(tmp_path, "generated/project/video.mp4") == (
        tmp_path / "generated/project/video.mp4"
    ).resolve()
