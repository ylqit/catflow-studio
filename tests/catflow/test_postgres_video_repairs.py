from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import delete

from catflow.application.service import (
    ProjectCreate,
    SegmentRepairApproveCommand,
    SegmentRepairCreateCommand,
    SegmentRepairPreviewCommand,
    StudioService,
)
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import EnvironmentPresetRecord, ProjectRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository


def test_postgres_recovers_repair_job_and_approves_one_active_edit_version() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="PostgreSQL 片段修复", theme="雨天擦爪", targetDurationSeconds=12)
    )

    try:
        base = service.register_asset(
            project.id,
            role="video",
            media_type="video",
            sha256="d" * 64,
            metadata={"durationFrames": 288, "frameRateNumerator": 24, "frameRateDenominator": 1},
        )
        environment = service.register_asset(
            project.id, role="environment", media_type="image", sha256="e" * 64
        )
        service.select_asset(project.id, slot="video", asset_id=base.id)
        service.select_asset(project.id, slot="environment", asset_id=environment.id)
        preview = service.preview_video_repair(
            project.id,
            SegmentRepairPreviewCommand(
                baseVideoAssetId=base.id,
                issueRange={"startFrame": 96, "endFrame": 192},
                prompt="只重拍擦爪动作。",
            ),
        )
        job = service.create_video_repair_job(
            project.id,
            SegmentRepairCreateCommand(
                repairId=preview.repair_id,
                expectedInputHash=preview.input_hash,
                expectedCostMicros=preview.expected_cost_micros,
                idempotencyKey=f"postgres-repair-{project.id}",
                paidConfirmation=True,
            ),
        )
        candidate = service.register_asset(
            project.id,
            role="repair_candidate",
            media_type="video",
            sha256="f" * 64,
            producing_job_id=job.id,
            metadata={"durationFrames": 144, "frameRateNumerator": 24, "frameRateDenominator": 1},
        )
        service.mark_video_repair_candidate_ready(preview.repair_id, candidate.id)

        recovered = StudioService(PostgresStudioRepository(sessions))
        assert recovered.get_job(job.id).video_repair_id == preview.repair_id
        assert recovered.get_video_repair(preview.repair_id).candidate_asset_id == candidate.id
        edit = recovered.approve_video_repair(
            project.id,
            preview.repair_id,
            SegmentRepairApproveCommand(
                candidateAssetId=candidate.id,
                candidateSourceRange={"startFrame": 25, "endFrame": 121},
                transition={"type": "cut", "durationFrames": 0},
                expectedBaseTimelineHash=preview.base_timeline_hash,
                idempotencyKey=f"postgres-repair-approval-{project.id}",
                qualityChecks={
                    "child_identity": "pass",
                    "cat_identity": "pass",
                    "pair_scale": "pass",
                    "style": "pass",
                    "structure": "pass",
                    "motion_continuity": "pass",
                    "causal_chain": "pass",
                },
                seamChecks={"in": "pass", "out": "pass"},
            ),
        )

        assert edit.active is True
        assert edit.format_version == 2
        assert recovered.list_edits(project.id)[0].id == edit.id
        approved_repair = recovered.get_video_repair(preview.repair_id)
        assert approved_repair.approved_edit_version_id == edit.id
        assert approved_repair.candidate_core_range.start_frame == 25
        assert approved_repair.candidate_core_range.end_frame == 121
    finally:
        with sessions.begin() as session:
            session.execute(
                delete(EnvironmentPresetRecord).where(
                    EnvironmentPresetRecord.source_project_id == project.id
                )
            )
            session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
        engine.dispose()
