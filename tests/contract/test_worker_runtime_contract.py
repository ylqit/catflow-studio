from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import StudioService
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.interfaces.api import AppSettings, create_app

WRITE_HEADERS = {
    "Origin": "http://127.0.0.1:8877",
    "X-CatFlow-CSRF": "worker-runtime-csrf",
}


def _client(settings: AppSettings) -> TestClient:
    service = StudioService(
        MemoryStudioRepository(),
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="planning-model",
            image_model="image-model",
            video_model="video-model",
            diagnostic_model="diagnostic-model",
            capability_revision="ark-test-v1",
            paid_calls_enabled=True,
            maximum_video_references=5,
            segment_reference_publishing_ready=True,
        ),
    )
    return TestClient(create_app(service, settings=settings))


def test_runtime_bootstrap_rejects_a_stale_worker_heartbeat(tmp_path: Path) -> None:
    ready_file = tmp_path / "worker-ready.json"
    supervisor_file = tmp_path / "worker-supervisor.json"
    old_heartbeat = datetime.now(UTC) - timedelta(seconds=16)
    ready_file.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "pid": os.getpid(),
                "workerId": "stale-worker",
                "provider": "ark",
                "startedAt": (old_heartbeat - timedelta(minutes=1)).isoformat(),
                "heartbeatAt": old_heartbeat.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    supervisor_file.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "supervisorPid": os.getpid(),
                "workerPid": os.getpid(),
                "state": "ready",
                "restartCount": 2,
                "lastExitAt": (old_heartbeat - timedelta(minutes=2)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    client = _client(
        AppSettings(
            csrf_token="worker-runtime-csrf",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8877",),
            worker_ready_file=ready_file,
            worker_supervisor_file=supervisor_file,
        )
    )

    runtime = client.get("/api/v1/runtime/bootstrap").json()

    assert runtime["workerReady"] is False
    assert runtime["worker"] == {
        "ready": False,
        "state": "stale",
        "lastHeartbeatAt": old_heartbeat.isoformat(),
        "lastExitAt": (old_heartbeat - timedelta(minutes=2)).isoformat(),
        "restartCount": 2,
        "retryingAutomatically": True,
    }
    assert "pid" not in str(runtime["worker"]).lower()


def test_worker_offline_blocks_job_creation_before_a_job_is_written() -> None:
    client = _client(
        AppSettings(
            csrf_token="worker-runtime-csrf",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8877",),
            worker_ready=False,
        )
    )
    project_id = client.post(
        "/api/v1/projects",
        json={"title": "后台离线", "theme": "窗边折纸", "targetDurationSeconds": 8},
        headers=WRITE_HEADERS,
    ).json()["id"]

    response = client.post(
        f"/api/v1/projects/{project_id}/planner/messages",
        json={
            "text": "孩子折好纸星星，猫咪轻碰一下。",
            "expectedContextRevision": 1,
            "idempotencyKey": "offline-planner-job",
        },
        headers=WRITE_HEADERS,
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "worker_unavailable",
            "message": "后台任务暂时不可用，本次操作没有创建任务。系统正在尝试恢复，请稍后再试。",
            "retryable": True,
        }
    }
    preview = client.post(
        f"/api/v1/projects/{project_id}/video-generations/preview",
        json={},
        headers=WRITE_HEADERS,
    )
    assert preview.status_code != 503


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/projects/{project_id}/shot-plans/generations",
            {"idempotencyKey": "offline-shot-plan"},
        ),
        (
            "/api/v1/projects/{project_id}/assets/{resource_id}/diagnose",
            {"assetId": "{resource_id}", "idempotencyKey": "offline-image-diagnosis"},
        ),
        (
            "/api/v1/projects/{project_id}/asset-generations",
            {
                "kind": "environment",
                "expectedInputHash": "a" * 64,
                "idempotencyKey": "offline-image-generation",
            },
        ),
        (
            "/api/v1/projects/{project_id}/video-generations",
            {
                "expectedInputHash": "b" * 64,
                "idempotencyKey": "offline-video-generation",
            },
        ),
        (
            "/api/v1/projects/{project_id}/video-diagnoses",
            {"assetId": "{resource_id}", "idempotencyKey": "offline-video-diagnosis"},
        ),
        (
            "/api/v1/projects/{project_id}/video-edits",
            {
                "baseVideoAssetId": "{resource_id}",
                "issueRange": {"startFrame": 0, "endFrame": 96},
                "instruction": "保持镜头不变，修正孩子为猫咪擦爪的动作。",
                "expectedInputHash": "c" * 64,
                "idempotencyKey": "offline-video-edit",
            },
        ),
        ("/api/v1/jobs/{resource_id}/resume-storage", {}),
        (
            "/api/v1/projects/{project_id}/exports",
            {"editVersionId": "{resource_id}", "idempotencyKey": "offline-export"},
        ),
    ],
)
def test_every_asynchronous_write_boundary_rejects_an_offline_worker(
    path: str,
    payload: dict[str, object],
) -> None:
    client = _client(
        AppSettings(
            csrf_token="worker-runtime-csrf",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8877",),
            worker_ready=False,
        )
    )
    project_id = str(uuid.uuid4())
    resource_id = str(uuid.uuid4())
    resolved_path = path.format(project_id=project_id, resource_id=resource_id)
    resolved_payload = json.loads(
        json.dumps(payload).replace("{resource_id}", resource_id)
    )

    response = client.post(resolved_path, json=resolved_payload, headers=WRITE_HEADERS)

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "worker_unavailable"
