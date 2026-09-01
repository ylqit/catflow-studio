"""Lease-based PostgreSQL worker queue for durable workflow steps."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from ...domain.production_recipes import recipe_task_source_hash
from ...domain.workflow import StepStatus
from .models import (
    Asset,
    CanvasEvent,
    CharacterDesignAsset,
    CharacterDesignRevision,
    GenerationAttempt,
    MediaGenerationBatch,
    ProductionRecipeInstance,
    WorkflowStep,
)
from .repositories import RecordNotFoundError, WorkflowConflictError

_RECOVERABLE_STATUSES = frozenset(
    {
        StepStatus.PENDING.value,
        StepStatus.SUBMITTING.value,
        StepStatus.QUEUED.value,
        StepStatus.RUNNING.value,
    }
)


@dataclass(frozen=True, slots=True)
class DurableLease:
    step_id: uuid.UUID
    project_id: uuid.UUID
    operation_key: str
    status: str
    attempt: int
    input_snapshot: dict[str, object]
    lease_owner: str
    lease_expires_at: datetime
    provider_task_id: str | None
    progress: dict[str, object]


@dataclass(frozen=True, slots=True)
class PersistentTaskRecovery:
    """Backend-derived recovery policy for one persisted workflow task."""

    allowed: bool
    mode: str | None = None
    label: str | None = None
    disabled_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "label": self.label,
            "disabledReason": self.disabled_reason,
        }


@dataclass(frozen=True, slots=True)
class PersistentTaskCancellation:
    """One honest cancellation policy derived from persisted Provider evidence."""

    allowed: bool
    mode: str
    label: str
    provider_status: str
    cost_may_already_apply: bool
    disabled_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "label": self.label,
            "disabledReason": self.disabled_reason,
            "providerStatus": self.provider_status,
            "costMayAlreadyApply": self.cost_may_already_apply,
        }


_PROVIDER_STATUS_ORDER = {
    "not_submitted": 0,
    "pending": 1,
    "queued": 1,
    "running": 2,
    "succeeded": 3,
    "failed": 3,
    "cancelled": 3,
}


def provider_status_after_observation(
    persisted_status: str | None,
    observed_status: str | None,
) -> str | None:
    """Keep Provider lifecycle evidence from moving backwards during polling races."""

    persisted = str(persisted_status or "").strip().lower()
    observed = str(observed_status or "").strip().lower()
    if not observed:
        return persisted or None
    if not persisted:
        return observed
    persisted_order = _PROVIDER_STATUS_ORDER.get(persisted)
    observed_order = _PROVIDER_STATUS_ORDER.get(observed)
    if persisted_order is None or observed_order is None:
        return observed
    if persisted_order > observed_order:
        return persisted
    if persisted_order == 3 and observed_order == 3 and persisted != observed:
        return persisted
    return observed


def cancellation_policy_from_evidence(
    *,
    status: str,
    kind: str,
    provider_task_id: str | None,
    submitted_at: datetime | None,
    has_active_lease: bool,
    has_generation_attempt: bool,
    provider_status_hint: str | None = None,
) -> PersistentTaskCancellation:
    """Describe cancellation without conflating local work with Provider work."""

    provider_submitted = bool(
        provider_task_id or submitted_at is not None or has_generation_attempt
    )
    normalized_provider_status = str(provider_status_hint or "").strip().lower()
    if status in {
        StepStatus.SUBMISSION_UNKNOWN.value,
        "cancelling",
        "cancellation_unknown",
    }:
        return PersistentTaskCancellation(
            allowed=False,
            mode="reconcile_required",
            label="先对账再处理",
            disabled_reason="Provider 提交或取消结果未知，必须先对账，不能本地取消或重试",
            provider_status="unknown",
            cost_may_already_apply=provider_submitted,
        )
    if status == StepStatus.CANCELLED.value:
        return _cancellation_unavailable(
            "任务已经取消",
            provider_status="cancelled",
            cost_may_already_apply=provider_submitted,
        )
    if status in {StepStatus.SUCCEEDED.value, StepStatus.AWAITING_REVIEW.value}:
        return _cancellation_unavailable(
            "Provider 任务已经完成，不能取消",
            provider_status="succeeded",
            cost_may_already_apply=provider_submitted,
        )
    if status in {StepStatus.FAILED.value, StepStatus.EXPIRED.value}:
        return _cancellation_unavailable(
            "任务已经终止，无需取消",
            provider_status="failed",
            cost_may_already_apply=provider_submitted,
        )
    if provider_task_id and normalized_provider_status in {
        "succeeded",
        "failed",
        "cancelled",
    }:
        return _cancellation_unavailable(
            (
                "Provider 任务已经完成，不能取消"
                if normalized_provider_status == "succeeded"
                else "Provider 任务已经失败，无需取消"
                if normalized_provider_status == "failed"
                else "Provider 排队任务已经取消"
            ),
            provider_status=normalized_provider_status,
            cost_may_already_apply=True,
        )
    if provider_task_id:
        if kind != "video":
            return _cancellation_unavailable(
                "当前 Provider 接口不支持取消已提交的非视频任务",
                provider_status="running" if status == StepStatus.RUNNING.value else "queued",
                cost_may_already_apply=True,
            )
        if normalized_provider_status == "running":
            return _cancellation_unavailable(
                "Provider 已开始生成，当前接口不支持取消；任务会继续跟踪",
                provider_status="running",
                cost_may_already_apply=True,
            )
        if status == StepStatus.QUEUED.value and not has_active_lease:
            return PersistentTaskCancellation(
                allowed=True,
                mode="provider_queued",
                label="取消 Provider 排队任务",
                provider_status="queued",
                cost_may_already_apply=True,
            )
        if status == StepStatus.RUNNING.value or has_active_lease:
            return _cancellation_unavailable(
                "Provider 已开始生成，当前接口不支持取消；任务会继续跟踪",
                provider_status="running",
                cost_may_already_apply=True,
            )
        return PersistentTaskCancellation(
            allowed=False,
            mode="reconcile_required",
            label="先对账再处理",
            disabled_reason="本地状态不能证明 Provider 仍在排队，必须先查询远端状态",
            provider_status="unknown",
            cost_may_already_apply=True,
        )
    if provider_submitted:
        return PersistentTaskCancellation(
            allowed=False,
            mode="reconcile_required",
            label="先对账再处理",
            disabled_reason="存在 Provider 提交证据但缺少任务号，无法证明尚未提交",
            provider_status="unknown",
            cost_may_already_apply=True,
        )
    if has_active_lease or status in {
        StepStatus.RUNNING.value,
        StepStatus.SUBMITTING.value,
    }:
        return _cancellation_unavailable(
            "Worker 正在准备提交，请刷新状态；不能宣称尚未提交 Provider",
            provider_status="not_submitted",
            cost_may_already_apply=False,
        )
    if status in {StepStatus.PENDING.value, StepStatus.QUEUED.value}:
        return PersistentTaskCancellation(
            allowed=True,
            mode="local_before_provider",
            label="取消，尚未提交 Provider",
            provider_status="not_submitted",
            cost_may_already_apply=False,
        )
    return _cancellation_unavailable(
        "当前任务状态不允许取消",
        provider_status="unknown",
        cost_may_already_apply=provider_submitted,
    )


def _cancellation_unavailable(
    reason: str,
    *,
    provider_status: str,
    cost_may_already_apply: bool,
) -> PersistentTaskCancellation:
    return PersistentTaskCancellation(
        allowed=False,
        mode="unavailable",
        label=(
            "Provider 已运行，无法取消"
            if provider_status == "running"
            else "当前不可取消"
        ),
        disabled_reason=reason,
        provider_status=provider_status,
        cost_may_already_apply=cost_may_already_apply,
    )


def is_claimable(
    *,
    status: str,
    lease_expires_at: datetime | None,
    next_retry_at: datetime | None,
    now: datetime,
) -> bool:
    if status not in _RECOVERABLE_STATUSES:
        return False
    if next_retry_at is not None and next_retry_at > now:
        return False
    return lease_expires_at is None or lease_expires_at <= now


def operation_matches(operation_key: str, prefixes: tuple[str, ...]) -> bool:
    return not prefixes or operation_key.startswith(prefixes)


class DurableWorkflowQueue:
    """Claims work with ``FOR UPDATE SKIP LOCKED`` and renewable leases."""

    def __init__(self, sessions: sessionmaker[Session], *, gateway: Any | None = None) -> None:
        self._sessions = sessions
        self._gateway = gateway

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
        operation_prefixes: tuple[str, ...] = (),
    ) -> DurableLease | None:
        normalized_worker = worker_id.strip()
        if not normalized_worker:
            raise ValueError("worker_id cannot be empty")
        if lease_seconds < 10 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 10 and 3600")
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=lease_seconds)
        with self._sessions.begin() as session:
            query = select(WorkflowStep).where(
                WorkflowStep.status.in_(_RECOVERABLE_STATUSES),
                or_(WorkflowStep.next_retry_at.is_(None), WorkflowStep.next_retry_at <= now),
                or_(
                    WorkflowStep.lease_expires_at.is_(None),
                    WorkflowStep.lease_expires_at <= now,
                ),
            )
            if operation_prefixes:
                query = query.where(
                    or_(
                        *(WorkflowStep.operation_key.startswith(prefix)
                          for prefix in operation_prefixes)
                    )
                )
            row = session.scalar(
                query
                .order_by(WorkflowStep.next_retry_at.nullsfirst(), WorkflowStep.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            if row.status in {StepStatus.PENDING.value, StepStatus.QUEUED.value}:
                row.status = StepStatus.RUNNING.value
            row.lease_owner = normalized_worker
            row.lease_expires_at = expires
            row.heartbeat_at = now
            record_workflow_task_event(session, row, "task_running")
            return _lease(row)

    def heartbeat(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> DurableLease | None:
        if lease_seconds < 10 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 10 and 3600")
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            if row.status not in _RECOVERABLE_STATUSES:
                return None
            self._require_owner(row, worker_id)
            row.heartbeat_at = now
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return _lease(row)

    def update_progress(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        current_step: int,
        total_steps: int,
        percent: int,
        message: str,
    ) -> DurableLease:
        if total_steps < 1:
            raise ValueError("total_steps must be positive")
        if current_step < 0 or current_step > total_steps:
            raise ValueError("current_step must be between zero and total_steps")
        if percent < 0 or percent > 100:
            raise ValueError("percent must be between zero and 100")
        normalized_message = message.strip()
        if not normalized_message:
            raise ValueError("progress message cannot be empty")
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            self._require_owner(row, worker_id)
            if row.status not in _RECOVERABLE_STATUSES:
                raise WorkflowConflictError("completed workflow steps cannot report progress")
            row.progress_json = {
                **dict(row.progress_json or {}),
                "currentStep": current_step,
                "totalSteps": total_steps,
                "percent": percent,
                "message": normalized_message,
            }
            record_workflow_task_event(session, row, "task_progress")
            return _lease(row)

    def assert_provider_submission_allowed(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
    ) -> None:
        """Re-read lifecycle state at the last local boundary before paid work."""

        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            self._require_owner(row, worker_id)
            if row.status not in _RECOVERABLE_STATUSES:
                raise WorkflowConflictError("任务已取消或进入对账状态，禁止提交 Provider")

    def cancellation_for(self, step_id: uuid.UUID) -> PersistentTaskCancellation:
        with self._sessions() as session:
            row = session.get(WorkflowStep, step_id)
            if row is None:
                raise RecordNotFoundError(f"WorkflowStep {step_id} was not found")
            return self._cancellation_policy(session, row)

    def cancellations_for(
        self,
        step_ids: tuple[uuid.UUID, ...],
    ) -> dict[uuid.UUID, PersistentTaskCancellation]:
        """Project cancellation policy for a task list with two bounded queries."""

        normalized_ids = tuple(dict.fromkeys(step_ids))
        if not normalized_ids:
            return {}
        with self._sessions() as session:
            rows = tuple(
                session.scalars(
                    select(WorkflowStep).where(WorkflowStep.id.in_(normalized_ids))
                )
            )
            attempted_step_ids = set(
                session.scalars(
                    select(GenerationAttempt.workflow_step_id).where(
                        GenerationAttempt.workflow_step_id.in_(normalized_ids)
                    )
                )
            )
            now = datetime.now(UTC)
            return {
                row.id: self._cancellation_policy_from_row(
                    row,
                    has_generation_attempt=row.id in attempted_step_ids,
                    now=now,
                )
                for row in rows
            }

    def cancel(
        self,
        step_id: uuid.UUID,
        *,
        expected_status: str,
        expected_provider_task_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Cancel locally before submission or cancel a still-queued Provider video."""

        now = datetime.now(UTC)
        provider_task_id: str | None = None
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            self._validate_cancellation_expectations(
                row,
                expected_status=expected_status,
                expected_provider_task_id=expected_provider_task_id,
            )
            policy = self._cancellation_policy(session, row)
            if not policy.allowed:
                raise WorkflowConflictError(
                    policy.disabled_reason or "该任务当前不允许取消"
                )
            if policy.mode == "local_before_provider":
                rows = self._local_cancellation_rows(session, row)
                for target in rows:
                    self._mark_cancelled_before_provider(
                        session,
                        target,
                        now=now,
                        reason=reason,
                    )
                return
            if policy.mode != "provider_queued" or row.provider_task_id is None:
                raise WorkflowConflictError("取消策略与任务证据不一致")
            if self._gateway is None:
                raise WorkflowConflictError("Provider 取消网关未配置")
            provider_task_id = row.provider_task_id
            row.status = StepStatus.CANCELLING.value
            row.next_retry_at = None
            row.progress_json = {
                **dict(row.progress_json or {}),
                "providerStatus": "queued",
                "message": "正在确认并取消 Provider 排队任务",
                "cancellationRequestedAt": now.isoformat(),
                "cancellationReason": (reason or "").strip() or None,
            }
            record_workflow_task_event(session, row, "task_provider_cancelling")
            record_canvas_projection_changed_event(session, row)

        assert provider_task_id is not None
        try:
            remote = self._gateway.get_video_task(provider_task_id)
        except Exception as exc:
            self._mark_cancellation_unknown(
                step_id,
                provider_task_id=provider_task_id,
                message=f"查询 Provider 状态失败：{exc}",
            )
            return
        remote_status = str(remote.status).strip().lower()
        if remote_status == "queued":
            try:
                cancelled = self._gateway.cancel_video_task(provider_task_id)
            except Exception as exc:
                self._resolve_failed_provider_cancel(
                    step_id,
                    provider_task_id=provider_task_id,
                    error=exc,
                )
                return
            if str(cancelled.status).strip().lower() not in {"cancelled", "deleted"}:
                self._mark_cancellation_unknown(
                    step_id,
                    provider_task_id=provider_task_id,
                    message="Provider 取消响应未返回确定的 cancelled 状态",
                )
                return
            self._mark_provider_cancelled(step_id, provider_task_id=provider_task_id)
            return
        if remote_status == "cancelled":
            self._mark_provider_cancelled(step_id, provider_task_id=provider_task_id)
            return
        if remote_status in {"running", "succeeded", "failed"}:
            self._restore_remote_tracking(
                step_id,
                provider_task_id=provider_task_id,
                provider_status=remote_status,
            )
            return
        self._mark_cancellation_unknown(
            step_id,
            provider_task_id=provider_task_id,
            message=f"Provider 返回未知任务状态：{remote_status or 'empty'}",
        )

    def _cancellation_policy(
        self,
        session: Session,
        row: WorkflowStep,
    ) -> PersistentTaskCancellation:
        has_generation_attempt = session.scalar(
            select(GenerationAttempt.id)
            .where(GenerationAttempt.workflow_step_id == row.id)
            .limit(1)
        ) is not None
        return self._cancellation_policy_from_row(
            row,
            has_generation_attempt=has_generation_attempt,
            now=datetime.now(UTC),
        )

    @staticmethod
    def _cancellation_policy_from_row(
        row: WorkflowStep,
        *,
        has_generation_attempt: bool,
        now: datetime,
    ) -> PersistentTaskCancellation:
        has_active_lease = bool(
            row.lease_owner
            and (row.lease_expires_at is None or row.lease_expires_at > now)
        )
        return cancellation_policy_from_evidence(
            status=row.status,
            kind=row.kind,
            provider_task_id=row.provider_task_id,
            submitted_at=row.submitted_at,
            has_active_lease=has_active_lease,
            has_generation_attempt=has_generation_attempt,
            provider_status_hint=dict(row.progress_json or {}).get("providerStatus"),
        )

    @staticmethod
    def _validate_cancellation_expectations(
        row: WorkflowStep,
        *,
        expected_status: str,
        expected_provider_task_id: str | None,
    ) -> None:
        if row.status != expected_status:
            raise WorkflowConflictError(
                f"任务状态已从 {expected_status} 变化为 {row.status}，请刷新后重试"
            )
        if expected_provider_task_id != row.provider_task_id:
            raise WorkflowConflictError("Provider task ID 已变化，请刷新后重试")

    def _local_cancellation_rows(
        self,
        session: Session,
        parent: WorkflowStep,
    ) -> tuple[WorkflowStep, ...]:
        child_ids = tuple(
            uuid.UUID(str(value))
            for value in dict(parent.progress_json or {}).get("childStepIds", [])
        )
        children = (
            tuple(
                session.scalars(
                    select(WorkflowStep)
                    .where(WorkflowStep.id.in_(child_ids))
                    .with_for_update()
                )
            )
            if child_ids
            else ()
        )
        cancellable: list[WorkflowStep] = [parent]
        for child in children:
            if child.status in {
                StepStatus.CANCELLED.value,
                StepStatus.FAILED.value,
                StepStatus.EXPIRED.value,
            }:
                continue
            policy = self._cancellation_policy(session, child)
            if policy.mode != "local_before_provider" or not policy.allowed:
                raise WorkflowConflictError(
                    "子任务已经被 Worker 领取或存在 Provider 证据，不能把父任务声明为提交前取消"
                )
            cancellable.append(child)
        return tuple(cancellable)

    @staticmethod
    def _mark_cancelled_before_provider(
        session: Session,
        row: WorkflowStep,
        *,
        now: datetime,
        reason: str | None,
    ) -> None:
        row.status = StepStatus.CANCELLED.value
        row.error_json = None
        row.next_retry_at = None
        row.lease_owner = None
        row.lease_expires_at = None
        row.completed_at = now
        row.progress_json = {
            **dict(row.progress_json or {}),
            "providerStatus": "not_submitted",
            "message": "已在提交 Provider 前取消，Provider 调用 0 次",
            "cancelledAt": now.isoformat(),
            "cancellationReason": (reason or "").strip() or None,
        }
        record_workflow_task_event(session, row, "task_cancelled_before_provider")
        record_canvas_projection_changed_event(session, row)

    def _resolve_failed_provider_cancel(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        error: Exception,
    ) -> None:
        try:
            remote = self._gateway.get_video_task(provider_task_id)
        except Exception:
            remote = None
        remote_status = (
            str(remote.status).strip().lower() if remote is not None else "unknown"
        )
        if remote_status in {"running", "succeeded", "failed", "cancelled"}:
            if remote_status == "cancelled":
                self._mark_provider_cancelled(
                    step_id,
                    provider_task_id=provider_task_id,
                )
            else:
                self._restore_remote_tracking(
                    step_id,
                    provider_task_id=provider_task_id,
                    provider_status=remote_status,
                )
            return
        self._mark_cancellation_unknown(
            step_id,
            provider_task_id=provider_task_id,
            message=f"Provider 取消请求结果未知：{error}",
        )

    def _mark_provider_cancelled(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
    ) -> None:
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            if row.provider_task_id != provider_task_id:
                raise WorkflowConflictError("Provider task ID 在取消期间发生变化")
            row.status = StepStatus.CANCELLED.value
            row.completed_at = now
            row.next_retry_at = None
            row.progress_json = {
                **dict(row.progress_json or {}),
                "providerStatus": "cancelled",
                "message": "Provider 排队任务已取消；费用是否产生以 Provider 账单为准",
                "cancelledAt": now.isoformat(),
            }
            record_workflow_task_event(session, row, "task_provider_cancelled")
            record_canvas_projection_changed_event(session, row)

    def _restore_remote_tracking(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        provider_status: str,
    ) -> None:
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            if row.provider_task_id != provider_task_id:
                raise WorkflowConflictError("Provider task ID 在取消期间发生变化")
            row.status = StepStatus.RUNNING.value
            row.next_retry_at = datetime.now(UTC)
            row.lease_owner = None
            row.lease_expires_at = None
            row.progress_json = {
                **dict(row.progress_json or {}),
                "providerStatus": provider_status,
                "message": (
                    "Provider 已开始生成，无法取消；任务会继续跟踪"
                    if provider_status == "running"
                    else "Provider 已到达终态，正在恢复结果跟踪"
                ),
            }
            record_workflow_task_event(session, row, "task_running")
            record_canvas_projection_changed_event(session, row)

    def _mark_cancellation_unknown(
        self,
        step_id: uuid.UUID,
        *,
        provider_task_id: str,
        message: str,
    ) -> None:
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            if row.provider_task_id != provider_task_id:
                raise WorkflowConflictError("Provider task ID 在取消期间发生变化")
            row.status = StepStatus.CANCELLATION_UNKNOWN.value
            row.error_json = {
                "code": "provider_cancellation_unknown",
                "message": message,
            }
            row.completed_at = now
            row.next_retry_at = None
            row.lease_owner = None
            row.lease_expires_at = None
            row.progress_json = {
                **dict(row.progress_json or {}),
                "providerStatus": "unknown",
                "message": "Provider 取消结果未知，必须先对账；禁止重试",
            }
            record_workflow_task_event(session, row, "task_cancellation_unknown")
            record_canvas_projection_changed_event(session, row)

    def finish(
        self,
        step_id: uuid.UUID,
        *,
        worker_id: str,
        status: StepStatus,
        error: dict[str, object] | None = None,
        next_retry_at: datetime | None = None,
        result_summary: dict[str, object] | None = None,
        progress_update: dict[str, object] | None = None,
    ) -> None:
        if status not in {
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.SUBMISSION_UNKNOWN,
            StepStatus.PENDING,
            StepStatus.AWAITING_REVIEW,
            StepStatus.QUEUED,
        }:
            raise ValueError("lease finish status is not supported")
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            self._require_owner(row, worker_id)
            row.status = status.value
            row.error_json = error
            row.next_retry_at = next_retry_at
            if progress_update is not None:
                progress_values = dict(progress_update)
                if "providerStatus" in progress_values:
                    progress_values["providerStatus"] = provider_status_after_observation(
                        dict(row.progress_json or {}).get("providerStatus"),
                        str(progress_values["providerStatus"]),
                    )
                row.progress_json = {
                    **dict(row.progress_json or {}),
                    **progress_values,
                }
            if result_summary is not None:
                row.progress_json = {
                    **dict(row.progress_json or {}),
                    "resultSummary": result_summary,
                }
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = now
            if status in {
                StepStatus.SUCCEEDED,
                StepStatus.FAILED,
                StepStatus.SUBMISSION_UNKNOWN,
            }:
                row.completed_at = now
            else:
                row.completed_at = None
            record_workflow_task_event(session, row, _event_type_for_status(status))
            if status in {StepStatus.FAILED, StepStatus.SUBMISSION_UNKNOWN}:
                self._stop_unsubmitted_validation_siblings(session, row, now=now)
            if status in {
                StepStatus.SUCCEEDED,
                StepStatus.FAILED,
                StepStatus.SUBMISSION_UNKNOWN,
                StepStatus.AWAITING_REVIEW,
            }:
                record_canvas_projection_changed_event(session, row)

    @staticmethod
    def _stop_unsubmitted_validation_siblings(
        session: Session,
        row: WorkflowStep,
        *,
        now: datetime,
    ) -> None:
        """Stop later paid calls after the first validation-only child failure."""

        snapshot = dict(row.input_snapshot_json or {})
        task_input = snapshot.get("input")
        character_design = (
            task_input.get("characterDesign")
            if isinstance(task_input, dict)
            else None
        )
        if not (
            row.operation_key.startswith("media:image:batch:")
            and isinstance(character_design, dict)
            and character_design.get("validationOnly") is True
        ):
            return
        parent_id_value = snapshot.get("parentStepId")
        if not parent_id_value:
            return
        parent = session.get(WorkflowStep, uuid.UUID(str(parent_id_value)))
        if parent is None or parent.operation_key != "recipe:character_design_validation":
            return
        sibling_ids = [
            uuid.UUID(str(value))
            for value in dict(parent.progress_json or {}).get("childStepIds", [])
            if str(value) != str(row.id)
        ]
        if not sibling_ids:
            return
        siblings = list(
            session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.id.in_(sibling_ids))
                .with_for_update()
            )
        )
        for sibling in siblings:
            if (
                sibling.status not in {StepStatus.PENDING.value, StepStatus.QUEUED.value}
                or sibling.provider_task_id is not None
                or sibling.submitted_at is not None
            ):
                continue
            sibling.status = StepStatus.CANCELLED.value
            sibling.error_json = {
                "code": "validation_batch_stopped",
                "message": "前序验证候选失败或提交状态未知；后续首次 Ark 调用已停止",
            }
            sibling.progress_json = {
                **dict(sibling.progress_json or {}),
                "message": "未提交 Provider：前序验证失败后已按费用边界停止",
                "providerStatus": "not_submitted",
            }
            sibling.completed_at = now
            sibling.next_retry_at = None
            record_workflow_task_event(session, sibling, "task_cancelled")
            record_canvas_projection_changed_event(session, sibling)

    def recovery_for(self, step_id: uuid.UUID) -> PersistentTaskRecovery:
        """Return the recovery policy after checking all persisted provider evidence."""

        with self._sessions() as session:
            row = session.get(WorkflowStep, step_id)
            if row is None:
                raise RecordNotFoundError(f"WorkflowStep {step_id} was not found")
            return self._recovery_policy(session, row, lock=False)

    def recover(self, step_id: uuid.UUID) -> None:
        """Resume the same durable intent without creating a second Provider submission."""

        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            row = self._locked_step(session, step_id)
            if row.status in {
                StepStatus.PENDING.value,
                StepStatus.SUBMITTING.value,
                StepStatus.QUEUED.value,
                StepStatus.RUNNING.value,
            }:
                return
            if row.status in {
                StepStatus.AWAITING_REVIEW.value,
                StepStatus.SUCCEEDED.value,
            }:
                return
            policy = self._recovery_policy(session, row, lock=True)
            if not policy.allowed:
                raise WorkflowConflictError(
                    policy.disabled_reason or "该持久任务不满足安全恢复条件"
                )
            if policy.mode == "resume_provider_tracking":
                provider_status = str(
                    dict(row.progress_json or {}).get("providerStatus") or "unknown"
                )
                row.status = StepStatus.QUEUED.value
                row.attempt += 1
                row.error_json = None
                row.lease_owner = None
                row.lease_expires_at = None
                row.heartbeat_at = None
                row.next_retry_at = None
                row.completed_at = None
                row.progress_json = {
                    **dict(row.progress_json or {}),
                    "currentStep": 2,
                    "totalSteps": 3,
                    "percent": 60,
                    "message": "继续跟踪原 Provider 任务，不会创建新的 Provider 调用",
                    "providerStatus": provider_status,
                    "recoveredAt": now.isoformat(),
                }
                record_workflow_task_event(session, row, "task_provider_tracking_resumed")
                record_canvas_projection_changed_event(session, row)
                return
            row.status = StepStatus.QUEUED.value
            row.attempt += 1
            row.error_json = None
            row.lease_owner = None
            row.lease_expires_at = None
            row.heartbeat_at = None
            row.next_retry_at = None
            row.provider_task_id = None
            row.submitted_at = None
            row.completed_at = None
            row.progress_json = {
                "currentStep": 0,
                "totalSteps": 3,
                "percent": 0,
                "message": "准备从角色设计批次调度失败步骤继续",
                "providerStatus": "not_submitted",
                "childStepIds": [],
                "recoveredAt": now.isoformat(),
            }
            record_workflow_task_event(session, row, "task_queued")
            record_canvas_projection_changed_event(session, row)

    def _recovery_policy(
        self,
        session: Session,
        row: WorkflowStep,
        *,
        lock: bool,
    ) -> PersistentTaskRecovery:
        if row.kind == "video" and row.provider_task_id:
            if row.status != StepStatus.FAILED.value:
                return _recovery_denied("视频 Provider 跟踪任务当前不是可恢复的失败状态")
            error = dict(row.error_json or {})
            provider_status = str(
                dict(row.progress_json or {}).get("providerStatus") or ""
            ).strip().lower()
            if (
                row.provider_task_id
                and row.submitted_at is not None
                and error.get("code") == "media_worker_failed"
                and error.get("message") == "workflow lease heartbeat failed"
                and provider_status in {"queued", "running"}
            ):
                return PersistentTaskRecovery(
                    allowed=True,
                    mode="resume_provider_tracking",
                    label="继续跟踪原 Provider 任务",
                )
            return _recovery_denied(
                "无法证明失败只发生在本地跟踪阶段；不能创建第二个 Provider 任务"
            )
        if row.operation_key != "recipe:character_design":
            return _recovery_denied("只有角色设计的供应商提交前调度失败可以恢复")
        if row.status == StepStatus.SUBMISSION_UNKNOWN.value:
            return _recovery_denied("供应商提交状态未知，只能对账恢复，不能重新排队")
        if row.status != StepStatus.FAILED.value:
            return _recovery_denied("任务当前不是可恢复的失败状态")

        error = dict(row.error_json or {})
        if not _is_pre_provider_dispatch_error(error):
            return _recovery_denied("该失败不属于已确认的供应商提交前批次调度错误")
        if row.provider_task_id is not None or row.submitted_at is not None:
            return _recovery_denied("检测到父任务供应商提交记录，不能安全重新排队")

        snapshot = dict(row.input_snapshot_json or {})
        recipe_id_value = snapshot.get("recipeInstanceId")
        payload = snapshot.get("payload")
        expected_revision = snapshot.get("expectedInstanceRevision")
        phase = str(snapshot.get("phase") or "")
        expected_hash = snapshot.get("sourceContentHash")
        if (
            not recipe_id_value
            or not isinstance(payload, dict)
            or not isinstance(expected_revision, int)
            or phase != "character_design"
            or not isinstance(expected_hash, str)
        ):
            return _recovery_denied("失败任务缺少可验证的角色设计输入快照")
        try:
            recipe_id = uuid.UUID(str(recipe_id_value))
        except ValueError:
            return _recovery_denied("失败任务中的配方标识无效")

        instance_query = select(ProductionRecipeInstance).where(
            ProductionRecipeInstance.id == recipe_id
        )
        if lock:
            instance_query = instance_query.with_for_update()
        instance = session.scalar(instance_query)
        if instance is None:
            return _recovery_denied("原配方实例已经不存在")
        if (
            instance.production_run_id != row.production_run_id
            or instance.revision != expected_revision
            or instance.lifecycle_status != "active"
        ):
            return _recovery_denied("配方阶段或版本已变化，不能复用原输入恢复")
        snapshot_canon = snapshot.get("canonProfileId")
        if snapshot_canon and snapshot_canon != instance.canon_profile_id:
            return _recovery_denied("Canon版本已变化，不能复用原角色设计输入")
        current_hash = recipe_task_source_hash(
            payload=payload,
            instance_id=instance.id,
            expected_revision=instance.revision,
            phase=phase,
        )
        if current_hash != expected_hash:
            return _recovery_denied("角色设计输入哈希已变化，不能安全恢复")

        requested_slots = _character_design_recovery_slots(payload, error)
        if requested_slots is None:
            return _recovery_denied("角色设计恢复阶段无效，不能确定需要检查的供应商证据")

        revision = self._character_design_revision_for_recovery(
            session,
            row,
            error,
            lock=lock,
        )
        if revision is None:
            if not _is_recipe_input_preparation_error(error):
                return _recovery_denied("找不到原角色设计版本，不能创建新的版本替代恢复")
            evidence_reason = self._provider_evidence_reason(session, row, ())
            if evidence_reason is not None:
                return _recovery_denied(evidence_reason)
            return PersistentTaskRecovery(
                allowed=True,
                mode="resume_pre_provider",
                label="从失败步骤继续",
            )
        if (
            revision.production_recipe_instance_id != instance.id
            or revision.production_run_id != row.production_run_id
            or revision.status != "generating"
        ):
            return _recovery_denied("原角色设计版本状态已变化，不能继续调度")
        if session.scalar(
            select(CharacterDesignAsset.id)
            .where(
                CharacterDesignAsset.character_design_revision_id == revision.id,
                CharacterDesignAsset.slot.in_(requested_slots),
            )
            .limit(1)
        ) is not None:
            return _recovery_denied("当前角色设计阶段已经产生图片资产，不能重新排队")

        batches = self._character_design_batches(
            session,
            row.production_run_id,
            revision.id,
            requested_slots=requested_slots,
        )
        evidence_reason = self._provider_evidence_reason(session, row, batches)
        if evidence_reason is not None:
            return _recovery_denied(evidence_reason)
        return PersistentTaskRecovery(
            allowed=True,
            mode="resume_pre_provider",
            label="从失败步骤继续",
        )

    @staticmethod
    def _character_design_revision_for_recovery(
        session: Session,
        row: WorkflowStep,
        error: dict[str, Any],
        *,
        lock: bool,
    ) -> CharacterDesignRevision | None:
        context = error.get("context")
        revision_id_value = (
            context.get("characterDesignRevisionId")
            if isinstance(context, dict)
            else None
        )
        if revision_id_value:
            try:
                revision_id = uuid.UUID(str(revision_id_value))
            except ValueError:
                return None
            query = select(CharacterDesignRevision).where(
                CharacterDesignRevision.id == revision_id
            )
            if lock:
                query = query.with_for_update()
            return session.scalar(query)
        # Compatibility for the r3 failure recorded before structured dispatch errors existed.
        query = (
            select(CharacterDesignRevision)
            .where(
                CharacterDesignRevision.production_run_id == row.production_run_id,
                CharacterDesignRevision.status == "generating",
            )
            .order_by(CharacterDesignRevision.revision.desc())
            .limit(1)
        )
        if lock:
            query = query.with_for_update()
        return session.scalar(query)

    @staticmethod
    def _character_design_batches(
        session: Session,
        project_id: uuid.UUID,
        revision_id: uuid.UUID,
        *,
        requested_slots: frozenset[str],
    ) -> tuple[MediaGenerationBatch, ...]:
        rows = session.scalars(
            select(MediaGenerationBatch).where(
                MediaGenerationBatch.production_run_id == project_id
            )
        )
        matches = []
        for batch in rows:
            context = dict(batch.input_json or {}).get("characterDesign")
            if (
                isinstance(context, dict)
                and str(context.get("revisionId")) == str(revision_id)
                and str(context.get("slot") or "") in requested_slots
            ):
                matches.append(batch)
        return tuple(matches)

    @staticmethod
    def _provider_evidence_reason(
        session: Session,
        parent: WorkflowStep,
        batches: tuple[MediaGenerationBatch, ...],
    ) -> str | None:
        child_ids = {
            batch.workflow_step_id for batch in batches if batch.workflow_step_id is not None
        }
        prefixes = tuple(
            f"media:{batch.media_kind}:batch:{batch.id}:candidate:" for batch in batches
        )
        declared_child_ids: set[uuid.UUID] = set()
        for value in dict(parent.progress_json or {}).get("childStepIds", []):
            try:
                declared_child_ids.add(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue
        candidates = list(
            session.scalars(
                select(WorkflowStep).where(
                    WorkflowStep.production_run_id == parent.production_run_id
                )
            )
        )
        children = [
            child
            for child in candidates
            if child.id in child_ids
            or child.id in declared_child_ids
            or dict(child.input_snapshot_json or {}).get("parentStepId") == str(parent.id)
            or any(child.operation_key.startswith(prefix) for prefix in prefixes)
        ]
        child_ids.update(child.id for child in children)
        if any(child.provider_task_id or child.submitted_at for child in children):
            return "检测到角色图片子任务供应商提交记录，不能安全恢复"
        if any(
            batch.status not in {"pending", "queued", "failed"}
            for batch in batches
        ):
            return "角色图片批次已经进入供应商处理阶段，不能安全恢复"
        if any(batch.output_asset_ids_json for batch in batches):
            return "角色图片批次已经产生输出资产，不能安全恢复"
        batch_ids = tuple(batch.id for batch in batches)
        attempt_conditions = []
        if child_ids:
            attempt_conditions.append(GenerationAttempt.workflow_step_id.in_(child_ids))
        if batch_ids:
            attempt_conditions.append(GenerationAttempt.business_object_id.in_(batch_ids))
        if attempt_conditions and session.scalar(
            select(GenerationAttempt.id).where(or_(*attempt_conditions)).limit(1)
        ) is not None:
            return "检测到角色图片生成尝试，不能安全恢复"
        if child_ids and session.scalar(
            select(Asset.id).where(Asset.producing_step_id.in_(child_ids)).limit(1)
        ) is not None:
            return "检测到角色图片输出资产，不能安全恢复"
        if session.scalar(
            select(GenerationAttempt.id)
            .where(
                GenerationAttempt.workflow_step_id == parent.id,
            )
            .limit(1)
        ) is not None:
            return "检测到父任务生成尝试，不能安全恢复"
        return None

    @staticmethod
    def _locked_step(session: Session, step_id: uuid.UUID) -> WorkflowStep:
        row = session.scalar(
            select(WorkflowStep).where(WorkflowStep.id == step_id).with_for_update()
        )
        if row is None:
            raise RecordNotFoundError(f"WorkflowStep {step_id} was not found")
        return row

    @staticmethod
    def _require_owner(row: WorkflowStep, worker_id: str) -> None:
        if row.lease_owner != worker_id:
            raise WorkflowConflictError("workflow lease is owned by another worker")


def _recovery_denied(reason: str) -> PersistentTaskRecovery:
    return PersistentTaskRecovery(
        allowed=False,
        disabled_reason=reason,
    )


def _is_pre_provider_dispatch_error(error: dict[str, Any]) -> bool:
    """Recognize only structured dispatch failures and the exact pre-fix r3 FK failure."""

    if error.get("code") == "recipe_dispatch_failed":
        return (
            error.get("failedStep") == "create_generation_batches"
            and error.get("recoverable") is True
            and error.get("providerSubmitted") is False
        )
    if error.get("code") == "recipe_input_validation_failed":
        return (
            error.get("failedStep") == "validate_recipe_input"
            and error.get("recoverable") is True
            and error.get("providerSubmitted") is False
        )
    if error.get("code") != "media_worker_failed":
        return False
    message = str(error.get("message") or "")
    return (
        "media_generation_batches_workflow_step_id_fkey" in message
        and "workflow_steps" in message
    ) or (
        "PaidRecipeRunRequest" in message
        and "characterDesignStage" in message
        and "Extra inputs are not permitted" in message
    )


def _is_recipe_input_preparation_error(error: dict[str, Any]) -> bool:
    if error.get("code") == "recipe_input_validation_failed":
        return error.get("failedStep") == "validate_recipe_input"
    if error.get("code") != "media_worker_failed":
        return False
    message = str(error.get("message") or "")
    return (
        "PaidRecipeRunRequest" in message
        and "characterDesignStage" in message
        and "Extra inputs are not permitted" in message
    )


def _character_design_recovery_slots(
    payload: dict[str, Any],
    error: dict[str, Any],
) -> frozenset[str] | None:
    stage = str(payload.get("characterDesignStage") or "all")
    stage_slots = {
        "all": frozenset({"child", "cat", "pair_scale"}),
        "identity": frozenset({"child", "cat"}),
        "pair_scale": frozenset({"pair_scale"}),
    }.get(stage)
    if stage_slots is None:
        return None
    context = error.get("context")
    context_slots = (
        frozenset(str(value) for value in context.get("slots", []))
        if isinstance(context, dict) and isinstance(context.get("slots"), list)
        else frozenset()
    )
    if not context_slots:
        return stage_slots
    if not context_slots.issubset(stage_slots):
        return None
    return context_slots


def _lease(row: WorkflowStep) -> DurableLease:
    if row.lease_owner is None or row.lease_expires_at is None:
        raise RuntimeError("claimed workflow row has no lease metadata")
    return DurableLease(
        step_id=row.id,
        project_id=row.production_run_id,
        operation_key=row.operation_key,
        status=row.status,
        attempt=row.attempt,
        input_snapshot=row.input_snapshot_json,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        provider_task_id=row.provider_task_id,
        progress=dict(row.progress_json or {}),
    )


def _event_type_for_status(status: StepStatus) -> str:
    return {
        StepStatus.SUCCEEDED: "task_succeeded",
        StepStatus.FAILED: "task_failed",
        StepStatus.SUBMISSION_UNKNOWN: "task_submission_unknown",
        StepStatus.AWAITING_REVIEW: "task_awaiting_review",
        StepStatus.PENDING: "task_progress",
        StepStatus.QUEUED: "task_progress",
    }[status]


def record_workflow_task_event(
    session: Session,
    row: WorkflowStep,
    event_type: str,
) -> None:
    snapshot = dict(row.input_snapshot_json or {})
    progress = dict(row.progress_json or {})
    session.add(
        CanvasEvent(
            production_run_id=row.production_run_id,
            event_type=event_type,
            data_json={
                "stepId": str(row.id),
                "projectId": str(row.production_run_id),
                "status": row.status,
                "operationKey": row.operation_key,
                "kind": row.kind,
                "canvasNodeId": snapshot.get("canvasNodeId"),
                "canvasGroupId": snapshot.get("canvasGroupId"),
                "recipeInstanceId": snapshot.get("recipeInstanceId"),
                "businessObjectId": snapshot.get("businessObjectId"),
                "parentStepId": snapshot.get("parentStepId"),
                "childStepIds": progress.get("childStepIds", []),
                "sceneId": None if row.scene_id is None else str(row.scene_id),
                "shotId": None if row.shot_card_id is None else str(row.shot_card_id),
                "phase": snapshot.get("phase") or snapshot.get("workflowStage"),
                "creationMode": snapshot.get("creationMode"),
                "progress": progress,
                "resultSummary": progress.get("resultSummary"),
                "providerStatus": progress.get("providerStatus"),
                "providerTaskId": row.provider_task_id,
                "error": row.error_json,
                "completedAt": row.completed_at.isoformat() if row.completed_at else None,
            },
        )
    )


def record_canvas_projection_changed_event(
    session: Session,
    row: WorkflowStep,
) -> None:
    """Persist the shared task-to-canvas invalidation event inside the caller transaction."""

    snapshot = dict(row.input_snapshot_json or {})
    session.add(
        CanvasEvent(
            production_run_id=row.production_run_id,
            event_type="canvas_projection_changed",
            data_json={
                "stepId": str(row.id),
                "canvasNodeId": snapshot.get("canvasNodeId"),
                "canvasGroupId": snapshot.get("canvasGroupId"),
                "recipeInstanceId": snapshot.get("recipeInstanceId"),
            },
        )
    )
