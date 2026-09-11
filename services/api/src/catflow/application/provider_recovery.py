"""Read-only provider evidence and conservative association validation."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from pydantic import Field

from catflow.domain.contract import ContractModel


class ProviderTaskCandidate(ContractModel):
    provider_task_id: str = Field(alias="providerTaskId")
    model: str | None = None
    status: str
    created_at: datetime | None = Field(alias="createdAt", default=None)
    parameters: dict[str, Any]
    mismatches: list[str]
    can_associate: bool = Field(alias="canAssociate")


class ProviderTaskLookupDto(ContractModel):
    status: Literal["query_failed", "not_found", "candidates"]
    model: str | None
    window_start: datetime = Field(alias="windowStart")
    window_end: datetime = Field(alias="windowEnd")
    candidates: list[ProviderTaskCandidate] = Field(default_factory=list)
    message: str
    queried_at: datetime = Field(alias="queriedAt")
    coverage_complete: bool = Field(alias="coverageComplete", default=False)


class ProviderTaskReader(Protocol):
    def list_tasks(self, *, model: str, page: int, page_size: int) -> list[dict]: ...
    def get_task(self, task_id: str) -> dict: ...


def submission_window(job):
    started = job.provider_submission_started_at or job.created_at
    return started - timedelta(minutes=2), started + timedelta(minutes=15)


def task_candidate(job, task: dict) -> ProviderTaskCandidate:
    start, end = submission_window(job)
    created = task.get("created_at")
    try:
        created = datetime.fromtimestamp(float(created), UTC)
    except (ValueError, TypeError, OverflowError):
        created = None
    mismatches = []
    if job.provider_submission_started_at is None:
        mismatches.append("本地提交时间缺失，无法确认提交窗口。")
    if (
        not job.model
        or task.get("model") != job.model
        or (job.frozen_input.get("model") and task.get("model") != job.frozen_input["model"])
    ):
        mismatches.append("云端模型与任务冻结模型不一致或不可确认。")
    if created is None or not start <= created <= end:
        mismatches.append("云端创建时间不在提交时间窗口内或不可确认。")
    snapshot = job.input_snapshot
    video = snapshot.video if snapshot else {}
    if hasattr(video, "model_dump"):
        video = video.model_dump(by_alias=True)
    parameters = {}
    for remote, local in (
        ("duration", "durationSeconds"),
        ("resolution", "resolution"),
        ("ratio", "aspectRatio"),
        ("generate_audio", "generateAudio"),
    ):
        expected = video.get(local)
        if expected is None:
            frozen = job.frozen_input
            expected = frozen.get(local, frozen.get(remote))
            if remote == "duration":
                expected = frozen.get("providerDurationSeconds", expected)
            elif remote == "ratio":
                expected = frozen.get("ratio", frozen.get("aspectRatio", expected))
        actual = task.get(remote)
        if isinstance(actual, (int, float, bool)):
            parameters[remote] = actual
        elif isinstance(actual, str):
            parameters[remote] = (
                actual if re.fullmatch(r"[0-9p:adaptive]{1,16}", actual) else "未知参数值"
            )
        if expected is not None and actual is not None and actual != expected:
            mismatches.append(f"{remote} 与冻结参数不一致。")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", task_id):
        task_id = ""
        mismatches.append("云端任务编号缺失。")
    status = task.get("status")
    if status not in {"queued", "running", "succeeded"}:
        mismatches.append("云端任务状态不支持关联恢复。")
    return ProviderTaskCandidate(
        providerTaskId=task_id or "",
        model=task.get("model") if task.get("model") == job.model else None,
        status=status
        if status in {"queued", "running", "succeeded", "failed", "cancelled", "expired"}
        else "unknown",
        createdAt=created,
        parameters=parameters,
        mismatches=mismatches,
        canAssociate=not mismatches,
    )
