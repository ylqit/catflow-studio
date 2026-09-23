"""HTTP to frozen task to actual typed Ark gateway boundary, with no network."""
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from catflow.application.gateways import VideoSubmissionResult
from catflow.application.production_props import PropImageInput, prop_image_preview
from catflow.interfaces.api import AppSettings, create_app
from catflow_worker.ark_job_gateway import ArkProviderJobGateway
from test_narrative_production import ready


def test_unit_http_request_reaches_worker_with_identical_prompt_and_prop_references():
    service, project, plan, _ = ready(15)
    client = TestClient(create_app(service, settings=AppSettings(csrf_token="test-token",
        allowed_hosts=("testserver",), allowed_origins=("http://127.0.0.1:8877",))))
    headers = {"Origin":"http://127.0.0.1:8877", "X-CatFlow-CSRF":"test-token"}
    base=f"/api/v1/projects/{project.id}/production-units/unit-1"
    preview=client.post(base+"/preview",json={"planId":str(plan.id)},headers=headers)
    assert preview.status_code == 200, preview.text
    snapshot=preview.json()
    response=client.post(base+"/generations",headers=headers,json={"planId":str(plan.id),
        "expectedInputHash":snapshot["inputHash"],"idempotencyKey":"http-unit-request"})
    assert response.status_code == 202, response.text
    job=service.get_job(uuid.UUID(response.json()["id"]))
    calls=[]
    def submit(**kwargs):
        calls.append(kwargs)
        return VideoSubmissionResult("mock-task")
    gateway=ArkProviderJobGateway(SimpleNamespace(submit_video=submit),
        resolve_asset_paths=lambda ids: tuple(Path(str(value)+".png") for value in ids),
        extract_video_frames=lambda *_: ())
    gateway.submit(job_id=job.id,kind=job.kind,frozen_input=job.frozen_input)
    assert calls[0]["prompt"] == snapshot["compiledProviderPrompt"]
    assert calls[0]["reference_roles"] == tuple(snapshot["referenceRoles"])
    assert calls[0]["reference_roles"][-1] == "prop:box"
    assert [p.stem for p in calls[0]["reference_paths"]] == snapshot["referenceAssetIds"]
    assert calls[0]["duration_seconds"] == 15
    assert calls[0]["generate_audio"] is True
    repeat=client.post(base+"/generations",headers=headers,json={"planId":str(plan.id),
        "expectedInputHash":snapshot["inputHash"],"idempotencyKey":"http-unit-request"})
    assert repeat.json()["id"] == str(job.id)


def test_prop_preview_and_frame_preparation_use_real_bound_sources_without_paid_work():
    service, project, plan, _=ready(15)
    before=len(service.list_jobs(project.id)) if hasattr(service,"list_jobs") else len(service._repository.list_project_jobs(project.id))
    preview=prop_image_preview(service,project.id,PropImageInput(key="box",name="纸箱",appearance="一个棕色方箱"))
    assert preview["referenceRoles"] == ["style_board"]
    from catflow.application.shot_production import ShotMediaPreviewCommand
    frame=service.preview_shot_media(project.id,ShotMediaPreviewCommand(
        shotPlanVersionId=plan.document["shotPlanVersionId"],shotId="s1",purpose="shot_frame"))
    assert "prop:box" in frame["referenceRoles"]
    assert "一个棕色方箱" in frame["compiledProviderPrompt"]
    assert len(service._repository.list_project_jobs(project.id)) == before
