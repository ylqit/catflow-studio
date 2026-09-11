from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from catflow.application.job_execution import GenerationPrepared, ReplacementGenerationCommand
from catflow.application.job_replacement import replace_unknown_job
from catflow.application.series import SeriesPatchCommand, SeriesPlanGenerationCommand
from catflow.application.service import StudioConflictError, StudioService
from catflow.infrastructure.models import JobRecord
from test_series_reception_integration import _postgres_service
from test_series_workflow import _series_command, _service


@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_replaced_unknown_plan_does_not_permanently_lock_series_targets(backend):
    engine = None
    sessions = None
    if backend == "postgres":
        engine, sessions, service = _postgres_service()
    else:
        service = _service()
    # Exercise Ark's replacement lifecycle using inert queued jobs in the test store.
    # No worker or provider gateway is started by this test.
    service = StudioService(
        service._repository,
        provider_runtime=replace(service.provider_runtime, provider="ark"),
    )
    try:
        series = service.create_story_series(_series_command().model_copy(update={
            "adaptation_policy": "condense_mainline",
        }))
        preview = service.preview_series_plan(series.id)
        old = service.create_series_plan_job(series.id, SeriesPlanGenerationCommand(
            expectedInputHash=preview.input_hash, idempotencyKey=f"old-targets-{series.id}",
        ))
        frozen = copy.deepcopy(old.frozen_input)
        if sessions:
            with sessions.begin() as session:
                record = session.get(JobRecord, old.id)
                record.status = "submitting"
                record.status = "submission_unknown"
        else:
            service._repository._jobs[old.id] = old.model_copy(
                update={"status": "submission_unknown"}
            )

        # A genuinely unresolved request still prevents changing the production target.
        with pytest.raises(StudioConflictError, match="规划任务尚未终结"):
            service.update_story_series(series.id, SeriesPatchCommand(mustKeep=[]))
        with pytest.raises(GenerationPrepared) as prepared:
            replace_unknown_job(service, old.id)
        replacement = replace_unknown_job(service, old.id, ReplacementGenerationCommand(
            inputHash=prepared.value.document["executionInputHash"],
            acknowledgeDuplicateCharge=True,
            idempotencyKey=f"replace-targets-{series.id}",
        ))
        assert replacement.supersedes_job_id == old.id
        # The active replacement owns the pending operation, so targets remain locked.
        with pytest.raises(StudioConflictError, match="规划任务尚未终结"):
            service.update_story_series(series.id, SeriesPatchCommand(mustKeep=[]))
        if sessions:
            with sessions.begin() as session:
                record = session.get(JobRecord, replacement.id)
                record.status = "submitting"
                record.status = "succeeded"
        else:
            service._repository._jobs[replacement.id] = replacement.model_copy(
                update={"status": "succeeded"}
            )

        updated = service.update_story_series(series.id, SeriesPatchCommand(mustKeep=[]))
        assert updated.must_keep == []
        assert service.get_job(old.id).status == "submission_unknown"
        assert service.get_job(old.id).frozen_input == frozen
        assert len(service.list_series_jobs(series.id)) == 2

        # Only the replaced unknown state is excluded, never another active lifecycle state.
        if sessions:
            with sessions.begin() as session:
                session.get(JobRecord, old.id).status = "polling"
        else:
            service._repository._jobs[old.id] = service.get_job(old.id).model_copy(
                update={"status": "polling"}
            )
        with pytest.raises(StudioConflictError, match="规划任务尚未终结"):
            service.update_story_series(series.id, SeriesPatchCommand(mustKeep=[]))
    finally:
        if engine:
            engine.dispose()
