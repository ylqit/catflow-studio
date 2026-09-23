"""Memory repository parity for production ownership and idempotent revisions."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from catflow.application.production_plan import ProductionPlanDto, document_hash
from catflow.application.production_selection import UnitSelectionDto
from catflow.application.job_execution import replacing_unknown
from catflow.application.service import StudioConflictError, StudioNotFoundError
from .memory_character_references import reference_transaction


class MemoryProduction:
    def _validate_production_assembly(self, project_id, guard):
        plan = self.get_production_plan(guard["planId"])
        if plan is None or not plan.active or plan.project_id != project_id:
            raise StudioConflictError("组装前生产计划已变化")
        self._require_production_sources(project_id, plan.document)
        for unit_id, expected in guard["selections"].items():
            selection = self.active_unit_selection(project_id, unit_id)
            if selection is None or selection.input_hash != expected:
                raise StudioConflictError("组装前选片已变化")
            self._require_production_upstream(project_id, selection.document)

    def list_production_plans(self, project_id):
        return sorted([p for p in self._production_plans.values() if p.project_id == project_id],
                      key=lambda p: p.revision, reverse=True)

    def get_production_plan(self, plan_id):
        return self._production_plans.get(plan_id)

    def production_plan_by_key(self, key):
        return next((p for p in self._production_plans.values() if p.idempotency_key == key), None)

    def _require_production_sources(self, project_id, document):
        project = self.get_project(project_id)
        shot = self.active_shot_plan(project_id)
        story = self.active_story(project_id)
        if (project is None or str(project.canon_profile_id) != document["canon"]["id"]
                or shot is None or str(shot.id) != document["shotPlanVersionId"]
                or story is None or str(story.id) != document["storyVersionId"]):
            raise StudioConflictError("生产来源已变化")
        for reference in [*document["canon"]["references"], *document["scenes"], *document["props"]]:
            asset = self.get_asset(uuid.UUID(reference["assetId"]))
            if asset is None or asset.sha256 != reference["sha256"]:
                raise StudioConflictError("生产参考已变化")

    def _require_production_upstream(self, project_id, document):
        upstream_id = document.get("upstreamSelectionId")
        if upstream_id:
            upstream = self._unit_selections.get(uuid.UUID(upstream_id))
            if (upstream is None or not upstream.active or upstream.project_id != project_id
                    or upstream.input_hash != document["upstreamSelectionHash"]):
                raise StudioConflictError("上游选片已变化")
            self._require_production_upstream(project_id, upstream.document)

    @reference_transaction
    def save_production_plan(self, project_id, command, document, request_hash):
        existing = self.production_plan_by_key(command.idempotency_key)
        if existing:
            if existing.request_hash != request_hash:
                raise StudioConflictError("幂等键已用于不同生产计划")
            return existing
        self._require_production_sources(project_id, document)
        plans = self.list_production_plans(project_id)
        current = next((p for p in plans if p.active), None)
        if (current.id if current else None) != command.expected_active_plan_id:
            raise StudioConflictError("当前生产计划已变化")
        record = ProductionPlanDto(id=uuid.uuid4(), projectId=project_id, revision=len(plans) + 1,
                                   active=False, inputHash=document_hash(document), requestHash=request_hash,
                                   idempotencyKey=command.idempotency_key, document=document,
                                   createdAt=datetime.now(UTC))
        self._production_plans[record.id] = record
        return record

    @reference_transaction
    def activate_production_plan(self, project_id, plan_id, expected_id):
        record = self.get_production_plan(plan_id)
        if record is None or record.project_id != project_id:
            raise StudioNotFoundError("production plan not found")
        if record.active:
            return record
        plans = self.list_production_plans(project_id)
        current = next((p for p in plans if p.active), None)
        if (current.id if current else None) != expected_id:
            raise StudioConflictError("当前生产计划已变化")
        self._require_production_sources(project_id, record.document)
        for plan in plans:
            plan.active = plan.id == record.id
        return record

    def list_unit_selections(self, project_id, unit_id):
        return sorted([s for s in self._unit_selections.values()
                       if s.project_id == project_id and s.unit_id == unit_id],
                      key=lambda s: s.revision, reverse=True)

    def active_unit_selection(self, project_id, unit_id):
        return next((s for s in self.list_unit_selections(project_id, unit_id) if s.active), None)

    def unit_selection_by_key(self, key):
        return next((s for s in self._unit_selections.values() if s.idempotency_key == key), None)

    @reference_transaction
    def save_unit_selection(self, project_id, unit_id, command, document, request_hash):
        existing = self.unit_selection_by_key(command.idempotency_key)
        if existing:
            if existing.request_hash != request_hash:
                raise StudioConflictError("幂等键已用于不同选片")
            return existing
        plan = self.get_production_plan(command.plan_id)
        if plan is None or not plan.active or plan.project_id != project_id:
            raise StudioConflictError("生产计划已变化")
        self._require_production_sources(project_id, plan.document)
        self._require_production_upstream(project_id, document)
        current = self.active_unit_selection(project_id, unit_id)
        if (current.id if current else None) != command.expected_selection_id:
            raise StudioConflictError("当前选片已变化")
        if current:
            current.active = False
        record = UnitSelectionDto(id=uuid.uuid4(), projectId=project_id, unitId=unit_id,
                                  revision=len(self.list_unit_selections(project_id, unit_id)) + 1, active=True,
                                  inputHash=document_hash(document), requestHash=request_hash,
                                  idempotencyKey=command.idempotency_key, document=document,
                                  createdAt=datetime.now(UTC))
        self._unit_selections[record.id] = record
        return record

    def _validate_production_job(self, job):
        frozen = job.frozen_input
        if frozen.get("purpose") == "production_prop":
            if any(j.frozen_input.get("productionPropKey") == frozen["productionPropKey"]
                   and j.status not in {"succeeded", "failed", "cancelled"} and not replacing_unknown(j.id)
                   for j in self.list_project_jobs(job.project_id)):
                raise StudioConflictError("此道具仍有运行或提交未知任务，请先恢复")
            return
        if frozen.get("purpose") != "production_unit":
            return
        plan = self.get_production_plan(uuid.UUID(frozen["productionPlanId"]))
        if (plan is None or not plan.active or plan.project_id != job.project_id
                or plan.input_hash != frozen["productionPlanHash"]):
            raise StudioConflictError("生产计划已变化")
        self._require_production_sources(job.project_id, plan.document)
        self._require_production_upstream(job.project_id, frozen)
        if any(j.frozen_input.get("productionUnitId") == frozen["productionUnitId"]
               and j.status not in {"succeeded", "failed", "cancelled"} and not replacing_unknown(j.id)
               for j in self.list_project_jobs(job.project_id)):
            raise StudioConflictError("此单元仍有运行或提交未知的任务，请先恢复")
