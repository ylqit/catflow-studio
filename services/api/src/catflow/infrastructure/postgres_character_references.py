"""Transactional ownership of character bindings, catalog and remakes."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.character_references import (
    CharacterRemakeCommand,
    CharacterRemakeDto,
    ReferenceBindingCommand,
    ReferenceBindingDto,
    ReferenceScope,
    remake_preview,
    remake_request_hash,
)
from catflow.application.series import SeriesCreateCommand
from catflow.application.service import (
    FIXED_CANON_ROLES,
    AuxiliaryCatReferenceDto,
    CatReferenceOptionDto,
    JobDto,
    StudioConflictError,
    StudioIdempotencyInputConflictError,
    StudioNotFoundError,
)

from .models import (
    AssetRecord,
    CanonProfileRecord,
    CatReferenceOptionRecord,
    CharacterRemakeRecord,
    JobRecord,
    LifePlannerMessageRecord,
    LifePlannerSessionRecord,
    ProjectRecord,
    SeriesEpisodeOutlineVersionRecord,
    SeriesEpisodeRecord,
    SeriesPlanVersionRecord,
    SeriesSourceBindingRecord,
    ShotPlanVersionRecord,
    StorySeriesRecord,
    StorySourceMaterializationRecord,
    StorySourceRelationSuggestionRecord,
    StorySourceUnitRecord,
    StoryVersionRecord,
)


def lock_reference_owner(session: Session, *, project_id=None, series_id=None):
    """Use one lock order for generation, manual production and binding changes."""
    if project_id is not None:
        episode = session.scalar(
            select(SeriesEpisodeRecord).where(SeriesEpisodeRecord.project_id == project_id)
        )
        if episode is not None:
            series_id = episode.series_id
    series = None
    projects = []
    if series_id is not None:
        series = session.scalar(
            select(StorySeriesRecord).where(StorySeriesRecord.id == series_id).with_for_update()
        )
        if series is None:
            raise StudioNotFoundError("series not found")
        ids = select(SeriesEpisodeRecord.project_id).where(
            SeriesEpisodeRecord.series_id == series_id
        )
        projects = list(
            session.scalars(
                select(ProjectRecord)
                .where(ProjectRecord.id.in_(ids))
                .order_by(ProjectRecord.id)
                .with_for_update()
            )
        )
    elif project_id is not None:
        project = session.scalar(
            select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
        )
        if project is None:
            raise StudioNotFoundError("project not found")
        projects = [project]
    return series, projects


def mark_reference_production(
    session: Session, *, project_id=None, series_id=None, job: JobDto | None = None
):
    series, projects = lock_reference_owner(session, project_id=project_id, series_id=series_id)
    owner = series or next((p for p in projects if p.id == project_id), None)
    if owner is None:
        return
    expected = job.frozen_input.get("canonProfileId") if job else None
    if expected is not None and str(owner.canon_profile_id) != str(expected):
        raise StudioConflictError("猫咪参考已变化，请重新预览后提交。")
    now = datetime.now(UTC)
    owner.production_started_at = owner.production_started_at or now
    for project in projects:
        if project.id == project_id:
            if project.canon_profile_id != owner.canon_profile_id:
                raise StudioConflictError("单集参考与系列不一致。")
            project.production_started_at = project.production_started_at or now


def _production_evidence(session: Session, series, projects):
    started = [p.production_started_at for p in projects if p.production_started_at]
    if series is not None and series.production_started_at:
        started.append(series.production_started_at)
    if started:
        return min(started)
    ids = [p.id for p in projects]
    jobs = session.scalar(
        select(JobRecord.created_at)
        .where(
            or_(
                JobRecord.project_id.in_(ids),
                JobRecord.series_id == series.id if series is not None else False,
            )
        )
        .order_by(JobRecord.created_at)
        .limit(1)
    )
    if jobs:
        return jobs
    for model in (StoryVersionRecord, ShotPlanVersionRecord, AssetRecord):
        value = session.scalar(
            select(model.created_at)
            .where(model.project_id.in_(ids))
            .order_by(model.created_at)
            .limit(1)
        )
        if value:
            return value
    if series is not None:
        return session.scalar(
            select(SeriesPlanVersionRecord.created_at)
            .where(SeriesPlanVersionRecord.series_id == series.id)
            .limit(1)
        )
    return None


def _source_snapshot(session: Session, scope: ReferenceScope, object_id: uuid.UUID):
    sources = []
    messages = []
    if scope == "series":
        record = session.get(StorySeriesRecord, object_id)
        if record is None:
            raise StudioNotFoundError("source series not found")
        values = {
            key: getattr(
                record,
                key + "_json" if key in {"recurring_elements", "must_keep", "must_avoid"} else key,
            )
            for key in SeriesCreateCommand.model_fields
            if key != "canon_profile_id"
        }
        draft = SeriesCreateCommand(**values)
        input_document = draft.model_dump(mode="json", by_alias=True, exclude={"canon_profile_id"})
        bindings = session.execute(
            select(SeriesSourceBindingRecord, StorySourceUnitRecord)
            .join(
                StorySourceUnitRecord,
                StorySourceUnitRecord.id == SeriesSourceBindingRecord.source_unit_id,
            )
            .where(SeriesSourceBindingRecord.series_id == object_id)
            .order_by(SeriesSourceBindingRecord.binding_order)
        ).all()
        sources = [
            {
                "unitId": str(unit.id),
                "title": unit.title,
                "rawText": unit.raw_text,
                "ordinal": binding.source_ordinal,
                "bindingOrder": binding.binding_order,
            }
            for binding, unit in bindings
        ]
    else:
        record = session.get(ProjectRecord, object_id)
        if record is None:
            raise StudioNotFoundError("source project not found")
        input_document = {
            "title": record.title,
            "theme": record.theme,
            "targetDurationSeconds": record.target_duration_seconds,
        }
        messages = list(
            session.scalars(
                select(LifePlannerMessageRecord.content)
                .join(
                    LifePlannerSessionRecord,
                    LifePlannerSessionRecord.id == LifePlannerMessageRecord.session_id,
                )
                .where(
                    LifePlannerSessionRecord.project_id == object_id,
                    LifePlannerMessageRecord.role == "user",
                )
                .order_by(LifePlannerMessageRecord.ordinal)
            )
        )
        materials = session.scalars(
            select(StorySourceMaterializationRecord).where(
                or_(
                    StorySourceMaterializationRecord.project_id == object_id,
                    StorySourceMaterializationRecord.target_project_id == object_id,
                    StorySourceMaterializationRecord.project_ids_json.contains([str(object_id)]),
                )
            )
        ).all()
        unit_ids = set()
        for material in materials:
            suggestion = session.get(StorySourceRelationSuggestionRecord, material.suggestion_id)
            if suggestion:
                unit_ids.update(uuid.UUID(str(value)) for value in suggestion.unit_ids_json)
        episode = session.scalar(
            select(SeriesEpisodeRecord).where(SeriesEpisodeRecord.project_id == object_id)
        )
        if episode:
            outline = session.scalar(
                select(SeriesEpisodeOutlineVersionRecord).where(
                    SeriesEpisodeOutlineVersionRecord.episode_id == episode.id,
                    SeriesEpisodeOutlineVersionRecord.active.is_(True),
                )
            )
            ordinals = (
                {c["sourceUnitOrdinal"] for c in outline.outline_json.get("sourceCoverage", [])}
                if outline
                else set()
            )
            unit_ids.update(
                session.scalars(
                    select(SeriesSourceBindingRecord.source_unit_id).where(
                        SeriesSourceBindingRecord.series_id == episode.series_id,
                        SeriesSourceBindingRecord.binding_order.in_(ordinals),
                    )
                )
            )
        for unit in session.scalars(
            select(StorySourceUnitRecord)
            .where(StorySourceUnitRecord.id.in_(unit_ids))
            .order_by(StorySourceUnitRecord.ordinal, StorySourceUnitRecord.id)
        ):
            sources.append(
                {
                    "unitId": str(unit.id),
                    "title": unit.title,
                    "rawText": unit.raw_text,
                    "ordinal": unit.ordinal,
                }
            )
        prior = session.scalar(
            select(CharacterRemakeRecord).where(
                CharacterRemakeRecord.source_type == "project",
                CharacterRemakeRecord.target_id == object_id,
            )
        )
        if prior:
            sources = deepcopy(prior.source_snapshot_json.get("sources", [])) + sources
            messages = list(prior.source_snapshot_json.get("userMessages", [])) + messages
    return {
        "input": input_document,
        "sources": sources,
        "userMessages": messages,
        "sourceCanonProfileId": str(record.canon_profile_id),
    }


class PostgresCharacterReferences:
    _sessions: sessionmaker[Session]

    def list_cat_reference_options(self):
        from .postgres_repository import _asset_dto, _canon_profile_dto

        with self._sessions() as session:
            result = []
            for record in session.scalars(
                select(CatReferenceOptionRecord).order_by(CatReferenceOptionRecord.sort_order)
            ):
                profile = (
                    session.get(CanonProfileRecord, record.canon_profile_id)
                    if record.canon_profile_id
                    else None
                )
                canon = _canon_profile_dto(session, profile) if profile else None
                auxiliary = []
                for item in record.auxiliary_json:
                    asset = session.get(AssetRecord, uuid.UUID(item["assetId"]))
                    if asset:
                        auxiliary.append(
                            AuxiliaryCatReferenceDto(view=item["view"], asset=_asset_dto(asset))
                        )
                result.append(
                    CatReferenceOptionDto(
                        key=record.key,
                        label=record.label,
                        canonProfileId=record.canon_profile_id,
                        profileHash=canon.profile_hash if canon else None,
                        version=canon.version if canon else None,
                        catIdentity=canon.cat_identity_prompt if canon else "",
                        fixedAssets=canon.fixed_assets if canon else {},
                        auxiliary=auxiliary,
                        available=canon is not None and not record.unavailable_reason,
                        unavailableReason=record.unavailable_reason,
                    )
                )
            return result

    def save_cat_reference_option(
        self, *, key, label, canon_profile_id, auxiliary, sort_order, unavailable_reason=None
    ):
        with self._sessions.begin() as session:
            record = session.get(CatReferenceOptionRecord, key)
            if record is None:
                record = CatReferenceOptionRecord(key=key)
                session.add(record)
            record.label, record.canon_profile_id = label, canon_profile_id
            record.auxiliary_json, record.sort_order = auxiliary, sort_order
            record.unavailable_reason = unavailable_reason

    def list_canon_profiles(self):
        from .postgres_repository import _canon_profile_dto

        with self._sessions() as session:
            return [
                _canon_profile_dto(session, record)
                for record in session.scalars(
                    select(CanonProfileRecord).order_by(CanonProfileRecord.version.desc())
                )
            ]

    def get_reference_binding(self, scope, object_id):
        from .postgres_repository import _canon_profile_dto

        with self._sessions.begin() as session:
            series, projects = lock_reference_owner(session, **{scope + "_id": object_id})
            record = (
                series
                if scope == "series"
                else next((p for p in projects if p.id == object_id), None)
            )
            if record is None:
                raise StudioNotFoundError("reference owner not found")
            started = _production_evidence(session, series, projects)
            inherited = scope == "project" and series is not None
            profile = session.get(CanonProfileRecord, record.canon_profile_id)
            canon = _canon_profile_dto(session, profile)
            option = session.scalar(
                select(CatReferenceOptionRecord).where(
                    CatReferenceOptionRecord.canon_profile_id == profile.id
                )
            )
            return ReferenceBindingDto(
                scope=scope,
                objectId=object_id,
                canonProfileId=profile.id,
                label=option.label if option else "历史参考设定",
                catIdentity=canon.cat_identity_prompt,
                canChange=not inherited and started is None,
                blockedReason="单集继承本系列的猫咪参考。"
                if inherited
                else "已经开始制作，请用其他猫咪重制。"
                if started
                else None,
                ownerSeriesId=series.id if inherited else None,
                productionStartedAt=started,
            )

    def change_reference_binding(self, scope, object_id, command: ReferenceBindingCommand):
        with self._sessions.begin() as session:
            series, projects = lock_reference_owner(session, **{scope + "_id": object_id})
            if scope == "project" and series is not None:
                raise StudioConflictError("单集继承本系列的猫咪参考，不能独立更换。")
            owner = series if scope == "series" else projects[0]
            if owner.canon_profile_id == command.canon_profile_id:
                return
            if _production_evidence(session, series, projects):
                raise StudioConflictError("已经开始制作，请用其他猫咪重制。")
            if owner.canon_profile_id != command.expected_canon_profile_id:
                raise StudioConflictError("猫咪参考已变化，请刷新后重试。")
            target = session.get(CanonProfileRecord, command.canon_profile_id)
            if target is None or set(target.profile_json.get("fixedAssets", {})) != set(
                FIXED_CANON_ROLES
            ):
                raise StudioConflictError("目标参考不存在或不完整。")
            owner.canon_profile_id = command.canon_profile_id
            owner.updated_at = datetime.now(UTC)
            for project in projects:
                project.canon_profile_id = command.canon_profile_id
                project.updated_at = owner.updated_at

    def character_remake_source(self, scope, object_id):
        with self._sessions() as session:
            return _source_snapshot(session, scope, object_id)

    def remake_input_context(self, project_id):
        with self._sessions() as session:
            record = session.scalar(
                select(CharacterRemakeRecord).where(
                    CharacterRemakeRecord.source_type == "project",
                    CharacterRemakeRecord.target_id == project_id,
                )
            )
            return deepcopy(record.source_snapshot_json) if record else None

    def create_character_remake(self, command: CharacterRemakeCommand):
        from .postgres_repository import _canon_profile_dto

        request_hash = remake_request_hash(command)
        with self._sessions.begin() as session:
            # Serialize identical local creation requests before allocating target IDs.
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "character-remake:" + command.idempotency_key},
            )
            existing = session.scalar(
                select(CharacterRemakeRecord).where(
                    CharacterRemakeRecord.idempotency_key == command.idempotency_key
                )
            )
            if existing:
                if existing.request_hash != request_hash:
                    raise StudioIdempotencyInputConflictError(
                        "idempotency key already belongs to different input"
                    )
                return CharacterRemakeDto(
                    id=existing.id,
                    sourceType=existing.source_type,
                    sourceId=existing.source_id,
                    targetId=existing.target_id,
                    canonProfileId=existing.canon_profile_id,
                    createdAt=existing.created_at,
                )
            lock_reference_owner(session, **{command.source_type + "_id": command.source_id})
            profile = session.get(CanonProfileRecord, command.canon_profile_id)
            if profile is None or set(profile.profile_json.get("fixedAssets", {})) != set(
                FIXED_CANON_ROLES
            ):
                raise StudioConflictError("目标参考不存在或不完整。")
            canon = _canon_profile_dto(session, profile)
            option = session.scalar(
                select(CatReferenceOptionRecord).where(
                    CatReferenceOptionRecord.canon_profile_id == profile.id
                )
            )
            snapshot = _source_snapshot(session, command.source_type, command.source_id)
            preview = remake_preview(
                command,
                snapshot,
                canon_hash=canon.profile_hash,
                label=option.label if option else "历史参考设定",
            )
            if preview.input_hash != command.expected_input_hash:
                raise StudioConflictError("重制来源或目标已变化，请重新预览。")
            values = dict(snapshot["input"])
            values["title"] = preview.title
            if command.source_type == "series":
                draft = SeriesCreateCommand(**values)
                fields = draft.model_dump(exclude={"canon_profile_id"})
                for name in ("recurring_elements", "must_keep", "must_avoid"):
                    fields[name + "_json"] = fields.pop(name)
                target = StorySeriesRecord(**fields, canon_profile_id=profile.id)
            else:
                target = ProjectRecord(
                    title=values["title"],
                    theme=values["theme"],
                    target_duration_seconds=values["targetDurationSeconds"],
                    aspect_ratio="9:16",
                    canon_profile_id=profile.id,
                )
            session.add(target)
            session.flush()
            if command.source_type == "series":
                for source in snapshot["sources"]:
                    session.add(
                        SeriesSourceBindingRecord(
                            series_id=target.id,
                            source_unit_id=uuid.UUID(source["unitId"]),
                            source_ordinal=source["ordinal"],
                            binding_order=source["bindingOrder"],
                        )
                    )
            else:
                session.add(LifePlannerSessionRecord(project_id=target.id, context_revision=1))
            record = CharacterRemakeRecord(
                source_type=command.source_type,
                source_id=command.source_id,
                target_id=target.id,
                canon_profile_id=profile.id,
                source_snapshot_json=snapshot,
                request_hash=request_hash,
                idempotency_key=command.idempotency_key,
            )
            session.add(record)
            session.flush()
            return CharacterRemakeDto(
                id=record.id,
                sourceType=record.source_type,
                sourceId=record.source_id,
                targetId=record.target_id,
                canonProfileId=record.canon_profile_id,
                createdAt=record.created_at,
            )
