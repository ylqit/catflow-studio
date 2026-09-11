from __future__ import annotations

import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from catflow.application.service import (
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    GenerationCommand,
    PlannerMessageCommand,
    SegmentReferencePreparationCommand,
    SegmentRepairCreateCommand,
    SegmentRepairPreviewCommand,
    ShotPlanActivationCommand,
    ShotPlanGenerationCommand,
)
from catflow.application.shot_production import (
    ShotFrameConfirmCommand,
    ShotMediaCommand,
    ShotMediaPreviewCommand,
)
from catflow.interfaces.api import AppSettings, create_app
from catflow_worker.ark_gateway import ArkTypedGateway
from catflow_worker.ark_job_gateway import ArkProviderJobGateway
from test_ark_gateway import Recorder, _image, _settings
from test_video_repair_workflow import _prepared_project
from test_workflow import _director_payload, _project, _proposal, _service


def _ready_storyboard():
    service = _service()
    project = _project(service)
    job = service.enqueue_planner_message(
        project.id,
        PlannerMessageCommand(
            text="整理物品",
            expectedContextRevision=1,
            idempotencyKey="prompt-boundary-story",
        ),
    )
    proposal = service.complete_planner_job(job.id, _proposal())
    service.adopt_proposal(project.id, proposal.id)
    scene = service.register_asset(project.id, role="environment", sha256="f" * 64)
    service.select_asset(project.id, slot="environment", asset_id=scene.id)
    director = service.create_shot_plan_generation_job(
        project.id,
        ShotPlanGenerationCommand(
            idempotencyKey="prompt-boundary-director",
        ),
    )
    plan = service.complete_shot_plan_job(director.id, _director_payload())
    service.activate_shot_plan(
        project.id,
        plan.id,
        ShotPlanActivationCommand(
            expectedActiveShotPlanVersionId=None,
            idempotencyKey="prompt-boundary-adopt",
        ),
    )
    service._repository._jobs[director.id] = director.model_copy(update={"status": "succeeded"})
    return service, project.id, plan


def _submit_frozen_to_sdk(job, tmp_path):
    image_path = _image(tmp_path / "input.png", "blue")
    recorder = Recorder(
        SimpleNamespace(
            id="sdk-task",
            data=[SimpleNamespace(url="https://example.com/image.png")],
        )
    )
    settings = replace(_settings(), video_model=job.model, image_model=job.model)
    typed = ArkTypedGateway(
        settings,
        client=SimpleNamespace(
            images=recorder,
            content_generation=SimpleNamespace(tasks=recorder),
        ),
    )
    gateway = ArkProviderJobGateway(
        typed,
        resolve_asset_paths=lambda ids: (image_path,) * len(ids),
        extract_video_frames=lambda *_: (),
        prepare_segment_media=lambda *_: (image_path,) * 3,
        publish_segment_reference=SimpleNamespace(
            publish_asset=lambda *_: SimpleNamespace(
                url="https://example.com/context.mp4",
                publication_id=uuid.uuid4(),
            ),
        ),
    )
    gateway.submit(job_id=job.id, kind=job.kind, frozen_input=job.frozen_input)
    request = recorder.calls[0]
    actual = request["prompt"] if job.kind == "generate_image" else request["content"][0]["text"]
    assert actual == job.frozen_input["compiledProviderPrompt"]
    assert job.frozen_input["providerPromptVersion"] == 1
    return actual


@pytest.mark.parametrize("purpose", ["whole_video", "environment", "shot_frame", "shot_video"])
def test_preview_create_worker_and_sdk_are_identical(tmp_path, purpose):
    service, project_id, plan = _ready_storyboard()
    if purpose == "whole_video":
        preview = service.preview_video_generation(project_id)
        job = service.create_video_job(
            project_id,
            GenerationCommand(
                expectedInputHash=preview.input_hash,
                idempotencyKey="boundary-whole-video",
            ),
        )
        assert job.input_snapshot.compiled_provider_prompt == preview.compiled_provider_prompt
        assert job.input_snapshot.schema_version == 3
        expected = preview.compiled_provider_prompt
    elif purpose == "environment":
        preview = service.preview_asset_generation(
            project_id, AssetGenerationPreviewCommand(kind="environment")
        )
        job = service.create_asset_generation_job(
            project_id,
            AssetGenerationCommand(
                kind="environment",
                expectedInputHash=preview.input_hash,
                idempotencyKey="boundary-environment",
            ),
        )
        assert job.image_input_snapshot.compiled_provider_prompt == preview.compiled_provider_prompt
        assert job.image_input_snapshot.schema_version == 3
        expected = preview.compiled_provider_prompt
    else:
        target = ShotMediaPreviewCommand(
            shotPlanVersionId=plan.id, shotId=plan.shots[0].id, purpose=purpose
        )
        if purpose == "shot_video":
            context = service.shot_production_context(project_id, target)
            frame = service.register_asset(project_id, role="shot_frame", sha256="7" * 64)
            plan = service.confirm_shot_frame(
                project_id,
                ShotFrameConfirmCommand(
                    shotPlanVersionId=plan.id,
                    shotId=plan.shots[0].id,
                    assetId=frame.id,
                    expectedDesignHash=context["designHash"],
                    checks=[
                        "identity_scale",
                        "placement_state",
                        "movement_space",
                        "action_start",
                        "continuity",
                    ],
                ),
            )
            target = target.model_copy(update={"shot_plan_version_id": plan.id})
        preview = service.preview_shot_media(project_id, target)
        job = service.create_shot_media_job(
            project_id,
            ShotMediaCommand(
                **target.model_dump(),
                expectedInputHash=preview["inputHash"],
                idempotencyKey="boundary-shot-media",
            ),
        )
        expected = preview["compiledProviderPrompt"]
        assert '"childBlocking"' not in expected
    assert _submit_frozen_to_sdk(job, tmp_path) == expected


@pytest.mark.parametrize(
    ("mode", "replace_end"),
    [
        ("edit_existing", False),
        ("edit_existing", True),
        ("from_frame", False),
    ],
)
def test_prepared_repair_preview_create_worker_sdk_are_identical(tmp_path, mode, replace_end):
    service, project_id, video_id = _prepared_project()
    target = SegmentRepairPreviewCommand(
        baseVideoAssetId=video_id,
        issueRange={"startFrame": 96, "endFrame": 192},
        instruction="孩子将唯一木球放回篮子，保留其他内容",
        generationMode=mode,
        audioMode="generate_candidate",
        anchorStartFrame=96 if mode == "from_frame" else None,
        anchorEndFrame=191 if mode == "from_frame" else None,
        endStatePolicy="replace" if replace_end else "match_original",
        desiredEndState="木球留在篮子内" if replace_end else "",
    )
    original = service.preview_video_repair(project_id, target)
    prepared = service.prepare_segment_references(
        project_id, SegmentReferencePreparationCommand(**target.model_dump())
    )
    asset_ids = []
    roles = ["repair_anchor_in"] + ([] if replace_end else ["repair_anchor_out"])
    if mode == "edit_existing":
        roles.append("repair_context")
    for index, role in enumerate(roles, 1):
        asset = service.register_asset(
            project_id,
            role=role,
            producing_job_id=prepared.id,
            sha256=str(index) * 64,
            media_type="video" if role == "repair_context" else "image",
            metadata={
                "durationFrames": original.generation_range.duration_frames,
                "paddedTailFrames": 0,
            },
        )
        asset_ids.append(asset.id)
    service._repository._jobs[prepared.id] = prepared.model_copy(
        update={
            "status": "succeeded",
            "result_asset_ids": asset_ids,
        }
    )
    target = target.model_copy(update={"reference_preparation_job_id": prepared.id})
    preview = service.preview_video_repair(project_id, target)
    job = service.create_video_repair_job(
        project_id,
        SegmentRepairCreateCommand(
            **target.model_dump(),
            expectedInputHash=preview.input_hash,
            idempotencyKey="boundary-repair",
        ),
    )
    assert job.input_snapshot.schema_version == 3
    assert job.input_snapshot.compiled_provider_prompt == preview.compiled_provider_prompt
    assert _submit_frozen_to_sdk(job, tmp_path) == preview.compiled_provider_prompt
    if mode == "from_frame":
        assert "视频1" not in preview.compiled_provider_prompt
        assert "图2：提供已确认的目标结束画面" in preview.compiled_provider_prompt
    elif replace_end:
        assert "图2：提供儿童身份" in preview.compiled_provider_prompt
        assert "出点衔接状态" not in preview.compiled_provider_prompt


@pytest.mark.parametrize("contract", ["professional-director-v2", "professional-director-v3"])
def test_manual_director_repair_enforces_frozen_contract_with_actionable_http_error(contract):
    service, project_id, _ = _ready_storyboard()
    job = service.create_shot_plan_generation_job(
        project_id,
        ShotPlanGenerationCommand(
            idempotencyKey="manual-contract-test",
        ),
    )
    payload = _director_payload().model_dump(mode="json", by_alias=True)
    for shot in payload["shots"]:
        for field in ("cameraSpatialRelation", "interactionConstraints", "visualExclusions"):
            shot.pop(field)
    service._repository._jobs[job.id] = job.model_copy(
        update={
            "status": "failed",
            "provider_result": {"payload": payload},
            "frozen_input": {**job.frozen_input, "outputContractRevision": contract},
        }
    )
    app = create_app(
        service,
        settings=AppSettings(
            csrf_token="test-csrf",
            allowed_hosts=("testserver",),
            allowed_origins=("http://127.0.0.1:8877",),
            base_url="http://127.0.0.1:8877",
        ),
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/shot-plans/generations/{job.id}/materialize",
            json={"payload": payload, "idempotencyKey": "manual-contract-materialize"},
            headers={"Origin": "http://127.0.0.1:8877", "X-CatFlow-CSRF": "test-csrf"},
        )
    if contract.endswith("v3"):
        assert response.status_code == 422
        assert "shots.0.cameraSpatialRelation" in response.json()["detail"]
        assert len(service.list_shot_plans(project_id)) == 1
    else:
        assert response.status_code == 200
        assert response.json()["reviewStatus"] == "candidate"
