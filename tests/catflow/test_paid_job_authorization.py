from __future__ import annotations

from datetime import UTC, datetime

import pytest

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    ImageDiagnosisCommand,
    PlannerMessageCommand,
    ProjectCreate,
    RateCardRevisionCreateCommand,
    StudioConflictError,
    StudioService,
)
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def _ark_service(*, paid_calls_enabled: bool = True) -> StudioService:
    return StudioService(
        MemoryStudioRepository(),
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="doubao-seed-2-1-pro-260628",
            image_model="doubao-seedream-5-0-260128",
            video_model="doubao-seedance-2-0-260128",
            diagnostic_model="doubao-seed-2-1-pro-260628",
            capability_revision="ark-seedance-2.0-v1",
            paid_calls_enabled=paid_calls_enabled,
            maximum_video_references=5,
            segment_reference_publishing_ready=True,
        ),
    )


def _project(service: StudioService):  # type: ignore[no-untyped-def]
    return service.create_project(
        ProjectCreate(title="雨天擦爪", theme="雨天擦爪", targetDurationSeconds=12)
    )


def test_ark_planner_submits_without_a_validation_run_and_remains_idempotent() -> None:
    service = _ark_service()
    project = _project(service)
    command = PlannerMessageCommand(
        text="雨天擦爪",
        expectedContextRevision=1,
        idempotencyKey="ark-planner-direct-submit",
    )

    first = service.enqueue_planner_message(project.id, command)
    same = service.enqueue_planner_message(project.id, command)

    assert same.id == first.id
    assert first.provider == "ark"
    assert first.model == "doubao-seed-2-1-pro-260628"
    assert first.expected_cost_micros is None
    assert first.validation_run_id is None
    assert "原地互看" in str(first.frozen_input["prompt"])


def test_normal_ark_image_jobs_have_no_application_quota() -> None:
    service = _ark_service()
    project = _project(service)
    preview = service.preview_asset_generation(
        project.id, AssetGenerationPreviewCommand(kind="environment")
    )

    first = service.create_asset_generation_job(
        project.id,
        AssetGenerationCommand(
            kind="environment",
            expectedInputHash=preview.input_hash,
            idempotencyKey="ark-environment-first",
        ),
    )
    same = service.create_asset_generation_job(
        project.id,
        AssetGenerationCommand(
            kind="environment",
            expectedInputHash=preview.input_hash,
            idempotencyKey="ark-environment-first",
        ),
    )
    second = service.create_asset_generation_job(
        project.id,
        AssetGenerationCommand(
            kind="environment",
            expectedInputHash=preview.input_hash,
            idempotencyKey="ark-environment-second",
        ),
    )

    assert same.id == first.id
    assert second.id != first.id
    assert first.validation_run_id is second.validation_run_id is None

    candidate = service.register_asset(
        project.id,
        role="environment",
        sha256="a" * 64,
        producing_job_id=first.id,
    )
    diagnosis = service.create_image_diagnosis_job(
        project.id,
        ImageDiagnosisCommand(
            assetId=candidate.id,
            idempotencyKey="ark-environment-diagnosis",
        ),
    )
    assert diagnosis.provider == "ark"
    assert diagnosis.validation_run_id is None


def test_global_paid_provider_switch_still_blocks_direct_submission() -> None:
    service = _ark_service(paid_calls_enabled=False)
    project = _project(service)

    with pytest.raises(StudioConflictError, match="paid provider calls are disabled"):
        service.enqueue_planner_message(
            project.id,
            PlannerMessageCommand(
                text="雨天擦爪",
                expectedContextRevision=1,
                idempotencyKey="ark-planner-disabled",
            ),
        )


def test_paid_job_freezes_the_active_model_rate_revision_at_creation() -> None:
    service = _ark_service()
    service.publish_rate_card(
        RateCardRevisionCreateCommand(
            provider="ark",
            model="doubao-seed-2-1-pro-260628",
            revision="planning-rates-2026-09-02",
            sourceUrl="https://example.test/ark-rates",
            effectiveFrom=datetime.now(UTC),
            rates=[
                {
                    "metric": "inputTokens",
                    "unit": "million_tokens",
                    "unitPriceMicros": 2_000_000,
                }
            ],
        )
    )
    project = _project(service)

    job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="雨天擦爪",
            expectedContextRevision=1,
            idempotencyKey="ark-planner-frozen-rate",
        ),
    )

    assert job.rate_card_revision == "planning-rates-2026-09-02"
    assert job.pricing_snapshot is not None
    assert job.pricing_snapshot["rates"] == [
        {
            "metric": "inputTokens",
            "unit": "million_tokens",
            "unitPriceMicros": 2_000_000,
        }
    ]
