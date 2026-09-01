from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.service import (
    AssetDto,
    EditDecisionListDto,
    EditVersionDto,
    JobDto,
    JobEventDto,
    LifeStoryProposalDto,
    PlannerMessageCommand,
    PlannerMessageDto,
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
)
from catflow.domain.models import LifeStoryProposalDraft, MicroEvent, ShotPlanDraft, ShotSpec

from .database import ensure_canon_v4
from .models import (
    AssetRecord,
    EditVersionRecord,
    JobEventRecord,
    JobRecord,
    LifePlannerMessageRecord,
    LifePlannerProposalRecord,
    LifePlannerSessionRecord,
    ProjectRecord,
    ProjectSelectionRecord,
    ShotPlanVersionRecord,
    StoryVersionRecord,
)


class PostgresStudioRepository:
    """PostgreSQL owns every durable CatFlow business fact and transaction boundary."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def active_canon_profile_id(self) -> uuid.UUID:
        with self._sessions.begin() as session:
            return ensure_canon_v4(session).id

    def create_project(self, draft: ProjectCreate, *, canon_profile_id: uuid.UUID) -> ProjectDto:
        with self._sessions.begin() as session:
            record = ProjectRecord(
                title=draft.title,
                theme=draft.theme,
                target_duration_seconds=draft.target_duration_seconds,
                aspect_ratio="9:16",
                canon_profile_id=canon_profile_id,
            )
            session.add(record)
            session.flush()
            session.add(LifePlannerSessionRecord(project_id=record.id, context_revision=1))
            session.flush()
            return _project_dto(record)

    def list_projects(self) -> list[ProjectDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(ProjectRecord).order_by(ProjectRecord.created_at.desc())
            ).all()
            return [_project_dto(record) for record in records]

    def get_project(self, project_id: uuid.UUID) -> ProjectDto | None:
        with self._sessions() as session:
            record = session.get(ProjectRecord, project_id)
            return _project_dto(record) if record is not None else None

    def update_project(self, project_id: uuid.UUID, patch: ProjectPatch) -> ProjectDto:
        with self._sessions.begin() as session:
            project = session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            if project is None:
                raise StudioNotFoundError("project not found")
            if patch.title is not None:
                project.title = patch.title
            if patch.theme is not None:
                project.theme = patch.theme
            if patch.target_duration_seconds is not None:
                project.target_duration_seconds = patch.target_duration_seconds
            project.updated_at = datetime.now(UTC)
            planner = session.scalar(
                select(LifePlannerSessionRecord)
                .where(LifePlannerSessionRecord.project_id == project_id)
                .with_for_update()
            )
            if planner is not None:
                planner.context_revision += 1
            session.execute(
                update(LifePlannerProposalRecord)
                .where(
                    LifePlannerProposalRecord.project_id == project_id,
                    LifePlannerProposalRecord.status == "draft",
                )
                .values(status="outdated")
            )
            session.flush()
            return _project_dto(project)

    def planner_snapshot(self, project_id: uuid.UUID) -> PlannerSnapshotDto:
        with self._sessions() as session:
            planner_session = session.scalar(
                select(LifePlannerSessionRecord).where(
                    LifePlannerSessionRecord.project_id == project_id
                )
            )
            if planner_session is None:
                raise StudioNotFoundError("planner session not found")
            messages = session.scalars(
                select(LifePlannerMessageRecord)
                .where(LifePlannerMessageRecord.session_id == planner_session.id)
                .order_by(LifePlannerMessageRecord.ordinal)
            ).all()
            proposals = session.scalars(
                select(LifePlannerProposalRecord)
                .where(LifePlannerProposalRecord.project_id == project_id)
                .order_by(LifePlannerProposalRecord.created_at, LifePlannerProposalRecord.id)
            ).all()
            return PlannerSnapshotDto(
                sessionId=planner_session.id,
                projectId=project_id,
                contextRevision=planner_session.context_revision,
                messages=[_planner_message_dto(record) for record in messages],
                proposals=[_proposal_dto(record) for record in proposals],
            )

    def enqueue_planner_message(
        self, project_id: uuid.UUID, command: PlannerMessageCommand, *, input_hash: str
    ) -> JobDto:
        with self._sessions.begin() as session:
            existing = _job_by_idempotency(session, command.idempotency_key)
            if existing is not None:
                _require_same_input(existing, input_hash)
                return _job_dto(session, existing)

            planner_session = session.scalar(
                select(LifePlannerSessionRecord)
                .where(LifePlannerSessionRecord.project_id == project_id)
                .with_for_update()
            )
            if planner_session is None:
                raise StudioNotFoundError("planner session not found")
            project = session.get(ProjectRecord, project_id)
            if project is None:
                raise StudioNotFoundError("project not found")
            ordinal = session.scalar(
                select(func.coalesce(func.max(LifePlannerMessageRecord.ordinal), 0)).where(
                    LifePlannerMessageRecord.session_id == planner_session.id
                )
            )
            now = datetime.now(UTC)
            session.add(
                LifePlannerMessageRecord(
                    session_id=planner_session.id,
                    ordinal=int(ordinal or 0) + 1,
                    role="user",
                    content=command.text,
                )
            )
            job = JobRecord(
                project_id=project_id,
                kind="plan_story",
                status="queued",
                input_hash=input_hash,
                idempotency_key=command.idempotency_key,
                provider="fake",
                model="catflow-fake-planner-v1",
                expected_cost_micros=0,
                frozen_input_json={
                    "text": command.text,
                    "contextRevision": command.expected_context_revision,
                    "sessionId": str(planner_session.id),
                    "targetDurationSeconds": project.target_duration_seconds,
                },
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            session.flush()
            _add_job_event(session, job, "job.queued")
            return _job_dto(session, job)

    def complete_planner_job(
        self, job_id: uuid.UUID, proposal: LifeStoryProposalDraft
    ) -> LifeStoryProposalDto:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRecord).where(JobRecord.id == job_id).with_for_update())
            if job is None:
                raise StudioNotFoundError("job not found")
            if job.kind != "plan_story":
                raise StudioConflictError("job is not a planner job")
            existing = session.scalar(
                select(LifePlannerProposalRecord).where(
                    LifePlannerProposalRecord.project_id == job.project_id,
                    LifePlannerProposalRecord.context_hash == job.input_hash,
                )
            )
            if existing is not None:
                return _proposal_dto(existing)

            planner_session = session.scalar(
                select(LifePlannerSessionRecord)
                .where(LifePlannerSessionRecord.project_id == job.project_id)
                .with_for_update()
            )
            if planner_session is None:
                raise StudioNotFoundError("planner session not found")
            ordinal = session.scalar(
                select(func.coalesce(func.max(LifePlannerMessageRecord.ordinal), 0)).where(
                    LifePlannerMessageRecord.session_id == planner_session.id
                )
            )
            proposal_record = LifePlannerProposalRecord(
                session_id=planner_session.id,
                project_id=job.project_id,
                status="draft",
                context_hash=job.input_hash,
                proposal_json={
                    "title": proposal.title,
                    "summary": proposal.summary,
                    "body": proposal.body,
                    "microEvent": proposal.micro_event.model_dump(mode="json", by_alias=True),
                    "targetDurationSeconds": proposal.target_duration_seconds,
                    "dialoguePolicy": proposal.dialogue_policy,
                    "environmentIntent": proposal.environment_intent,
                    "propIntent": proposal.prop_intent,
                    "warnings": [],
                },
            )
            session.add(proposal_record)
            session.add(
                LifePlannerMessageRecord(
                    session_id=planner_session.id,
                    ordinal=int(ordinal or 0) + 1,
                    role="assistant",
                    content=proposal.summary,
                )
            )
            job.status = "succeeded"
            job.updated_at = datetime.now(UTC)
            session.flush()
            _add_job_event(
                session,
                job,
                "planner.proposal.created",
                {"proposalId": str(proposal_record.id)},
            )
            return _proposal_dto(proposal_record)

    def adopt_proposal(self, project_id: uuid.UUID, proposal_id: uuid.UUID) -> StoryVersionDto:
        with self._sessions.begin() as session:
            session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            proposal = session.get(LifePlannerProposalRecord, proposal_id)
            if proposal is None or proposal.project_id != project_id:
                raise StudioNotFoundError("proposal not found")
            existing = session.scalar(
                select(StoryVersionRecord).where(
                    StoryVersionRecord.project_id == project_id,
                    StoryVersionRecord.source_proposal_id == proposal_id,
                )
            )
            if existing is not None:
                return _story_dto(existing)
            revision = session.scalar(
                select(func.coalesce(func.max(StoryVersionRecord.revision), 0)).where(
                    StoryVersionRecord.project_id == project_id
                )
            )
            session.execute(
                update(StoryVersionRecord)
                .where(
                    StoryVersionRecord.project_id == project_id,
                    StoryVersionRecord.active.is_(True),
                )
                .values(active=False)
            )
            payload = proposal.proposal_json
            story = StoryVersionRecord(
                project_id=project_id,
                revision=int(revision or 0) + 1,
                source_proposal_id=proposal.id,
                title=str(payload["title"]),
                body=str(payload["body"]),
                micro_event_json=dict(payload["microEvent"]),
                target_duration_seconds=int(payload["targetDurationSeconds"]),
                dialogue_policy=str(payload["dialoguePolicy"]),
                environment_intent=str(payload["environmentIntent"]),
                active=True,
            )
            proposal.status = "adopted"
            proposal.adopted_at = datetime.now(UTC)
            session.add(story)
            session.flush()
            return _story_dto(story)

    def active_story(self, project_id: uuid.UUID) -> StoryVersionDto | None:
        with self._sessions() as session:
            record = session.scalar(
                select(StoryVersionRecord).where(
                    StoryVersionRecord.project_id == project_id,
                    StoryVersionRecord.active.is_(True),
                )
            )
            return _story_dto(record) if record is not None else None

    def list_stories(self, project_id: uuid.UUID) -> list[StoryVersionDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(StoryVersionRecord)
                .where(StoryVersionRecord.project_id == project_id)
                .order_by(StoryVersionRecord.revision.desc())
            ).all()
            return [_story_dto(record) for record in records]

    def create_story(self, project_id: uuid.UUID, command: StoryCreateCommand) -> StoryVersionDto:
        with self._sessions.begin() as session:
            project = session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            if project is None:
                raise StudioNotFoundError("project not found")
            revision = session.scalar(
                select(func.coalesce(func.max(StoryVersionRecord.revision), 0)).where(
                    StoryVersionRecord.project_id == project_id
                )
            )
            session.execute(
                update(StoryVersionRecord)
                .where(
                    StoryVersionRecord.project_id == project_id,
                    StoryVersionRecord.active.is_(True),
                )
                .values(active=False)
            )
            record = StoryVersionRecord(
                project_id=project_id,
                revision=int(revision or 0) + 1,
                title=command.title,
                body=command.body,
                micro_event_json=command.micro_event.model_dump(mode="json", by_alias=True),
                target_duration_seconds=command.target_duration_seconds,
                dialogue_policy=command.dialogue_policy,
                environment_intent=command.environment_intent,
                active=True,
            )
            session.add(record)
            session.flush()
            return _story_dto(record)

    def activate_story(self, project_id: uuid.UUID, story_id: uuid.UUID) -> StoryVersionDto:
        with self._sessions.begin() as session:
            session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            record = session.get(StoryVersionRecord, story_id)
            if record is None or record.project_id != project_id:
                raise StudioNotFoundError("story version not found")
            session.execute(
                update(StoryVersionRecord)
                .where(StoryVersionRecord.project_id == project_id)
                .values(active=False)
            )
            record.active = True
            session.flush()
            return _story_dto(record)

    def create_shot_plan(self, project_id: uuid.UUID, draft: ShotPlanDraft) -> ShotPlanVersionDto:
        with self._sessions.begin() as session:
            session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            revision = session.scalar(
                select(func.coalesce(func.max(ShotPlanVersionRecord.revision), 0)).where(
                    ShotPlanVersionRecord.project_id == project_id
                )
            )
            session.execute(
                update(ShotPlanVersionRecord)
                .where(
                    ShotPlanVersionRecord.project_id == project_id,
                    ShotPlanVersionRecord.active.is_(True),
                )
                .values(active=False)
            )
            record = ShotPlanVersionRecord(
                project_id=project_id,
                revision=int(revision or 0) + 1,
                source_story_version_id=draft.source_story_version_id,
                source_selection_hash=draft.source_selection_hash,
                clip_json=draft.clip.model_dump(mode="json", by_alias=True),
                shots_json=[shot.model_dump(mode="json", by_alias=True) for shot in draft.shots],
                total_duration_seconds=draft.total_duration_seconds,
                active=True,
            )
            session.add(record)
            session.flush()
            return _shot_plan_dto(record)

    def active_shot_plan(self, project_id: uuid.UUID) -> ShotPlanVersionDto | None:
        with self._sessions() as session:
            record = session.scalar(
                select(ShotPlanVersionRecord).where(
                    ShotPlanVersionRecord.project_id == project_id,
                    ShotPlanVersionRecord.active.is_(True),
                )
            )
            return _shot_plan_dto(record) if record is not None else None

    def list_shot_plans(self, project_id: uuid.UUID) -> list[ShotPlanVersionDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(ShotPlanVersionRecord)
                .where(ShotPlanVersionRecord.project_id == project_id)
                .order_by(ShotPlanVersionRecord.revision.desc())
            ).all()
            return [_shot_plan_dto(record) for record in records]

    def activate_shot_plan(
        self, project_id: uuid.UUID, shot_plan_id: uuid.UUID
    ) -> ShotPlanVersionDto:
        with self._sessions.begin() as session:
            session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            record = session.get(ShotPlanVersionRecord, shot_plan_id)
            if record is None or record.project_id != project_id:
                raise StudioNotFoundError("shot plan version not found")
            session.execute(
                update(ShotPlanVersionRecord)
                .where(ShotPlanVersionRecord.project_id == project_id)
                .values(active=False)
            )
            record.active = True
            session.flush()
            return _shot_plan_dto(record)

    def register_asset(
        self,
        project_id: uuid.UUID,
        *,
        role: str,
        sha256: str,
        media_type: str,
        storage_key: str,
        byte_size: int,
        producing_job_id: uuid.UUID | None,
    ) -> AssetDto:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.project_id == project_id,
                    AssetRecord.sha256 == sha256,
                    AssetRecord.role == role,
                )
            )
            if existing is not None:
                return _asset_dto(existing)
            record = AssetRecord(
                project_id=project_id,
                producing_job_id=producing_job_id,
                role=role,
                media_type=media_type,
                storage_key=storage_key,
                sha256=sha256,
                byte_size=byte_size,
                metadata_json={},
            )
            session.add(record)
            session.flush()
            return _asset_dto(record)

    def select_asset(
        self,
        project_id: uuid.UUID,
        *,
        slot: str,
        asset_id: uuid.UUID,
        decision: str = "selected",
    ) -> ProjectSelectionDto:
        with self._sessions.begin() as session:
            asset = session.get(AssetRecord, asset_id)
            if asset is None or asset.project_id != project_id:
                raise StudioNotFoundError("asset not found")
            record = ProjectSelectionRecord(
                project_id=project_id,
                asset_id=asset_id,
                slot=slot,
                decision=decision,
                source_hash=_selection_source_hash(project_id, slot, asset.sha256),
            )
            session.add(record)
            session.flush()
            return _selection_dto(record)

    def current_selections(self, project_id: uuid.UUID) -> dict[str, AssetDto]:
        with self._sessions() as session:
            records = session.execute(
                select(ProjectSelectionRecord, AssetRecord)
                .join(AssetRecord, AssetRecord.id == ProjectSelectionRecord.asset_id)
                .where(
                    ProjectSelectionRecord.project_id == project_id,
                    ProjectSelectionRecord.decision.in_(("selected", "approved")),
                )
                .order_by(
                    ProjectSelectionRecord.created_at.desc(),
                    ProjectSelectionRecord.id.desc(),
                )
            ).all()
            current: dict[str, AssetDto] = {}
            for selection, asset in records:
                current.setdefault(selection.slot, _asset_dto(asset))
            return current

    def list_assets(self, project_id: uuid.UUID) -> list[AssetDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(AssetRecord)
                .where(AssetRecord.project_id == project_id)
                .order_by(AssetRecord.created_at.desc())
            ).all()
            return [_asset_dto(record) for record in records]

    def get_asset(self, asset_id: uuid.UUID) -> AssetDto | None:
        with self._sessions() as session:
            record = session.get(AssetRecord, asset_id)
            return _asset_dto(record) if record is not None else None

    def create_job(self, job: JobDto) -> JobDto:
        with self._sessions.begin() as session:
            existing = _job_by_idempotency(session, job.idempotency_key)
            if existing is not None:
                _require_same_input(existing, job.input_hash)
                return _job_dto(session, existing)
            record = JobRecord(
                id=job.id,
                project_id=job.project_id,
                kind=job.kind,
                status=job.status,
                input_hash=job.input_hash,
                idempotency_key=job.idempotency_key,
                provider=job.provider,
                model=job.model,
                provider_task_id=job.provider_task_id,
                expected_cost_micros=job.expected_cost_micros,
                frozen_input_json=job.frozen_input,
                supersedes_job_id=job.supersedes_job_id,
                error_json=job.error,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
            session.add(record)
            session.flush()
            _add_job_event(session, record, "job.queued")
            return _job_dto(session, record)

    def get_job(self, job_id: uuid.UUID) -> JobDto | None:
        with self._sessions() as session:
            record = session.get(JobRecord, job_id)
            return _job_dto(session, record) if record is not None else None

    def cancel_job(self, job_id: uuid.UUID) -> JobDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(JobRecord).where(JobRecord.id == job_id).with_for_update()
            )
            if record is None:
                raise StudioNotFoundError("job not found")
            if record.status in {"succeeded", "failed", "cancelled"}:
                return _job_dto(session, record)
            record.status = "cancelled" if record.status == "queued" else "cancel_requested"
            record.updated_at = datetime.now(UTC)
            _add_job_event(session, record, f"job.{record.status}")
            session.flush()
            return _job_dto(session, record)

    def list_job_events(self, *, after_event_id: int, limit: int = 100) -> list[JobEventDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(JobEventRecord)
                .where(JobEventRecord.id > after_event_id)
                .order_by(JobEventRecord.id)
                .limit(limit)
            ).all()
            return [
                JobEventDto(
                    id=record.id,
                    jobId=record.job_id,
                    projectId=record.project_id,
                    eventType=record.event_type,
                    payload=record.payload_json,
                    createdAt=record.created_at,
                )
                for record in records
            ]

    def create_edit(
        self,
        project_id: uuid.UUID,
        *,
        source_selection_hash: str,
        edl: EditDecisionListDto,
    ) -> EditVersionDto:
        with self._sessions.begin() as session:
            session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            revision = session.scalar(
                select(func.coalesce(func.max(EditVersionRecord.revision), 0)).where(
                    EditVersionRecord.project_id == project_id
                )
            )
            record = EditVersionRecord(
                project_id=project_id,
                revision=int(revision or 0) + 1,
                source_selection_hash=source_selection_hash,
                edl_json=edl.model_dump(mode="json", by_alias=True),
                status="draft",
            )
            session.add(record)
            session.flush()
            return _edit_dto(record)

    def list_edits(self, project_id: uuid.UUID) -> list[EditVersionDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(EditVersionRecord)
                .where(EditVersionRecord.project_id == project_id)
                .order_by(EditVersionRecord.revision.desc())
            ).all()
            return [_edit_dto(record) for record in records]

    def get_edit(self, edit_id: uuid.UUID) -> EditVersionDto | None:
        with self._sessions() as session:
            record = session.get(EditVersionRecord, edit_id)
            return _edit_dto(record) if record is not None else None


def _project_dto(record: ProjectRecord) -> ProjectDto:
    return ProjectDto(
        id=record.id,
        title=record.title,
        theme=record.theme,
        targetDurationSeconds=record.target_duration_seconds,
        aspectRatio=record.aspect_ratio,
        canonProfileId=record.canon_profile_id,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _planner_message_dto(record: LifePlannerMessageRecord) -> PlannerMessageDto:
    return PlannerMessageDto(
        id=record.id,
        role=record.role,
        content=record.content,
        ordinal=record.ordinal,
        createdAt=record.created_at,
    )


def _proposal_dto(record: LifePlannerProposalRecord) -> LifeStoryProposalDto:
    payload = record.proposal_json
    return LifeStoryProposalDto(
        id=record.id,
        projectId=record.project_id,
        status=record.status,
        title=payload["title"],
        summary=payload["summary"],
        body=payload["body"],
        microEvent=MicroEvent.model_validate(payload["microEvent"]),
        targetDurationSeconds=payload["targetDurationSeconds"],
        dialoguePolicy=payload["dialoguePolicy"],
        environmentIntent=payload["environmentIntent"],
        propIntent=payload.get("propIntent"),
        contextHash=record.context_hash,
        warnings=payload.get("warnings", []),
    )


def _story_dto(record: StoryVersionRecord) -> StoryVersionDto:
    return StoryVersionDto(
        id=record.id,
        projectId=record.project_id,
        revision=record.revision,
        sourceProposalId=record.source_proposal_id,
        title=record.title,
        body=record.body,
        microEvent=MicroEvent.model_validate(record.micro_event_json),
        targetDurationSeconds=record.target_duration_seconds,
        dialoguePolicy=record.dialogue_policy,
        environmentIntent=record.environment_intent,
        active=record.active,
        createdAt=record.created_at,
    )


def _shot_plan_dto(record: ShotPlanVersionRecord) -> ShotPlanVersionDto:
    return ShotPlanVersionDto(
        id=record.id,
        projectId=record.project_id,
        revision=record.revision,
        sourceStoryVersionId=record.source_story_version_id,
        sourceSelectionHash=record.source_selection_hash,
        clip=record.clip_json,
        shots=[ShotSpec.model_validate(payload) for payload in record.shots_json],
        totalDurationSeconds=record.total_duration_seconds,
        active=record.active,
        outdated=False,
        createdAt=record.created_at,
    )


def _asset_dto(record: AssetRecord) -> AssetDto:
    return AssetDto(
        id=record.id,
        projectId=record.project_id,
        producingJobId=record.producing_job_id,
        candidateIndex=record.candidate_index,
        role=record.role,
        mediaType=record.media_type,
        storageKey=record.storage_key,
        sha256=record.sha256,
        byteSize=record.byte_size,
        metadata=record.metadata_json,
        createdAt=record.created_at,
    )


def _selection_dto(record: ProjectSelectionRecord) -> ProjectSelectionDto:
    return ProjectSelectionDto(
        id=record.id,
        projectId=record.project_id,
        assetId=record.asset_id,
        slot=record.slot,
        decision=record.decision,
        sourceHash=record.source_hash,
        createdAt=record.created_at,
    )


def _job_by_idempotency(session: Session, key: str) -> JobRecord | None:
    return session.scalar(select(JobRecord).where(JobRecord.idempotency_key == key))


def _edit_dto(record: EditVersionRecord) -> EditVersionDto:
    return EditVersionDto(
        id=record.id,
        projectId=record.project_id,
        revision=record.revision,
        sourceSelectionHash=record.source_selection_hash,
        edl=EditDecisionListDto.model_validate(record.edl_json),
        status=record.status,
        renderedAssetId=record.rendered_asset_id,
        createdAt=record.created_at,
    )


def _require_same_input(record: JobRecord, input_hash: str) -> None:
    if record.input_hash != input_hash:
        raise StudioConflictError("idempotency key already belongs to different input")


def _job_dto(session: Session, record: JobRecord) -> JobDto:
    asset_ids = list(
        session.scalars(
            select(AssetRecord.id)
            .where(AssetRecord.producing_job_id == record.id)
            .order_by(AssetRecord.candidate_index, AssetRecord.created_at)
        ).all()
    )
    return JobDto(
        id=record.id,
        projectId=record.project_id,
        kind=record.kind,
        status=record.status,
        inputHash=record.input_hash,
        idempotencyKey=record.idempotency_key,
        provider=record.provider,
        model=record.model,
        providerTaskId=record.provider_task_id,
        expectedCostMicros=record.expected_cost_micros,
        frozenInput=record.frozen_input_json,
        resultAssetIds=asset_ids,
        supersedesJobId=record.supersedes_job_id,
        error=record.error_json,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _add_job_event(
    session: Session,
    job: JobRecord,
    event_type: str,
    payload: dict[str, object] | None = None,
) -> None:
    session.add(
        JobEventRecord(
            job_id=job.id,
            project_id=job.project_id,
            event_type=event_type,
            payload_json=payload or {"jobId": str(job.id), "status": job.status},
        )
    )


def _selection_source_hash(project_id: uuid.UUID, slot: str, sha256: str) -> str:
    document = {"projectId": str(project_id), "slot": slot, "sha256": sha256}
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
