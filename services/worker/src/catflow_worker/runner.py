from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.gateways import ProviderGatewayError
from catflow.domain.billing import RateCardItem, calculate_usage_cost
from catflow.infrastructure.models import JobEventRecord, JobRecord, VideoRepairRecord


class ProviderPoll(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["running", "succeeded", "failed"]
    result: dict[str, object] | None = None
    usage: dict[str, object] | None = None
    error: dict[str, object] | None = None


class ProviderSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    task_id: str | None = Field(alias="taskId", default=None)
    result: dict[str, object] | None = None
    usage: dict[str, object] | None = None
    metadata: dict[str, object] | None = None


class ProviderTaskGateway(Protocol):
    def prepare_submission(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> None: ...

    def submit(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> ProviderSubmission: ...

    def poll(self, provider_task_id: str) -> ProviderPoll: ...

    def cancel(self, provider_task_id: str) -> bool: ...


class JobResultHandler(Protocol):
    def store_result(self, job_id: uuid.UUID) -> None: ...


class DurableJobWorker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        provider: ProviderTaskGateway,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        poll_backoff_seconds: float = 2.0,
        result_handler: JobResultHandler | None = None,
    ) -> None:
        self._sessions = sessions
        self._provider = provider
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._poll_backoff_seconds = poll_backoff_seconds
        self._result_handler = result_handler

    def run_once(self) -> bool:
        claimed = self._claim()
        if claimed is None:
            return False
        (
            job_id,
            kind,
            status,
            provider_task_id,
            submission_started_at,
            frozen_input,
        ) = claimed

        if status == "cancel_requested":
            self._cancel(job_id, provider_task_id)
        elif status == "storing" or (kind == "render_export" and status == "submitting"):
            self._store_local_result(job_id)
        elif status == "submitting" and provider_task_id is not None:
            self._mark_submitted(job_id)
        elif status == "submitting" and submission_started_at is not None:
            self._mark_submission_unknown(
                job_id,
                ProviderGatewayError(
                    code="provider_submission_interrupted",
                    message="provider submission started before the worker restarted",
                    retryable=False,
                    submission_unknown=True,
                ),
            )
        elif status == "submitting":
            self._submit(job_id, frozen_input)
        elif status in {"submitted", "polling"}:
            self._poll(job_id, provider_task_id)
        return True

    def _claim(
        self,
    ) -> (
        tuple[
            uuid.UUID,
            str,
            str,
            str | None,
            datetime | None,
            dict[str, object],
        ]
        | None
    ):
        now = datetime.now(UTC)
        priority = case(
            (JobRecord.status.in_(("cancel_requested", "storing")), 0),
            (JobRecord.status.in_(("queued", "submitting")), 1),
            else_=2,
        )
        with self._sessions.begin() as session:
            job = session.scalar(
                select(JobRecord)
                .where(
                    JobRecord.status.in_(
                        (
                            "queued",
                            "submitting",
                            "submitted",
                            "polling",
                            "storing",
                            "cancel_requested",
                        )
                    ),
                    JobRecord.provider.in_(("ark", "local_ffmpeg")),
                    or_(JobRecord.leased_until.is_(None), JobRecord.leased_until < now),
                )
                .order_by(priority, JobRecord.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.status == "queued":
                job.status = "submitting"
                job.updated_at = now
                self._add_event(session, job, "job.submitting")
            job.locked_by = self._worker_id
            job.leased_until = now + timedelta(seconds=self._lease_seconds)
            session.flush()
            return (
                job.id,
                job.kind,
                job.status,
                job.provider_task_id,
                job.provider_submission_started_at,
                dict(job.frozen_input_json),
            )

    def _submit(self, job_id: uuid.UUID, frozen_input: dict[str, object]) -> None:
        kind = self._job_kind(job_id)
        prepare = getattr(self._provider, "prepare_submission", None)
        if callable(prepare):
            try:
                prepare(job_id=job_id, kind=kind, frozen_input=frozen_input)
            except ProviderGatewayError as exc:
                self._fail_with_document(job_id, exc.as_error_document())
                return
            except Exception as exc:
                self._fail_with_message(
                    job_id,
                    "provider_input_preparation_failed",
                    str(exc),
                    retryable=False,
                )
                return
        if not self._begin_submission(job_id):
            return
        try:
            submission = self._provider.submit(
                job_id=job_id,
                kind=kind,
                frozen_input=frozen_input,
            )
        except ProviderGatewayError as exc:
            if exc.submission_unknown:
                self._mark_submission_unknown(job_id, exc)
            else:
                self._fail_with_document(job_id, exc.as_error_document())
            return
        except Exception as exc:
            self._mark_submission_unknown(
                job_id,
                ProviderGatewayError(
                    code="provider_submission_exception",
                    message=str(exc),
                    retryable=False,
                    submission_unknown=True,
                ),
            )
            return
        if submission.result is not None:
            self._persist_immediate_result(job_id, submission)
            return
        if not submission.task_id:
            self._mark_submission_unknown(
                job_id,
                ProviderGatewayError(
                    code="missing_provider_task_id",
                    message="provider did not return a task id",
                    retryable=False,
                    submission_unknown=True,
                ),
            )
            return
        provider_task_id = submission.task_id
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            if job.provider_task_id is not None:
                self._release(job)
                return
            if job.status != "submitting":
                self._release(job)
                return
            job.provider_task_id = provider_task_id
            job.provider_result_json = submission.metadata
            job.provider_request_id = _provider_request_id(submission.metadata)
            job.status = "submitted"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.submitted")

    def _job_kind(self, job_id: uuid.UUID) -> str:
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            if job is None:
                raise ValueError("job not found")
            return job.kind

    def _persist_immediate_result(self, job_id: uuid.UUID, submission: ProviderSubmission) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None or job.status != "submitting":
                return
            job.provider_result_json = submission.result
            _record_provider_usage(
                job,
                usage=submission.usage,
                provider_result=submission.result,
            )
            job.status = "storing" if self._result_handler is not None else "succeeded"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, f"job.{job.status}")
        if self._result_handler is not None:
            self._store_local_result(job_id)

    def _begin_submission(self, job_id: uuid.UUID) -> bool:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None or job.status != "submitting" or job.provider_task_id is not None:
                return False
            if job.provider_submission_started_at is not None:
                return False
            job.provider_submission_started_at = datetime.now(UTC)
            job.updated_at = job.provider_submission_started_at
            session.flush()
            return True

    def _mark_submission_unknown(self, job_id: uuid.UUID, error: ProviderGatewayError) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "submission_unknown"
            job.error_json = error.as_error_document()
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.submission_unknown")

    def _mark_submitted(self, job_id: uuid.UUID) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            if job.status == "submitting" and job.provider_task_id is not None:
                job.status = "submitted"
                job.updated_at = datetime.now(UTC)
                self._add_event(session, job, "job.submitted")
            self._release(job)

    def _poll(self, job_id: uuid.UUID, provider_task_id: str | None) -> None:
        if provider_task_id is None:
            self._fail(job_id, "missing_provider_task_id", retryable=False)
            return
        result = self._provider.poll(provider_task_id)
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            previous_status = job.status
            now = datetime.now(UTC)
            if result.status == "running":
                job.status = "polling"
                event_type = "job.polling" if previous_status != "polling" else None
                job.locked_by = None
                job.leased_until = now + timedelta(seconds=self._poll_backoff_seconds)
            elif result.status == "succeeded":
                job.provider_result_json = {
                    **dict(job.provider_result_json or {}),
                    **dict(result.result or {}),
                }
                _record_provider_usage(
                    job,
                    usage=result.usage,
                    provider_result=job.provider_result_json,
                )
                job.status = "storing" if self._result_handler is not None else "succeeded"
                event_type = f"job.{job.status}"
                self._release(job)
            else:
                job.status = "failed"
                job.error_json = result.error or {
                    "code": "provider_failed",
                    "message": "Provider task failed",
                    "retryable": False,
                }
                event_type = "job.failed"
                self._release(job)
            job.updated_at = now
            if event_type is not None:
                self._add_event(session, job, event_type)
        if result.status == "succeeded" and self._result_handler is not None:
            self._store_local_result(job_id)

    def _store_local_result(self, job_id: uuid.UUID) -> None:
        if self._result_handler is None:
            self._fail(job_id, "result_handler_unavailable", retryable=False)
            return
        try:
            self._result_handler.store_result(job_id)
        except Exception as exc:
            self._fail_with_message(
                job_id,
                "result_storage_failed",
                str(exc),
                retryable=False,
            )
            return
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "succeeded"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.succeeded")

    def _cancel(self, job_id: uuid.UUID, provider_task_id: str | None) -> None:
        cancelled = provider_task_id is None or self._provider.cancel(provider_task_id)
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "cancelled" if cancelled else "cancel_requested"
            if cancelled and job.video_repair_id is not None:
                repair = session.get(VideoRepairRecord, job.video_repair_id)
                if repair is not None and repair.status == "generating":
                    repair.status = "cancelled"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, f"job.{job.status}")

    def _fail(self, job_id: uuid.UUID, code: str, *, retryable: bool) -> None:
        self._fail_with_message(job_id, code, code.replace("_", " "), retryable=retryable)

    def _fail_with_message(
        self, job_id: uuid.UUID, code: str, message: str, *, retryable: bool
    ) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "failed"
            job.error_json = {
                "code": code,
                "message": message,
                "retryable": retryable,
            }
            if job.video_repair_id is not None:
                repair = session.get(VideoRepairRecord, job.video_repair_id)
                if repair is not None and repair.status == "generating":
                    repair.status = "failed"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.failed")

    def _fail_with_document(self, job_id: uuid.UUID, error: dict[str, object]) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                return
            job.status = "failed"
            job.error_json = error
            if job.video_repair_id is not None:
                repair = session.get(VideoRepairRecord, job.video_repair_id)
                if repair is not None and repair.status == "generating":
                    repair.status = "failed"
            job.updated_at = datetime.now(UTC)
            self._release(job)
            self._add_event(session, job, "job.failed")

    @staticmethod
    def _release(job: JobRecord) -> None:
        job.locked_by = None
        job.leased_until = None

    @staticmethod
    def _add_event(session: Session, job: JobRecord, event_type: str) -> None:
        session.add(
            JobEventRecord(
                job_id=job.id,
                project_id=job.project_id,
                event_type=event_type,
                payload_json={"jobId": str(job.id), "status": job.status},
            )
        )


def _record_provider_usage(
    job: JobRecord,
    *,
    usage: dict[str, object] | None,
    provider_result: dict[str, object] | None,
) -> None:
    request_id = _provider_request_id(provider_result)
    if request_id is not None:
        job.provider_request_id = request_id
    if usage is None:
        return

    numeric_usage = {
        key: value
        for key, value in usage.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    job.actual_usage_json = numeric_usage

    provider_cost = None if provider_result is None else provider_result.get("actualCostMicros")
    if isinstance(provider_cost, int) and not isinstance(provider_cost, bool):
        job.actual_cost_micros = provider_cost
        job.billing_status = "provider_adjusted"
        return

    snapshot = job.pricing_snapshot_json
    rates_document = snapshot.get("rates") if isinstance(snapshot, dict) else None
    if not isinstance(rates_document, list):
        job.billing_status = "unpriced"
        return
    try:
        rates = tuple(RateCardItem.model_validate(item) for item in rates_document)
        calculated = calculate_usage_cost(numeric_usage, rates)
    except (TypeError, ValueError):
        job.billing_status = "unpriced"
        return
    job.actual_cost_micros = calculated.actual_cost_micros
    job.billing_status = calculated.status
    revision = snapshot.get("revision")
    if isinstance(revision, str):
        job.rate_card_revision = revision


def _provider_request_id(document: dict[str, object] | None) -> str | None:
    if document is None:
        return None
    for key in ("requestId", "responseId"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
