from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from catflow.application.service import JobDto, ProjectCreate, StudioService
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.interfaces.api import AppSettings, create_app
from catflow.interfaces.cli import validate_loopback_host


def _client() -> TestClient:
    service = StudioService(MemoryStudioRepository())
    app = create_app(
        service,
        settings=AppSettings(
            csrf_token="csrf-for-test",
            allowed_hosts=("testserver", "127.0.0.1", "localhost"),
            allowed_origins=("http://127.0.0.1:8877", "http://localhost:8877"),
            base_url="http://127.0.0.1:8877",
        ),
    )
    return TestClient(app)


def test_runtime_bootstrap_exposes_loopback_readiness_without_provider_secrets() -> None:
    response = _client().get("/api/v1/runtime/bootstrap")

    assert response.status_code == 200
    payload = response.json()
    assert payload["csrfToken"] == "csrf-for-test"
    assert payload["baseUrl"] == "http://127.0.0.1:8877"
    assert payload["localOnly"] is True
    assert payload["provider"]["apiKeyConfigured"] is False
    assert "apiKey" not in payload["provider"]


def test_write_request_requires_same_origin_and_csrf_header() -> None:
    client = _client()
    payload = {
        "title": "雨天擦爪",
        "theme": "孩子替回家的猫咪擦爪",
        "targetDurationSeconds": 12,
    }

    missing_headers = client.post("/api/v1/projects", json=payload)
    hostile_origin = client.post(
        "/api/v1/projects",
        json=payload,
        headers={"Origin": "https://hostile.example", "X-CatFlow-CSRF": "csrf-for-test"},
    )
    valid = client.post(
        "/api/v1/projects",
        json=payload,
        headers={
            "Origin": "http://127.0.0.1:8877",
            "X-CatFlow-CSRF": "csrf-for-test",
        },
    )

    assert missing_headers.status_code == 403
    assert missing_headers.json()["detail"] == "same-origin request required"
    assert hostile_origin.status_code == 403
    assert hostile_origin.json()["detail"] == "same-origin request required"
    assert valid.status_code == 201
    assert valid.json()["aspectRatio"] == "9:16"


def test_loopback_host_validation_has_no_public_escape_hatch() -> None:
    assert validate_loopback_host("127.0.0.1") == "127.0.0.1"

    for forbidden in ("0.0.0.0", "::", "192.168.1.10"):
        try:
            validate_loopback_host(forbidden)
        except ValueError as exc:
            assert "loopback" in str(exc)
        else:
            raise AssertionError(f"{forbidden} must not be accepted")


def test_job_and_project_usage_endpoints_preserve_missing_metrics_and_unpriced_state() -> None:
    repository = MemoryStudioRepository()
    service = StudioService(repository)
    project = service.create_project(
        ProjectCreate(title="雨天擦爪", theme="孩子替猫咪擦爪", targetDurationSeconds=12)
    )
    now = datetime.now(UTC)
    job = repository.create_job(
        JobDto(
            id=uuid.uuid4(),
            projectId=project.id,
            kind="generate_video",
            status="succeeded",
            inputHash="a" * 64,
            idempotencyKey="usage-http-test",
            provider="ark",
            model="doubao-seedance-2-0",
            actualUsage={"completionTokens": 9600},
            billingStatus="unpriced",
            providerRequestId="request-usage-test",
            frozenInput={},
            createdAt=now,
            updatedAt=now,
        )
    )
    client = TestClient(
        create_app(
            service,
            settings=AppSettings(
                csrf_token="csrf-for-test",
                allowed_hosts=("testserver",),
                allowed_origins=("http://127.0.0.1:8877",),
            ),
        )
    )

    usage = client.get(f"/api/v1/jobs/{job.id}/usage")
    summary = client.get(f"/api/v1/projects/{project.id}/usage-summary")

    assert usage.status_code == 200
    assert usage.json()["providerUsage"] == {"completionTokens": 9600}
    assert usage.json()["completionTokens"] == 9600
    assert usage.json()["inputTokens"] is None
    assert usage.json()["billingStatus"] == "unpriced"
    assert usage.json()["calculatedCostMicros"] is None
    assert "inputTokens" not in usage.json()["providerUsage"]
    assert summary.status_code == 200
    assert summary.json()["totals"] == {"completionTokens": 9600}
    assert summary.json()["unpricedJobCount"] == 1


def test_rate_card_publication_creates_an_immutable_revision_visible_to_settings() -> None:
    client = _client()
    response = client.post(
        "/api/v1/runtime/rate-cards",
        json={
            "provider": "ark",
            "model": "doubao-seed-2-1-pro-260628",
            "revision": "ark-planning-2026-09-02",
            "sourceUrl": "https://www.volcengine.com/docs/pricing",
            "effectiveFrom": "2026-09-02T00:00:00Z",
            "rates": [
                {
                    "metric": "inputTokens",
                    "unit": "million_tokens",
                    "unitPriceMicros": 2_000_000,
                },
                {
                    "metric": "outputTokens",
                    "unit": "million_tokens",
                    "unitPriceMicros": 5_000_000,
                },
            ],
        },
        headers={
            "Origin": "http://127.0.0.1:8877",
            "X-CatFlow-CSRF": "csrf-for-test",
        },
    )

    assert response.status_code == 201
    assert response.json()["revision"] == "ark-planning-2026-09-02"
    assert len(response.json()["rates"]) == 2
    listing = client.get("/api/v1/runtime/rate-cards")
    assert listing.status_code == 200
    assert listing.json() == [response.json()]


def test_video_edit_product_routes_exist_while_legacy_repair_writes_are_deprecated() -> None:
    schema = _client().get("/openapi.json").json()
    paths = schema["paths"]

    assert "/api/v1/projects/{project_id}/video-edits/preview" in paths
    assert "/api/v1/projects/{project_id}/video-edits" in paths
    assert "/api/v1/projects/{project_id}/video-edits/{edit_id}/approve" in paths
    assert paths["/api/v1/projects/{project_id}/video-repairs/preview"]["post"][
        "deprecated"
    ] is True
