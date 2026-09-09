"""Durable execution facts and transaction fencing, shared by worker and recovery API."""

from __future__ import annotations

import hashlib
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from catflow.application.job_execution import PROVIDER_TERMINAL, production_scope
from catflow.domain.jobs import JobStatus, transition_job

from .models import JobEventRecord, JobRecord


@event.listens_for(JobRecord.status, "set", retval=True)
def validate_status_transition(_job, value, previous, _initiator):
    if isinstance(previous, str):
        return transition_job(JobStatus(previous), JobStatus(value)).value
    return value


class LostJobLease(RuntimeError):
    pass


# Only materialization transactions use this context. Receipt registration and lease
# renewal deliberately use independent sessions outside it.
materialization_lease: ContextVar[tuple[UUID, str, int] | None] = ContextVar(
    "materialization_lease", default=None
)


@event.listens_for(Session, "before_flush")
def guard_paid_creation(session: Session, _flush_context: object, _instances: object) -> None:
    from catflow.application.service import StudioConflictError

    additions = [row for row in session.new if isinstance(row, JobRecord) and row.provider == "ark"]
    for job in additions:
        frozen = job.frozen_input_json
        scope = production_scope(
            job.kind, job.project_id, job.series_id, job.story_source_document_id, frozen
        )
        lock = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], signed=True)
        with session.no_autoflush:
            session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": lock})
            others = session.scalars(
                select(JobRecord).where(
                    JobRecord.kind == job.kind,
                    JobRecord.project_id == job.project_id,
                    JobRecord.series_id == job.series_id,
                    JobRecord.story_source_document_id == job.story_source_document_id,
                )
            ).all()
            same = [
                row
                for row in others
                if production_scope(
                    row.kind,
                    row.project_id,
                    row.series_id,
                    row.story_source_document_id,
                    row.frozen_input_json,
                )
                == scope
            ]
            superseded = {row.supersedes_job_id for row in same if row.supersedes_job_id}
            consent = frozen.get("replacementConsent")
            permitted_old = None
            if consent:
                old = session.scalar(
                    select(JobRecord).where(JobRecord.id == job.supersedes_job_id).with_for_update()
                )
                if (
                    old is None
                    or old not in same
                    or old.status != "submission_unknown"
                    or old.id in superseded
                    or consent.get("jobId") != str(old.id)
                    or consent.get("inputHash") != frozen.get("executionInputHash")
                    or consent.get("submissionKey") != job.idempotency_key
                    or consent.get("acknowledgeDuplicateCharge") is not True
                ):
                    raise StudioConflictError("重复生成确认已失效或已使用，请重新核实任务。")
                permitted_old = old.id
            for row in same:
                if row.status == "submission_unknown" and row.id in superseded:
                    continue
                if (
                    row.status not in {"succeeded", "failed", "cancelled"}
                    and row.id != permitted_old
                ):
                    raise StudioConflictError(
                        "此制作对象已有未终结或结果未知的任务，请先查看原任务。"
                    )


@event.listens_for(Session, "before_flush")
def fence_materialization(session: Session, _flush_context: object, _instances: object) -> None:
    if session.new or session.dirty or session.deleted:
        validate_materialization_owner(session)


@event.listens_for(Session, "do_orm_execute")
def fence_bulk_materialization(state) -> None:
    if state.is_update or state.is_delete or state.is_insert:
        validate_materialization_owner(state.session)


def validate_materialization_owner(session: Session) -> None:
    """Fence ORM flushes and bulk SQL writes with the same transaction-held lock."""
    owner = materialization_lease.get()
    if owner is None:
        return
    transaction = session.get_transaction()
    if session.info.get("materializationFence") == (transaction, owner):
        return
    job_id, worker_id, epoch = owner
    # Lock through the business commit, not through network or media processing.
    with session.no_autoflush:
        current = session.execute(
            select(
                JobRecord.locked_by, JobRecord.lease_epoch, JobRecord.leased_until, JobRecord.status
            )
            .where(JobRecord.id == job_id)
            .with_for_update()
        ).first()
    if (
        current is None
        or current.locked_by != worker_id
        or current.lease_epoch != epoch
        or current.leased_until is None
        or current.leased_until <= datetime.now(UTC)
        or current.status != "storing"
    ):
        raise LostJobLease("执行租约已变化；结果回执保留，停止写入业务内容。")
    session.info["materializationFence"] = (session.get_transaction(), owner)


def record_job_event(
    session: Session, job: JobRecord, name: str, payload: dict[str, Any] | None = None
) -> None:
    job.revision = (job.revision or 0) + 1
    job.updated_at = datetime.now(UTC)
    session.add(
        JobEventRecord(
            job_id=job.id,
            project_id=job.project_id,
            series_id=job.series_id,
            story_source_document_id=job.story_source_document_id,
            event_type=name,
            payload_json={
                "jobId": str(job.id),
                "status": job.status,
                **(payload or {}),
                "revision": job.revision,
            },
        )
    )


def apply_provider_receipt(session: Session, job: JobRecord, receipt: dict[str, Any]) -> bool:
    """Register evidence even after lease loss; never materialize business results here."""
    reference = receipt["reference"]
    existing = session.scalar(
        select(JobEventRecord)
        .where(
            JobEventRecord.job_id == job.id,
            JobEventRecord.payload_json["receiptId"].astext == reference["id"],
        )
        .limit(1)
    )
    if existing is not None:
        if existing.payload_json["receipt"]["sha256"] != reference["sha256"]:
            raise ValueError("receipt hash changed after registration")
        return False
    document = receipt["document"]
    facts = dict(job.execution_json or {})
    previous = facts.get("providerStatus")
    incoming = document.get("providerStatus")
    conflict = previous in PROVIDER_TERMINAL and incoming and incoming != previous
    if conflict:
        facts.update(
            recoveryState="needs_attention",
            receiptConflict=True,
            queryError={
                "code": "receipt_conflict",
                "message": "收到与已确认终态冲突的回执，请重新核实。",
            },
        )
    else:
        if incoming in PROVIDER_TERMINAL | {"submitted", "queued", "running", "in_progress"}:
            facts.update(providerStatus=incoming, providerObservedAt=receipt["receivedAt"])
        elif incoming:
            facts["unrecognizedProviderStatus"] = incoming
        if document.get("providerError"):
            facts["providerError"] = document["providerError"]
        for key in ("responseExpiresAt", "store"):
            if key in document:
                facts[key] = document[key]
        if document.get("complete"):
            facts.update(resultComplete=True, resultReceivedAt=receipt["receivedAt"])
        if (
            document.get("confirmedQuery")
            and incoming == previous
            and incoming in PROVIDER_TERMINAL
            and not facts.get("identifierConflict")
        ):
            facts.update(receiptConflict=False, recoveryState="none", queryError=None)
        current = dict(job.provider_result_json or {})
        if not facts.get("resultComplete") or document.get("complete"):
            for key, value in (document.get("result") or {}).items():
                if (
                    key == "requestError"
                    and isinstance(current.get(key), dict)
                    and isinstance(value, dict)
                ):
                    current[key] = {**current[key], **value}
                else:
                    current[key] = value
        job.provider_result_json = current or None
    for column, key in (
        ("provider_task_id", "taskId"),
        ("provider_response_id", "responseId"),
        ("provider_client_request_id", "clientRequestId"),
        ("provider_request_id", "serverRequestId"),
    ):
        value = document.get(key)
        if value:
            old = getattr(job, column)
            if (
                column in {"provider_task_id", "provider_response_id"}
                and old is not None
                and old != value
            ):
                facts.update(
                    recoveryState="needs_attention", receiptConflict=True, identifierConflict=True
                )
            else:
                setattr(job, column, value)
    facts["lastReceipt"] = reference
    job.execution_json = facts
    record_job_event(
        session,
        job,
        "job.receipt_conflict" if conflict else "job.received",
        {
            "receiptId": reference["id"],
            "receipt": reference,
            "providerStatus": incoming,
            "serverRequestId": document.get("serverRequestId"),
            "clientRequestId": document.get("clientRequestId"),
            "complete": document.get("complete", False),
        },
    )

    return True


def schedule_recovery(job: JobRecord, action: str, *, now: datetime | None = None) -> None:
    """The same transition is used by automatic recovery and the user command."""
    now = now or datetime.now(UTC)
    facts = dict(job.execution_json or {})
    facts.update(
        recoveryState="automatic",
        queryFailureCount=0,
        queryError=None,
        queryWindowStartedAt=now.isoformat(),
        localRetryCount=0,
        stage="query" if action == "query_provider" else "persist",
        stageStartedAt=now.isoformat(),
    )
    job.execution_json = facts
    job.status = "polling" if action == "query_provider" else "storing"
    job.error_json = None
    job.next_action_at = now
    job.updated_at = now


def query_retry(
    facts: dict[str, Any], error: dict[str, Any], *, now: datetime, jitter: float = 0.0
) -> tuple[dict[str, Any], datetime | None]:
    count = int(facts.get("queryFailureCount", 0)) + 1
    start = facts.get("queryWindowStartedAt") or now.isoformat()
    elapsed = (now - datetime.fromisoformat(start)).total_seconds()
    blocked = error.get("httpStatus") in {401, 403, 404, 410} or error.get("code") in {
        "provider_state_unrecognized",
        "response_expired",
        "receipt_conflict",
        "execution_environment_changed",
        "local_adapter_error",
    }
    stop = blocked or count >= 12 or elapsed >= 1800
    delay = max(min(60, 5 * 2 ** min(count - 1, 4)), float(error.get("retryAfterSeconds") or 0))
    return (
        {
            **facts,
            "queryFailureCount": count,
            "queryError": error,
            "lastQueryAt": now.isoformat(),
            "queryWindowStartedAt": start,
            "recoveryState": "needs_attention" if stop else "automatic",
        },
        None if stop else now + timedelta(seconds=delay + max(0, jitter)),
    )
