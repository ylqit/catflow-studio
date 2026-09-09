"""Ark Responses streaming reception and receipt parsing; no implicit creation retry."""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from catflow.application.gateways import ProviderGatewayError

from .provider_receipts import provider_call, receipt_document, receive_receipt


def response_usage(document: dict[str, Any]) -> dict[str, int]:
    names = {
        "input_tokens": "inputTokens",
        "output_tokens": "outputTokens",
        "total_tokens": "totalTokens",
        "completion_tokens": "completionTokens",
    }
    return {
        names[k]: v
        for k, v in (document.get("usage") or {}).items()
        if k in names and isinstance(v, int) and not isinstance(v, bool)
    }


def save_response(response: Any, *, complete: bool | None = None) -> dict[str, Any]:
    document = receipt_document(response)
    status = document.get("status")
    receive_receipt(
        {
            "responseId": document.get("id"),
            "providerStatus": status,
            "complete": status == "completed" if complete is None else complete,
            "providerError": document.get("error"),
            "usage": response_usage(document),
            "result": {"rawResponse": document},
            **({"store": document["store"]} if isinstance(document.get("store"), bool) else {}),
            **(
                {
                    "responseExpiresAt": datetime.fromtimestamp(
                        document["expire_at"], UTC
                    ).isoformat()
                }
                if isinstance(document.get("expire_at"), int)
                else {}
            ),
        }
    )
    return document


def receive_response_stream(client: Any, request: dict[str, Any]) -> dict[str, Any]:
    call = provider_call.get()
    if call is None:
        raise RuntimeError("streaming response requires a durable job context")
    contract = call.contract
    client_id = str(uuid.uuid4())
    retention = int(contract.get("retentionSeconds", 259200))
    if not 0 < retention <= 259200:
        raise ValueError("Responses 保存期限必须在 1 秒到 3 天之间。")
    expires = int(time.time()) + retention
    call.receive(
        {
            "clientRequestId": client_id,
            "store": True,
            "responseExpiresAt": datetime.fromtimestamp(expires, UTC).isoformat(),
        }
    )
    stream = client.responses.create(
        **request,
        store=True,
        stream=True,
        caching={"type": "disabled"},
        expire_at=expires,
        extra_headers={"X-Client-Request-Id": client_id},
        timeout=httpx.Timeout(float(contract.get("streamIdleTimeoutSeconds", 120)), connect=10),
    )
    headers = stream.response.headers
    call.receive({"serverRequestId": headers.get("x-request-id")})
    watchdog = threading.Timer(float(contract.get("streamTotalTimeoutSeconds", 1800)), stream.close)
    watchdog.daemon = True
    watchdog.start()
    chunks: list[str] = []
    pending_bytes = 0
    total_bytes = 0
    response_id = None
    terminal = None
    checkpoint_lock = threading.Lock()
    checkpoint_stop = threading.Event()
    checkpoint_errors: list[Exception] = []

    def persist_checkpoint():
        nonlocal pending_bytes
        with checkpoint_lock:
            if not pending_bytes:
                return
            document = {
                "responseId": response_id,
                "complete": False,
                "result": {"rawText": "".join(chunks)},
            }
            pending_bytes = 0
        call.receive(document)

    def checkpoint_loop():
        while not checkpoint_stop.wait(0.5):
            try:
                persist_checkpoint()
            except Exception as exc:
                checkpoint_errors.append(exc)
                stream.close()
                return

    checkpoint_thread = threading.Thread(target=checkpoint_loop, daemon=True)
    checkpoint_thread.start()
    try:
        for event in stream:
            if checkpoint_errors:
                raise checkpoint_errors[0]
            event_type = getattr(event, "type", "")
            response = getattr(event, "response", None)
            if response is not None:
                response_id = getattr(response, "id", response_id)
                document = save_response(response)
                if event_type in {"response.completed", "response.failed", "response.incomplete"}:
                    terminal = document
            if event_type == "response.output_text.delta":
                delta = str(getattr(event, "delta", ""))
                size = len(delta.encode())
                with checkpoint_lock:
                    chunks.append(delta)
                    pending_bytes += size
                total_bytes += size
                if total_bytes > 2 * 1024 * 1024:
                    raise ProviderGatewayError(
                        code="structured_output_too_large",
                        message="返回正文超过 2 MiB；已保留接收记录。",
                        retryable=False,
                        response_id=response_id,
                    )
                if pending_bytes >= 16384:
                    persist_checkpoint()
            if event_type == "error":
                call.receive({"result": {"streamError": receipt_document(event)}})
                raise ProviderGatewayError(
                    code="response_stream_error",
                    message=str(getattr(event, "message", "流式接收异常")),
                    retryable=False,
                    response_id=response_id,
                    submission_unknown=True,
                )
    finally:
        checkpoint_stop.set()
        checkpoint_thread.join(timeout=2)
        watchdog.cancel()
        stream.close()
        persist_checkpoint()
    if checkpoint_errors:
        raise checkpoint_errors[0]
    if terminal is None:
        raise ProviderGatewayError(
            code="response_stream_interrupted",
            message="连接结束但未取得完成回执，将按已保存编号核实。",
            retryable=False,
            response_id=response_id,
            submission_unknown=True,
        )
    return terminal


def parse_response(document: dict[str, Any]) -> dict[str, Any]:
    status = document.get("status")
    if status != "completed":
        raise ProviderGatewayError(
            code="response_not_completed",
            message=f"Ark Response: {status}",
            retryable=False,
            response_id=document.get("id"),
            provider_status=status,
            incomplete_reason=(document.get("incomplete_details") or {}).get("reason"),
            usage=response_usage(document),
        )
    text = document.get("output_text") or "".join(
        content.get("text", "")
        for output in document.get("output", [])
        if output.get("type") == "message"
        for content in output.get("content", [])
        if content.get("type") == "output_text"
    )
    if len(text.encode()) > 2 * 1024 * 1024:
        raise ProviderGatewayError(
            code="structured_output_too_large",
            message="返回正文超过 2 MiB。",
            retryable=False,
            response_id=document.get("id"),
        )
    try:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("expected JSON object")
        pending = [(payload, 0)]
        while pending:
            node, depth = pending.pop()
            if depth > 40:
                raise ProviderGatewayError(
                    code="structured_output_too_deep",
                    message="JSON nesting exceeds 40",
                    retryable=False,
                    response_id=document.get("id"),
                )
            if isinstance(node, dict):
                pending.extend((v, depth + 1) for v in node.values())
            elif isinstance(node, list):
                pending.extend((v, depth + 1) for v in node)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProviderGatewayError(
            code="invalid_structured_output",
            message=str(exc),
            retryable=False,
            response_id=document.get("id"),
        ) from exc
    return {"payload": payload, "responseId": document.get("id"), "model": document.get("model")}
