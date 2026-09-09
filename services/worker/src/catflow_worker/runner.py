from __future__ import annotations

import logging
import uuid
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from catflow.domain.billing import RateCardItem, calculate_usage_cost
from catflow.infrastructure.models import JobRecord

LOGGER = logging.getLogger(__name__)


class ProviderPoll(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["running", "succeeded", "failed", "unknown"]
    provider_status: str | None = None
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


class JobResultHandler(Protocol):
    def store_result(self, job_id: uuid.UUID) -> None: ...


class JobResultError(RuntimeError):
    """A classified result-validation failure safe to persist on the owning Job."""

    def __init__(self, *, code: str, message: str, detail: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    def as_error_document(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": False,
            "detail": self.detail,
        }


# Public import retained for worker integrations. Lifecycle ownership lives in durable_execution.
from .durable_execution import ExecutionLifecycle as DurableJobWorker  # noqa: E402, F401


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
    for key in ("serverRequestId",):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
