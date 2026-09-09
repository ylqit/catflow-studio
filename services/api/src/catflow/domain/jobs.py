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


# Recovery transitions are guarded by execution evidence and API capabilities at
# their ownership boundary. This table is also enforced on ORM status writes.
_TRANSITIONS = {
    JobStatus.QUEUED: {
        JobStatus.SUBMITTING,
        JobStatus.STORING,
        JobStatus.CANCELLED,
        JobStatus.FAILED,
    },
    JobStatus.SUBMITTING: {
        JobStatus.SUBMITTED,
        JobStatus.POLLING,
        JobStatus.STORING,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCEL_REQUESTED,
        JobStatus.CANCELLED,
        JobStatus.SUBMISSION_UNKNOWN,
    },
    JobStatus.SUBMITTED: {
        JobStatus.POLLING,
        JobStatus.STORING,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCEL_REQUESTED,
        JobStatus.CANCELLED,
        JobStatus.SUBMISSION_UNKNOWN,
    },
    JobStatus.POLLING: {
        JobStatus.STORING,
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCEL_REQUESTED,
        JobStatus.CANCELLED,
        JobStatus.SUBMISSION_UNKNOWN,
    },
    JobStatus.STORING: {JobStatus.SUCCEEDED, JobStatus.FAILED},
    JobStatus.CANCEL_REQUESTED: {
        JobStatus.CANCELLED,
        JobStatus.FAILED,
        JobStatus.POLLING,
        JobStatus.SUBMISSION_UNKNOWN,
    },
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: {JobStatus.POLLING, JobStatus.STORING},
    JobStatus.CANCELLED: set(),
    JobStatus.SUBMISSION_UNKNOWN: {JobStatus.POLLING, JobStatus.STORING},
}


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    if target != current and target not in _TRANSITIONS[current]:
        raise ValueError(f"illegal job transition {current.value} -> {target.value}")
    return target
