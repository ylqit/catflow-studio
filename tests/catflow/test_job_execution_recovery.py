"""Isolated rules/DB tests. These are not evidence of real Ark model behavior."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from catflow.application.gateways import ProviderGatewayError
from catflow.application.job_execution import JobRecoveryCommand, summarize_execution
from catflow.application.service import ProjectCreate, StudioConflictError, StudioService
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.job_lifecycle import (
    LostJobLease,
    apply_provider_receipt,
    materialization_lease,
    query_retry,
)
from catflow.infrastructure.models import JobEventRecord, JobRecord, ProjectRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from catflow_worker.provider_receipts import ReceiptJournal
from catflow_worker.runner import DurableJobWorker


def summary(**changes):
    values = {
        "kind": "plan_shots",
        "provider": "ark",
        "status": "submission_unknown",
        "facts": None,
        "task_id": None,
        "response_id": None,
        "client_request_id": None,
        "result": None,
        "error": None,
        "usage": None,
        "submitted_at": datetime.now(UTC),
    }
    values.update(changes)
    return summarize_execution(**values)


def test_unknown_trace_is_not_queryable_and_missing_usage_is_unconfirmed():
    value = summary(error={"requestId": "resp-looking-trace"})
    assert value.provider_response_id is None
    assert value.result_state == "missing"
    assert value.usage_unconfirmed
    assert "query_provider" not in value.available_actions
    assert "prepare_replacement" in value.available_actions
    assert "cancel" not in value.available_actions


def test_explicit_legacy_response_id_does_not_promise_retrieval():
    value = summary(result={"responseId": "known-response", "payload": {"shots": []}})
    assert value.provider_response_id == "known-response"
    assert "query_provider" not in value.available_actions


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed"])
def test_query_errors_preserve_last_external_fact(status):
    now = datetime.now(UTC)
    facts = {"providerStatus": status, "providerObservedAt": now.isoformat()}
    delays = []
    for _ in range(12):
        facts, due = query_retry(facts, {"code": "timeout"}, now=now)
        assert facts["providerStatus"] == status
        assert facts["providerObservedAt"] == now.isoformat()
        if due:
            delays.append((due - now).total_seconds())
    assert delays[:5] == [5, 10, 20, 40, 60]
    assert due is None and facts["recoveryState"] == "needs_attention"


def test_retry_after_and_query_window():
    now = datetime.now(UTC)
    _, due = query_retry({}, {"retryAfterSeconds": 180}, now=now)
    assert (due - now).total_seconds() == 180
    facts, due = query_retry(
        {"queryWindowStartedAt": (now - timedelta(minutes=31)).isoformat()},
        {"code": "timeout"},
        now=now,
    )
    assert due is None
    assert facts["recoveryState"] == "needs_attention"


@pytest.mark.parametrize(
    "code,paused",
    [("local_adapter_error", True), ("provider_timeout", False), ("provider_error", False)],
)
def test_local_adapter_errors_pause_but_transport_errors_still_recover(code, paused):
    facts, due = query_retry(
        {"providerStatus": "submitted"}, {"code": code, "retryable": False}, now=datetime.now(UTC)
    )
    assert (due is None) == paused
    assert facts["providerStatus"] == "submitted"
    assert facts["queryFailureCount"] == 1


@pytest.mark.parametrize("code", [401, 403, 404, 410])
def test_query_access_or_expiry_is_attention_not_generation_failure(code):
    facts, due = query_retry(
        {"providerStatus": "running"}, {"httpStatus": code}, now=datetime.now(UTC)
    )
    assert due is None and facts["providerStatus"] == "running"


def test_journal_is_atomic_and_keeps_body_without_credentials(tmp_path):
    journal = ReceiptJournal(tmp_path)
    identifier = uuid.uuid4()
    receipt = journal.append(
        identifier,
        {
            "responseId": "r1",
            "result": {"rawText": "{broken"},
            "Authorization": "secret",
            "api_key": "secret",
        },
    )
    restored = journal.read(identifier)
    assert restored == [receipt]
    assert "secret" not in json.dumps(restored)
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.fixture
def database():
    engine = create_database_engine(DatabaseSettings.from_env())
    sessions = create_session_factory(engine)
    service = StudioService(PostgresStudioRepository(sessions))
    project = service.create_project(
        ProjectCreate(title="恢复内部验证", theme="测试", targetDurationSeconds=12)
    )
    yield sessions, PostgresStudioRepository(sessions), project.id
    with sessions.begin() as session:
        session.execute(delete(ProjectRecord).where(ProjectRecord.id == project.id))
    engine.dispose()


def add_job(sessions, project, **values):
    identifier = uuid.uuid4()
    with sessions.begin() as session:
        session.add(
            JobRecord(
                id=identifier,
                project_id=project,
                kind="generate_video",
                input_hash="a" * 64,
                idempotency_key=str(identifier),
                provider="ark",
                model="isolated-rules",
                frozen_input_json=values.pop("frozen_input_json", {}),
                **values,
            )
        )
    return identifier


class NoCreateProvider:
    def submit(self, **_kwargs):
        pytest.fail("recovery must not invoke model creation")

    def poll(self, task_id):
        raise ProviderGatewayError(
            code="provider_timeout", message="isolated timeout", retryable=True
        )


def test_worker_query_timeout_releases_lease_and_preserves_provider_state(database, tmp_path):
    sessions, repository, project = database
    identifier = add_job(
        sessions,
        project,
        status="submitted",
        provider_task_id="existing-task",
        execution_json={"providerStatus": "running"},
    )
    worker = DurableJobWorker(
        sessions, NoCreateProvider(), worker_id="query-test", receipt_root=tmp_path
    )
    assert worker.run_once()
    job = repository.get_job(identifier)
    assert job.execution.provider_status == "running"
    assert job.execution.query_failure_count == 1
    assert job.status != "failed" and job.next_action_at is not None
    with sessions() as session:
        row = session.get(JobRecord, identifier)
        assert row.locked_by is None and row.leased_until is None
    assert not worker.run_once()


def test_receipt_deduplication_and_terminal_conflict(database, tmp_path):
    sessions, repository, project = database
    identifier = add_job(sessions, project, status="submitted")
    journal = ReceiptJournal(tmp_path)
    first = journal.append(
        identifier,
        {
            "taskId": "task",
            "providerStatus": "succeeded",
            "complete": True,
            "result": {"videoUrl": "https://example.volces.com/video"},
        },
    )
    late = journal.append(identifier, {"taskId": "task", "providerStatus": "running"})
    with sessions.begin() as session:
        row = session.get(JobRecord, identifier)
        assert apply_provider_receipt(session, row, first)
    with sessions.begin() as session:
        row = session.get(JobRecord, identifier)
        assert not apply_provider_receipt(session, row, first)
        assert apply_provider_receipt(session, row, late)
    job = repository.get_job(identifier)
    assert job.execution.provider_status == "succeeded"
    assert job.execution.recovery_state == "needs_attention"
    with sessions() as session:
        assert (
            len(
                session.scalars(
                    select(JobEventRecord).where(JobEventRecord.job_id == identifier)
                ).all()
            )
            == 2
        )


def test_local_recovery_command_is_idempotent_and_rejects_changed_revision(database):
    sessions, repository, project = database
    identifier = add_job(
        sessions,
        project,
        status="failed",
        provider_task_id="task",
        provider_result_json={"videoUrl": "https://example.volces.com/video"},
        execution_json={"providerStatus": "succeeded", "resultComplete": True},
    )
    command = JobRecoveryCommand(
        action="process_result", expectedRevision=0, idempotencyKey="one-recovery"
    )
    first = repository.recover_job(identifier, command)
    again = repository.recover_job(identifier, command)
    assert first.revision == again.revision and again.status == "storing"
    with pytest.raises(StudioConflictError):
        repository.recover_job(
            identifier, command.model_copy(update={"idempotency_key": "another-recovery"})
        )


def test_materialization_rejects_old_worker_epoch(database):
    sessions, _, project = database
    identifier = add_job(
        sessions,
        project,
        status="storing",
        locked_by="new-worker",
        lease_epoch=2,
        leased_until=datetime.now(UTC) + timedelta(seconds=60),
    )
    token = materialization_lease.set((identifier, "old-worker", 1))
    try:
        with pytest.raises(LostJobLease), sessions.begin() as session:
            row = session.get(ProjectRecord, project)
            row.title = "must not persist"
    finally:
        materialization_lease.reset(token)
    with sessions() as session:
        assert session.get(ProjectRecord, project).title != "must not persist"


def test_worker_restores_received_task_after_restart_without_create(database, tmp_path):
    sessions, repository, project = database
    identifier = add_job(
        sessions, project, status="submitting", provider_submission_started_at=datetime.now(UTC)
    )
    ReceiptJournal(tmp_path).append(
        identifier, {"taskId": "already-accepted", "providerStatus": "submitted"}
    )
    worker = DurableJobWorker(
        sessions, NoCreateProvider(), worker_id="restart", receipt_root=tmp_path
    )
    assert worker.run_once()
    job = repository.get_job(identifier)
    assert job.provider_task_id == "already-accepted" and job.status == "polling"


def test_late_task_receipt_recovers_unknown_without_submission(database, tmp_path):
    sessions, repository, project = database
    identifier = add_job(sessions, project, status="submission_unknown")
    ReceiptJournal(tmp_path).append(
        identifier,
        {
            "taskId": "late-task",
            "providerStatus": "running",
            "serverRequestId": "request-one",
        },
    )
    worker = DurableJobWorker(sessions, NoCreateProvider(), worker_id="late", receipt_root=tmp_path)
    assert worker.run_once()
    job = repository.get_job(identifier)
    assert job.provider_task_id == "late-task" and job.status == "polling"
    assert job.execution.provider_status == "running"


def test_query_lane_does_not_wait_for_unrelated_historical_receipts(database, tmp_path):
    sessions, repository, project = database
    historical = add_job(
        sessions, project, status="succeeded", execution_json={"providerStatus": "succeeded"}
    )
    ReceiptJournal(tmp_path).append(historical, {"providerStatus": "failed"})
    current = add_job(
        sessions, project, status="submitted", provider_task_id="accepted-current-task"
    )
    query = DurableJobWorker(
        sessions, NoCreateProvider(), worker_id="active-query", receipt_root=tmp_path, lane="query"
    )
    assert query.run_once()
    # The current task is queried; historical reconciliation has its own lane.
    assert repository.get_job(current).execution.query_failure_count == 1
    assert not repository.get_job(historical).execution_facts.get("receiptConflict")

    recovery = DurableJobWorker(
        sessions, NoCreateProvider(), worker_id="receipt-recovery",
        receipt_root=tmp_path, lane="reconcile",
    )
    recovery.run_once()
    assert repository.get_job(historical).execution_facts["receiptConflict"] is True


def test_reconciliation_lane_restores_unknown_without_claiming_provider_work(database, tmp_path):
    sessions, repository, project = database
    identifier = add_job(sessions, project, status="submission_unknown")
    ReceiptJournal(tmp_path).append(
        identifier, {"taskId": "late-accepted-task", "providerStatus": "running"}
    )
    recovery = DurableJobWorker(
        sessions, NoCreateProvider(), worker_id="receipt-only",
        receipt_root=tmp_path, lane="reconcile",
    )
    recovery.run_once()
    job = repository.get_job(identifier)
    assert job.provider_task_id == "late-accepted-task"
    assert job.status == "polling"
    assert job.execution.query_failure_count == 0
    with sessions() as session:
        assert session.get(JobRecord, identifier).lease_epoch == 0

    query = DurableJobWorker(
        sessions, NoCreateProvider(), worker_id="restored-query",
        receipt_root=tmp_path, lane="query",
    )
    assert query.run_once()
    assert repository.get_job(identifier).execution.query_failure_count == 1


def test_new_query_request_id_is_not_a_task_conflict(database, tmp_path):
    sessions, repository, project = database
    identifier = add_job(sessions, project, status="submitted")
    journal = ReceiptJournal(tmp_path)
    for request_id in ("create-request", "query-request"):
        receipt = journal.append(
            identifier,
            {
                "taskId": "one-task",
                "providerStatus": "running",
                "serverRequestId": request_id,
            },
        )
        with sessions.begin() as session:
            apply_provider_receipt(session, session.get(JobRecord, identifier), receipt)
    job = repository.get_job(identifier)
    assert job.provider_request_id == "query-request"
    assert job.execution.recovery_state == "none"


def test_bulk_materialization_also_rejects_old_worker(database):
    sessions, _, project = database
    identifier = add_job(
        sessions,
        project,
        status="storing",
        locked_by="new",
        lease_epoch=2,
        leased_until=datetime.now(UTC) + timedelta(seconds=60),
    )
    token = materialization_lease.set((identifier, "old", 1))
    try:
        with pytest.raises(LostJobLease), sessions.begin() as session:
            session.execute(
                update(ProjectRecord).where(ProjectRecord.id == project).values(title="bad")
            )
    finally:
        materialization_lease.reset(token)


def test_unknown_replacement_binds_consent_and_does_not_overwrite_late_story(database, tmp_path):
    from catflow.application.job_execution import GenerationPrepared, ReplacementGenerationCommand
    from catflow.application.job_replacement import replace_unknown_job
    from catflow.application.provider_config import ProviderRuntime
    from catflow.application.service import PlannerMessageCommand

    sessions, repository, project = database
    service = StudioService(
        repository,
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="offline-plan",
            diagnostic_model="offline-plan",
            image_model="offline-image",
            video_model="offline-video",
            capability_revision="ark-seedance-2.0-v1",
            paid_calls_enabled=True,
            maximum_video_references=5,
            segment_reference_publishing_ready=True,
        ),
    )
    old = service.enqueue_planner_message(
        project,
        PlannerMessageCommand(
            text="内部恢复测试",
            expectedContextRevision=1,
            idempotencyKey=f"original-{project}",
        ),
    )
    with sessions.begin() as session:
        row = session.get(JobRecord, old.id)
        row.status = "submitting"
        row.status = "submission_unknown"
        row.provider_submission_started_at = datetime.now(UTC)
    with pytest.raises(GenerationPrepared) as prepared:
        replace_unknown_job(service, old.id)
    with sessions() as session:
        assert (
            len(session.scalars(select(JobRecord).where(JobRecord.project_id == project)).all())
            == 1
        )
    command = ReplacementGenerationCommand(
        inputHash=prepared.value.document["executionInputHash"],
        acknowledgeDuplicateCharge=True,
        idempotencyKey=f"replacement-{project}",
    )
    created = replace_unknown_job(service, old.id, command)
    assert created.supersedes_job_id == old.id
    assert replace_unknown_job(service, old.id, command).id == created.id
    with pytest.raises(StudioConflictError):
        replace_unknown_job(
            service, old.id, command.model_copy(update={"idempotency_key": "another-key"})
        )
    assert service.get_job(old.id).status == "submission_unknown"
    assert service.get_job(old.id).successor_job_ids == [created.id]

    class NeverOverwrite:
        def store_result(self, _job_id):
            pytest.fail("late story must not replace current planner context")

    with sessions.begin() as session:
        row = session.get(JobRecord, old.id)
        row.status = "storing"
        row.provider_result_json = {"payload": {"title": "late"}}
        row.execution_json = {
            **row.execution_json,
            "resultComplete": True,
            "providerStatus": "completed",
        }
    worker = DurableJobWorker(
        sessions,
        NoCreateProvider(),
        worker_id="late-story",
        result_handler=NeverOverwrite(),
        receipt_root=tmp_path,
        lane="local",
    )
    assert worker.run_once()
    historical = service.get_job(old.id)
    assert historical.execution.historical_result and historical.status == "succeeded"
    assert service.get_job(created.id).status == "queued"


def test_materialization_allows_multiple_flushes_under_one_transaction(database):
    sessions, _, project = database
    identifier = add_job(
        sessions,
        project,
        status="storing",
        locked_by="owner",
        lease_epoch=1,
        leased_until=datetime.now(UTC) + timedelta(seconds=60),
    )
    token = materialization_lease.set((identifier, "owner", 1))
    try:
        with sessions.begin() as session:
            session.get(JobRecord, identifier).status = "succeeded"
            session.flush()
            session.get(ProjectRecord, project).title = "saved in the same fenced transaction"
    finally:
        materialization_lease.reset(token)


def test_confirmed_query_resolves_stale_status_conflict_but_not_identifier_conflict(
    database, tmp_path
):
    sessions, repository, project = database
    identifier = add_job(
        sessions,
        project,
        status="submitted",
        provider_task_id="original",
        execution_json={"providerStatus": "succeeded", "receiptConflict": True},
    )
    journal = ReceiptJournal(tmp_path)
    for task, expected in (("original", False), ("different", True), ("original", True)):
        receipt = journal.append(
            identifier, {"taskId": task, "providerStatus": "succeeded", "confirmedQuery": True}
        )
        with sessions.begin() as session:
            apply_provider_receipt(session, session.get(JobRecord, identifier), receipt)
        assert repository.get_job(identifier).execution_facts["receiptConflict"] is expected


def test_corrupt_receipt_pauses_its_job_without_resubmission(database, tmp_path):
    sessions, repository, project = database
    identifier = add_job(sessions, project, status="queued")
    directory = tmp_path / str(identifier)
    directory.mkdir()
    (directory / "broken.json").write_text("incomplete-json")
    worker = DurableJobWorker(
        sessions, NoCreateProvider(), worker_id="corrupt-receipt", receipt_root=tmp_path
    )
    assert not worker.run_once()
    assert repository.get_job(identifier).execution.query_error["code"] == "receipt_read_error"


class SimulatedTaskReader:
    def __init__(self, task=None, *, fail=False, full=False):
        self.task = task
        self.fail = fail
        self.full = full
        self.get_calls = []

    def list_tasks(self, **kwargs):
        if self.fail:
            raise RuntimeError("secret provider error https://private.example")
        return [self.task] * (50 if self.full else 1) if self.task else []

    def get_task(self, task_id):
        self.get_calls.append(task_id)
        if self.fail:
            raise RuntimeError("private")
        return dict(self.task)


def test_cloud_lookup_and_confirmed_association_then_poll_without_create(database, tmp_path):
    sessions, repository, project = database
    now = datetime.now(UTC)
    identifier = add_job(
        sessions, project, status="submission_unknown", provider_submission_started_at=now
    )
    reader = SimulatedTaskReader(
        {
            "id": "cloud-task",
            "model": "isolated-rules",
            "created_at": int(now.timestamp()),
            "status": "running",
            "content": {"video_url": "secret"},
        }
    )
    service = StudioService(repository, provider_task_reader=reader)
    found = service.lookup_provider_tasks(identifier)
    assert found.status == "candidates" and found.coverage_complete
    assert found.candidates[0].can_associate
    assert "secret" not in found.model_dump_json()
    assert repository.get_job(identifier).provider_task_id is None
    command = JobRecoveryCommand(
        action="associate_provider_task",
        providerTaskId="cloud-task",
        expectedRevision=0,
        idempotencyKey="cloud-association",
        confirmAssociation=True,
        acknowledgeUnverifiedParameters=True,
    )
    with pytest.raises(StudioConflictError):
        service.recover_job(identifier, command.model_copy(update={"confirm_association": False}))
    first = service.recover_job(identifier, command)
    assert first.status == "polling" and first.provider_task_id == "cloud-task"
    assert reader.get_calls == ["cloud-task"]
    reader.fail = True
    assert service.recover_job(identifier, command).revision == first.revision
    reader.fail = False
    duplicate = add_job(
        sessions,
        project,
        status="submission_unknown",
        provider_submission_started_at=now,
        frozen_input_json={"targetShotId": "other-shot"},
    )
    with pytest.raises(StudioConflictError, match="已关联"):
        service.recover_job(duplicate, command)
    worker = DurableJobWorker(
        sessions, NoCreateProvider(), worker_id="association-test", receipt_root=tmp_path
    )
    assert worker.run_once()
    assert repository.get_job(identifier).execution.query_failure_count == 1


@pytest.mark.parametrize("case", ["empty", "failed", "bounded", "mismatch", "stale", "lease"])
def test_cloud_lookup_failures_and_association_guards(database, case):
    sessions, repository, project = database
    now = datetime.now(UTC)
    identifier = add_job(
        sessions,
        project,
        status="submission_unknown",
        provider_submission_started_at=now,
        leased_until=now + timedelta(minutes=1) if case == "lease" else None,
    )
    task = {
        "id": "cloud-task",
        "model": "isolated-rules",
        "status": "running",
        "created_at": int(now.timestamp()),
    }
    if case == "mismatch":
        task["model"] = "wrong-model"
    reader = SimulatedTaskReader(
        None if case == "empty" else task, fail=case == "failed", full=case == "bounded"
    )
    service = StudioService(repository, provider_task_reader=reader)
    result = service.lookup_provider_tasks(identifier)
    if case == "empty":
        assert result.status == "not_found" and result.coverage_complete
    elif case == "failed":
        assert result.status == "query_failed" and "private" not in result.model_dump_json()
    elif case == "bounded":
        assert not result.coverage_complete and "覆盖不完整" in result.message
    else:
        command = JobRecoveryCommand(
            action="associate_provider_task",
            providerTaskId="cloud-task",
            expectedRevision=99 if case == "stale" else 0,
            idempotencyKey="cloud-guard",
            confirmAssociation=True,
            acknowledgeUnverifiedParameters=True,
        )
        with pytest.raises(StudioConflictError):
            service.recover_job(identifier, command)
        assert repository.get_job(identifier).provider_task_id is None


def test_association_parameter_mismatches_and_missing_evidence():
    from types import SimpleNamespace

    from catflow.application.provider_recovery import task_candidate

    now = datetime.now(UTC)
    job = SimpleNamespace(
        model="frozen",
        provider_submission_started_at=now,
        created_at=now,
        frozen_input={},
        input_snapshot=SimpleNamespace(
            video={
                "durationSeconds": 8,
                "resolution": "480p",
                "aspectRatio": "9:16",
                "generateAudio": False,
            }
        ),
    )
    task = {
        "id": "cloud",
        "model": "frozen",
        "created_at": int(now.timestamp()),
        "status": "running",
        "duration": 5,
        "resolution": "https://private.example",
    }
    candidate = task_candidate(job, task)
    assert not candidate.can_associate
    assert len(candidate.mismatches) == 2
    assert "https://" not in candidate.model_dump_json()
    task.update(duration=8, resolution="480p")
    assert task_candidate(job, task).can_associate
    job.provider_submission_started_at = None
    assert not task_candidate(job, task).can_associate


def test_concurrent_association_is_atomic_and_keys_bind_task_id(database):
    from concurrent.futures import ThreadPoolExecutor

    from catflow.application.service import StudioIdempotencyInputConflictError

    sessions, repository, project = database
    now = datetime.now(UTC)
    ids = [
        add_job(
            sessions,
            project,
            status="submission_unknown",
            provider_submission_started_at=now,
            frozen_input_json={"targetShotId": f"shot-{index}"},
        )
        for index in range(2)
    ]
    reader = SimulatedTaskReader(
        {
            "id": "shared-task",
            "model": "isolated-rules",
            "created_at": int(now.timestamp()),
            "status": "running",
        }
    )
    service = StudioService(repository, provider_task_reader=reader)
    command = JobRecoveryCommand(
        action="associate_provider_task",
        providerTaskId="shared-task",
        expectedRevision=0,
        idempotencyKey="concurrent-key",
        confirmAssociation=True,
        acknowledgeUnverifiedParameters=True,
    )

    def associate(identifier):
        try:
            return service.recover_job(identifier, command)
        except StudioConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(associate, ids))
    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    reader.task["id"] = "another-task"
    with pytest.raises(StudioIdempotencyInputConflictError):
        service.recover_job(
            winners[0].id, command.model_copy(update={"provider_task_id": "another-task"})
        )


def test_associated_task_reaches_success_through_existing_landing_path(database, tmp_path):
    from catflow_worker.runner import ProviderPoll

    sessions, repository, project = database
    now = datetime.now(UTC)
    identifier = add_job(
        sessions, project, status="submission_unknown", provider_submission_started_at=now
    )
    reader = SimulatedTaskReader(
        {
            "id": "finished-task",
            "model": "isolated-rules",
            "created_at": int(now.timestamp()),
            "status": "succeeded",
        }
    )
    service = StudioService(repository, provider_task_reader=reader)
    service.recover_job(
        identifier,
        JobRecoveryCommand(
            action="associate_provider_task",
            providerTaskId="finished-task",
            expectedRevision=0,
            idempotencyKey="finished-association",
            confirmAssociation=True,
            acknowledgeUnverifiedParameters=True,
        ),
    )
    landed = []

    class CompletedProvider(NoCreateProvider):
        def poll(self, task_id):
            assert task_id == "finished-task"
            return ProviderPoll(
                status="succeeded", result={"videoUrl": "https://example.invalid/test"}
            )

    class SimulatedLanding:
        def store_result(self, job_id):
            landed.append(job_id)

    worker = DurableJobWorker(
        sessions,
        CompletedProvider(),
        worker_id="finished-association",
        receipt_root=tmp_path,
        result_handler=SimulatedLanding(),
    )
    assert worker.run_once()
    assert worker.run_once()
    assert landed == [identifier]
    assert repository.get_job(identifier).status == "succeeded"


def test_frozen_only_parameters_cannot_silently_match():
    from types import SimpleNamespace

    from catflow.application.provider_recovery import task_candidate

    now = datetime.now(UTC)
    job = SimpleNamespace(
        model="frozen",
        provider_submission_started_at=now,
        created_at=now,
        input_snapshot=None,
        frozen_input={"durationSeconds": 8, "resolution": "480p", "generateAudio": True},
    )
    task = {
        "id": "cloud",
        "model": "frozen",
        "created_at": int(now.timestamp()),
        "status": "running",
        "duration": 4,
        "resolution": "720p",
        "generate_audio": False,
    }
    candidate = task_candidate(job, task)
    assert not candidate.can_associate and len(candidate.mismatches) == 3


def test_api_owned_reader_only_issues_read_requests(monkeypatch):
    import httpx

    from catflow.infrastructure.ark_provider_reader import ArkProviderTaskReader

    calls = []

    def transport(request):
        calls.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/tasks"):
            assert request.url.params["filter.model"] == "frozen"
            assert request.url.params["page_num"] == "2"
            return httpx.Response(200, json={"items": [{"id": "cloud-task"}]})
        return httpx.Response(200, json={"id": "cloud-task"})

    monkeypatch.setenv("ARK_API_KEY", "simulated-key")
    monkeypatch.setenv("ARK_BASE_URL", "https://provider.invalid/api/v3")
    reader = ArkProviderTaskReader(transport=httpx.MockTransport(transport))
    assert reader.list_tasks(model="frozen", page=2, page_size=50) == [{"id": "cloud-task"}]
    assert reader.get_task("cloud-task") == {"id": "cloud-task"}
    assert len(calls) == 2
