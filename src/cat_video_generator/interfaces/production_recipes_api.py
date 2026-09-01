"""HTTP contracts for fixed-IP production recipes."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Protocol
from urllib.parse import quote

from fastapi import (
    APIRouter,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import Field

from ..domain.contract_base import StrictModel
from ..domain.production_recipes import (
    CanvasGroupRunRequest,
    CharacterDesignRecipeRunRequest,
    DirectorWorkflowAdoptionRequest,
    EpisodeRules,
    GenerationPlanRevisionDraft,
    HumanReviewDecision,
    HumanReviewDraft,
    PaidRecipeRunRequest,
    ProductionRecipeInstanceDraft,
    ProductionRecipeInstancePatch,
    RecipeSequenceRunRequest,
    StoryboardProductionPlanConfirmation,
    StoryboardRecipeRunRequest,
)
from .http_headers import parse_version_header

logger = logging.getLogger(__name__)


class ProductionRecipeApiService(Protocol):
    def preview_director_workflow_adoption(
        self,
        project_id: uuid.UUID,
    ) -> dict[str, Any]: ...

    def adopt_director_workflow(
        self,
        project_id: uuid.UUID,
        payload: DirectorWorkflowAdoptionRequest,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]: ...

    def list_recipes(self) -> list[dict[str, Any]]: ...

    def create_instance(
        self,
        project_id: uuid.UUID,
        payload: ProductionRecipeInstanceDraft,
    ) -> dict[str, Any]: ...

    def get_instance(self, instance_id: uuid.UUID) -> dict[str, Any]: ...

    def update_instance(
        self,
        instance_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: ProductionRecipeInstancePatch,
    ) -> dict[str, Any]: ...

    def record_review(
        self,
        instance_id: uuid.UUID,
        payload: HumanReviewDraft,
        *,
        episode_rules: EpisodeRules | None = None,
    ) -> dict[str, Any]: ...

    def confirm_storyboard_production_plan(
        self,
        instance_id: uuid.UUID,
        payload: StoryboardProductionPlanConfirmation,
    ) -> dict[str, Any]: ...

    def revise_generation_plan(
        self,
        instance_id: uuid.UUID,
        plan_id: uuid.UUID,
        *,
        expected_revision: int,
        payload: GenerationPlanRevisionDraft,
    ) -> dict[str, Any]: ...

    def run_story(
        self, instance_id: uuid.UUID, payload: PaidRecipeRunRequest
    ) -> dict[str, Any]: ...

    def run_story_events(
        self, instance_id: uuid.UUID, payload: PaidRecipeRunRequest
    ) -> dict[str, Any]: ...

    def run_story_script(
        self, instance_id: uuid.UUID, payload: PaidRecipeRunRequest
    ) -> dict[str, Any]: ...

    def run_creative_brief(
        self, instance_id: uuid.UUID, payload: PaidRecipeRunRequest
    ) -> dict[str, Any]: ...

    def run_character_design(
        self, instance_id: uuid.UUID, payload: CharacterDesignRecipeRunRequest
    ) -> dict[str, Any]: ...

    def preview_character_design(
        self, instance_id: uuid.UUID, payload: CharacterDesignRecipeRunRequest
    ) -> dict[str, Any]: ...

    def preview_character_design_validation(
        self, instance_id: uuid.UUID, payload: PaidRecipeRunRequest
    ) -> dict[str, Any]: ...

    def run_storyboard(
        self, instance_id: uuid.UUID, payload: StoryboardRecipeRunRequest
    ) -> dict[str, Any]: ...

    def run_anchor(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]: ...

    def run_video(
        self,
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]: ...

    def run_sequence(
        self, instance_id: uuid.UUID, payload: RecipeSequenceRunRequest
    ) -> dict[str, Any]: ...

    def compile_group(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def run_group(
        self, group_id: uuid.UUID, payload: CanvasGroupRunRequest
    ) -> dict[str, Any]: ...

    def enqueue_recipe_task(
        self,
        instance_id: uuid.UUID,
        *,
        operation_key: str,
        payload: PaidRecipeRunRequest,
        shot_id: uuid.UUID | None = None,
        group_id: uuid.UUID | None = None,
        creation_mode: str | None = None,
    ) -> dict[str, Any]: ...

    def enqueue_group_task(
        self,
        group_id: uuid.UUID,
        payload: CanvasGroupRunRequest,
    ) -> dict[str, Any]: ...

    def save_group_template(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def ungroup(
        self,
        group_id: uuid.UUID,
        *,
        expected_revision: int,
    ) -> dict[str, Any]: ...

    def convert_group_to_shots(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def group_download_manifest(self, group_id: uuid.UUID) -> dict[str, Any]: ...

    def build_group_download(self, group_id: uuid.UUID) -> tuple[bytes, str]: ...


class ReviewDecisionRequest(StrictModel):
    recipe_instance_id: uuid.UUID = Field(alias="recipeInstanceId")
    target_type: str = Field(alias="targetType", min_length=1, max_length=80)
    target_id: uuid.UUID = Field(alias="targetId")
    target_revision: int | None = Field(alias="targetRevision", default=None, ge=1)
    target_hash: str | None = Field(
        alias="targetHash",
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    decision: HumanReviewDecision
    blocking_diagnostic_present: bool = Field(
        alias="blockingDiagnosticPresent",
        default=False,
    )
    issues: list[str] = Field(default_factory=list, max_length=30)
    reason: str | None = Field(default=None, max_length=2_000)
    episode_rules: EpisodeRules | None = Field(alias="episodeRules", default=None)


def install_production_recipe_routes(
    app: FastAPI,
    service: ProductionRecipeApiService,
) -> None:
    router = APIRouter(prefix="/api/v2", tags=["production-recipes"])

    @router.get("/production-recipes")
    def list_recipes() -> list[dict[str, Any]]:
        return service.list_recipes()

    @router.get("/projects/{project_id}/director-workflow-adoption-preview")
    def preview_director_workflow_adoption(
        project_id: uuid.UUID,
    ) -> dict[str, Any]:
        return service.preview_director_workflow_adoption(project_id)

    @router.post(
        "/projects/{project_id}/director-workflow-adoptions",
        status_code=status.HTTP_201_CREATED,
    )
    def adopt_director_workflow(
        project_id: uuid.UUID,
        payload: DirectorWorkflowAdoptionRequest,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=96),
    ) -> dict[str, Any]:
        return service.adopt_director_workflow(
            project_id,
            payload,
            idempotency_key=idempotency_key,
        )

    @router.post(
        "/projects/{project_id}/recipe-instances",
        status_code=status.HTTP_201_CREATED,
    )
    def create_instance(
        project_id: uuid.UUID,
        payload: ProductionRecipeInstanceDraft,
    ) -> dict[str, Any]:
        return service.create_instance(project_id, payload)

    @router.get("/recipe-instances/{instance_id}")
    def get_instance(instance_id: uuid.UUID) -> dict[str, Any]:
        return service.get_instance(instance_id)

    @router.patch("/recipe-instances/{instance_id}")
    def update_instance(
        instance_id: uuid.UUID,
        payload: ProductionRecipeInstancePatch,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.update_instance(
            instance_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.post(
        "/recipe-instances/{instance_id}/creative-brief-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_creative_brief(
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:creative",
            payload=payload,
        )

    @router.post(
        "/recipe-instances/{instance_id}/story-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_story(
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:story",
            payload=payload,
        )

    @router.post(
        "/recipe-instances/{instance_id}/story-event-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_story_events(
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:story_events",
            payload=payload,
        )

    @router.post(
        "/recipe-instances/{instance_id}/story-script-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_story_script(
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:story_script",
            payload=payload,
        )

    @router.post(
        "/recipe-instances/{instance_id}/character-design-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_character_design(
        instance_id: uuid.UUID,
        payload: CharacterDesignRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:character_design",
            payload=payload,
        )

    @router.post("/recipe-instances/{instance_id}/character-design-input-preview")
    def preview_character_design(
        instance_id: uuid.UUID,
        payload: CharacterDesignRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.preview_character_design(instance_id, payload)

    @router.post(
        "/recipe-instances/{instance_id}/character-design-validation-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_character_design_validation(
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:character_design_validation",
            payload=payload,
        )

    @router.post(
        "/recipe-instances/{instance_id}/character-design-validation-input-preview"
    )
    def preview_character_design_validation(
        instance_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.preview_character_design_validation(instance_id, payload)

    @router.post(
        "/recipe-instances/{instance_id}/storyboard-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_storyboard(
        instance_id: uuid.UUID,
        payload: StoryboardRecipeRunRequest,
    ) -> dict[str, Any]:
        if (
            payload.creation_mode.value == "from_characters"
            and len(payload.reference_asset_ids) < 2
        ):
            raise ValueError("治愈组合包的角色生成分镜必须同时包含固定儿童与固定猫咪素材")
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:storyboard",
            payload=payload,
            creation_mode=payload.creation_mode.value,
        )

    @router.put(
        "/recipe-instances/{instance_id}/generation-plans/{plan_id}"
    )
    def revise_generation_plan(
        instance_id: uuid.UUID,
        plan_id: uuid.UUID,
        payload: GenerationPlanRevisionDraft,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.revise_generation_plan(
            instance_id,
            plan_id,
            expected_revision=parse_version_header(if_match),
            payload=payload,
        )

    @router.post(
        "/recipe-instances/{instance_id}/storyboard-production-confirmations",
        status_code=status.HTTP_201_CREATED,
    )
    def confirm_storyboard_production_plan(
        instance_id: uuid.UUID,
        payload: StoryboardProductionPlanConfirmation,
    ) -> dict[str, Any]:
        return service.confirm_storyboard_production_plan(instance_id, payload)

    @router.post(
        "/recipe-instances/{instance_id}/shots/{shot_id}/anchor-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_anchor(
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:anchor",
            payload=payload,
            shot_id=shot_id,
        )

    @router.post(
        "/recipe-instances/{instance_id}/shots/{shot_id}/video-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_video(
        instance_id: uuid.UUID,
        shot_id: uuid.UUID,
        payload: PaidRecipeRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:video",
            payload=payload,
            shot_id=shot_id,
        )

    @router.post(
        "/recipe-instances/{instance_id}/sequence-runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_sequence(
        instance_id: uuid.UUID,
        payload: RecipeSequenceRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_recipe_task(
            instance_id,
            operation_key="recipe:sequence",
            payload=payload,
        )

    @router.post("/canvas-groups/{group_id}/compile-run")
    def compile_group(group_id: uuid.UUID) -> dict[str, Any]:
        return service.compile_group(group_id)

    @router.post(
        "/canvas-groups/{group_id}/runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def run_group(
        group_id: uuid.UUID,
        payload: CanvasGroupRunRequest,
    ) -> dict[str, Any]:
        return service.enqueue_group_task(group_id, payload)

    @router.post(
        "/canvas-groups/{group_id}/toolbox-templates",
        status_code=status.HTTP_201_CREATED,
    )
    def save_group_template(group_id: uuid.UUID) -> dict[str, Any]:
        return service.save_group_template(group_id)

    @router.post("/canvas-groups/{group_id}/ungroup")
    def ungroup(
        group_id: uuid.UUID,
        if_match: str = Header(alias="If-Match"),
    ) -> dict[str, Any]:
        return service.ungroup(
            group_id,
            expected_revision=parse_version_header(if_match),
        )

    @router.post("/canvas-groups/{group_id}/shot-groups")
    def convert_group_to_shots(group_id: uuid.UUID) -> dict[str, Any]:
        return service.convert_group_to_shots(group_id)

    @router.get("/canvas-groups/{group_id}/download-manifest")
    def group_download_manifest(group_id: uuid.UUID) -> dict[str, Any]:
        return service.group_download_manifest(group_id)

    @router.get("/canvas-groups/{group_id}/download")
    def download_group(group_id: uuid.UUID) -> Response:
        content, filename = service.build_group_download(group_id)
        return Response(
            content=content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            },
        )

    @router.post("/review-decisions", status_code=status.HTTP_201_CREATED)
    def record_review(payload: ReviewDecisionRequest, request: Request) -> dict[str, Any]:
        review_document = payload.model_dump(mode="json", by_alias=True)
        instance_id = uuid.UUID(review_document.pop("recipeInstanceId"))
        episode_rules_document = review_document.pop("episodeRules")
        correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        try:
            return service.record_review(
                instance_id,
                HumanReviewDraft.model_validate(review_document),
                episode_rules=(
                    None
                    if episode_rules_document is None
                    else EpisodeRules.model_validate(episode_rules_document)
                ),
            )
        except (LookupError, ValueError):
            raise
        except Exception as exc:
            logger.exception(
                "unexpected production recipe review transaction failure",
                extra={
                    "correlation_id": correlation_id,
                    "recipe_instance_id": str(instance_id),
                    "review_target_type": review_document.get("targetType"),
                    "review_target_id": review_document.get("targetId"),
                    "review_target_revision": review_document.get("targetRevision"),
                    "request_path": request.url.path,
                },
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "剧情审核事务未完成，剧情仍未批准且任务仍等待审核",
                    "errorType": "review_transaction_failed",
                    "correlationId": correlation_id,
                },
            ) from exc

    app.include_router(router)
