import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from catflow.application import service as dto
from catflow.application.job_execution import GenerationPrepared, ReplacementGenerationCommand
from catflow.application.job_replacement import replace_unknown_job
from catflow.infrastructure.database import (
    DatabaseSettings,
    create_database_engine,
    create_session_factory,
)
from catflow.infrastructure.models import JobRecord, VideoEditDraftRecord
from catflow.infrastructure.postgres_repository import PostgresStudioRepository
from test_video_edit_v2 import edit_fixture, ready_preparation


@pytest.fixture(params=["memory", "postgres"])
def flow(request):
    engine = (
        create_database_engine(DatabaseSettings.from_env()) if request.param == "postgres" else None
    )
    sessions = create_session_factory(engine) if engine else None
    service, repo, project, video, draft, target = edit_fixture(
        PostgresStudioRepository(sessions) if sessions else None
    )

    def save(job):
        if sessions:
            with sessions.begin() as session:
                session.execute(
                    update(JobRecord).where(JobRecord.id == job.id).values(status=job.status)
                )
        else:
            repo._jobs[job.id] = job

    class JobSink:
        def __setitem__(self, key, value):
            save(value)

    prep = ready_preparation(service, SimpleNamespace(_jobs=JobSink()), project, target)
    target = target.model_copy(update={"reference_preparation_job_id": prep.id})
    preview = service.preview_video_repair(project.id, target)
    command = dto.SegmentRepairCreateCommand(
        **target.model_dump(),
        expectedInputHash=preview.input_hash,
        idempotencyKey=str(uuid.uuid4()),
    )
    old = service.create_video_repair_job(project.id, command)
    save(old.model_copy(update={"status": "submission_unknown"}))
    yield SimpleNamespace(
        service=service,
        repo=repo,
        project=project,
        draft=draft,
        target=target,
        command=command,
        old=old,
        save=save,
        sessions=sessions,
    )
    if engine:
        engine.dispose()


def prepared(flow, job_id):
    with pytest.raises(GenerationPrepared) as caught:
        replace_unknown_job(flow.service, job_id)
    return ReplacementGenerationCommand(
        inputHash=caught.value.document["executionInputHash"],
        acknowledgeDuplicateCharge=True,
        idempotencyKey=str(uuid.uuid4()),
    )


def test_unknown_replacement_chain_and_historical_active_blocker(flow):
    f = flow
    with pytest.raises(dto.StudioVideoEditInProgressError) as caught:
        f.service.preview_video_repair(f.project.id, f.target)
    assert caught.value.detail["blockingJobId"] == str(f.old.id)
    with pytest.raises(dto.StudioVideoEditInProgressError):
        f.service.create_video_repair_job(
            f.project.id, f.command.model_copy(update={"idempotency_key": str(uuid.uuid4())})
        )
    command = prepared(f, f.old.id)
    successor = replace_unknown_job(f.service, f.old.id, command)
    assert successor.supersedes_job_id == f.old.id
    assert replace_unknown_job(f.service, f.old.id, command).id == successor.id
    repair = f.service.get_video_repair(successor.video_repair_id)
    assert f.repo.create_video_repair_job(repair, successor).id == successor.id
    f.save(successor.model_copy(update={"status": "submission_unknown"}))
    second = replace_unknown_job(f.service, successor.id, prepared(f, successor.id))
    f.save(second.model_copy(update={"status": "succeeded"}))
    assert f.service.preview_video_repair(f.project.id, f.target)
    f.save(f.old.model_copy(update={"status": "polling"}))
    with pytest.raises(dto.StudioVideoEditInProgressError):
        f.service.preview_video_repair(f.project.id, f.target)
    f.save(f.old.model_copy(update={"status": "submission_unknown"}))
    new = f.service.create_video_repair_job(
        f.project.id, f.command.model_copy(update={"idempotency_key": str(uuid.uuid4())})
    )
    assert new.supersedes_job_id is None


def test_competing_replacements_create_one_successor(flow):
    f = flow
    command = prepared(f, f.old.id)

    def submit(index):
        try:
            return replace_unknown_job(
                f.service,
                f.old.id,
                command.model_copy(update={"idempotency_key": str(uuid.uuid4())}),
            )
        except dto.StudioConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert len([item for item in results if item]) == 1
    assert len(f.service.get_job(f.old.id).successor_job_ids) == 1


@pytest.mark.parametrize(
    "field,value", [("acknowledgeDuplicateCharge", False), ("inputHash", "0" * 64)]
)
def test_repository_rejects_changed_replacement_consent(flow, monkeypatch, field, value):
    f = flow
    command = prepared(f, f.old.id)
    write = f.repo.create_video_repair_job

    def tampered_write(repair, job):
        frozen = {
            **job.frozen_input,
            "replacementConsent": {**job.frozen_input["replacementConsent"], field: value},
        }
        return write(repair, job.model_copy(update={"frozen_input": frozen}))

    monkeypatch.setattr(f.repo, "create_video_repair_job", tampered_write)
    with pytest.raises(dto.StudioConflictError):
        replace_unknown_job(f.service, f.old.id, command)
    assert not f.service.get_job(f.old.id).successor_job_ids


@pytest.mark.parametrize("race", ["status", "draft"])
def test_transaction_rechecks_status_and_draft(flow, monkeypatch, race):
    f = flow
    command = prepared(f, f.old.id)
    write = f.repo.create_video_repair_job

    def racing_write(repair, job):
        if race == "status":
            f.save(f.old.model_copy(update={"status": "polling"}))
        elif f.sessions:
            with f.sessions.begin() as session:
                session.get(VideoEditDraftRecord, f.draft.id).head_edit_version_id = None
        else:
            f.repo._edit_drafts[f.draft.id] = f.draft.model_copy(
                update={"head_edit_version_id": None}
            )
        return write(repair, job)

    monkeypatch.setattr(f.repo, "create_video_repair_job", racing_write)
    with pytest.raises(dto.StudioConflictError):
        replace_unknown_job(f.service, f.old.id, command)
    assert not f.service.get_job(f.old.id).successor_job_ids


def test_preview_rejects_unrelated_blocker(flow):
    f = flow
    other = f.old.model_copy(
        update={"id": uuid.uuid4(), "idempotency_key": str(uuid.uuid4()), "status": "polling"}
    )
    # Create the second submission while the original is terminal, then model
    # reconciliation revealing that the original submission is still unknown.
    f.save(f.old.model_copy(update={"status": "failed"}))
    f.repo.create_job(other)
    f.save(f.old.model_copy(update={"status": "submission_unknown"}))
    with pytest.raises(dto.StudioVideoEditInProgressError) as caught:
        replace_unknown_job(f.service, f.old.id)
    assert caught.value.detail["blockingJobId"] == str(other.id)


def test_http_conflict_details_and_openapi(flow):
    from fastapi.testclient import TestClient

    from catflow.interfaces.api import AppSettings, create_app

    f = flow
    app = create_app(f.service, settings=AppSettings(csrf_token="test"))
    with TestClient(app, base_url="http://127.0.0.1:8877") as client:
        response = client.post(
            f"/api/v1/projects/{f.project.id}/video-edits/preview",
            json=f.target.model_dump(mode="json", by_alias=True),
            headers={"origin": "http://127.0.0.1:8877", "x-catflow-csrf": "test"},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "video_edit_in_progress",
        "message": "已有局部修改任务尚未结束，请先查看该任务。",
        "blockingJobId": str(f.old.id),
        "editDraftId": str(f.draft.id),
        "videoRepairId": str(f.old.video_repair_id),
    }
    assert "VideoEditInProgressDetail" in app.openapi()["components"]["schemas"]
