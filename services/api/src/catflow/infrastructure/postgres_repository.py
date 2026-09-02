from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.service import (
    FIXED_CANON_ROLES,
    AssetDto,
    CanonProfileDto,
    CanonRevisionCreateCommand,
    EditDecisionListDto,
    EditDecisionListV2,
    EditVersionDto,
    EnvironmentPresetDto,
    FixedCanonRole,
    GenerationInputSnapshotDto,
    JobDto,
    JobEventDto,
    JobPublicationDto,
    LifeStoryProposalDto,
    PlannerJobDto,
    PlannerMessageCommand,
    PlannerMessageDto,
    PlannerSnapshotDto,
    ProjectCreate,
    ProjectDto,
    ProjectPatch,
    ProjectSelectionDto,
    RateCardRevisionCreateCommand,
    RateCardRevisionDto,
    ShotPlanVersionDto,
    StoredAssetDto,
    StoryCreateCommand,
    StoryVersionDto,
    StudioConflictError,
    StudioNotFoundError,
    ValidationRunDto,
    ValidationRunPreviewDto,
    VideoRepairDto,
    VideoRepairStatus,
)
from catflow.domain.billing import RateCardItem, rate_card_revision_signature
from catflow.domain.models import LifeStoryProposalDraft, MicroEvent, ShotPlanDraft, ShotSpec
from catflow.domain.validation import (
    ValidationCallKind,
    ValidationManifest,
    reserve_validation_call,
)
from catflow.domain.video_repairs import FrameRange, RationalFrameRate

from .database import canon_v4_document, ensure_canon_v4
from .models import (
    AssetRecord,
    CanonProfileRecord,
    EditVersionRecord,
    EnvironmentPresetRecord,
    JobEventRecord,
    JobRecord,
    LifePlannerMessageRecord,
    LifePlannerProposalRecord,
    LifePlannerSessionRecord,
    MediaPublicationRecord,
    ProjectRecord,
    ProjectSelectionRecord,
    ProviderRateCardRecord,
    ShotPlanVersionRecord,
    StoryVersionRecord,
    ValidationRunRecord,
    VideoRepairRecord,
)


class PostgresStudioRepository:
    """PostgreSQL owns every durable CatFlow business fact and transaction boundary."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def publish_rate_card(
        self, command: RateCardRevisionCreateCommand
    ) -> RateCardRevisionDto:
        with self._sessions.begin() as session:
            existing_rows = session.scalars(
                select(ProviderRateCardRecord).where(
                    ProviderRateCardRecord.provider == command.provider,
                    ProviderRateCardRecord.model == command.model,
                    ProviderRateCardRecord.revision == command.revision,
                )
            ).all()
            if existing_rows:
                existing = _rate_card_revision_dto(existing_rows)
                expected = rate_card_revision_signature(
                    provider=command.provider,
                    model=command.model,
                    revision=command.revision,
                    source_url=command.source_url,
                    effective_from=command.effective_from,
                    rates=command.rates,
                )
                actual = rate_card_revision_signature(
                    provider=existing.provider,
                    model=existing.model,
                    revision=existing.revision,
                    source_url=existing.source_url,
                    effective_from=existing.effective_from,
                    rates=existing.rates,
                )
                if actual != expected:
                    raise StudioConflictError(
                        "rate-card revision already exists with different rates"
                    )
                return existing

            session.execute(
                update(ProviderRateCardRecord)
                .where(
                    ProviderRateCardRecord.provider == command.provider,
                    ProviderRateCardRecord.model == command.model,
                    ProviderRateCardRecord.active.is_(True),
                )
                .values(active=False)
            )
            now = datetime.now(UTC)
            rows = [
                ProviderRateCardRecord(
                    id=uuid.uuid4(),
                    provider=command.provider,
                    model=command.model,
                    metric=rate.metric,
                    unit=rate.unit,
                    unit_price_micros=rate.unit_price_micros,
                    currency="CNY",
                    source_url=command.source_url,
                    effective_from=command.effective_from,
                    revision=command.revision,
                    active=True,
                    created_at=now,
                )
                for rate in command.rates
            ]
            session.add_all(rows)
            session.flush()
            return _rate_card_revision_dto(rows)

    def list_rate_cards(self) -> list[RateCardRevisionDto]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ProviderRateCardRecord).order_by(
                    ProviderRateCardRecord.created_at.desc(),
                    ProviderRateCardRecord.provider,
                    ProviderRateCardRecord.model,
                    ProviderRateCardRecord.metric,
                )
            ).all()
            groups: dict[tuple[str, str, str], list[ProviderRateCardRecord]] = {}
            for row in rows:
                groups.setdefault((row.provider, row.model, row.revision), []).append(row)
            return [_rate_card_revision_dto(group) for group in groups.values()]

    def active_canon_profile_id(self) -> uuid.UUID:
        with self._sessions.begin() as session:
            return ensure_canon_v4(session).id

    def current_canon_profile(self) -> CanonProfileDto:
        with self._sessions.begin() as session:
            record = ensure_canon_v4(session)
            return _canon_profile_dto(session, record)

    def register_canon_asset(
        self,
        *,
        role: FixedCanonRole,
        sha256: str,
        storage_key: str,
        byte_size: int,
    ) -> StoredAssetDto:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(AssetRecord).where(
                    AssetRecord.project_id.is_(None),
                    AssetRecord.role == role,
                    AssetRecord.sha256 == sha256,
                )
            )
            if existing is not None:
                return _asset_dto(existing)
            record = AssetRecord(
                project_id=None,
                role=role,
                media_type="image",
                storage_key=storage_key,
                sha256=sha256,
                byte_size=byte_size,
                metadata_json={"scope": "global_canon_candidate"},
            )
            session.add(record)
            session.flush()
            return _asset_dto(record)

    def publish_canon_revision(self, command: CanonRevisionCreateCommand) -> CanonProfileDto:
        with self._sessions.begin() as session:
            fixed: dict[FixedCanonRole, AssetRecord] = {}
            for role in FIXED_CANON_ROLES:
                asset = session.get(AssetRecord, command.fixed_assets[role])
                if asset is None or asset.project_id is not None or asset.role != role:
                    raise StudioConflictError(
                        "fixed asset must be a matching global Canon candidate"
                    )
                fixed[role] = asset
            active = ensure_canon_v4(session)
            version = session.scalar(
                select(func.coalesce(func.max(CanonProfileRecord.version), 0)).where(
                    CanonProfileRecord.profile_key == active.profile_key
                )
            )
            document = canon_v4_document()
            document["fixedAssets"] = {
                role: {"assetId": str(asset.id), "sha256": asset.sha256}
                for role, asset in fixed.items()
            }
            profile_hash = hashlib.sha256(
                json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            existing = session.scalar(
                select(CanonProfileRecord).where(CanonProfileRecord.profile_hash == profile_hash)
            )
            if existing is not None:
                session.execute(update(CanonProfileRecord).values(active=False))
                existing.active = True
                session.flush()
                return _canon_profile_dto(session, existing)
            session.execute(update(CanonProfileRecord).values(active=False))
            record = CanonProfileRecord(
                profile_key=active.profile_key,
                version=int(version or 0) + 1,
                active=True,
                profile_json=document,
                profile_hash=profile_hash,
            )
            session.add(record)
            session.flush()
            return _canon_profile_dto(session, record)

    def create_validation_run(self, preview: ValidationRunPreviewDto) -> ValidationRunDto:
        now = datetime.now(UTC)
        with self._sessions.begin() as session:
            record = ValidationRunRecord(
                status="authorized",
                manifest_hash=preview.manifest_hash,
                topics_json=list(preview.topics),
                duration_seconds=preview.duration_seconds,
                resolution=preview.resolution,
                aspect_ratio=preview.aspect_ratio,
                target_budget_cny=preview.target_budget_cny,
                call_limits_json={kind.value: limit for kind, limit in preview.call_limits.items()},
                usage_json={kind.value: 0 for kind in preview.call_limits},
                provider=preview.provider,
                models_json=preview.models,
                capability_revision=preview.capability_revision,
                cost_estimate_status=preview.cost_estimate_status,
                canon_snapshot_json=preview.canon.model_dump(mode="json", by_alias=True),
                repair_snapshot_json=preview.repair.model_dump(mode="json", by_alias=True),
                authorized_at=now,
            )
            session.add(record)
            session.flush()
            return _validation_run_dto(record)

    def get_validation_run(self, run_id: uuid.UUID) -> ValidationRunDto | None:
        with self._sessions() as session:
            record = session.get(ValidationRunRecord, run_id)
            return _validation_run_dto(record) if record is not None else None

    def latest_validation_run(self) -> ValidationRunDto | None:
        with self._sessions() as session:
            record = session.scalar(
                select(ValidationRunRecord).order_by(
                    ValidationRunRecord.created_at.desc(), ValidationRunRecord.id.desc()
                )
            )
            return _validation_run_dto(record) if record is not None else None

    def set_validation_run_status(self, run_id: uuid.UUID, status: str) -> ValidationRunDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(ValidationRunRecord)
                .where(ValidationRunRecord.id == run_id)
                .with_for_update()
            )
            if record is None:
                raise StudioNotFoundError("validation run not found")
            record.status = status
            session.flush()
            return _validation_run_dto(record)

    def reserve_validation_call(
        self, run_id: uuid.UUID, kind: ValidationCallKind
    ) -> ValidationRunDto:
        with self._sessions.begin() as session:
            record = _reserve_validation_call_in_session(session, run_id, kind)
            session.flush()
            return _validation_run_dto(record)

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
            latest_job = session.scalar(
                select(JobRecord)
                .where(JobRecord.project_id == project_id, JobRecord.kind == "plan_story")
                .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
                .limit(1)
            )
            return PlannerSnapshotDto(
                sessionId=planner_session.id,
                projectId=project_id,
                contextRevision=planner_session.context_revision,
                messages=[_planner_message_dto(record) for record in messages],
                proposals=[_proposal_dto(record) for record in proposals],
                latestJob=(
                    PlannerJobDto(
                        id=latest_job.id,
                        status=latest_job.status,
                        provider=latest_job.provider,
                        model=latest_job.model,
                        providerTaskId=latest_job.provider_task_id,
                        actualUsage=latest_job.actual_usage_json,
                        actualCostMicros=latest_job.actual_cost_micros,
                        currency=latest_job.currency,
                        billingStatus=latest_job.billing_status,
                        rateCardRevision=latest_job.rate_card_revision,
                        error=latest_job.error_json,
                        createdAt=latest_job.created_at,
                        updatedAt=latest_job.updated_at,
                    )
                    if latest_job is not None
                    else None
                ),
            )

    def enqueue_planner_message(
        self, project_id: uuid.UUID, command: PlannerMessageCommand, *, job: JobDto
    ) -> JobDto:
        with self._sessions.begin() as session:
            existing = _job_by_idempotency(session, command.idempotency_key)
            if existing is not None:
                _require_same_input(existing, job.input_hash)
                return _job_dto(session, existing)

            if job.validation_run_id is not None:
                _require_unique_validation_job(session, job)
                _reserve_validation_call_in_session(
                    session,
                    job.validation_run_id,
                    ValidationCallKind.PLAN_STORY,
                )

            planner_session = session.scalar(
                select(LifePlannerSessionRecord)
                .where(LifePlannerSessionRecord.project_id == project_id)
                .with_for_update()
            )
            if planner_session is None:
                raise StudioNotFoundError("planner session not found")
            if session.get(ProjectRecord, project_id) is None:
                raise StudioNotFoundError("project not found")
            ordinal = session.scalar(
                select(func.coalesce(func.max(LifePlannerMessageRecord.ordinal), 0)).where(
                    LifePlannerMessageRecord.session_id == planner_session.id
                )
            )
            session.add(
                LifePlannerMessageRecord(
                    session_id=planner_session.id,
                    ordinal=int(ordinal or 0) + 1,
                    role="user",
                    content=command.text,
                )
            )
            record = JobRecord(
                id=job.id,
                project_id=project_id,
                kind=job.kind,
                status=job.status,
                input_hash=job.input_hash,
                idempotency_key=job.idempotency_key,
                provider=job.provider,
                model=job.model,
                validation_run_id=job.validation_run_id,
                expected_cost_micros=job.expected_cost_micros,
                frozen_input_json=job.frozen_input,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
            session.add(record)
            session.flush()
            _add_job_event(session, record, "job.queued")
            return _job_dto(session, record)

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
                director_treatment_json=(
                    draft.director_treatment.model_dump(mode="json", by_alias=True)
                    if draft.director_treatment is not None
                    else None
                ),
                director_prompt_revision=draft.director_prompt_revision,
                director_model=draft.director_model,
                director_input_hash=draft.director_input_hash,
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
        metadata: dict[str, object] | None = None,
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
                metadata_json=metadata or {},
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
            project = session.get(ProjectRecord, project_id)
            if project is None:
                raise StudioNotFoundError("project not found")
            canon = session.get(CanonProfileRecord, project.canon_profile_id)
            fixed_assets = (canon.profile_json if canon is not None else {}).get("fixedAssets", {})
            if slot in fixed_assets:
                raise StudioConflictError("global Canon slots cannot be overridden by a project")
            asset = session.get(AssetRecord, asset_id)
            if asset is None or asset.project_id != project_id:
                raise StudioNotFoundError("asset not found")
            if slot == "environment" and (
                asset.role != "environment" or asset.media_type != "image"
            ):
                raise StudioConflictError("shared environment must be an environment image")
            record = ProjectSelectionRecord(
                project_id=project_id,
                asset_id=asset_id,
                slot=slot,
                decision=decision,
                source_hash=_selection_source_hash(project_id, slot, asset.sha256),
            )
            session.add(record)
            if slot == "environment":
                session.execute(
                    update(EnvironmentPresetRecord)
                    .where(EnvironmentPresetRecord.active.is_(True))
                    .values(active=False)
                )
                session.add(
                    EnvironmentPresetRecord(
                        source_project_id=project_id,
                        asset_id=asset_id,
                        active=True,
                    )
                )
            session.flush()
            return _selection_dto(record)

    def current_selections(self, project_id: uuid.UUID) -> dict[str, AssetDto]:
        with self._sessions() as session:
            project = session.get(ProjectRecord, project_id)
            if project is None:
                raise StudioNotFoundError("project not found")
            canon = session.get(CanonProfileRecord, project.canon_profile_id)
            fixed_document = (canon.profile_json if canon is not None else {}).get(
                "fixedAssets", {}
            )
            fixed_ids = [uuid.UUID(str(item["assetId"])) for item in fixed_document.values()]
            fixed_records = (
                {
                    record.id: record
                    for record in session.scalars(
                        select(AssetRecord).where(AssetRecord.id.in_(fixed_ids))
                    ).all()
                }
                if fixed_ids
                else {}
            )
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
            current: dict[str, AssetDto] = {
                role: _asset_dto(fixed_records[uuid.UUID(str(item["assetId"]))])
                for role, item in fixed_document.items()
                if uuid.UUID(str(item["assetId"])) in fixed_records
            }
            for selection, asset in records:
                current.setdefault(selection.slot, _asset_dto(asset))
            active_environment = session.execute(
                select(EnvironmentPresetRecord, AssetRecord)
                .join(AssetRecord, AssetRecord.id == EnvironmentPresetRecord.asset_id)
                .where(EnvironmentPresetRecord.active.is_(True))
            ).one_or_none()
            if active_environment is not None:
                _, asset = active_environment
                current["environment"] = _asset_dto(asset)
            return current

    def environment_presets(self) -> list[EnvironmentPresetDto]:
        with self._sessions() as session:
            records = session.execute(
                select(EnvironmentPresetRecord, AssetRecord)
                .join(AssetRecord, AssetRecord.id == EnvironmentPresetRecord.asset_id)
                .order_by(
                    EnvironmentPresetRecord.created_at.desc(),
                    EnvironmentPresetRecord.id.desc(),
                )
            ).all()
            return [
                EnvironmentPresetDto(
                    id=preset.id,
                    sourceProjectId=preset.source_project_id,
                    asset=_asset_dto(asset),
                    active=preset.active,
                    createdAt=preset.created_at,
                )
                for preset, asset in records
            ]

    def list_assets(self, project_id: uuid.UUID) -> list[AssetDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(AssetRecord)
                .where(AssetRecord.project_id == project_id)
                .order_by(AssetRecord.created_at.desc())
            ).all()
            return [_asset_dto(record) for record in records]

    def get_asset(self, asset_id: uuid.UUID) -> StoredAssetDto | None:
        with self._sessions() as session:
            record = session.get(AssetRecord, asset_id)
            return _asset_dto(record) if record is not None else None

    def create_job(self, job: JobDto) -> JobDto:
        with self._sessions.begin() as session:
            existing = _job_by_idempotency(session, job.idempotency_key)
            if existing is not None:
                _require_same_input(existing, job.input_hash)
                return _job_dto(session, existing)
            if job.validation_run_id is not None:
                _require_unique_validation_job(session, job)
                _reserve_validation_call_in_session(
                    session,
                    job.validation_run_id,
                    ValidationCallKind(job.kind),
                )
            record = _new_job_record(job)
            session.add(record)
            session.flush()
            _add_job_event(session, record, "job.queued")
            return _job_dto(session, record)

    def get_job(self, job_id: uuid.UUID) -> JobDto | None:
        with self._sessions() as session:
            record = session.get(JobRecord, job_id)
            return _job_dto(session, record) if record is not None else None

    def list_project_jobs(self, project_id: uuid.UUID) -> list[JobDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(JobRecord)
                .where(JobRecord.project_id == project_id)
                .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
            ).all()
            return [_job_dto(session, record) for record in records]

    def latest_job(self, project_id: uuid.UUID, *, kind: str) -> JobDto | None:
        with self._sessions() as session:
            record = session.scalar(
                select(JobRecord)
                .where(JobRecord.project_id == project_id, JobRecord.kind == kind)
                .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
                .limit(1)
            )
            return _job_dto(session, record) if record is not None else None

    def resume_job_storage(self, job_id: uuid.UUID) -> JobDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(JobRecord).where(JobRecord.id == job_id).with_for_update()
            )
            if record is None:
                raise StudioNotFoundError("job not found")
            provider_result = record.provider_result_json
            error = record.error_json
            if (
                record.status != "failed"
                or not isinstance(error, dict)
                or error.get("code") != "result_storage_failed"
                or record.kind not in {"generate_image", "generate_video"}
                or not isinstance(provider_result, dict)
                or not (provider_result.get("url") or provider_result.get("videoUrl"))
                or (record.kind == "generate_video" and not record.provider_task_id)
            ):
                raise StudioConflictError("job is not eligible for result storage recovery")
            record.status = "storing"
            record.error_json = None
            record.updated_at = datetime.now(UTC)
            _add_job_event(session, record, "job.storing")
            session.flush()
            return _job_dto(session, record)

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

    def latest_job_event_id(self) -> int:
        with self._sessions() as session:
            return int(session.scalar(select(func.coalesce(func.max(JobEventRecord.id), 0))) or 0)

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
            session.execute(
                update(EditVersionRecord)
                .where(
                    EditVersionRecord.project_id == project_id,
                    EditVersionRecord.active.is_(True),
                )
                .values(active=False)
            )
            edl_document = edl.model_dump(mode="json", by_alias=True)
            record = EditVersionRecord(
                project_id=project_id,
                revision=int(revision or 0) + 1,
                source_selection_hash=source_selection_hash,
                edl_json=edl_document,
                status="draft",
                format_version=1,
                active=True,
                timeline_hash=_document_hash(edl_document),
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

    def active_edit(self, project_id: uuid.UUID) -> EditVersionDto | None:
        with self._sessions() as session:
            record = session.scalar(
                select(EditVersionRecord).where(
                    EditVersionRecord.project_id == project_id,
                    EditVersionRecord.active.is_(True),
                )
            )
            return _edit_dto(record) if record is not None else None

    def create_video_repair(self, repair: VideoRepairDto) -> VideoRepairDto:
        with self._sessions.begin() as session:
            record = _new_video_repair_record(repair)
            session.add(record)
            session.flush()
            return _video_repair_dto(record)

    def create_video_repair_job(self, repair: VideoRepairDto, job: JobDto) -> JobDto:
        with self._sessions.begin() as session:
            existing = _job_by_idempotency(session, job.idempotency_key)
            if existing is not None:
                _require_same_input(existing, job.input_hash)
                return _job_dto(session, existing)
            repair_record = _new_video_repair_record(repair)
            job_record = _new_job_record(job)
            session.add(repair_record)
            session.flush()
            session.add(job_record)
            session.flush()
            _add_job_event(session, job_record, "job.queued")
            return _job_dto(session, job_record)

    def get_video_repair(self, repair_id: uuid.UUID) -> VideoRepairDto | None:
        with self._sessions() as session:
            record = session.get(VideoRepairRecord, repair_id)
            return _video_repair_dto(record) if record is not None else None

    def list_video_repairs(self, project_id: uuid.UUID) -> list[VideoRepairDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(VideoRepairRecord)
                .where(VideoRepairRecord.project_id == project_id)
                .order_by(VideoRepairRecord.created_at.desc(), VideoRepairRecord.id.desc())
            ).all()
            return [_video_repair_dto(record) for record in records]

    def set_video_repair_status(
        self,
        repair_id: uuid.UUID,
        *,
        status: VideoRepairStatus,
        candidate_asset_id: uuid.UUID | None = None,
    ) -> VideoRepairDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(VideoRepairRecord).where(VideoRepairRecord.id == repair_id).with_for_update()
            )
            if record is None:
                raise StudioNotFoundError("video repair not found")
            record.status = status
            if candidate_asset_id is not None:
                record.candidate_asset_id = candidate_asset_id
            session.flush()
            return _video_repair_dto(record)

    def approve_video_repair(
        self,
        repair_id: uuid.UUID,
        *,
        edl: EditDecisionListV2,
        source_selection_hash: str,
        parent_edit_version_id: uuid.UUID | None,
        candidate_asset_id: uuid.UUID,
        candidate_source_range: FrameRange,
        idempotency_key: str,
    ) -> EditVersionDto:
        with self._sessions.begin() as session:
            repair = session.scalar(
                select(VideoRepairRecord).where(VideoRepairRecord.id == repair_id).with_for_update()
            )
            if repair is None:
                raise StudioNotFoundError("video repair not found")
            if repair.approval_idempotency_key is not None:
                if (
                    repair.approval_idempotency_key != idempotency_key
                    or repair.approved_edit_version_id is None
                ):
                    raise StudioConflictError(
                        "repair approval idempotency key belongs to different input"
                    )
                existing = session.get(EditVersionRecord, repair.approved_edit_version_id)
                if existing is None:
                    raise StudioConflictError("approved edit version is missing")
                return _edit_dto(existing)
            conflicting = session.scalar(
                select(VideoRepairRecord.id).where(
                    VideoRepairRecord.approval_idempotency_key == idempotency_key,
                    VideoRepairRecord.id != repair_id,
                )
            )
            if conflicting is not None:
                raise StudioConflictError(
                    "repair approval idempotency key belongs to different input"
                )
            session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == repair.project_id).with_for_update()
            )
            current_video_id = session.scalar(
                select(ProjectSelectionRecord.asset_id)
                .where(
                    ProjectSelectionRecord.project_id == repair.project_id,
                    ProjectSelectionRecord.slot == "video",
                    ProjectSelectionRecord.decision.in_(("selected", "approved")),
                )
                .order_by(
                    ProjectSelectionRecord.created_at.desc(),
                    ProjectSelectionRecord.id.desc(),
                )
                .limit(1)
            )
            active_edit_id = session.scalar(
                select(EditVersionRecord.id).where(
                    EditVersionRecord.project_id == repair.project_id,
                    EditVersionRecord.active.is_(True),
                )
            )
            if (
                current_video_id != repair.base_video_asset_id
                or active_edit_id != repair.base_edit_version_id
            ):
                repair.status = "outdated"
                raise StudioConflictError("base timeline changed")
            if (
                repair.status != "candidate_ready"
                or repair.candidate_asset_id != candidate_asset_id
            ):
                raise StudioConflictError("video repair has no matching candidate ready")
            session.execute(
                update(EditVersionRecord)
                .where(
                    EditVersionRecord.project_id == repair.project_id,
                    EditVersionRecord.active.is_(True),
                )
                .values(active=False)
            )
            revision = int(
                session.scalar(
                    select(func.coalesce(func.max(EditVersionRecord.revision), 0)).where(
                        EditVersionRecord.project_id == repair.project_id
                    )
                )
                or 0
            )
            document = edl.model_dump(mode="json", by_alias=True)
            edit = EditVersionRecord(
                project_id=repair.project_id,
                revision=revision + 1,
                source_selection_hash=source_selection_hash,
                edl_json=document,
                status="draft",
                parent_edit_version_id=parent_edit_version_id,
                format_version=2,
                active=True,
                timeline_hash=_document_hash(document),
            )
            session.add(edit)
            session.flush()
            repair.status = "approved"
            repair.candidate_core_start_frame = candidate_source_range.start_frame
            repair.candidate_core_end_frame = candidate_source_range.end_frame
            repair.approved_candidate_asset_id = candidate_asset_id
            repair.approved_edit_version_id = edit.id
            repair.approval_idempotency_key = idempotency_key
            repair.approved_at = datetime.now(UTC)
            session.flush()
            return _edit_dto(edit)


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


def _validation_run_dto(record: ValidationRunRecord) -> ValidationRunDto:
    call_limits = {ValidationCallKind(key): value for key, value in record.call_limits_json.items()}
    return ValidationRunDto(
        id=record.id,
        status=record.status,
        manifestHash=record.manifest_hash,
        topics=tuple(record.topics_json),
        durationSeconds=record.duration_seconds,
        resolution=record.resolution,
        aspectRatio=record.aspect_ratio,
        targetBudgetCny=record.target_budget_cny,
        callLimits=call_limits,
        totalCallLimit=sum(call_limits.values()),
        maximumVideoCalls=(
            call_limits[ValidationCallKind.GENERATE_VIDEO]
            + call_limits.get(ValidationCallKind.REGENERATE_VIDEO_SEGMENT, 0)
        ),
        provider=record.provider,
        models=record.models_json,
        capabilityRevision=record.capability_revision,
        costEstimateStatus=record.cost_estimate_status,
        canon=record.canon_snapshot_json,
        repair=record.repair_snapshot_json,
        usage={ValidationCallKind(key): value for key, value in record.usage_json.items()},
        createdAt=record.created_at,
        authorizedAt=record.authorized_at,
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
        directorTreatment=record.director_treatment_json,
        directorPromptRevision=record.director_prompt_revision,
        directorModel=record.director_model,
        directorInputHash=record.director_input_hash,
        active=record.active,
        outdated=False,
        createdAt=record.created_at,
    )


def _asset_dto(record: AssetRecord) -> StoredAssetDto:
    metadata = dict(record.metadata_json)
    if record.width is not None:
        metadata.setdefault("width", record.width)
    if record.height is not None:
        metadata.setdefault("height", record.height)
    if record.duration_ms is not None:
        metadata.setdefault("durationMs", record.duration_ms)
        if record.media_type == "video":
            metadata.setdefault("durationFrames", round(record.duration_ms * 24 / 1000))
            metadata.setdefault("frameRateNumerator", 24)
            metadata.setdefault("frameRateDenominator", 1)
    return StoredAssetDto(
        id=record.id,
        projectId=record.project_id,
        canonProfileId=record.canon_profile_id,
        producingJobId=record.producing_job_id,
        candidateIndex=record.candidate_index,
        role=record.role,
        mediaType=record.media_type,
        storageKey=record.storage_key,
        sha256=record.sha256,
        byteSize=record.byte_size,
        metadata=metadata,
        createdAt=record.created_at,
    )


def _canon_profile_dto(session: Session, record: CanonProfileRecord) -> CanonProfileDto:
    fixed_document = record.profile_json.get("fixedAssets", {})
    fixed_ids = [uuid.UUID(str(item["assetId"])) for item in fixed_document.values()]
    assets = (
        {
            asset.id: asset
            for asset in session.scalars(
                select(AssetRecord).where(AssetRecord.id.in_(fixed_ids))
            ).all()
        }
        if fixed_ids
        else {}
    )
    return CanonProfileDto(
        id=record.id,
        version=record.version,
        specVersion=4,
        active=record.active,
        profileHash=record.profile_hash,
        profile=record.profile_json,
        fixedAssets={
            role: _asset_dto(assets[uuid.UUID(str(item["assetId"]))])
            for role, item in fixed_document.items()
            if uuid.UUID(str(item["assetId"])) in assets
        },
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


def _new_job_record(job: JobDto) -> JobRecord:
    return JobRecord(
        id=job.id,
        project_id=job.project_id,
        kind=job.kind,
        status=job.status,
        input_hash=job.input_hash,
        idempotency_key=job.idempotency_key,
        provider=job.provider,
        model=job.model,
        provider_task_id=job.provider_task_id,
        validation_run_id=job.validation_run_id,
        parent_job_id=job.parent_job_id,
        video_repair_id=job.video_repair_id,
        provider_submission_started_at=job.provider_submission_started_at,
        provider_result_json=job.provider_result,
        actual_usage_json=job.actual_usage,
        expected_cost_micros=job.expected_cost_micros,
        actual_cost_micros=job.actual_cost_micros,
        currency=job.currency,
        billing_status=job.billing_status,
        rate_card_revision=job.rate_card_revision,
        pricing_snapshot_json=job.pricing_snapshot,
        provider_request_id=job.provider_request_id,
        frozen_input_json=job.frozen_input,
        supersedes_job_id=job.supersedes_job_id,
        error_json=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _new_video_repair_record(repair: VideoRepairDto) -> VideoRepairRecord:
    return VideoRepairRecord(
        id=repair.id,
        project_id=repair.project_id,
        base_video_asset_id=repair.base_video_asset_id,
        base_edit_version_id=repair.base_edit_version_id,
        base_timeline_hash=repair.base_timeline_hash,
        frame_rate_numerator=repair.frame_rate.numerator,
        frame_rate_denominator=repair.frame_rate.denominator,
        issue_start_frame=repair.issue_range.start_frame,
        issue_end_frame=repair.issue_range.end_frame,
        generation_start_frame=repair.generation_range.start_frame,
        generation_end_frame=repair.generation_range.end_frame,
        candidate_core_start_frame=repair.candidate_core_range.start_frame,
        candidate_core_end_frame=repair.candidate_core_range.end_frame,
        provider_duration_seconds=repair.provider_duration_seconds,
        selection_policy_version=repair.selection_policy_version,
        edit_intent=repair.legacy_edit_intent,
        instruction=repair.instruction,
        prompt=repair.prompt,
        negative_prompt=repair.negative_prompt,
        input_hash=repair.input_hash,
        status=repair.status,
        preview_json=repair.preview.model_dump(mode="json", by_alias=True),
        created_at=repair.created_at,
    )


def _job_by_idempotency(session: Session, key: str) -> JobRecord | None:
    return session.scalar(select(JobRecord).where(JobRecord.idempotency_key == key))


def _reserve_validation_call_in_session(
    session: Session,
    run_id: uuid.UUID,
    kind: ValidationCallKind,
) -> ValidationRunRecord:
    """Lock and consume one paid-call allowance in the caller's job transaction."""
    record = session.scalar(
        select(ValidationRunRecord).where(ValidationRunRecord.id == run_id).with_for_update()
    )
    if record is None:
        raise StudioNotFoundError("validation run not found")
    if record.status != "authorized":
        raise StudioConflictError("validation run is not authorized")
    manifest = ValidationManifest(
        topics=tuple(record.topics_json),
        duration_seconds=record.duration_seconds,
        resolution=record.resolution,
        aspect_ratio=record.aspect_ratio,
        target_budget_cny=record.target_budget_cny,
        repair_topic=str(record.repair_snapshot_json["topic"]),
        repair_start_frame=int(record.repair_snapshot_json["issueRange"]["startFrame"]),
        repair_end_frame=int(record.repair_snapshot_json["issueRange"]["endFrame"]),
        repair_prompt=str(record.repair_snapshot_json["prompt"]),
        call_limits={
            ValidationCallKind(key): value for key, value in record.call_limits_json.items()
        },
    )
    usage = {ValidationCallKind(key): value for key, value in record.usage_json.items()}
    record.usage_json = {
        key.value: value for key, value in reserve_validation_call(manifest, usage, kind).items()
    }
    return record


def _require_unique_validation_job(session: Session, job: JobDto) -> None:
    duplicate = session.scalar(
        select(JobRecord.id).where(
            JobRecord.validation_run_id == job.validation_run_id,
            JobRecord.project_id == job.project_id,
            JobRecord.kind == job.kind,
        )
    )
    if duplicate is not None:
        raise StudioConflictError("validation run already has this project call")


def _edit_dto(record: EditVersionRecord) -> EditVersionDto:
    edl = (
        EditDecisionListV2.model_validate(record.edl_json)
        if record.format_version == 2
        else EditDecisionListDto.model_validate(record.edl_json)
    )
    return EditVersionDto(
        id=record.id,
        projectId=record.project_id,
        revision=record.revision,
        sourceSelectionHash=record.source_selection_hash,
        edl=edl,
        status=record.status,
        renderedAssetId=record.rendered_asset_id,
        parentEditVersionId=record.parent_edit_version_id,
        formatVersion=record.format_version,
        active=record.active,
        timelineHash=record.timeline_hash,
        createdAt=record.created_at,
    )


def _video_repair_dto(record: VideoRepairRecord) -> VideoRepairDto:
    preview_document = {
        key: value
        for key, value in record.preview_json.items()
        if key not in {"repairId", "editIntent", "legacyEditIntent"}
    }
    preview_document.update({
        "instruction": record.instruction,
    })
    return VideoRepairDto(
        id=record.id,
        projectId=record.project_id,
        baseVideoAssetId=record.base_video_asset_id,
        baseEditVersionId=record.base_edit_version_id,
        baseTimelineHash=record.base_timeline_hash,
        frameRate=RationalFrameRate(
            numerator=record.frame_rate_numerator,
            denominator=record.frame_rate_denominator,
        ),
        issueRange=FrameRange(startFrame=record.issue_start_frame, endFrame=record.issue_end_frame),
        generationRange=FrameRange(
            startFrame=record.generation_start_frame,
            endFrame=record.generation_end_frame,
        ),
        candidateCoreRange=FrameRange(
            startFrame=record.candidate_core_start_frame,
            endFrame=record.candidate_core_end_frame,
        ),
        providerDurationSeconds=record.provider_duration_seconds,
        selectionPolicyVersion=record.selection_policy_version,
        legacyEditIntent=record.edit_intent,
        instruction=record.instruction,
        prompt=record.prompt,
        negativePrompt=record.negative_prompt,
        inputHash=record.input_hash,
        status=record.status,
        candidateAssetId=record.candidate_asset_id,
        approvedCandidateAssetId=record.approved_candidate_asset_id,
        approvedEditVersionId=record.approved_edit_version_id,
        approvalIdempotencyKey=record.approval_idempotency_key,
        preview=preview_document,
        createdAt=record.created_at,
        approvedAt=record.approved_at,
    )


def _rate_card_revision_dto(
    rows: list[ProviderRateCardRecord],
) -> RateCardRevisionDto:
    if not rows:
        raise ValueError("rate-card revision requires at least one metric")
    first = rows[0]
    return RateCardRevisionDto(
        provider=first.provider,
        model=first.model,
        revision=first.revision,
        sourceUrl=first.source_url,
        effectiveFrom=first.effective_from,
        rates=tuple(
            RateCardItem(
                metric=row.metric,
                unit=row.unit,
                unitPriceMicros=row.unit_price_micros,
            )
            for row in sorted(rows, key=lambda item: item.metric)
        ),
        active=all(row.active for row in rows),
        createdAt=first.created_at,
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
    publication_record = session.scalar(
        select(MediaPublicationRecord).where(MediaPublicationRecord.job_id == record.id)
    )
    publication = (
        JobPublicationDto(
            id=publication_record.id,
            state=publication_record.state,
            publicHost=publication_record.public_host,
            signedUrlExpiresAt=publication_record.signed_url_expires_at,
            deleteAfter=publication_record.delete_after,
        )
        if publication_record is not None
        else None
    )
    snapshot_document = record.frozen_input_json.get("inputSnapshot")
    input_snapshot = (
        GenerationInputSnapshotDto.model_validate(snapshot_document)
        if isinstance(snapshot_document, dict)
        else _legacy_generation_input_snapshot(record)
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
        validationRunId=record.validation_run_id,
        parentJobId=record.parent_job_id,
        videoRepairId=record.video_repair_id,
        providerSubmissionStartedAt=record.provider_submission_started_at,
        providerResult=record.provider_result_json,
        publication=publication,
        actualUsage=record.actual_usage_json,
        expectedCostMicros=record.expected_cost_micros,
        actualCostMicros=record.actual_cost_micros,
        currency=record.currency,
        billingStatus=record.billing_status,
        rateCardRevision=record.rate_card_revision,
        pricingSnapshot=record.pricing_snapshot_json,
        providerRequestId=record.provider_request_id,
        inputSnapshot=input_snapshot,
        frozenInput=record.frozen_input_json,
        resultAssetIds=asset_ids,
        supersedesJobId=record.supersedes_job_id,
        error=record.error_json,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _legacy_generation_input_snapshot(
    record: JobRecord,
) -> GenerationInputSnapshotDto | None:
    frozen = record.frozen_input_json
    common_keys = {
        "prompt",
        "negativePrompt",
        "capabilityRevision",
        "durationSeconds",
        "resolution",
        "aspectRatio",
    }
    if record.provider is None or record.model is None or not common_keys <= frozen.keys():
        return None
    if record.kind == "generate_video":
        required = {"storyVersionId", "shotPlanVersionId", "selectionHash", "references"}
        if not required <= frozen.keys() or not isinstance(frozen["references"], list):
            return None
        document = {
            "schemaVersion": 1,
            "kind": "whole_video",
            "state": "submitted",
            "provider": record.provider,
            "model": record.model,
            "capabilityRevision": frozen["capabilityRevision"],
            "inputHash": record.input_hash,
            "prompt": frozen["prompt"],
            "negativePrompt": frozen["negativePrompt"],
            "references": frozen["references"],
            "video": {
                "durationSeconds": frozen["durationSeconds"],
                "resolution": frozen["resolution"],
                "aspectRatio": frozen["aspectRatio"],
                "frameRate": 24,
            },
            "source": {
                "storyVersionId": frozen["storyVersionId"],
                "shotPlanVersionId": frozen["shotPlanVersionId"],
                "selectionHash": frozen["selectionHash"],
            },
            "promptCompilerRevision": frozen.get("promptCompilerRevision"),
            "createdAt": record.created_at,
        }
    elif record.kind == "regenerate_video_segment":
        required = {
            "baseVideoAssetId",
            "baseTimelineHash",
            "instruction",
            "issueRange",
            "generationRange",
            "candidateCoreRange",
            "imageReferences",
        }
        if not required <= frozen.keys() or not isinstance(frozen["imageReferences"], list):
            return None
        document = {
            "schemaVersion": 1,
            "kind": "segment_edit",
            "state": "submitted",
            "provider": record.provider,
            "model": record.model,
            "capabilityRevision": frozen["capabilityRevision"],
            "inputHash": record.input_hash,
            "prompt": frozen["prompt"],
            "negativePrompt": frozen["negativePrompt"],
            "references": [
                {
                    "assetId": reference.get("assetId"),
                    "role": reference.get("role"),
                    "priority": index,
                    "included": True,
                    "sha256": reference.get("sha256"),
                    "derived": reference.get("derived", False),
                }
                for index, reference in enumerate(frozen["imageReferences"], start=1)
                if isinstance(reference, dict)
            ],
            "video": {
                "durationSeconds": frozen["durationSeconds"],
                "resolution": frozen["resolution"],
                "aspectRatio": frozen["aspectRatio"],
                "frameRate": 24,
            },
            "source": {
                "baseVideoAssetId": frozen["baseVideoAssetId"],
                "baseTimelineHash": frozen["baseTimelineHash"],
            },
            "segmentEdit": {
                "instruction": frozen["instruction"],
                "issueRange": frozen["issueRange"],
                "generationRange": frozen["generationRange"],
                "candidateCoreRange": frozen["candidateCoreRange"],
            },
            "promptCompilerRevision": frozen.get("promptCompilerRevision"),
            "createdAt": record.created_at,
        }
    else:
        return None
    try:
        return GenerationInputSnapshotDto.model_validate(document)
    except ValidationError:
        return None


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


def _document_hash(document: object) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
