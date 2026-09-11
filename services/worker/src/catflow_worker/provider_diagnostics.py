"""Transport evidence without retaining request payloads, credentials or media URLs."""

from __future__ import annotations

import re
from typing import Any

import httpx


def timeout_evidence(exc: BaseException) -> dict[str, Any]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and all(current is not item for item in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    category = None
    for item in chain:
        for kind, name in (
            (httpx.ConnectTimeout, "connect"),
            (httpx.WriteTimeout, "write"),
            (httpx.ReadTimeout, "read"),
            (httpx.PoolTimeout, "pool"),
        ):
            # httpcore also occurs below SDK exceptions; names describe the same
            # transport phases, while a generic SDK timeout remains unknown.
            if isinstance(item, kind) or type(item).__name__ == kind.__name__:
                category = name
                break
        if category:
            break
    if category is None and any(
        isinstance(item, TimeoutError) or "timeout" in type(item).__name__.lower() for item in chain
    ):
        category = "unknown"
    return {"timeoutCategory": category, "exceptionTypes": [type(item).__name__ for item in chain]}


def request_size_evidence(request: httpx.Request | None) -> dict[str, Any]:
    if request is None:
        return {}
    try:
        size = len(request.content)
    except (httpx.RequestNotRead, AttributeError):
        length = request.headers.get("content-length")
        if not length or not length.isdigit():
            return {}
        return {"requestBytes": int(length), "requestBytesSource": "content_length"}
    return {"requestBytes": size, "requestBytesSource": "http_request_body"}


def redact_transport_error(value: Any) -> Any:
    """Error bodies may echo inputs; diagnostic receipts must never keep those."""
    if isinstance(value, dict):
        return {
            key: redact_transport_error(item)
            for key, item in value.items()
            if str(key).lower().replace("-", "_")
            not in {
                "authorization",
                "api_key",
                "apikey",
                "access_token",
                "secret",
                "credentials",
                "content",
                "input",
                "request",
                "prompt",
                "url",
                "image_url",
                "video_url",
                "b64_json",
                "base64",
                "token",
            }
        }
    if isinstance(value, list):
        return [redact_transport_error(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"data:[^\s\"\'<>]+", "[媒体内容已隐藏]", value, flags=re.I)
        value = re.sub(r"https?://[^\s\"\'<>]+", "[媒体地址已隐藏]", value, flags=re.I)
        value = re.sub(r"[A-Za-z0-9+/]{256,}={0,2}", "[编码内容已隐藏]", value)
        value = re.sub(r"(?i)(bearer\s+)[^\s,;\"\']+", r"\1[凭据已隐藏]", value)
        value = re.sub(
            r"""(?i)(["']?(?:api[_-]?key|access[_-]?token|secret|authorization)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}\]]+)""",
            r"\1[凭据已隐藏]",
            value,
        )
        return value[:4000]
    return value
