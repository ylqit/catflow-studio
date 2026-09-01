from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from cat_video_generator.domain.contracts import VisualProfileDraft
from cat_video_generator.domain.rendering import ProjectSequencePlan, SequenceClip
from cat_video_generator.infrastructure.db import models
from cat_video_generator.infrastructure.db.repositories import (
    _asset,
    _profile_hash,
    _resolve_storage_key,
    _storage_key_for,
)
from cat_video_generator.infrastructure.db.session import ALEMBIC_HEAD
from cat_video_generator.infrastructure.media.storage import LocalAssetStore


def test_v5_database_models_expose_creation_flow_columns() -> None:
    assert ALEMBIC_HEAD == "0031_workflow_task_cancellation"
    assert hasattr(models.ProductionRun, "default_reference_bindings_json")
    assert hasattr(models.ProductionRun, "current_visual_profile_revision_id")
    assert hasattr(models.VisualProfileRevision, "profile_hash")
    assert hasattr(models.VisualProfileRevision, "reference_snapshot_json")
    assert hasattr(models.Scene, "story_mode")
    assert hasattr(models.Scene, "target_shot_count")
    assert hasattr(models.Scene, "look_plan_json")
    assert hasattr(models.Scene, "selected_look_asset_id")
    assert hasattr(models.Scene, "look_draft_json")
    assert hasattr(models.Scene, "look_draft_revision")
    assert hasattr(models.ShotCard, "inherit_project_references")
    assert hasattr(models.ShotCard, "use_scene_look")
    assert hasattr(models.ShotCard, "draft_revision")
    assert hasattr(models.ShotCard, "scene_look_usage")
    assert hasattr(models.Asset, "storage_key")
    assert not hasattr(models.Asset, "local_path")


def test_storage_key_round_trip_stays_below_asset_root(tmp_path: Path) -> None:
    asset_root = tmp_path / "assets"
    path = asset_root / "imported" / "sha256" / "ab" / "asset.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"asset")

    key = _storage_key_for(path, asset_root)

    assert key == "imported/sha256/ab/asset.png"
    assert _resolve_storage_key(key, asset_root) == path.resolve()


@pytest.mark.parametrize(
    "key",
    ["../secret.png", "/tmp/secret.png", "C:/secret.png", "legacy:C:/old.png"],
)
def test_storage_key_rejects_absolute_legacy_and_traversal_paths(
    tmp_path: Path, key: str
) -> None:
    with pytest.raises(ValueError, match="storage key"):
        _resolve_storage_key(key, tmp_path / "assets")


def test_storage_key_rejects_landed_asset_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"asset")

    with pytest.raises(ValueError, match="asset root"):
        _storage_key_for(outside, tmp_path / "assets")


def test_legacy_storage_key_is_quarantined_when_loading_asset(tmp_path: Path) -> None:
    row = SimpleNamespace(
        id=uuid.uuid4(),
        production_run_id=None,
        scene_id=None,
        shot_card_id=None,
        producing_step_id=None,
        role="identity",
        media_type="image",
        scope="canon",
        status="approved",
        storage_key="legacy:C:/old-machine/canon.png",
        sha256="0" * 64,
        metadata_json={},
        semantic_key="person:headshot",
    )

    asset = _asset(row, tmp_path / "assets")

    assert asset.path is None
    assert asset.content_ready is False


def test_visual_profile_hash_includes_reference_content_hash() -> None:
    draft = VisualProfileDraft()
    first = _profile_hash(
        draft,
        reference_snapshot=[{"assetId": str(uuid.uuid4()), "sha256": "1" * 64}],
    )
    second = _profile_hash(
        draft,
        reference_snapshot=[{"assetId": str(uuid.uuid4()), "sha256": "2" * 64}],
    )

    assert first != second


def test_visual_profile_hash_uses_the_migration_json_canonicalization() -> None:
    draft = VisualProfileDraft()
    snapshot: list[dict[str, str]] = []
    payload = {
        **draft.model_dump(mode="json", by_alias=True),
        "referenceSnapshot": snapshot,
    }
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    assert _profile_hash(draft, reference_snapshot=snapshot) == expected


def test_sequence_renderer_compiles_fades_and_cross_dissolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fixture")
    sources = tuple(tmp_path / f"clip-{index}.mp4" for index in range(3))
    for source in sources:
        source.write_bytes(b"video")
    shot_ids = [uuid.uuid4() for _ in sources]
    asset_ids = [uuid.uuid4() for _ in sources]
    plan = ProjectSequencePlan(
        duration_ms=29_700,
        clips=[
            SequenceClip(
                order=1,
                shot_card_id=shot_ids[0],
                source_asset_id=asset_ids[0],
                source_start_ms=0,
                source_end_ms=10_000,
                timeline_start_ms=0,
                timeline_end_ms=10_000,
            ),
            SequenceClip(
                order=2,
                shot_card_id=shot_ids[1],
                source_asset_id=asset_ids[1],
                source_start_ms=0,
                source_end_ms=10_000,
                timeline_start_ms=10_000,
                timeline_end_ms=20_000,
                transitionFromPrevious={"type": "fade_black", "durationMs": 300},
            ),
            SequenceClip(
                order=3,
                shot_card_id=shot_ids[2],
                source_asset_id=asset_ids[2],
                source_start_ms=0,
                source_end_ms=10_000,
                timeline_start_ms=19_700,
                timeline_end_ms=29_700,
                transitionFromPrevious={"type": "cross_dissolve", "durationMs": 300},
            ),
        ],
    )
    captured: list[str] = []

    def fake_run(command: list[str], *, timeout: int, label: str) -> None:
        assert timeout == 1800
        assert label == "project sequence"
        captured.extend(command)
        Path(command[-1]).write_bytes(b"composite")

    monkeypatch.setattr(
        "cat_video_generator.infrastructure.media.storage._run",
        fake_run,
    )
    store = LocalAssetStore(
        work_root=tmp_path / "work",
        asset_root=tmp_path / "assets",
        ffmpeg_path=ffmpeg,
    )

    landed = store.compose_sequence(sources, plan)
    filter_graph = captured[captured.index("-filter_complex") + 1]

    assert landed.path.is_file()
    assert "fade=t=out" in filter_graph
    assert "fade=t=in" in filter_graph
    assert "concat=n=2:v=1:a=1" in filter_graph
    assert "xfade=transition=fade" in filter_graph
    assert "acrossfade" in filter_graph


def test_sequence_renderer_compiles_project_boundary_fades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fixture")
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    plan = ProjectSequencePlan(
        duration_ms=8_000,
        clips=[
            SequenceClip(
                order=1,
                shot_card_id=uuid.uuid4(),
                source_asset_id=uuid.uuid4(),
                source_start_ms=0,
                source_end_ms=8_000,
                timeline_start_ms=0,
                timeline_end_ms=8_000,
            )
        ],
        introTransition={"type": "fade_black", "durationMs": 500},
        outroTransition={"type": "fade_black", "durationMs": 500},
    )
    captured: list[str] = []

    def fake_run(command: list[str], *, timeout: int, label: str) -> None:
        captured.extend(command)
        Path(command[-1]).write_bytes(b"composite")

    monkeypatch.setattr(
        "cat_video_generator.infrastructure.media.storage._run",
        fake_run,
    )
    store = LocalAssetStore(
        work_root=tmp_path / "work",
        asset_root=tmp_path / "assets",
        ffmpeg_path=ffmpeg,
    )

    store.compose_sequence((source,), plan)
    filter_graph = captured[captured.index("-filter_complex") + 1]

    assert "fade=t=in:st=0:d=0.500" in filter_graph
    assert "afade=t=in:st=0:d=0.500" in filter_graph
    assert "fade=t=out:st=7.500:d=0.500" in filter_graph
    assert "afade=t=out:st=7.500:d=0.500" in filter_graph


def test_sequence_renderer_handles_dissolve_after_a_cut(tmp_path: Path) -> None:
    ffmpeg_name = shutil.which("ffmpeg")
    if ffmpeg_name is None:
        pytest.skip("FFmpeg is not installed")
    ffmpeg = Path(ffmpeg_name)
    source = tmp_path / "clip.mp4"
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:r=30:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    shots = [uuid.uuid4() for _ in range(3)]
    assets = [uuid.uuid4() for _ in range(3)]
    plan = ProjectSequencePlan(
        duration_ms=2_700,
        clips=[
            SequenceClip(
                order=1,
                shot_card_id=shots[0],
                source_asset_id=assets[0],
                source_start_ms=0,
                source_end_ms=1_000,
                timeline_start_ms=0,
                timeline_end_ms=1_000,
            ),
            SequenceClip(
                order=2,
                shot_card_id=shots[1],
                source_asset_id=assets[1],
                source_start_ms=0,
                source_end_ms=1_000,
                timeline_start_ms=1_000,
                timeline_end_ms=2_000,
                transitionFromPrevious={"type": "cut", "durationMs": 0},
            ),
            SequenceClip(
                order=3,
                shot_card_id=shots[2],
                source_asset_id=assets[2],
                source_start_ms=0,
                source_end_ms=1_000,
                timeline_start_ms=1_700,
                timeline_end_ms=2_700,
                transitionFromPrevious={"type": "cross_dissolve", "durationMs": 300},
            ),
        ],
    )
    store = LocalAssetStore(
        work_root=tmp_path / "work",
        asset_root=tmp_path / "assets",
        ffmpeg_path=ffmpeg,
    )

    landed = store.compose_sequence((source, source, source), plan)

    assert landed.path.is_file()
    assert landed.byte_size > 0
