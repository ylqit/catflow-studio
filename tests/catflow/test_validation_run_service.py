from __future__ import annotations

import pytest

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import (
    StudioConflictError,
    StudioService,
    ValidationRunCreateCommand,
)
from catflow.domain.validation import ValidationCallKind
from catflow.infrastructure.memory_repository import MemoryStudioRepository


def _ark_runtime() -> ProviderRuntime:
    return ProviderRuntime(
        provider="ark",
        planning_model="doubao-seed-2-1-pro-260628",
        image_model="doubao-seedream-5-0-260128",
        video_model="doubao-seedance-2-0-260128",
        diagnostic_model="doubao-seed-2-1-pro-260628",
        capability_revision="ark-seedance-2.0-v1",
        paid_calls_enabled=True,
        maximum_video_references=5,
        segment_reference_publishing_ready=True,
    )


def test_validation_run_authorization_freezes_manifest_without_creating_work() -> None:
    repository = MemoryStudioRepository()
    service = StudioService(repository, provider_runtime=_ark_runtime())
    preview = service.preview_validation_run()
    payload = preview.model_dump(by_alias=True)

    assert preview.total_call_limit == 10
    assert preview.maximum_video_calls == 4
    assert preview.models["video"] == "doubao-seedance-2-0-260128"
    assert preview.cost_estimate_status == "unmetered_paid"
    assert payload["canon"]["childAge"] == "6-7"
    assert payload["canon"]["childHeightCm"] == 120
    assert len(payload["canon"]["references"]) == 4
    assert [item["role"] for item in payload["canon"]["references"]] == [
        "episode_child",
        "episode_cat",
        "pair_scale",
        "style_board",
    ]

    run = service.authorize_validation_run(
        ValidationRunCreateCommand(
            expectedManifestHash=preview.manifest_hash,
            paidCallAcknowledged=True,
        )
    )

    assert run.status == "authorized"
    assert run.manifest_hash == preview.manifest_hash
    assert repository.list_projects() == []
    assert sum(run.usage.values()) == 0


def test_validation_run_cannot_authorize_when_paid_repair_transport_is_blocked() -> None:
    runtime = _ark_runtime()
    runtime = ProviderRuntime(
        provider=runtime.provider,
        planning_model=runtime.planning_model,
        image_model=runtime.image_model,
        video_model=runtime.video_model,
        diagnostic_model=runtime.diagnostic_model,
        capability_revision=runtime.capability_revision,
        paid_calls_enabled=runtime.paid_calls_enabled,
        maximum_video_references=runtime.maximum_video_references,
        segment_reference_publishing_ready=False,
    )
    service = StudioService(MemoryStudioRepository(), provider_runtime=runtime)
    preview = service.preview_validation_run()

    assert preview.authorization_ready is False
    assert preview.blocking_reasons == (
        "Ark 片段修复需要先把本地上下文视频安全发布为 Provider 可读取的 HTTPS URL；"
        "当前未配置受管发布器。",
    )
    with pytest.raises(StudioConflictError, match="HTTPS URL"):
        service.authorize_validation_run(
            ValidationRunCreateCommand(
                expectedManifestHash=preview.manifest_hash,
                paidCallAcknowledged=True,
            )
        )


def test_validation_run_rejects_manifest_drift_and_atomically_enforces_call_limits() -> None:
    service = StudioService(
        MemoryStudioRepository(),
        provider_runtime=_ark_runtime(),
    )
    preview = service.preview_validation_run()

    with pytest.raises(StudioConflictError, match="manifest changed"):
        service.authorize_validation_run(
            ValidationRunCreateCommand(
                expectedManifestHash="0" * 64,
                paidCallAcknowledged=True,
            )
        )

    run = service.authorize_validation_run(
        ValidationRunCreateCommand(
            expectedManifestHash=preview.manifest_hash,
            paidCallAcknowledged=True,
        )
    )
    for _ in range(3):
        service.reserve_validation_call(run.id, ValidationCallKind.GENERATE_VIDEO)

    with pytest.raises(StudioConflictError, match="generate_video limit"):
        service.reserve_validation_call(run.id, ValidationCallKind.GENERATE_VIDEO)

    persisted = service.get_validation_run(run.id)
    assert persisted.usage[ValidationCallKind.GENERATE_VIDEO] == 3
    assert sum(persisted.usage.values()) == 3
