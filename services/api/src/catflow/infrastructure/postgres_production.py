"""Production persistence; every mutation shares the character-owner lock order."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update, text

from catflow.application.production_plan import ProductionPlanDto, document_hash
from catflow.application.production_selection import UnitSelectionDto
from catflow.application.job_execution import replacing_unknown
from catflow.application.service import StudioConflictError, StudioNotFoundError
from .models import AssetRecord, JobRecord, ProjectRecord, ShotPlanVersionRecord, StoryVersionRecord
from .postgres_character_references import lock_reference_owner
from .production_models import ProductionPlanRecord, ProductionSelectionRecord


def plan_dto(record):
    return ProductionPlanDto(id=record.id, projectId=record.project_id, revision=record.revision,
                             active=record.active, inputHash=record.input_hash, requestHash=record.request_hash,
                             idempotencyKey=record.idempotency_key, document=record.document_json,
                             createdAt=record.created_at)


def selection_dto(record):
    return UnitSelectionDto(id=record.id, projectId=record.project_id, unitId=record.unit_id,
                            revision=record.revision, active=record.active, inputHash=record.input_hash,
                            requestHash=record.request_hash, idempotencyKey=record.idempotency_key,
                            document=record.document_json, createdAt=record.created_at)


def require_sources(session, project_id, document):
    project = session.get(ProjectRecord, project_id)
    shot = session.get(ShotPlanVersionRecord, uuid.UUID(document["shotPlanVersionId"]))
    story = session.get(StoryVersionRecord, uuid.UUID(document["storyVersionId"]))
    if (project is None or str(project.canon_profile_id) != document["canon"]["id"]
            or shot is None or not shot.active or shot.project_id != project_id
            or story is None or not story.active or story.project_id != project_id):
        raise StudioConflictError("生产来源已变化，请重新预览")
    for reference in [*document["canon"]["references"], *document["scenes"], *document["props"]]:
        asset = session.get(AssetRecord, uuid.UUID(reference["assetId"]))
        if asset is None or asset.sha256 != reference["sha256"]:
            raise StudioConflictError("生产参考已变化")


def require_upstream(session, project_id, document):
    upstream_id = document.get("upstreamSelectionId")
    if upstream_id:
        upstream = session.get(ProductionSelectionRecord, uuid.UUID(upstream_id))
        if (upstream is None or not upstream.active or upstream.project_id != project_id
                or upstream.input_hash != document["upstreamSelectionHash"]):
            raise StudioConflictError("上游选片已变化，请重新预览")
        require_upstream(session, project_id, upstream.document_json)


def validate_production_job(session, job):
    """Called inside create_job, after the shared owner lock and idempotent replay."""
    frozen = job.frozen_input
    if frozen.get("purpose") == "production_prop":
        running = session.scalars(select(JobRecord.id).where(
            JobRecord.project_id == job.project_id,
            JobRecord.frozen_input_json["productionPropKey"].astext == frozen["productionPropKey"],
            JobRecord.status.notin_(["succeeded", "failed", "cancelled"])))
        if any(not replacing_unknown(job_id) for job_id in running):
            raise StudioConflictError("此道具仍有运行或提交未知任务，请先恢复")
        return
    if frozen.get("purpose") != "production_unit":
        return
    plan = session.get(ProductionPlanRecord, uuid.UUID(frozen["productionPlanId"]))
    if (plan is None or not plan.active or plan.project_id != job.project_id
            or plan.input_hash != frozen["productionPlanHash"]):
        raise StudioConflictError("生产计划已变化，请重新预览")
    require_sources(session, job.project_id, plan.document_json)
    require_upstream(session, job.project_id, frozen)
    running = session.scalars(select(JobRecord.id).where(
        JobRecord.project_id == job.project_id,
        JobRecord.frozen_input_json["productionUnitId"].astext == frozen["productionUnitId"],
        JobRecord.status.notin_(["succeeded", "failed", "cancelled"])))
    if any(not replacing_unknown(job_id) for job_id in running):
        raise StudioConflictError("此单元仍有运行或提交未知的任务，请先恢复")


def validate_production_assembly(session, project_id, guard):
    plan = session.get(ProductionPlanRecord, guard["planId"])
    if plan is None or not plan.active or plan.project_id != project_id:
        raise StudioConflictError("组装前生产计划已变化")
    require_sources(session, project_id, plan.document_json)
    for unit_id, expected in guard["selections"].items():
        selection = session.scalar(select(ProductionSelectionRecord).where(
            ProductionSelectionRecord.project_id == project_id,
            ProductionSelectionRecord.unit_id == unit_id, ProductionSelectionRecord.active.is_(True)))
        if selection is None or selection.input_hash != expected:
            raise StudioConflictError("组装前选片已变化")
        require_upstream(session, project_id, selection.document_json)


class PostgresProduction:
    def list_production_plans(self, project_id):
        with self._sessions() as session:
            return [plan_dto(r) for r in session.scalars(select(ProductionPlanRecord).where(
                ProductionPlanRecord.project_id == project_id).order_by(ProductionPlanRecord.revision.desc()))]

    def get_production_plan(self, plan_id):
        with self._sessions() as session:
            record = session.get(ProductionPlanRecord, plan_id)
            return plan_dto(record) if record else None

    def production_plan_by_key(self, key):
        with self._sessions() as session:
            record = session.scalar(select(ProductionPlanRecord).where(ProductionPlanRecord.idempotency_key == key))
            return plan_dto(record) if record else None

    def save_production_plan(self, project_id, command, document, request_hash):
        with self._sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                            {"key": "production-plan:" + command.idempotency_key})
            lock_reference_owner(session, project_id=project_id)
            existing = session.scalar(select(ProductionPlanRecord).where(
                ProductionPlanRecord.idempotency_key == command.idempotency_key))
            if existing:
                if existing.request_hash != request_hash:
                    raise StudioConflictError("幂等键已用于不同生产计划")
                return plan_dto(existing)
            require_sources(session, project_id, document)
            current = session.scalar(select(ProductionPlanRecord).where(
                ProductionPlanRecord.project_id == project_id, ProductionPlanRecord.active.is_(True)))
            if (current.id if current else None) != command.expected_active_plan_id:
                raise StudioConflictError("当前生产计划已变化")
            revision = session.scalar(select(func.coalesce(func.max(ProductionPlanRecord.revision), 0)).where(
                ProductionPlanRecord.project_id == project_id))
            record = ProductionPlanRecord(id=uuid.uuid4(), project_id=project_id,
                                          shot_plan_version_id=command.shot_plan_version_id, revision=revision + 1,
                                          active=False,
                                          input_hash=document_hash(document), request_hash=request_hash,
                                          idempotency_key=command.idempotency_key, document_json=document,
                                          created_at=datetime.now(UTC))
            session.add(record)
            session.flush()
            return plan_dto(record)

    def activate_production_plan(self, project_id, plan_id, expected_id):
        with self._sessions.begin() as session:
            lock_reference_owner(session, project_id=project_id)
            record = session.get(ProductionPlanRecord, plan_id)
            if record is None or record.project_id != project_id:
                raise StudioNotFoundError("production plan not found")
            if record.active:
                return plan_dto(record)
            current = session.scalar(select(ProductionPlanRecord.id).where(
                ProductionPlanRecord.project_id == project_id, ProductionPlanRecord.active.is_(True)))
            if current != expected_id:
                raise StudioConflictError("当前生产计划已变化")
            require_sources(session, project_id, record.document_json)
            session.execute(update(ProductionPlanRecord).where(
                ProductionPlanRecord.project_id == project_id).values(active=False))
            session.flush()
            record.active = True
            session.flush()
            return plan_dto(record)

    def list_unit_selections(self, project_id, unit_id):
        with self._sessions() as session:
            return [selection_dto(r) for r in session.scalars(select(ProductionSelectionRecord).where(
                ProductionSelectionRecord.project_id == project_id,
                ProductionSelectionRecord.unit_id == unit_id).order_by(ProductionSelectionRecord.revision.desc()))]

    def active_unit_selection(self, project_id, unit_id):
        with self._sessions() as session:
            record = session.scalar(select(ProductionSelectionRecord).where(
                ProductionSelectionRecord.project_id == project_id,
                ProductionSelectionRecord.unit_id == unit_id, ProductionSelectionRecord.active.is_(True)))
            return selection_dto(record) if record else None

    def unit_selection_by_key(self, key):
        with self._sessions() as session:
            record = session.scalar(
                select(ProductionSelectionRecord).where(ProductionSelectionRecord.idempotency_key == key))
            return selection_dto(record) if record else None

    def save_unit_selection(self, project_id, unit_id, command, document, request_hash):
        with self._sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                            {"key": "unit-selection:" + command.idempotency_key})
            lock_reference_owner(session, project_id=project_id)
            existing = session.scalar(select(ProductionSelectionRecord).where(
                ProductionSelectionRecord.idempotency_key == command.idempotency_key))
            if existing:
                if existing.request_hash != request_hash:
                    raise StudioConflictError("幂等键已用于不同选片")
                return selection_dto(existing)
            plan = session.get(ProductionPlanRecord, command.plan_id)
            if plan is None or not plan.active or plan.project_id != project_id:
                raise StudioConflictError("生产计划已变化")
            require_sources(session, project_id, plan.document_json)
            require_upstream(session, project_id, document)
            current = session.scalar(select(ProductionSelectionRecord).where(
                ProductionSelectionRecord.project_id == project_id,
                ProductionSelectionRecord.unit_id == unit_id, ProductionSelectionRecord.active.is_(True)))
            if (current.id if current else None) != command.expected_selection_id:
                raise StudioConflictError("当前选片已变化")
            revision = session.scalar(select(func.coalesce(func.max(ProductionSelectionRecord.revision), 0)).where(
                ProductionSelectionRecord.project_id == project_id, ProductionSelectionRecord.unit_id == unit_id))
            if current:
                current.active = False
                session.flush()
            record = ProductionSelectionRecord(id=uuid.uuid4(), project_id=project_id, unit_id=unit_id,
                                               plan_id=command.plan_id, asset_id=command.asset_id,
                                               revision=revision + 1, active=True,
                                               input_hash=document_hash(document), request_hash=request_hash,
                                               idempotency_key=command.idempotency_key, document_json=document,
                                               created_at=datetime.now(UTC))
            session.add(record)
            session.flush()
            return selection_dto(record)
