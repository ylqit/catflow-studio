from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete

from catflow.application.service import (
    EditCreateCommand,
    ExportCommand,
    ProjectCreate,
    StudioService,
)
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.media import LocalMediaStore
from catflow.infrastructure.models import AssetRecord, JobRecord, ProjectRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.media_jobs import LocalMediaJobExecutor


def _create_test_video(path: Path, *, ffmpeg_path: Path) -> None:
    """Create a deterministic local fixture; this does not model a Provider response."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0xD9C6AF:s=480x854:r=24:d=8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def test_ffmpeg_export_validates_edl_source_and_records_rendered_asset(tmp_path: Path) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="FFmpeg EDL 测试", theme="折叠毛巾", targetDurationSeconds=8)
    )
    source_job_id = uuid.uuid4()
    source_asset_id = uuid.uuid4()
    media_store = LocalMediaStore(tmp_path / "media")
    ffmpeg_path = Path(os.environ["FFMPEG_PATH"])
    executor = LocalMediaJobExecutor(
        sessions,
        media_store,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=Path(os.environ["FFPROBE_PATH"]),
    )
    try:
        storage_key = f"generated/{project.id}/video/{source_job_id}.mp4"
        source_path = media_store.resolve(storage_key)
        _create_test_video(source_path, ffmpeg_path=ffmpeg_path)
        payload = source_path.read_bytes()
        source_sha256 = hashlib.sha256(payload).hexdigest()
        with sessions.begin() as session:
            session.add(
                JobRecord(
                    id=source_job_id,
                    project_id=project.id,
                    kind="generate_video",
                    status="succeeded",
                    input_hash="e" * 64,
                    idempotency_key=f"media-fixture-{source_job_id}",
                    provider="ark",
                    model="doubao-seedance-test-fixture",
                    provider_task_id=f"fixture-{source_job_id}",
                    expected_cost_micros=None,
                    frozen_input_json={"prompt": "local media fixture"},
                )
            )
            session.flush()
            session.add(
                AssetRecord(
                    id=source_asset_id,
                    project_id=project.id,
                    producing_job_id=source_job_id,
                    candidate_index=0,
                    role="video",
                    media_type="video",
                    storage_key=storage_key,
                    sha256=source_sha256,
                    byte_size=len(payload),
                    width=480,
                    height=854,
                    duration_ms=8000,
                    metadata_json={"fixture": "local-ffmpeg"},
                )
            )
        source = service.get_asset(source_asset_id)
        service.select_asset(project.id, slot="video", asset_id=source.id)
        edit = service.create_edit(
            project.id,
            EditCreateCommand(
                edl={
                    "sourceVideoSelections": [
                        {
                            "assetId": str(source.id),
                            "sha256": source.sha256,
                            "startMs": 0,
                            "endMs": 8000,
                        }
                    ],
                    "transitions": [{"afterClipIndex": 0, "type": "fade", "durationMs": 250}],
                    "audioPolicy": "mute",
                    "output": {
                        "aspectRatio": "9:16",
                        "width": 720,
                        "height": 1280,
                        "format": "mp4",
                    },
                }
            ),
        )
        export = service.create_export_job(
            project.id,
            ExportCommand(
                editVersionId=edit.id,
                idempotencyKey=f"edl-export-{project.id}",
            ),
        )

        executor.store_result(export.id)

        final = next(asset for asset in service.list_assets(project.id) if asset.role == "final")
        rendered_edit = service.list_edits(project.id)[0]
        assert final.producing_job_id == export.id
        assert final.metadata["width"] == 720
        assert final.metadata["height"] == 1280
        assert rendered_edit.status == "rendered"
        assert rendered_edit.rendered_asset_id == final.id
    finally:
        with sessions.begin() as session:
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
