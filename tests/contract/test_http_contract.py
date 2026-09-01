from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from catflow.application.provider_config import ProviderRuntime
from catflow.application.service import StudioService
from catflow.domain.models import LifeStoryProposalDraft
from catflow.infrastructure.memory_repository import MemoryStudioRepository
from catflow.interfaces.api import AppSettings, create_app

WRITE_HEADERS = {
    "Origin": "http://127.0.0.1:8877",
    "X-CatFlow-CSRF": "contract-csrf",
}


def _app_client() -> tuple[StudioService, TestClient]:
    service = StudioService(
        MemoryStudioRepository(),
        provider_runtime=ProviderRuntime(
            provider="ark",
            planning_model="doubao-seed-2-1-pro-260628",
            image_model="doubao-seedream-5-0-260128",
            video_model="doubao-seedance-2-0-260128",
            diagnostic_model="doubao-seed-2-1-pro-260628",
            capability_revision="ark-seedance-2.0-v1",
            paid_calls_enabled=True,
            maximum_video_references=5,
        ),
    )
    app = create_app(
        service,
        settings=AppSettings(
            csrf_token="contract-csrf",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8877",),
            base_url="http://127.0.0.1:8877",
        ),
    )
    return service, TestClient(app)


def test_openapi_exposes_one_goal_focused_api_surface() -> None:
    _, client = _app_client()
    paths = set(client.get("/openapi.json").json()["paths"])

    assert {
        "/api/v1/health",
        "/api/v1/runtime/bootstrap",
        "/api/v1/validation-runs/preview",
        "/api/v1/validation-runs",
        "/api/v1/validation-runs/{run_id}",
        "/api/v1/canon/current",
        "/api/v1/projects",
        "/api/v1/projects/{project_id}",
        "/api/v1/projects/{project_id}/workspace",
        "/api/v1/projects/{project_id}/planner",
        "/api/v1/projects/{project_id}/planner/messages",
        "/api/v1/projects/{project_id}/planner/proposals/{proposal_id}/adopt",
        "/api/v1/projects/{project_id}/stories",
        "/api/v1/projects/{project_id}/stories/{story_version_id}/activate",
        "/api/v1/projects/{project_id}/shot-plans",
        "/api/v1/projects/{project_id}/shot-plans/{shot_plan_version_id}/activate",
        "/api/v1/projects/{project_id}/assets",
        "/api/v1/projects/{project_id}/assets/upload",
        "/api/v1/assets/{asset_id}",
        "/api/v1/projects/{project_id}/assets/{asset_id}/diagnose",
        "/api/v1/projects/{project_id}/selections",
        "/api/v1/projects/{project_id}/asset-generations/preview",
        "/api/v1/projects/{project_id}/asset-generations",
        "/api/v1/projects/{project_id}/video-generations/preview",
        "/api/v1/projects/{project_id}/video-generations",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/jobs/{job_id}/resume-storage",
        "/api/v1/events",
        "/api/v1/projects/{project_id}/edits",
        "/api/v1/projects/{project_id}/exports",
        "/api/v1/projects/{project_id}/final-selection",
        "/api/v1/assets/{asset_id}/content",
    } <= paths
    assert all("/api/v2" not in path for path in paths)


def test_validation_run_http_flow_only_authorizes_the_frozen_manifest() -> None:
    service, client = _app_client()

    preview = client.post(
        "/api/v1/validation-runs/preview",
        json={},
        headers=WRITE_HEADERS,
    )
    assert preview.status_code == 200
    assert preview.json()["totalCallLimit"] == 9
    assert preview.json()["maximumVideoCalls"] == 3

    authorized = client.post(
        "/api/v1/validation-runs",
        json={
            "expectedManifestHash": preview.json()["manifestHash"],
            "paidCallAcknowledged": True,
        },
        headers=WRITE_HEADERS,
    )
    assert authorized.status_code == 201
    run_id = authorized.json()["id"]
    assert client.get(f"/api/v1/validation-runs/{run_id}").json()["status"] == "authorized"
    assert service.list_projects() == []


def test_planner_http_flow_returns_durable_job_and_adopts_directly_to_story() -> None:
    service, client = _app_client()
    manifest = client.post(
        "/api/v1/validation-runs/preview",
        json={},
        headers=WRITE_HEADERS,
    ).json()
    validation_run_id = client.post(
        "/api/v1/validation-runs",
        json={
            "expectedManifestHash": manifest["manifestHash"],
            "paidCallAcknowledged": True,
        },
        headers=WRITE_HEADERS,
    ).json()["id"]
    project_response = client.post(
        "/api/v1/projects",
        json={
            "title": "雨天擦爪",
            "theme": "雨天擦爪",
            "targetDurationSeconds": 12,
        },
        headers=WRITE_HEADERS,
    )
    project_id = uuid.UUID(project_response.json()["id"])

    job_response = client.post(
        f"/api/v1/projects/{project_id}/planner/messages",
        json={
            "text": "做一个雨天擦爪的生活片段",
            "expectedContextRevision": 1,
            "idempotencyKey": "http-planner-rain",
            "validationRunId": validation_run_id,
            "paidCallAcknowledged": True,
        },
        headers=WRITE_HEADERS,
    )
    assert job_response.status_code == 202
    job_id = uuid.UUID(job_response.json()["id"])

    proposal = service.complete_planner_job(
        job_id,
        LifeStoryProposalDraft(
            title="雨天擦爪",
            summary="孩子替回家的猫咪擦干爪子。",
            body="孩子在玄关铺开毛巾。",
            trigger="猫咪踩着湿脚印进门",
            childAction="孩子铺开毛巾",
            catResponse="猫咪把前爪放上毛巾",
            visibleChange="脚印停止延伸",
            warmEnding="猫咪靠着孩子打呼噜",
            targetDurationSeconds=12,
            dialoguePolicy="none",
            environmentIntent="雨天玄关",
        ),
    )
    adopted = client.post(
        f"/api/v1/projects/{project_id}/planner/proposals/{proposal.id}/adopt",
        json={},
        headers=WRITE_HEADERS,
    )

    assert adopted.status_code == 201
    assert adopted.json()["revision"] == 1
    assert adopted.json()["active"] is True
    assert (
        client.get(f"/api/v1/projects/{project_id}/planner").json()["proposals"][0]["status"]
        == "adopted"
    )


def test_http_conflict_is_never_disguised_as_server_failure() -> None:
    _, client = _app_client()
    project_id = client.post(
        "/api/v1/projects",
        json={"title": "纸星星", "theme": "窗边折纸", "targetDurationSeconds": 10},
        headers=WRITE_HEADERS,
    ).json()["id"]

    response = client.post(
        f"/api/v1/projects/{project_id}/planner/messages",
        json={
            "text": "继续",
            "expectedContextRevision": 9,
            "idempotencyKey": "http-wrong-context",
        },
        headers=WRITE_HEADERS,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "planner context revision changed"


def test_spa_deep_links_fall_back_to_index_without_masking_api_404(tmp_path) -> None:  # type: ignore[no-untyped-def]
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>CatFlow SPA</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('catflow')", encoding="utf-8")
    app = create_app(
        StudioService(MemoryStudioRepository()),
        settings=AppSettings(csrf_token="test-csrf", allowed_hosts=("testserver",)),
        spa_dist=dist,
    )
    client = TestClient(app)

    deep_link = client.get("/projects/123/planner")
    asset = client.get("/assets/app.js")
    missing_api = client.get("/api/v1/not-a-real-route")

    assert deep_link.status_code == 200
    assert "CatFlow SPA" in deep_link.text
    assert asset.status_code == 200
    assert missing_api.status_code == 404
