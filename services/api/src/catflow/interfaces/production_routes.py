"""HTTP boundary for versioned production plans and unit selection."""
import uuid

from fastapi import APIRouter, Depends
from fastapi.routing import APIRoute

from catflow.application.production_plan import (
    ProductionActivation, ProductionPlanDraft, ProductionPlanDto, UnitGeneration, UnitTarget,
)
from catflow.application.production_selection import (
    ProductionAssembly, UnitSelectionCommand, UnitSelectionDto, UnitEvidenceCommand,
)
from catflow.application.service import JobDto, VideoEditDraftDto, StudioConflictError, StudioValidationError
from catflow.application.production_props import PropImageInput, PropImageGeneration, prop_image_preview, \
    generate_prop_image


class ProductionRoute(APIRoute):
    """Map domain validation to the existing 422 envelope without hiding conflicts."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def validated(request):
            try:
                return await handler(request)
            except StudioConflictError:
                raise
            except ValueError as exc:
                raise StudioValidationError(str(exc)) from exc

        return validated


def production_router(service, require_worker):
    router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["production"], route_class=ProductionRoute)
    production = service.production

    @router.post("/production-props/preview")
    def preview_prop(project_id: uuid.UUID, command: PropImageInput):
        return prop_image_preview(service, project_id, command)

    @router.post("/production-props/generations", response_model=JobDto,
                 status_code=202, dependencies=[Depends(require_worker)])
    def generate_prop(project_id: uuid.UUID, command: PropImageGeneration):
        return generate_prop_image(service, project_id, command)

    @router.get("/production-units/{unit_id}/jobs", response_model=list[JobDto])
    def unit_jobs(project_id: uuid.UUID, unit_id: str):
        service._require_project(project_id)
        return [job for job in production.repository.list_project_jobs(project_id)
                if job.frozen_input.get("productionUnitId") == unit_id]

    @router.get("/production-plans", response_model=list[ProductionPlanDto])
    def plans(project_id: uuid.UUID):
        service._require_project(project_id)
        return production.repository.list_production_plans(project_id)

    @router.post("/production-plans/preview")
    def preview_plan(project_id: uuid.UUID, command: ProductionPlanDraft):
        return production.preview_plan(project_id, command)

    @router.post("/production-plans", response_model=ProductionPlanDto)
    def create_plan(project_id: uuid.UUID, command: ProductionPlanDraft):
        return production.create_plan(project_id, command)

    @router.post("/production-plans/{plan_id}/activate", response_model=ProductionPlanDto)
    def activate_plan(project_id: uuid.UUID, plan_id: uuid.UUID, command: ProductionActivation):
        return production.repository.activate_production_plan(
            project_id, plan_id, command.expected_active_plan_id)

    @router.post("/production-units/{unit_id}/preview")
    def preview_unit(project_id: uuid.UUID, unit_id: str, command: UnitTarget):
        return production.preview_unit(project_id, unit_id, command.plan_id)

    @router.post("/production-units/{unit_id}/generations", response_model=JobDto,
                 status_code=202, dependencies=[Depends(require_worker)])
    def generate_unit(project_id: uuid.UUID, unit_id: str, command: UnitGeneration):
        return production.generate_unit(project_id, unit_id, command)

    @router.get("/production-units/{unit_id}/selections", response_model=list[UnitSelectionDto])
    def selections(project_id: uuid.UUID, unit_id: str):
        service._require_project(project_id)
        return production.repository.list_unit_selections(project_id, unit_id)

    @router.post("/production-units/{unit_id}/selections", response_model=UnitSelectionDto)
    def select_unit(project_id: uuid.UUID, unit_id: str, command: UnitSelectionCommand):
        return production.select_unit(project_id, unit_id, command)

    @router.post("/production-units/{unit_id}/evidence", response_model=JobDto,
                 status_code=202, dependencies=[Depends(require_worker)])
    def evidence(project_id: uuid.UUID, unit_id: str, command: UnitEvidenceCommand):
        return production.prepare_evidence(project_id, unit_id, command)

    @router.post("/production-plans/{plan_id}/assemble", response_model=VideoEditDraftDto)
    def assemble(project_id: uuid.UUID, plan_id: uuid.UUID, command: ProductionAssembly):
        return production.assemble(project_id, plan_id, command)

    return router
