from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    POLLING = "polling"
    STORING = "storing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUBMISSION_UNKNOWN = "submission_unknown"


_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.SUBMITTING, JobStatus.CANCELLED}),
    JobStatus.SUBMITTING: frozenset(
        {
            JobStatus.SUBMITTED,
            JobStatus.FAILED,
            JobStatus.CANCEL_REQUESTED,
            JobStatus.SUBMISSION_UNKNOWN,
        }
    ),
    JobStatus.SUBMITTED: frozenset(
        {JobStatus.POLLING, JobStatus.FAILED, JobStatus.CANCEL_REQUESTED}
    ),
    JobStatus.POLLING: frozenset(
        {JobStatus.STORING, JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCEL_REQUESTED}
    ),
    JobStatus.STORING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.CANCEL_REQUESTED: frozenset({JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.SUBMISSION_UNKNOWN: frozenset(),
}


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    if target not in _TRANSITIONS[current]:
        raise ValueError(f"illegal job transition {current.value} -> {target.value}")
    return target
