"""Execution facts shared by durable jobs, recovery APIs and task presentations."""

from __future__ import annotations

import inspect
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Literal

from pydantic import Field

from catflow.domain.contract import ContractModel

TEXT_JOB_KINDS = frozenset(
    {
        "plan_story",
        "plan_shots",
        "plan_video_edit",
        "plan_series",
        "plan_series_segment",
        "plan_series_episode",
        "analyze_story_source",
        "diagnose_image",
        "diagnose_video",
    }
)
VIDEO_JOB_KINDS = frozenset({"generate_video", "regenerate_video_segment"})
ACTIVE_STATUSES = frozenset(
    {
        "queued",
        "submitting",
        "submitted",
        "polling",
        "storing",
        "cancel_requested",
    }
)
PROVIDER_TERMINAL = frozenset(
    {"completed", "succeeded", "failed", "incomplete", "cancelled", "expired"}
)


def public_result(value: Any) -> Any:
    """Expose evidence without credentials or expiring provider download URLs."""
    if isinstance(value, dict):
        return {
            key: public_result(item)
            for key, item in value.items()
            if key.lower().replace("-", "_")
            not in {"authorization", "api_key", "access_token", "secret", "credentials"}
        }
    if isinstance(value, list):
        return [public_result(item) for item in value]
    if isinstance(value, str) and value.startswith(("https://", "http://", "data:image/")):
        return "[临时媒体地址由后台保管]"
    return value


def execution_contract(kind: str) -> dict[str, Any]:
    protocol = (
        "responses"
        if kind in TEXT_JOB_KINDS
        else ("video_task" if kind in VIDEO_JOB_KINDS else "image_sync")
    )
    return {
        "version": 2,
        "protocol": protocol,
        "connectTimeoutSeconds": 10,
        "queryTimeoutSeconds": 30,
        "streamIdleTimeoutSeconds": 120,
        "streamTotalTimeoutSeconds": 1800,
        "imageTimeoutSeconds": 600,
        "queryIntervalSeconds": 5,
        "queryWindowSeconds": 1800,
        "maxQueryFailures": 12,
        "maxLocalRetries": 3,
        **(
            {
                "store": True,
                "stream": True,
                "caching": {"type": "disabled"},
                "retentionSeconds": 259200,
            }
            if protocol == "responses"
            else {}
        ),
    }


class JobExecutionDto(ContractModel):
    contract_version: int = Field(alias="contractVersion", default=1)
    protocol: str | None = None
    stage: str = "prepare"
    stage_started_at: datetime | None = Field(alias="stageStartedAt", default=None)
    provider_status: str | None = Field(alias="providerStatus", default=None)
    provider_observed_at: datetime | None = Field(alias="providerObservedAt", default=None)
    provider_response_id: str | None = Field(alias="providerResponseId", default=None)
    provider_client_request_id: str | None = Field(alias="providerClientRequestId", default=None)
    provider_error: dict[str, Any] | None = Field(alias="providerError", default=None)
    response_expires_at: datetime | None = Field(alias="responseExpiresAt", default=None)
    result_received_at: datetime | None = Field(alias="resultReceivedAt", default=None)
    recovery_state: Literal["none", "automatic", "needs_attention", "unavailable"] = Field(
        alias="recoveryState", default="none"
    )
    last_query_at: datetime | None = Field(alias="lastQueryAt", default=None)
    next_action_at: datetime | None = Field(alias="nextActionAt", default=None)
    query_failure_count: int = Field(alias="queryFailureCount", default=0)
    query_error: dict[str, Any] | None = Field(alias="queryError", default=None)
    available_actions: list[str] = Field(alias="availableActions", default_factory=list)
    waiting_for_provider: bool = Field(alias="waitingForProvider", default=False)
    needs_attention: bool = Field(alias="needsAttention", default=False)
    usage_unconfirmed: bool = Field(alias="usageUnconfirmed", default=False)
    result_state: Literal["missing", "partial", "complete"] = Field(
        alias="resultState", default="missing"
    )
    historical_result: bool = Field(alias="historicalResult", default=False)


class JobRecoveryCommand(ContractModel):
    action: Literal["query_provider", "process_result", "associate_provider_task"]
    provider_task_id: str | None = Field(
        alias="providerTaskId", default=None, pattern=r"^[A-Za-z0-9_-]{1,200}$"
    )
    confirm_association: bool = Field(alias="confirmAssociation", default=False)
    acknowledge_unverified_parameters: bool = Field(
        alias="acknowledgeUnverifiedParameters", default=False
    )
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class UnknownJobReplacement(ContractModel):
    job_id: uuid.UUID = Field(alias="jobId")
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    acknowledge_duplicate_charge: Literal[True] = Field(alias="acknowledgeDuplicateCharge")


class PaidJobCommand(ContractModel):
    replacement: UnknownJobReplacement | None = Field(default=None, exclude=True)
    prepare_only: bool = Field(alias="prepareOnly", default=False, exclude=True)
    replacement_job_id: uuid.UUID | None = Field(
        alias="replacementJobId", default=None, exclude=True
    )


generation_command: ContextVar[PaidJobCommand | None] = ContextVar(
    "generation_command", default=None
)


class ReplacementGenerationCommand(ContractModel):
    input_hash: str = Field(alias="inputHash", pattern=r"^[a-f0-9]{64}$")
    acknowledge_duplicate_charge: Literal[True] = Field(alias="acknowledgeDuplicateCharge")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=96)


class GenerationPrepared(Exception):
    def __init__(self, document: dict[str, Any]):
        self.document = document


generation_references: ContextVar[dict[tuple[str, str], str] | None] = ContextVar("generation_references", default=None)


def generation_request(method):
    """Scope preparation and one-submission consent across nested generation entry points."""
    signature = inspect.signature(method)

    @wraps(method)
    def execute(*args, **kwargs):
        command = signature.bind(*args, **kwargs).arguments.get("command")
        # A series episode may enter through the older planner URL. Preserve the
        # outer request consent when that entry calls the series-specific builder.
        current = generation_command.get()
        token = generation_command.set(current if current is not None else command)
        references = generation_references.get()
        reference_token = generation_references.set(references if references is not None else {})
        try:
            return method(*args, **kwargs)
        finally:
            generation_references.reset(reference_token)
            generation_command.reset(token)

    return execute


def replacing_unknown(job_id: uuid.UUID) -> bool:
    command = generation_command.get()
    return bool(
        command
        and (
            (command.replacement and command.replacement.job_id == job_id)
            or (command.prepare_only and command.replacement_job_id == job_id)
        )
    )


def production_scope(
    kind: str, project_id: Any, series_id: Any, source_id: Any, frozen: dict[str, Any]
) -> str:
    # One creation object may have several independent image slots or shots.
    target = (
        frozen.get("targetShotId")
        or frozen.get("candidateAssetId")
        or frozen.get("videoAssetId")
        or (frozen.get("editDraftId") if kind == "regenerate_video_segment" else None)
        or frozen.get("episodeId")
        or frozen.get("seriesEpisodeId")
        or frozen.get("kind")
        or frozen.get("role")
        or "default"
    )
    return f"{project_id or series_id or source_id}:{kind}:{frozen.get('purpose', '')}:{target}"


def summarize_execution(
    *,
    kind: str,
    provider: str | None,
    status: str,
    facts: dict[str, Any] | None,
    task_id: str | None,
    response_id: str | None,
    client_request_id: str | None,
    result: dict[str, Any] | None,
    error: dict[str, Any] | None,
    usage: dict[str, Any] | None,
    submitted_at: datetime | None,
    next_action_at: datetime | None = None,
) -> JobExecutionDto:
    facts = facts or {}
    result = result or {}
    protocol = facts.get("protocol") or (
        execution_contract(kind)["protocol"] if provider == "ark" else "local"
    )
    # Only the explicit result field proves a legacy response identifier.
    response_id = response_id or (result.get("responseId") if kind in TEXT_JOB_KINDS else None)
    original = result.get("rawResponse")
    stage = facts.get("stage") or {
        "queued": "prepare",
        "submitting": "submit",
        "submitted": "query",
        "polling": "query",
        "storing": "persist",
        "succeeded": "complete",
        "submission_unknown": "submit",
        "cancelled": "complete",
    }.get(status, "persist" if result else "submit")
    state = facts.get("recoveryState", "none")
    if status == "submission_unknown" and not (task_id or response_id):
        state = "unavailable"
    elif status == "failed" and state == "none":
        state = "needs_attention"
    complete = bool(
        result.get("payload")
        or result.get("landedAssetId")
        or result.get("url")
        or result.get("videoUrl")
        or facts.get("resultComplete")
    )
    partial = (
        bool(original or result.get("rawText") or (result.get("requestError") or {}).get("body"))
        and not complete
    )
    actions = ["view_details"]
    if complete or partial or error:
        actions.append("view_result")
    expired = False
    expiry = facts.get("responseExpiresAt")
    if expiry:
        expired = datetime.fromisoformat(expiry).astimezone(UTC) <= datetime.now(UTC)
    queryable = bool(task_id or (response_id and facts.get("store") is True and not expired))
    if queryable and status not in {"succeeded", "cancelled"}:
        actions.append("query_provider")
    if (
        status == "failed"
        and complete
        and facts.get("providerStatus") not in {"failed", "incomplete"}
    ):
        actions.append("process_result")
    if status == "submission_unknown" and not queryable:
        actions.append("prepare_replacement")
        if provider == "ark" and kind in VIDEO_JOB_KINDS and not task_id:
            actions.append("lookup_provider_tasks")
    if status in {"queued", "submitting"} and submitted_at is None:
        actions.append("cancel")
    waiting = status in ACTIVE_STATUSES and state not in {"needs_attention", "unavailable"}
    return JobExecutionDto(
        contractVersion=facts.get("contractVersion", 1),
        protocol=protocol,
        stage=stage,
        stageStartedAt=facts.get("stageStartedAt"),
        providerStatus=facts.get("providerStatus"),
        providerObservedAt=facts.get("providerObservedAt"),
        providerResponseId=response_id,
        providerClientRequestId=client_request_id,
        providerError=facts.get("providerError"),
        responseExpiresAt=expiry,
        resultReceivedAt=facts.get("resultReceivedAt"),
        recoveryState=state,
        lastQueryAt=facts.get("lastQueryAt"),
        nextActionAt=next_action_at,
        queryFailureCount=facts.get("queryFailureCount", 0),
        queryError=facts.get("queryError"),
        availableActions=actions,
        waitingForProvider=waiting,
        needsAttention=state in {"needs_attention", "unavailable"} or bool(error),
        usageUnconfirmed=provider not in {None, "local_ffmpeg"}
        and submitted_at is not None
        and not usage,
        resultState="complete" if complete else "partial" if partial else "missing",
        historicalResult=bool(facts.get("historicalResult")),
    )
