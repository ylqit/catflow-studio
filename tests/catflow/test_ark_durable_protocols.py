"""Offline SDK/receipt contracts; not real provider acceptance evidence."""

from __future__ import annotations

import json
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from volcenginesdkarkruntime import Ark

from catflow.application.gateways import ProviderGatewayError
from catflow.application.job_execution import execution_contract
from catflow_worker.ark_gateway import ArkGatewaySettings, ArkTypedGateway
from catflow_worker.ark_responses import receive_response_stream
from catflow_worker.provider_receipts import ProviderCall, ReceiptJournal, provider_call


@pytest.fixture
def receiving(tmp_path):
    journal = ReceiptJournal(tmp_path)
    identifier = uuid.uuid4()
    documents = []
    call = ProviderCall(identifier, execution_contract("plan_shots"), journal, documents.append)
    token = provider_call.set(call)
    yield call, documents
    provider_call.reset(token)


def test_stream_checkpoints_during_idle_and_preserves_created_id(receiving):
    call, documents = receiving
    persisted = threading.Event()
    original = call.register

    def register(receipt):
        original(receipt)
        if receipt["document"].get("result", {}).get("rawText"):
            persisted.set()

    call.register = register
    requests = []

    class InterruptedStream:
        response = SimpleNamespace(headers={"x-request-id": "server-id"})

        def __iter__(self):
            yield SimpleNamespace(
                type="response.created",
                response=SimpleNamespace(
                    id="response-id",
                    status="in_progress",
                ),
            )
            yield SimpleNamespace(type="response.output_text.delta", delta='{"shots":')
            assert persisted.wait(1), "body must reach disk even while the next delta is absent"

        def close(self):
            pass

    def create(**request):
        requests.append(request)
        return InterruptedStream()

    with pytest.raises(ProviderGatewayError, match="连接结束"):
        receive_response_stream(SimpleNamespace(responses=SimpleNamespace(create=create)), {})
    assert len(requests) == 1
    assert requests[0]["stream"] and requests[0]["store"]
    assert requests[0]["caching"] == {"type": "disabled"}
    assert 259195 < requests[0]["expire_at"] - time.time() <= 259200
    receipts = call.journal.read(call.job_id)
    assert any(r["document"].get("responseId") == "response-id" for r in receipts)
    assert any(r["document"].get("result", {}).get("rawText") == '{"shots":' for r in receipts)
    assert not any(r["document"].get("complete") for r in receipts)


def test_sdk_video_raw_receipt_is_registered_before_return(receiving):
    call, documents = receiving
    call.contract = execution_contract("generate_video")
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "accepted-task"}, headers={"x-request-id": "server"})

    client = Ark(
        api_key="offline-only",
        base_url="https://unit.invalid/api/v3",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    gateway = ArkTypedGateway(
        ArkGatewaySettings(
            api_key="offline-only",
            base_url="https://unit.invalid/api/v3",
            planning_model="plan",
            diagnostic_model="plan",
            image_model="image",
            video_model="video",
            request_timeout_seconds=30,
        ),
        client=client,
    )
    result = gateway.submit_video(
        prompt="contract",
        reference_paths=(),
        reference_roles=(),
        duration_seconds=8,
        resolution="480p",
    )
    assert result.task_id == "accepted-task" and result.request_id == "server"
    assert len(requests) == 1
    assert requests[0].headers["x-client-request-id"]
    assert any(r["document"].get("taskId") == "accepted-task" for r in documents)
    assert json.loads(requests[0].content)["duration"] == 8
    client.close()


def test_database_failure_does_not_discard_received_id(receiving):
    from sqlalchemy.exc import OperationalError

    call, _ = receiving

    def unavailable(_receipt):
        raise OperationalError("isolated", {}, Exception("offline database"))

    call.register = unavailable
    call.receive({"responseId": "received-during-outage"})
    assert call.journal.read(call.job_id)[0]["document"]["responseId"] == "received-during-outage"


@pytest.mark.parametrize(
    "kind,video_status",
    [("video", status) for status in ("succeeded", "failed", "cancelled", "expired")]
    + [("response", None), ("image", None)],
)
def test_installed_sdk_query_and_image_methods_persist_raw_results(receiving, kind, video_status):
    call, documents = receiving
    call.contract = execution_contract(
        "generate_image"
        if kind == "image"
        else "generate_video"
        if kind == "video"
        else "plan_shots"
    )
    bodies = {
        "video": {
            "id": "existing-task",
            "status": video_status,
            "content": {"video_url": "https://unit.invalid/video.mp4"},
            **(
                {"error": {"code": "original_provider_code", "message": "original failure detail"}}
                if video_status != "succeeded"
                else {}
            ),
        },
        "response": {
            "id": "existing-response",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
        },
        "image": {
            "model": "image",
            "data": [{"url": "https://unit.invalid/image.png"}],
            "usage": {"generated_images": 1},
        },
    }
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json=bodies[kind], headers={"x-request-id": "server"})

    client = Ark(
        api_key="offline-only",
        base_url="https://unit.invalid/api/v3",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    gateway = ArkTypedGateway(
        ArkGatewaySettings(
            "offline-only", "https://unit.invalid/api/v3", "plan", "image", "video", "plan", 30
        ),
        client=client,
    )
    gateway.validate_execution_contract(
        {},
        "generate_image"
        if kind == "image"
        else "generate_video"
        if kind == "video"
        else "plan_shots",
    )
    if kind == "video":
        result = gateway.poll_video("existing-task")
        assert result.provider_status == video_status
        assert result.status == ("succeeded" if video_status == "succeeded" else "failed")
        if video_status != "succeeded":
            assert result.error["message"] == "original failure detail"
    elif kind == "response":
        gateway.retrieve_response("existing-response")
    else:
        assert gateway.generate_image(
            prompt="scene", negative_prompt="no text", reference_paths=(), reference_roles=()
        ).url.endswith("image.png")
    assert len(requests) == 1
    assert requests[0].method == ("POST" if kind == "image" else "GET")
    assert requests[0].url.path.endswith(
        {
            "video": "/contents/generations/tasks/existing-task",
            "response": "/responses/existing-response",
            "image": "/images/generations",
        }[kind]
    )
    assert any(
        r["document"].get("result", {}).get("rawResponse") == bodies[kind] for r in documents
    )
    assert call.journal.read(call.job_id)
    client.close()


def test_missing_sdk_method_is_blocked_before_submission():
    gateway = ArkTypedGateway(
        ArkGatewaySettings(
            "offline-only", "https://unit.invalid/api/v3", "plan", "image", "video", "plan", 30
        ),
        client=SimpleNamespace(),
    )
    with pytest.raises(ProviderGatewayError) as error:
        gateway.validate_execution_contract({}, "generate_image")
    assert error.value.code == "local_adapter_error"
    assert not error.value.submission_unknown
