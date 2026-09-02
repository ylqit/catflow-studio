from __future__ import annotations

import uuid
from datetime import UTC, datetime

from catflow.infrastructure.models import AssetRecord
from catflow.infrastructure.postgres_repository import _asset_dto


def test_legacy_video_columns_project_to_the_24_fps_edit_metadata() -> None:
    record = AssetRecord(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        role="video",
        media_type="video",
        storage_key="generated/project/video/job.mp4",
        sha256="a" * 64,
        byte_size=1024,
        width=480,
        height=854,
        duration_ms=12_000,
        metadata_json={},
        created_at=datetime.now(UTC),
    )

    projected = _asset_dto(record)

    assert projected.metadata["durationMs"] == 12_000
    assert projected.metadata["durationFrames"] == 288
    assert projected.metadata["frameRateNumerator"] == 24
    assert projected.metadata["frameRateDenominator"] == 1
    assert projected.metadata["width"] == 480
    assert projected.metadata["height"] == 854
