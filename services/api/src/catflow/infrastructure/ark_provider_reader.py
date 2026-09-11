"""API-owned Ark reader; deliberately exposes no task creation operation."""

import os

import httpx


class ArkProviderTaskReader:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self._transport = transport

    def _get(self, path: str, params: dict | None = None) -> dict:
        base = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        key = os.environ.get("ARK_API_KEY", "")
        if not key or not base.startswith("https://"):
            raise ValueError("Ark lookup is not configured")
        with httpx.Client(transport=self._transport, timeout=30, follow_redirects=False) as client:
            response = client.get(
                f"{base.rstrip('/')}/contents/generations/tasks{path}",
                params=params,
                headers={"Authorization": f"Bearer {key}"},
            )
            response.raise_for_status()
            return response.json()

    def list_tasks(self, *, model: str, page: int, page_size: int) -> list[dict]:
        result = self._get("", {"filter.model": model, "page_num": page, "page_size": page_size})
        items = result.get("items")
        if not isinstance(items, list):
            raise ValueError("invalid provider task listing")
        return items

    def get_task(self, task_id: str) -> dict:
        return self._get(f"/{task_id}")
