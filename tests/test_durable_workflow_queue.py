from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from cat_video_generator.application.ports import VideoTaskResult
from cat_video_generator.infrastructure.db.durable_queue import (
    DurableWorkflowQueue,
    cancellation_policy_from_evidence,
    is_claimable,
    operation_matches,
    provider_status_after_observation,
)


class _CancellationSession:
    def __init__(self, scalar_results: list[object | None]) -> None:
        self.scalar_results = scalar_results
        self.events: list[object] = []

    def scalar(self, _statement: object) -> object | None:
        return self.scalar_results.pop(0)

    def add(self, row: object) -> None:
        self.events.append(row)


class _CancellationContext(AbstractContextManager[_CancellationSession]):
    def __init__(self, session: _CancellationSession) -> None:
        self.session = session

    def __enter__(self) -> _CancellationSession:
        return self.session

    def __exit__(self, *_args: object) -> bool:
        return False


class _CancellationSessions:
    def __init__(self, session: _CancellationSession) -> None:
        self.session = session

    def begin(self) -> _CancellationContext:
        return _CancellationContext(self.session)


def _task_row(*, provider_task_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        scene_id=None,
        shot_card_id=None,
        kind="video",
        status="queued",
        attempt=1,
        operation_key="media:video:batch:test:candidate:1",
        input_snapshot_json={},
        progress_json={},
        error_json=None,
        provider_task_id=provider_task_id,
        submitted_at=(
            datetime(2026, 8, 28, 1, 0, tzinfo=UTC)
            if provider_task_id
            else None
        ),
        lease_owner=None,
        lease_expires_at=None,
        heartbeat_at=None,
        next_retry_at=None,
        completed_at=None,
    )


def test_pending_work_is_claimable_when_retry_window_is_open() -> None:
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)

    assert is_claimable(
        status="pending",
        lease_expires_at=None,
        next_retry_at=None,
        now=now,
    )
    assert not is_claimable(
        status="pending",
        lease_expires_at=None,
        next_retry_at=now + timedelta(seconds=1),
        now=now,
    )


def test_expired_worker_lease_can_be_recovered_but_unknown_submission_cannot() -> None:
    now = datetime(2026, 8, 20, 1, 0, tzinfo=UTC)

    assert is_claimable(
        status="running",
        lease_expires_at=now - timedelta(seconds=1),
        next_retry_at=None,
        now=now,
    )
    assert not is_claimable(
        status="running",
        lease_expires_at=now + timedelta(seconds=30),
        next_retry_at=None,
        now=now,
    )
    assert not is_claimable(
        status="submission_unknown",
        lease_expires_at=now - timedelta(minutes=10),
        next_retry_at=None,
        now=now,
    )


def test_heartbeat_stops_cleanly_after_the_owned_task_reaches_a_terminal_status() -> None:
    row = _task_row(provider_task_id="provider-video-complete")
    row.status = "awaiting_review"
    row.lease_owner = "worker-1"
    row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=30)
    session = _CancellationSession([row])
    queue = DurableWorkflowQueue(_CancellationSessions(session))  # type: ignore[arg-type]

    renewed = queue.heartbeat(row.id, worker_id="worker-1")

    assert renewed is None
    assert row.status == "awaiting_review"
    assert row.lease_expires_at is not None


def test_specialized_worker_only_claims_owned_operation_prefixes() -> None:
    prefixes = ("media:image:batch:", "video:edit-recipe:")

    assert operation_matches("media:image:batch:123:candidate:1", prefixes)
    assert operation_matches("video:edit-recipe:456", prefixes)
    assert not operation_matches("director:story_candidate", prefixes)


def test_unsubmitted_task_can_only_be_cancelled_before_a_worker_claims_it() -> None:
    policy = cancellation_policy_from_evidence(
        status="queued",
        kind="video",
        provider_task_id=None,
        submitted_at=None,
        has_active_lease=False,
        has_generation_attempt=False,
    )

    assert policy.allowed is True
    assert policy.mode == "local_before_provider"
    assert policy.provider_status == "not_submitted"
    assert policy.cost_may_already_apply is False
    assert policy.label == "取消，尚未提交 Provider"

    claimed = cancellation_policy_from_evidence(
        status="running",
        kind="video",
        provider_task_id=None,
        submitted_at=None,
        has_active_lease=True,
        has_generation_attempt=False,
    )
    assert claimed.allowed is False
    assert claimed.mode == "unavailable"
    assert "Worker" in (claimed.disabled_reason or "")


def test_provider_queued_and_running_have_different_cancellation_semantics() -> None:
    queued = cancellation_policy_from_evidence(
        status="queued",
        kind="video",
        provider_task_id="provider-task-1",
        submitted_at=datetime(2026, 8, 28, 1, 0, tzinfo=UTC),
        has_active_lease=False,
        has_generation_attempt=True,
    )
    assert queued.allowed is True
    assert queued.mode == "provider_queued"
    assert queued.provider_status == "queued"
    assert queued.cost_may_already_apply is True
    assert queued.label == "取消 Provider 排队任务"

    running = cancellation_policy_from_evidence(
        status="running",
        kind="video",
        provider_task_id="provider-task-1",
        submitted_at=datetime(2026, 8, 28, 1, 0, tzinfo=UTC),
        has_active_lease=True,
        has_generation_attempt=True,
    )
    assert running.allowed is False
    assert running.mode == "unavailable"
    assert running.provider_status == "running"
    assert running.cost_may_already_apply is True
    assert running.label == "Provider 已运行，无法取消"
    assert "不支持取消" in (running.disabled_reason or "")

    reconciled_running = cancellation_policy_from_evidence(
        status="queued",
        kind="video",
        provider_task_id="provider-task-1",
        submitted_at=datetime(2026, 8, 28, 1, 0, tzinfo=UTC),
        has_active_lease=False,
        has_generation_attempt=True,
        provider_status_hint="running",
    )
    assert reconciled_running.allowed is False
    assert reconciled_running.provider_status == "running"


def test_provider_status_observations_cannot_regress_after_running() -> None:
    assert provider_status_after_observation("queued", "running") == "running"
    assert provider_status_after_observation("running", "queued") == "running"
    assert provider_status_after_observation("succeeded", "running") == "succeeded"


def test_unknown_submission_and_cancellation_require_reconciliation() -> None:
    for status in ("submission_unknown", "cancellation_unknown", "cancelling"):
        policy = cancellation_policy_from_evidence(
            status=status,
            kind="video",
            provider_task_id="provider-task-unknown",
            submitted_at=datetime(2026, 8, 28, 1, 0, tzinfo=UTC),
            has_active_lease=False,
            has_generation_attempt=True,
        )
        assert policy.allowed is False
        assert policy.mode == "reconcile_required"
        assert policy.provider_status == "unknown"
        assert policy.label == "先对账再处理"


def test_provider_cancellation_is_not_offered_for_non_video_tasks() -> None:
    policy = cancellation_policy_from_evidence(
        status="queued",
        kind="image",
        provider_task_id="provider-image-1",
        submitted_at=datetime(2026, 8, 28, 1, 0, tzinfo=UTC),
        has_active_lease=False,
        has_generation_attempt=True,
    )

    assert policy.allowed is False
    assert policy.mode == "unavailable"
    assert "当前 Provider 接口不支持" in (policy.disabled_reason or "")


def test_queue_atomically_cancels_unsubmitted_task_without_gateway_call() -> None:
    row = _task_row()
    session = _CancellationSession([row, None])

    class Gateway:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"local cancellation must not call gateway: {name}")

    queue = DurableWorkflowQueue(  # type: ignore[arg-type]
        _CancellationSessions(session),
        gateway=Gateway(),
    )

    queue.cancel(
        row.id,
        expected_status="queued",
        expected_provider_task_id=None,
        reason="Web 任务中心人工取消",
    )

    assert row.status == "cancelled"
    assert row.completed_at is not None
    assert row.progress_json["providerStatus"] == "not_submitted"
    assert row.progress_json["message"] == "已在提交 Provider 前取消，Provider 调用 0 次"
    assert len(session.events) == 2


def test_queue_cancels_provider_task_only_after_remote_confirms_queued() -> None:
    row = _task_row(provider_task_id="provider-video-queued")
    session = _CancellationSession([row, None, row])
    calls: list[str] = []

    class Gateway:
        def get_video_task(self, task_id: str) -> VideoTaskResult:
            calls.append(f"get:{task_id}")
            return VideoTaskResult(task_id=task_id, status="queued")

        def cancel_video_task(self, task_id: str) -> VideoTaskResult:
            calls.append(f"cancel:{task_id}")
            return VideoTaskResult(task_id=task_id, status="cancelled")

    queue = DurableWorkflowQueue(  # type: ignore[arg-type]
        _CancellationSessions(session),
        gateway=Gateway(),
    )

    queue.cancel(
        row.id,
        expected_status="queued",
        expected_provider_task_id="provider-video-queued",
    )

    assert calls == [
        "get:provider-video-queued",
        "cancel:provider-video-queued",
    ]
    assert row.status == "cancelled"
    assert row.progress_json["providerStatus"] == "cancelled"
    assert "费用是否产生" in row.progress_json["message"]


def test_queue_never_calls_provider_delete_after_remote_reports_running() -> None:
    row = _task_row(provider_task_id="provider-video-running")
    session = _CancellationSession([row, None, row])
    calls: list[str] = []

    class Gateway:
        def get_video_task(self, task_id: str) -> VideoTaskResult:
            calls.append(f"get:{task_id}")
            return VideoTaskResult(task_id=task_id, status="running")

        def cancel_video_task(self, _task_id: str) -> VideoTaskResult:
            raise AssertionError("running Provider task must not be deleted")

    queue = DurableWorkflowQueue(  # type: ignore[arg-type]
        _CancellationSessions(session),
        gateway=Gateway(),
    )

    queue.cancel(
        row.id,
        expected_status="queued",
        expected_provider_task_id="provider-video-running",
    )

    assert calls == ["get:provider-video-running"]
    assert row.status == "running"
    assert row.progress_json["providerStatus"] == "running"
    assert "无法取消" in row.progress_json["message"]


def test_queue_recovers_failed_local_tracking_without_resubmitting_provider() -> None:
    row = _task_row(provider_task_id="provider-video-running")
    submitted_at = row.submitted_at
    row.status = "failed"
    row.error_json = {
        "code": "media_worker_failed",
        "message": "workflow lease heartbeat failed",
    }
    row.progress_json = {
        "providerStatus": "running",
        "message": "Worker 执行失败",
    }
    row.completed_at = datetime.now(UTC)
    session = _CancellationSession([row])
    queue = DurableWorkflowQueue(_CancellationSessions(session))  # type: ignore[arg-type]

    queue.recover(row.id)

    assert row.status == "queued"
    assert row.provider_task_id == "provider-video-running"
    assert row.submitted_at == submitted_at
    assert row.error_json is None
    assert row.completed_at is None
    assert row.progress_json["providerStatus"] == "running"
    assert "不会创建新的 Provider 调用" in row.progress_json["message"]
