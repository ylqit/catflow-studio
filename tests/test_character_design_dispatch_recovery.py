from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

from cat_video_generator.domain.production_recipes import RecipeDispatchError
from cat_video_generator.domain.workflow import StepStatus
from cat_video_generator.infrastructure.db.aigc_canvas_repository import (
    SqlAlchemyAigcCanvasRepository,
    _GenerationBatchPlan,
)
from cat_video_generator.infrastructure.db.durable_queue import (
    DurableWorkflowQueue,
    PersistentTaskRecovery,
    _character_design_recovery_slots,
    _is_pre_provider_dispatch_error,
    _is_recipe_input_preparation_error,
)
from cat_video_generator.infrastructure.db.models import WorkflowStep
from cat_video_generator.infrastructure.db.repositories import WorkflowConflictError


class _RecordingSession:
    def __init__(self, *, fail_flush: int | None = None) -> None:
        self.operations: list[tuple[str, tuple[object, ...] | None]] = []
        self._flush_count = 0
        self._fail_flush = fail_flush

    def add_all(self, rows: list[object]) -> None:
        self.operations.append(("add_all", tuple(rows)))

    def add(self, row: object) -> None:
        self.operations.append(("add", (row,)))

    def flush(self) -> None:
        self._flush_count += 1
        self.operations.append(("flush", None))
        if self._fail_flush == self._flush_count:
            raise SQLAlchemyError("forced flush failure")


def _batch_plan(slot: str) -> _GenerationBatchPlan:
    project_id = uuid.uuid4()
    node = SimpleNamespace(id=uuid.uuid4(), status="draft", data_json={})
    batch = SimpleNamespace(id=uuid.uuid4(), idempotency_key=f"batch-{slot}")
    steps = (SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4()))
    prompts = (SimpleNamespace(id=uuid.uuid4()), SimpleNamespace(id=uuid.uuid4()))
    payload = SimpleNamespace(
        project_id=project_id,
        candidate_count=2,
        input={"characterDesign": {"slot": slot}},
    )
    return _GenerationBatchPlan(payload, node, batch, steps, prompts)


def test_character_batches_flush_all_child_steps_before_any_batch() -> None:
    repository = SqlAlchemyAigcCanvasRepository(SimpleNamespace())  # type: ignore[arg-type]
    events: list[tuple[uuid.UUID, str, dict[str, str]]] = []
    repository._record_event = (  # type: ignore[method-assign]
        lambda _session, project_id, event_type, data: events.append(
            (project_id, event_type, data)
        )
    )
    session = _RecordingSession()
    plans = tuple(_batch_plan(slot) for slot in ("child", "cat", "pair_scale"))

    repository._persist_generation_batch_plans(session, plans)  # type: ignore[arg-type]

    assert session.operations[:5] == [
        ("add_all", tuple(step for plan in plans for step in plan.steps)),
        ("flush", None),
        ("add_all", tuple(plan.batch for plan in plans)),
        ("flush", None),
        ("add_all", tuple(prompt for plan in plans for prompt in plan.prompts)),
    ]
    assert [event_type for _, event_type, _ in events] == [
        "generation_batch_queued",
        "generation_batch_queued",
        "generation_batch_queued",
    ]
    for plan in plans:
        assert plan.node.status == "pending"
        assert plan.node.data_json["batchId"] == str(plan.batch.id)
        assert plan.node.data_json["candidateStepIds"] == [
            str(step.id) for step in plan.steps
        ]


def test_child_step_flush_failure_never_adds_media_batches_or_events() -> None:
    repository = SqlAlchemyAigcCanvasRepository(SimpleNamespace())  # type: ignore[arg-type]
    events: list[object] = []
    repository._record_event = (  # type: ignore[method-assign]
        lambda *_values: events.append(object())
    )
    session = _RecordingSession(fail_flush=1)
    plans = tuple(_batch_plan(slot) for slot in ("child", "cat", "pair_scale"))

    with pytest.raises(SQLAlchemyError, match="forced flush failure"):
        repository._persist_generation_batch_plans(session, plans)  # type: ignore[arg-type]

    assert session.operations == [
        ("add_all", tuple(step for plan in plans for step in plan.steps)),
        ("flush", None),
    ]
    assert events == []


class _TransactionContext(AbstractContextManager[_RecordingSession]):
    def __init__(self, session: _RecordingSession) -> None:
        self.session = session
        self.rolled_back = False

    def __enter__(self) -> _RecordingSession:
        return self.session

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.rolled_back = exc is not None
        return False


class _TransactionFactory:
    def __init__(self) -> None:
        self.context = _TransactionContext(_RecordingSession())

    def begin(self) -> _TransactionContext:
        return self.context


def test_atomic_dispatch_converts_database_failure_to_pre_provider_error() -> None:
    project_id = uuid.uuid4()
    parent_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    recipe_id = uuid.uuid4()
    factory = _TransactionFactory()
    repository = SqlAlchemyAigcCanvasRepository(factory)  # type: ignore[arg-type]
    parent = SimpleNamespace(
        id=parent_id,
        production_run_id=project_id,
        operation_key="recipe:character_design",
        input_snapshot_json={"recipeInstanceId": str(recipe_id)},
    )
    revision = SimpleNamespace(
        id=revision_id,
        production_run_id=project_id,
        production_recipe_instance_id=recipe_id,
    )
    repository._required = (  # type: ignore[method-assign]
        lambda _session, model, _identifier, **_values: (
            parent if model is WorkflowStep else revision
        )
    )
    plans = {slot: _batch_plan(slot) for slot in ("child", "cat", "pair_scale")}
    repository._plan_generation_batch = (  # type: ignore[method-assign]
        lambda _session, payload: (plans[payload.input["characterDesign"]["slot"]], None)
    )
    repository._persist_generation_batch_plans = (  # type: ignore[method-assign]
        lambda _session, _plans: (_ for _ in ()).throw(SQLAlchemyError("foreign key"))
    )
    payloads = tuple(
        SimpleNamespace(
            project_id=project_id,
            idempotency_key=f"idem-{slot}",
            input={
                "characterDesign": {
                    "slot": slot,
                    "revisionId": str(revision_id),
                }
            },
        )
        for slot in ("child", "cat", "pair_scale")
    )

    with pytest.raises(RecipeDispatchError) as captured:
        repository.create_generation_batches(payloads, parent_step_id=parent_id)

    assert factory.context.rolled_back is True
    assert captured.value.to_error_document() == {
        "code": "recipe_dispatch_failed",
        "failedStep": "create_generation_batches",
        "recoverable": True,
        "providerSubmitted": False,
        "message": "角色设计图片批次调度失败；数据库事务已回滚，供应商尚未提交",
        "context": {
            "parentStepId": str(parent_id),
            "projectId": str(project_id),
            "characterDesignRevisionId": str(revision_id),
            "slots": ["child", "cat", "pair_scale"],
        },
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            {
                "code": "recipe_dispatch_failed",
                "failedStep": "create_generation_batches",
                "recoverable": True,
                "providerSubmitted": False,
            },
            True,
        ),
        (
            {
                "code": "recipe_dispatch_failed",
                "failedStep": "create_generation_batches",
                "recoverable": True,
                "providerSubmitted": True,
            },
            False,
        ),
        (
            {
                "code": "media_worker_failed",
                "message": (
                    "violates foreign key constraint "
                    "media_generation_batches_workflow_step_id_fkey on workflow_steps"
                ),
            },
            True,
        ),
        (
            {"code": "media_worker_failed", "message": "provider timed out"},
            False,
        ),
        (
            {
                "code": "recipe_input_validation_failed",
                "failedStep": "validate_recipe_input",
                "recoverable": True,
                "providerSubmitted": False,
            },
            True,
        ),
        (
            {
                "code": "media_worker_failed",
                "message": (
                    "1 validation error for PaidRecipeRunRequest\n"
                    "characterDesignStage\n  Extra inputs are not permitted"
                ),
            },
            True,
        ),
    ],
)
def test_only_proven_pre_provider_failures_are_recoverable(
    error: dict[str, Any],
    expected: bool,
) -> None:
    assert _is_pre_provider_dispatch_error(error) is expected


def test_only_exact_legacy_character_stage_validation_is_a_preparation_error() -> None:
    legacy_error = {
        "code": "media_worker_failed",
        "message": (
            "1 validation error for PaidRecipeRunRequest\n"
            "characterDesignStage\n  Extra inputs are not permitted"
        ),
    }

    assert _is_recipe_input_preparation_error(legacy_error) is True
    assert _is_recipe_input_preparation_error(
        {"code": "media_worker_failed", "message": "unrelated validation error"}
    ) is False


@pytest.mark.parametrize(
    ("stage", "context_slots", "expected"),
    [
        ("identity", [], frozenset({"child", "cat"})),
        ("pair_scale", [], frozenset({"pair_scale"})),
        ("all", ["child", "cat"], frozenset({"child", "cat"})),
        ("identity", ["pair_scale"], None),
        ("unknown", [], None),
    ],
)
def test_recovery_evidence_is_scoped_to_the_requested_character_stage(
    stage: str,
    context_slots: list[str],
    expected: frozenset[str] | None,
) -> None:
    error = {"context": {"slots": context_slots}} if context_slots else {}

    assert _character_design_recovery_slots(
        {"characterDesignStage": stage},
        error,
    ) == expected


class _BeginOnlySessions:
    def __init__(self, session: _RecordingSession) -> None:
        self.context = _TransactionContext(session)

    def begin(self) -> _TransactionContext:
        return self.context


def _failed_character_task() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        operation_key="recipe:character_design",
        status=StepStatus.FAILED.value,
        attempt=1,
        error_json={"code": "recipe_dispatch_failed"},
        lease_owner="old-worker",
        lease_expires_at=object(),
        heartbeat_at=object(),
        next_retry_at=object(),
        provider_task_id=None,
        submitted_at=None,
        completed_at=object(),
        progress_json={"percent": 35},
        input_snapshot_json={"recipeInstanceId": str(uuid.uuid4())},
        kind="director",
        scene_id=None,
        shot_card_id=None,
    )


def test_recover_requeues_the_same_parent_task_once() -> None:
    session = _RecordingSession()
    queue = DurableWorkflowQueue(_BeginOnlySessions(session))  # type: ignore[arg-type]
    row = _failed_character_task()
    queue._locked_step = lambda _session, step_id: row  # type: ignore[method-assign]
    queue._recovery_policy = (  # type: ignore[method-assign]
        lambda _session, _row, **_values: PersistentTaskRecovery(
            allowed=True,
            mode="resume_pre_provider",
            label="从失败步骤继续",
        )
    )

    queue.recover(row.id)

    assert row.status == StepStatus.QUEUED.value
    assert row.attempt == 2
    assert row.error_json is None
    assert row.provider_task_id is None
    assert row.progress_json["providerStatus"] == "not_submitted"
    assert row.progress_json["childStepIds"] == []
    added_rows = [
        item
        for operation, rows in session.operations
        if operation == "add"
        for item in rows or ()
    ]
    assert len(added_rows) == 2

    operations_after_first_recovery = list(session.operations)
    queue.recover(row.id)
    assert row.attempt == 2
    assert session.operations == operations_after_first_recovery


def test_recover_preserves_failure_when_backend_policy_denies_it() -> None:
    session = _RecordingSession()
    queue = DurableWorkflowQueue(_BeginOnlySessions(session))  # type: ignore[arg-type]
    row = _failed_character_task()
    queue._locked_step = lambda _session, step_id: row  # type: ignore[method-assign]
    queue._recovery_policy = (  # type: ignore[method-assign]
        lambda _session, _row, **_values: PersistentTaskRecovery(
            allowed=False,
            disabled_reason="检测到 Provider 提交记录，不能安全重新排队",
        )
    )

    with pytest.raises(WorkflowConflictError, match="Provider 提交记录"):
        queue.recover(row.id)

    assert row.status == StepStatus.FAILED.value
    assert row.attempt == 1
    assert session.operations == []


class _EvidenceSession:
    def __init__(
        self,
        children: list[SimpleNamespace],
        *,
        scalar_values: list[object | None] | None = None,
    ) -> None:
        self.children = children
        self.scalar_values = list(scalar_values or [])

    def scalars(self, _query: object) -> list[SimpleNamespace]:
        return self.children

    def scalar(self, _query: object) -> object | None:
        return self.scalar_values.pop(0) if self.scalar_values else None


def test_provider_child_submission_evidence_disables_safe_recovery() -> None:
    child = SimpleNamespace(
        id=uuid.uuid4(),
        provider_task_id="ark-task-123",
        submitted_at=None,
        input_snapshot_json={},
        operation_key="media:image:batch:test:candidate:1",
    )
    batch = SimpleNamespace(
        id=uuid.uuid4(),
        media_kind="image",
        workflow_step_id=child.id,
        status="pending",
        output_asset_ids_json=[],
    )
    parent = SimpleNamespace(
        id=uuid.uuid4(),
        production_run_id=uuid.uuid4(),
        progress_json={},
    )
    session = _EvidenceSession([child])

    reason = DurableWorkflowQueue._provider_evidence_reason(  # type: ignore[arg-type]
        session,
        parent,
        (batch,),
    )

    assert reason == "检测到角色图片子任务供应商提交记录，不能安全恢复"


def test_generation_attempt_evidence_disables_batch_completion() -> None:
    repository = SqlAlchemyAigcCanvasRepository(SimpleNamespace())  # type: ignore[arg-type]
    child = SimpleNamespace(
        id=uuid.uuid4(),
        provider_task_id=None,
        submitted_at=None,
    )
    batch = SimpleNamespace(
        id=uuid.uuid4(),
        media_kind="image",
        output_asset_ids_json=[],
    )
    session = _EvidenceSession([child], scalar_values=[uuid.uuid4(), None])

    with pytest.raises(WorkflowConflictError, match="Provider 提交、生成尝试或输出资产"):
        repository._require_pristine_generation_batch(session, batch)  # type: ignore[arg-type]
