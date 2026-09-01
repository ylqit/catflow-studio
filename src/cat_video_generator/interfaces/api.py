"""FastAPI surface for the V5 scene and video-clip creation studio."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..application.shot_queue import GatewayUnavailableError, RevisionConflictError
from ..domain.contracts import (
    CURRENT_CONTRACT_VERSION,
    AcceptedVisualAssetPlan,
    ReferenceRole,
    ReferenceTarget,
    ReferenceUsage,
    VisualAssetPurpose,
)
from ..domain.rendering import SequenceStatus
from ..domain.workflow import StepStatus
from ..infrastructure.ark.runtime import (
    RuntimeConfigurationConflictError,
    RuntimeConfigurationFileError,
)
from ..infrastructure.db.repositories import WorkflowConflictError
from ..infrastructure.db.session import ALEMBIC_HEAD
from .api_schemas import (
    AcceptShotAssistanceRequest,
    AcceptStoryDiagnosisRequest,
    AcceptStoryExpansionRequest,
    AcceptStoryRewriteRequest,
    AcceptSuggestionsRequest,
    AcceptVisualAssetPlanRequest,
    AssistShotRequest,
    BuildSequenceRequest,
    CancelTaskRequest,
    CreateProjectRequest,
    DiagnoseStoryRequest,
    ExpandStoryRequest,
    GenerateReferenceImageRequest,
    GenerateRequest,
    GenerateSceneLookRequest,
    OrderRequest,
    PlanVisualAssetsRequest,
    PreviewReferenceImageRequest,
    RangeEditRequest,
    ReconcileRequest,
    ReferencesRequest,
    ReviewRequest,
    ReviseVisualAssetPlanRequest,
    RewriteStoryRequest,
    SaveAnchorBriefRequest,
    SaveSceneLookDraftRequest,
    SceneRequest,
    SelectSceneLookRequest,
    SelectSequenceRequest,
    ShotRequest,
    SuggestShotsRequest,
    UpdateProjectRequest,
    UpdateRuntimeSettingsRequest,
    VisualProfileRequest,
)
from .api_v2 import install_canvas_v2_routes
from .http_headers import parse_version_header
from .jobs import JobConflictError, JobRegistry
from .production_recipes_api import install_production_recipe_routes
from .sse import parse_event_cursor, stream_events

if TYPE_CHECKING:
    from ..bootstrap import RuntimeContainer


API_FEATURES = (
    "manual_video_edit_boundaries",
    "reference_media_video_generation",
    "storyboard_production_confirmations",
    "visual_asset_plan_manual_revisions",
    "workflow_task_cancellation_v1",
    "legacy_director_workflow_adoption_v1",
)


class _SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            normalized = path.replace("\\", "/").lstrip("/")
            if (
                exc.status_code != 404
                or scope["method"] not in {"GET", "HEAD"}
                or normalized.startswith("api/")
                or Path(normalized).suffix
            ):
                raise
            return await super().get_response("index.html", scope)


def create_app(
    container: RuntimeContainer,
    *,
    job_registry: JobRegistry,
    static_dir: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Cat Video Shot Queue", version="5.0.0", redoc_url=None)
    server_started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    repository = container.repository
    roots = tuple(
        item.expanduser().resolve()
        for item in (
            container.runtime_settings.work_root,
            container.runtime_settings.asset_root,
        )
    )

    @app.exception_handler(LookupError)
    async def not_found(_request: Request, exc: LookupError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_request(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(WorkflowConflictError)
    async def workflow_conflict(_request: Request, exc: WorkflowConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RevisionConflictError)
    async def revision_conflict(_request: Request, exc: RevisionConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(GatewayUnavailableError)
    async def gateway_unavailable(_request: Request, exc: GatewayUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(RuntimeConfigurationConflictError)
    async def runtime_configuration_conflict(
        _request: Request,
        exc: RuntimeConfigurationConflictError,
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(RuntimeConfigurationFileError)
    async def runtime_configuration_file_error(
        _request: Request,
        exc: RuntimeConfigurationFileError,
    ) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    def runtime_revision(request: Request) -> int | None:
        raw = request.headers.get("X-CVG-Runtime-Config-Revision")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError("X-CVG-Runtime-Config-Revision 必须是非负整数") from exc

    def paid_submit(
        request: Request,
        *,
        kind: str,
        key: str,
        fn: Callable[[], Any],
        context: dict[str, Any],
        execution: Any | None = None,
    ) -> dict[str, Any]:
        execution = execution or container.runtime_configuration.capture(runtime_revision(request))
        paid_context = {
            **context,
            "runtimeConfigRevision": execution.config.revision,
            "model": {
                "planning": execution.config.planning_model,
                "image": execution.config.image_model,
                "video": execution.config.video_model,
                "review": execution.config.review_model,
            },
        }
        return _submit(
            job_registry,
            kind=kind,
            key=f"{key}:runtime:{execution.config.revision}",
            fn=lambda: container.runtime_configuration.run(execution, fn),
            context=paid_context,
        )

    def runtime_settings_document() -> dict[str, Any]:
        return {
            **container.runtime_configuration.api_document(),
            "databaseReady": container.alembic_revision == ALEMBIC_HEAD,
        }

    @app.get("/api/v1/runtime-settings")
    def runtime_settings() -> dict[str, Any]:
        return runtime_settings_document()

    @app.put("/api/v1/runtime-settings")
    def update_runtime_settings(payload: UpdateRuntimeSettingsRequest) -> dict[str, Any]:
        container.runtime_configuration.save(
            payload.expected_revision,
            {
                "planningModel": payload.planning_model,
                "imageModel": payload.image_model,
                "videoModel": payload.video_model,
                "reviewModel": payload.review_model,
                "videoResolution": payload.video_resolution,
                "semanticReviewEnabled": payload.semantic_review_enabled,
            },
        )
        return runtime_settings_document()

    @app.delete("/api/v1/runtime-settings/override")
    def restore_runtime_settings(request: Request) -> dict[str, Any]:
        revision = runtime_revision(request)
        if revision is None:
            raise RuntimeConfigurationConflictError(
                "恢复部署默认需要 X-CVG-Runtime-Config-Revision"
            )
        container.runtime_configuration.restore_defaults(revision)
        return runtime_settings_document()

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        database_ready = container.alembic_revision == ALEMBIC_HEAD
        return {
            "ready": database_ready,
            "databaseReady": database_ready,
            "applicationVersion": __version__,
            "serverStartedAt": server_started_at,
            "apiFeatures": list(API_FEATURES),
            "contractVersion": CURRENT_CONTRACT_VERSION,
            "alembicRevision": container.alembic_revision,
            "expectedAlembicRevision": ALEMBIC_HEAD,
            **container.runtime_configuration.report(),
        }

    @app.get("/api/v1/projects")
    def list_projects() -> list[dict[str, Any]]:
        return [
            {
                "id": str(item.id),
                "title": item.title,
                "contentDate": item.content_date.isoformat(),
                "status": item.status.value,
            }
            for item in repository.list_projects()
        ]

    @app.post("/api/v1/projects")
    def create_project(payload: CreateProjectRequest) -> dict[str, Any]:
        return container.editing.create_project(
            payload.project,
            content_date=payload.content_date,
        )

    @app.get("/api/v1/projects/{project_id}")
    def project_graph(project_id: uuid.UUID) -> dict[str, Any]:
        return repository.project_graph(project_id)

    @app.get("/api/v1/projects/{project_id}/production-board")
    def production_board(project_id: uuid.UUID) -> dict[str, Any]:
        return container.production.production_board(project_id)
    @app.patch("/api/v1/projects/{project_id}")
    def update_project(
        project_id: uuid.UUID,
        payload: UpdateProjectRequest,
    ) -> dict[str, Any]:
        project = repository.update_project(
            project_id,
            title=payload.title,
            content_date=payload.content_date,
        )
        return {
            "id": str(project.id),
            "title": project.title,
            "contentDate": project.content_date.isoformat(),
            "status": project.status.value,
        }

    @app.put("/api/v1/projects/{project_id}/default-references")
    def update_project_default_references(
        project_id: uuid.UUID,
        payload: ReferencesRequest,
    ) -> dict[str, Any]:
        project = repository.update_project_default_references(project_id, payload.references)
        return {
            "projectId": str(project.id),
            "defaultReferenceBindings": [
                item.model_dump(mode="json", by_alias=True)
                for item in project.default_reference_bindings
            ],
        }

    @app.get("/api/v1/projects/{project_id}/visual-profile")
    def get_visual_profile(project_id: uuid.UUID) -> dict[str, Any]:
        return {
            **_visual_profile_json(repository.get_visual_profile(project_id)),
            "canonDefaults": repository.get_default_visual_profile(project_id).model_dump(
                mode="json",
                by_alias=True,
            ),
        }

    @app.put("/api/v1/projects/{project_id}/visual-profile")
    def update_visual_profile(
        project_id: uuid.UUID,
        payload: VisualProfileRequest,
    ) -> dict[str, Any]:
        return _visual_profile_json(repository.save_visual_profile(project_id, payload))

    @app.post("/api/v1/projects/{project_id}/restore-canon-references")
    def restore_project_canon_references(project_id: uuid.UUID) -> dict[str, Any]:
        return container.editing.restore_project_canon_references(project_id)

    @app.post("/api/v1/projects/{project_id}/scenes")
    def add_scene(project_id: uuid.UUID, payload: SceneRequest) -> dict[str, Any]:
        return _scene_json(repository.add_scene(project_id, payload))

    @app.patch("/api/v1/scenes/{scene_id}")
    def update_scene(scene_id: uuid.UUID, payload: SceneRequest) -> dict[str, Any]:
        return _scene_json(repository.update_scene(scene_id, payload))

    @app.delete("/api/v1/scenes/{scene_id}", status_code=204)
    def delete_scene(scene_id: uuid.UUID) -> None:
        repository.delete_scene(scene_id)

    @app.put("/api/v1/projects/{project_id}/scene-order")
    def reorder_scenes(project_id: uuid.UUID, payload: OrderRequest) -> dict[str, bool]:
        repository.reorder_scenes(project_id, tuple(payload.ids))
        return {"saved": True}

    @app.get("/api/v1/scenes/{scene_id}/creative-workflow")
    def creative_workflow(scene_id: uuid.UUID) -> dict[str, Any]:
        return container.editing.creative_workflow(scene_id)

    @app.post("/api/v1/scenes/{scene_id}/story-expansions")
    def expand_story(
        scene_id: uuid.UUID,
        payload: ExpandStoryRequest,
        request: Request,
    ) -> dict[str, Any]:
        return paid_submit(
            request,
            kind="story_expansion",
            key=f"scene:{scene_id}:story-expansion",
            fn=lambda: _story_expansion_json(
                container.editing.expand_story(
                    scene_id,
                    allow_paid_generation=payload.allow_paid_generation,
                    storyboard_revision_id=payload.storyboard_revision_id,
                    structure_hash=payload.structure_hash,
                    generation_plan_id=payload.generation_plan_id,
                    generation_plan_hash=payload.generation_plan_hash,
                )
            ),
            context={
                "sceneId": scene_id,
                "operationKey": "director:story-expansion",
            },
        )

    @app.post("/api/v1/steps/{step_id}/accept-story-expansion")
    def accept_story_expansion(
        step_id: uuid.UUID,
        payload: AcceptStoryExpansionRequest,
    ) -> dict[str, Any]:
        return _scene_json(
            container.editing.accept_story_expansion(
                step_id,
                expansion=payload.expansion,
            )
        )

    @app.post("/api/v1/scenes/{scene_id}/story-diagnoses")
    def diagnose_story(
        scene_id: uuid.UUID,
        payload: DiagnoseStoryRequest,
        request: Request,
    ) -> dict[str, Any]:
        submitted = paid_submit(
            request,
            kind="story_diagnosis",
            key=f"scene:{scene_id}:story-diagnosis",
            fn=lambda: _story_diagnosis_json(
                container.editing.diagnose_story(
                    scene_id,
                    allow_paid_generation=payload.allow_paid_generation,
                )
            ),
            context={
                "sceneId": scene_id,
                "operationKey": "director:story-diagnosis",
            },
        )
        return submitted

    @app.post("/api/v1/steps/{step_id}/accept-story-diagnosis")
    def accept_story_diagnosis(
        step_id: uuid.UUID,
        payload: AcceptStoryDiagnosisRequest,
    ) -> dict[str, Any]:
        step = container.editing.accept_story_diagnosis(
            step_id,
            diagnosis=payload.diagnosis,
            selected_strategy=payload.selected_strategy,
            additional_instructions=payload.additional_instructions,
            preserve_original=payload.preserve_original,
        )
        return _creative_step_json(step)

    @app.post("/api/v1/scenes/{scene_id}/story-rewrites")
    def rewrite_story(
        scene_id: uuid.UUID,
        payload: RewriteStoryRequest,
        request: Request,
    ) -> dict[str, Any]:
        submitted = paid_submit(
            request,
            kind="story_rewrite",
            key=f"scene:{scene_id}:story-rewrite:{payload.diagnosis_step_id}",
            fn=lambda: _story_rewrite_json(
                container.editing.rewrite_story(
                    scene_id,
                    diagnosis_step_id=payload.diagnosis_step_id,
                    allow_paid_generation=payload.allow_paid_generation,
                )
            ),
            context={
                "sceneId": scene_id,
                "operationKey": "director:story-rewrite",
            },
        )
        return submitted

    @app.post("/api/v1/steps/{step_id}/accept-story-rewrite")
    def accept_story_rewrite(
        step_id: uuid.UUID,
        payload: AcceptStoryRewriteRequest,
    ) -> dict[str, Any]:
        return _scene_json(
            container.editing.accept_story_rewrite(
                step_id,
                rewrite=payload.rewrite,
            )
        )

    @app.post("/api/v1/scenes/{scene_id}/shot-suggestions")
    def suggest_shots(
        scene_id: uuid.UUID,
        payload: SuggestShotsRequest,
        request: Request,
    ) -> dict[str, Any]:
        return paid_submit(
            request,
            kind="shot_suggestions",
            key=f"scene:{scene_id}:suggestions",
            fn=lambda: _suggestion_json(
                container.editing.suggest_shots(
                    scene_id,
                    allow_paid_generation=payload.allow_paid_generation,
                )
            ),
            context={"sceneId": scene_id, "operationKey": "director:shot-suggestions"},
        )

    @app.post("/api/v1/steps/{step_id}/accept-suggestions")
    def accept_suggestions(
        step_id: uuid.UUID,
        payload: AcceptSuggestionsRequest,
    ) -> list[dict[str, Any]]:
        return [
            _shot_json(item)
            for item in container.editing.accept_suggestions(
                step_id,
                look_plan=payload.look_plan,
                shots=tuple(payload.shots),
                apply_mode=payload.apply_mode,
                source_shot_revisions=payload.source_shot_revisions,
            )
        ]

    @app.post("/api/v1/scenes/{scene_id}/visual-asset-plans")
    def plan_visual_assets(
        scene_id: uuid.UUID,
        payload: PlanVisualAssetsRequest,
        request: Request,
    ) -> dict[str, Any]:
        return paid_submit(
            request,
            kind="visual_asset_plan",
            key=f"scene:{scene_id}:visual-asset-plan",
            fn=lambda: _visual_asset_plan_json(
                container.editing.plan_visual_assets(
                    scene_id,
                    allow_paid_generation=payload.allow_paid_generation,
                    storyboard_revision_id=payload.storyboard_revision_id,
                    structure_hash=payload.structure_hash,
                    generation_plan_id=payload.generation_plan_id,
                    generation_plan_hash=payload.generation_plan_hash,
                )
            ),
            context={
                "sceneId": scene_id,
                "operationKey": "director:visual-asset-plan",
            },
        )

    @app.post("/api/v1/steps/{step_id}/accept-visual-asset-plan")
    def accept_visual_asset_plan(
        step_id: uuid.UUID,
        payload: AcceptVisualAssetPlanRequest,
    ) -> dict[str, Any]:
        return _creative_step_json(
            container.editing.accept_visual_asset_plan(
                step_id,
                plan=payload.plan,
            )
        )

    @app.post("/api/v1/scenes/{scene_id}/shots")
    def add_shot(scene_id: uuid.UUID, payload: ShotRequest) -> dict[str, Any]:
        return _shot_json(repository.add_shot(scene_id, payload))

    @app.patch("/api/v1/shots/{shot_id}")
    def update_shot(shot_id: uuid.UUID, payload: ShotRequest) -> dict[str, Any]:
        return _shot_json(repository.update_shot(shot_id, payload))

    @app.get("/api/v1/shots/{shot_id}/assist-context")
    def shot_assist_context(shot_id: uuid.UUID) -> dict[str, Any]:
        return container.editing.shot_assist_context(shot_id)

    @app.post("/api/v1/shots/{shot_id}/assist")
    def assist_shot(
        shot_id: uuid.UUID,
        payload: AssistShotRequest,
        request: Request,
    ) -> dict[str, Any]:
        return paid_submit(
            request,
            kind="shot_assistance",
            key=f"shot:{shot_id}:assist:{payload.source_draft_revision}",
            fn=lambda: _shot_assistance_json(
                container.editing.assist_shot(
                    shot_id,
                    source_draft_revision=payload.source_draft_revision,
                    candidate_asset_ids=tuple(payload.candidate_asset_ids),
                    allow_paid_generation=payload.allow_paid_generation,
                )
            ),
            context={
                "shotId": shot_id,
                "operationKey": "director:shot-assistance",
                "sourceDraftRevision": payload.source_draft_revision,
            },
        )

    @app.get("/api/v1/shots/{shot_id}/assist-analyses")
    def shot_assist_analyses(shot_id: uuid.UUID) -> list[dict[str, Any]]:
        return container.editing.list_shot_assistance(shot_id)

    @app.get("/api/v1/shots/{shot_id}/previous-tail")
    def previous_tail(shot_id: uuid.UUID) -> dict[str, Any]:
        return container.production.tail_frame_status(shot_id)

    @app.post("/api/v1/shots/{shot_id}/adopt-previous-tail-anchor")
    def adopt_previous_tail_anchor(shot_id: uuid.UUID) -> dict[str, Any]:
        shot = container.production.adopt_previous_tail_anchor(shot_id)
        return {
            **_shot_json(shot),
            "previousTail": container.production.tail_frame_status(shot_id),
        }

    @app.post("/api/v1/steps/{step_id}/accept-shot-assistance")
    def accept_shot_assistance(
        step_id: uuid.UUID,
        payload: AcceptShotAssistanceRequest,
    ) -> dict[str, Any]:
        return _shot_json(
            container.editing.accept_shot_assistance(
                step_id,
                source_draft_revision=payload.source_draft_revision,
                patch=payload.patch,
                accepted_anchor_brief=payload.accepted_anchor_brief,
            )
        )

    @app.post("/api/v1/shots/{shot_id}/anchor-briefs")
    def save_anchor_brief(
        shot_id: uuid.UUID,
        payload: SaveAnchorBriefRequest,
    ) -> dict[str, Any]:
        container.editing.save_anchor_brief(
            shot_id,
            source_draft_revision=payload.source_draft_revision,
            brief=payload.brief,
        )
        return container.production.generation_workspace(shot_id)

    @app.delete("/api/v1/shots/{shot_id}", status_code=204)
    def delete_shot(shot_id: uuid.UUID) -> None:
        repository.delete_shot(shot_id)

    @app.put("/api/v1/scenes/{scene_id}/shot-order")
    def reorder_shots(scene_id: uuid.UUID, payload: OrderRequest) -> dict[str, bool]:
        repository.reorder_shots(scene_id, tuple(payload.ids))
        return {"saved": True}

    @app.put("/api/v1/scenes/{scene_id}/look-asset")
    def select_scene_look_asset(
        scene_id: uuid.UUID,
        payload: SelectSceneLookRequest,
    ) -> dict[str, Any]:
        return _scene_json(repository.select_scene_look_asset(scene_id, payload.asset_id))

    @app.get("/api/v1/scenes/{scene_id}/look-draft")
    def get_scene_look_draft(scene_id: uuid.UUID) -> dict[str, Any]:
        return container.production.get_scene_look_draft(scene_id)

    @app.put("/api/v1/scenes/{scene_id}/look-draft")
    def save_scene_look_draft(
        scene_id: uuid.UUID,
        payload: SaveSceneLookDraftRequest,
    ) -> dict[str, Any]:
        return container.production.save_scene_look_draft(
            scene_id,
            expected_revision=payload.expected_revision,
            draft=payload.draft,
        )

    @app.post("/api/v1/scenes/{scene_id}/look-prompt-preview")
    def preview_scene_look_prompt(scene_id: uuid.UUID) -> dict[str, Any]:
        return container.production.preview_scene_look_prompt(scene_id)

    @app.get("/api/v1/scenes/{scene_id}/look-versions")
    def scene_look_versions(scene_id: uuid.UUID) -> list[dict[str, Any]]:
        scene = repository.get_scene(scene_id)
        versions: list[dict[str, Any]] = []
        for asset in repository.list_assets(project_id=scene.project_id):
            if asset.scene_id != scene_id or asset.role != "scene_look":
                continue
            item = {
                **_asset_json(asset),
                "selected": asset.id == scene.selected_look_asset_id,
                "attempt": None,
                "prompt": None,
                "inputSnapshot": {},
            }
            if asset.step_id is not None:
                step = repository.get_step(asset.step_id)
                prompt = repository.get_prompt(step.id)
                item["attempt"] = step.attempt
                item["inputSnapshot"] = step.input_snapshot
                item["prompt"] = (
                    None
                    if prompt is None
                    else {
                        "id": str(prompt.id),
                        "purpose": prompt.purpose.value,
                        "model": prompt.model,
                        "text": prompt.text,
                        "sha256": prompt.sha256,
                    }
                )
            versions.append(item)
        return sorted(versions, key=lambda item: int(item["attempt"] or 0), reverse=True)

    @app.get("/api/v1/scenes/{scene_id}/visual-assets")
    def scene_visual_assets(scene_id: uuid.UUID) -> dict[str, Any]:
        scene = repository.get_scene(scene_id)
        assets = repository.list_assets(
            project_id=scene.project_id,
            include_canon=True,
        )

        def version_json(asset: Any) -> dict[str, Any]:
            item = {
                **_asset_json(asset),
                "attempt": None,
                "prompt": None,
                "inputSnapshot": {},
            }
            if asset.step_id is not None:
                step = repository.get_step(asset.step_id)
                prompt = repository.get_prompt(step.id)
                item["attempt"] = step.attempt
                item["inputSnapshot"] = step.input_snapshot
                item["prompt"] = None if prompt is None else {
                    "id": str(prompt.id),
                    "purpose": prompt.purpose.value,
                    "model": prompt.model,
                    "text": prompt.text,
                    "sha256": prompt.sha256,
                }
            return item

        workflow = container.editing.creative_workflow(scene.id)
        return {
            "sceneId": str(scene.id),
            "lookDraftRevision": scene.look_draft_revision,
            "selectedReferenceAssetIds": (
                []
                if scene.look_draft is None
                else [str(item.asset_id) for item in scene.look_draft.reference_bindings]
            ),
            "canon": [
                _asset_json(item)
                for item in assets
                if item.scope == "canon" and item.status == "approved"
            ],
            "project": [
                version_json(item)
                for item in assets
                if item.scope == "project" and item.media_type == "image"
            ],
            "scene": [
                version_json(item)
                for item in assets
                if item.scope == "scene"
                and item.scene_id == scene.id
                and item.media_type == "image"
            ],
            "plans": workflow["stages"].get("visualAssets", []),
            "readiness": container.editing.scene_asset_readiness(scene.id).model_dump(
                mode="json",
                by_alias=True,
            ),
        }

    @app.get("/api/v1/shots/{shot_id}")
    def shot_trace(shot_id: uuid.UUID) -> dict[str, Any]:
        return repository.shot_trace(shot_id)

    @app.get("/api/v1/shots/{shot_id}/generation-workspace")
    def shot_generation_workspace(shot_id: uuid.UUID) -> dict[str, Any]:
        return container.production.generation_workspace(shot_id)
    @app.get("/api/v1/shots/{shot_id}/prompt-preview")
    def shot_prompt_preview(
        shot_id: uuid.UUID,
        target: ReferenceTarget = ReferenceTarget.VIDEO,
        regeneration_instruction: str | None = None,
    ) -> dict[str, Any]:
        return container.production.preview_shot_prompt(
            shot_id,
            target=target,
            regeneration_instruction=regeneration_instruction,
        )

    @app.put("/api/v1/shots/{shot_id}/references")
    def update_references(shot_id: uuid.UUID, payload: ReferencesRequest) -> dict[str, Any]:
        shot = repository.get_shot(shot_id)
        draft = shot.draft.model_copy(update={"reference_bindings": payload.references})
        return _shot_json(repository.update_shot(shot_id, draft))

    async def store_uploaded_reference(
        project_id: uuid.UUID,
        *,
        file: UploadFile,
        usage: ReferenceUsage,
        role: ReferenceRole,
        display_name: str | None,
        scope: str = "project",
        scene_id: uuid.UUID | None = None,
        purpose: VisualAssetPurpose | None = None,
    ) -> dict[str, Any]:
        upload_root = container.runtime_settings.work_root / "uploads"
        upload_root.mkdir(parents=True, exist_ok=True)
        suffix = Path(file.filename or "reference.png").suffix or ".png"
        temporary = upload_root / f"{uuid.uuid4().hex}{suffix}"
        try:
            payload = await file.read(32 * 1024 * 1024 + 1)
            if len(payload) > 32 * 1024 * 1024:
                raise ValueError("reference image cannot exceed 32 MiB")
            temporary.write_bytes(payload)
            asset = container.production.import_reference(
                project_id=project_id,
                path=temporary,
                usage=usage.value,
                role=role.value,
                display_name=display_name,
                scope=scope,
                scene_id=scene_id,
                purpose=purpose,
            )
            return _asset_json(asset)
        finally:
            temporary.unlink(missing_ok=True)

    @app.post("/api/v1/projects/{project_id}/references")
    async def upload_reference(
        project_id: uuid.UUID,
        usage: ReferenceUsage = Form(...),
        role: ReferenceRole = Form(...),
        display_name: str | None = Form(default=None, alias="displayName"),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        return await store_uploaded_reference(
            project_id,
            file=file,
            usage=usage,
            role=role,
            display_name=display_name,
        )

    @app.post("/api/v1/projects/{project_id}/visual-references")
    async def upload_project_visual_reference(
        project_id: uuid.UUID,
        purpose: VisualAssetPurpose = Form(...),
        display_name: str | None = Form(default=None, alias="displayName"),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        role = {
            VisualAssetPurpose.WARDROBE: ReferenceRole.SCENE,
            VisualAssetPurpose.ENVIRONMENT: ReferenceRole.SCENE,
            VisualAssetPurpose.PROP: ReferenceRole.PROP,
            VisualAssetPurpose.COMPOSITION: ReferenceRole.COMPOSITION,
        }[purpose]
        return await store_uploaded_reference(
            project_id,
            file=file,
            usage=ReferenceUsage.GENERATION_REFERENCE,
            role=role,
            display_name=display_name,
            purpose=purpose,
        )

    @app.post("/api/v1/scenes/{scene_id}/visual-references")
    async def upload_scene_visual_reference(
        scene_id: uuid.UUID,
        purpose: VisualAssetPurpose = Form(...),
        display_name: str | None = Form(default=None, alias="displayName"),
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        scene = repository.get_scene(scene_id)
        role = {
            VisualAssetPurpose.WARDROBE: ReferenceRole.SCENE,
            VisualAssetPurpose.ENVIRONMENT: ReferenceRole.SCENE,
            VisualAssetPurpose.PROP: ReferenceRole.PROP,
            VisualAssetPurpose.COMPOSITION: ReferenceRole.COMPOSITION,
        }[purpose]
        return await store_uploaded_reference(
            scene.project_id,
            file=file,
            usage=ReferenceUsage.GENERATION_REFERENCE,
            role=role,
            display_name=display_name,
            scope="scene",
            scene_id=scene.id,
            purpose=purpose,
        )

    @app.post("/api/v1/shots/{shot_id}/anchors")
    def generate_anchor(
        shot_id: uuid.UUID,
        payload: GenerateRequest,
        request: Request,
    ) -> dict[str, Any]:
        execution = container.runtime_configuration.capture(runtime_revision(request))
        container.runtime_configuration.run(
            execution,
            lambda: container.production.validate_anchor_request(
                shot_id,
                allow_paid_generation=payload.allow_paid_generation,
                expected_input_hash=payload.expected_input_hash,
                regeneration_instruction=payload.retry_reason,
            ),
        )
        return paid_submit(
            request,
            kind="generate_anchor",
            key=f"shot:{shot_id}:anchor",
            fn=lambda: container.production.generate_anchor(
                shot_id,
                allow_paid_generation=payload.allow_paid_generation,
                regenerate=payload.regenerate,
                reason=payload.retry_reason,
                expected_input_hash=payload.expected_input_hash,
            ),
            context={"shotId": shot_id, "operationKey": "image:anchor"},
            execution=execution,
        )

    @app.post("/api/v1/scenes/{scene_id}/look-images")
    def generate_scene_look(
        scene_id: uuid.UUID,
        payload: GenerateSceneLookRequest,
        request: Request,
    ) -> dict[str, Any]:
        execution = container.runtime_configuration.capture(runtime_revision(request))
        container.runtime_configuration.run(
            execution,
            lambda: container.production.validate_scene_look_request(
                scene_id,
                payload.draft_revision,
            ),
        )
        return paid_submit(
            request,
            kind="generate_scene_look",
            key=f"scene:{scene_id}:look",
            fn=lambda: container.production.generate_scene_look(
                scene_id,
                allow_paid_generation=payload.allow_paid_generation,
                draft_revision=payload.draft_revision,
                regenerate=payload.regenerate,
                reason=payload.retry_reason,
                expected_input_hash=payload.expected_input_hash,
            ),
            context={"sceneId": scene_id, "operationKey": "image:scene-look"},
            execution=execution,
        )

    @app.post("/api/v1/scenes/{scene_id}/reference-images")
    def generate_scene_reference_image(
        scene_id: uuid.UUID,
        payload: GenerateReferenceImageRequest,
        request: Request,
    ) -> dict[str, Any]:
        scene = repository.get_scene(scene_id)
        execution = container.runtime_configuration.capture(runtime_revision(request))
        operation_key = container.runtime_configuration.run(
            execution,
            lambda: container.production.validate_reference_image_request(
                project_id=scene.project_id,
                scene_id=scene.id,
                scope="scene",
                draft=payload.draft,
                allow_paid_generation=payload.allow_paid_generation,
            ),
        )
        submitted = paid_submit(
            request,
            kind="generate_reference_image",
            key=(
                f"scene:{scene.id}:{operation_key}"
            ),
            fn=lambda: container.production.generate_reference_image(
                project_id=scene.project_id,
                scene_id=scene.id,
                scope="scene",
                draft=payload.draft,
                allow_paid_generation=payload.allow_paid_generation,
                regenerate=payload.regenerate,
                reason=payload.retry_reason,
                expected_input_hash=payload.expected_input_hash,
            ),
            context={
                "projectId": scene.project_id,
                "sceneId": scene.id,
                "operationKey": operation_key,
            },
            execution=execution,
        )
        return {**submitted, "operationKey": operation_key}

    @app.post("/api/v1/scenes/{scene_id}/reference-images/preview")
    def preview_scene_reference_image(
        scene_id: uuid.UUID,
        payload: PreviewReferenceImageRequest,
    ) -> dict[str, Any]:
        scene = repository.get_scene(scene_id)
        return container.production.preview_reference_image(
            project_id=scene.project_id,
            scene_id=scene.id,
            scope="scene",
            draft=payload.draft,
            regeneration_instruction=payload.reason if payload.regenerate else None,
        )

    @app.post("/api/v1/steps/{step_id}/visual-asset-plan-revisions")
    def revise_visual_asset_plan(
        step_id: uuid.UUID,
        payload: ReviseVisualAssetPlanRequest,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return _creative_step_json(
            container.editing.revise_visual_asset_plan(
                step_id,
                expected_revision=parse_version_header(if_match),
                plan=AcceptedVisualAssetPlan(selections=payload.selections),
                note=payload.note,
            )
        )

    @app.post("/api/v1/projects/{project_id}/reference-images")
    def generate_project_reference_image(
        project_id: uuid.UUID,
        payload: GenerateReferenceImageRequest,
        request: Request,
    ) -> dict[str, Any]:
        execution = container.runtime_configuration.capture(runtime_revision(request))
        operation_key = container.runtime_configuration.run(
            execution,
            lambda: container.production.validate_reference_image_request(
                project_id=project_id,
                scene_id=None,
                scope="project",
                draft=payload.draft,
                allow_paid_generation=payload.allow_paid_generation,
            ),
        )
        submitted = paid_submit(
            request,
            kind="generate_reference_image",
            key=(
                f"project:{project_id}:{operation_key}"
            ),
            fn=lambda: container.production.generate_reference_image(
                project_id=project_id,
                scene_id=None,
                scope="project",
                draft=payload.draft,
                allow_paid_generation=payload.allow_paid_generation,
                regenerate=payload.regenerate,
                reason=payload.retry_reason,
                expected_input_hash=payload.expected_input_hash,
            ),
            context={
                "projectId": project_id,
                "operationKey": operation_key,
            },
            execution=execution,
        )
        return {**submitted, "operationKey": operation_key}

    @app.post("/api/v1/projects/{project_id}/reference-images/preview")
    def preview_project_reference_image(
        project_id: uuid.UUID,
        payload: PreviewReferenceImageRequest,
    ) -> dict[str, Any]:
        return container.production.preview_reference_image(
            project_id=project_id,
            scene_id=None,
            scope="project",
            draft=payload.draft,
            regeneration_instruction=payload.reason if payload.regenerate else None,
        )

    @app.post("/api/v1/shots/{shot_id}/videos")
    def generate_video(
        shot_id: uuid.UUID,
        payload: GenerateRequest,
        request: Request,
    ) -> dict[str, Any]:
        execution = container.runtime_configuration.capture(runtime_revision(request))
        container.runtime_configuration.run(
            execution,
            lambda: container.production.validate_video_request(
                shot_id,
                allow_paid_generation=payload.allow_paid_generation,
                expected_input_hash=payload.expected_input_hash,
                regeneration_instruction=payload.retry_reason,
            ),
        )
        return paid_submit(
            request,
            kind="generate_video",
            key=f"shot:{shot_id}:video",
            fn=lambda: container.production.generate_video(
                shot_id,
                allow_paid_generation=payload.allow_paid_generation,
                regenerate=payload.regenerate,
                reason=payload.retry_reason,
                expected_input_hash=payload.expected_input_hash,
            ),
            context={"shotId": shot_id, "operationKey": "video:shot"},
            execution=execution,
        )

    @app.get("/api/v1/shots/{shot_id}/versions")
    def shot_versions(shot_id: uuid.UUID) -> list[dict[str, Any]]:
        return [
            _asset_json(item)
            for item in repository.list_assets(shot_id=shot_id)
            if item.media_type == "video"
        ]

    @app.post("/api/v1/assets/{asset_id}/review")
    def review_asset(asset_id: uuid.UUID, payload: ReviewRequest) -> dict[str, Any]:
        return container.production.decide_asset(
            asset_id,
            decision=payload.decision,
            reason=payload.reason,
            select=payload.select,
        )

    @app.post("/api/v1/shots/{shot_id}/versions/{asset_id}/select")
    def select_version(shot_id: uuid.UUID, asset_id: uuid.UUID) -> dict[str, Any]:
        asset = repository.get_asset(asset_id)
        if asset.shot_card_id != shot_id:
            raise ValueError("media version does not belong to the requested shot")
        if asset.media_type == "video" and asset.role in {"shot_video", "shot_video_edit"}:
            kind = "video"
        elif asset.media_type == "image" and asset.role == "shot_anchor":
            kind = "anchor"
        else:
            raise ValueError("asset is not a selectable shot anchor or video version")
        return _shot_json(repository.select_shot_asset(shot_id, kind=kind, asset_id=asset_id))

    @app.post("/api/v1/shots/{shot_id}/range-edits")
    def range_edit(
        shot_id: uuid.UUID,
        payload: RangeEditRequest,
        request: Request,
    ) -> dict[str, Any]:
        return paid_submit(
            request,
            kind="range_edit",
            key=f"shot:{shot_id}:range:{payload.source_asset_id}:{payload.start_ms}:{payload.end_ms}",
            fn=lambda: container.production.range_edit(
                shot_id,
                source_asset_id=payload.source_asset_id,
                start_ms=payload.start_ms,
                end_ms=payload.end_ms,
                instruction=payload.instruction,
                allow_paid_generation=payload.allow_paid_generation,
            ),
            context={"shotId": shot_id, "operationKey": "video:range-edit"},
        )

    @app.post("/api/v1/projects/{project_id}/sequences")
    def build_sequence(
        project_id: uuid.UUID,
        payload: BuildSequenceRequest | None = None,
    ) -> dict[str, Any]:
        transitions = {
            item.after_shot_id: item.transition
            for item in (payload.transitions if payload is not None else [])
        }
        return _submit(
            job_registry,
            kind="build_sequence",
            key=f"project:{project_id}:sequence",
            fn=lambda: _sequence_json(
                container.sequences.build_project_sequence(
                    project_id,
                    transitions=transitions,
                    intro_transition=None if payload is None else payload.intro_transition,
                    outro_transition=None if payload is None else payload.outro_transition,
                )
            ),
            context={"projectId": project_id, "operationKey": "sequence:build"},
        )

    @app.get("/api/v1/projects/{project_id}/sequences")
    def list_sequences(project_id: uuid.UUID) -> list[dict[str, Any]]:
        return [_sequence_json(item) for item in repository.list_sequences(project_id)]

    @app.post("/api/v1/projects/{project_id}/sequences/{sequence_id}/select")
    def select_sequence(
        project_id: uuid.UUID,
        sequence_id: uuid.UUID,
        payload: SelectSequenceRequest,
    ) -> dict[str, Any]:
        decided = repository.decide_sequence(sequence_id, approved=payload.approve)
        if not payload.approve:
            return _sequence_json(decided)
        return _sequence_json(repository.select_sequence(project_id, sequence_id))

    @app.post("/api/v1/steps/{step_id}/resume")
    def resume_step(step_id: uuid.UUID) -> dict[str, Any]:
        step = repository.get_step(step_id)
        return _submit(
            job_registry,
            kind="resume_step",
            key=f"step:{step_id}:resume",
            fn=lambda: container.production.resume_step(step_id, wait=False),
            context={
                "projectId": step.project_id,
                "sceneId": step.scene_id,
                "shotId": step.shot_card_id,
                "operationKey": "resume",
                "stepId": step_id,
            },
        )

    @app.get("/api/v1/steps/{step_id}/reconciliation-candidates")
    def reconciliation_candidates(step_id: uuid.UUID) -> tuple[dict[str, Any], ...]:
        return container.production.reconcile_candidates(step_id)

    @app.post("/api/v1/steps/{step_id}/reconcile")
    def reconcile_step(step_id: uuid.UUID, payload: ReconcileRequest) -> dict[str, Any]:
        return container.production.reconcile(step_id, task_id=payload.provider_task_id)

    @app.get("/api/v1/assets/{asset_id}/content")
    def asset_content(asset_id: uuid.UUID) -> FileResponse:
        asset = repository.get_asset(asset_id)
        if asset.path is None:
            raise HTTPException(status_code=404, detail="asset content requires repair")
        resolved = asset.path.expanduser().resolve()
        if not any(resolved.is_relative_to(root) for root in roots):
            raise HTTPException(status_code=403, detail="asset is outside configured media roots")
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="asset file does not exist")
        return FileResponse(resolved)

    @app.get("/api/v1/canon")
    def list_canon() -> list[dict[str, Any]]:
        return [
            _asset_json(item)
            for item in repository.list_assets()
            if item.scope == "canon" and item.status == "approved"
        ]

    @app.get("/api/v1/projects/{project_id}/tasks")
    def project_tasks(project_id: uuid.UUID) -> list[dict[str, Any]]:
        repository.get_project(project_id)
        items = list(reversed(repository.list_steps(project_id=project_id)))[:100]
        cancellations = _task_cancellations_json(container, items)
        return [
            _task_json(
                item,
                recovery=_task_recovery_json(container, item),
                cancellation=cancellations.get(item.id),
            )
            for item in items
        ]

    @app.post("/api/v1/task-center/tasks/{step_id}/recover")
    def recover_persistent_task(step_id: uuid.UUID) -> dict[str, Any]:
        container.workflow_queue.recover(step_id)
        item = repository.get_step(step_id)
        return _task_json(
            item,
            recovery=_task_recovery_json(container, item),
            cancellation=_task_cancellation_json(container, item),
        )

    @app.post("/api/v1/steps/{step_id}/cancellation")
    def cancel_persistent_task(
        step_id: uuid.UUID,
        payload: CancelTaskRequest,
    ) -> dict[str, Any]:
        container.workflow_queue.cancel(
            step_id,
            expected_status=payload.expected_status,
            expected_provider_task_id=payload.expected_provider_task_id,
            reason=payload.reason,
        )
        item = repository.get_step(step_id)
        return _task_json(
            item,
            recovery=_task_recovery_json(container, item),
            cancellation=_task_cancellation_json(container, item),
        )

    @app.get("/api/v1/task-center")
    def task_center() -> dict[str, list[dict[str, Any]]]:
        latest_by_operation: dict[
            tuple[
                uuid.UUID,
                uuid.UUID | None,
                uuid.UUID | None,
                str,
                str | None,
                str | None,
                str | None,
            ],
            Any,
        ] = {}
        for item in repository.task_center_steps():
            if item.operation_key == "editor:anchor-brief":
                continue
            snapshot = item.input_snapshot if isinstance(item.input_snapshot, dict) else {}
            key = (
                item.project_id,
                item.scene_id,
                item.shot_card_id,
                item.operation_key,
                snapshot.get("canvasNodeId"),
                snapshot.get("businessObjectId"),
                snapshot.get("parentStepId"),
            )
            latest_by_operation.setdefault(key, item)
        persistent = sorted(
            latest_by_operation.values(),
            key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )[:100]
        cancellations = _task_cancellations_json(container, persistent)
        assets_by_step: dict[uuid.UUID, list[Any]] = {}
        for asset in repository.list_assets():
            if asset.step_id is not None:
                assets_by_step.setdefault(asset.step_id, []).append(asset)

        def persistent_task(item: Any) -> dict[str, Any]:
            projected = _task_json(
                item,
                recovery=_task_recovery_json(container, item),
                cancellation=cancellations.get(item.id),
            )
            snapshot = item.input_snapshot if isinstance(item.input_snapshot, dict) else {}
            completed_at = getattr(item, "completed_at", None)
            projected.update(
                canvasNodeId=snapshot.get("canvasNodeId"),
                canvasGroupId=snapshot.get("canvasGroupId"),
                recipeInstanceId=snapshot.get("recipeInstanceId"),
                businessObjectId=snapshot.get("businessObjectId"),
                creationMode=snapshot.get("creationMode"),
                parentStepId=snapshot.get("parentStepId"),
                childStepIds=item.progress.get("childStepIds", []),
                workflowStage=snapshot.get("workflowStage"),
                phase=snapshot.get("phase"),
                progress=item.progress,
                resultSummary=item.progress.get("resultSummary"),
                completedAt=(
                    None if completed_at is None else completed_at.isoformat()
                ),
            )
            if item.status is StepStatus.SUCCEEDED and any(
                asset.status == "candidate" for asset in assets_by_step.get(item.id, [])
            ):
                projected["status"] = "awaiting_review"
            return projected

        return {
            "runtimeJobs": [item.to_dict() for item in job_registry.list(limit=100)],
            "persistentTasks": [persistent_task(item) for item in persistent],
        }

    @app.get("/api/v1/task-center/events")
    async def task_center_events(
        request: Request,
        last_event_id: str | None = Header(alias="Last-Event-ID", default=None),
        after_event_id: int | None = Query(alias="afterEventId", default=None, ge=0),
    ) -> StreamingResponse:
        cursor = parse_event_cursor(last_event_id, after_event_id)

        def load(after_sequence: int) -> tuple[dict[str, Any], ...]:
            return repository.task_center_events(after_sequence=after_sequence, limit=200)

        return StreamingResponse(
            stream_events(request, loader=load, after_sequence=cursor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return [item.to_dict() for item in job_registry.list()]

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        return job_registry.get(job_id).to_dict()

    canvas_v2 = getattr(container, "canvas_v2", None)
    if canvas_v2 is not None:
        install_canvas_v2_routes(app, canvas_v2, job_registry)
    production_recipes = getattr(container, "production_recipes", None)
    if production_recipes is not None:
        install_production_recipe_routes(app, production_recipes)

    if static_dir is not None:
        if not (static_dir / "index.html").is_file():
            raise ValueError(f"static directory has no index.html: {static_dir}")
        app.mount("/", _SPAStaticFiles(directory=static_dir, html=True), name="web")
    return app


def _submit(
    jobs: JobRegistry,
    *,
    kind: str,
    key: str,
    fn: Callable[[], Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    try:
        record = jobs.submit(kind=kind, dedup_key=key, fn=fn, context=context)
    except JobConflictError as exc:
        return {"jobId": exc.job_id, "status": "running", "reused": True}
    return {"jobId": record.job_id, "status": record.status, "reused": False}


def _scene_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "projectId": str(item.project_id),
        "order": item.order,
        **item.draft.model_dump(mode="json", by_alias=True),
        "status": item.status.value,
        "selectedLookAssetId": (
            None
            if item.selected_look_asset_id is None
            else str(item.selected_look_asset_id)
        ),
        "lookDraftRevision": item.look_draft_revision,
    }


def _shot_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "sceneId": str(item.scene_id),
        "order": item.order,
        **item.draft.model_dump(mode="json", by_alias=True),
        "draftRevision": item.draft_revision,
        "useSceneLook": item.draft.use_scene_look,
        "status": item.status.value,
        "selectedAnchorAssetId": None
        if item.selected_anchor_asset_id is None
        else str(item.selected_anchor_asset_id),
        "selectedVideoAssetId": None
        if item.selected_video_asset_id is None
        else str(item.selected_video_asset_id),
    }


def _asset_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "role": item.role,
        "mediaType": item.media_type,
        "scope": item.scope,
        "status": item.status,
        "projectId": None if item.project_id is None else str(item.project_id),
        "sceneId": None if item.scene_id is None else str(item.scene_id),
        "shotId": None if item.shot_card_id is None else str(item.shot_card_id),
        "producingStepId": None if item.step_id is None else str(item.step_id),
        "sha256": item.sha256,
        "semanticKey": item.semantic_key,
        "metadata": item.metadata,
        "contentReady": item.content_ready,
        "displayName": item.display_name,
        "referencePurpose": item.reference_purpose,
        "visualProfileRevisionId": item.metadata.get("visualProfileRevisionId"),
        "lookDraftRevision": item.metadata.get("lookDraftRevision"),
        "createdAt": None if item.created_at is None else item.created_at.isoformat(),
    }


def _shot_assistance_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "analysis": item.analysis.model_dump(mode="json", by_alias=True),
    }


def _story_diagnosis_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "diagnosis": item.output.model_dump(mode="json", by_alias=True),
    }


def _story_expansion_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "output": item.output.model_dump(mode="json", by_alias=True),
    }


def _story_rewrite_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "rewrite": item.output.model_dump(mode="json", by_alias=True),
    }


def _visual_asset_plan_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "plan": item.output.model_dump(mode="json", by_alias=True),
    }


def _creative_step_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.id),
        "operationKey": item.operation_key,
        "status": item.status.value,
        "attempt": item.attempt,
        "model": item.model,
        "sourceHash": item.input_snapshot.get("sourceHash"),
        "shotSnapshotHash": item.input_snapshot.get("shotSnapshotHash"),
        "providerOutput": item.input_snapshot.get("providerOutput"),
        "acceptedOutput": item.input_snapshot.get("acceptedOutput"),
        "acceptedAt": item.input_snapshot.get("acceptedAt"),
        "source": item.input_snapshot.get("source"),
        "manualRevisionOfStepId": item.input_snapshot.get("manualRevisionOfStepId"),
        "manualRevisionNote": item.input_snapshot.get("manualRevisionNote"),
        "error": item.error,
        "createdAt": None if item.created_at is None else item.created_at.isoformat(),
    }


def _task_json(
    item: Any,
    *,
    recovery: dict[str, object] | None = None,
    cancellation: dict[str, object] | None = None,
) -> dict[str, Any]:
    snapshot = item.input_snapshot if isinstance(item.input_snapshot, dict) else {}
    progress = item.progress if isinstance(item.progress, dict) else {}
    projected = {
        "stepId": str(item.id),
        "projectId": str(item.project_id),
        "sceneId": None if item.scene_id is None else str(item.scene_id),
        "shotId": None if item.shot_card_id is None else str(item.shot_card_id),
        "kind": item.kind.value,
        "status": item.status.value,
        "attempt": item.attempt,
        "operationKey": item.operation_key,
        "provider": item.provider,
        "providerTaskId": item.provider_task_id,
        "canvasNodeId": snapshot.get("canvasNodeId"),
        "canvasGroupId": snapshot.get("canvasGroupId"),
        "recipeInstanceId": snapshot.get("recipeInstanceId"),
        "businessObjectId": snapshot.get("businessObjectId"),
        "creationMode": snapshot.get("creationMode"),
        "parentStepId": snapshot.get("parentStepId"),
        "childStepIds": progress.get("childStepIds", []),
        "workflowStage": snapshot.get("workflowStage"),
        "phase": snapshot.get("phase"),
        "model": item.model,
        "inputSnapshot": snapshot,
        "error": item.error,
        "progress": progress,
        "resultSummary": progress.get("resultSummary"),
        "createdAt": None if item.created_at is None else item.created_at.isoformat(),
        "updatedAt": None if item.updated_at is None else item.updated_at.isoformat(),
        "completedAt": None if item.completed_at is None else item.completed_at.isoformat(),
    }
    if recovery is not None:
        projected["recovery"] = recovery
    if cancellation is not None:
        projected["cancellation"] = cancellation
    return projected


def _task_recovery_json(
    container: Any,
    item: Any,
) -> dict[str, object] | None:
    if item.status not in {
        StepStatus.FAILED,
        StepStatus.SUBMISSION_UNKNOWN,
    }:
        return None
    if not (item.operation_key == "recipe:character_design" or item.kind.value == "video"):
        return None
    return container.workflow_queue.recovery_for(item.id).to_dict()


def _task_cancellation_json(
    container: Any,
    item: Any,
) -> dict[str, object] | None:
    queue = getattr(container, "workflow_queue", None)
    if queue is None:
        return None
    return queue.cancellation_for(item.id).to_dict()


def _task_cancellations_json(
    container: Any,
    items: list[Any],
) -> dict[uuid.UUID, dict[str, object]]:
    queue = getattr(container, "workflow_queue", None)
    if queue is None or not items:
        return {}
    return {
        step_id: policy.to_dict()
        for step_id, policy in queue.cancellations_for(
            tuple(item.id for item in items)
        ).items()
    }


def _visual_profile_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "projectId": str(item.project_id),
        "revision": item.revision,
        "profileHash": item.profile_hash,
        "sourceProfileId": item.source_profile_id,
        **item.draft.model_dump(mode="json", by_alias=True),
        "createdAt": None if item.created_at is None else item.created_at.isoformat(),
    }


def _suggestion_json(item: Any) -> dict[str, Any]:
    return {
        "stepId": str(item.step_id),
        "output": item.output.model_dump(mode="json", by_alias=True),
    }


def _sequence_json(item: Any) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "projectId": str(item.project_id),
        "revision": item.revision,
        "parentSequenceId": None
        if item.parent_sequence_id is None
        else str(item.parent_sequence_id),
        "renderedAssetId": None if item.rendered_asset_id is None else str(item.rendered_asset_id),
        "status": item.status.value if isinstance(item.status, SequenceStatus) else item.status,
        "plan": item.plan.model_dump(mode="json"),
    }
