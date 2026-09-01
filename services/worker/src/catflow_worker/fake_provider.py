from __future__ import annotations

import uuid

from .runner import ProviderPoll, ProviderSubmission


class FakeProviderGateway:
    """A zero-cost deterministic gateway used by local development and CI."""

    def submit(
        self, *, job_id: uuid.UUID, kind: str, frozen_input: dict[str, object]
    ) -> ProviderSubmission:
        return ProviderSubmission(taskId=f"fake-{job_id}")

    def poll(self, provider_task_id: str) -> ProviderPoll:
        if not provider_task_id.startswith("fake-"):
            return ProviderPoll(
                status="failed",
                error={
                    "code": "unknown_fake_task",
                    "message": "Fake provider task ID is invalid",
                    "retryable": False,
                },
            )
        return ProviderPoll(status="succeeded")

    def cancel(self, provider_task_id: str) -> bool:
        return provider_task_id.startswith("fake-")
