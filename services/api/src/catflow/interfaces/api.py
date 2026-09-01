from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from catflow.application.service import (
    AssetDto,
    AssetGenerationCommand,
    AssetGenerationPreviewCommand,
    AssetGenerationPreviewDto,
    EditCreateCommand,
    EditVersionDto,
    ExportCommand,
    FinalSelectionCommand,
    GenerationCommand,
    GenerationPreviewDto,
    ImageDiagnosisCommand,
    JobDto,
    PlannerMessageCommand,
    PlannerSnapshotDto,
    ProjectCreate,
    ProjectDto,
    ProjectPatch,
    ProjectSelectionDto,
    ShotPlanVersionDto,
    StoryCreateCommand,
    StoryVersionDto,
    StudioConflictError,
    StudioNotFoundError,
    StudioService,
)
from catflow.domain.contract import ContractModel
from catflow.domain.models import ShotPlanDraft
from catflow.infrastructure.database import canon_v4_document, canon_v4_hash
from catflow.infrastructure.media import InvalidMediaError, LocalMediaStore


@dataclass(frozen=True, slots=True)
class AppSettings:
    csrf_token: str
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8765",
        "http://localhost:8765",
    )


class SelectionCommand(ContractModel):
    slot: str = Field(min_length=1, max_length=32)
    asset_id: uuid.UUID = Field(alias="assetId")


class PreviewCommand(ContractModel):
    maximum_references: int = Field(alias="maximumReferences", default=4, ge=0, le=20)


def create_app(
    service: StudioService,
    *,
    settings: AppSettings,
    media_store: LocalMediaStore | None = None,
    spa_dist: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="CatFlow Studio API", version="0.1.0")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.middleware("http")
    async def protect_local_writes(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith(
            "/api/"
        ):
            origin = request.headers.get("origin")
            if origin not in settings.allowed_origins:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "same-origin request required"},
                )
            if request.headers.get("x-catflow-csrf") != settings.csrf_token:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "invalid csrf token"},
                )
            content_type = request.headers.get("content-type", "")
            is_upload = request.url.path.endswith("/assets/upload")
            accepted = (
                content_type.startswith("multipart/form-data")
                if is_upload
                else content_type.startswith("application/json")
            )
            if not accepted:
                return JSONResponse(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    content={
                        "detail": "multipart/form-data required"
                        if is_upload
                        else "application/json required"
                    },
                )
        return await call_next(request)

    @app.exception_handler(StudioConflictError)
    async def handle_conflict(_request: Request, exc: StudioConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(StudioNotFoundError)
    async def handle_not_found(_request: Request, exc: StudioNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(InvalidMediaError)
    async def handle_invalid_media(_request: Request, exc: InvalidMediaError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/runtime/bootstrap")
    def runtime_bootstrap() -> dict[str, object]:
        return {
            "csrfToken": settings.csrf_token,
            "baseUrl": "http://127.0.0.1:8765",
            "localOnly": True,
        }

    @app.get("/api/v1/runtime/settings")
    def runtime_settings() -> dict[str, object]:
        return {
            "provider": "fake",
            "planningModel": "catflow-fake-planner-v1",
            "videoModel": "catflow-fake-video-v1",
            "paidCallsEnabled": False,
        }

    @app.put("/api/v1/runtime/settings")
    def update_runtime_settings(payload: dict[str, Any]) -> dict[str, Any]:
        if payload:
            raise HTTPException(status_code=422, detail="runtime settings are environment-owned")
        return runtime_settings()

    @app.get("/api/v1/canon/current")
    def current_canon() -> dict[str, object]:
        return {
            "id": str(service.current_canon_profile_id()),
            "version": 4,
            "profileHash": canon_v4_hash(),
            "profile": canon_v4_document(),
        }

    @app.get("/api/v1/projects", response_model=list[ProjectDto])
    def list_projects() -> list[ProjectDto]:
        return service.list_projects()

    @app.post("/api/v1/projects", response_model=ProjectDto, status_code=status.HTTP_201_CREATED)
    def create_project(draft: ProjectCreate) -> ProjectDto:
        return service.create_project(draft)

    @app.get("/api/v1/projects/{project_id}", response_model=ProjectDto)
    def get_project(project_id: uuid.UUID) -> ProjectDto:
        project = service.get_project(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project

    @app.patch("/api/v1/projects/{project_id}", response_model=ProjectDto)
    def patch_project(project_id: uuid.UUID, patch: ProjectPatch) -> ProjectDto:
        return service.update_project(project_id, patch)

    @app.get("/api/v1/projects/{project_id}/workspace")
    def workspace(project_id: uuid.UUID) -> dict[str, Any]:
        return service.workspace(project_id)

    @app.get("/api/v1/projects/{project_id}/planner", response_model=PlannerSnapshotDto)
    def planner(project_id: uuid.UUID) -> PlannerSnapshotDto:
        return service.get_planner(project_id)

    @app.post(
        "/api/v1/projects/{project_id}/planner/messages",
        response_model=JobDto,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def planner_message(project_id: uuid.UUID, command: PlannerMessageCommand) -> JobDto:
        return service.enqueue_planner_message(project_id, command)

    @app.post(
        "/api/v1/projects/{project_id}/planner/proposals/{proposal_id}/adopt",
        response_model=StoryVersionDto,
        status_code=status.HTTP_201_CREATED,
    )
    def adopt_proposal(
        project_id: uuid.UUID, proposal_id: uuid.UUID, _payload: dict[str, Any] = Body(default={})
    ) -> StoryVersionDto:
        return service.adopt_proposal(project_id, proposal_id)

    @app.get("/api/v1/projects/{project_id}/stories", response_model=list[StoryVersionDto])
    def stories(project_id: uuid.UUID) -> list[StoryVersionDto]:
        return service.list_stories(project_id)

    @app.post(
        "/api/v1/projects/{project_id}/stories",
        response_model=StoryVersionDto,
        status_code=status.HTTP_201_CREATED,
    )
    def create_story(project_id: uuid.UUID, command: StoryCreateCommand) -> StoryVersionDto:
        return service.create_story(project_id, command)

    @app.post(
        "/api/v1/projects/{project_id}/stories/{story_version_id}/activate",
        response_model=StoryVersionDto,
    )
    def activate_story(
        project_id: uuid.UUID,
        story_version_id: uuid.UUID,
        _payload: dict[str, Any] = Body(default={}),
    ) -> StoryVersionDto:
        return service.activate_story(project_id, story_version_id)

    @app.get("/api/v1/projects/{project_id}/shot-plans", response_model=list[ShotPlanVersionDto])
    def shot_plans(project_id: uuid.UUID) -> list[ShotPlanVersionDto]:
        return service.list_shot_plans(project_id)

    @app.post(
        "/api/v1/projects/{project_id}/shot-plans",
        response_model=ShotPlanVersionDto,
        status_code=status.HTTP_201_CREATED,
    )
    def create_shot_plan(project_id: uuid.UUID, draft: ShotPlanDraft) -> ShotPlanVersionDto:
        return service.create_shot_plan(project_id, draft)

    @app.post(
        "/api/v1/projects/{project_id}/shot-plans/{shot_plan_version_id}/activate",
        response_model=ShotPlanVersionDto,
    )
    def activate_shot_plan(
        project_id: uuid.UUID,
        shot_plan_version_id: uuid.UUID,
        _payload: dict[str, Any] = Body(default={}),
    ) -> ShotPlanVersionDto:
        return service.activate_shot_plan(project_id, shot_plan_version_id)

    @app.get("/api/v1/projects/{project_id}/assets", response_model=list[AssetDto])
    def assets(project_id: uuid.UUID) -> list[AssetDto]:
        return service.list_assets(project_id)

    @app.get("/api/v1/assets/{asset_id}", response_model=AssetDto)
    def asset(asset_id: uuid.UUID) -> AssetDto:
        return service.get_asset(asset_id)

    @app.post(
        "/api/v1/projects/{project_id}/assets/{asset_id}/diagnose",
        response_model=JobDto,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def diagnose_asset(
        project_id: uuid.UUID,
        asset_id: uuid.UUID,
        command: ImageDiagnosisCommand,
    ) -> JobDto:
        if command.asset_id != asset_id:
            raise HTTPException(status_code=422, detail="assetId must match the route")
        return service.create_image_diagnosis_job(project_id, command)

    @app.post(
        "/api/v1/projects/{project_id}/assets/upload",
        response_model=AssetDto,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_asset(
        project_id: uuid.UUID,
        role: str,
        file: UploadFile = File(...),
    ) -> AssetDto:
        if media_store is None:
            raise HTTPException(status_code=503, detail="media storage unavailable")
        payload = await file.read(20 * 1024 * 1024 + 1)
        if len(payload) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="media file exceeds 20 MiB")
        stored = media_store.save_upload(
            payload,
            filename=file.filename or "upload",
            declared_content_type=file.content_type or "application/octet-stream",
            role=role,
        )
        return service.register_asset(
            project_id,
            role=role,
            sha256=stored.sha256,
            media_type=stored.media_type,
            storage_key=stored.storage_key,
            byte_size=stored.byte_size,
        )

    @app.post(
        "/api/v1/projects/{project_id}/selections",
        response_model=ProjectSelectionDto,
        status_code=status.HTTP_201_CREATED,
    )
    def select_asset(project_id: uuid.UUID, command: SelectionCommand) -> ProjectSelectionDto:
        return service.select_asset(project_id, slot=command.slot, asset_id=command.asset_id)

    @app.post(
        "/api/v1/projects/{project_id}/asset-generations/preview",
        response_model=AssetGenerationPreviewDto,
    )
    def preview_asset_generation(
        project_id: uuid.UUID, command: AssetGenerationPreviewCommand
    ) -> AssetGenerationPreviewDto:
        return service.preview_asset_generation(project_id, command)

    @app.post(
        "/api/v1/projects/{project_id}/asset-generations",
        response_model=JobDto,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_asset_generation(project_id: uuid.UUID, command: AssetGenerationCommand) -> JobDto:
        return service.create_asset_generation_job(project_id, command)

    @app.post(
        "/api/v1/projects/{project_id}/video-generations/preview",
        response_model=GenerationPreviewDto,
    )
    def preview_video(project_id: uuid.UUID, command: PreviewCommand) -> GenerationPreviewDto:
        return service.preview_video_generation(
            project_id, maximum_references=command.maximum_references
        )

    @app.post(
        "/api/v1/projects/{project_id}/video-generations",
        response_model=JobDto,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_video_job(project_id: uuid.UUID, command: GenerationCommand) -> JobDto:
        return service.create_video_job(project_id, command)

    @app.get("/api/v1/jobs/{job_id}", response_model=JobDto)
    def job(job_id: uuid.UUID) -> JobDto:
        return service.get_job(job_id)

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobDto)
    def cancel_job(job_id: uuid.UUID, _payload: dict[str, Any] = Body(default={})) -> JobDto:
        return service.cancel_job(job_id)

    @app.get("/api/v1/events")
    async def events(request: Request, afterEventId: int = 0) -> StreamingResponse:  # noqa: N803
        async def stream():  # type: ignore[no-untyped-def]
            cursor = afterEventId
            yield "event: connected\ndata: {}\n\n"
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(service.list_job_events, after_event_id=cursor)
                if batch:
                    for item in batch:
                        cursor = item.id
                        payload = {
                            "jobId": str(item.job_id),
                            "projectId": str(item.project_id),
                            "eventType": item.event_type,
                            "payload": item.payload,
                        }
                        yield (
                            f"id: {item.id}\nevent: {item.event_type}\n"
                            f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                        )
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/v1/projects/{project_id}/edits", response_model=list[EditVersionDto])
    def edits(project_id: uuid.UUID) -> list[EditVersionDto]:
        return service.list_edits(project_id)

    @app.post(
        "/api/v1/projects/{project_id}/edits",
        response_model=EditVersionDto,
        status_code=status.HTTP_201_CREATED,
    )
    def create_edit(project_id: uuid.UUID, command: EditCreateCommand) -> EditVersionDto:
        return service.create_edit(project_id, command)

    @app.post(
        "/api/v1/projects/{project_id}/exports",
        response_model=JobDto,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_export(project_id: uuid.UUID, command: ExportCommand) -> JobDto:
        return service.create_export_job(project_id, command)

    @app.post(
        "/api/v1/projects/{project_id}/final-selection",
        response_model=ProjectSelectionDto,
    )
    def final_selection(
        project_id: uuid.UUID, command: FinalSelectionCommand
    ) -> ProjectSelectionDto:
        return service.approve_final(project_id, command)

    @app.get("/api/v1/assets/{asset_id}/content")
    def asset_content(asset_id: uuid.UUID) -> FileResponse:
        if media_store is None:
            raise HTTPException(status_code=503, detail="media storage unavailable")
        asset = service.get_asset(asset_id)
        path = media_store.resolve(asset.storage_key)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="asset content not found")
        media_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".mp4": "video/mp4",
        }.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type, filename=path.name)

    if spa_dist is not None:
        index_path = spa_dist / "index.html"
        if not index_path.is_file():
            raise ValueError(f"SPA index is missing: {index_path}")
        asset_directory = spa_dist / "assets"
        if asset_directory.is_dir():
            app.mount("/assets", StaticFiles(directory=asset_directory), name="spa-assets")

        @app.get("/{spa_path:path}", include_in_schema=False)
        def spa_fallback(spa_path: str) -> FileResponse:
            if spa_path == "api" or spa_path.startswith(("api/", "assets/", "media/")):
                raise HTTPException(status_code=404, detail="not found")
            return FileResponse(index_path, media_type="text/html")

    return app
