from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from catflow.application.continuity import (
    EpisodeContinuityConfirmCommand,
    EpisodeContinuityResetCommand,
    EpisodeContinuitySnapshotDto,
    EpisodeContinuityState,
    SeriesAssetBindingDto,
    SeriesAssetBindingsPatchCommand,
    planned_continuity_state,
)
from catflow.application.project_library import suggested_theme_tags
from catflow.application.series import (
    SeriesCreateCommand,
    SeriesEpisodeDto,
    SeriesEpisodeOutlineDraft,
    SeriesPatchCommand,
    SeriesPlanDraft,
    SeriesPlanVersionDto,
    SeriesValidationIssueDto,
    StorySeriesDto,
    validate_series_plan,
)
from catflow.application.service import (
    FIXED_CANON_ROLES,
    AssetDto,
    CanonProfileDto,
    CanonRevisionCreateCommand,
    EditDecisionListDto,
    EditDecisionListV2,
    EditVersionDto,
    FixedCanonRole,
    GenerationInputSnapshotDto,
    ImageGenerationInputSnapshotDto,
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
    StudioIdempotencyInputConflictError,
    StudioNotFoundError,
    ValidationRunDto,
    VideoRepairDto,
    VideoRepairStatus,
)
from catflow.application.story_imports import (
    StoryImportAnalysisDraft,
    StoryImportConfirmCommand,
    StoryImportCreateCommand,
    StoryImportMaterializationDto,
    StoryImportProjectDto,
    StorySourceDocumentDto,
    StorySourceRelationSuggestionDto,
    StorySourceUnitDto,
)
from catflow.domain.billing import RateCardItem, rate_card_revision_signature
from catflow.domain.models import LifeStoryProposalDraft, MicroEvent, ShotPlanDraft, ShotSpec
from catflow.domain.video_repairs import FrameRange, RationalFrameRate

from .database import canon_v4_document, ensure_canon_v4
from .models import (
    AssetRecord,
    CanonProfileRecord,
    EditVersionRecord,
    EpisodeContinuitySnapshotRecord,
    EpisodeReferenceManifestRecord,
    JobEventRecord,
    JobRecord,
    LifePlannerMessageRecord,
    LifePlannerProposalRecord,
    LifePlannerSessionRecord,
    MediaPublicationRecord,
    ProjectRecord,
    ProjectSelectionRecord,
    ProjectTagRecord,
    ProviderRateCardRecord,
    SeriesAssetBindingRecord,
    SeriesEpisodeOutlineVersionRecord,
    SeriesEpisodeRecord,
    SeriesPlanVersionRecord,
    ShotPlanVersionRecord,
    StorySeriesRecord,
    StorySourceDocumentRecord,
    StorySourceMaterializationRecord,
    StorySourceRelationSuggestionRecord,
    StorySourceUnitRecord,
    StoryVersionRecord,
    ValidationRunRecord,
    VideoRepairRecord,
)


class PostgresStudioRepository:
    """PostgreSQL owns every durable CatFlow business fact and transaction boundary."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def publish_rate_card(self, command: RateCardRevisionCreateCommand) -> RateCardRevisionDto:
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
            tags = suggested_theme_tags(draft.theme)
            if tags:
                session.add_all(
                    ProjectTagRecord(
                        project_id=record.id,
                        name=tag.name,
                        normalized_name=tag.normalized_name,
                    )
                    for tag in tags
                )
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

    def create_story_series(
        self, command: SeriesCreateCommand, *, canon_profile_id: uuid.UUID
    ) -> StorySeriesDto:
        with self._sessions.begin() as session:
            record = StorySeriesRecord(
                title=command.title,
                premise=command.premise,
                narrative_mode=command.narrative_mode,
                planned_episode_count=command.planned_episode_count,
                default_episode_duration_seconds=command.default_episode_duration_seconds,
                world_setting=command.world_setting,
                emotional_direction=command.emotional_direction,
                ending_goal=command.ending_goal,
                recurring_elements_json=command.recurring_elements,
                must_keep_json=command.must_keep,
                must_avoid_json=command.must_avoid,
                additional_notes=command.additional_notes,
                canon_profile_id=canon_profile_id,
            )
            session.add(record)
            session.flush()
            return _story_series_dto(session, record)

    def list_story_series(self) -> list[StorySeriesDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(StorySeriesRecord).order_by(
                    StorySeriesRecord.updated_at.desc(), StorySeriesRecord.id.desc()
                )
            ).all()
            return [_story_series_dto(session, record) for record in records]

    def get_story_series(self, series_id: uuid.UUID) -> StorySeriesDto | None:
        with self._sessions() as session:
            record = session.get(StorySeriesRecord, series_id)
            return _story_series_dto(session, record) if record is not None else None

    def update_story_series(
        self, series_id: uuid.UUID, command: SeriesPatchCommand
    ) -> StorySeriesDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(StorySeriesRecord).where(StorySeriesRecord.id == series_id).with_for_update()
            )
            if record is None:
                raise StudioNotFoundError("story series not found")
            for field_name in command.model_fields_set:
                setattr(record, field_name, getattr(command, field_name))
            record.updated_at = datetime.now(UTC)
            session.flush()
            return _story_series_dto(session, record)

    def create_series_plan_version(
        self,
        series_id: uuid.UUID,
        *,
        plan: SeriesPlanDraft,
        input_hash: str,
        prompt_revision: str,
        producing_job_id: uuid.UUID,
        validation_issues: list[SeriesValidationIssueDto] | None = None,
    ) -> SeriesPlanVersionDto:
        with self._sessions.begin() as session:
            series = session.scalar(
                select(StorySeriesRecord).where(StorySeriesRecord.id == series_id).with_for_update()
            )
            if series is None:
                raise StudioNotFoundError("story series not found")
            existing = session.scalar(
                select(SeriesPlanVersionRecord).where(
                    SeriesPlanVersionRecord.producing_job_id == producing_job_id
                )
            )
            if existing is not None:
                return _series_plan_dto(existing)
            revision = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(SeriesPlanVersionRecord.revision), 0)).where(
                            SeriesPlanVersionRecord.series_id == series_id
                        )
                    )
                    or 0
                )
                + 1
            )
            now = datetime.now(UTC)
            session.execute(
                update(SeriesPlanVersionRecord)
                .where(
                    SeriesPlanVersionRecord.series_id == series_id,
                    SeriesPlanVersionRecord.status == "candidate",
                )
                .values(status="superseded", decided_at=now)
            )
            if validation_issues is None:
                disposition, issues = validate_series_plan(
                    plan,
                    expected_episode_count=series.planned_episode_count,
                    narrative_mode=series.narrative_mode,
                )
            else:
                issues = validation_issues
                disposition = (
                    "needs_input"
                    if any(issue.severity == "blocking" for issue in issues)
                    else "candidate_ready"
                )
            record = SeriesPlanVersionRecord(
                series_id=series_id,
                revision=revision,
                status="candidate",
                active=False,
                disposition=disposition,
                plan_json=plan.model_dump(mode="json", by_alias=True),
                issues_json=[issue.model_dump(mode="json", by_alias=True) for issue in issues],
                input_hash=input_hash,
                prompt_revision=prompt_revision,
                producing_job_id=producing_job_id,
            )
            session.add(record)
            session.flush()
            return _series_plan_dto(record)

    def list_series_plan_versions(self, series_id: uuid.UUID) -> list[SeriesPlanVersionDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(SeriesPlanVersionRecord)
                .where(SeriesPlanVersionRecord.series_id == series_id)
                .order_by(SeriesPlanVersionRecord.revision.desc())
            ).all()
            return [_series_plan_dto(record) for record in records]

    def materialize_series_plan_version(
        self,
        series_id: uuid.UUID,
        *,
        base_plan_version_id: uuid.UUID,
        plan: SeriesPlanDraft,
        idempotency_key: str,
    ) -> SeriesPlanVersionDto:
        with self._sessions.begin() as session:
            prior = session.scalar(
                select(SeriesPlanVersionRecord).where(
                    SeriesPlanVersionRecord.materialization_idempotency_key
                    == idempotency_key
                )
            )
            if prior is not None:
                if (
                    prior.series_id != series_id
                    or prior.base_plan_version_id != base_plan_version_id
                ):
                    raise StudioIdempotencyInputConflictError(
                        "idempotency key already belongs to different input"
                    )
                return _series_plan_dto(prior)
            series = session.scalar(
                select(StorySeriesRecord)
                .where(StorySeriesRecord.id == series_id)
                .with_for_update()
            )
            base = session.scalar(
                select(SeriesPlanVersionRecord)
                .where(
                    SeriesPlanVersionRecord.id == base_plan_version_id,
                    SeriesPlanVersionRecord.series_id == series_id,
                )
                .with_for_update()
            )
            if series is None or base is None:
                raise StudioNotFoundError("series plan version not found")
            if base.active or base.status != "candidate":
                raise StudioConflictError("only a pending series plan can be completed")
            now = datetime.now(UTC)
            session.execute(
                update(SeriesPlanVersionRecord)
                .where(
                    SeriesPlanVersionRecord.series_id == series_id,
                    SeriesPlanVersionRecord.status == "candidate",
                )
                .values(status="superseded", decided_at=now)
            )
            revision = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(SeriesPlanVersionRecord.revision), 0))
                        .where(SeriesPlanVersionRecord.series_id == series_id)
                    )
                    or 0
                )
                + 1
            )
            disposition, issues = validate_series_plan(
                plan,
                expected_episode_count=series.planned_episode_count,
                narrative_mode=series.narrative_mode,
            )
            input_hash = hashlib.sha256(
                json.dumps(
                    {
                        "basePlanVersionId": str(base_plan_version_id),
                        "plan": plan.model_dump(mode="json", by_alias=True),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            created = SeriesPlanVersionRecord(
                series_id=series_id,
                revision=revision,
                status="candidate",
                active=False,
                disposition=disposition,
                plan_json=plan.model_dump(mode="json", by_alias=True),
                issues_json=[
                    issue.model_dump(mode="json", by_alias=True) for issue in issues
                ],
                input_hash=input_hash,
                prompt_revision="manual-series-plan-v1",
                producing_job_id=None,
                base_plan_version_id=base_plan_version_id,
                materialization_idempotency_key=idempotency_key,
            )
            session.add(created)
            session.flush()
            return _series_plan_dto(created)

    def activate_series_plan_version(
        self,
        series_id: uuid.UUID,
        plan_version_id: uuid.UUID,
        *,
        expected_active_plan_version_id: uuid.UUID | None,
        idempotency_key: str,
    ) -> SeriesPlanVersionDto:
        with self._sessions.begin() as session:
            prior = session.scalar(
                select(SeriesPlanVersionRecord).where(
                    SeriesPlanVersionRecord.activation_idempotency_key == idempotency_key
                )
            )
            if prior is not None:
                if prior.series_id != series_id or prior.id != plan_version_id:
                    raise StudioIdempotencyInputConflictError(
                        "idempotency key already belongs to different input"
                    )
                return _series_plan_dto(prior)
            series = session.scalar(
                select(StorySeriesRecord).where(StorySeriesRecord.id == series_id).with_for_update()
            )
            selected = session.scalar(
                select(SeriesPlanVersionRecord)
                .where(
                    SeriesPlanVersionRecord.id == plan_version_id,
                    SeriesPlanVersionRecord.series_id == series_id,
                )
                .with_for_update()
            )
            if series is None or selected is None:
                raise StudioNotFoundError("series plan version not found")
            active_id = session.scalar(
                select(SeriesPlanVersionRecord.id).where(
                    SeriesPlanVersionRecord.series_id == series_id,
                    SeriesPlanVersionRecord.active.is_(True),
                )
            )
            if active_id != expected_active_plan_version_id:
                raise StudioConflictError("active series plan changed")
            if selected.status != "candidate" or selected.disposition != "candidate_ready":
                raise StudioConflictError("series plan requires completion before adoption")
            now = datetime.now(UTC)
            session.execute(
                update(SeriesPlanVersionRecord)
                .where(SeriesPlanVersionRecord.series_id == series_id)
                .values(active=False)
            )
            selected.active = True
            selected.status = "accepted"
            selected.decided_at = now
            selected.activation_idempotency_key = idempotency_key
            existing_episodes = {
                item.episode_order: item
                for item in session.scalars(
                    select(SeriesEpisodeRecord)
                    .where(SeriesEpisodeRecord.series_id == series_id)
                    .with_for_update()
                ).all()
            }
            parsed_plan = SeriesPlanDraft.model_validate(selected.plan_json)
            for index, outline in enumerate(parsed_plan.episodes):
                episode = existing_episodes.get(outline.order)
                if episode is None:
                    episode = SeriesEpisodeRecord(
                        series_id=series_id,
                        episode_order=outline.order,
                        status="outline",
                    )
                    session.add(episode)
                    session.flush()
                    revision = 1
                else:
                    session.execute(
                        update(SeriesEpisodeOutlineVersionRecord)
                        .where(
                            SeriesEpisodeOutlineVersionRecord.episode_id == episode.id,
                            SeriesEpisodeOutlineVersionRecord.active.is_(True),
                        )
                        .values(active=False)
                    )
                    revision = (
                        int(
                            session.scalar(
                                select(
                                    func.coalesce(
                                        func.max(SeriesEpisodeOutlineVersionRecord.revision), 0
                                    )
                                ).where(
                                    SeriesEpisodeOutlineVersionRecord.episode_id == episode.id
                                )
                            )
                            or 0
                        )
                        + 1
                    )
                session.add(
                    SeriesEpisodeOutlineVersionRecord(
                        episode_id=episode.id,
                        revision=revision,
                        source_plan_version_id=selected.id,
                        outline_json=outline.model_dump(mode="json", by_alias=True),
                        active=True,
                    )
                )
                session.execute(
                    update(EpisodeContinuitySnapshotRecord)
                    .where(
                        EpisodeContinuitySnapshotRecord.episode_id == episode.id,
                        EpisodeContinuitySnapshotRecord.active.is_(True),
                    )
                    .values(active=False)
                )
                previous_outline = (
                    parsed_plan.episodes[index - 1] if index > 0 else None
                )
                for direction in ("incoming", "outgoing"):
                    state = planned_continuity_state(
                        bible=parsed_plan.series_bible,
                        episode=outline,
                        direction=direction,
                        previous_episode=previous_outline,
                    )
                    session.add(
                        EpisodeContinuitySnapshotRecord(
                            episode_id=episode.id,
                            direction=direction,
                            source="planned",
                            snapshot_json=state.model_dump(mode="json", by_alias=True),
                            decisions_json={},
                            confirmed=False,
                            active=True,
                        )
                    )
            series.updated_at = now
            session.flush()
            return _series_plan_dto(selected)

    def reject_series_plan_version(
        self, series_id: uuid.UUID, plan_version_id: uuid.UUID
    ) -> SeriesPlanVersionDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(SeriesPlanVersionRecord)
                .where(
                    SeriesPlanVersionRecord.id == plan_version_id,
                    SeriesPlanVersionRecord.series_id == series_id,
                )
                .with_for_update()
            )
            if record is None:
                raise StudioNotFoundError("series plan version not found")
            if record.active or record.status != "candidate":
                raise StudioConflictError("only a pending series plan can be rejected")
            record.status = "rejected"
            record.decided_at = datetime.now(UTC)
            session.flush()
            return _series_plan_dto(record)

    def list_series_episodes(self, series_id: uuid.UUID) -> list[SeriesEpisodeDto]:
        with self._sessions() as session:
            episodes = session.scalars(
                select(SeriesEpisodeRecord)
                .where(SeriesEpisodeRecord.series_id == series_id)
                .order_by(SeriesEpisodeRecord.episode_order)
            ).all()
            return [_series_episode_dto(session, episode) for episode in episodes]

    def list_series_jobs(self, series_id: uuid.UUID) -> list[JobDto]:
        with self._sessions() as session:
            episode_project_ids = select(SeriesEpisodeRecord.project_id).where(
                SeriesEpisodeRecord.series_id == series_id,
                SeriesEpisodeRecord.project_id.is_not(None),
            )
            records = session.scalars(
                select(JobRecord)
                .where(
                    or_(
                        JobRecord.series_id == series_id,
                        JobRecord.project_id.in_(episode_project_ids),
                    )
                )
                .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
            ).all()
            return [_job_dto(session, item) for item in records]

    def materialize_series_episode(
        self,
        series_id: uuid.UUID,
        episode_id: uuid.UUID,
        *,
        idempotency_key: str,
    ) -> ProjectDto:
        with self._sessions.begin() as session:
            prior = session.scalar(
                select(SeriesEpisodeRecord).where(
                    SeriesEpisodeRecord.materialization_idempotency_key == idempotency_key
                )
            )
            if prior is not None:
                if (
                    prior.series_id != series_id
                    or prior.id != episode_id
                    or prior.project_id is None
                ):
                    raise StudioIdempotencyInputConflictError(
                        "idempotency key already belongs to different input"
                    )
                project = session.get(ProjectRecord, prior.project_id)
                if project is None:
                    raise StudioConflictError("materialized project is missing")
                return _project_dto(project)
            series = session.get(StorySeriesRecord, series_id)
            episode = session.scalar(
                select(SeriesEpisodeRecord)
                .where(
                    SeriesEpisodeRecord.id == episode_id,
                    SeriesEpisodeRecord.series_id == series_id,
                )
                .with_for_update()
            )
            if series is None or episode is None:
                raise StudioNotFoundError("series episode not found")
            if episode.project_id is not None:
                project = session.get(ProjectRecord, episode.project_id)
                if project is None:
                    raise StudioConflictError("materialized project is missing")
                episode.materialization_idempotency_key = idempotency_key
                return _project_dto(project)
            outline_record = session.scalar(
                select(SeriesEpisodeOutlineVersionRecord).where(
                    SeriesEpisodeOutlineVersionRecord.episode_id == episode_id,
                    SeriesEpisodeOutlineVersionRecord.active.is_(True),
                )
            )
            if outline_record is None:
                raise StudioConflictError("series episode has no active outline")
            outline = SeriesEpisodeOutlineDraft.model_validate(outline_record.outline_json)
            project = ProjectRecord(
                title=f"第{episode.episode_order}集 · {outline.title}",
                theme=outline.premise,
                target_duration_seconds=outline.target_duration_seconds,
                aspect_ratio="9:16",
                canon_profile_id=series.canon_profile_id,
            )
            session.add(project)
            session.flush()
            tags = suggested_theme_tags(outline.premise)
            session.add_all(
                ProjectTagRecord(
                    project_id=project.id,
                    name=tag.name,
                    normalized_name=tag.normalized_name,
                )
                for tag in tags
            )
            session.add(LifePlannerSessionRecord(project_id=project.id, context_revision=1))
            episode.project_id = project.id
            episode.materialization_idempotency_key = idempotency_key
            episode.status = "story_review"
            episode.updated_at = datetime.now(UTC)
            series.updated_at = episode.updated_at
            session.flush()
            return _project_dto(project)

    def list_episode_continuity(
        self, episode_id: uuid.UUID
    ) -> list[EpisodeContinuitySnapshotDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(EpisodeContinuitySnapshotRecord)
                .where(EpisodeContinuitySnapshotRecord.episode_id == episode_id)
                .order_by(
                    EpisodeContinuitySnapshotRecord.created_at.desc(),
                    EpisodeContinuitySnapshotRecord.id.desc(),
                )
            ).all()
            return [_episode_continuity_dto(item) for item in records]

    def confirm_episode_continuity(
        self, episode_id: uuid.UUID, command: EpisodeContinuityConfirmCommand
    ) -> EpisodeContinuitySnapshotDto:
        with self._sessions.begin() as session:
            prior = session.scalar(
                select(EpisodeContinuitySnapshotRecord).where(
                    EpisodeContinuitySnapshotRecord.idempotency_key
                    == command.idempotency_key
                )
            )
            if prior is not None:
                if prior.episode_id != episode_id or prior.direction != command.direction:
                    raise StudioIdempotencyInputConflictError(
                        "idempotency key already belongs to different input"
                    )
                return _episode_continuity_dto(prior)
            current = session.scalar(
                select(EpisodeContinuitySnapshotRecord)
                .where(
                    EpisodeContinuitySnapshotRecord.episode_id == episode_id,
                    EpisodeContinuitySnapshotRecord.direction == command.direction,
                    EpisodeContinuitySnapshotRecord.active.is_(True),
                )
                .with_for_update()
            )
            if current is None:
                raise StudioNotFoundError("episode continuity snapshot not found")
            if (
                command.expected_snapshot_id is not None
                and command.expected_snapshot_id != current.id
            ):
                raise StudioConflictError("episode continuity changed")
            current.active = False
            created = EpisodeContinuitySnapshotRecord(
                episode_id=episode_id,
                direction=command.direction,
                source="confirmed",
                snapshot_json=command.state.model_dump(mode="json", by_alias=True),
                decisions_json=command.decisions,
                confirmed=True,
                active=True,
                idempotency_key=command.idempotency_key,
            )
            session.add(created)
            session.flush()
            return _episode_continuity_dto(created)

    def reset_episode_continuity(
        self, episode_id: uuid.UUID, command: EpisodeContinuityResetCommand
    ) -> EpisodeContinuitySnapshotDto:
        with self._sessions.begin() as session:
            current = session.scalar(
                select(EpisodeContinuitySnapshotRecord)
                .where(
                    EpisodeContinuitySnapshotRecord.episode_id == episode_id,
                    EpisodeContinuitySnapshotRecord.direction == command.direction,
                    EpisodeContinuitySnapshotRecord.active.is_(True),
                )
                .with_for_update()
            )
            if current is None or current.id != command.expected_snapshot_id:
                raise StudioConflictError("episode continuity changed")
            planned = session.scalar(
                select(EpisodeContinuitySnapshotRecord)
                .where(
                    EpisodeContinuitySnapshotRecord.episode_id == episode_id,
                    EpisodeContinuitySnapshotRecord.direction == command.direction,
                    EpisodeContinuitySnapshotRecord.source == "planned",
                )
                .order_by(EpisodeContinuitySnapshotRecord.created_at.desc())
            )
            if planned is None:
                raise StudioNotFoundError("planned episode continuity not found")
            current.active = False
            restored = EpisodeContinuitySnapshotRecord(
                episode_id=episode_id,
                direction=command.direction,
                source="planned",
                snapshot_json=planned.snapshot_json,
                decisions_json={},
                confirmed=False,
                active=True,
            )
            session.add(restored)
            session.flush()
            return _episode_continuity_dto(restored)

    def series_episode_for_project(self, project_id: uuid.UUID) -> SeriesEpisodeDto | None:
        with self._sessions() as session:
            episode = session.scalar(
                select(SeriesEpisodeRecord).where(
                    SeriesEpisodeRecord.project_id == project_id
                )
            )
            return _series_episode_dto(session, episode) if episode is not None else None

    def list_series_asset_bindings(
        self, series_id: uuid.UUID
    ) -> list[SeriesAssetBindingDto]:
        with self._sessions() as session:
            rows = session.execute(
                select(SeriesAssetBindingRecord, AssetRecord)
                .join(AssetRecord, AssetRecord.id == SeriesAssetBindingRecord.asset_id)
                .where(
                    SeriesAssetBindingRecord.series_id == series_id,
                    SeriesAssetBindingRecord.active.is_(True),
                )
                .order_by(SeriesAssetBindingRecord.binding_key)
            ).all()
            return [_series_asset_binding_dto(binding, asset) for binding, asset in rows]

    def replace_series_asset_bindings(
        self, series_id: uuid.UUID, command: SeriesAssetBindingsPatchCommand
    ) -> list[SeriesAssetBindingDto]:
        with self._sessions.begin() as session:
            series = session.scalar(
                select(StorySeriesRecord)
                .where(StorySeriesRecord.id == series_id)
                .with_for_update()
            )
            if series is None:
                raise StudioNotFoundError("story series not found")
            current = session.scalars(
                select(SeriesAssetBindingRecord)
                .where(
                    SeriesAssetBindingRecord.series_id == series_id,
                    SeriesAssetBindingRecord.active.is_(True),
                )
                .with_for_update()
            ).all()
            desired = {
                item.binding_key: (item.role, item.asset_id) for item in command.bindings
            }
            existing = {
                item.binding_key: (item.role, item.asset_id) for item in current
            }
            if desired == existing:
                assets = {
                    item.id: item
                    for item in session.scalars(
                        select(AssetRecord).where(
                            AssetRecord.id.in_([record.asset_id for record in current])
                        )
                    ).all()
                }
                return [
                    _series_asset_binding_dto(record, assets[record.asset_id])
                    for record in sorted(current, key=lambda item: item.binding_key)
                ]
            asset_ids = [item.asset_id for item in command.bindings]
            assets = {
                item.id: item
                for item in session.scalars(
                    select(AssetRecord).where(AssetRecord.id.in_(asset_ids))
                ).all()
            }
            if len(assets) != len(set(asset_ids)):
                raise StudioNotFoundError("series asset not found")
            for record in current:
                record.active = False
            created: list[SeriesAssetBindingRecord] = []
            for item in command.bindings:
                record = SeriesAssetBindingRecord(
                    series_id=series_id,
                    binding_key=item.binding_key,
                    role=item.role,
                    asset_id=item.asset_id,
                    active=True,
                )
                session.add(record)
                created.append(record)
            series.updated_at = datetime.now(UTC)
            session.flush()
            return [
                _series_asset_binding_dto(record, assets[record.asset_id])
                for record in sorted(created, key=lambda item: item.binding_key)
            ]

    def find_story_source_document(
        self, *, content_hash: str
    ) -> StorySourceDocumentDto | None:
        with self._sessions() as session:
            record = session.scalar(
                select(StorySourceDocumentRecord).where(
                    StorySourceDocumentRecord.content_hash == content_hash
                )
            )
            return _story_source_document_dto(session, record) if record is not None else None

    def list_story_source_documents(self) -> list[StorySourceDocumentDto]:
        with self._sessions() as session:
            records = session.scalars(
                select(StorySourceDocumentRecord).order_by(
                    StorySourceDocumentRecord.created_at.desc(),
                    StorySourceDocumentRecord.id.desc(),
                )
            ).all()
            return [_story_source_document_dto(session, record) for record in records]

    def get_story_source_document(
        self, document_id: uuid.UUID
    ) -> StorySourceDocumentDto | None:
        with self._sessions() as session:
            record = session.get(StorySourceDocumentRecord, document_id)
            return _story_source_document_dto(session, record) if record is not None else None

    def create_story_source_document(
        self,
        command: StoryImportCreateCommand,
        *,
        document_id: uuid.UUID,
        content_hash: str,
        job: JobDto,
    ) -> StorySourceDocumentDto:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(StorySourceDocumentRecord).where(
                    StorySourceDocumentRecord.content_hash == content_hash
                )
            )
            if existing is not None:
                return _story_source_document_dto(session, existing)
            document = StorySourceDocumentRecord(
                id=document_id,
                content_hash=content_hash,
                source_format=command.source_format,
                file_name=command.file_name,
                raw_text=command.raw_text.replace("\r\n", "\n")
                .replace("\r", "\n")
                .strip(),
                status="analyzing",
            )
            session.add(document)
            session.flush()
            job_record = _new_job_record(job)
            session.add(job_record)
            session.flush()
            document.analysis_job_id = job.id
            _add_job_event(session, job_record, "job.queued")
            session.flush()
            return _story_source_document_dto(session, document)

    def restart_story_source_analysis(
        self, document_id: uuid.UUID, job: JobDto
    ) -> JobDto:
        with self._sessions.begin() as session:
            existing = _job_by_idempotency(session, job.idempotency_key)
            if existing is not None:
                _require_same_input(existing, job.input_hash)
                if existing.story_source_document_id != document_id:
                    raise StudioIdempotencyInputConflictError(
                        "idempotency key already belongs to different input"
                    )
                return _job_dto(session, existing)
            document = session.scalar(
                select(StorySourceDocumentRecord)
                .where(StorySourceDocumentRecord.id == document_id)
                .with_for_update()
            )
            if document is None:
                raise StudioNotFoundError("story source document not found")
            materialization_count = session.scalar(
                select(func.count())
                .select_from(StorySourceMaterializationRecord)
                .join(
                    StorySourceRelationSuggestionRecord,
                    StorySourceRelationSuggestionRecord.id
                    == StorySourceMaterializationRecord.suggestion_id,
                )
                .where(StorySourceRelationSuggestionRecord.document_id == document_id)
            )
            if document.status == "confirmed" or materialization_count:
                raise StudioConflictError("confirmed story relationships cannot be reanalyzed")
            previous = (
                session.get(JobRecord, document.analysis_job_id)
                if document.analysis_job_id is not None
                else None
            )
            if previous is not None and previous.status == "submission_unknown":
                raise StudioConflictError("story source analysis submission is unresolved")
            if previous is not None and previous.status not in {
                "failed",
                "cancelled",
                "succeeded",
            }:
                raise StudioConflictError("story source analysis is still running")
            record = _new_job_record(job)
            session.add(record)
            session.flush()
            document.analysis_job_id = record.id
            document.status = "analyzing"
            document.updated_at = datetime.now(UTC)
            _add_job_event(session, record, "job.queued")
            session.flush()
            return _job_dto(session, record)

    def complete_story_source_analysis(
        self, job_id: uuid.UUID, analysis: StoryImportAnalysisDraft
    ) -> StorySourceDocumentDto:
        with self._sessions.begin() as session:
            job = session.scalar(
                select(JobRecord).where(JobRecord.id == job_id).with_for_update()
            )
            if (
                job is None
                or job.kind != "analyze_story_source"
                or job.story_source_document_id is None
            ):
                raise StudioNotFoundError("story source analysis job not found")
            document = session.scalar(
                select(StorySourceDocumentRecord)
                .where(StorySourceDocumentRecord.id == job.story_source_document_id)
                .with_for_update()
            )
            if document is None:
                raise StudioNotFoundError("story source document not found")
            if document.analysis_job_id != job_id:
                raise StudioConflictError("story source analysis is no longer current")
            existing_unit_count = session.scalar(
                select(func.count()).select_from(StorySourceUnitRecord).where(
                    StorySourceUnitRecord.document_id == document.id
                )
            )
            if document.status == "analyzed" and existing_unit_count:
                return _story_source_document_dto(session, document)
            materialization_count = session.scalar(
                select(func.count())
                .select_from(StorySourceMaterializationRecord)
                .join(
                    StorySourceRelationSuggestionRecord,
                    StorySourceRelationSuggestionRecord.id
                    == StorySourceMaterializationRecord.suggestion_id,
                )
                .where(StorySourceRelationSuggestionRecord.document_id == document.id)
            )
            if materialization_count:
                raise StudioConflictError("confirmed story relationships cannot be replaced")
            if existing_unit_count:
                session.execute(
                    delete(StorySourceRelationSuggestionRecord).where(
                        StorySourceRelationSuggestionRecord.document_id == document.id
                    )
                )
                session.execute(
                    delete(StorySourceUnitRecord).where(
                        StorySourceUnitRecord.document_id == document.id
                    )
                )
                session.flush()
            units_by_ordinal: dict[int, StorySourceUnitRecord] = {}
            for item in analysis.units:
                record = StorySourceUnitRecord(
                    document_id=document.id,
                    ordinal=item.ordinal,
                    title=item.title,
                    theme=item.theme,
                    raw_text=item.raw_text,
                    analysis_json=item.analysis,
                )
                session.add(record)
                units_by_ordinal[item.ordinal] = record
            session.flush()
            for item in analysis.relation_suggestions:
                session.add(
                    StorySourceRelationSuggestionRecord(
                        document_id=document.id,
                        relation_type=item.relation_type,
                        suggested_series_id=item.suggested_series_id,
                        unit_ids_json=[
                            str(units_by_ordinal[ordinal].id)
                            for ordinal in item.unit_ordinals
                        ],
                        title=item.title,
                        narrative_mode=item.narrative_mode,
                        confidence=item.confidence,
                        rationale=item.rationale,
                        status="suggested",
                    )
                )
            document.status = "analyzed"
            document.updated_at = datetime.now(UTC)
            session.flush()
            return _story_source_document_dto(session, document)

    def confirm_story_source(
        self, document_id: uuid.UUID, command: StoryImportConfirmCommand
    ) -> StoryImportMaterializationDto:
        with self._sessions.begin() as session:
            prior = session.scalar(
                select(StorySourceMaterializationRecord).where(
                    StorySourceMaterializationRecord.idempotency_key
                    == command.idempotency_key
                )
            )
            if prior is not None:
                if (
                    prior.suggestion_id != command.suggestion_id
                    or prior.target_type != command.target
                    or prior.target_series_id != command.target_series_id
                    or prior.target_project_id != command.target_project_id
                ):
                    raise StudioIdempotencyInputConflictError(
                        "idempotency key already belongs to different input"
                    )
                return _story_source_materialization_dto(session, prior)
            document = session.scalar(
                select(StorySourceDocumentRecord)
                .where(StorySourceDocumentRecord.id == document_id)
                .with_for_update()
            )
            suggestion = session.scalar(
                select(StorySourceRelationSuggestionRecord)
                .where(
                    StorySourceRelationSuggestionRecord.id == command.suggestion_id,
                    StorySourceRelationSuggestionRecord.document_id == document_id,
                )
                .with_for_update()
            )
            if document is None or suggestion is None:
                raise StudioNotFoundError("story source relation suggestion not found")
            unit_ids = [uuid.UUID(str(value)) for value in suggestion.unit_ids_json]
            units = session.scalars(
                select(StorySourceUnitRecord)
                .where(StorySourceUnitRecord.id.in_(unit_ids))
                .order_by(StorySourceUnitRecord.ordinal)
            ).all()
            series_record: StorySeriesRecord | None = None
            projects: list[ProjectRecord] = []
            if command.target == "new_series":
                if len(units) < 2:
                    raise StudioConflictError("a series requires at least two source units")
                series_record = StorySeriesRecord(
                    title=suggestion.title,
                    premise="\n".join(item.raw_text for item in units),
                    narrative_mode=suggestion.narrative_mode or "continuous",
                    planned_episode_count=len(units),
                    default_episode_duration_seconds=12,
                    world_setting="由已导入原文中的地点、时间和环境归纳",
                    emotional_direction="保持原文的情绪变化",
                    recurring_elements_json=[],
                    must_keep_json=["保留来源文本中的核心事件"],
                    must_avoid_json=["不得无依据改写来源事实"],
                    additional_notes=f"来源文档 {document.id}",
                    canon_profile_id=ensure_canon_v4(session).id,
                )
                session.add(series_record)
                session.flush()
            elif command.target == "append_series":
                if command.target_series_id is None:
                    raise StudioConflictError("append_series requires a target series")
                series_record = session.get(StorySeriesRecord, command.target_series_id)
                if series_record is None:
                    raise StudioNotFoundError("target story series not found")
            elif command.target == "independent":
                canon_profile_id = ensure_canon_v4(session).id
                for unit in units:
                    project = ProjectRecord(
                        title=unit.title,
                        theme=unit.raw_text,
                        target_duration_seconds=12,
                        aspect_ratio="9:16",
                        canon_profile_id=canon_profile_id,
                    )
                    session.add(project)
                    session.flush()
                    session.add(
                        LifePlannerSessionRecord(project_id=project.id, context_revision=1)
                    )
                    projects.append(project)
            elif command.target_series_id is not None:
                series_record = session.get(StorySeriesRecord, command.target_series_id)
                if series_record is None:
                    raise StudioNotFoundError("target story series not found")
            elif command.target_project_id is not None:
                if session.get(ProjectRecord, command.target_project_id) is None:
                    raise StudioNotFoundError("target project not found")
            else:
                raise StudioConflictError("story relationship target is missing")
            suggestion.status = "accepted"
            record = StorySourceMaterializationRecord(
                suggestion_id=suggestion.id,
                target_type=command.target,
                idempotency_key=command.idempotency_key,
                series_id=series_record.id if series_record is not None else None,
                project_id=projects[0].id if len(projects) == 1 else None,
                target_series_id=command.target_series_id,
                target_project_id=command.target_project_id,
                project_ids_json=[str(item.id) for item in projects],
            )
            session.add(record)
            remaining = int(
                session.scalar(
                    select(func.count())
                    .select_from(StorySourceRelationSuggestionRecord)
                    .where(
                        StorySourceRelationSuggestionRecord.document_id == document_id,
                        StorySourceRelationSuggestionRecord.status == "suggested",
                        StorySourceRelationSuggestionRecord.id != suggestion.id,
                    )
                )
                or 0
            )
            if remaining == 0:
                document.status = "confirmed"
            document.updated_at = datetime.now(UTC)
            session.flush()
            return StoryImportMaterializationDto(
                id=record.id,
                suggestionId=suggestion.id,
                target=command.target,
                targetSeriesId=command.target_series_id,
                targetProjectId=command.target_project_id,
                series=(
                    _story_series_dto(session, series_record)
                    if series_record is not None
                    else None
                ),
                projects=[
                    StoryImportProjectDto(
                        id=item.id,
                        title=item.title,
                        theme=item.theme,
                        targetDurationSeconds=item.target_duration_seconds,
                    )
                    for item in projects
                ],
                createdAt=record.created_at,
            )

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
                .where(
                    JobRecord.project_id == project_id,
                    JobRecord.kind.in_(("plan_story", "plan_series_episode")),
                )
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
            if job.kind not in {"plan_story", "plan_series_episode"}:
                raise StudioConflictError("job is not a planner job")
            if job.project_id is None:
                raise StudioConflictError("planner job has no project")
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

    def create_shot_plan(
        self,
        project_id: uuid.UUID,
        draft: ShotPlanDraft,
        *,
        active: bool = True,
        review_status: str = "accepted",
        producing_job_id: uuid.UUID | None = None,
        base_shot_plan_version_id: uuid.UUID | None = None,
    ) -> ShotPlanVersionDto:
        with self._sessions.begin() as session:
            session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            revision = session.scalar(
                select(func.coalesce(func.max(ShotPlanVersionRecord.revision), 0)).where(
                    ShotPlanVersionRecord.project_id == project_id
                )
            )
            now = datetime.now(UTC)
            if active:
                session.execute(
                    update(ShotPlanVersionRecord)
                    .where(
                        ShotPlanVersionRecord.project_id == project_id,
                        ShotPlanVersionRecord.active.is_(True),
                    )
                    .values(active=False)
                )
            if review_status == "candidate":
                session.execute(
                    update(ShotPlanVersionRecord)
                    .where(
                        ShotPlanVersionRecord.project_id == project_id,
                        ShotPlanVersionRecord.review_status == "candidate",
                    )
                    .values(review_status="superseded", decided_at=now)
                )
            if active and base_shot_plan_version_id is not None:
                session.execute(
                    update(ShotPlanVersionRecord)
                    .where(
                        ShotPlanVersionRecord.id == base_shot_plan_version_id,
                        ShotPlanVersionRecord.project_id == project_id,
                        ShotPlanVersionRecord.review_status == "candidate",
                    )
                    .values(review_status="superseded", decided_at=now)
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
                review_status=review_status,
                producing_job_id=producing_job_id,
                base_shot_plan_version_id=base_shot_plan_version_id,
                decided_at=now if review_status != "candidate" else None,
                active=active,
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
        self,
        project_id: uuid.UUID,
        shot_plan_id: uuid.UUID,
        *,
        expected_active_shot_plan_version_id: uuid.UUID | None,
    ) -> ShotPlanVersionDto:
        with self._sessions.begin() as session:
            session.scalar(
                select(ProjectRecord).where(ProjectRecord.id == project_id).with_for_update()
            )
            record = session.scalar(
                select(ShotPlanVersionRecord)
                .where(ShotPlanVersionRecord.id == shot_plan_id)
                .with_for_update()
            )
            if record is None or record.project_id != project_id:
                raise StudioNotFoundError("shot plan version not found")
            if record.active and record.review_status == "accepted":
                return _shot_plan_dto(record)
            if record.review_status in {"rejected", "superseded"}:
                raise StudioConflictError("shot plan version cannot be activated")
            current_active_id = session.scalar(
                select(ShotPlanVersionRecord.id).where(
                    ShotPlanVersionRecord.project_id == project_id,
                    ShotPlanVersionRecord.active.is_(True),
                )
            )
            if (
                expected_active_shot_plan_version_id is not None
                and current_active_id != expected_active_shot_plan_version_id
            ):
                raise StudioConflictError("active shot plan version changed")
            session.execute(
                update(ShotPlanVersionRecord)
                .where(ShotPlanVersionRecord.project_id == project_id)
                .values(active=False)
            )
            record.active = True
            record.review_status = "accepted"
            record.decided_at = datetime.now(UTC)
            session.flush()
            return _shot_plan_dto(record)

    def reject_shot_plan(
        self, project_id: uuid.UUID, shot_plan_id: uuid.UUID
    ) -> ShotPlanVersionDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(ShotPlanVersionRecord)
                .where(ShotPlanVersionRecord.id == shot_plan_id)
                .with_for_update()
            )
            if record is None or record.project_id != project_id:
                raise StudioNotFoundError("shot plan version not found")
            if record.review_status == "rejected":
                return _shot_plan_dto(record)
            if record.review_status != "candidate" or record.active:
                raise StudioConflictError("only a pending shot plan candidate can be rejected")
            record.review_status = "rejected"
            record.decided_at = datetime.now(UTC)
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
                raise StudioConflictError("project environment must be an environment image")
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
            seen_slots: set[str] = set()
            for selection, asset in records:
                if selection.slot in seen_slots:
                    continue
                seen_slots.add(selection.slot)
                if selection.decision in {"selected", "approved"}:
                    current[selection.slot] = _asset_dto(asset)
            return current

    def replace_continuity_keyframes(
        self, project_id: uuid.UUID, asset_ids: list[uuid.UUID]
    ) -> list[AssetDto]:
        with self._sessions.begin() as session:
            project = session.get(ProjectRecord, project_id)
            if project is None:
                raise StudioNotFoundError("project not found")
            assets = {
                asset.id: asset
                for asset in session.scalars(
                    select(AssetRecord).where(AssetRecord.id.in_(asset_ids))
                ).all()
            }
            if len(assets) != len(asset_ids) or any(
                asset.project_id != project_id or asset.media_type != "image"
                for asset in assets.values()
            ):
                raise StudioNotFoundError("continuity keyframe not found")
            latest_rows = session.execute(
                select(ProjectSelectionRecord, AssetRecord)
                .join(AssetRecord, AssetRecord.id == ProjectSelectionRecord.asset_id)
                .where(
                    ProjectSelectionRecord.project_id == project_id,
                    ProjectSelectionRecord.slot.in_(
                        ("continuity_keyframe_1", "continuity_keyframe_2")
                    ),
                )
                .order_by(
                    ProjectSelectionRecord.created_at.desc(),
                    ProjectSelectionRecord.id.desc(),
                )
            ).all()
            current_by_slot: dict[str, AssetRecord] = {}
            for selection, asset in latest_rows:
                current_by_slot.setdefault(selection.slot, asset)
            result: list[AssetDto] = []
            for index, slot in enumerate(
                ("continuity_keyframe_1", "continuity_keyframe_2")
            ):
                if index < len(asset_ids):
                    asset = assets[asset_ids[index]]
                    decision = "selected"
                    result.append(_asset_dto(asset))
                elif slot in current_by_slot:
                    asset = current_by_slot[slot]
                    decision = "rejected"
                else:
                    continue
                session.add(
                    ProjectSelectionRecord(
                        project_id=project_id,
                        asset_id=asset.id,
                        slot=slot,
                        decision=decision,
                        source_hash=_selection_source_hash(project_id, slot, asset.sha256),
                    )
                )
            session.flush()
            return result

    def save_episode_reference_manifest(
        self,
        episode_id: uuid.UUID,
        job_id: uuid.UUID,
        continuity_snapshot_id: uuid.UUID | None,
        references: list[dict[str, object]],
    ) -> None:
        with self._sessions.begin() as session:
            existing = session.scalar(
                select(EpisodeReferenceManifestRecord).where(
                    EpisodeReferenceManifestRecord.job_id == job_id
                )
            )
            if existing is not None:
                if (
                    existing.episode_id != episode_id
                    or existing.continuity_snapshot_id != continuity_snapshot_id
                    or existing.references_json != references
                ):
                    raise StudioConflictError("episode reference manifest changed")
                return
            session.add(
                EpisodeReferenceManifestRecord(
                    episode_id=episode_id,
                    job_id=job_id,
                    continuity_snapshot_id=continuity_snapshot_id,
                    references_json=references,
                )
            )

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
            if job.kind == "plan_shots":
                session.scalar(
                    select(ProjectRecord)
                    .where(ProjectRecord.id == job.project_id)
                    .with_for_update()
                )
            existing = _job_by_idempotency(session, job.idempotency_key)
            if existing is not None:
                _require_same_input(existing, job.input_hash)
                return _job_dto(session, existing)
            if job.kind == "plan_shots":
                running = session.scalar(
                    select(JobRecord.id).where(
                        JobRecord.project_id == job.project_id,
                        JobRecord.kind == "plan_shots",
                        JobRecord.status.in_(
                            {
                                "queued",
                                "submitting",
                                "submitted",
                                "polling",
                                "storing",
                                "cancel_requested",
                                "submission_unknown",
                            }
                        ),
                    )
                )
                if running is not None:
                    raise StudioConflictError("a shot plan generation job is already running")
            record = _new_job_record(job)
            session.add(record)
            session.flush()
            _add_job_event(session, record, "job.queued")
            return _job_dto(session, record)

    def get_job(self, job_id: uuid.UUID) -> JobDto | None:
        with self._sessions() as session:
            record = session.get(JobRecord, job_id)
            return _job_dto(session, record) if record is not None else None

    def record_director_validation(
        self, job_id: uuid.UUID, validation: dict[str, object]
    ) -> JobDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(JobRecord).where(JobRecord.id == job_id).with_for_update()
            )
            if record is None:
                raise StudioNotFoundError("job not found")
            provider_result = dict(record.provider_result_json or {})
            provider_result["validation"] = validation
            record.provider_result_json = provider_result
            session.flush()
            return _job_dto(session, record)

    def record_series_plan_validation(
        self, job_id: uuid.UUID, validation: dict[str, object]
    ) -> JobDto:
        with self._sessions.begin() as session:
            record = session.scalar(
                select(JobRecord).where(JobRecord.id == job_id).with_for_update()
            )
            if record is None:
                raise StudioNotFoundError("job not found")
            provider_result = dict(record.provider_result_json or {})
            provider_result["validation"] = validation
            record.provider_result_json = provider_result
            session.flush()
            return _job_dto(session, record)

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
                    seriesId=record.series_id,
                    storySourceDocumentId=record.story_source_document_id,
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


def _story_series_dto(session: Session, record: StorySeriesRecord) -> StorySeriesDto:
    active_plan_id = session.scalar(
        select(SeriesPlanVersionRecord.id).where(
            SeriesPlanVersionRecord.series_id == record.id,
            SeriesPlanVersionRecord.active.is_(True),
        )
    )
    planned_count = int(
        session.scalar(
            select(func.count())
            .select_from(SeriesEpisodeRecord)
            .where(SeriesEpisodeRecord.series_id == record.id)
        )
        or 0
    )
    materialized_count = int(
        session.scalar(
            select(func.count())
            .select_from(SeriesEpisodeRecord)
            .where(
                SeriesEpisodeRecord.series_id == record.id,
                SeriesEpisodeRecord.project_id.is_not(None),
            )
        )
        or 0
    )
    completed_count = int(
        session.scalar(
            select(func.count())
            .select_from(SeriesEpisodeRecord)
            .where(
                SeriesEpisodeRecord.series_id == record.id,
                SeriesEpisodeRecord.status == "completed",
            )
        )
        or 0
    )
    return StorySeriesDto(
        id=record.id,
        title=record.title,
        premise=record.premise,
        narrativeMode=record.narrative_mode,
        plannedEpisodeCount=record.planned_episode_count,
        defaultEpisodeDurationSeconds=record.default_episode_duration_seconds,
        worldSetting=record.world_setting,
        emotionalDirection=record.emotional_direction,
        endingGoal=record.ending_goal,
        recurringElements=record.recurring_elements_json,
        mustKeep=record.must_keep_json,
        mustAvoid=record.must_avoid_json,
        additionalNotes=record.additional_notes,
        canonProfileId=record.canon_profile_id,
        activePlanVersionId=active_plan_id,
        plannedCount=planned_count,
        materializedCount=materialized_count,
        completedCount=completed_count,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _series_plan_dto(record: SeriesPlanVersionRecord) -> SeriesPlanVersionDto:
    return SeriesPlanVersionDto(
        id=record.id,
        seriesId=record.series_id,
        revision=record.revision,
        status=record.status,
        active=record.active,
        disposition=record.disposition,
        plan=SeriesPlanDraft.model_validate(record.plan_json),
        inputHash=record.input_hash,
        promptRevision=record.prompt_revision,
        producingJobId=record.producing_job_id,
        basePlanVersionId=record.base_plan_version_id,
        issues=[SeriesValidationIssueDto.model_validate(item) for item in record.issues_json],
        decidedAt=record.decided_at,
        createdAt=record.created_at,
    )


def _series_episode_dto(session: Session, record: SeriesEpisodeRecord) -> SeriesEpisodeDto:
    outline = session.scalar(
        select(SeriesEpisodeOutlineVersionRecord).where(
            SeriesEpisodeOutlineVersionRecord.episode_id == record.id,
            SeriesEpisodeOutlineVersionRecord.active.is_(True),
        )
    )
    if outline is None:
        raise StudioConflictError("series episode has no active outline")
    payload = SeriesEpisodeOutlineDraft.model_validate(outline.outline_json)
    return SeriesEpisodeDto(
        id=record.id,
        seriesId=record.series_id,
        order=record.episode_order,
        title=payload.title,
        targetDurationSeconds=payload.target_duration_seconds,
        status=record.status,
        projectId=record.project_id,
        activeOutlineVersionId=outline.id,
        outline=payload,
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _episode_continuity_dto(
    record: EpisodeContinuitySnapshotRecord,
) -> EpisodeContinuitySnapshotDto:
    return EpisodeContinuitySnapshotDto(
        id=record.id,
        episodeId=record.episode_id,
        direction=record.direction,
        source=record.source,
        state=EpisodeContinuityState.model_validate(record.snapshot_json),
        decisions=record.decisions_json,
        confirmed=record.confirmed,
        active=record.active,
        createdAt=record.created_at,
    )


def _series_asset_binding_dto(
    record: SeriesAssetBindingRecord, asset: AssetRecord
) -> SeriesAssetBindingDto:
    return SeriesAssetBindingDto(
        id=record.id,
        seriesId=record.series_id,
        bindingKey=record.binding_key,
        role=record.role,
        assetId=record.asset_id,
        assetSha256=asset.sha256,
        active=record.active,
        createdAt=record.created_at,
    )


def _story_source_document_dto(
    session: Session, record: StorySourceDocumentRecord
) -> StorySourceDocumentDto:
    projected_status = record.status
    if record.status == "analyzing" and record.analysis_job_id is not None:
        analysis_status = session.scalar(
            select(JobRecord.status).where(JobRecord.id == record.analysis_job_id)
        )
        if analysis_status in {"failed", "cancelled"}:
            projected_status = "failed"
    unit_records = session.scalars(
        select(StorySourceUnitRecord)
        .where(StorySourceUnitRecord.document_id == record.id)
        .order_by(StorySourceUnitRecord.ordinal)
    ).all()
    suggestion_records = session.scalars(
        select(StorySourceRelationSuggestionRecord)
        .where(StorySourceRelationSuggestionRecord.document_id == record.id)
        .order_by(
            StorySourceRelationSuggestionRecord.created_at,
            StorySourceRelationSuggestionRecord.id,
        )
    ).all()
    return StorySourceDocumentDto(
        id=record.id,
        contentHash=record.content_hash,
        sourceFormat=record.source_format,
        fileName=record.file_name,
        rawText=record.raw_text,
        status=projected_status,
        analysisJobId=record.analysis_job_id,
        units=[
            StorySourceUnitDto(
                id=item.id,
                documentId=item.document_id,
                ordinal=item.ordinal,
                title=item.title,
                theme=item.theme,
                rawText=item.raw_text,
                analysis=item.analysis_json,
                createdAt=item.created_at,
            )
            for item in unit_records
        ],
        relationSuggestions=[
            StorySourceRelationSuggestionDto(
                id=item.id,
                documentId=item.document_id,
                relationType=item.relation_type,
                unitIds=[uuid.UUID(str(value)) for value in item.unit_ids_json],
                title=item.title,
                narrativeMode=item.narrative_mode,
                suggestedSeriesId=item.suggested_series_id,
                confidence=item.confidence,
                rationale=item.rationale,
                status=item.status,
                createdAt=item.created_at,
            )
            for item in suggestion_records
        ],
        createdAt=record.created_at,
        updatedAt=record.updated_at,
    )


def _story_source_materialization_dto(
    session: Session, record: StorySourceMaterializationRecord
) -> StoryImportMaterializationDto:
    suggestion = session.get(StorySourceRelationSuggestionRecord, record.suggestion_id)
    if suggestion is None:
        raise StudioConflictError("story source materialization suggestion is missing")
    series = (
        session.get(StorySeriesRecord, record.series_id)
        if record.series_id is not None
        else None
    )
    project_ids = [uuid.UUID(str(value)) for value in record.project_ids_json]
    projects = (
        session.scalars(
            select(ProjectRecord)
            .where(ProjectRecord.id.in_(project_ids))
            .order_by(ProjectRecord.created_at, ProjectRecord.id)
        ).all()
        if project_ids
        else []
    )
    return StoryImportMaterializationDto(
        id=record.id,
        suggestionId=record.suggestion_id,
        target=record.target_type,
        targetSeriesId=record.target_series_id,
        targetProjectId=record.target_project_id,
        series=_story_series_dto(session, series) if series is not None else None,
        projects=[
            StoryImportProjectDto(
                id=item.id,
                title=item.title,
                theme=item.theme,
                targetDurationSeconds=item.target_duration_seconds,
            )
            for item in projects
        ],
        createdAt=record.created_at,
    )


def _validation_run_dto(record: ValidationRunRecord) -> ValidationRunDto:
    call_limits = dict(record.call_limits_json)
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
            call_limits.get("generate_video", 0) + call_limits.get("regenerate_video_segment", 0)
        ),
        provider=record.provider,
        models=record.models_json,
        capabilityRevision=record.capability_revision,
        costEstimateStatus=record.cost_estimate_status,
        canon=record.canon_snapshot_json,
        repair=record.repair_snapshot_json,
        usage=dict(record.usage_json),
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
        reviewStatus=record.review_status,
        producingJobId=record.producing_job_id,
        baseShotPlanVersionId=record.base_shot_plan_version_id,
        decidedAt=record.decided_at,
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
        series_id=job.series_id,
        story_source_document_id=job.story_source_document_id,
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
    preview_document.update(
        {
            "instruction": record.instruction,
        }
    )
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
        raise StudioIdempotencyInputConflictError(
            "idempotency key already belongs to different input"
        )


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
    image_snapshot_document = record.frozen_input_json.get("imageInputSnapshot")
    image_input_snapshot = (
        ImageGenerationInputSnapshotDto.model_validate(image_snapshot_document)
        if isinstance(image_snapshot_document, dict)
        else None
    )
    return JobDto(
        id=record.id,
        projectId=record.project_id,
        seriesId=record.series_id,
        storySourceDocumentId=record.story_source_document_id,
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
        imageInputSnapshot=image_input_snapshot,
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
            series_id=job.series_id,
            story_source_document_id=job.story_source_document_id,
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
