"""Worker lifecycle: one paid submission, evidence recovery, fenced materialization."""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

from catflow.application.gateways import ProviderGatewayError
from catflow.infrastructure.job_lifecycle import (
    LostJobLease,
    apply_provider_receipt,
    materialization_lease,
    query_retry,
    record_job_event,
    schedule_recovery,
)
from catflow.infrastructure.models import JobRecord, VideoRepairRecord

from .provider_receipts import ProviderCall, ReceiptJournal, provider_call

LOGGER = logging.getLogger(__name__)


class ExecutionLifecycle:
    """Owns leasing, reconciliation and recovery for all provider protocols."""

    def __init__(
        self,
        sessions,
        provider,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        poll_backoff_seconds: float = 5,
        result_handler=None,
        receipt_root: Path | None = None,
        lane: str | None = None,
    ):
        self._sessions = sessions
        self._provider = provider
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._poll_backoff_seconds = poll_backoff_seconds
        self._result_handler = result_handler
        self._journal = ReceiptJournal(receipt_root or Path(".work/provider-receipts"))
        self._lane = lane
        self._epoch = 0
        self._registered_receipts: set[str] = set()
        self._next_receipt_scan = 0.0

    def _claim(self):
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            query = select(JobRecord).where(
                JobRecord.status.in_(
                    ("queued", "submitting", "submitted", "polling", "storing", "cancel_requested")
                ),
                JobRecord.provider.in_(("ark", "local_ffmpeg")),
                or_(JobRecord.leased_until.is_(None), JobRecord.leased_until < now),
                or_(JobRecord.next_action_at.is_(None), JobRecord.next_action_at <= now),
                or_(
                    JobRecord.execution_json["recoveryState"].astext.is_(None),
                    JobRecord.execution_json["recoveryState"].astext.not_in(
                        ("needs_attention", "unavailable")
                    ),
                ),
            )
            if self._lane == "submit":
                query = query.where(
                    JobRecord.status.in_(("queued", "submitting")), JobRecord.provider == "ark"
                )
            elif self._lane == "query":
                query = query.where(
                    JobRecord.status.in_(("submitted", "polling", "cancel_requested"))
                )
            elif self._lane == "local":
                query = query.where(
                    or_(JobRecord.status == "storing", JobRecord.provider == "local_ffmpeg")
                )
            job = session.scalar(
                query.order_by(JobRecord.updated_at, JobRecord.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            self._epoch = job.lease_epoch = (job.lease_epoch or 0) + 1
            job.locked_by = self._worker_id
            job.leased_until = now + timedelta(seconds=self._lease_seconds)
            if job.status == "queued":
                job.status = "storing" if job.provider == "local_ffmpeg" else "submitting"
                record_job_event(session, job, f"job.{job.status}")
            return job.id

    def _owned(self, session, job_id):
        job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
        if (
            job is None
            or job.locked_by != self._worker_id
            or job.lease_epoch != self._epoch
            or job.leased_until is None
            or job.leased_until <= datetime.now(UTC)
        ):
            raise LostJobLease(str(job_id))
        return job

    def _renew(self, job_id, stop):
        while not stop.wait(min(15, self._lease_seconds / 4)):
            try:
                with self._sessions.begin() as session:
                    job = self._owned(session, job_id)
                    job.leased_until = datetime.now(UTC) + timedelta(seconds=self._lease_seconds)
            except LostJobLease:
                return
            except SQLAlchemyError:
                LOGGER.exception("lease_renewal_deferred job_id=%s", job_id)

    def _register(self, receipt):
        from .runner import _record_provider_usage

        with self._sessions.begin() as session:
            job = session.scalar(
                select(JobRecord)
                .where(JobRecord.id == uuid.UUID(receipt["jobId"]))
                .with_for_update()
            )
            if job is None:
                return True
            fresh = apply_provider_receipt(session, job, receipt)
            usage = receipt["document"].get("usage")
            if fresh and usage:
                _record_provider_usage(job, usage=usage, provider_result=None)
            facts = job.execution_json or {}
            resumable = (
                facts.get("resultComplete")
                or job.provider_task_id
                or (job.provider_response_id and facts.get("store"))
            )
            if (
                job.status == "submission_unknown"
                and resumable
                and job.leased_until
                and job.leased_until > datetime.now(UTC)
            ):
                return False
            if (
                job.status == "submission_unknown"
                and not facts.get("receiptConflict")
                and (job.leased_until is None or job.leased_until <= datetime.now(UTC))
            ):
                if facts.get("resultComplete"):
                    schedule_recovery(job, "process_result")
                    record_job_event(session, job, "job.late_receipt_recovery")
                elif job.provider_task_id or (job.provider_response_id and facts.get("store")):
                    schedule_recovery(job, "query_provider")
                    record_job_event(session, job, "job.late_receipt_recovery")
            return True

    def _reconcile_receipts(self):
        """Replay durable evidence after outages, including jobs no longer claimable."""
        if self._lane not in {None, "reconcile"} or time.monotonic() < self._next_receipt_scan:
            return
        self._next_receipt_scan = time.monotonic() + 5
        identifiers = []
        for directory in self._journal.root.glob("*"):
            if not directory.is_dir():
                continue
            try:
                identifier = uuid.UUID(directory.name)
            except ValueError:
                continue
            identifiers.append(identifier)
        with self._sessions() as session:
            present = set(
                session.scalars(select(JobRecord.id).where(JobRecord.id.in_(identifiers)))
            )
        for identifier in present:
            try:
                for receipt in self._journal.read(identifier):
                    key = receipt["reference"]["id"]
                    if key not in self._registered_receipts and self._register(receipt):
                        self._registered_receipts.add(key)
            except (OSError, ValueError, KeyError):
                # A damaged receipt blocks this job only. Never infer that it was
                # not submitted, and never let one file stop all other queries.
                with self._sessions.begin() as session:
                    job = session.scalar(
                        select(JobRecord).where(JobRecord.id == identifier).with_for_update()
                    )
                    facts = dict(job.execution_json or {})
                    if not facts.get("receiptReadError"):
                        facts.update(
                            recoveryState="needs_attention",
                            receiptReadError=True,
                            queryError={
                                "code": "receipt_read_error",
                                "message": "本地回执无法校验，请检查文件；不会重新提交生成。",
                            },
                        )
                        job.execution_json = facts
                        record_job_event(session, job, "job.receipt_read_error")

    def run_once(self) -> bool:
        self._reconcile_receipts()
        if self._lane == "reconcile":
            # Historical evidence recovery must not hold up queries for accepted
            # tasks. This lane never claims work or calls a paid provider.
            return False
        job_id = self._claim()
        if job_id is None:
            return False
        stop = threading.Event()
        renewal = threading.Thread(target=self._renew, args=(job_id, stop), daemon=True)
        renewal.start()
        token = None
        try:
            for receipt in self._journal.read(job_id):
                self._register(receipt)
            with self._sessions() as session:
                job = session.get(JobRecord, job_id)
                if job is None:
                    raise LostJobLease(str(job_id))
                frozen = dict(job.frozen_input_json)
                status, kind = job.status, job.kind
            token = provider_call.set(
                ProviderCall(
                    job_id, frozen.get("executionContract", {}), self._journal, self._register
                )
            )
            if status == "cancel_requested":
                self._cancel(job_id)
            elif status == "storing":
                self._store(job_id)
            elif status in {"submitted", "polling"}:
                self._query(job_id)
            else:
                self._submit(job_id, kind, frozen)
        except LostJobLease:
            LOGGER.warning("job_lease_changed job_id=%s", job_id)
        except SQLAlchemyError:
            LOGGER.exception("job_database_error_receipts_preserved job_id=%s", job_id)
            raise
        except Exception as exc:
            LOGGER.exception("job_iteration_failed job_id=%s", job_id)
            self._submission_error(job_id, exc)
        finally:
            if token is not None:
                provider_call.reset(token)
            stop.set()
            renewal.join(timeout=2)
            with self._sessions.begin() as session:
                job = session.scalar(
                    select(JobRecord).where(JobRecord.id == job_id).with_for_update()
                )
                if job and job.locked_by == self._worker_id and job.lease_epoch == self._epoch:
                    job.locked_by = None
                    job.leased_until = None
        return True

    def _submit(self, job_id, kind, frozen):
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            facts = dict(job.execution_json or {})
            if facts.get("receiptConflict"):
                return
            if facts.get("providerStatus") in {"failed", "incomplete", "cancelled", "expired"}:
                job.status = "failed"
                job.error_json = facts.get("providerError") or {"message": "外部任务未完成。"}
                record_job_event(session, job, "job.failed")
                return
            if facts.get("resultComplete"):
                job.status = "storing"
                record_job_event(session, job, "job.storing")
                return
            if job.provider_task_id or job.provider_response_id:
                job.status = "polling"
                record_job_event(session, job, "job.polling")
                return
            if job.provider_submission_started_at:
                job.status = "submission_unknown"
                job.error_json = {
                    "code": "submission_interrupted",
                    "submissionUnknown": True,
                    "message": "提交已开始，未收到可查询编号；不会自动重新提交。",
                }
                record_job_event(session, job, "job.submission_unknown")
                return
        prepare = getattr(self._provider, "prepare_submission", None)
        if prepare:
            started = time.monotonic()
            try:
                prepare(job_id=job_id, kind=kind, frozen_input=frozen)
            except Exception as exc:
                self._fail(job_id, self._error(exc), stage="prepare")
                return
            finally:
                call = provider_call.get()
                if call is not None and kind in {"generate_video", "regenerate_video_segment"}:
                    call.diagnostics["localPreparationMs"] = round(
                        (time.monotonic() - started) * 1000, 3
                    )
                    call.receive({"result": {"preparationDiagnostics": dict(call.diagnostics)}})
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            if job.status != "submitting" or job.provider_submission_started_at:
                return
            now = datetime.now(UTC)
            job.provider_submission_started_at = now
            job.execution_json = {
                **(job.execution_json or {}),
                "stage": "submit",
                "stageStartedAt": now.isoformat(),
            }
            record_job_event(session, job, "job.submission_started")
        try:
            submission = self._provider.submit(job_id=job_id, kind=kind, frozen_input=frozen)
        except Exception as exc:
            self._submission_error(job_id, exc)
            return
        # Journal normalized results so DB recovery does not need another parse.
        provider_call.get().receive(
            {
                "taskId": submission.task_id,
                "responseId": (submission.result or {}).get("responseId")
                if kind.startswith("plan_")
                or kind in {"analyze_story_source", "diagnose_image", "diagnose_video"}
                else None,
                "providerStatus": "completed" if submission.result else "submitted",
                "complete": submission.result is not None,
                "result": submission.result or submission.metadata,
                "usage": submission.usage,
            }
        )
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            if (job.execution_json or {}).get("receiptConflict"):
                return
            if not submission.result and not submission.task_id:
                job.status = "submission_unknown"
            else:
                job.status = "storing" if submission.result else "submitted"
            job.execution_json = {
                **(job.execution_json or {}),
                "stage": "persist" if submission.result else "query",
            }
            record_job_event(session, job, f"job.{job.status}")

    def _submission_error(self, job_id, exc):
        error = self._error(exc)
        call = provider_call.get()
        if call is not None:
            call.receive(
                {
                    "responseId": error.get("responseId"),
                    "serverRequestId": error.get("requestId"),
                    "clientRequestId": error.get("clientRequestId"),
                    "providerStatus": error.get("providerStatus"),
                    "usage": error.get("providerUsage"),
                    "providerError": error
                    if error.get("providerStatus") in {"failed", "incomplete"}
                    else None,
                    "result": {"requestError": error},
                }
            )
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            facts = dict(job.execution_json or {})
            if facts.get("providerStatus") in {"failed", "incomplete", "cancelled", "expired"}:
                job.status = "failed"
                facts.update(stage="receive", recoveryState="needs_attention")
            elif facts.get("resultComplete"):
                # A parsing error is actionable and preserves the complete raw response.
                job.status = "failed"
                facts.update(stage="parse", recoveryState="needs_attention")
            elif job.provider_task_id or (job.provider_response_id and facts.get("store")):
                job.status = "polling"
                facts.update(stage="query", recoveryState="automatic")
                job.next_action_at = datetime.now(UTC) + timedelta(seconds=5)
            elif job.provider_submission_started_at and error.get("submissionUnknown", True):
                job.status = "submission_unknown"
                facts.update(recoveryState="unavailable")
            else:
                job.status = "failed"
                facts.update(recoveryState="needs_attention")
            job.execution_json = facts
            job.error_json = error
            record_job_event(session, job, f"job.{job.status}")

    def _query(self, job_id):
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            facts = job.execution_json or {}
            if facts.get("resultComplete") and not facts.get("receiptConflict"):
                schedule_recovery(job, "process_result")
                record_job_event(session, job, "job.result_recovered")
                return
        with self._sessions() as session:
            job = session.get(JobRecord, job_id)
            task_id, response_id = job.provider_task_id, job.provider_response_id
            facts = dict(job.execution_json or {})
        try:
            expiry = facts.get("responseExpiresAt")
            if expiry and datetime.fromisoformat(expiry) <= datetime.now(UTC):
                raise ProviderGatewayError(
                    code="response_expired", message="Response 已过云端保存期。", retryable=False
                )
            if response_id:
                result = self._provider.poll_response(response_id)
            elif task_id:
                result = self._provider.poll(task_id)
            else:
                raise ProviderGatewayError(
                    code="provider_state_unrecognized",
                    message="没有可查询的外部编号。",
                    retryable=False,
                )
            if result.status == "unknown":
                self._query_error(job_id, result.error or {"code": "provider_state_unrecognized"})
                return
        except Exception as exc:
            self._query_error(job_id, self._error(exc))
            return
        raw_status = result.provider_status or (
            "completed" if response_id and result.status == "succeeded" else result.status
        )
        provider_call.get().receive(
            {
                "providerStatus": raw_status,
                "confirmedQuery": True,
                "providerError": result.error,
                "result": result.result,
                "usage": result.usage,
                "complete": result.status == "succeeded",
            }
        )
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            facts = dict(job.execution_json or {})
            if facts.get("receiptConflict"):
                return
            now = datetime.now(UTC)
            facts.update(lastQueryAt=now.isoformat(), queryFailureCount=0, queryError=None)
            if result.status == "running":
                start = facts.get("queryWindowStartedAt") or now.isoformat()
                paused = (now - datetime.fromisoformat(start)).total_seconds() >= 1800
                job.status = "polling"
                facts.update(
                    stage="query",
                    queryWindowStartedAt=start,
                    recoveryState="needs_attention" if paused else "none",
                )
                job.next_action_at = (
                    None if paused else now + timedelta(seconds=self._poll_backoff_seconds)
                )
            elif result.status == "succeeded":
                job.status = "storing"
                facts.update(stage="persist", recoveryState="none")
                job.error_json = None
                job.next_action_at = None
            else:
                job.status = "cancelled" if raw_status == "cancelled" else "failed"
                facts.update(stage="query", recoveryState="needs_attention")
                job.error_json = result.error or {"code": raw_status, "message": "外部任务已结束。"}
            job.execution_json = facts
            record_job_event(session, job, f"job.{job.status}")

    def _query_error(self, job_id, error):
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            job.execution_json, job.next_action_at = query_retry(
                dict(job.execution_json or {}), error, now=datetime.now(UTC), jitter=random.random()
            )
            record_job_event(session, job, "job.query_deferred")

    def _store(self, job_id):
        if self._result_handler is None:
            self._fail(
                job_id,
                {"code": "result_handler_unavailable", "message": "缺少本地处理器。"},
                stage="persist",
            )
            return
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            successor = session.scalar(
                select(JobRecord.id).where(JobRecord.supersedes_job_id == job.id).limit(1)
            )
            if successor:
                job.execution_json = {
                    **(job.execution_json or {}),
                    "historicalResult": True,
                    "stage": "persist",
                    "recoveryState": "none",
                }
                if job.kind not in {"generate_image", "generate_video", "regenerate_video_segment"}:
                    job.status = "succeeded"
                    job.execution_json = {**job.execution_json, "stage": "complete"}
                    record_job_event(session, job, "job.historical_result")
                    return
                # Preserve late media locally before provider URLs expire. Its
                # immutable asset is history, never a new selection or adoption.
                record_job_event(session, job, "job.historical_media_received")
            result = dict(job.provider_result_json or {})
            kind = job.kind
        try:
            normalize = getattr(self._provider, "restore_result", None)
            if (
                normalize
                and result.get("rawResponse")
                and not any(
                    result.get(key) for key in ("payload", "url", "videoUrl", "landedAssetId")
                )
            ):
                restored = normalize(kind, result["rawResponse"])
                with self._sessions.begin() as session:
                    job = self._owned(session, job_id)
                    job.provider_result_json = {**result, **restored}
            token = materialization_lease.set((job_id, self._worker_id, self._epoch))
            try:
                self._result_handler.store_result(job_id)
            finally:
                materialization_lease.reset(token)
        except LostJobLease:
            raise
        except Exception as exc:
            from .runner import JobResultError

            error = exc.as_error_document() if isinstance(exc, JobResultError) else self._error(exc)
            transient = (
                isinstance(exc, (OSError, SQLAlchemyError, httpx.TransportError))
                or (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code in {408, 429, 500, 502, 503, 504}
                )
                or error.get("retryable") is True
            )
            with self._sessions.begin() as session:
                job = self._owned(session, job_id)
                facts = dict(job.execution_json or {})
                count = int(facts.get("localRetryCount", 0)) + 1
                retry = transient and count <= 3
                facts.update(
                    stage="persist",
                    localRetryCount=count,
                    recoveryState="automatic" if retry else "needs_attention",
                )
                job.status = "storing" if retry else "failed"
                job.execution_json = facts
                job.error_json = {**error, "localProcessing": True}
                job.next_action_at = (
                    datetime.now(UTC) + timedelta(seconds=5 * count) if retry else None
                )
                record_job_event(session, job, "job.local_recovery" if retry else "job.failed")
            return
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            job.status = "succeeded"
            job.error_json = None
            job.next_action_at = None
            job.execution_json = {
                **(job.execution_json or {}),
                "stage": "complete",
                "recoveryState": "none",
            }
            record_job_event(session, job, "job.succeeded")

    def _cancel(self, job_id):
        # No delete API is assumed to stop running generation. The API only permits
        # local cancellation before dispatch; legacy requests are reconciled truthfully.
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            if job.provider_submission_started_at is None:
                job.status = "cancelled"
            elif job.provider_task_id or job.provider_response_id:
                job.status = "polling"
                job.execution_json = {
                    **(job.execution_json or {}),
                    "recoveryState": "none",
                    "cancelNote": "未取得远端取消确认，继续核实原任务。",
                }
            else:
                job.status = "submission_unknown"
            record_job_event(session, job, f"job.{job.status}")

    def _fail(self, job_id, error, *, stage):
        with self._sessions.begin() as session:
            job = self._owned(session, job_id)
            job.status = "failed"
            job.error_json = error
            job.execution_json = {
                **(job.execution_json or {}),
                "stage": stage,
                "recoveryState": "needs_attention",
            }
            if job.video_repair_id:
                repair = session.get(VideoRepairRecord, job.video_repair_id)
                if repair and repair.status == "generating":
                    repair.status = "failed"
            record_job_event(session, job, "job.failed")

    @staticmethod
    def _error(exc: Exception) -> dict[str, Any]:
        if isinstance(exc, ProviderGatewayError):
            return exc.as_error_document()
        return {
            "code": type(exc).__name__,
            "message": str(exc),
            "retryable": False,
            "submissionUnknown": True,
        }
