from __future__ import annotations

from fastapi.testclient import TestClient

from catflow.application.service import StudioService
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
            allowed_origins=("http://127.0.0.1:8765", "http://localhost:8765"),
        ),
    )
    return TestClient(app)


def test_runtime_bootstrap_exposes_only_the_ephemeral_csrf_token() -> None:
    response = _client().get("/api/v1/runtime/bootstrap")

    assert response.status_code == 200
    assert response.json() == {
        "csrfToken": "csrf-for-test",
        "baseUrl": "http://127.0.0.1:8765",
        "localOnly": True,
    }


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
            "Origin": "http://127.0.0.1:8765",
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
