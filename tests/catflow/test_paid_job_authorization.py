from __future__ import annotations

import pytest

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    CanonRevisionCreateCommand,
    ImageDiagnosisCommand,
    PlannerMessageCommand,
    ProjectCreate,
    StudioConflictError,
    StudioService,
    ValidationRunCreateCommand,
)
from catflow.domain.validation import ValidationCallKind
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def _ark_service() -> StudioService:
    return StudioService(
        MemoryStudioRepository(),
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="doubao-seed-2-1-pro-260628",
            image_model="doubao-seedream-5-0-260128",
            video_model="doubao-seedance-2-0-260128",
            diagnostic_model="doubao-seed-2-1-pro-260628",
            capability_revision="ark-seedance-2.0-v1",
            paid_calls_enabled=True,
            maximum_video_references=5,
            segment_reference_publishing_ready=True,
        ),
    )


def test_ark_planner_requires_authorized_run_and_reserves_once_per_idempotent_job() -> None:
    service = _ark_service()
    project = service.create_project(
        ProjectCreate(title="雨天擦爪", theme="雨天擦爪", targetDurationSeconds=12)
    )

    with pytest.raises(StudioConflictError, match="validation run"):
        service.enqueue_planner_message(
            project.id,
            PlannerMessageCommand(
                text="雨天擦爪",
                expectedContextRevision=1,
                idempotencyKey="ark-planner-missing-run",
            ),
        )

    preview = service.preview_validation_run()
    run = service.authorize_validation_run(
        ValidationRunCreateCommand(
            expectedManifestHash=preview.manifest_hash,
            paidCallAcknowledged=True,
        )
    )
    command = PlannerMessageCommand(
        text="雨天擦爪",
        expectedContextRevision=1,
        idempotencyKey="ark-planner-one-reservation",
        validationRunId=run.id,
        paidCallAcknowledged=True,
    )

    first = service.enqueue_planner_message(project.id, command)
    same = service.enqueue_planner_message(project.id, command)

    assert same.id == first.id
    assert first.provider == "ark"
    assert first.model == "doubao-seed-2-1-pro-260628"
    assert first.expected_cost_micros is None
    assert first.validation_run_id == run.id
    assert first.frozen_input["outputSchema"]["required"] == [
        "title",
        "summary",
        "body",
        "trigger",
        "childAction",
        "catResponse",
        "visibleChange",
        "warmEnding",
        "targetDurationSeconds",
        "dialoguePolicy",
        "environmentIntent",
    ]
    assert "原地互看" in str(first.frozen_input["prompt"])
    persisted = service.get_validation_run(run.id)
    assert persisted.usage[ValidationCallKind.PLAN_STORY] == 1


def test_ark_image_generation_and_diagnosis_consume_their_exact_allowances() -> None:
    service = _ark_service()
    project = service.create_project(
        ProjectCreate(title="共享室内环境", theme="雨天擦爪", targetDurationSeconds=12)
    )
    manifest = service.preview_validation_run()
    run = service.authorize_validation_run(
        ValidationRunCreateCommand(
            expectedManifestHash=manifest.manifest_hash,
            paidCallAcknowledged=True,
        )
    )
    preview = service.preview_asset_generation(
        project.id, AssetGenerationPreviewCommand(kind="environment")
    )
    image_job = service.create_asset_generation_job(
        project.id,
        AssetGenerationCommand(
            kind="environment",
            expectedInputHash=preview.input_hash,
            expectedCostMicros=None,
            idempotencyKey="ark-shared-environment",
            validationRunId=run.id,
            paidCallAcknowledged=True,
        ),
    )
    candidate = service.register_asset(
        project.id,
        role="environment",
        sha256="a" * 64,
        producing_job_id=image_job.id,
    )
    diagnosis = service.create_image_diagnosis_job(
        project.id,
        ImageDiagnosisCommand(
            assetId=candidate.id,
            idempotencyKey="ark-shared-environment-diagnosis",
            validationRunId=run.id,
            paidCallAcknowledged=True,
        ),
    )

    assert image_job.provider == diagnosis.provider == "ark"
    assert image_job.expected_cost_micros is diagnosis.expected_cost_micros is None
    assert diagnosis.frozen_input["outputSchema"]["required"] == [
        "identity",
        "style",
        "anatomy",
        "technical",
        "warnings",
    ]
    usage = service.get_validation_run(run.id).usage
    assert usage[ValidationCallKind.GENERATE_IMAGE] == 1
    assert usage[ValidationCallKind.DIAGNOSE_IMAGE] == 1

    with pytest.raises(StudioConflictError, match="already has this project call"):
        service.create_asset_generation_job(
            project.id,
            AssetGenerationCommand(
                kind="environment",
                expectedInputHash=preview.input_hash,
                expectedCostMicros=None,
                idempotencyKey="ark-shared-environment-second-click",
                validationRunId=run.id,
                paidCallAcknowledged=True,
            ),
        )
    assert service.get_validation_run(run.id).usage[ValidationCallKind.GENERATE_IMAGE] == 1


def test_project_bound_to_an_older_canon_cannot_spend_a_new_validation_run() -> None:
    service = _ark_service()
    old_project = service.create_project(
        ProjectCreate(title="雨天擦爪", theme="雨天擦爪", targetDurationSeconds=12)
    )
    uploaded = {
        role: service.register_canon_asset(
            role=role,
            sha256=f"{index:x}" * 64,
            storage_key=f"canon/new/{role}.png",
            byte_size=100,
        )
        for index, role in enumerate(
            ("episode_child", "episode_cat", "pair_scale", "style_board"), start=5
        )
    }
    service.publish_canon_revision(
        CanonRevisionCreateCommand(
            fixedAssets={role: asset.id for role, asset in uploaded.items()}
        )
    )
    preview = service.preview_validation_run()
    run = service.authorize_validation_run(
        ValidationRunCreateCommand(
            expectedManifestHash=preview.manifest_hash,
            paidCallAcknowledged=True,
        )
    )

    with pytest.raises(StudioConflictError, match="Project Canon|project Canon"):
        service.enqueue_planner_message(
            old_project.id,
            PlannerMessageCommand(
                text="雨天擦爪",
                expectedContextRevision=1,
                idempotencyKey="old-canon-project-planning",
                validationRunId=run.id,
                paidCallAcknowledged=True,
            ),
        )
