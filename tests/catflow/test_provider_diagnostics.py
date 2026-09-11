from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from volcenginesdkarkruntime._exceptions import ArkAPITimeoutError

from catflow.application.gateways import ProviderGatewayError
from catflow_worker.ark_gateway import ArkGatewaySettings, ArkTypedGateway, _provider_error
from catflow_worker.provider_receipts import ProviderCall, provider_call


@pytest.mark.parametrize(
    "timeout,category",
    [
        (httpx.ConnectTimeout, "connect"),
        (httpx.WriteTimeout, "write"),
        (httpx.ReadTimeout, "read"),
        (httpx.PoolTimeout, "pool"),
    ],
)
def test_sdk_timeout_keeps_underlying_phase_request_size_and_safe_trace(
    monkeypatch, timeout, category
):
    from catflow_worker import ark_gateway

    receipts = []
    monkeypatch.setattr(ark_gateway, "receive_receipt", receipts.append)
    body = json.dumps(
        {
            "content": [
                {"image_url": "data:image/png;base64,c2VjcmV0"},
                {"video_url": "https://private.test/video?signature=secret"},
            ]
        }
    ).encode()
    request = httpx.Request(
        "POST",
        "https://provider.test/tasks",
        content=body,
        headers={"X-Client-Request-Id": "client-123", "Authorization": "Bearer hidden-key"},
    )
    error = ArkAPITimeoutError(request=request, request_id="client-123")
    error.__cause__ = timeout("transport timeout", request=request)
    mapped = _provider_error(
        error,
        submission=True,
        diagnostics={"submitElapsedMs": 120000, "imageReferenceCount": 1, "videoReferenceCount": 1},
    )
    evidence = mapped.as_error_document()["diagnostics"]
    assert evidence["timeoutCategory"] == category
    assert evidence["requestBytes"] == len(body)
    assert evidence["requestBytesSource"] == "http_request_body"
    assert evidence["clientRequestId"] == "client-123"
    assert evidence["serverRequestId"] is None
    assert mapped.submission_unknown and mapped.timed_out and not mapped.retryable
    serialized = json.dumps(receipts) + json.dumps(mapped.as_error_document())
    assert all(
        secret not in serialized
        for secret in ["hidden-key", "c2VjcmV0", "signature=secret", "private.test"]
    )


def test_generic_timeout_does_not_invent_transport_phase_and_error_echoes_are_redacted(monkeypatch):
    from catflow_worker import ark_gateway

    receipts = []
    monkeypatch.setattr(ark_gateway, "receive_receipt", receipts.append)
    error = TimeoutError(
        "Read https://private.test?signature=secret data:image/png;base64,AAA Bearer hidden-key"
    )
    error.body = {
        "error": {"code": "timeout", "message": str(error), "input": "payload"},
        "api_key": "hidden-key",
    }
    mapped = _provider_error(error, submission=True)
    assert mapped.as_error_document()["diagnostics"]["timeoutCategory"] == "unknown"
    text = json.dumps(receipts) + str(mapped)
    assert all(value not in text for value in ["https://", "base64,", "hidden-key", "payload"])


@pytest.mark.parametrize(
    "message",
    [
        '{"api_key": "quoted-secret with spaces"}',
        "{'access_token': 'quoted-secret with spaces'}",
        "api-key=quoted-secret",
        "secret:quoted-secret",
    ],
)
def test_quoted_credentials_cannot_escape_error_message_redaction(monkeypatch, message):
    from catflow_worker import ark_gateway

    receipts = []
    monkeypatch.setattr(ark_gateway, "receive_receipt", receipts.append)
    mapped = _provider_error(TimeoutError(message), submission=True)
    assert "quoted-secret" not in json.dumps(receipts) + json.dumps(mapped.as_error_document())


@pytest.mark.parametrize("outcome", ["timeout", "missing_id", "success"])
def test_video_submission_measures_evidence_without_retry_or_changing_timeout(monkeypatch, outcome):
    from catflow_worker import ark_gateway

    receipts, requests = [], []
    monkeypatch.setattr(ark_gateway, "receive_receipt", receipts.append)

    def create(**kwargs):
        requests.append(kwargs)
        request = httpx.Request(
            "POST",
            "https://provider.test/tasks",
            json={k: v for k, v in kwargs.items() if k not in {"timeout", "extra_headers"}},
            headers=kwargs["extra_headers"],
        )
        if outcome == "timeout":
            outer = ArkAPITimeoutError(
                request=request, request_id=kwargs["extra_headers"]["X-Client-Request-Id"]
            )
            outer.__cause__ = httpx.ReadTimeout("timed out", request=request)
            raise outer
        return SimpleNamespace(
            headers={"x-request-id": "server-456"},
            http_response=httpx.Response(200, request=request),
            json=lambda: {"id": "provider-task"} if outcome == "success" else {},
        )

    call = ProviderCall(
        uuid.uuid4(), {"version": 2}, None, lambda _: None, diagnostics={"localPreparationMs": 250}
    )
    monkeypatch.setattr(call, "receive", receipts.append)
    token = provider_call.set(call)
    settings = ArkGatewaySettings(
        "test-key", "https://provider.test", "plan", "image", "video", "diagnostic", 120
    )
    gateway = ArkTypedGateway(
        settings,
        client=SimpleNamespace(
            content_generation=SimpleNamespace(
                tasks=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
            )
        ),
    )
    try:
        content = [
            {"type": "text", "text": "静态结尾"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
            {
                "type": "video_url",
                "video_url": {"url": "https://private.test/video?signature=secret"},
            },
        ]
        if outcome == "success":
            result = gateway._submit_video_task(content=content, duration=4)
            assert result.task_id == "provider-task"
        else:
            with pytest.raises(ProviderGatewayError) as caught:
                gateway._submit_video_task(content=content, duration=4)
            assert caught.value.submission_unknown
            assert caught.value.diagnostics["requestBytesSource"] == "http_request_body"
        assert len(requests) == 1 and requests[0]["timeout"] == 120
        metrics = [
            entry["result"].get("submissionDiagnostics")
            for entry in receipts
            if entry.get("result", {}).get("submissionDiagnostics")
        ][-1]
        assert metrics["imageReferenceCount"] == metrics["videoReferenceCount"] == 1
        assert metrics["localPreparationMs"] == 250
        assert "base64" not in json.dumps(receipts) and "signature=secret" not in json.dumps(
            receipts
        )
    finally:
        provider_call.reset(token)
