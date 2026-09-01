from __future__ import annotations

from catflow.application.service import (
    EditCreateCommand,
    ExportCommand,
    FinalSelectionCommand,
    ProjectCreate,
    StudioConflictError,
    StudioService,
)
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def test_edit_export_and_final_selection_keep_source_hashes_and_versions() -> None:
    service = StudioService(MemoryStudioRepository())
    project = service.create_project(
        ProjectCreate(title="导出测试", theme="窗边阳光", targetDurationSeconds=8)
    )
    video = service.register_asset(
        project.id,
        role="video",
        media_type="video",
        sha256="a" * 64,
        storage_key="video/source.mp4",
        byte_size=100,
    )
    service.select_asset(project.id, slot="video", asset_id=video.id)
    edit = service.create_edit(
        project.id,
        EditCreateCommand(
            edl={
                "sourceVideoSelections": [
                    {
                        "assetId": str(video.id),
                        "sha256": video.sha256,
                        "startMs": 0,
                        "endMs": 8000,
                    }
                ],
                "transitions": [{"afterClipIndex": 0, "type": "fade", "durationMs": 250}],
                "audioPolicy": "native_fades",
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
        ExportCommand(editVersionId=edit.id, idempotencyKey="export-simple-edl"),
    )
    same_export = service.create_export_job(
        project.id,
        ExportCommand(editVersionId=edit.id, idempotencyKey="export-simple-edl"),
    )
    final = service.register_asset(
        project.id,
        role="final",
        media_type="video",
        sha256="b" * 64,
        storage_key="final/output.mp4",
        byte_size=80,
        producing_job_id=export.id,
    )
    selection = service.approve_final(project.id, FinalSelectionCommand(assetId=final.id))

    assert edit.revision == 1
    assert edit.source_selection_hash
    assert export.id == same_export.id
    assert export.kind == "render_export"
    assert selection.slot == "final"
    assert selection.decision == "approved"

    changed_video = service.register_asset(
        project.id,
        role="video",
        media_type="video",
        sha256="c" * 64,
        storage_key="video/changed.mp4",
        byte_size=100,
    )
    service.select_asset(project.id, slot="video", asset_id=changed_video.id)
    try:
        service.create_export_job(
            project.id,
            ExportCommand(editVersionId=edit.id, idempotencyKey="export-stale-edl"),
        )
    except StudioConflictError as exc:
        assert "outdated" in str(exc)
    else:
        raise AssertionError("an outdated EDL must not be exported")
