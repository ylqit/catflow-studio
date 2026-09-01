"""V5项目、场景、视频片段和供应商步骤状态。"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar


class WorkflowTransitionError(ValueError):
    """请求的状态转换不属于当前工作流。"""


class RunStatus(StrEnum):
    ACTIVE = "active"
    FAILED = "failed"


class SceneStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"


class ShotStatus(StrEnum):
    READY = "ready"
    VIDEO_PENDING = "video_pending"
    APPROVED = "approved"


class StepKind(StrEnum):
    DIRECTOR = "director"
    IMAGE = "image"
    VIDEO = "video"


class PromptPurpose(StrEnum):
    DIRECTOR = "director"
    IMAGE = "image"
    VIDEO = "video"
    REVIEW = "review"


class StepStatus(StrEnum):
    PENDING = "pending"
    SUBMITTING = "submitting"
    SUBMISSION_UNKNOWN = "submission_unknown"
    CANCELLING = "cancelling"
    CANCELLATION_UNKNOWN = "cancellation_unknown"
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_PROMPT_PURPOSES_BY_STEP_KIND = {
    StepKind.DIRECTOR: frozenset({PromptPurpose.DIRECTOR}),
    StepKind.IMAGE: frozenset({PromptPurpose.IMAGE, PromptPurpose.REVIEW}),
    StepKind.VIDEO: frozenset({PromptPurpose.VIDEO, PromptPurpose.REVIEW}),
}


def validate_prompt_purpose(
    kind: StepKind,
    purpose: PromptPurpose,
    *,
    generation_intent: bool = False,
) -> PromptPurpose:
    allowed = _PROMPT_PURPOSES_BY_STEP_KIND[kind]
    if purpose not in allowed:
        raise ValueError(f"{kind.value}步骤不允许purpose={purpose.value}")
    if generation_intent and purpose is PromptPurpose.REVIEW:
        raise ValueError("审核Prompt不能作为收费生成意图")
    return purpose


_RUN_TRANSITIONS = {
    RunStatus.ACTIVE: {RunStatus.FAILED},
    RunStatus.FAILED: {RunStatus.ACTIVE},
}

_STEP_TRANSITIONS = {
    StepStatus.PENDING: {
        StepStatus.SUBMITTING,
        StepStatus.RUNNING,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    },
    StepStatus.SUBMITTING: {
        StepStatus.SUBMISSION_UNKNOWN,
        StepStatus.QUEUED,
        StepStatus.AWAITING_REVIEW,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
    },
    StepStatus.SUBMISSION_UNKNOWN: {
        StepStatus.QUEUED,
        StepStatus.RUNNING,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    },
    StepStatus.CANCELLING: {
        StepStatus.CANCELLATION_UNKNOWN,
        StepStatus.RUNNING,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    },
    StepStatus.CANCELLATION_UNKNOWN: {
        StepStatus.CANCELLING,
        StepStatus.RUNNING,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.CANCELLED,
    },
    StepStatus.QUEUED: {
        StepStatus.CANCELLING,
        StepStatus.RUNNING,
        StepStatus.AWAITING_REVIEW,
        StepStatus.SUCCEEDED,
        StepStatus.FAILED,
        StepStatus.EXPIRED,
        StepStatus.CANCELLED,
    },
    StepStatus.RUNNING: {
        StepStatus.CANCELLING,
        StepStatus.SUCCEEDED,
        StepStatus.AWAITING_REVIEW,
        StepStatus.FAILED,
        StepStatus.EXPIRED,
        StepStatus.CANCELLED,
    },
    StepStatus.AWAITING_REVIEW: {StepStatus.SUCCEEDED, StepStatus.FAILED},
    StepStatus.SUCCEEDED: set(),
    StepStatus.FAILED: set(),
    StepStatus.EXPIRED: set(),
    StepStatus.CANCELLED: set(),
}

StatusT = TypeVar("StatusT", RunStatus, StepStatus)


def _transition(
    current: StatusT,
    target: StatusT,
    transitions: dict[StatusT, set[StatusT]],
) -> StatusT:
    if target is current:
        return current
    if target not in transitions[current]:
        raise WorkflowTransitionError(f"非法状态转换: {current.value} -> {target.value}")
    return target


def transition_run(current: RunStatus, target: RunStatus) -> RunStatus:
    return _transition(current, target, _RUN_TRANSITIONS)


def transition_step(current: StepStatus, target: StepStatus) -> StepStatus:
    return _transition(current, target, _STEP_TRANSITIONS)
