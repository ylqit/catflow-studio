from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path

from PIL import Image

from cat_video_generator.application.ports import (
    LandedAsset,
    StoredAsset,
    StoredProject,
    StoredScene,
    StoredShot,
)
from cat_video_generator.application.shot_queue import ShotProductionService
from cat_video_generator.domain.contracts import SceneDraft, ShotCardDraft
from cat_video_generator.domain.workflow import RunStatus, SceneStatus, ShotStatus


class _TailExtractor:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.calls = 0

    def extract_tail_frame(self, source: StoredAsset) -> tuple[Path, int]:
        self.calls += 1
        path = self.tmp_path / f"tail-{self.calls}.png"
        Image.new("RGB", (90, 160), color=(80, 120, 90)).save(path)
        return path, 11_850


class _TailStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def import_local(self, path: Path) -> LandedAsset:
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        destination = self.root / f"{digest}.png"
        destination.write_bytes(payload)
        return LandedAsset(destination, digest, len(payload))

    def download(self, url: str, *, suffix: str) -> LandedAsset:
        assert url == "https://provider.example/tail.png"
        source = self.root / f"provider-tail{suffix}"
        Image.new("RGB", (90, 160), color=(60, 90, 120)).save(source)
        return self.import_local(source)


class _TailProbe:
    def inspect_image(self, path: Path) -> dict[str, object]:
        assert path.is_file()
        return {"passed": True, "width": 90, "height": 160}


class _TailRepository:
    def __init__(self, tmp_path: Path) -> None:
        project_id = uuid.uuid4()
        scene_id = uuid.uuid4()
        source_path = tmp_path / "approved.mp4"
        source_path.write_bytes(b"video")
        self.project = StoredProject(
            id=project_id,
            title="湖泊的鱼",
            content_date=date(2026, 8, 13),
            status=RunStatus.ACTIVE,
        )
        self.scene = StoredScene(
            id=scene_id,
            project_id=project_id,
            order=1,
            draft=SceneDraft(title="出门", sourceText="猫咪跟着人物出门。"),
            status=SceneStatus.READY,
        )
        self.video = StoredAsset(
            id=uuid.uuid4(),
            project_id=project_id,
            scene_id=scene_id,
            shot_card_id=None,
            step_id=None,
            role="shot_video",
            media_type="video",
            scope="shot",
            status="approved",
            path=source_path,
            sha256="a" * 64,
            metadata={"qc": {"durationMs": 12_000}},
        )
        self.shots = (
            StoredShot(
                id=uuid.uuid4(),
                scene_id=scene_id,
                project_id=project_id,
                order=1,
                draft=ShotCardDraft(
                    title="上一片段",
                    direction="1. 猫咪走到门边。\n2. 人物开门并稳定收尾。",
                ),
                status=ShotStatus.APPROVED,
                selected_video_asset_id=self.video.id,
            ),
            StoredShot(
                id=uuid.uuid4(),
                scene_id=scene_id,
                project_id=project_id,
                order=2,
                draft=ShotCardDraft(
                    title="下一片段",
                    direction="1. 猫咪走出门。\n2. 人物跟随并稳定收尾。",
                    anchorMode="generate",
                    sceneLookUsage="derive_anchor",
                ),
                status=ShotStatus.READY,
            ),
        )
        self.assets: list[StoredAsset] = [self.video]

    def get_shot(self, shot_id: uuid.UUID) -> StoredShot:
        return next(item for item in self.shots if item.id == shot_id)

    def get_scene(self, scene_id: uuid.UUID) -> StoredScene:
        assert scene_id == self.scene.id
        return self.scene

    def list_shots(self, scene_id: uuid.UUID) -> tuple[StoredShot, ...]:
        assert scene_id == self.scene.id
        return self.shots

    def get_asset(self, asset_id: uuid.UUID) -> StoredAsset:
        return next(item for item in self.assets if item.id == asset_id)

    def list_assets(self, **kwargs: object) -> tuple[StoredAsset, ...]:
        shot_id = kwargs.get("shot_id")
        return tuple(item for item in self.assets if item.shot_card_id == shot_id)

    def add_asset(self, **kwargs: object) -> StoredAsset:
        landed = kwargs["landed"]
        assert isinstance(landed, LandedAsset)
        asset = StoredAsset(
            id=uuid.uuid4(),
            project_id=kwargs["project_id"],  # type: ignore[arg-type]
            scene_id=kwargs["scene_id"],  # type: ignore[arg-type]
            shot_card_id=kwargs["shot_id"],  # type: ignore[arg-type]
            step_id=kwargs["step_id"],  # type: ignore[arg-type]
            role=str(kwargs["role"]),
            media_type=str(kwargs["media_type"]),
            scope=str(kwargs["scope"]),
            status=str(kwargs["status"]),
            path=landed.path,
            sha256=landed.sha256,
            metadata=kwargs["metadata"],  # type: ignore[arg-type]
            semantic_key=str(kwargs["semantic_key"]),
        )
        self.assets.append(asset)
        return asset

    def update_shot(self, shot_id: uuid.UUID, draft: ShotCardDraft) -> StoredShot:
        current = self.get_shot(shot_id)
        updated = replace(
            current,
            draft=draft,
            draft_revision=current.draft_revision + 1,
            selected_anchor_asset_id=None,
            selected_video_asset_id=None,
            status=ShotStatus.READY,
        )
        self.shots = tuple(updated if item.id == shot_id else item for item in self.shots)
        return updated


def test_adopt_previous_tail_extracts_once_and_replaces_unique_anchor(tmp_path: Path) -> None:
    repository = _TailRepository(tmp_path)
    extractor = _TailExtractor(tmp_path)
    service = ShotProductionService(
        repository=repository,  # type: ignore[arg-type]
        gateway=None,
        asset_store=_TailStore(tmp_path),  # type: ignore[arg-type]
        media_probe=_TailProbe(),  # type: ignore[arg-type]
        frame_extractor=extractor,  # type: ignore[arg-type]
        provider_name="fake",
        resolution="720p",
    )
    target = repository.shots[1]

    first = service.adopt_previous_tail_anchor(target.id)
    second = service.adopt_previous_tail_anchor(target.id)

    assert first.draft.anchor_mode.value == "existing"
    assert first.draft.scene_look_usage.value == "appearance_only"
    approved = [
        item
        for item in first.draft.reference_bindings
        if item.usage.value == "approved_anchor"
    ]
    assert len(approved) == 1
    tail = repository.get_asset(approved[0].asset_id)
    assert tail.role == "shot_tail_frame"
    assert tail.metadata["sourceVideoAssetId"] == str(repository.video.id)
    assert tail.metadata["timestampMs"] == 11_850
    assert second.draft.reference_bindings == first.draft.reference_bindings
    assert extractor.calls == 1


def test_provider_returned_tail_frame_is_saved_without_local_extraction(
    tmp_path: Path,
) -> None:
    repository = _TailRepository(tmp_path)
    extractor = _TailExtractor(tmp_path)
    service = ShotProductionService(
        repository=repository,  # type: ignore[arg-type]
        gateway=None,
        asset_store=_TailStore(tmp_path),  # type: ignore[arg-type]
        media_probe=_TailProbe(),  # type: ignore[arg-type]
        frame_extractor=extractor,  # type: ignore[arg-type]
        provider_name="ark",
        resolution="720p",
    )
    step_id = uuid.uuid4()

    tail = service._land_provider_tail_frame(
        source_video=repository.video,
        shot_id=repository.shots[0].id,
        step_id=step_id,
        last_frame_url="https://provider.example/tail.png",
    )

    assert tail.role == "shot_tail_frame"
    assert tail.status == "approved"
    assert tail.step_id == step_id
    assert tail.metadata["providerReturned"] is True
    assert tail.metadata["sourceVideoAssetId"] == str(repository.video.id)
    assert extractor.calls == 0


def test_tail_anchor_becomes_stale_when_previous_selected_video_changes(
    tmp_path: Path,
) -> None:
    repository = _TailRepository(tmp_path)
    extractor = _TailExtractor(tmp_path)
    service = ShotProductionService(
        repository=repository,  # type: ignore[arg-type]
        gateway=None,
        asset_store=_TailStore(tmp_path),  # type: ignore[arg-type]
        media_probe=_TailProbe(),  # type: ignore[arg-type]
        frame_extractor=extractor,  # type: ignore[arg-type]
        provider_name="fake",
        resolution="720p",
    )
    target = repository.shots[1]
    adopted = service.adopt_previous_tail_anchor(target.id)
    replacement_path = tmp_path / "replacement.mp4"
    replacement_path.write_bytes(b"replacement")
    replacement = replace(
        repository.video,
        id=uuid.uuid4(),
        path=replacement_path,
        sha256="b" * 64,
    )
    repository.assets.append(replacement)
    repository.shots = (
        replace(repository.shots[0], selected_video_asset_id=replacement.id),
        adopted,
    )

    stale = service.tail_frame_status(target.id)
    refreshed = service.adopt_previous_tail_anchor(target.id)
    current = service.tail_frame_status(target.id)

    assert stale["stale"] is True
    assert stale["available"] is False
    assert current["stale"] is False
    assert current["available"] is True
    assert current["boundAssetId"] != stale["boundAssetId"]
    assert refreshed.selected_video_asset_id is None
    assert extractor.calls == 2
